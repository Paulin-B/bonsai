"""Saying it out loud.

Two halves, and the second one is the part that matters. Making sound is a matter of
finding whichever engine is installed and handing it text. Deciding *what* text is the
design problem: the model writes markdown, fenced code, absolute paths and emoji, and
read aloud those come out as "backtick def main backtick" and thirty seconds of
punctuation. A reply spoken badly is worse than a reply not spoken, because it cannot
be skimmed past.

So `speakable()` is the substance here, and it is testable without any engine at all.
"""
import queue
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from .config import DEFAULTS
from .store import settings


# Engines, best first. Each is (name, how to check, how to synthesise) - piper sounds
# like a person and runs on the CPU, which matters here because both GPUs are busy
# holding the model. espeak-ng sounds like 1987 but needs no model file, so it is a
# fair fallback rather than nothing.
# Kokoro is a library, not a binary, and it lives in its own virtual environment: the
# system Python here is externally managed, and installing a 300MB neural network into
# it to make an assistant talk is not a reasonable thing to do to someone's machine.
# So it is driven the way piper is - a subprocess that takes text and writes a wav.
KOKORO_HOME = Path.home() / ".local/share/bonsai-voice"
KOKORO_MODELS = Path.home() / ".local/share/bonsai_voices"


# The 54 voices of Kokoro v1.0, as a list rather than a question asked of the model:
# opening Settings should not load 310MB to fill a dropdown. First letter is the
# language, second is the gender, which is worth spelling out because "zf_xiaoni" is
# not self-explanatory.
KOKORO_LANGUAGES = {
    "a": "American", "b": "British", "e": "Spanish", "f": "French", "h": "Hindi",
    "i": "Italian", "j": "Japanese", "p": "Portuguese", "z": "Chinese",
}

KOKORO_VOICES = [
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore",
    "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky", "am_adam", "am_echo",
    "am_eric", "am_fenrir", "am_liam", "am_michael", "am_onyx", "am_puck", "am_santa",
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily", "bm_daniel", "bm_fable",
    "bm_george", "bm_lewis", "ef_dora", "em_alex", "em_santa", "ff_siwis", "hf_alpha",
    "hf_beta", "hm_omega", "hm_psi", "if_sara", "im_nicola", "jf_alpha",
    "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo", "pf_dora", "pm_alex",
    "pm_santa", "zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi", "zm_yunjian",
    "zm_yunxi", "zm_yunxia", "zm_yunyang",
]


def voice_label(name):
    """'af_heart' -> 'Heart (American, female)'."""
    language = KOKORO_LANGUAGES.get(name[:1], "")
    gender = {"f": "female", "m": "male"}.get(name[1:2], "")
    person = name.split("_", 1)[-1].replace("_", " ").title()
    if not (language and gender):
        return name
    return f"{person} ({language}, {gender})"


def voice_choices():
    """{shown: stored} for the settings dropdown, English first since it is the
    default and the rest are a long way down an alphabetical list."""
    english = [v for v in KOKORO_VOICES if v[0] in "ab"]
    others = [v for v in KOKORO_VOICES if v[0] not in "ab"]
    return {voice_label(name): name for name in english + others}


def kokoro_python():
    configured = (settings().get("kokoro_python") or "").strip()
    candidate = Path(configured).expanduser() if configured else KOKORO_HOME / "bin/python"
    return candidate if candidate.is_file() else None


def kokoro_files():
    """(model, voices) if both are there, else None."""
    config = settings()
    model = Path((config.get("kokoro_model") or "").strip()
                 or KOKORO_MODELS / "kokoro.onnx").expanduser()
    voices = Path((config.get("kokoro_voices") or "").strip()
                  or KOKORO_MODELS / "voices.bin").expanduser()
    return (model, voices) if model.is_file() and voices.is_file() else None


def piper_binary():
    return shutil.which("piper") or shutil.which("piper-tts")


def espeak_binary():
    return shutil.which("espeak-ng") or shutil.which("espeak")


