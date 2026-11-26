"""Signal extraction and event detection.

Signals are sampled at RATE frames/sec from a tiny (64x36) decode, so a 10-minute 4K
recording analyses in a minute or two with no Python dependencies:
  brightness  mean luma-ish level 0..255
  redness     red/green ratio on the left/right screen edges (damage / death vignettes)
  motion      mean abs difference to the previous sample
  loudness    RMS dBFS of the loudest audio track (None when the recording is silent)
  where       where the motion is per sample: [x, y, spread] as fractions of the picture (None when
              too little moves); spread ~0.41 is motion all over (a camera turn), small is one spot
  grid        16x9 blocks over the whole recording: mean motion and brightness per block, to find
              screen corners that stay still while the picture moves (a HUD minimap)
  cues        times the user's on-screen signs show (a kill icon, a victory banner): each sign's box,
              cropped at full size 5 times a second in the same decode, compared (SSIM) with a frame
              where the user saw it
  scenes      camera/shot cuts, from ffmpeg's scdet on a 10 fps copy in the same decode (+-0.1 s)
  bars        black-bar boxes (letterbox/pillarbox) per sample, from ffmpeg's cropdetect, same decode

Detectors turn signals into events:
  dark events  fades to black/red; classified 'death' when a red vignette precedes them
  gameplay     deaths, the winning attempt, the win moment and the outro
  moments      generic top-scoring windows (motion + loudness + scene cuts)
"""
from __future__ import annotations

import json
import math
import os
import re
import statistics
import tempfile
from array import array

from .errors import InputError, MediaError
from .preflight import has_filter
from .probe import probe
from .tasks import Task, filter_quote, run

RATE = 2.0
W, H = 64, 36

DEFAULTS = {
    "dark_threshold": 35.0,     # brightness below this = screen is dark
    "death_redness": 1.1,       # edge red/green ratio before a fade that marks a death
    "min_attempt": 20.0,        # seconds of play between a death and a win to count as an attempt
    "scene_threshold": 10.0,    # scdet score (0-100) that counts as a cut
}
SCENE_RATE = 10                 # scdet looks at this many frames a second (cut times to 0.1 s)
SCENE_MERGE = 0.4               # cuts closer than this are one transition (flashes, dissolves)
BAR_MIN = 0.02                  # an edge band thinner than this share of the frame isn't a bar
BAR_AGREE = 0.6                 # share of a segment's samples that must show the same bar
BAR_RECUR = 0.2                 # share of the whole recording's samples that must show it too
GRID_W, GRID_H = 16, 9          # blocks of 4x4 scan pixels
CUE_RATE = 5                    # on-screen signs are checked this many times a second
CUE_SIZE = 64                   # ... at this size (a sign's box is scaled to it, in grey)
CUE_MERGE = 1.0                 # matches closer than this are one appearance
CUE_BOOST = 2.5                 # excitement added around each appearance
MIN_MOTION = 1.5                # mean per-pixel change below this: nothing to aim at


