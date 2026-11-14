"""The edit decision list (EDL) - the contract every other module reads.

edit.json:
{
  "source": "/path/raw.mov", "orientation": "vertical", "width": 1080, "height": 1920, "fps": 60,
  "music": "ai", "music_style": "taiko", "source_audio": "duck",
  "segments": [
    {"src_in": 642.4, "src_out": 644.0, "speed": 0.5, "kind": "hook",
     "top": "IT TOOK ME|8 TRIES", "bottom": "WAIT FOR IT", "zoom": 1.25,
     "hits": [643.0], "impact": null}
  ]
}
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


def validate(edit: dict, source_duration: float | None = None) -> list[str]:
    errors = []
    for i, s in enumerate(edit["segments"]):
        if s["kind"] not in KINDS:
            errors.append(f"segment {i}: unknown kind {s['kind']!r}")
        if s["src_out"] <= s["src_in"]:
            errors.append(f"segment {i}: src_out must be after src_in")
        if not 0.1 <= s["speed"] <= 4:
            errors.append(f"segment {i}: speed {s['speed']} out of range 0.1..4")
        if source_duration and s["src_out"] > source_duration + 0.05:
            errors.append(f"segment {i}: src_out {s['src_out']} beyond source end {source_duration:.2f}")
    return errors