PLAYERS = (
    ("pw-play", ["pw-play"]),
    ("paplay", ["paplay"]),
    ("aplay", ["aplay", "-q"]),
    ("ffplay", ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]),
)


def player_argv():
    for _, argv in PLAYERS:
        if shutil.which(argv[0]):
            return argv
    return None


def available_engine():
    """Which engine will be used, or None. Named so the UI can say which."""
    config = settings()
    if kokoro_python() and kokoro_files():
        return "kokoro"
    model = (config.get("speech_model") or "").strip()
    if piper_binary() and model and Path(model).expanduser().is_file():
        return "piper"
    if espeak_binary():
        return "espeak"
    return None


def why_silent():
    """A sentence explaining what is missing, for the log rather than a crash."""
    if not player_argv():
        return ("Nothing to play sound with - none of pw-play, paplay, aplay or "
                "ffplay is installed.")
    if kokoro_python() and not kokoro_files():
        return (f"Kokoro is installed but its model is missing. Expected "
                f"{KOKORO_MODELS}/kokoro.onnx and voices.bin.")
    model = (settings().get("speech_model") or "").strip()
    if piper_binary() and not model:
        return ("piper is installed but no voice model is set. Point 'Voice model' in "
                "Settings at a piper .onnx file.")
    if piper_binary() and not Path(model).expanduser().is_file():
        return f"The voice model is set to {model}, which is not a file."
    return ("No speech engine found. Install piper for a voice that sounds like a "
            "person, or espeak-ng for one that does not but works immediately.")


# -- what is worth hearing -----------------------------------------------------

FENCE_RE = re.compile(r"```.*?```", re.S)
INLINE_CODE_RE = re.compile(r"`([^`]*)`")
URL_RE = re.compile(r"https?://\S+")
# ~/ as a unit: matching a bare ~ let the match start at the slash after it and left
# the tilde behind, so "~/Projects/x/factorio.py" spoke as "tilde factorio.py".
PATH_RE = re.compile(r"(?:~/|/)(?:[\w.+-]+/)*([\w.+-]+)")
MARKUP_RE = re.compile(r"^\s{0,3}(?:#{1,6}\s*|[-*+]\s+|\d+[.)]\s+|>\s*)", re.M)
EMPHASIS_RE = re.compile(r"(\*{1,3}|_{1,3})(\S.*?\S|\S)\1")
TABLE_RE = re.compile(r"^\s*\|.*\|\s*$", re.M)
EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF←-⇿"
    "⬀-⯿️‍]+")