class _Frames:
    """Turns a raw rgb24 byte stream into per-sample signals as it arrives (no full decode in memory)."""

    def __init__(self, on_sample=None, ignore: list | None = None):
        self.size = W * H * 3
        # boxes whose motion isn't the action (a facecam, the user's HUD signs): left out of "where"
        self.ignore = [(int(b[0] * W), int(b[1] * H), int((b[0] + b[2]) * W + 0.999), int((b[1] + b[3]) * H + 0.999))
                       for b in ignore or []]
        self.edge = [(y * W + x) * 3 for y in range(H) for x in (0, 1, 2, W - 3, W - 2, W - 1)]
        self.bright, self.red, self.motion = [], [], []
        self.buf, self.prev, self.on_sample = bytearray(), None, on_sample
        self.bars = None
        self.where: list = []
        self.prev_g = None
        self.grid_motion = [0.0] * (GRID_W * GRID_H)
        self.grid_bright = [0.0] * (GRID_W * GRID_H)
        self.diffs = 0

    def feed(self, chunk: bytes) -> None:
        self.buf += chunk
        n = len(self.buf) // self.size
        for k in range(n):
            f = self.buf[k * self.size:(k + 1) * self.size]
            self.bright.append(round(sum(f) / self.size, 1))
            rr = sum(f[i] for i in self.edge); gg = sum(f[i + 1] for i in self.edge)
            self.red.append(round(rr / (gg + 1), 2))
            sample = f[::7]
            self.motion.append(0.0 if self.prev is None else
                               round(sum(abs(a - b) for a, b in zip(sample, self.prev)) / len(sample), 1))
            self.prev = sample
            self._where(f)
        del self.buf[:n * self.size]
        if n and self.on_sample:
            self.on_sample(len(self.bright) / RATE)


    def _where(self, f) -> None:
        """Where the picture changed since the last sample (green is close to luma), and the
        running per-block motion and brightness for the whole recording."""
        g = f[1::3]
        bw = W // GRID_W
        for y in range(H):
            row = g[y * W:(y + 1) * W]
            base = y // (H // GRID_H) * GRID_W
            for bx in range(GRID_W):
                self.grid_bright[base + bx] += sum(row[bx * bw:(bx + 1) * bw])
        if self.prev_g is None:
            self.prev_g = g
            self.where.append(None)
            return
        d = [abs(a - b) for a, b in zip(g, self.prev_g)]
        self.prev_g = g
        self.diffs += 1
        grid_d = d
        if self.ignore:
            d = list(d)
            for x0, y0, x1, y1 in self.ignore:
                for y in range(max(0, y0), min(H, y1)):
                    d[y * W + max(0, x0):y * W + min(W, x1)] = [0] * (min(W, x1) - max(0, x0))
        cols = [sum(d[x::W]) for x in range(W)]
        rows = [sum(d[y * W:(y + 1) * W]) for y in range(H)]
        for y in range(H):
            row = grid_d[y * W:(y + 1) * W]
            base = y // (H // GRID_H) * GRID_W
            for bx in range(GRID_W):
                self.grid_motion[base + bx] += sum(row[bx * bw:(bx + 1) * bw])
        total = sum(rows)
        if total < MIN_MOTION * W * H:
            self.where.append(None)
            return
        cx = sum(c * (x + 0.5) for x, c in enumerate(cols)) / total / W
        cy = sum(r * (y + 0.5) for y, r in enumerate(rows)) / total / H
        vx = sum(c * ((x + 0.5) / W - cx) ** 2 for x, c in enumerate(cols)) / total
        vy = sum(r * ((y + 0.5) / H - cy) ** 2 for y, r in enumerate(rows)) / total
        self.where.append([round(cx, 3), round(cy, 3), round(math.sqrt(vx + vy), 3)])

    def grid(self) -> dict:
        n, px = len(self.bright) or 1, (W // GRID_W) * (H // GRID_H)
        return {"cols": GRID_W, "rows": GRID_H,
                "motion": [round(v / max(1, self.diffs) / px, 2) for v in self.grid_motion],
                "bright": [round(v / n / px, 1) for v in self.grid_bright]}


def cue_crop(box: list) -> str:
    x, y, w, h = box
    even = lambda e: f"trunc({e}/2)*2"   # noqa: E731
    return (f"crop=w='max(2,{even(f'iw*{w}')})':h='max(2,{even(f'ih*{h}')})':x='{even(f'iw*{x}')}'"
            f":y='{even(f'ih*{y}')}',scale={CUE_SIZE}:{CUE_SIZE},format=gray")


def _cue_refs(path: str, cues: list[dict], task: Task | None, made: list[str]) -> list[str]:
    """A still of each sign's box at the moment the user gave (the reference it is matched against).
    Each file is also added to `made` as soon as it exists, so a failure leaves nothing behind."""
    refs = []
    for k, c in enumerate(cues):
        fd, p = tempfile.mkstemp(prefix=f"nookcut_cue{k}_", suffix=".png")
        os.close(fd)
        refs.append(p)
        made.append(p)
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{c['t']:.3f}", "-i", path, "-frames:v", "1",
             "-vf", cue_crop(c["box"]), p], task, f"reading the sign '{c['label']}'")
    return refs


