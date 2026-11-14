"""Original procedural score synced to the edit. Everything is synthesised here, so the
output carries no third-party copyright.

Styles:
  taiko      Japanese drums, low drone, bamboo-flute melody (samurai, martial, historical)
  pulse      electronic kick/clap, saw-ish bass, arp lead (shooters, sci-fi, esports, tech)
  cinematic  low booms, string-like pad, brass-ish swells (bosses, epic, trailers, sports)
"""
from __future__ import annotations

import math
import os
import random
import struct
import wave

from .edit import timeline, to_output, total
from .tasks import Task, run

SR = 48000

STYLES = {
    "taiko":     {"drum_f": 62, "drone": (55.0, 82.41, 110.0), "lead": "flute", "bpm": 120,
                  "scale": (440, 523.25, 587.33, 659.25, 783.99)},
    "pulse":     {"drum_f": 50, "drone": (41.2, 61.74, 82.41), "lead": "arp", "bpm": 128,
                  "scale": (329.63, 392.0, 440.0, 493.88, 587.33)},
    "cinematic": {"drum_f": 45, "drone": (49.0, 73.42, 98.0, 146.83), "lead": "brass", "bpm": 100,
                  "scale": (293.66, 349.23, 392.0, 440.0, 523.25)},
}


class Bus:
    def __init__(self, seconds: float):
        self.n = int(seconds * SR)
        self.buf = [0.0] * self.n

    def add(self, t0: float, samples: list[float]) -> None:
        i0 = int(t0 * SR)
        for k, v in enumerate(samples):
            i = i0 + k
            if 0 <= i < self.n:
                self.buf[i] += v


def drum(f, dur, amp, style):
    out, ph, lp = [], 0.0, 0.0
    sweep = 3.0 if style == "pulse" else 1.6
    for k in range(int(dur * SR)):
        t = k / SR
        ph += 2 * math.pi * f * (1 + sweep * math.exp(-t * 35)) / SR
        lp += 0.08 * (random.uniform(-1, 1) - lp)
        decay = 7 if style == "pulse" else 4.5
        out.append(amp * (math.sin(ph) * math.exp(-t * decay) + lp * math.exp(-t * 40) * 1.6))
    return out


def snap(amp=0.35, style="taiko"):
    f = 820 if style == "taiko" else 1800
    out = []
    for k in range(int(0.12 * SR)):
        t = k / SR
        out.append(amp * (math.sin(2 * math.pi * f * t) * 0.4 + random.uniform(-1, 1) * 0.6) * math.exp(-t * 45))
    return out


def clash(amp=0.5):
    parts = [(2350, 1.0), (3170, .7), (4410, .55), (5900, .4), (7600, .25)]
    out = []
    for k in range(int(0.6 * SR)):
        t = k / SR
        v = sum(a * math.sin(2 * math.pi * f * t) * math.exp(-t * (5 + f / 900)) for f, a in parts)
        out.append(amp * (v + random.uniform(-1, 1) * math.exp(-t * 90) * 1.2) / 2.5)
    return out


def whoosh(dur=0.4, amp=0.3):
    out, lp = [], 0.0
    for k in range(int(dur * SR)):
        t = k / SR
        lp += (0.02 + 0.25 * t / dur) * (random.uniform(-1, 1) - lp)
        out.append(amp * math.sin(math.pi * t / dur) ** 2 * lp * 3)
    return out


def swell(dur, amp=0.4):
    out, lp = [], 0.0
    for k in range(int(dur * SR)):
        t = k / SR
        lp += (0.01 + 0.2 * t / dur) * (random.uniform(-1, 1) - lp)
        out.append(amp * (t / dur) ** 2.5 * lp * 3)
    return out


def boom(amp=1.0):
    out, ph, lp = [], 0.0, 0.0
    for k in range(int(3.5 * SR)):
        t = k / SR
        ph += 2 * math.pi * (38 + 90 * math.exp(-t * 6)) / SR
        lp += 0.03 * (random.uniform(-1, 1) - lp)
        out.append(amp * (math.sin(ph) * math.exp(-t * 1.3) + lp * 2.5 * math.exp(-t * 2)))
    return out


