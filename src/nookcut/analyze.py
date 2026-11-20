"""Signal extraction and event detection.

Signals are sampled at RATE frames/sec from a tiny (64x36) decode, so a 10-minute 4K
recording analyses in a minute or two with no Python dependencies:
  brightness  mean luma-ish level 0..255
  redness     red/green ratio on the left/right screen edges (damage / death vignettes)
  motion      mean abs difference to the previous sample
  loudness    RMS dBFS of the loudest audio track (None when the recording is silent)
  scenes      camera/shot cuts, from ffmpeg's scdet on a 10 fps copy in the same decode (+-0.1 s)

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

from .errors import MediaError
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


class _Frames:
    """Turns a raw rgb24 byte stream into per-sample signals as it arrives (no full decode in memory)."""

    def __init__(self, on_sample=None):
        self.size = W * H * 3
        self.edge = [(y * W + x) * 3 for y in range(H) for x in (0, 1, 2, W - 3, W - 2, W - 1)]
        self.bright, self.red, self.motion = [], [], []
        self.buf, self.prev, self.on_sample = bytearray(), None, on_sample

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
        del self.buf[:n * self.size]
        if n and self.on_sample:
            self.on_sample(len(self.bright) / RATE)


def _decode(path: str, hwaccel: str | None, task: Task | None, on_sample=None,
            scene_threshold: float = DEFAULTS["scene_threshold"]) -> tuple[_Frames, list[float] | None]:
    """One decode: the 64x36 signal frames, and scene cuts (None when ffmpeg has no scdet)."""
    scenes = has_filter("scdet")
    fd, cuts_file = tempfile.mkstemp(prefix="nookcut_cuts_", suffix=".txt")
    os.close(fd)
    try:
        for accel in ([hwaccel, None] if hwaccel else [None]):
            frames = _Frames(on_sample)
            cmd = ["ffmpeg", "-v", "error"] + (["-hwaccel", accel] if accel else []) + ["-i", path, "-an"]
            if scenes:
                cmd += ["-filter_complex",
                        f"[0:v]split[a][b];[a]fps={RATE},scale={W}:{H}[s];[b]fps={SCENE_RATE},scale=160:90,"
                        f"scdet=t={scene_threshold},metadata=print:key=lavfi.scd.time:file={filter_quote(cuts_file)},"
                        "nullsink", "-map", "[s]"]
            else:
                cmd += ["-vf", f"fps={RATE},scale={W}:{H}"]
            cmd += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
            try:
                run(cmd, task, "scanning the video", on_chunk=frames.feed)
                with open(cuts_file, encoding="utf-8") as f:
                    return frames, merge_cuts(f.read()) if scenes else None
            except MediaError:
                if not accel:
                    raise
        raise AssertionError("unreachable")
    finally:
        os.remove(cuts_file)


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


def signals(path: str, hwaccel: str | None = None, task: Task | None = None) -> dict:
    task = task or Task(track=False)
    task.begin("reading the source", note="checking streams and audio tracks")
    info = probe(path, task)
    dur = info["duration"]
    task.begin("scanning the video", dur, note=f"{dur / 60:.1f} min of footage")
    frames, scenes = _decode(path, hwaccel, task, lambda t: task.set(t))
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
            "motion": frames.motion, "loudness": loud, "scenes": scenes}


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
            task: Task | None = None, more_stages: tuple = ()) -> dict:
    p = {**DEFAULTS, **(params or {})}
    if task:
        task.plan(("reading the source", 1), ("scanning the video", 6), ("measuring loudness", 2),
                  ("detecting moments", 0.2), *more_stages)
    sig = signals(path, hwaccel, task)
    if task:
        task.begin("detecting moments")
    events = dark_events(sig, p)
    result = {"signals": sig, "params": p, "events": events,
              "gameplay": gameplay(sig, events, p, key_moments or []),
              "scores": moment_scores(sig)}
    if out_json:
        with open(out_json, "w") as f:
            json.dump(result, f)
    return result


def summary(result: dict) -> dict:
    """Small, human/agent-readable view of an analysis (no raw signal arrays)."""
    g = result["gameplay"]
    info = result["signals"]["info"]
    return {
        "duration": round(info["duration"], 1),
        "resolution": f'{info["width"]}x{info["height"]}@{info["fps"]:.2f}',
        "audio": "silent" if result["signals"]["loudness"] is None else "has sound",
        "deaths": [d["onset"] for d in g["deaths"]],
        "win_at": g["win"]["onset"] if g["win"] else None,
        "final_attempt": g["final_attempt"],
        "fades": [(e["onset"], e["kind"]) for e in result["events"]],
        "scene_cuts": (len(result["signals"]["scenes"]) if result["signals"].get("scenes") is not None
                       else "not detected (this ffmpeg has no scdet filter)"),
    }
