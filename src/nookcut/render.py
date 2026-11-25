"""ffmpeg renderer: one clip per segment (in parallel), then concat + audio mix.

Layouts (framing "band", the default)
  vertical  1080x1920  sharp gameplay band (1.2:1 crop) over a blurred fill, captions above/below
  square    1080x1080  full 16:9 frame over a blurred fill
  wide      1920x1080  full frame, captions over the picture
Framing "full": any orientation is filled edge to edge by a window of the picture with the output's
shape, placed by each segment's focus (and panned to focus_end), captions over the picture. Works for
any source size or shape: 720p to 4K, 4:3, ultrawide, phone-vertical.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from .edit import fps_value, seg_duration, timeline, total
from .errors import EnvironmentNotReady, InputError, MediaError, explain_ffmpeg
from . import broll as br
from . import color
from . import subtitles as subs
from .preflight import encoder_args, find_font, has_filter, install_hint, pick_hdr_encoder
from .probe import hdr_of
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


def window(src_w: int, src_h: int, W: int, H: int, zoom: float = 1.0) -> tuple[int, int]:
    """Framing "full": the largest W:H window of a src_w x src_h picture, shrunk by zoom (even sizes)."""
    zoom = max(1.0, float(zoom or 1))
    if src_w * H >= src_h * W:      # picture wider than the output: full height, cut the sides
        ch = src_h / zoom
        cw = ch * W / H
    else:                           # picture taller (phone recording into square/wide): cut top and bottom
        cw = src_w / zoom
        ch = cw * H / W
    return max(2, int(cw) // 2 * 2), max(2, int(ch) // 2 * 2)


def focus_of(s: dict) -> tuple[list, list]:
    start = list(s.get("focus") or (0.5, 0.5))
    return start, list(s.get("focus_end") or start)


def _focus_crop(s: dict, cw: int, ch: int, dur: float) -> str:
    """crop for framing "full": the window centred on focus, eased to focus_end over the segment,
    kept inside the picture. Fractions of iw/ih, so it holds for any decoded size."""
    (x0, y0), (x1, y1) = focus_of(s)
    p = f"min(1,t/{max(dur, 0.01):.4f})"
    ease = f"({p})*({p})*(3-2*({p}))"

    def axis(a, b, full, size):
        centre = f"{a:.4f}" if abs(b - a) < 1e-4 else f"({a:.4f}+({b - a:.4f})*{ease})"
        return f"'max(0,min({full}-{size},{centre}*{full}-{size}/2))'"
    return (f"crop=w='min({cw},iw)':h='min({ch},ih)':x={axis(x0, x1, 'iw', 'ow')}:y={axis(y0, y1, 'ih', 'oh')}")


def upscale(edit: dict, s: dict | None = None) -> float | None:
    """How much framing "full" enlarges the recording (2.7 for 720p into 1080x1920), or None if unknown."""
    sw, sh = edit.get("source_width"), edit.get("source_height")
    if not sw or not sh:
        return None
    if s and s.get("crop"):
        sw, sh = s["crop"][2], s["crop"][3]
    cw, _ = window(sw, sh, edit["width"], edit["height"], (s or {}).get("zoom", 1.0))
    return round(edit["width"] / cw, 2)


def color_plan(edit: dict, sdr_only: bool = False) -> dict:
    """How colour is handled: pre = filters right after decoding, args = options before -i,
    hdr = the HDR facts when the output stays HDR (else None, and the output is SDR bt709)."""
    if "source_hdr" not in edit:  # planned by an older NookCut (or a bare source): look at it now
        edit["source_hdr"] = hdr_of(edit["source"]) if edit.get("source") else None
    src = edit.get("source_hdr")
    if src and edit.get("hdr") == "keep" and not sdr_only:
        return {"pre": color.hdr_keep_filter(src), "args": [], "hdr": src, "tonemap": None}
    if not src:
        return {"pre": color.sdr_filter(edit.get("source_matrix")), "args": [], "hdr": None, "tonemap": None}
    method = color.pick_tonemap()
    if not method:
        raise EnvironmentNotReady("This video is HDR, and converting it to normal colour needs ffmpeg's zscale "
                                  "filter, which this ffmpeg lacks.", f"Install a full ffmpeg build: {install_hint()}")
    return {"pre": color.tonemap_filter(method), "args": color.tonemap_args(method), "hdr": None, "tonemap": method}


def _speech(work, i, words, dur, L, font, W, H, hdr=None):
    """Word-by-word speech captions where the bottom caption would go (libass, else drawtext)."""
    if has_filter("ass"):
        p = os.path.join(work, f"s{i}.ass")
        with open(p, "w", encoding="utf-8") as f:
            f.write(subs.ass(words, dur, W, H, L["bottom_y"], L["speech"], font, L["speech_chars"],
                             colour=lambda c: color.caption_color(c, hdr)))
        return [f"ass={_q(p)}:fontsdir={_q(os.path.dirname(font))}"]
    out = []
    for k, (a, b, text) in enumerate(subs.plain(words, dur, L["speech_chars"])):
        p = os.path.join(work, f"s{i}_{k}.txt"); open(p, "w", encoding="utf-8").write(text)
        out.append(f"drawtext=fontfile={_q(font)}:textfile={_q(p)}:fontsize={L['speech']}"
                   f":fontcolor={color.caption_color('white', hdr)}"
                   f":borderw=6:bordercolor=black:x=(w-text_w)/2:y={L['bottom_y']}:enable='between(t,{a:.3f},{b:.3f})'")
    return out


def _text(work, i, s, dur, L, font, W=0, H=0, speech=None, hdr=None):
    """Captions and transitions; colours are lowered to paper white when the output is HDR."""
    f, kind = [], s["kind"]
    c = color.caption_color("0xFF2A2A" if kind == "death" else "white", hdr)
    flash = color.caption_color("white", hdr)
    for j, line in enumerate(x for x in s["top"].split("|") if x):
        p = os.path.join(work, f"t{i}_{j}.txt"); open(p, "w", encoding="utf-8").write(line)
        f.append(f"drawtext=fontfile={_q(font)}:textfile={_q(p)}:fontsize={L['big']}:fontcolor={c}"
                 f":borderw=7:bordercolor=black:x=(w-text_w)/2:y={L['top_y'] + j * L['line']}:alpha='min(1,t/0.12)'")
    if speech:
        f += _speech(work, i, speech, dur, L, font, W, H, hdr)
    elif s["bottom"]:
        p = os.path.join(work, f"b{i}.txt"); open(p, "w", encoding="utf-8").write(s["bottom"].replace("|", " "))
        bcolor = color.caption_color("0xFF2A2A" if kind == "death" else "0xFFD400", hdr)
        f.append(f"drawtext=fontfile={_q(font)}:textfile={_q(p)}:fontsize={L['small']}:fontcolor={bcolor}"
                 f":borderw=5:bordercolor=black:x=(w-text_w)/2:y={L['bottom_y']}:alpha='min(1,t/0.2)'")
    if kind in ("death", "finisher"):
        f.append(f"fade=in:st=0:d=0.12:color={flash}")
    if kind == "hook":
        f.append(f"fade=out:st={dur - 0.12:.3f}:d=0.12:color={flash}")
    if kind == "end":
        f.append(f"fade=out:st={max(0, dur - 0.6):.3f}:d=0.6")
    return f


INSET = 0.38      # an inset picture's width, as a share of the picture area
INSET_GAP = 24    # its distance from the corner, in pixels


def _fill(w: int, h: int) -> str:
    """Scale to cover a w x h box and crop the overflow (any aspect in, no black edges)."""
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"


def _broll(edit: dict, s: dict, box: tuple, rate: str, dur: float, look_args: list, L: dict) -> tuple:
    """B-roll over the picture area box=(w, h, x, y): extra input options, the filter steps that
    turn [base] (and [fg2], the source picture, when the layout needs it) into [cv], and whether
    [fg2] is needed. The source's cut, speed and sound are untouched."""
    b = s["broll"]
    path = br.resolve(edit.get("broll_dir", ""), b["file"])
    info = br._probe_one(path, None)
    w, h, x, y = box
    t0 = float(b.get("start") or 0)
    t1 = float(b["end"]) if b.get("end") is not None else dur
    on = f":enable='between(t,{t0:.3f},{t1:.3f})'"
    # its own colour handling: B-roll can be HDR or BT.601 whatever the main recording is
    if info["hdr"]:
        method = color.pick_tonemap()
        if not method:
            raise EnvironmentNotReady(f"The B-roll file {b['file']} is HDR, and converting it needs ffmpeg's "
                                      "zscale filter.", "Use an SDR copy of it, or install a full ffmpeg build.")
        pre = color.tonemap_filter(method) + ","
        extra = [a for a in color.tonemap_args(method) if a not in look_args] if method == "videotoolbox" else []
        if extra and "-init_hw_device" in look_args:
            extra = []
    else:
        pre, extra = color.sdr_filter(info["matrix"]) + ",", []
    if info["still"]:
        args = extra + ["-loop", "1", "-framerate", rate, "-t", f"{dur + 0.5:.3f}", "-i", path]
    else:
        args = extra + ["-stream_loop", "-1", "-ss", f"{float(b.get('in') or 0):.3f}", "-t", f"{dur + 0.5:.3f}",
                        "-i", path]
    src = f"[1:v]{pre}setpts=PTS-STARTPTS+{t0:.3f}/TB,fps={rate}"

    def fitted(bw, bh):
        if not info["still"]:
            return _fill(bw, bh)
        # a still drifts slowly across, so it looks alive (and isn't a frozen picture)
        zw, zh = int(bw * 1.12) // 2 * 2, int(bh * 1.12) // 2 * 2
        span = max(0.5, t1 - t0)
        return (f"{_fill(zw, zh)},crop={bw}:{bh}:x='(iw-ow)*min(1,max(0,(t-{t0:.3f})/{span:.3f}))'"
                f":y='(ih-oh)/2'")

    layout, main = b.get("layout", "cover"), b.get("main", "broll")
    iw = int(w * INSET) // 2 * 2
    ih = int(iw * h / w) // 2 * 2
    bottom = y + h - INSET_GAP
    if L["bottom_y"] < y + h:      # captions are drawn over the picture (wide): keep the box above them
        bottom = min(bottom, L["bottom_y"] - INSET_GAP)
    corner = (x + w - iw - INSET_GAP - 8, bottom - ih - 8)
    frame = "pad=iw+8:ih+8:4:4:color=white"
    if layout == "split":
        half = w // 2 // 2 * 2
        steps = (f"{src},{fitted(w - half, h)}[br];[fg2]crop={half}:{h}:{(w - half) // 2}:0[sh];"
                 f"[base][sh]overlay={x}:{y}{on}[t1];[t1][br]overlay={x + half}:{y}{on}[cv]")
        return args, steps, True
    if layout == "inset" and main == "source":
        steps = f"{src},{fitted(iw, ih)},{frame}[br];[base][br]overlay={corner[0]}:{corner[1]}{on}[cv]"
        return args, steps, False
    if layout == "inset":   # B-roll full-size, the speaker (or gameplay) in the corner
        steps = (f"{src},{fitted(w, h)}[br];[base][br]overlay={x}:{y}{on}[t1];"
                 f"[fg2]scale={iw}:{ih},{frame}[sm];[t1][sm]overlay={corner[0]}:{corner[1]}{on}[cv]")
        return args, steps, True
    if layout == "fit":   # the whole clip, over a blurred, darker copy filling the box
        steps = (f"{src},split[b1][b2];[b1]{fitted(w, h)},gblur=sigma=24,eq=brightness=-0.15[bb];"
                 f"[b2]scale={w}:{h}:force_original_aspect_ratio=decrease,setsar=1[bf];"
                 f"[bb][bf]overlay=(W-w)/2:(H-h)/2[br];[base][br]overlay={x}:{y}{on}[cv]")
        return args, steps, False
    steps = f"{src},{fitted(w, h)}[br];[base][br]overlay={x}:{y}{on}[cv]"
    return args, steps, False


