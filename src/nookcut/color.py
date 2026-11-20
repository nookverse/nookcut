"""HDR sources: detect HDR10/HLG, tone-map to SDR (on the GPU when it can), or keep HDR10 output.

Without this an HDR recording (PS5/Xbox captures, iPhone video) renders grey and washed out:
PQ/HLG pixels read as if they were SDR. Each method is test-run once before it is trusted, like
the GPU encoders in preflight.

  videotoolbox  Apple GPU (scale_vt): tone map with almost no CPU
  cpu           zscale + Hable tone map: works everywhere zscale (libzimg) exists
NOOKCUT_TONEMAP=auto|videotoolbox|cpu picks one; auto tries them in that order.
"""
from __future__ import annotations

import functools
import math
import os
import platform
import subprocess
from fractions import Fraction

from .preflight import has_filter

HDR_TRANSFERS = {"smpte2084": "pq", "arib-std-b67": "hlg"}
METHODS = ("videotoolbox", "cpu")


def hdr_from_probe(stream: dict, frame: dict | None = None) -> dict | None:
    """HDR facts from an ffprobe video stream (and its first frame's side data), or None for SDR."""
    kind = HDR_TRANSFERS.get(stream.get("color_transfer", ""))
    if not kind:
        return None
    out = {"transfer": kind, "trc": stream["color_transfer"],
           "primaries": stream.get("color_primaries") or "bt2020",
           "matrix": stream.get("color_space") or "bt2020nc", "range": stream.get("color_range") or "tv",
           "bits": 10 if "10" in stream.get("pix_fmt", "") or "12" in stream.get("pix_fmt", "") else 8}
    for sd in (frame or {}).get("side_data_list", []) + stream.get("side_data_list", []):
        t = sd.get("side_data_type", "")
        if t == "Mastering display metadata" and "master_display" not in out:
            out["master_display"] = _x265_master_display(sd)
        elif t == "Content light level metadata" and "max_cll" not in out:
            out["max_cll"] = f"{sd.get('max_content', 0)},{sd.get('max_average', 0)}"
    return out


def _x265_master_display(sd: dict) -> str | None:
    """ffprobe's fractions -> x265's G(x,y)B(x,y)R(x,y)WP(x,y)L(max,min) (0.00002 / 0.0001 units)."""
    try:
        c = {k: round(Fraction(sd[k]) * 50000) for k in
             ("red_x", "red_y", "green_x", "green_y", "blue_x", "blue_y", "white_point_x", "white_point_y")}
        lmax, lmin = round(Fraction(sd["max_luminance"]) * 10000), round(Fraction(sd["min_luminance"]) * 10000)
    except (KeyError, ValueError, ZeroDivisionError):
        return None
    return (f"G({c['green_x']},{c['green_y']})B({c['blue_x']},{c['blue_y']})R({c['red_x']},{c['red_y']})"
            f"WP({c['white_point_x']},{c['white_point_y']})L({lmax},{lmin})")


# ---------- HDR -> SDR ----------

