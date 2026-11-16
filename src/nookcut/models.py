"""Optional local models, fetched only when the user runs `nookcut models install`.

NookCut never downloads anything on its own: this module is the single place that touches the
network, and only from that user-run command. Files live in ~/.nookcut/models (or
$NOOKCUT_MODELS_DIR) and are checked against a SHA-256 before use.

whisper  speech-to-text models for ffmpeg's `whisper` filter (whisper.cpp ggml format)
"""
from __future__ import annotations

import hashlib
import os
import urllib.request

from .errors import InputError, MediaError

_HF = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"

# name: (file, bytes, sha256, what it is for)
WHISPER = {
    "tiny": ("ggml-tiny.bin", 77691713,
             "be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21",
             "fastest, rough; for old or slow computers"),
    "base": ("ggml-base.bin", 147951465,
             "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
             "recommended: good captions on any computer"),
    "small": ("ggml-small.bin", 487601967,
              "1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b",
              "more accurate, about 3x slower than base"),
    "medium": ("ggml-medium.bin", 1533763059,
               "6c14d5adee5f86394037b4e4e8b59f1673b6cee10e3cf0b11bbdbee79c156208",
               "very accurate, slow without a GPU"),
    "large-v3-turbo": ("ggml-large-v3-turbo-q5_0.bin", 574041195,
                       "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2",
                       "best quality; needs a fast computer or a GPU"),
}
DEFAULT = "base"
# when several are installed, the best one is used unless NOOKCUT_WHISPER_MODEL says otherwise
PREFERENCE = ("large-v3-turbo", "medium", "small", "base", "tiny")


def models_dir() -> str:
    return os.path.expanduser(os.getenv("NOOKCUT_MODELS_DIR") or "~/.nookcut/models")


def _file(name: str) -> str:
    return os.path.join(models_dir(), WHISPER[name][0])


def installed() -> list[str]:
    """Whisper models present with the right size (the full hash is checked at install)."""
    out = []
    for name, (_, size, _, _) in WHISPER.items():
        p = _file(name)
        if os.path.isfile(p) and os.path.getsize(p) == size:
            out.append(name)
    return out


def whisper_model() -> tuple[str, str] | None:
    """(name, path) of the model to use, or None. NOOKCUT_WHISPER_MODEL may be a name or a file path."""
    want = os.getenv("NOOKCUT_WHISPER_MODEL", "").strip()
    if want and os.path.isfile(os.path.expanduser(want)):
        return os.path.basename(want), os.path.expanduser(want)
    have = installed()
    if want in have:
        return want, _file(want)
    for name in PREFERENCE:
        if name in have:
            return name, _file(name)
    return None


def listing() -> list[dict]:
    have = set(installed())
    using = (whisper_model() or ("", ""))[0]
    return [{"name": n, "size_mb": round(size / 1e6), "installed": n in have, "in_use": n == using,
             "about": about} for n, (_, size, _, about) in WHISPER.items()]


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def install(name: str = DEFAULT, on_progress=None) -> str:
    """Download one whisper model (user-run only), verify its SHA-256, return its path.
    on_progress(done_bytes, total_bytes). Ctrl+C or a failure leaves no partial file behind."""
    if name not in WHISPER:
        raise InputError(f"There is no model called {name!r}.", f"Pick one of: {', '.join(WHISPER)}.")
    file, size, sha, _ = WHISPER[name]
    dest = _file(name)
    if name in installed():
        return dest
    os.makedirs(models_dir(), exist_ok=True)
    part = dest + ".part"
    try:
        req = urllib.request.Request(_HF + file, headers={"User-Agent": "nookcut"})
        with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
            done = 0
            for chunk in iter(lambda: r.read(1 << 20), b""):
                f.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, size)
        if _sha256(part) != sha:
            raise MediaError(f"The downloaded {name} model is damaged (checksum mismatch).",
                             "Run the install again; if it keeps failing, try another network.")
        os.replace(part, dest)
        return dest
    except OSError as e:
        raise MediaError(f"Downloading the {name} model failed.",
                         "Check your internet connection and try again.", str(e)) from e
    finally:
        if os.path.exists(part):
            os.remove(part)


def remove(name: str) -> bool:
    if name not in WHISPER:
        raise InputError(f"There is no model called {name!r}.", f"Pick one of: {', '.join(WHISPER)}.")
    p = _file(name)
    if os.path.exists(p):
        os.remove(p)
        return True
    return False
