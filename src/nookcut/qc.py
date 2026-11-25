"""Looking at video: frame sheets agents can see, and quick checks of a finished render.

frames()  a grid of frames from the render or the source, each labelled with its time
verify()  what a viewer would notice, as numbers any agent (or small local model) can read:
          size, frame rate, length against the plan, colour tags, loudness, black or frozen
          stretches and silence; plus a sheet with one frame per segment for agents that can see
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile

from .edit import fps_value, timeline, total
from .preflight import find_font
from .tasks import Task, filter_quote, run

TILE = {"vertical": (270, 480), "square": (360, 360), "wide": (480, 270)}
LOUDNESS = (-18.0, -10.0)    # integrated LUFS most short-form videos land in (platforms aim near -14)
TRUE_PEAK = -1.0             # dBTP above this can distort after the platforms' encoding


def _clock(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


def frames(video: str, out_path: str, times: list[float], labels: list[str] | None = None,
           tile: tuple[int, int] = (480, 270), pre: str = "", args: list[str] | None = None,
           task: Task | None = None) -> str:
    """A grid (4 across) of frames at `times` (seconds in `video`), each labelled. pre/args convert
    colour first (HDR sources), as for the render."""
    task = task or Task(track=False)
    font = find_font()
    work = tempfile.mkdtemp(prefix="nookcut_frames_")
    try:
        task.begin("grabbing frames", len(times))
        for i, t in enumerate(times):
            label = (labels[i] if labels else "") or _clock(t)
            lp = os.path.join(work, f"l{i}.txt")
            with open(lp, "w", encoding="utf-8") as f:
                f.write(label)
            text = (f",drawtext=fontfile={filter_quote(font)}:textfile={filter_quote(lp)}:fontsize=16"
                    ":fontcolor=yellow:box=1:boxcolor=black@0.7:x=6:y=6") if font else ""
            vf = (f"{pre + ',' if pre else ''}scale={tile[0]}:{tile[1]}:force_original_aspect_ratio=decrease,"
                  f"pad={tile[0]}:{tile[1]}:(ow-iw)/2:(oh-ih)/2{text}")
            run(["ffmpeg", "-v", "error", "-y", *(args or []), "-ss", f"{max(0.0, t):.3f}", "-i", video,
                 "-frames:v", "1", "-vf", vf, os.path.join(work, f"f{i:03d}.png")], task,
                f"grabbing the frame at {_clock(t)}", timeout=120)
            task.set(i + 1)
        cols = min(4, len(times))
        rows = (len(times) + cols - 1) // cols
        run(["ffmpeg", "-v", "error", "-y", "-framerate", "1", "-i", os.path.join(work, "f%03d.png"),
             "-vf", f"tile={cols}x{rows}", "-frames:v", "1", "-q:v", "3", out_path], task, "building the frame sheet")
        return out_path
    finally:
        shutil.rmtree(work, ignore_errors=True)


def thumbnails(items: list[tuple[str, float, str]], out_path: str, tile: tuple[int, int] = (320, 180),
               task: Task | None = None) -> str:
    """A labelled grid of one frame from each of several files: [(path, time, label), ...]."""
    task = task or Task(track=False)
    font = find_font()
    work = tempfile.mkdtemp(prefix="nookcut_thumbs_")
    try:
        for i, (path, t, label) in enumerate(items):
            lp = os.path.join(work, f"l{i}.txt")
            with open(lp, "w", encoding="utf-8") as f:
                f.write(label)
            text = (f",drawtext=fontfile={filter_quote(font)}:textfile={filter_quote(lp)}:fontsize=14"
                    ":fontcolor=yellow:box=1:boxcolor=black@0.7:x=4:y=4") if font else ""
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1", "-vf",
                 f"scale={tile[0]}:{tile[1]}:force_original_aspect_ratio=decrease,pad={tile[0]}:{tile[1]}:"
                 f"(ow-iw)/2:(oh-ih)/2{text}", os.path.join(work, f"f{i:03d}.png")], task, f"a picture of {label}",
                timeout=60)
        cols = min(4, len(items))
        run(["ffmpeg", "-v", "error", "-y", "-framerate", "1", "-i", os.path.join(work, "f%03d.png"),
             "-vf", f"tile={cols}x{(len(items) + cols - 1) // cols}", "-frames:v", "1", "-q:v", "3", out_path],
            task, "building the B-roll sheet")
        return out_path
    finally:
        shutil.rmtree(work, ignore_errors=True)


def spread(duration: float, count: int) -> list[float]:
    """count times spread evenly across a video, away from the very first and last frame."""
    count = max(1, min(int(count), 24))
    return [round(duration * (k + 0.5) / count, 2) for k in range(count)]


def _probe(video: str, task: Task | None) -> dict:
    out = run(["ffprobe", "-v", "error", "-show_entries",
               "format=duration:stream=codec_type,codec_name,width,height,avg_frame_rate,pix_fmt,"
               "color_transfer,color_primaries,color_space,color_range", "-of", "json", video], task,
              "reading the video", capture=True, timeout=60)
    return json.loads(out)


def _metadata(text: str, key: str) -> list[float]:
    return [float(v) for v in re.findall(rf"{re.escape(key)}=(-?[0-9.]+|-?inf)", text) if v not in ("inf", "-inf")]


def scan(video: str, has_audio: bool, task: Task | None = None) -> dict:
    """One decode of the video: black and frozen stretches, loudness, true peak and silence."""
    work = tempfile.mkdtemp(prefix="nookcut_qc_")
    vfile, afile = os.path.join(work, "v.txt"), os.path.join(work, "a.txt")
    try:
        graph = (f"[0:v]blackdetect=d=0.5:pix_th=0.10,freezedetect=n=0.003:d=2,"
                 f"metadata=print:file={filter_quote(vfile)}[v]")
        maps = ["-map", "[v]"]
        if has_audio:
            graph += (f";[0:a]ebur128=peak=true:metadata=1,silencedetect=n=-50dB:d=2,"
                      f"ametadata=print:file={filter_quote(afile)}[a]")
            maps += ["-map", "[a]"]
        run(["ffmpeg", "-v", "error", "-i", video, "-filter_complex", graph, *maps, "-f", "null", "-"],
            task, "checking the video", timeout=1800)
        v = open(vfile, encoding="utf-8").read() if os.path.exists(vfile) else ""
        a = open(afile, encoding="utf-8").read() if os.path.exists(afile) else ""
    finally:
        shutil.rmtree(work, ignore_errors=True)
    out = {"black": _pairs(v, "lavfi.black_start", "lavfi.black_end"),
           "frozen": _pairs(v, "lavfi.freezedetect.freeze_start", "lavfi.freezedetect.freeze_end")}
    if has_audio:
        integrated = _metadata(a, "lavfi.r128.I")
        peaks = _metadata(a, "lavfi.r128.true_peaks_ch0") + _metadata(a, "lavfi.r128.true_peaks_ch1")
        out["lufs"] = round(integrated[-1], 1) if integrated else None
        top = max(peaks) if peaks else 0.0  # ebur128 writes true peaks as linear amplitude, not dB
        out["true_peak"] = round(20 * math.log10(top), 1) if top > 0 else None
        out["silent"] = _pairs(a, "lavfi.silence_start", "lavfi.silence_end")
    return out


def _pairs(text: str, start_key: str, end_key: str) -> list[list[float]]:
    starts, ends = _metadata(text, start_key), _metadata(text, end_key)
    return [[round(s, 2), round(ends[k], 2) if k < len(ends) else None] for k, s in enumerate(starts)]


def verify(edit: dict, video: str, sheet: str, task: Task | None = None) -> dict:
    """Check a render against its edit. issues are what to fix or tell the user; facts are the
    measurements; frames is a sheet with one frame per segment (middle of each)."""
    task = task or Task(track=False)
    info = _probe(video, task)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), {})
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    length = float(info.get("format", {}).get("duration") or 0)
    num, _, den = (v.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    facts = {"size": f"{v.get('width')}x{v.get('height')}", "fps": round(fps, 2), "length_s": round(length, 2),
             "codec": v.get("codec_name"), "colour": v.get("color_transfer") or "untagged"}
    issues = []
    if (v.get("width"), v.get("height")) != (edit.get("width"), edit.get("height")):
        issues.append(f"size is {facts['size']}, the plan says {edit.get('width')}x{edit.get('height')}")
    if abs(fps - fps_value(edit)) > 0.5:
        issues.append(f"frame rate is {fps:.2f}, the plan says {fps_value(edit):.2f}")
    if abs(length - total(edit)) > 0.5:
        issues.append(f"length is {length:.1f} s, the plan says {total(edit):.1f} s")
    want = (edit.get("source_hdr") or {}).get("trc") if edit.get("hdr") == "keep" and edit.get("source_hdr") \
        else "bt709"
    if v.get("color_transfer") != want:
        issues.append(f"colour is tagged {facts['colour']}, expected {want}")
    expect_sound = edit.get("music", "ai") != "none" or edit.get("source_audio") != "mute"
    if expect_sound and not has_audio:
        issues.append("there is no sound")

    if edit.get("framing") == "full":
        from .render import upscale
        ups = [u for u in (upscale(edit, s) for s in edit["segments"]) if u]
        if ups:
            facts["enlarged"] = max(ups)
            if max(ups) > 3:
                issues.append(f"the recording is enlarged up to {max(ups):g}x to fill the frame, so it looks soft; "
                              "use zoom 1 or a higher-resolution recording")
    found = scan(video, has_audio, task)
    end_fade = length - 0.8
    black = [b for b in found["black"] if b[0] < end_fade]   # the end card fades out on purpose
    if black:
        issues.append("black picture at " + ", ".join(f"{b[0]:.1f}-{b[1] or length:.1f} s" for b in black))
    if found["frozen"]:
        issues.append("frozen picture at " + ", ".join(f"{f[0]:.1f}-{f[1] or length:.1f} s" for f in found["frozen"]))
    if has_audio:
        facts.update(lufs=found["lufs"], true_peak_db=found["true_peak"])
        if found["lufs"] is not None and not LOUDNESS[0] <= found["lufs"] <= LOUDNESS[1]:
            issues.append(f"loudness is {found['lufs']} LUFS; short-form videos usually sit between "
                          f"{LOUDNESS[0]:.0f} and {LOUDNESS[1]:.0f} (platforms aim near -14)")
        if found["true_peak"] is not None and found["true_peak"] > TRUE_PEAK:
            issues.append(f"true peak is {found['true_peak']} dBTP; above {TRUE_PEAK:.0f} it can distort")
        if found["silent"] and expect_sound:
            issues.append("silence at " + ", ".join(f"{s[0]:.1f}-{s[1] or length:.1f} s" for s in found["silent"]))

    pre, args = "", []
    if want != "bt709":  # an HDR render: show its frames tone-mapped, as most screens would
        from .color import pick_tonemap, tonemap_args, tonemap_filter
        method = pick_tonemap()
        if method:
            pre, args = tonemap_filter(method), tonemap_args(method)
    mids, labels = [], []
    for i, (start, dur, s) in enumerate(timeline(edit)):
        mids.append(round(min(length - 0.05, start + dur / 2), 2))
        labels.append(f"{i + 1}. {s['kind']} {_clock(start + dur / 2)}")
    if len(mids) > 24:
        pick = [round(k * (len(mids) - 1) / 23) for k in range(24)]
        mids, labels = [mids[k] for k in pick], [labels[k] for k in pick]
    frames(video, sheet, mids, labels, TILE.get(edit.get("orientation"), TILE["wide"]), pre, args, task)
    return {"ok": not issues, "issues": issues, "facts": facts, "frames": sheet}
