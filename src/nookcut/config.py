"""Settings in a file: ~/.config/nookcut/config.env (NOOKCUT_* lines, KEY=VALUE).

Read at start by the CLI and the MCP server (also the shared background server, which doesn't see
your shell profile). Values already set in the environment win. Only NOOKCUT_* keys are read.
"""
from __future__ import annotations

import os
import re
import shlex

PATH = os.path.expanduser(os.getenv("NOOKCUT_CONFIG", "~/.config/nookcut/config.env"))
_KEY = re.compile(r"^NOOKCUT_[A-Z_]+$")


def read() -> dict[str, str]:
    out: dict[str, str] = {}
    if not os.path.exists(PATH):
        return out
    with open(PATH, encoding="utf-8") as f:
        for line in f:
            key, sep, value = line.strip().partition("=")
            if sep and _KEY.match(key.strip()) and not line.lstrip().startswith("#"):
                try:
                    out[key.strip()] = " ".join(shlex.split(value)) if value.strip().startswith(("'", '"')) \
                        else value.strip()
                except ValueError:
                    out[key.strip()] = value.strip()
    return out


def load() -> None:
    """Put the file's settings into the environment (the environment wins)."""
    for key, value in read().items():
        os.environ.setdefault(key, value)


def write(key: str, value: str | None) -> str:
    """Set (or with None remove) one setting in the file; returns the file path."""
    if not _KEY.match(key):
        raise ValueError(f"{key} isn't a NookCut setting (they start with NOOKCUT_)")
    values = read()
    if value is None:
        values.pop(key, None)
    else:
        values[key] = value
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        f.write("# NookCut settings (nookcut config). The environment overrides these.\n")
        f.writelines(f"{k}={v}\n" for k, v in sorted(values.items()))
    os.chmod(PATH, 0o600)
    return PATH
