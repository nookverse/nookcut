"""Script to video: the user's script, narrated offline, over their B-roll (or a moving background).

The script is turned into a generated "recording" and then edited like any other:
  narration.wav   every line spoken (voice.py), joined with short pauses; times are exact
  base.mp4        a slowly moving background at the output size, with the narration as its sound
  transcript.json the script's words at their spoken times (captions, captions.srt, get_transcript)
  edit.json       one segment per line, with B-roll from the user's folder where it fits
The usual render, checks, Resolve export and publish kit follow. NookCut never writes the script:
the words (and any facts in them) are the user's.
"""
from __future__ import annotations

import os
import re

from . import broll as br
from . import edit as ed
from .errors import InputError

MAX_CHARS = 8000          # a script longer than this is more than a short video
MAX_LINES = 120
LINE_MAX = 220            # characters: longer lines are split at sentence ends
GAP = 0.35                # pause between lines, seconds
WORDS_PER_SECOND = 2.6    # for the length estimate before anything is spoken
_END = re.compile(r"(?<=[.!?…])\s+")


def lines(script: str) -> list[str]:
    """The user's lines are the shots; a long one is split at its sentence ends."""
    text = (script or "").strip()
    if not text:
        raise InputError("The script is empty.", "Write what the video should say, one line per shot.")
    if len(text) > MAX_CHARS:
        raise InputError(f"The script is {len(text)} characters; the limit is {MAX_CHARS}.",
                         "Shorten it, or split it into several videos.")
    out: list[str] = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        parts = _END.split(line) if len(line) > LINE_MAX else [line]
        out += [p for p in parts if p.strip()]
    if len(out) > MAX_LINES:
        raise InputError(f"The script has {len(out)} lines; the limit is {MAX_LINES}.", "Shorten it.")
    return out


def say_form(line: str) -> str:
    """How a line is spoken: '1-10' is 'one to ten', '#3' is 'number 3', '&' is 'and'."""
    text = re.sub(r"(\d+)\s*[-–]\s*(\d+)", r"\1 to \2", line)
    text = text.replace("#", "number ").replace("&", " and ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def estimate(script_lines: list[str]) -> float:
    words = sum(len(x.split()) for x in script_lines)
    return round(words / WORDS_PER_SECOND + GAP * max(0, len(script_lines) - 1), 1)


def word_times(text: str, start: float, length: float) -> list[list]:
    """The words of a line spread over its spoken time by length (the text is known exactly)."""
    words = text.split()
    weights = [len(w) + 1 for w in words]
    t, out = start, []
    for w, wt in zip(words, weights):
        d = length * wt / sum(weights)
        out.append([round(t, 3), round(t + d * 0.92, 3), w])
        t += d
    return out


def build_edit(brief, base_info: dict, placed: list[dict], files: list[dict], fps: str) -> tuple[dict, list[str]]:
    """One segment per line, cut at the pauses, with B-roll where it fits. Returns (edit, notes)."""
    from .copywriting import wrap
    w, h = brief.size
    total = base_info["duration"]
    edit = {"source": brief.source, "title": brief.title, "game": brief.game, "template": "script",
            "cut_by": "script", "orientation": brief.orientation, "width": w, "height": h, "fps": fps,
            "language": brief.language, "music": brief.music, "music_file": brief.music_file,
            "video_type": brief.video_type, "music_style": "cinematic" if brief.video_type != "gameplay" else "taiko",
            "source_audio": "keep", "source_timecode": "00:00:00:00", "source_fps": base_info["fps"],
            "source_duration": total, "source_hdr": None, "hdr": "sdr", "source_matrix": "bt709",
            "broll_dir": brief.broll_dir, "voice": {"name": brief.voice, "speed": brief.voice_speed},
            "vo_captions": True, "subtitles": True, "segments": []}
    width = 16 if brief.orientation == "vertical" else 26
    for k, line in enumerate(placed):
        a = 0.0 if k == 0 else round(placed[k - 1]["end"] + GAP / 2, 3)
        b = total if k == len(placed) - 1 else round(line["end"] + GAP / 2, 3)
        kind = "hook" if k == 0 else "end" if k == len(placed) - 1 and len(placed) > 1 else "highlight"
        s = ed.seg(a, b, kind, top=wrap(brief.title.upper(), width) if k == 0 else "")
        s["words"] = line["words"]
        s["line"] = line["text"]
        edit["segments"].append(s)
    notes = place_broll(edit, files, brief) if files else ["no B-roll folder: lines show over the moving background"]
    return edit, notes


def place_broll(edit: dict, files: list[dict], brief) -> list[str]:
    """B-roll for a script: a clip named after a word in the line first; the rest of the clips in
    folder order for lines nothing matched (each clip once); after that the moving background, with
    the line's opening words on screen. Whole lines, from the clip's start."""
    names = {f["file"]: br.tokens(f["file"]) for f in files}
    info = {f["file"]: f for f in files}
    used: dict[str, float] = {}       # clip -> how far into it the last line got
    notes, pending = [], []
    for i, s in enumerate(edit["segments"]):
        forms = set().union(*(br._forms(x[2]) for x in s["words"])) if s["words"] else set()
        named = [f for f, t in names.items() if forms & t]
        hit = next((f for f in named if f not in used), None)
        dur = ed.seg_duration(s)
        if hit:
            _put(s, info[hit], edit, f"the line says '{sorted(forms & names[hit])[0]}'")
        else:
            # a clip this line names again: carry on from where the last line left it, if it's long enough
            again = next((f for f in named if not info[f].get("still")
                          and used[f] + dur <= (info[f].get("duration") or 0)), None)
            if not again:
                pending.append(i)
                continue
            hit = again
            _put(s, info[hit], edit, f"the line says '{sorted(forms & names[hit])[0]}' again", used[hit])
        used[hit] = used.get(hit, 0.0) + dur
        notes.append(f"line {i + 1}: {hit} ({s['broll']['why']})")
    spare = [f["file"] for f in files if f["file"] not in used]
    for name in spare:
        used[name] = 0.0
    for i in pending:
        s = edit["segments"][i]
        if spare:
            name = spare.pop(0)
            _put(s, info[name], edit, "no clip named after this line; next unused clip")
            notes.append(f"line {i + 1}: {name} (no clip matched; next unused clip)")
        elif s["kind"] != "hook":
            from .copywriting import wrap
            s["top"] = wrap(headline(s["line"]), 16 if edit["orientation"] == "vertical" else 26)
            notes.append(f"line {i + 1}: background with the line's first words (no B-roll left)")
    return notes


def headline(line: str, most: int = 6) -> str:
    """A line's opening on screen: up to its first clause break, at most `most` words."""
    clause = re.split(r"[,.;:!?…]", line, maxsplit=1)[0]
    words = clause.split()[:most]
    return " ".join(words).upper()


def _put(s: dict, clip: dict, edit: dict, why: str, start_in: float = 0.0) -> None:
    """A clip shaped very differently from the frame (landscape in vertical) is shown whole (fit),
    otherwise it fills the frame (cover)."""
    layout = "cover"
    if clip.get("width") and clip.get("height"):
        clip_ratio = clip["width"] / clip["height"]
        frame_ratio = edit["width"] / edit["height"]
        if max(clip_ratio, frame_ratio) / min(clip_ratio, frame_ratio) > 1.6:
            layout = "fit"
    s["broll"] = {"file": clip["file"], "layout": layout, "start": 0.0, "end": None, "in": round(start_in, 2),
                  "why": why}
