"""Synthesise one line with Kokoro, as a subprocess.

Kokoro is a Python library rather than a binary, and it lives in its own virtual
environment - the system Python here is externally managed, and a text assistant has
no business installing a 300MB neural network into it. So this is run by that
environment's interpreter and talks to Bonsai the way piper does: text in, a wav out.

    <venv>/bin/python -m bonsai.kokoro_say --model M --voices V --voice af_heart \
        --speed 1.0 --out out.wav      (text on stdin)
"""
import argparse
import sys
import wave


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--voices", required=True)
    parser.add_argument("--voice", default="af_heart")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--lang", default="en-us")
    parser.add_argument("--out", required=True)
    parser.add_argument("--list-voices", action="store_true")
    args = parser.parse_args()

    try:
        import numpy as np
        from kokoro_onnx import Kokoro
    except ImportError as exc:
        sys.exit(f"kokoro-onnx is not installed in this environment: {exc}")

    kokoro = Kokoro(args.model, args.voices)
    if args.list_voices:
        print("\n".join(sorted(kokoro.get_voices())))
        return

    text = sys.stdin.read().strip()
    if not text:
        sys.exit("nothing to say")
    samples, rate = kokoro.create(text, voice=args.voice,
                                  speed=max(0.5, min(2.5, args.speed)),
                                  lang=args.lang)
    with wave.open(args.out, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes())


if __name__ == "__main__":
    main()