def lead(kind, f, dur, amp=0.17):
    out, ph = [], 0.0
    for k in range(int(dur * SR)):
        t = k / SR
        env = min(1, t / 0.1) * min(1, (dur - t) / 0.25)
        vib = 1 + 0.006 * math.sin(2 * math.pi * 5.2 * t) * min(1, t / 0.4)
        ph += 2 * math.pi * f * vib / SR
        if kind == "flute":
            v = math.sin(ph) + 0.25 * math.sin(2 * ph) + random.uniform(-1, 1) * 0.12
        elif kind == "arp":
            v = sum(math.sin(h * ph) / h for h in range(1, 6)) * math.exp(-t * 3)
        else:
            v = sum(math.sin(h * ph) / h for h in range(1, 8)) * min(1, t / 0.3)
        out.append(amp * env * v)
    return out


def compose(edit: dict, out_wav: str, style: str | None = None, seed: int = 7,
            task: Task | None = None) -> str:
    """Write a mastered stereo WAV (-14 LUFS) exactly as long as the edit."""
    random.seed(seed)
    st = STYLES[style or edit.get("music_style", "taiko")]
    name = style or edit.get("music_style", "taiko")
    length = total(edit)
    mus, sfx = Bus(length + 1.5), Bus(length + 1.5)
    tl = timeline(edit)
    finisher = next((s for s, d, g in tl if g["kind"] == "finisher"), None)
    calm = next((s for s, d, g in tl if g["kind"] in ("outro", "end")), length)

    for i in range(mus.n):
        t = i / SR
        lvl = 0.02 if finisher is not None and finisher <= t < finisher + 2.4 else 0.10
        v = sum(math.sin(2 * math.pi * f * t) / (j + 1) for j, f in enumerate(st["drone"]))
        v *= 0.75 + 0.25 * math.sin(2 * math.pi * 0.2 * t)
        mus.buf[i] += lvl * v * min(1, t / 0.5) * min(1, max(0, length + 1.0 - t) / 1.5)

    big, mid, small = (drum(st["drum_f"], 1.6, 1.0, name), drum(st["drum_f"] * 1.25, 0.9, 0.7, name),
                       drum(st["drum_f"] * 1.6, 0.5, 0.45, name))
    beat = 60 / st["bpm"]
    for start, dur, s in tl:
        k = s["kind"]
        if k == "hook":
            mus.add(start, big); sfx.add(start + 0.05, clash(0.5)); mus.add(start + dur - 1.0, swell(1.0, 0.5))
        elif k == "intro":
            t = start
            while t < start + dur - 0.01:
                mus.add(t, mid); t += beat * 2
        elif k == "death":
            mus.add(start, big); sfx.add(start, whoosh()); mus.add(start + dur * 0.5, small)
        elif k == "card":
            mus.add(start, big); mus.add(start, swell(dur, 0.6))
        elif k in ("highlight", "slowmo"):
            t, b = start, 0
            while t < start + dur - 0.01:
                pos = b % 8
                if pos in (0, 3, 4):
                    mus.add(t, mid if pos else big)
                elif pos in (6, 7):
                    mus.add(t, small)
                if pos in (2, 5):
                    mus.add(t, snap(style=name))
                t += beat / 2; b += 1
        elif k == "finisher":
            mus.add(start, swell(1.2, 0.5))
        for h in s.get("hits", []):
            if s["src_in"] <= h < s["src_out"]:
                sfx.add(to_output(start, s, h), clash(0.45))
        if s.get("impact") and k == "finisher" and s["src_in"] <= s["impact"] < s["src_out"]:
            t = to_output(start, s, s["impact"])
            mus.add(t, boom()); sfx.add(t, clash(0.9))

    t = calm + 0.3
    scale = st["scale"]
    for f, d in [(scale[0], 1.2), (scale[1], 0.8), (scale[2], 1.6), (scale[1], 0.6), (scale[0], 0.6), (scale[0] * 0.89, 2.0)]:
        if t + d > length:
            break
        mus.add(t, lead(st["lead"], f, d + 0.15)); t += d
    if length > 4:
        mus.add(length - 3.0, big)

    mx = max(max(abs(v) for v in mus.buf), 1e-9)
    raw = out_wav + ".raw.wav"
    with wave.open(raw, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, a / mx * 0.7 + b * 0.6)) * 32000))
                               for a, b in zip(mus.buf, sfx.buf)))
    try:
        run(["ffmpeg", "-v", "error", "-y", "-i", raw, "-af",
             "aecho=0.8:0.6:60|140|260:0.35|0.25|0.15,pan=stereo|c0=c0|c1=c0,extrastereo=1.5,"
             "loudnorm=I=-14:TP=-1.0:LRA=9", "-ar", "48000", "-t", f"{length:.3f}", out_wav],
            task, "mastering the music", progress=(lambda t: task.set(t)) if task else None)
    finally:
        os.remove(raw)
    return out_wav
