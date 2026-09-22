"""Transcribe wav files, one per line, without reloading the model each time.

small.en takes twenty seconds to load and a second to transcribe, so a process per
clip would spend its life loading. This stays resident instead: paths in on stdin,
one line of text out per path, empty when nothing was said.

Run by the voice environment's interpreter, like kokoro_say - faster-whisper is not
in the system Python and should not be.

    <venv>/bin/python bonsai/whisper_hear.py --model small.en
"""
import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="small.en")
    parser.add_argument("--language", default="en")
    parser.add_argument("--compute", default="int8")
    args = parser.parse_args()

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        sys.exit(f"faster-whisper is not installed in this environment: {exc}")

    model = WhisperModel(args.model, device="cpu", compute_type=args.compute)
    # Said once the model is up, so the caller can wait for it rather than guess.
    print("READY", flush=True)

    for line in sys.stdin:
        path = line.strip()
        if not path:
            continue
        try:
            segments, _ = model.transcribe(
                path, language=args.language, beam_size=1,
                # Whisper's own VAD as a second gate: a door closing gets past a
                # loudness check and comes back as "Thank you." otherwise.
                vad_filter=True,
                condition_on_previous_text=False)
            text = " ".join(segment.text.strip() for segment in segments).strip()
        except Exception as exc:
            text = ""
            print(f"ERROR {exc}", file=sys.stderr, flush=True)
        print(text.replace("\n", " "), flush=True)


if __name__ == "__main__":
    main()
