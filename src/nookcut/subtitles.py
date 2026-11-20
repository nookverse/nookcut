"""Speech captions: word-by-word .ass files for the render, and an .srt for editors and platforms.

The active word is highlighted (yellow, slight pop) inside a short phrase of up to three words,
drawn by libass (ffmpeg's `ass` filter). Without libass the phrase is drawn with drawtext instead,
without the highlight.
"""
from __future__ import annotations

import os
import re
import struct

from .transcribe import phrases

HIGHLIGHT_RGB = "0xFFD400"  # the spoken word (ASS writes colours as &HAABBGGRR)
HOLD = 0.35                 # keep a phrase up across gaps shorter than this (no flicker)


def font_family(path: str) -> tuple[str, bool]:
    """(family name, is bold) from a .ttf/.otf name table; falls back to the file name."""
    fallback = (re.sub(r"[-_ ]?(Bold|Regular|bd)$", "", os.path.splitext(os.path.basename(path))[0], flags=re.I),
                "bold" in path.lower())
    try:
        with open(path, "rb") as f:
            data = f.read()
        n = struct.unpack(">H", data[4:6])[0]
        for i in range(n):
            tag, _, off, _ = struct.unpack(">4sIII", data[12 + 16 * i:28 + 16 * i])
            if tag != b"name":
                continue
            _, count, strings = struct.unpack(">HHH", data[off:off + 6])
            names = {}
            for r in range(count):
                pid, eid, _, nid, length, soff = struct.unpack(">HHHHHH", data[off + 6 + 12 * r:off + 18 + 12 * r])
                if nid in (1, 2) and (pid, nid) not in names:
                    raw = data[off + strings + soff:off + strings + soff + length]
                    names[(pid, nid)] = raw.decode("utf-16-be" if pid in (0, 3) else "mac_roman", "replace")
            family = names.get((3, 1)) or names.get((1, 1)) or names.get((0, 1))
            sub = names.get((3, 2)) or names.get((1, 2)) or ""
            if family:
                return family, "bold" in sub.lower()
    except (OSError, struct.error, IndexError):
        pass
    return fallback


def _clean(word: str) -> str:
    w = re.sub(r"[,.;:…]+$", "", word.strip()).upper()
    return w.replace("\\", "/").replace("{", "(").replace("}", ")")


def _ts(t: float) -> str:
    cs = max(0, round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def local_words(s: dict) -> list[list]:
    """A segment's words in its own output time (after speed), clipped to the segment."""
    dur = (s["src_out"] - s["src_in"]) / s["speed"]
    out = []
    for a, b, w in s.get("words") or []:
        la, lb = (a - s["src_in"]) / s["speed"], (b - s["src_in"]) / s["speed"]
        la, lb = max(0.0, la), min(dur, lb)
        if lb - la > 0.02 and _clean(w):
            out.append([round(la, 3), round(lb, 3), w])
    return out


def timed_phrases(words: list[list], dur: float, max_chars: int, max_words: int = 3) -> list[tuple]:
    """[(start, end, [words])]: each phrase stays up until the next one when the gap is short."""
    groups = phrases(words, max_chars, max_words)
    out = []
    for k, g in enumerate(groups):
        a, b = g[0][0], g[-1][1]
        nxt = groups[k + 1][0][0] if k + 1 < len(groups) else dur
        if nxt - b < HOLD:
            b = nxt
        out.append((a, min(dur, max(b, a + 0.3)), g))
    return out


def _ass_colour(hex_rgb: str) -> str:
    """0xRRGGBB -> ASS &H00BBGGRR."""
    v = hex_rgb.removeprefix("0x")
    return f"&H00{v[4:6]}{v[2:4]}{v[0:2]}"


def ass(words: list[list], dur: float, width: int, height: int, y: int, size: int, font: str,
        max_chars: int, colour=None) -> str:
    """An .ass script for one segment: phrase by phrase, the spoken word highlighted.
    colour(0xRRGGBB) -> 0xRRGGBB adapts the text colours to the output (HDR: paper white)."""
    family, bold = font_family(font)
    colour = colour or (lambda c: c)
    white, highlight = _ass_colour(colour("0xFFFFFF")), _ass_colour(colour(HIGHLIGHT_RGB)) + "&"
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
             "WrapStyle: 2", "ScaledBorderAndShadow: yes",
             "YCbCr Matrix: None",  # no BT.601 colour correction: colours land as written, in SDR and HDR
             "", "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
             "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
             "Alignment, MarginL, MarginR, MarginV, Encoding",
             f"Style: Speech,{family},{size},{white},{white},&H00000000,&H90000000,{-1 if bold else 0},"
             f"0,0,0,100,100,1,0,1,{max(3, size // 14)},{max(2, size // 30)},8,40,40,0,1",
             "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    pos = f"{{\\an8\\pos({width // 2},{y})}}"
    for a, b, g in timed_phrases(words, dur, max_chars):
        for k, w in enumerate(g):
            start = a if k == 0 else w[0]
            end = g[k + 1][0] if k + 1 < len(g) else b
            if end - start < 0.01:
                continue
            text = " ".join(
                f"{{\\c{highlight}\\fscx108\\fscy108\\t(0,90,\\fscx100\\fscy100)}}{_clean(x[2])}{{\\r}}" if j == k
                else _clean(x[2]) for j, x in enumerate(g))
            lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Speech,,0,0,0,,{pos}{text}")
    return "\n".join(lines) + "\n"


def plain(words: list[list], dur: float, max_chars: int) -> list[tuple[float, float, str]]:
    """(start, end, text) per phrase, for the drawtext fallback."""
    return [(a, b, " ".join(_clean(w[2]) for w in g)) for a, b, g in timed_phrases(words, dur, max_chars)]


def srt(edit: dict) -> str:
    """Speech captions for the whole edit, in output time (YouTube, TikTok and Resolve import .srt)."""
    from .edit import timeline
    cues = []
    for start, dur, s in timeline(edit):
        for a, b, g in timed_phrases(local_words(s), dur, 42, 8):
            cues.append((start + a, start + b, " ".join(re.sub(r"\s+", " ", w[2]) for w in g)))

    def tc(t):
        ms = max(0, round(t * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    return "".join(f"{n}\n{tc(a)} --> {tc(b)}\n{t}\n\n" for n, (a, b, t) in enumerate(cues, 1))
