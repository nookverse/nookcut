"""Environment check: ffmpeg/ffprobe, required encoders/filters, caption font, GPU encoder."""
from __future__ import annotations

import functools
import os
import platform
import re
import shutil
import subprocess

REQUIRED_FILTERS = ["drawtext", "gblur", "minterpolate", "loudnorm", "aecho", "overlay", "tile"]
REQUIRED_ENCODERS = ["libx264", "aac"]

# Hardware H.264 encoders, tried in this order; the first that really encodes a test clip is used.
# The segments are copied into the final video as they are, so quality is set close to x264 crf 17.
#   pre   options before -i      fmt   how the video graph ends (pixel format / upload to the GPU)
HW_ENCODERS = {
    "videotoolbox": {"label": "Apple VideoToolbox", "codec": "h264_videotoolbox", "pre": [], "fmt": "format=nv12",
                     "args": ["-q:v", "65"]},
    "nvenc": {"label": "NVIDIA NVENC", "codec": "h264_nvenc", "pre": [], "fmt": "format=nv12",
              "args": ["-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "19", "-b:v", "0"]},
    "amf": {"label": "AMD AMF", "codec": "h264_amf", "pre": [], "fmt": "format=nv12",
            "args": ["-quality", "quality", "-rc", "cqp", "-qp_i", "18", "-qp_p", "20", "-qp_b", "22"]},
    "qsv": {"label": "Intel Quick Sync", "codec": "h264_qsv", "pre": [], "fmt": "format=nv12",
            "args": ["-preset", "medium", "-global_quality", "20"]},
    "vaapi": {"label": "VA-API (AMD/Intel on Linux)", "codec": "h264_vaapi",
              "pre": ["-vaapi_device", "/dev/dri/renderD128"], "fmt": "format=nv12,hwupload", "args": ["-qp", "18"]},
}
SOFTWARE_ENCODER = {"label": "CPU (libx264)", "codec": "libx264", "pre": [], "fmt": "format=yuv420p",
                    "args": ["-preset", "fast", "-crf", "17"]}
# hardware decoder that pairs with each encoder; Windows uses d3d11va, which works on every GPU vendor
_DECODER_FOR = {"videotoolbox": "videotoolbox", "nvenc": "cuda", "qsv": "qsv", "vaapi": "vaapi", "amf": "d3d11va"}

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/Library/Fonts/Impact.ttf",
    "C:/Windows/Fonts/impact.ttf",
    "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def install_hint() -> str:
    system = platform.system()
    if system == "Darwin":
        # Homebrew's plain `ffmpeg` is a minimal build without drawtext (captions); ffmpeg-full has it.
        return ("brew install ffmpeg-full && echo 'export PATH=\"$(brew --prefix ffmpeg-full)/bin:$PATH\"' "
                ">> ~/.zprofile   (then open a new terminal; needs Homebrew: https://brew.sh)")
    if system == "Windows":
        return "winget install Gyan.FFmpeg   (then reopen your terminal)"
    return "sudo apt install ffmpeg   (Debian/Ubuntu)  or  sudo dnf install ffmpeg   (Fedora)"


def find_font() -> str | None:
    env = os.getenv("NOOKCUT_FONT")
    if env and os.path.exists(env):
        return env
    return next((f for f in FONT_CANDIDATES if os.path.exists(f)), None)


def _out(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout
    except OSError:
        return ""


def encoder_args(name: str | None) -> dict:
    """Encoder settings for a name from HW_ENCODERS; None (or unknown) means the CPU encoder."""
    return HW_ENCODERS.get(name or "", SOFTWARE_ENCODER)


@functools.lru_cache(maxsize=None)
def _encodes(name: str) -> bool:
    """True if this GPU encoder really works here (listed in ffmpeg is not enough: needs the GPU + driver)."""
    e = HW_ENCODERS[name]
    cmd = (["ffmpeg", "-v", "error", "-nostdin"] + e["pre"] +
           ["-f", "lavfi", "-i", "color=c=gray:s=1280x720:r=60:d=0.25", "-vf", e["fmt"],
            "-c:v", e["codec"], *e["args"], "-f", "null", "-"])
    try:
        return subprocess.run(cmd, capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def pick_encoder(encoders_listing: str) -> str | None:
    """First working GPU encoder, or None for the CPU. NOOKCUT_ENCODER=auto|cpu|<name> overrides."""
    want = os.getenv("NOOKCUT_ENCODER", "auto").strip().lower()
    if want in ("cpu", "software", "libx264"):
        return None
    names = [want] if want in HW_ENCODERS else list(HW_ENCODERS)
    return next((n for n in names if re.search(rf"\s{HW_ENCODERS[n]['codec']}\s", encoders_listing)
                 and _encodes(n)), None)


def pick_decoder(hwaccels_listing: str, encoder: str | None, system: str | None = None) -> str | None:
    """Hardware decoder to try first (render and analyze fall back to the CPU if it can't decode a file)."""
    system = system or platform.system()
    available = set(re.findall(r"^(\w+)$", hwaccels_listing, re.M))
    order = ["d3d11va"] if system == "Windows" else []
    order += [_DECODER_FOR[encoder]] if encoder else []
    order += ["videotoolbox", "cuda", "qsv", "d3d11va", "vaapi"]
    return next((h for h in order if h in available), None)


def check() -> dict:
    """Return a report; report['ok'] is False when something required is missing."""
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    report = {"ok": False, "os": platform.system(), "ffmpeg": ffmpeg, "ffprobe": ffprobe,
              "version": None, "missing": [], "font": find_font(), "install": install_hint(),
              "hardware_decode": None, "hardware_encode": None, "video_encoder": SOFTWARE_ENCODER["label"]}
    if not ffmpeg or not ffprobe:
        report["missing"] = [n for n, p in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)) if not p]
        return report
    m = re.search(r"ffmpeg version (\S+)", _out(["ffmpeg", "-version"]))
    report["version"] = m.group(1) if m else "unknown"
    filters = _out(["ffmpeg", "-hide_banner", "-filters"])
    encoders = _out(["ffmpeg", "-hide_banner", "-encoders"])
    hw = _out(["ffmpeg", "-hide_banner", "-hwaccels"])
    report["missing"] += [f"filter:{f}" for f in REQUIRED_FILTERS if not re.search(rf"\s{f}\s", filters)]
    report["missing"] += [f"encoder:{e}" for e in REQUIRED_ENCODERS if not re.search(rf"\s{e}\s", encoders)]
    report["hardware_encode"] = pick_encoder(encoders)
    report["video_encoder"] = encoder_args(report["hardware_encode"])["label"]
    report["hardware_decode"] = pick_decoder(hw, report["hardware_encode"], report["os"])
    if not report["font"]:
        report["missing"].append("font (set NOOKCUT_FONT to a bold .ttf)")
    if any(m.startswith(("filter:", "encoder:")) for m in report["missing"]):
        report["install"] = ("Your ffmpeg build lacks required parts; install a full build: " + install_hint())
    report["ok"] = not report["missing"]
    return report
