"""Hearing you.

Always on, with a switch - which is the whole design. Someone talking on Discord does
not want every word of it going to an assistant, so the switch has to be one click and
has to take effect instantly, not at the end of whatever is being recorded. Push to
talk is the same machinery with the switch held down instead of left on.

What is captured is decided by which source is chosen. PipeWire exposes a microphone
and a monitor of whatever is playing, so listening to a YouTube video is this same
code pointed somewhere else.

Speech is found before it is transcribed. Whisper on a stream of silence returns
"Thank you." and "you" forever, so audio is only sent once it is loud enough for long
enough, and only the loud part is sent.
"""
import subprocess
import tempfile
import time
import wave
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from .speech import KOKORO_HOME, kokoro_python
from .store import settings

RATE = 16000                 # what whisper wants, and a third of the data of 48k
FRAME_MS = 100
FRAME_BYTES = RATE * 2 * FRAME_MS // 1000


def recorder_argv(source=""):
    """A command that writes raw 16-bit mono PCM to stdout, or None."""
    import shutil
    if shutil.which("pw-record"):
        argv = ["pw-record", "--rate", str(RATE), "--channels", "1",
                "--format", "s16", "--latency", f"{FRAME_MS}ms"]
        if source:
            argv += ["--target", source]
        return argv + ["-"]
    if shutil.which("parecord"):
        argv = ["parecord", "--rate", str(RATE), "--channels", "1",
                "--format", "s16le", "--raw"]
        if source:
            argv += ["-d", source]
        return argv
    if shutil.which("arecord"):
        return ["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"]
    return None


def transcriber_argv():
    python = kokoro_python()
    if python is None:
        return None
    return [str(python), str(Path(__file__).with_name("whisper_hear.py")),
            "--model", settings().get("listen_model", "small.en")]


def why_deaf():
    """Why listening is not available, in a sentence."""
    if recorder_argv() is None:
        return ("Nothing to record with - none of pw-record, parecord or arecord is "
                "installed.")
    if kokoro_python() is None:
        return (f"No speech environment at {KOKORO_HOME}. Create it and install "
                "faster-whisper into it to let Bonsai hear you.")
    return ""


def loudness(frame):
    """Mean absolute sample value, 0 to 32767. Cheap, and enough to find speech."""
    if not frame:
        return 0.0
    total = 0
    for index in range(0, len(frame) - 1, 2):
        sample = int.from_bytes(frame[index:index + 2], "little", signed=True)
        total += sample if sample >= 0 else -sample
    return total / (len(frame) / 2)


class Segmenter:
    """Turns a stream of frames into utterances.

    Speech starts when it has been loud for a moment and ends when it has been quiet
    for longer - a gap inside a sentence is shorter than the gap after one. A little
    audio from before the start is kept, because the first consonant is always below
    the threshold and "top the server" is a different instruction."""

    def __init__(self, threshold=240, start_frames=2, stop_frames=8, preroll=3,
                 longest=30.0):
        self.threshold = threshold
        self.start_frames = start_frames
        self.stop_frames = stop_frames
        self.preroll = preroll
        self.longest = longest
        self.recent = []
        self.speech = []
        self.loud = 0
        self.quiet = 0
        self.talking = False

    def feed(self, frame):
        """Returns finished audio as bytes, or None."""
        level = loudness(frame)
        if not self.talking:
            self.recent.append(frame)
            self.recent = self.recent[-self.preroll:]
            self.loud = self.loud + 1 if level >= self.threshold else 0
            if self.loud >= self.start_frames:
                self.talking = True
                self.speech = list(self.recent)
                self.recent, self.quiet = [], 0
            return None

        self.speech.append(frame)
        self.quiet = self.quiet + 1 if level < self.threshold else 0
        spoken_for = len(self.speech) * FRAME_MS / 1000
        if self.quiet >= self.stop_frames or spoken_for >= self.longest:
            audio = b"".join(self.speech)
            self.speech, self.talking, self.loud, self.quiet = [], False, 0, 0
            return audio
        return None

    def drop(self):
        """Forget anything part-said. Used when the switch goes off mid-sentence."""
        self.speech, self.recent = [], []
        self.talking, self.loud, self.quiet = False, 0, 0


class Listener(QThread):
    """Records, finds speech, transcribes it, and says what it heard."""

    heard = pyqtSignal(str)
    ready = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self._stop = False
        self._open = True            # the switch: push-to-talk closes it between presses
        self.recorder = None
        self.whisper = None

    def set_open(self, listening):
        """Push to talk. Closing it also throws away whatever was half-said."""
        self._open = listening

    def stop(self):
        self._stop = True

    def _shut_down(self):
        for process in (self.recorder, self.whisper):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except Exception:
                    process.kill()

    def transcribe(self, audio):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            path = Path(handle.name)
        try:
            with wave.open(str(path), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(RATE)
                out.writeframes(audio)
            self.whisper.stdin.write(f"{path}\n")
            self.whisper.stdin.flush()
            return (self.whisper.stdout.readline() or "").strip()
        finally:
            path.unlink(missing_ok=True)

    def run(self):
        problem = why_deaf()
        if problem:
            self.failed.emit(problem)
            return
        record = recorder_argv(self.config.get("listen_source", ""))
        try:
            self.whisper = subprocess.Popen(
                transcriber_argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1)
            # Loading the model takes twenty seconds; nothing should be recorded into
            # a void until it is up.
            if (self.whisper.stdout.readline() or "").strip() != "READY":
                self.failed.emit("The transcriber did not start.")
                return
            self.ready.emit()
            self.recorder = subprocess.Popen(record, stdout=subprocess.PIPE,
                                             stderr=subprocess.DEVNULL)
        except Exception as exc:
            self.failed.emit(f"Could not start listening: {exc}")
            self._shut_down()
            return

        segmenter = Segmenter(threshold=self.config.get("listen_threshold", 240))
        try:
            while not self._stop:
                frame = self.recorder.stdout.read(FRAME_BYTES)
                if not frame:
                    break
                if not self._open:
                    segmenter.drop()
                    continue
                audio = segmenter.feed(frame)
                if not audio:
                    continue
                if len(audio) < RATE:      # under half a second is a cough, not a word
                    continue
                text = self.transcribe(audio)
                # Whisper says "Thank you." to silence, and a hum comes back as "you".
                # Neither is worth waking anything up for.
                if text and len(text) > 2 and text.strip(" .,!?").lower() not in (
                        "you", "thank you", "thanks", "bye", "um", "uh"):
                    self.heard.emit(text)
        except Exception as exc:
            if not self._stop:
                self.failed.emit(f"Listening stopped: {exc}")
        finally:
            self._shut_down()