_SDR_OUT = "scale=out_range=tv:out_color_matrix=bt709"
_CPU = ("zscale=t=linear:npl=203,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
        "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
_VT = ("format=p010le,hwupload,scale_vt=color_matrix=bt709:color_primaries=bt709:color_transfer=bt709,"
       "hwdownload,format=p010le," + _SDR_OUT + ",format=yuv420p")


def tonemap_filter(method: str) -> str:
    """Filters that turn HDR frames into SDR bt709 8-bit, limited range."""
    return _VT if method == "videotoolbox" else _CPU


def tonemap_args(method: str) -> list[str]:
    """ffmpeg options the tone map needs before -i (a GPU device for the filters)."""
    return ["-init_hw_device", "videotoolbox=vt", "-filter_hw_device", "vt"] if method == "videotoolbox" else []


def sdr_filter() -> str:
    """SDR sources: limited range (some captures are full range). Only the range changes: an untagged
    source would otherwise be read as BT.601 and converted, shifting its colours."""
    return "scale=out_range=tv,format=yuv420p"


def _test_source() -> list[str]:
    return ["-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=0.3,format=yuv420p10le,"
            "setparams=color_trc=smpte2084:color_primaries=bt2020:colorspace=bt2020nc:range=tv"]


@functools.lru_cache(maxsize=None)
def _works(method: str) -> bool:
    if method == "videotoolbox" and (platform.system() != "Darwin" or not has_filter("scale_vt")):
        return False
    if method == "cpu" and not (has_filter("zscale") and has_filter("tonemap")):
        return False
    cmd = ["ffmpeg", "-v", "error", "-nostdin", *tonemap_args(method), *_test_source(),
           "-vf", tonemap_filter(method), "-f", "null", "-"]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def pick_tonemap() -> str | None:
    """The first tone map that really runs here (GPU first), or None if there is none."""
    want = os.getenv("NOOKCUT_TONEMAP", "auto").strip().lower()
    names = [want] if want in METHODS else list(METHODS)
    return next((m for m in names if _works(m)), None)


# ---------- keeping HDR ----------

def hdr_keep_filter(hdr: dict) -> str:
    """HDR frames -> 10-bit, limited range, in their own transfer (PS5 captures are full range)."""
    return "scale=out_range=tv:out_color_matrix=bt2020,format=yuv420p10le"


def color_tags(hdr: dict | None) -> list[str]:
    """Output colour tags: what players and platforms read to show the picture right."""
    if hdr:
        return ["-color_primaries", "bt2020", "-color_trc", hdr["trc"], "-colorspace", "bt2020nc",
                "-color_range", "tv"]
    return ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv"]


def x265_params(hdr: dict) -> str:
    """HDR10 signalling for the CPU encoder (GPU encoders copy the frames' metadata themselves)."""
    p = [f"colorprim=bt2020", f"transfer={hdr['trc']}", "colormatrix=bt2020nc", "range=limited",
         "repeat-headers=1"]
    if hdr["transfer"] == "pq":
        p.append("hdr10=1")
        if hdr.get("master_display"):
            p.append(f"master-display={hdr['master_display']}")
        if hdr.get("max_cll"):
            p.append(f"max-cll={hdr['max_cll']}")
    return ":".join(p)


_BT709_TO_BT2020 = ((0.6274, 0.3293, 0.0433), (0.0691, 0.9195, 0.0114), (0.0164, 0.0880, 0.8956))


def _pq(nits: float) -> float:
    """SMPTE ST 2084 inverse EOTF: cd/m2 -> signal 0..1."""
    m1, m2, c1, c2, c3 = 2610 / 16384, 2523 / 4096 * 128, 3424 / 4096, 2413 / 4096 * 32, 2392 / 4096 * 32
    y = max(0.0, nits) / 10000
    return ((c1 + c2 * y ** m1) / (1 + c3 * y ** m1)) ** m2


def _hlg(e: float) -> float:
    """ARIB STD-B67 OETF: scene light 0..1 -> signal 0..1."""
    a, b, c = 0.17883277, 0.28466892, 0.55991073
    return (3 * e) ** 0.5 if e <= 1 / 12 else a * math.log(12 * e - b) + c


def caption_color(hex_rgb: str, hdr: dict | None) -> str:
    """A caption colour (0xRRGGBB or 'white') for the output: as is for SDR; for HDR the same
    colour at paper white (203 cd/m2, ITU-R BT.2408) in BT.2020 PQ/HLG, so text doesn't glare
    and keeps its hue."""
    rgb = "FFFFFF" if hex_rgb == "white" else hex_rgb.removeprefix("0x").removeprefix("#")
    if not hdr:
        return "0x" + rgb
    lin = [(int(rgb[i:i + 2], 16) / 255) ** 2.4 for i in (0, 2, 4)]         # BT.1886 display light
    wide = [sum(m * v for m, v in zip(row, lin)) for row in _BT709_TO_BT2020]
    if hdr["transfer"] == "pq":
        sig = [_pq(203 * v) for v in wide]
    else:  # HLG reference white is 75% signal, scene light 0.2647
        sig = [_hlg(0.2647 * v) for v in wide]
    return "0x" + "".join(f"{min(255, round(v * 255)):02X}" for v in sig)
