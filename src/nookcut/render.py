"""ffmpeg renderer: one clip per segment (in parallel), then concat + audio mix.

Layouts
  vertical  1080x1920  sharp gameplay band (1.2:1 crop) over a blurred fill, captions above/below
  square    1080x1080  full 16:9 frame over a blurred fill
  wide      1920x1080  full frame, captions over the picture
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from .edit import seg_duration, timeline, total
from .errors import EnvironmentNotReady, MediaError, explain_ffmpeg
from . import subtitles as subs
from .preflight import encoder_args, find_font, has_filter
from .tasks import Task, _nice, filter_quote, run


def _decoded_size(source: str, hwaccel: str | None) -> tuple[int, int, str | None]:
    """Frame size as actually decoded, plus the hwaccel to use (None if it can't decode this file).

    The stream header can report the coded size (e.g. 1920x1088 for VP9/H.264 padded to 16 rows)
    while frames come out at 1920x1080; cropping from the header size then fails every segment.
    """
    for accel in ([hwaccel, None] if hwaccel else [None]):
        cmd = ["ffmpeg", "-v", "info", "-nostdin"] + (["-hwaccel", accel] if accel else [])
        cmd += ["-i", source, "-map", "0:v:0", "-frames:v", "1", "-vf", "showinfo", "-f", "null", "-"]
        try:
            r = subprocess.run(_nice(cmd), capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            continue
        m = re.search(r"\bs:(\d+)x(\d+)", r.stderr)
        if r.returncode == 0 and m:
            return int(m.group(1)), int(m.group(2)), accel
    probe_cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                 "-of", "csv=p=0", source]
    r = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=120)
    fields = r.stdout.split()
    if not fields:
        raise explain_ffmpeg(r.stderr, "reading the video size")
    w, h = (int(x) for x in fields[0].split(",")[:2])
    return w, h, None


_q = filter_quote


def _layout(edit: dict) -> dict:
    o = edit["orientation"]
    if o == "vertical":
        return {"fg_w": 1080, "fg_h": 900, "fg_y": 470, "crop_aspect": 1.2, "top_y": 150, "line": 140,
                "big": 120, "small": 58, "bottom_y": 1440, "speech": 112, "speech_chars": 16}
    if o == "square":
        return {"fg_w": 1080, "fg_h": 608, "fg_y": 236, "crop_aspect": 16 / 9, "top_y": 40, "line": 95,
                "big": 82, "small": 44, "bottom_y": 920, "speech": 76, "speech_chars": 22}
    return {"fg_w": 1920, "fg_h": 1080, "fg_y": 0, "crop_aspect": 16 / 9, "top_y": 60, "line": 120,
            "big": 104, "small": 56, "bottom_y": 930, "speech": 92, "speech_chars": 28}


def _speech(work, i, words, dur, L, font, W, H):
    """Word-by-word speech captions where the bottom caption would go (libass, else drawtext)."""
    if has_filter("ass"):
        p = os.path.join(work, f"s{i}.ass")
        with open(p, "w", encoding="utf-8") as f:
            f.write(subs.ass(words, dur, W, H, L["bottom_y"], L["speech"], font, L["speech_chars"]))
        return [f"ass={_q(p)}:fontsdir={_q(os.path.dirname(font))}"]
    out = []
    for k, (a, b, text) in enumerate(subs.plain(words, dur, L["speech_chars"])):
        p = os.path.join(work, f"s{i}_{k}.txt"); open(p, "w", encoding="utf-8").write(text)
        out.append(f"drawtext=fontfile={_q(font)}:textfile={_q(p)}:fontsize={L['speech']}:fontcolor=white"
                   f":borderw=6:bordercolor=black:x=(w-text_w)/2:y={L['bottom_y']}:enable='between(t,{a:.3f},{b:.3f})'")
    return out


def _text(work, i, s, dur, L, font, W=0, H=0, speech=None):
    f, kind = [], s["kind"]
    color = "0xFF2A2A" if kind == "death" else "white"
    for j, line in enumerate(x for x in s["top"].split("|") if x):
        p = os.path.join(work, f"t{i}_{j}.txt"); open(p, "w", encoding="utf-8").write(line)
        f.append(f"drawtext=fontfile={_q(font)}:textfile={_q(p)}:fontsize={L['big']}:fontcolor={color}"
                 f":borderw=7:bordercolor=black:x=(w-text_w)/2:y={L['top_y'] + j * L['line']}:alpha='min(1,t/0.12)'")
    if speech:
        f += _speech(work, i, speech, dur, L, font, W, H)
    elif s["bottom"]:
        p = os.path.join(work, f"b{i}.txt"); open(p, "w", encoding="utf-8").write(s["bottom"].replace("|", " "))
        bcolor = "0xFF2A2A" if kind == "death" else "0xFFD400"
        f.append(f"drawtext=fontfile={_q(font)}:textfile={_q(p)}:fontsize={L['small']}:fontcolor={bcolor}"
                 f":borderw=5:bordercolor=black:x=(w-text_w)/2:y={L['bottom_y']}:alpha='min(1,t/0.2)'")
    if kind in ("death", "finisher"):
        f.append("fade=in:st=0:d=0.12:color=white")
    if kind == "hook":
        f.append(f"fade=out:st={dur - 0.12:.3f}:d=0.12:color=white")
    if kind == "end":
        f.append(f"fade=out:st={max(0, dur - 0.6):.3f}:d=0.6")
    return f


def _segment(edit, i, s, work, font, hwaccel, src_w, src_h, task=None, on_progress=None, encoder=None):
    L = _layout(edit)
    enc = encoder_args(encoder)
    dur = seg_duration(s)
    zoom = s.get("zoom", 1.0)
    W, H = edit["width"], edit["height"]
    if src_w / src_h >= L["crop_aspect"]:
        # landscape source: crop a band of the layout's aspect and place it at fg_y
        ch = int(src_h / zoom) // 2 * 2
        cw = min(src_w, int(ch * L["crop_aspect"])) // 2 * 2
        fg_w, fg_h, x, y = L["fg_w"], L["fg_h"], 0, L["fg_y"]
    else:
        # source narrower than the band (e.g. recorded vertical): fit it whole, centred
        cw, ch = int(src_w / zoom) // 2 * 2, int(src_h / zoom) // 2 * 2
        fg_h = H
        fg_w = int(H * cw / ch) // 2 * 2
        if fg_w > W:
            fg_w, fg_h = W, int(W * ch / cw) // 2 * 2
        x, y = (W - fg_w) // 2, (H - fg_h) // 2
    fg = f"crop='min({cw},iw)':'min({ch},ih)',scale={fg_w}:{fg_h}"
    if s["speed"] < 1:
        fg += ",minterpolate=fps=60:mi_mode=mci:mc_mode=aobmc:vsbmc=1"
    if s["kind"] == "card":
        fg += ",eq=brightness=-0.25:saturation=0.6"
    if (fg_w, fg_h) == (W, H):
        graph = f"[0:v]setpts=(PTS-STARTPTS)/{s['speed']},fps=60,{fg}"
    else:
        graph = (f"[0:v]setpts=(PTS-STARTPTS)/{s['speed']},fps=60,split[a][b];"
                 f"[a]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,crop={W // 4}:{H // 4},"
                 f"gblur=sigma=10,scale={W}:{H},eq=brightness=-0.18:saturation=0.8[bg];[b]{fg}[fg];"
                 f"[bg][fg]overlay={x}:{y}")
    speech = subs.local_words(s) if edit.get("subtitles") else []
    graph += "," + ",".join(_text(work, i, s, dur, L, font, W, H, speech) + [enc["fmt"]]) + "[v]"
    out = os.path.join(work, f"seg{i:03d}.mov")
    cmd = ["ffmpeg", "-v", "error", "-y"] + enc["pre"]
    if hwaccel:
        cmd += ["-hwaccel", hwaccel]
    cmd += ["-ss", f"{s['src_in']}", "-t", f"{s['src_out'] - s['src_in']}", "-i", edit["source"]]
    has_audio = edit.get("source_audio") != "mute"
    cmd += ["-filter_complex", graph + (f";[0:a:0]atempo={max(0.5, s['speed'])},asetpts=PTS-STARTPTS[a]"
                                        if has_audio else ""),
            "-map", "[v]"]
    if has_audio:
        cmd += ["-map", "[a]", "-c:a", "pcm_s16le", "-ar", "48000"]
    cmd += ["-t", f"{dur:.4f}", "-c:v", enc["codec"], *enc["args"], "-r", "60", out]
    run(cmd, task, f"rendering segment {i + 1} ({s['kind']} at {s['src_in']:.1f}s)", progress=on_progress)
    return out


def _workers() -> int:
    try:
        return max(1, int(os.environ["NOOKCUT_WORKERS"]))
    except (KeyError, ValueError):
        return min(4, max(1, (os.cpu_count() or 2) // 2))


def render(edit: dict, out_path: str, music_wav: str | None, hwaccel: str | None = None,
           workers: int | None = None, only: list[int] | None = None, task: Task | None = None,
           encoder: str | None = None) -> str:
    """encoder: a GPU encoder from preflight.HW_ENCODERS, or None for the CPU (libx264)."""
    task = task or Task(track=False)
    font = find_font()
    if not font:
        raise EnvironmentNotReady("No caption font was found.", "Set NOOKCUT_FONT to a .ttf/.otf file path.")
    task.begin("preparing", note="checking the source frame size")
    src_w, src_h, hwaccel = _decoded_size(edit["source"], hwaccel)
    work = tempfile.mkdtemp(prefix="nookcut_")
    try:
        idx = list(only if only is not None else range(len(edit["segments"])))
        durs = {i: seg_duration(edit["segments"][i]) for i in idx}
        done = dict.fromkeys(idx, 0.0)
        lock = threading.Lock()

        def render_all(enc):
            done.update(dict.fromkeys(idx, 0.0))
            failed = threading.Event()
            label = encoder_args(enc)["label"]
            task.begin("rendering segments", sum(durs.values()), note=f"{len(idx)} segments on {label}")

            def seg_progress(i):
                def update(t):
                    with lock:
                        done[i] = min(durs[i], t)
                        finished = sum(1 for k in idx if done[k] >= durs[k] - 0.05)
                        task.set(sum(done.values()), note=f"{finished}/{len(idx)} segments")
                return update

            def one(i):
                if failed.is_set():
                    return None
                out = _segment(edit, i, edit["segments"][i], work, font, hwaccel, src_w, src_h, task, seg_progress(i),
                               enc)
                seg_progress(i)(durs[i])
                return out

            ex = ThreadPoolExecutor(workers or _workers())
            futures = {ex.submit(one, i): i for i in idx}
            segs = {}
            try:
                for fut in as_completed(futures):
                    segs[futures[fut]] = fut.result()
            except BaseException:
                # first failure wins: stop the other segments instead of letting them finish for nothing
                failed.set()
                task.stop_processes()
                ex.shutdown(wait=True, cancel_futures=True)
                raise
            ex.shutdown()
            return segs

        try:
            segs = render_all(encoder)
        except MediaError:
            if not encoder:
                raise
            # GPU encoders can fail mid-job (driver hiccup, NVENC session limit while OBS records):
            # redo every segment on the CPU, since mixing encoders would break the stream-copy join
            segs = render_all(None)
        segs = [segs[i] for i in idx]
        task.begin("mixing audio", total(edit), note="joining segments, music and game sound")
        listing = os.path.join(work, "list.txt")
        with open(listing, "w") as f:
            f.writelines(f"file '{p}'\n" for p in segs)
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing]
        has_src_audio = edit.get("source_audio") != "mute"
        if music_wav:
            cmd += ["-i", music_wav]
        if has_src_audio and music_wav:
            game_vol = 1.0 if edit["source_audio"] == "keep" else 0.9
            mus_vol = 0.35 if edit["source_audio"] == "keep" else 0.6
            cmd += ["-filter_complex", f"[0:a]volume={game_vol}[g];[1:a]volume={mus_vol}[m];"
                    "[g][m]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.9[a]",
                    "-map", "0:v", "-map", "[a]"]
        elif music_wav:
            cmd += ["-map", "0:v", "-map", "1:a"]
        else:
            cmd += ["-map", "0:v"] + (["-map", "0:a"] if has_src_audio else [])
        cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "320k", "-t", f"{total(edit):.3f}",
                "-movflags", "+faststart", out_path]
        try:
            run(cmd, task, "mixing the final video", progress=task.set)
        except BaseException:
            if os.path.exists(out_path):
                os.remove(out_path)  # never leave a half-written video behind
            raise
        return out_path
    finally:
        shutil.rmtree(work, ignore_errors=True)


def preview(edit: dict, out_png: str, task: Task | None = None) -> str:
    """Contact sheet: one frame per segment with kind/time/caption, for approval before render."""
    task = task or Task(track=False)
    font = find_font()
    work = tempfile.mkdtemp(prefix="nookcut_prev_")
    try:
        tiles = []
        items = list(timeline(edit))
        task.begin("grabbing frames", len(items))
        for i, (start, dur, s) in enumerate(items):
            mid = s["src_in"] + (s["src_out"] - s["src_in"]) * 0.5
            label = f"{i + 1}. {s['kind']} @{start:.1f}s ({dur:.1f}s)  src {s['src_in']:.1f}"
            cap = s["top"].replace("|", " ") or s["bottom"]
            lp = os.path.join(work, f"l{i}.txt"); open(lp, "w", encoding="utf-8").write(label)
            cp = os.path.join(work, f"c{i}.txt"); open(cp, "w", encoding="utf-8").write(cap or " ")
            out = os.path.join(work, f"p{i:03d}.png")
            vf = (f"scale=480:270,drawtext=fontfile={_q(font)}:textfile={_q(lp)}:fontsize=18:fontcolor=yellow"
                  f":box=1:boxcolor=black@0.7:x=6:y=6,drawtext=fontfile={_q(font)}:textfile={_q(cp)}:fontsize=26"
                  f":fontcolor=white:borderw=3:bordercolor=black:x=(w-text_w)/2:y=h-40")
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{mid}", "-i", edit["source"], "-frames:v", "1",
                 "-vf", vf, out], task, f"grabbing the frame for segment {i + 1}", timeout=120)
            tiles.append(out)
            task.set(i + 1)
        cols = 4
        rows = (len(tiles) + cols - 1) // cols
        run(["ffmpeg", "-v", "error", "-y", "-framerate", "1", "-i", os.path.join(work, "p%03d.png"),
             "-vf", f"tile={cols}x{rows}", "-frames:v", "1", out_png], task, "building the contact sheet")
        return out_png
    finally:
        shutil.rmtree(work, ignore_errors=True)