def speakable(text, limit=None):
    """The part of a reply worth saying out loud.

    Code is named, not read: "def main colon" tells a listener nothing and takes
    longer than the explanation around it. Paths become their filename, because
    "slash home slash paulinb slash Projects slash" is most of a sentence and none of
    the meaning. What is left is prose, which is what a voice is for."""
    if not text:
        return ""
    limit = settings().get("max_spoken_chars", 600) if limit is None else limit

    blocks = len(FENCE_RE.findall(text))
    spoken = FENCE_RE.sub(" ", text)
    spoken = TABLE_RE.sub(" ", spoken)
    spoken = URL_RE.sub("a link", spoken)
    spoken = INLINE_CODE_RE.sub(r"\1", spoken)
    spoken = PATH_RE.sub(r"\1", spoken)
    spoken = MARKUP_RE.sub("", spoken)
    spoken = EMPHASIS_RE.sub(r"\2", spoken)
    spoken = EMOJI_RE.sub(" ", spoken)
    spoken = re.sub(r"[ \t]+", " ", spoken)
    # Lines that held only a table row or a fence are now blank, and a run of blank
    # lines is a pause for nothing.
    spoken = re.sub(r"\n[ \t]*(?=\n)", "", spoken)
    spoken = re.sub(r"\n{2,}", "\n", spoken)
    spoken = "\n".join(line.strip() for line in spoken.splitlines() if line.strip())

    if blocks:
        # Said once at the end rather than in place: interrupting a sentence with
        # "code block" to announce something the listener can see is worse than
        # mentioning it after.
        spoken += (f" {blocks} code block{'s' if blocks > 1 else ''} in there too."
                   if spoken else
                   f"There {'are' if blocks > 1 else 'is'} {blocks} code "
                   f"block{'s' if blocks > 1 else ''} on screen.")

    if len(spoken) > limit:
        # Cut at a sentence so it does not stop mid-word; the text is on screen
        # anyway, so this is a preview rather than a truncation people must work with.
        cut = spoken[:limit]
        stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        spoken = (cut[:stop + 1] if stop > limit // 3 else cut.rsplit(" ", 1)[0])
        spoken += " There is more on screen."
    return spoken.strip()


# -- making the sound ----------------------------------------------------------

def synthesise(text, path):
    """Write `text` to `path` as a wav. Returns None, or a reason it did not."""
    config = settings()
    engine = available_engine()
    if engine == "kokoro":
        model, voices = kokoro_files()
        # By path, not -m: the module form imports bonsai/__init__.py, which pulls in
        # PyQt6 and everything else, none of which is in the voice environment.
        argv = [str(kokoro_python()), str(Path(__file__).with_name("kokoro_say.py")),
                "--model", str(model), "--voices", str(voices),
                "--voice", config.get("kokoro_voice", "af_heart"),
                "--speed", str(config.get("speech_rate", 1.0)),
                "--out", str(path)]
    elif engine == "piper":
        model = str(Path(config["speech_model"]).expanduser())
        argv = [piper_binary(), "--model", model, "--output_file", str(path)]
        rate = config.get("speech_rate", 1.0)
        if abs(rate - 1.0) > 0.01:
            # piper calls it a length scale, and it runs the other way: a bigger
            # number is slower. Exposing "rate" and inverting here keeps the setting
            # meaning what a person would expect.
            argv += ["--length_scale", f"{1.0 / max(0.1, rate):.3f}"]
    elif engine == "espeak":
        argv = [espeak_binary(), "-w", str(path),
                "-s", str(int(150 * config.get("speech_rate", 1.0)))]
    else:
        return why_silent()
    try:
        # Run from the project root so `-m bonsai.kokoro_say` resolves; the venv holds
        # kokoro-onnx, this tree holds the script.
        done = subprocess.run(argv, input=text, capture_output=True, text=True,
                              timeout=120)
    except Exception as exc:
        return f"Speech failed: {exc}"
    if done.returncode != 0 or not Path(path).exists():
        return f"Speech failed: {(done.stderr or done.stdout or '').strip()[:200]}"
    return None


class Speaker(QThread):
    """Speaks queued lines, one after another, and can be shut up mid-sentence.

    A queue rather than a call per line: two replies arriving together used to talk
    over each other, and a voice interrupting itself is unlistenable."""

    failed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.lines = queue.Queue()
        self._playing = None
        self._stop = False

    def say(self, text):
        spoken = speakable(text)
        if spoken:
            self.lines.put(spoken)
            if not self.isRunning():
                self.start()

    def silence(self):
        """Drop anything queued and cut off what is being said now."""
        while not self.lines.empty():
            try:
                self.lines.get_nowait()
            except queue.Empty:
                break
        process, self._playing = self._playing, None
        if process is not None and process.poll() is None:
            process.terminate()

    def stop(self):
        self._stop = True
        self.silence()
        self.lines.put(None)

    def run(self):
        play = player_argv()
        if play is None:
            self.failed.emit(why_silent())
            return
        while not self._stop:
            try:
                text = self.lines.get(timeout=0.4)
            except queue.Empty:
                continue
            if text is None:
                return
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
                wav = Path(handle.name)
            try:
                problem = synthesise(text, wav)
                if problem:
                    self.failed.emit(problem)
                    return          # it will not start working on the next line
                process = subprocess.Popen(play + [str(wav)],
                                           stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL)
                self._playing = process
                process.wait()
                self._playing = None
            except Exception as exc:
                self.failed.emit(f"Speech failed: {exc}")
                return
            finally:
                wav.unlink(missing_ok=True)
