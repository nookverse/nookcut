"""Errors with a plain-language message, a hint for what to do next, and the raw detail kept apart.

Every failure NookCut raises on purpose is a NookCutError. The CLI prints `message` and `hint`;
the MCP server returns `to_dict()` so agents can relay it to the user word for word.
"""
from __future__ import annotations

import re


class NookCutError(Exception):
    code = "error"

    def __init__(self, message: str, hint: str = "", detail: str = ""):
        super().__init__(message)
        self.message, self.hint, self.detail = message, hint, detail

    def __str__(self) -> str:
        return self.message + (f" {self.hint}" if self.hint else "")

    def to_dict(self) -> dict:
        d = {"code": self.code, "message": self.message}
        if self.hint:
            d["hint"] = self.hint
        if self.detail:
            d["detail"] = self.detail
        return d


class EnvironmentNotReady(NookCutError):
    code = "environment"


class InputError(NookCutError):
    """Bad path, unreadable source, invalid brief or edit."""
    code = "input"


class MediaError(NookCutError):
    """ffmpeg/ffprobe failed on the media."""
    code = "media"


class Busy(NookCutError):
    code = "busy"


class Cancelled(NookCutError):
    code = "cancelled"

    def __init__(self, message: str = "Stopped. All ffmpeg processes for this job were ended.", **kw):
        super().__init__(message, **kw)


# (pattern in ffmpeg stderr, message, hint)
_FFMPEG = [
    (r"No such file or directory", "A file NookCut needs can't be found.",
     "Check the source path still exists and the drive is mounted."),
    (r"moov atom not found|Invalid data found when processing input|EBML header parsing failed",
     "The source file is damaged or incomplete.",
     "If it is still recording or copying, wait until it finishes; otherwise re-export it from the recorder."),
    (r"No space left on device", "The disk is full.", "Free some space on the output drive and try again."),
    (r"Permission denied", "NookCut isn't allowed to read or write one of the files.",
     "Check folder permissions, or choose a project folder inside your home directory."),
    (r"Cannot allocate memory|Out of memory", "The computer ran out of memory.",
     "Close other heavy apps, or render with fewer parallel workers."),
    (r"Stream map '0:a[^']*' matches no streams|matches no streams", "The source has no usable audio track.",
     "Set source_audio to 'mute' in the brief and plan again."),
    (r"Unknown encoder|Encoder not found|No such filter|Filter not found",
     "This ffmpeg build is missing a feature NookCut needs.",
     "Run check_environment (or `nookcut check`) and install the full ffmpeg it suggests."),
    (r"Invalid too big or non positive size|Failed to configure input pad|Error reinitializing filters",
     "ffmpeg couldn't set up the video filters for this source's frame size.",
     "Please report this with the detail below; it is a NookCut bug for this kind of file."),
    (r"Error while decoding|corrupt|Invalid NAL unit", "Part of the source video can't be decoded.",
     "The file may be damaged; try re-exporting it, or move the cut away from the broken part."),
    (r"Fontconfig error|Cannot load font|Could not load font", "The caption font can't be loaded.",
     "Set NOOKCUT_FONT to a .ttf/.otf file path."),
]


def ffmpeg_detail(stderr: str, limit: int = 12) -> str:
    """The lines that explain an ffmpeg failure: the first errors, not the generic EOF tail."""
    seen, lines = set(), []
    for line in stderr.splitlines():
        line = line.strip()
        if line and line not in seen:
            seen.add(line)
            lines.append(line)
    if len(lines) > limit:
        lines = lines[:limit - 2] + ["..."] + lines[-1:]
    return "\n".join(lines)


def explain_ffmpeg(stderr: str, step: str) -> MediaError:
    detail = ffmpeg_detail(stderr)
    for pattern, message, hint in _FFMPEG:
        if re.search(pattern, stderr, re.I):
            return MediaError(f"{step.capitalize()} failed: {message}", hint, detail)
    return MediaError(f"{step.capitalize()} failed: ffmpeg stopped with an error.",
                      "See the detail; if it repeats with this file, please report it.", detail)


def as_nookcut(e: BaseException) -> NookCutError:
    """Wrap anything into a NookCutError with a readable message (unexpected errors keep their type)."""
    if isinstance(e, NookCutError):
        return e
    if isinstance(e, (KeyboardInterrupt, SystemExit)):
        return Cancelled()
    if isinstance(e, FileNotFoundError):
        return InputError(f"File not found: {e.filename or e}", "Check the path and that the drive is mounted.")
    if isinstance(e, PermissionError):
        return InputError(str(e), "Pick a folder NookCut is allowed to use.")
    if isinstance(e, (ValueError, KeyError)):
        return InputError(str(e).strip("'\""))
    return NookCutError(f"Unexpected error: {type(e).__name__}: {e}",
                        "This is likely a NookCut bug; please report it with the steps that caused it.")
