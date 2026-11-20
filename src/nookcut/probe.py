"""Media inspection via ffprobe/ffmpeg."""
from __future__ import annotations

import json
import math
import os
from array import array

from .color import hdr_from_probe
from .errors import InputError, MediaError
from .tasks import Task, run


def probe(path: str, task: Task | None = None) -> dict:
    """Duration, video size/fps, start timecode and per-track audio peak (dBFS)."""
    if not os.path.isfile(path):
        raise InputError(f"The source video can't be found: {path}", "Check the path and that the drive is mounted.")
    try:
        out = run(["ffprobe", "-v", "error", "-show_entries",
                   "format=duration:stream=index,codec_type,codec_name,width,height,r_frame_rate,pix_fmt,"
                   "color_transfer,color_primaries,color_space,color_range:stream_side_data"
                   ":stream_tags=timecode", "-of", "json", path], task, "reading the source", capture=True,
                  timeout=120)
    except MediaError as e:
        if "ffmpeg stopped with an error" not in e.message:  # a cause we recognise, e.g. damaged/incomplete
            raise InputError(f"{os.path.basename(path)}: {e.message.split(': ', 1)[-1]}", e.hint, e.detail) from None
        raise InputError(f"This file can't be read as a video: {os.path.basename(path)}",
                         "Make sure it is a finished video file (mp4, mov, mkv, webm...).", e.detail) from None
    info = json.loads(out)
    video = next((s for s in info.get("streams", []) if s["codec_type"] == "video"), None)
    if not video:
        raise InputError(f"There is no video in {os.path.basename(path)}.", "Pick a video file, not audio only.")
    num, den = (int(x) for x in video["r_frame_rate"].split("/"))
    duration = float(info.get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise InputError(f"{os.path.basename(path)} has no readable length.",
                         "The file may still be recording or was cut off; re-export it.")
    timecode = next((s.get("tags", {}).get("timecode") for s in info["streams"]
                     if s.get("tags", {}).get("timecode")), "00:00:00:00")
    audio = [s for s in info["streams"] if s["codec_type"] == "audio"]
    dolby = dolby_vision(video)
    if dolby and dolby["base"] is None:
        raise InputError(f"{os.path.basename(path)} is Dolby Vision profile {dolby['profile']}, which has no "
                         "standard HDR or SDR picture inside, so its colours can't be shown correctly.",
                         "Export it again as HDR10 or SDR (in the camera app, DaVinci Resolve or HandBrake), "
                         "or record with Dolby Vision turned off.")
    hdr = hdr_from_probe(video)
    if hdr:  # mastering display / light level usually travel with the frames, not the stream
        hdr = hdr_from_probe(video, _first_frame(path, task))
    return {
        "path": path,
        "duration": duration,
        "width": video["width"],
        "height": video["height"],
        "fps": num / den if den else 30.0,
        "timecode": timecode,
        "hdr": hdr,
        "matrix": video.get("color_space") if video.get("color_space") not in (None, "", "unknown") else None,
        "dolby_vision": dolby,   # its extra shot-by-shot metadata isn't kept; the base picture is used
        "audio_tracks": [{"index": i, "peak_db": audio_peak(path, i, task=task)} for i in range(len(audio))],
    }


# Dolby Vision base-layer compatibility: what a player without Dolby Vision sees
_DV_BASE = {1: "HDR10", 2: "SDR", 4: "HLG", 6: "HDR10"}


def dolby_vision(video: dict) -> dict | None:
    """Dolby Vision facts from the stream's configuration record, or None. base is None when there
    is no standard picture underneath (profile 5: Dolby's own colour encoding, needs its metadata)."""
    rec = next((sd for sd in video.get("side_data_list") or []
                if sd.get("side_data_type") == "DOVI configuration record"), None)
    if not rec:
        return None
    compat = int(rec.get("dv_bl_signal_compatibility_id", 0) or 0)
    return {"profile": int(rec.get("dv_profile", 0) or 0), "base": _DV_BASE.get(compat)}


def hdr_of(path: str, task: Task | None = None) -> dict | None:
    """Just the HDR facts of a file (for edits planned before NookCut knew about HDR)."""
    try:
        out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                   "stream=pix_fmt,color_transfer,color_primaries,color_space,color_range", "-of", "json", path],
                  task, "reading the colour format", capture=True, timeout=60)
        video = (json.loads(out).get("streams") or [{}])[0]
    except (MediaError, ValueError):
        return None
    return hdr_from_probe(video, _first_frame(path, task)) if hdr_from_probe(video) else None


def _first_frame(path: str, task: Task | None) -> dict:
    try:
        out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-read_intervals", "%+#1",
                   "-show_frames", "-of", "json", path], task, "reading HDR details", capture=True, timeout=60)
        return (json.loads(out).get("frames") or [{}])[0]
    except (MediaError, ValueError):
        return {}


def audio_peak(path: str, track: int, seconds: float = 0, task: Task | None = None) -> float:
    """Peak level of an audio track in dBFS (-inf-ish = silent)."""
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-map", f"0:a:{track}", "-ac", "1", "-ar", "4000"]
    if seconds:
        cmd += ["-t", str(seconds)]
    peak = 0

    def chunk(b: bytes):
        nonlocal peak
        a = array("h"); a.frombytes(b[: len(b) // 2 * 2])
        peak = max(peak, max((abs(x) for x in a), default=0))

    try:
        run(cmd + ["-f", "s16le", "-"], task, "reading audio", on_chunk=chunk)
    except MediaError:
        return -180.0  # unreadable track counts as silent
    return round(20 * math.log10(peak / 32768), 1) if peak else -180.0


def timecode_seconds(tc: str, fps: float) -> float:
    h, m, s, f = (int(x) for x in tc.replace(";", ":").split(":"))
    return h * 3600 + m * 60 + s + f / round(fps)