def needs_interpolation(edit: dict, s: dict) -> bool:
    """Slow motion needs made-up frames only when the source has too few: source fps x speed below
    the output rate (60 fps at 0.5x gives 30: interpolate; 120 fps at 0.5x gives 60: real frames)."""
    src = float(edit.get("source_fps") or 30)
    return s["speed"] < 1 and src * s["speed"] < fps_value(edit) * 0.95


def _segment(edit, i, s, work, font, hwaccel, src_w, src_h, task=None, on_progress=None, encoder=None,
             look=None):
    look = look or color_plan(edit)
    hdr = look["hdr"]
    pre = look["pre"]
    if s.get("crop"):  # black bars cut off first, so the layout works with the picture itself
        bx, by, src_w, src_h = s["crop"]
        pre += f",crop={src_w}:{src_h}:{bx}:{by}"
    L = _layout(edit)
    enc = encoder_args(encoder, hdr=bool(hdr))
    dur = seg_duration(s)
    zoom = s.get("zoom", 1.0)
    W, H = edit["width"], edit["height"]
    full = edit.get("framing") == "full"
    if full:
        # the picture fills the frame: a window of the output's shape, placed by focus
        cw, ch = window(src_w, src_h, W, H, zoom)
        fg_w, fg_h, x, y = W, H, 0, 0
    elif src_w / src_h >= L["crop_aspect"]:
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
    if full and focus_of(s)[0] != focus_of(s)[1]:
        # a pan: enlarge first, then move the window in output pixels, so a slow pan glides
        # instead of stepping by whole (enlarged) source pixels
        k = W / cw
        fg = (f"scale={int(src_w * k) // 2 * 2}:{int(src_h * k) // 2 * 2}:flags=lanczos,"
              f"{_focus_crop(s, W, H, dur)},setsar=1")
    elif full:
        fg = f"{_focus_crop(s, cw, ch, dur)},scale={fg_w}:{fg_h}:flags=lanczos,setsar=1"
    else:
        fg = f"crop='min({cw},iw)':'min({ch},ih)',scale={fg_w}:{fg_h}"
    rate = str(edit.get("fps") or 60)
    if needs_interpolation(edit, s):  # only invent frames the source doesn't have (120 fps at 0.5x has them)
        fg += f",minterpolate=fps={rate}:mi_mode=mci:mc_mode=aobmc:vsbmc=1"
    if s["kind"] == "card":
        fg += ",eq=brightness=-0.25:saturation=0.6"
    extra_in, layer, need_src = [], "", False
    if s.get("broll"):
        if hdr:
            raise InputError("B-roll can't be shown in HDR output yet.",
                             "Set hdr to sdr, or remove the B-roll from this segment (update_edit broll: null).")
        extra_in, layer, need_src = _broll(edit, s, (fg_w, fg_h, x, y), rate, dur, look["args"], L)
    picture = ",split[fg][fg2]" if need_src else "[fg]"
    if (fg_w, fg_h) == (W, H):
        graph = f"[0:v]{pre},setpts=(PTS-STARTPTS)/{s['speed']},fps={rate},{fg}" + \
            (",split[base][fg2]" if need_src else "[base]")
    else:
        graph = (f"[0:v]{pre},setpts=(PTS-STARTPTS)/{s['speed']},fps={rate},split[a][b];"
                 f"[a]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,crop={W // 4}:{H // 4},"
                 f"gblur=sigma=10,scale={W}:{H},eq=brightness=-0.18:saturation=0.8[bg];[b]{fg}{picture};"
                 f"[bg][fg]overlay={x}:{y}" + (":format=yuv420p10" if hdr else "") + "[base]")
    if layer:
        graph += ";" + layer
    speech = subs.local_words(s) if edit.get("subtitles") else []
    if not speech and edit.get("vo_captions") and s.get("_vo_words"):
        speech = s["_vo_words"]   # the voiceover's own words, timed to the spoken line
    graph += f";[{'cv' if layer else 'base'}]" + \
        ",".join(_text(work, i, s, dur, L, font, W, H, speech, hdr) + [enc["fmt"]]) + "[v]"
    out = os.path.join(work, f"seg{i:03d}.mov")
    cmd = ["ffmpeg", "-v", "error", "-y"] + enc["pre"] + look["args"]
    if hwaccel:
        cmd += ["-hwaccel", hwaccel]
    cmd += ["-ss", f"{s['src_in']}", "-t", f"{s['src_out'] - s['src_in']}", "-i", edit["source"]]
    cmd += extra_in   # B-roll is input 1; the sound always comes from the recording
    has_audio = edit.get("source_audio") != "mute"
    # 10 ms fades at both ends: jump cuts join without clicks
    afade = f"afade=t=in:d=0.01,afade=t=out:st={max(0.0, dur - 0.01):.4f}:d=0.01"
    audio = f"[0:a:0]atempo={max(0.5, s['speed'])},asetpts=PTS-STARTPTS,{afade}[a]"
    # HDR: VideoToolbox (seen with ffmpeg 9) drops the HDR10 mastering/light-level metadata when the
    # same encode also writes audio, so the sound is cut on its own and muxed back in afterwards
    together = has_audio and not hdr
    cmd += ["-filter_complex", graph + (";" + audio if together else ""), "-map", "[v]"]
    if together:
        cmd += ["-map", "[a]", "-c:a", "pcm_s16le", "-ar", "48000"]
    cmd += ["-t", f"{dur:.4f}", "-c:v", enc["codec"], *enc["args"], *color.color_tags(hdr)]
    if hdr and enc["codec"] == "libx265":
        cmd += ["-x265-params", color.x265_params(hdr)]
    video = out if together or not has_audio else out[:-4] + "_v.mov"
    cmd += ["-r", rate, video]
    run(cmd, task, f"rendering segment {i + 1} ({s['kind']} at {s['src_in']:.1f}s)", progress=on_progress)
    if video != out:
        wav = out[:-4] + ".wav"
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{s['src_in']}", "-t", f"{s['src_out'] - s['src_in']}",
             "-i", edit["source"], "-filter_complex", audio, "-map", "[a]", "-c:a", "pcm_s16le", "-ar", "48000",
             "-t", f"{dur:.4f}", wav], task, f"cutting the sound of segment {i + 1}")
        # one file per segment again, so the join re-aligns sound and picture at every cut
        run(["ffmpeg", "-v", "error", "-y", "-i", video, "-i", wav, "-map", "0:v", "-map", "1:a", "-c", "copy",
             "-shortest", out], task, f"joining the sound of segment {i + 1}")
    return out


