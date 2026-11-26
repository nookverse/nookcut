"""The edit decision list (EDL) - the contract every other module reads.

edit.json:
{
  "source": "/path/raw.mov", "orientation": "vertical", "width": 1080, "height": 1920, "fps": 60,
  "music": "ai", "music_style": "taiko", "source_audio": "duck",
  "segments": [
    {"src_in": 642.4, "src_out": 644.0, "speed": 0.5, "kind": "hook",
     "top": "IT TOOK ME|8 TRIES", "bottom": "WAIT FOR IT", "zoom": 1.25,
     "hits": [643.0], "impact": null, "focus": [0.62, 0.5], "focus_end": [0.4, 0.5]}
  ]
}
framing "split" (vertical) stacks a panel on top ("panel": {"kind": "facecam"|"minimap", "box": [x, y, w, h]
as fractions of the whole picture, or "file": a separate webcam recording, "offset": s}) over the action.
In split, the action below the panel is a window of the picture; focus is that window's centre as
fractions of the picture (0.5, 0.5 = middle), focus_end makes it pan there over the segment.
'|' in captions forces a line break. kind drives transitions and music:
hook, intro, death, card, highlight, slowmo, finisher, outro, end.
"""
from __future__ import annotations

import json

KINDS = ("hook", "intro", "death", "card", "highlight", "slowmo", "finisher", "outro", "end")


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(edit: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(edit, f, indent=2, ensure_ascii=False)


def output_fps(source_fps: float, choice: str = "auto") -> str:
    """Output frame rate as ffmpeg writes it: 60 (or 30), in the source's family so frames aren't
    repeated or dropped in a stutter: 60000/1001 for 59.94/29.97/23.976 sources, 50 (or 25) for
    25/50/100 fps (PAL) sources."""
    if abs(source_fps - round(source_fps)) < 0.01 and round(source_fps) in (25, 50, 100):
        return "25" if choice == "30" else "50"
    base = 30 if choice == "30" else 60
    ntsc = abs(source_fps - round(source_fps)) > 0.01 and abs(source_fps * 1.001 - round(source_fps * 1.001)) < 0.02
    return f"{base * 1000}/1001" if ntsc else str(base)


def fps_value(edit: dict) -> float:
    num, _, den = str(edit.get("fps") or 60).partition("/")
    return float(num) / float(den or 1)


def seg(src_in, src_out, kind, speed=1.0, top="", bottom="", zoom=1.0, hits=None, impact=None) -> dict:
    return {"src_in": round(src_in, 3), "src_out": round(src_out, 3), "speed": speed, "kind": kind,
            "top": top, "bottom": bottom, "zoom": zoom, "hits": hits or [], "impact": impact}


def seg_duration(s: dict) -> float:
    return (s["src_out"] - s["src_in"]) / s["speed"]


def timeline(edit: dict) -> list[tuple[float, float, dict]]:
    out, t = [], 0.0
    for s in edit["segments"]:
        d = seg_duration(s)
        out.append((round(t, 4), round(d, 4), s))
        t += d
    return out


def total(edit: dict) -> float:
    return round(sum(seg_duration(s) for s in edit["segments"]), 3)


def to_output(start: float, s: dict, src_t: float) -> float:
    return start + (src_t - s["src_in"]) / s["speed"]


def _point(v) -> bool:
    return (isinstance(v, (list, tuple)) and len(v) == 2
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) and 0 <= x <= 1 for x in v))


def validate(edit: dict, source_duration: float | None = None) -> list[str]:
    errors = []
    if edit.get("framing", "band") == "full":
        errors.append("full-bleed framing isn't available any more; use band or split (update_edit framing)")
    elif edit.get("framing", "band") not in ("band", "split"):
        errors.append(f"framing {edit.get('framing')!r} is unknown; use band or split")
    if edit.get("framing") == "split":
        p = edit.get("panel") or {}
        if edit.get("orientation") != "vertical":
            errors.append("framing split is for vertical videos")
        if p.get("box") is not None and not (isinstance(p["box"], (list, tuple)) and len(p["box"]) == 4 and
                                             all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in p["box"])):
            errors.append("panel box must be [x, y, width, height], each 0-1 (fractions of the picture)")
        if p.get("kind") not in (None, "facecam", "minimap"):
            errors.append("panel kind must be facecam or minimap")
        if p.get("style") not in (None, "card", "full"):
            errors.append("panel style must be card or full")
    if edit.get("caption_style", "highlight") not in ("highlight", "streamer"):
        errors.append("caption_style must be highlight or streamer")
    if len(edit.get("label") or "") > 40:
        errors.append("label must be at most 40 characters")
    for i, s in enumerate(edit["segments"]):
        if s["kind"] not in KINDS:
            errors.append(f"segment {i}: unknown kind {s['kind']!r}")
        if s["src_out"] <= s["src_in"]:
            errors.append(f"segment {i}: src_out must be after src_in")
        if not 0.1 <= s["speed"] <= 4:
            errors.append(f"segment {i}: speed {s['speed']} out of range 0.1..4")
        if source_duration and s["src_out"] > source_duration + 0.05:
            errors.append(f"segment {i}: src_out {s['src_out']} beyond source end {source_duration:.2f}")
        for key in ("focus", "focus_end"):
            if s.get(key) is not None and not _point(s[key]):
                errors.append(f"segment {i}: {key} must be [x, y], each 0-1 (0.5 is the middle of the picture)")
        if not 1 <= float(s.get("zoom") or 1) <= 4:
            errors.append(f"segment {i}: zoom {s.get('zoom')} out of range 1..4")
        if s.get("vo") is not None and (not isinstance(s["vo"], str) or len(s["vo"]) > 400):
            errors.append(f"segment {i}: vo must be text of at most 400 characters")
        if s.get("vo_at") is not None and not 0 <= float(s["vo_at"]) < seg_duration(s):
            errors.append(f"segment {i}: vo_at must be inside the segment (0-{seg_duration(s):.1f} s)")
        if s.get("broll") and edit.get("hdr") == "keep" and edit.get("source_hdr"):
            errors.append(f"segment {i}: B-roll can't be shown in HDR output yet (set hdr to sdr, or remove it)")
        elif s.get("broll"):
            from .broll import check
            errors += [f"segment {i}: {e}" for e in check(s["broll"], edit.get("broll_dir", ""), seg_duration(s))]
    return errors
