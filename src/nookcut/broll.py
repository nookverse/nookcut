"""B-roll: the user's own clips and pictures, shown over a segment's picture.

Only files in the brief's broll_dir are used (NookCut never downloads any). A segment's B-roll is a
picture layer: its cut points, speed, sound, captions and music stay as they are.

  segment["broll"] = {"file": "name inside broll_dir", "layout": "cover" | "inset" | "split",
                      "main": "broll" | "source"  (inset: which picture is full-size),
                      "start": 0.0, "end": 4.0    (seconds into the segment's output; end may be null),
                      "in": 0.0                   (where the clip starts), "why": "..."}

  cover  the B-roll fills the picture area (the speaker keeps talking underneath)
  inset  one picture full-size, the other in a box in the corner
  split  side by side, half each
  fit    the whole clip, over a blurred copy of itself (a landscape clip in a vertical video)
"""
from __future__ import annotations

import json
import os
import re

from .errors import InputError, MediaError
from .tasks import Task, run

VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp")
LAYOUTS = ("cover", "fit", "inset", "split")
MAX_FILES = 300
# auto-placement: how long one B-roll shot lasts, and how much of the video B-roll may cover
SHOT_MAX = 4.0
SHOT_MIN = 1.2
COVER_SHARE = 0.35
STOP = {"the", "and", "for", "with", "this", "that", "from", "clip", "video", "final", "copy", "broll",
        "img", "dsc", "mov", "edit", "export", "untitled", "screen", "recording", "shot", "take",
        "you", "your", "are", "was", "but", "not", "all", "can", "get", "got", "one", "now", "out", "just",
        "like", "what", "when", "then", "there", "here", "have", "has", "his", "her", "its", "our", "they"}
COMMON = 3        # a word the speaker says more often than this names nothing in particular


# ---------- the folder ----------

def resolve(broll_dir: str, name: str) -> str:
    """A B-roll name -> its real path, refusing anything outside broll_dir (also via ../ or links)."""
    if not broll_dir:
        raise InputError("This project has no B-roll folder.", "Set broll_dir in the brief (create_project).")
    base = os.path.realpath(os.path.expanduser(broll_dir))
    path = os.path.realpath(os.path.join(base, name))
    if not path.startswith(base + os.sep):
        raise InputError(f"{name} isn't inside the B-roll folder.", "Use a file name from list_broll.")
    if not os.path.isfile(path):
        raise InputError(f"There is no B-roll file called {name}.", "Use a file name from list_broll.")
    return path


def _files(base: str) -> list[str]:
    out = []
    for root, dirs, names in os.walk(base):
        dirs[:] = [d for d in sorted(dirs) if not d.startswith(".")][:50]
        if os.path.relpath(root, base).count(os.sep) >= 2:
            dirs[:] = []
        for n in sorted(names):
            path = os.path.join(root, n)
            inside = os.path.realpath(path).startswith(base + os.sep)  # links out of the folder are skipped
            if n.lower().endswith(VIDEO_EXT + IMAGE_EXT) and not n.startswith(".") and inside:
                out.append(os.path.relpath(path, base))
            if len(out) >= MAX_FILES:
                return out
    return out


def _probe_one(path: str, task: Task | None) -> dict:
    raw = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
               "format=duration:stream=width,height,color_transfer,color_space,codec_name", "-of", "json", path],
              task, "reading a B-roll file", capture=True, timeout=60)
    info = json.loads(raw)
    v = (info.get("streams") or [{}])[0]
    still = path.lower().endswith(IMAGE_EXT) or v.get("codec_name") in ("png", "mjpeg", "webp", "bmp")
    return {"still": still, "duration": None if still else float(info.get("format", {}).get("duration") or 0),
            "width": v.get("width"), "height": v.get("height"),
            "hdr": v.get("color_transfer") in ("smpte2084", "arib-std-b67"),
            "matrix": v.get("color_space") if v.get("color_space") not in (None, "unknown") else None}