def _workers() -> int:
    try:
        return max(1, int(os.environ["NOOKCUT_WORKERS"]))
    except (KeyError, ValueError):
        return min(4, max(1, (os.cpu_count() or 2) // 2))


def _mix_graph(cmd: list, edit: dict, has_src_audio: bool, music_wav: str | None, voice_wav: str | None) -> str:
    """The final audio: the recording's sound and the music (the bed), ducked under the voiceover when
    there is one, then brought to the platforms' loudness (-14 LUFS) with true peaks under -2 dBTP
    (AAC adds about half a dB, so the finished file stays under -1 dBTP). Adds its inputs to cmd."""
    master = f"loudnorm=I={TARGET_LUFS}:TP=-2:LRA=11,aresample=48000"
    n, parts, bed = 1, [], None
    if music_wav:
        cmd += ["-i", music_wav]
        music, n = f"{n}:a", n + 1
    if voice_wav:
        cmd += ["-i", voice_wav]
        vo = f"{n}:a"
    if has_src_audio and music_wav:
        game_vol = 1.0 if edit["source_audio"] == "keep" else 0.9
        mus_vol = 0.35 if edit["source_audio"] == "keep" else 0.6
        parts.append(f"[0:a]volume={game_vol}[g];[{music}]volume={mus_vol}[m];"
                     "[g][m]amix=inputs=2:duration=first:normalize=0[bed]")
        bed = "bed"
    elif music_wav:
        parts.append(f"[{music}]anull[bed]")
        bed = "bed"
    elif has_src_audio:
        parts.append("[0:a]anull[bed]")
        bed = "bed"
    if voice_wav and bed:
        # the bed dips about 12 dB while the voice speaks and comes back after it
        parts.append(f"[{vo}]asplit[key][v];[bed][key]sidechaincompress=threshold=0.02:ratio=8:attack=15:"
                     f"release=350:makeup=1[duck];[duck][v]amix=inputs=2:duration=first:normalize=0,{master}[a]")
    elif voice_wav:
        parts.append(f"[{vo}]{master}[a]")
    elif bed:
        parts.append(f"[bed]{master}[a]")
    return ";".join(parts)


def render(edit: dict, out_path: str, music_wav: str | None, hwaccel: str | None = None,
           workers: int | None = None, only: list[int] | None = None, task: Task | None = None,
           encoder: str | None = None, voice_wav: str | None = None) -> str:
    """encoder: a GPU encoder from preflight.HW_ENCODERS, or None for the CPU (libx264). When the
    source is HDR and the edit keeps HDR, the HDR encoder (preflight.HDR_ENCODERS) is picked here."""
    task = task or Task(track=False)
    font = find_font()
    if not font:
        raise EnvironmentNotReady("No caption font was found.", "Set NOOKCUT_FONT to a .ttf/.otf file path.")
    look = color_plan(edit)
    if look["hdr"]:
        encoder = pick_hdr_encoder()
        if not encoder and not re.search(r"\slibx265\s", subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout):
            raise EnvironmentNotReady("Keeping HDR needs a 10-bit HEVC encoder, and none works here.",
                                      "Set hdr to sdr, or install a full ffmpeg build: " + install_hint())
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
            label = encoder_args(enc, hdr=bool(look["hdr"]))["label"]
            if look["tonemap"]:
                label += f", HDR converted on the {'GPU' if look['tonemap'] == 'videotoolbox' else 'CPU'}"
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
                               enc, look)
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
            if not encoder and look["tonemap"] != "videotoolbox":
                raise
            # GPU encoders can fail mid-job (driver hiccup, NVENC session limit while OBS records):
            # redo every segment on the CPU, since mixing encoders would break the stream-copy join.
            # A GPU tone map that failed is redone on the CPU too.
            if look["tonemap"] == "videotoolbox" and color.pick_tonemap() != "cpu" and color._works("cpu"):
                look.update(pre=color.tonemap_filter("cpu"), args=[], tonemap="cpu")
            segs = render_all(None)
        segs = [segs[i] for i in idx]
        task.begin("mixing audio", total(edit), note="joining segments, music and game sound")
        listing = os.path.join(work, "list.txt")
        with open(listing, "w") as f:
            f.writelines(f"file '{p}'\n" for p in segs)
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing]
        has_src_audio = edit.get("source_audio") != "mute"
        audio = _mix_graph(cmd, edit, has_src_audio, music_wav, voice_wav)   # adds its inputs first
        cmd += ["-map", "0:v"]
        if audio:
            cmd += ["-filter_complex", audio, "-map", "[a]"]
        cmd += ["-c:v", "copy", *(["-tag:v", "hvc1"] if look["hdr"] else []), *color.color_tags(look["hdr"]),
                "-c:a", "aac", "-b:a", "320k", "-t", f"{total(edit):.3f}",
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


def _window_boxes(edit: dict, s: dict) -> str:
    """Contact sheet, framing "full": outline what the viewer sees (yellow at the start, cyan where a
    pan ends), so a wrong focus shows before anything is rendered."""
    W, H, z = edit["width"], edit["height"], max(1.0, float(s.get("zoom") or 1))
    bw, bh = f"min(iw\\,ih*{W}/{H})/{z}", f"min(ih\\,iw*{H}/{W})/{z}"
    out = ""
    start, end = focus_of(s)
    for (fx, fy), col in ((start, "yellow"), (end, "cyan")) if end != start else ((start, "yellow"),):
        out += (f",drawbox=w={bw}:h={bh}:x=max(0\\,min(iw-w\\,{fx}*iw-w/2)):"
                f"y=max(0\\,min(ih-h\\,{fy}*ih-h/2)):color={col}:t=12")
    return out


PREVIEW_MAX = 40
TARGET_LUFS = -14   # where YouTube, TikTok and Instagram normalise short-form video


def preview(edit: dict, out_png: str, task: Task | None = None) -> str:
    """Contact sheet: one frame per segment with kind/time/caption, for approval before render."""
    task = task or Task(track=False)
    font = find_font()
    look = color_plan(edit, sdr_only=True)  # the contact sheet is always SDR
    work = tempfile.mkdtemp(prefix="nookcut_prev_")
    try:
        tiles = []
        items = list(timeline(edit))
        if len(items) > PREVIEW_MAX:  # a tightened talk can have hundreds of cuts: sample them evenly
            items = [items[round(k * (len(items) - 1) / (PREVIEW_MAX - 1))] for k in range(PREVIEW_MAX)]
        task.begin("grabbing frames", len(items))
        for i, (start, dur, s) in enumerate(items):
            mid = s["src_in"] + (s["src_out"] - s["src_in"]) * 0.5
            label = f"{i + 1}. {s['kind']} @{start:.1f}s ({dur:.1f}s)  src {s['src_in']:.1f}"
            cap = s["top"].replace("|", " ") or s["bottom"]
            lp = os.path.join(work, f"l{i}.txt"); open(lp, "w", encoding="utf-8").write(label)
            cp = os.path.join(work, f"c{i}.txt"); open(cp, "w", encoding="utf-8").write(cap or " ")
            out = os.path.join(work, f"p{i:03d}.png")
            bars = f",crop={s['crop'][2]}:{s['crop'][3]}:{s['crop'][0]}:{s['crop'][1]}" if s.get("crop") else ""
            source, at, pre_args, colour = edit["source"], mid, look["args"], look["pre"] + bars
            b = s.get("broll")
            local = (mid - s["src_in"]) / s["speed"]
            if b and float(b.get("start") or 0) <= local <= (b["end"] if b.get("end") is not None else dur):
                # the sheet shows what the viewer will see: the B-roll, where it covers the picture
                source = br.resolve(edit.get("broll_dir", ""), b["file"])
                at, pre_args, colour = float(b.get("in") or 0) + local - float(b.get("start") or 0), [], "null"
                length = br._probe_one(source, task).get("duration")
                if length:
                    at %= length   # short clips loop in the render; the sheet shows the same frame
                label += f"  B-roll: {b['file']} ({b.get('layout', 'cover')})"
                open(lp, "w", encoding="utf-8").write(label)
            if edit.get("framing") == "full" and colour != "null":
                colour += _window_boxes(edit, s)
            vf = (f"{colour},scale=480:270,drawtext=fontfile={_q(font)}:textfile={_q(lp)}:fontsize=18:fontcolor=yellow"
                  f":box=1:boxcolor=black@0.7:x=6:y=6,drawtext=fontfile={_q(font)}:textfile={_q(cp)}:fontsize=26"
                  f":fontcolor=white:borderw=3:bordercolor=black:x=(w-text_w)/2:y=h-40")
            still = source.lower().endswith(br.IMAGE_EXT)
            run(["ffmpeg", "-v", "error", "-y", *pre_args, *([] if still else ["-ss", f"{at:.3f}"]), "-i", source,
                 "-frames:v", "1",
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
