"""Speak one line with Kokoro (local, offline) for NookCut's voiceover.

NookCut doesn't import this file: your Kokoro Python runs it, so NookCut itself keeps no extra
dependencies. `nookcut config kokoro` writes the command for you; by hand it is

  NOOKCUT_TTS_COMMAND="/path/to/kokoro/python /path/to/kokoro_say.py --text-file {text_file}
                       --out {out_wav} --voice {voice} --speed {speed} --language {language}"

Needs: pip install kokoro-onnx soundfile, and kokoro-v1.0.onnx + voices-v1.0.bin in --models
(default $KOKORO_DIR or ~/.local/share/kokoro-tts). `--list` prints the voices.
"""
import argparse
import os
import sys

# a natural default voice per language (Kokoro's first letter of a voice is its language)
DEFAULTS = {"en": "af_heart", "es": "ef_dora", "fr": "ff_siwis", "hi": "hf_alpha", "it": "if_sara",
            "pt": "pf_dora", "ja": "jf_alpha", "zh": "zf_xiaobei"}
LANG = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi", "i": "it", "p": "pt-br", "j": "ja", "z": "cmn"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--text-file")
    ap.add_argument("--out")
    ap.add_argument("--voice", default="")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--language", default="en")
    ap.add_argument("--models", default=os.environ.get("KOKORO_DIR", os.path.expanduser("~/.local/share/kokoro-tts")))
    ap.add_argument("--list", action="store_true", help="print the voices and exit")
    a = ap.parse_args()
    try:
        import soundfile
        from kokoro_onnx import Kokoro
    except ImportError:
        print("kokoro_say: this Python needs `pip install kokoro-onnx soundfile`", file=sys.stderr)
        return 2
    model, voices = os.path.join(a.models, "kokoro-v1.0.onnx"), os.path.join(a.models, "voices-v1.0.bin")
    if not (os.path.isfile(model) and os.path.isfile(voices)):
        print(f"kokoro_say: kokoro-v1.0.onnx and voices-v1.0.bin aren't in {a.models}", file=sys.stderr)
        return 2
    k = Kokoro(model, voices)
    if a.list:
        print("\n".join(sorted(k.get_voices())))
        return 0
    voice = a.voice or DEFAULTS.get(a.language.lower()[:2], "af_heart")
    if voice not in k.get_voices():
        print(f"kokoro_say: there is no Kokoro voice {voice!r}; run with --list", file=sys.stderr)
        return 2
    with open(a.text_file, encoding="utf-8") as f:
        text = f.read().strip()
    audio, rate = k.create(text, voice=voice, speed=max(0.5, min(2.0, a.speed)), lang=LANG.get(voice[0], "en-us"))
    soundfile.write(a.out, audio, rate)
    return 0


if __name__ == "__main__":
    sys.exit(main())