def index(broll_dir: str, cache_file: str | None = None, task: Task | None = None) -> list[dict]:
    """Every usable file in the folder (two levels deep, up to MAX_FILES), with what rendering needs.
    Probes are cached by size and modification time."""
    base = os.path.realpath(os.path.expanduser(broll_dir or ""))
    if not os.path.isdir(base):
        raise InputError(f"The B-roll folder can't be found: {broll_dir}", "Check the path and that the drive is mounted.")
    cache = {}
    if cache_file and os.path.exists(cache_file):
        try:
            with open(cache_file, encoding="utf-8") as f:
                cache = json.load(f)
        except ValueError:
            cache = {}
    out, fresh = [], {}
    for name in _files(base):
        path = os.path.join(base, name)
        st = os.stat(path)
        key = f"{name}|{st.st_size}|{int(st.st_mtime)}"
        item = cache.get(key)
        if item is None:
            try:
                item = _probe_one(path, task)
            except MediaError:
                continue  # unreadable: leave it out rather than fail the whole folder
        fresh[key] = item
        out.append({"file": name, **item})
    if cache_file:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(fresh, f)
    return out


# ---------- placing it ----------

def tokens(name: str) -> set[str]:
    """Words a file name stands for: 'boss_fight-2.mp4' -> {'boss', 'fight'}."""
    words = re.findall(r"[a-z]+", os.path.splitext(os.path.basename(name))[0].lower())
    return {w for w in words if len(w) >= 3 and w not in STOP}


def _forms(word: str) -> set[str]:
    """A spoken word and its singular guesses: 'bosses' -> {'bosses', 'bosse', 'boss'}."""
    w = re.sub(r"[^a-z0-9]+", "", word.lower())
    out = {w} if len(w) >= 3 else set()
    if len(w) > 3 and w.endswith("s"):
        out.add(w[:-1])
    if len(w) > 4 and w.endswith("es"):
        out.add(w[:-2])
    return out


def auto_place(edit: dict, files: list[dict]) -> list[str]:
    """Put B-roll where the speech names it: a file whose name has a word the speaker says covers the
    picture from that word for up to SHOT_MAX seconds. Deterministic; each file is used once; the
    hook and end card stay on the source; B-roll covers at most COVER_SHARE of the video. Returns
    a line per placement saying why."""
    from .edit import total
    budget = COVER_SHARE * total(edit)
    used, notes, covered = set(), [], 0.0
    said: dict[str, int] = {}
    for s in edit["segments"]:
        for w in s.get("words") or []:
            for f in _forms(w[2]):
                said[f] = said.get(f, 0) + 1
    names = {f["file"]: {t for t in tokens(f["file"]) if said.get(t, 0) <= COMMON} for f in files}
    for s in edit["segments"]:
        if s["kind"] in ("hook", "end") or s.get("broll") or not s.get("words"):
            continue
        dur = (s["src_out"] - s["src_in"]) / s["speed"]
        for w in s["words"]:
            forms = _forms(w[2])
            match = next((f for f, t in names.items() if f not in used and forms & t), None)
            if not match:
                continue
            start = max(0.0, (w[0] - s["src_in"]) / s["speed"])
            end = min(dur, start + SHOT_MAX)
            if end - start < SHOT_MIN or covered + end - start > budget:
                continue
            s["broll"] = {"file": match, "layout": "cover", "start": round(start, 2), "end": round(end, 2),
                          "in": 0.0, "why": f"the speaker says '{w[2].strip('.,!?')}'"}
            used.add(match)
            covered += end - start
            notes.append(f"{match}: {start:.1f}-{end:.1f} s into segment {edit['segments'].index(s) + 1} "
                         f"({s['broll']['why']})")
            break
    return notes


def check(b: dict, broll_dir: str, seg_dur: float) -> list[str]:
    """Problems with a segment's broll entry (for edit validation)."""
    errors = []
    if b.get("layout", "cover") not in LAYOUTS:
        errors.append(f"broll layout {b.get('layout')!r} is unknown; use {', '.join(LAYOUTS)}")
    if b.get("main", "broll") not in ("broll", "source"):
        errors.append("broll main must be 'broll' or 'source'")
    start, end = float(b.get("start") or 0), b.get("end")
    if end is not None and not 0 <= start < float(end) <= seg_dur + 0.05:
        errors.append(f"broll start/end must be inside the segment (0-{seg_dur:.1f} s)")
    try:
        resolve(broll_dir, b.get("file", ""))
    except InputError as e:
        errors.append(e.message)
    return errors