def parse_ssim(stats: str) -> list[float]:
    """ssim's stats file -> one similarity (0-1) per checked frame."""
    return [float(m) for m in re.findall(r"All:([0-9.]+)", stats)]


def cue_times(values: list[float], at: float, rate: float = CUE_RATE) -> dict:
    """When a sign shows: the starts of runs of frames that look like the reference. The bar sits
    between the usual look of the box and the reference moment itself, so a sign over changing
    scenery is still found; a sign that barely differs from its surroundings is reported as such."""
    if not values:
        return {"at": [], "note": "the sign's box couldn't be read"}
    ordered = sorted(values)
    usual = ordered[len(ordered) // 2]
    k = min(len(values) - 1, int(round(at * rate)))
    ref = max(values[max(0, k - 2):k + 3])
    if ref - usual < 0.15:
        return {"at": [], "note": ("the box looks the same most of the time; pick a moment where the sign "
                                   "is clearly on screen, and a box tight around it")}
    bar = max(0.5, usual + 0.6 * (ref - usual))
    out: list[float] = []
    last = None
    for i, v in enumerate(values):
        if v >= bar:
            t = i / rate
            if last is None or t - last > CUE_MERGE:
                out.append(round(t, 2))
            last = t
    return {"at": out, "bar": round(bar, 3)}


def _decode(path: str, hwaccel: str | None, task: Task | None, on_sample=None,
            scene_threshold: float = DEFAULTS["scene_threshold"],
            cues: list[dict] | None = None, ignore: list | None = None) -> tuple[_Frames, list[float] | None]:
    """One decode: the 64x36 signal frames, and scene cuts (None when ffmpeg has no scdet)."""
    scenes, bars = has_filter("scdet"), has_filter("cropdetect")
    cues = cues or []
    refs: list[str] = []
    files = []
    for name in ["cuts", "bars"] + [f"cue{k}" for k in range(len(cues))]:
        fd, p = tempfile.mkstemp(prefix=f"nookcut_{name}_", suffix=".txt")
        os.close(fd)
        files.append(p)
    cuts_file, bars_file, cue_files = files[0], files[1], files[2:]
    try:
        refs += _cue_refs(path, cues, task, refs) if cues else []
        for accel in ([hwaccel, None] if hwaccel else [None]):
            frames = _Frames(on_sample, ignore)
            cmd = ["ffmpeg", "-v", "error"] + (["-hwaccel", accel] if accel else []) + ["-i", path]
            for ref in refs:
                cmd += ["-i", ref]
            cmd += ["-an"]
            extra = []
            for k, c in enumerate(cues):
                extra.append(f"fps={CUE_RATE},{cue_crop(c['box'])}[cm{k}];[{k + 1}:v]format=gray[cr{k}];"
                             f"[cm{k}][cr{k}]ssim=stats_file={filter_quote(cue_files[k])},nullsink")
            if scenes:
                extra.append(f"fps={SCENE_RATE},scale=160:90,scdet=t={scene_threshold},"
                             f"metadata=print:key=lavfi.scd.time:file={filter_quote(cuts_file)},nullsink")
            if bars:
                extra.append(f"fps={RATE},cropdetect=limit=0.094:round=2:reset=1:skip=0,"
                             f"metadata=print:file={filter_quote(bars_file)},nullsink")
            if extra:
                labels = "".join(f"[x{k}]" for k in range(len(extra)))
                cmd += ["-filter_complex",
                        f"[0:v]split={len(extra) + 1}[a]{labels};[a]fps={RATE},scale={W}:{H}[s];"
                        + ";".join(f"[x{k}]{f}" for k, f in enumerate(extra)), "-map", "[s]"]
            else:
                cmd += ["-vf", f"fps={RATE},scale={W}:{H}"]
            cmd += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
            try:
                run(cmd, task, "scanning the video", on_chunk=frames.feed)
                with open(cuts_file, encoding="utf-8") as f:
                    cuts = merge_cuts(f.read()) if scenes else None
                with open(bars_file, encoding="utf-8") as f:
                    frames.bars = parse_bars(f.read()) if bars else None
                frames.cues = []
                for c, cf in zip(cues, cue_files):
                    with open(cf, encoding="utf-8") as f:
                        frames.cues.append({"label": c["label"], "t": c["t"], "box": c["box"],
                                            **cue_times(parse_ssim(f.read()), c["t"])})
                return frames, cuts
            except MediaError:
                if not accel:
                    raise
        raise AssertionError("unreachable")
    finally:
        for p in files + refs:
            if os.path.exists(p):
                os.remove(p)


def parse_bars(metadata: str) -> list[list]:
    """cropdetect's printed values -> [[t, x, y, w, h], ...] (content box per sample; empty frames
    such as all-black ones give no box)."""
    out = []
    for block in re.split(r"frame:\d+", metadata)[1:]:
        t = re.search(r"pts_time:([0-9.]+)", block)
        v = {k: int(x) for k, x in re.findall(r"lavfi\.cropdetect\.(w|h|x|y)=(-?\d+)", block)}
        if t and len(v) == 4 and v["w"] > 0 and v["h"] > 0:
            out.append([round(float(t.group(1)), 2), v["x"], v["y"], v["w"], v["h"]])
    return out


def content_box(bars: list[list] | None, a: float, b: float, width: int, height: int) -> list[int] | None:
    """The picture inside black bars for the source range [a, b): [x, y, w, h], or None when there are
    no bars to cut. Edges come from the union of the samples (never crops picture), and an edge is
    only cut when most samples agree on it: a dark scene reports a smaller box, but bars don't move.
    A bar must also recur across the recording (a dark wall in one shot doesn't), and side bars
    must be symmetric, as pillarboxing is."""
    samples = [s[1:] for s in bars or [] if a <= s[0] < b]
    if not samples:
        return None
    top, left = min(s[1] for s in samples), min(s[0] for s in samples)
    bottom = height - max(s[1] + s[3] for s in samples)
    right = width - max(s[0] + s[2] for s in samples)

    everywhere = [s[1:] for s in bars]

    def agreed(edge: int, side, size: int) -> int:
        if edge < BAR_MIN * size:
            return 0
        here = sum(abs(side(v) - edge) <= 0.01 * size for v in samples)
        overall = sum(abs(side(v) - edge) <= 0.01 * size for v in everywhere)
        return edge if here >= BAR_AGREE * len(samples) and overall >= BAR_RECUR * len(everywhere) else 0

    top = agreed(top, lambda v: v[1], height)
    bottom = agreed(bottom, lambda v: height - v[1] - v[3], height)
    left = agreed(left, lambda v: v[0], width)
    right = agreed(right, lambda v: width - v[0] - v[2], width)
    if abs(left - right) > 0.01 * width:  # one dark side is scenery, not pillarboxing
        left = right = 0
    if not (top or bottom or left or right):
        return None
    w, h = (width - left - right) // 2 * 2, (height - top - bottom) // 2 * 2
    return [left // 2 * 2, top // 2 * 2, w, h]


def merge_cuts(metadata: str) -> list[float]:
    """scdet's printed lavfi.scd.time values -> cut times, one per transition."""
    out: list[float] = []
    for t in sorted(float(x) for x in re.findall(r"lavfi\.scd\.time=([0-9.]+)", metadata)):
        if not out or t - out[-1] >= SCENE_MERGE:
            out.append(round(t, 2))
    return out


def _loudness(path: str, track: int, n: int, task: Task | None, on_progress=None) -> list[float]:
    a, carry = array("h"), bytearray()

    def chunk(b: bytes):
        carry.extend(b)
        k = len(carry) // 2 * 2
        a.frombytes(bytes(carry[:k]))
        del carry[:k]
        if on_progress:
            on_progress(len(a) / 8000)

    run(["ffmpeg", "-v", "error", "-i", path, "-map", f"0:a:{track}", "-ac", "1",
         "-ar", "8000", "-f", "s16le", "-"], task, "measuring loudness", on_chunk=chunk)
    step = int(8000 / RATE)
    out = []
    for k in range(n):
        seg = a[k * step:(k + 1) * step]
        rms = math.sqrt(sum(x * x for x in seg) / len(seg)) if seg else 0
        out.append(round(20 * math.log10(rms / 32768), 1) if rms else -90.0)
    return out


def signals(path: str, hwaccel: str | None = None, task: Task | None = None, cues: list[dict] | None = None,
            ignore: list | None = None) -> dict:
    task = task or Task(track=False)
    task.begin("reading the source", note="checking streams and audio tracks")
    info = probe(path, task)
    dur = info["duration"]
    task.begin("scanning the video", dur, note=f"{dur / 60:.1f} min of footage")
    late = [c for c in cues or [] if c["t"] >= dur]
    if late:
        raise InputError(f"The sign '{late[0]['label']}' is set at {late[0]['t']:.1f}s, after the recording ends "
                         f"({dur:.1f}s).", "Give a time when the sign is on screen.")
    # the user's sign boxes and facecam move without being the action
    ignore = list(ignore or []) + [c["box"] for c in cues or []]
    frames, scenes = _decode(path, hwaccel, task, lambda t: task.set(t), cues=cues, ignore=ignore)
    if not frames.bright:
        raise MediaError("Scanning the video failed: no frames could be decoded.",
                         "The file may be damaged; try re-exporting it.")
    live = [t for t in info["audio_tracks"] if t["peak_db"] > -60]
    loud = None
    if live:
        best = max(live, key=lambda t: t["peak_db"])
        task.begin("measuring loudness", dur, note=f"audio track {best['index'] + 1}")
        loud = _loudness(path, best["index"], len(frames.bright), task, lambda t: task.set(t))
    return {"info": info, "rate": RATE, "brightness": frames.bright, "redness": frames.red,
            "motion": frames.motion, "loudness": loud, "scenes": scenes, "bars": frames.bars,
            "where": frames.where, "grid": frames.grid(), "cues": getattr(frames, "cues", [])}


def dark_events(sig: dict, params: dict) -> list[dict]:
    B, R, rate = sig["brightness"], sig["redness"], sig["rate"]
    thr = params["dark_threshold"]
    runs, k = [], 0
    while k < len(B):
        if B[k] < thr:
            j = k
            while j + 1 < len(B) and (B[j + 1] < thr or (j + 2 < len(B) and B[j + 2] < thr)):
                j += 1
            runs.append((k, j))
            k = j + 1
        else:
            k += 1
    events = []
    for s, e in runs:
        look = int(4 * rate)
        base_win = B[max(0, s - 4 * look):max(1, s - look)] or B[:1]
        base = statistics.median(base_win)
        onset = s
        for j in range(max(0, s - look), s + 1):
            if B[j] < 0.6 * base:
                onset = j
                break
        red_pre = max(R[max(0, onset - look):onset + 2] or [0])
        events.append({
            "onset": round(onset / rate, 2), "start": round(s / rate, 2), "end": round((e + 1) / rate, 2),
            "red_pre": red_pre,
            "kind": "death" if red_pre >= params["death_redness"] else "cut",
        })
    return events


def gameplay(sig: dict, events: list[dict], params: dict, key_moments: list[dict]) -> dict:
    deaths = [e for e in events if e["kind"] == "death"]
    cuts = [e for e in events if e["kind"] == "cut"]
    win = None
    after = deaths[-1]["end"] if deaths else 0.0
    for c in cuts:
        if c["onset"] - after >= params["min_attempt"]:
            win = {**c, "by": "detected"}
            break
    for km in key_moments:   # the user knows best
        if any(w in km.get("label", "").lower() for w in ("win", "final", "kill", "victory", "blow")):
            win = {"onset": float(km["t"]) + 0.6, "start": float(km["t"]) + 0.6, "end": float(km["t"]) + 1.5,
                   "kind": "cut", "red_pre": 0, "by": "user"}
    intro = next((c for c in cuts if not deaths or c["end"] < deaths[0]["onset"]), None)
    final_attempt = None
    if win:
        start = next((d["end"] for d in reversed(deaths) if d["end"] < win["onset"]), 0.0)
        final_attempt = {"start": start, "end": win["onset"]}
    return {"deaths": deaths, "win": win, "intro_cut": intro, "final_attempt": final_attempt}


def moment_scores(sig: dict) -> list[float]:
    """Per-sample excitement: normalised motion + loudness, with scene cuts damped."""
    M = sig["motion"]
    mu = statistics.mean(M) or 1
    sd = statistics.pstdev(M) or 1
    L = sig["loudness"]
    if L:
        lmu, lsd = statistics.mean(L), statistics.pstdev(L) or 1
    cut_samples = {round(c * sig["rate"]) + d for c in sig.get("scenes") or [] for d in (0, 1)}
    out = []
    for k, m in enumerate(M):
        z = (m - mu) / sd
        if z > 4 or k in cut_samples:  # a scene cut is a change of picture, not action
            z = min(z, 0.5)
        if L:
            z += 0.8 * (L[k] - lmu) / lsd
        if sig["brightness"][k] < 35:
            z -= 3
        out.append(round(z, 3))
    for c in sig.get("cues") or []:   # the user's own signs are the surest moments there are
        for t in c["at"]:
            for k in range(max(0, int((t - 1.0) * sig["rate"])), min(len(out), int((t + 1.5) * sig["rate"]) + 1)):
                out[k] = round(out[k] + CUE_BOOST, 3)
    return out


def best_windows(scores: list[float], rate: float, lo: float, hi: float,
                 length: float, count: int) -> list[tuple[float, float]]:
    """Top non-overlapping windows of `length` seconds inside [lo, hi]."""
    n = int(length * rate)
    a, b = int(lo * rate), int(hi * rate) - n
    if b <= a:
        return []
    cand = sorted(((sum(scores[i:i + n]), i) for i in range(a, b)), reverse=True)
    taken: list[int] = []
    for _, i in cand:
        if all(abs(i - j) >= n + rate for j in taken):
            taken.append(i)
        if len(taken) == count:
            break
    return sorted((round(i / rate, 2), round((i + n) / rate, 2)) for i in taken)


def peak_times(scores: list[float], rate: float, lo: float, hi: float, k: int = 3) -> list[float]:
    a, b = int(lo * rate), int(hi * rate)
    idx = sorted(range(a, min(b, len(scores))), key=lambda i: -scores[i])[:k]
    return sorted(round(i / rate, 2) for i in idx)


def analyze(path: str, out_json: str | None = None, params: dict | None = None,
            key_moments: list[dict] | None = None, hwaccel: str | None = None,
            task: Task | None = None, more_stages: tuple = (), cues: list[dict] | None = None,
            ignore: list | None = None) -> dict:
    p = {**DEFAULTS, **(params or {})}
    if task:
        task.plan(("reading the source", 1), ("scanning the video", 6), ("measuring loudness", 2),
                  ("detecting moments", 0.2), *more_stages)
    sig = signals(path, hwaccel, task, cues, ignore)
    if task:
        task.begin("detecting moments")
    events = dark_events(sig, p)
    # a sign named like a win (victory, winner, final kill) marks the win at its last appearance;
    # signs that repeat (a kill icon) only raise the excitement around each appearance
    signed = [{"t": c["at"][-1], "label": c["label"]} for c in sig.get("cues") or []
              if c["at"] and any(w in c["label"].lower() for w in ("win", "victory", "final", "champion"))]
    result = {"signals": sig, "params": p, "events": events,
              "gameplay": gameplay(sig, events, p, signed + (key_moments or [])),
              "scores": moment_scores(sig)}
    if out_json:
        with open(out_json, "w") as f:
            json.dump(result, f)
    return result


SUMMARY_LIST = 20   # longer lists stay in analysis.json, so a long stream doesn't flood the agent


def _listed(events: list[dict], key) -> dict:
    """{count, at: [...]}: every onset when few, else the SUMMARY_LIST strongest (by `key`), in time order."""
    top = sorted(events, key=key, reverse=True)[:SUMMARY_LIST]
    out = {"count": len(events), "at": sorted(e["onset"] for e in top)}
    if len(events) > SUMMARY_LIST:
        out["note"] = f"the {SUMMARY_LIST} strongest; all {len(events)} are in analysis.json"
    return out


def summary(result: dict) -> dict:
    """Small, human/agent-readable view of an analysis (no raw signal arrays, lists capped)."""
    g = result["gameplay"]
    info = result["signals"]["info"]
    cuts = [e for e in result["events"] if e["kind"] == "cut"]
    return {
        "duration": round(info["duration"], 1),
        "resolution": f'{info["width"]}x{info["height"]}@{info["fps"]:.2f}',
        "audio": "silent" if result["signals"]["loudness"] is None else "has sound",
        "deaths": _listed(g["deaths"], lambda e: e["red_pre"]),
        "win_at": g["win"]["onset"] if g["win"] else None,
        "final_attempt": g["final_attempt"],
        "fades": _listed(cuts, lambda e: e["end"] - e["start"]),   # fades that aren't deaths; longest first
        **({"cues": {c["label"]: {"count": len(c["at"]), "at": c["at"][:SUMMARY_LIST],
                                  **({"note": c["note"]} if c.get("note") else {})}
                     for c in result["signals"].get("cues") or []}} if result["signals"].get("cues") else {}),
        "scene_cuts": (len(result["signals"]["scenes"]) if result["signals"].get("scenes") is not None
                       else "not detected (this ffmpeg has no scdet filter)"),
    }


HUD_STILL = 0.6    # a HUD block moves at most this share of the picture's mean motion (a map scrolls a little)
HUD_ORDER = ("top-left", "top-right", "bottom-right", "bottom-left")   # where games usually put the map
HUD_CLEAR = 1.5    # a corner must score this much above the next to beat a usual map corner
HUD_DARK = 20      # mean brightness below this is black bars, not a map


def hud_corner(grid: dict | None) -> dict | None:
    """Guess the minimap: the largest box in a screen corner whose blocks stay still while the rest
    of the picture moves, and isn't black. Returns {"box": [x, y, w, h] as fractions, "corner",
    "still"} or None (no corner stands out: the picture barely moves, or nothing is still)."""
    if not grid:
        return None
    cols, rows, M, B = grid["cols"], grid["rows"], grid["motion"], grid["bright"]
    mean = sum(M) / len(M)
    if mean < 0.5:
        return None
    still = [m / mean <= HUD_STILL and b >= HUD_DARK for m, b in zip(M, B)]
    found = {}
    for corner, (left, top) in {"top-left": (True, True), "top-right": (False, True),
                                "bottom-right": (False, False), "bottom-left": (True, False)}.items():
        best = None
        for w in range(2, cols // 3 + 1):
            for h in range(2, rows // 2 + 1):
                xs = range(w) if left else range(cols - w, cols)
                ys = range(h) if top else range(rows - h, rows)
                if not all(still[y * cols + x] for y in ys for x in xs):
                    continue
                calm = 1 - sum(M[y * cols + x] for y in ys for x in xs) / (w * h) / mean
                score = w * h * (0.5 + calm)
                if not best or score > best[0]:
                    best = (score, corner, [xs[0] / cols, ys[0] / rows, w / cols, h / rows], calm)
        if best:
            found[corner] = best
    if not found:
        return None
    ranked = sorted(found.values(), key=lambda b: -b[0])
    pick = ranked[0]
    if len(ranked) > 1 and ranked[0][0] < HUD_CLEAR * ranked[1][0]:
        # several still corners, none clearly the map: the usual map corner among them
        pick = next(found[c] for c in HUD_ORDER if c in found)
    # the map's edge blocks are only partly still, so the box grows half a block on its inner sides
    x, y, w, h = pick[2]
    gx, gy = 0.5 / cols, 0.5 / rows
    left, top = pick[1].endswith("left"), pick[1].startswith("top")
    x, w = (x, min(1 - x, w + gx)) if left else (max(0.0, x - gx), w + min(gx, x))
    y, h = (y, min(1 - y, h + gy)) if top else (max(0.0, y - gy), h + min(gy, y))
    return {"box": [round(v, 4) for v in (x, y, w, h)], "corner": pick[1], "still": round(pick[3], 2),
            "others": [b[1] for b in ranked if b is not pick]}
