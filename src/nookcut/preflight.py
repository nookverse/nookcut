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
# HDR output (`hdr: keep`): 10-bit HEVC Main10, the codec the platforms take HDR10/HLG in. H.264 can't
# carry it on these GPUs. Each one is test-encoded with a 10-bit clip before it's used.
HDR_ENCODERS = {
    "videotoolbox": {"label": "Apple VideoToolbox (HEVC 10-bit)", "codec": "hevc_videotoolbox", "pre": [],
                     "fmt": "format=p010le", "args": ["-profile:v", "main10", "-q:v", "65", "-tag:v", "hvc1"]},
    "nvenc": {"label": "NVIDIA NVENC (HEVC 10-bit)", "codec": "hevc_nvenc", "pre": [], "fmt": "format=p010le",
              "args": ["-profile:v", "main10", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "19", "-b:v", "0",
                       "-tag:v", "hvc1"]},
    "amf": {"label": "AMD AMF (HEVC 10-bit)", "codec": "hevc_amf", "pre": [], "fmt": "format=p010le",
            "args": ["-profile:v", "main10", "-quality", "quality", "-rc", "cqp", "-qp_i", "18", "-qp_p", "20",
                     "-tag:v", "hvc1"]},
    "qsv": {"label": "Intel Quick Sync (HEVC 10-bit)", "codec": "hevc_qsv", "pre": [], "fmt": "format=p010le",
            "args": ["-profile:v", "main10", "-preset", "medium", "-global_quality", "20", "-tag:v", "hvc1"]},
    "vaapi": {"label": "VA-API (HEVC 10-bit)", "codec": "hevc_vaapi", "pre": ["-vaapi_device", "/dev/dri/renderD128"],
              "fmt": "format=p010le,hwupload", "args": ["-profile:v", "main10", "-qp", "18", "-tag:v", "hvc1"]},
}
SOFTWARE_HDR_ENCODER = {"label": "CPU (libx265 10-bit)", "codec": "libx265", "pre": [], "fmt": "format=yuv420p10le",
                        "args": ["-preset", "fast", "-crf", "18", "-tag:v", "hvc1"]}
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


def encoder_args(name: str | None, hdr: bool = False) -> dict:
    """Encoder settings for a name from HW_ENCODERS (HDR_ENCODERS when hdr); None means the CPU."""
    if hdr:
        return HDR_ENCODERS.get(name or "", SOFTWARE_HDR_ENCODER)
    return HW_ENCODERS.get(name or "", SOFTWARE_ENCODER)


@functools.lru_cache(maxsize=None)
def _encodes(name: str, hdr: bool = False) -> bool:
    """True if this GPU encoder really works here (listed in ffmpeg is not enough: needs the GPU + driver)."""
    e = (HDR_ENCODERS if hdr else HW_ENCODERS)[name]
    src = "color=c=gray:s=1280x720:r=60:d=0.25" + (",format=yuv420p10le" if hdr else "")
    cmd = (["ffmpeg", "-v", "error", "-nostdin"] + e["pre"] +
           ["-f", "lavfi", "-i", src, "-vf", e["fmt"],
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


@functools.lru_cache(maxsize=None)
def pick_hdr_encoder() -> str | None:
    """First GPU encoder that really writes 10-bit HEVC here, or None for the CPU (libx265).
    NOOKCUT_ENCODER=cpu|<name> applies here too."""
    want = os.getenv("NOOKCUT_ENCODER", "auto").strip().lower()
    if want in ("cpu", "software", "libx264", "libx265"):
        return None
    listing = _out(["ffmpeg", "-hide_banner", "-encoders"])
    names = [want] if want in HDR_ENCODERS else list(HDR_ENCODERS)
    return next((n for n in names if re.search(rf"\s{HDR_ENCODERS[n]['codec']}\s", listing)
                 and _encodes(n, True)), None)


def hdr_status() -> dict:
    """What happens to HDR sources here: the tone map used for SDR output, and the HDR encoder."""
    from .color import pick_tonemap
    tm = pick_tonemap()
    enc = pick_hdr_encoder()
    x265 = re.search(r"\slibx265\s", _out(["ffmpeg", "-hide_banner", "-encoders"])) is not None
    out = {"to_sdr": {"videotoolbox": "Apple GPU", "cpu": "CPU (zscale)"}.get(tm, "not possible"),
           "keep_hdr": encoder_args(enc, True)["label"] if enc or x265 else "not possible"}
    if not tm:
        out["how"] = "HDR videos need ffmpeg's zscale filter (libzimg) to convert. " + install_hint()
    return out


def pick_decoder(hwaccels_listing: str, encoder: str | None, system: str | None = None) -> str | None:
    """Hardware decoder to try first (render and analyze fall back to the CPU if it can't decode a file)."""
    system = system or platform.system()
    available = set(re.findall(r"^(\w+)$", hwaccels_listing, re.M))
    order = ["d3d11va"] if system == "Windows" else []
    order += [_DECODER_FOR[encoder]] if encoder else []
    order += ["videotoolbox", "cuda", "qsv", "d3d11va", "vaapi"]
    return next((h for h in order if h in available), None)


@functools.lru_cache(maxsize=None)
def has_filter(name: str) -> bool:
    return re.search(rf"\s{re.escape(name)}\s", _out(["ffmpeg", "-hide_banner", "-filters"])) is not None


def speech_status() -> dict:
    """Optional speech-to-text: ffmpeg's whisper filter plus a model from `nookcut models install`."""
    from .models import whisper_model
    model = whisper_model()
    out = {"ready": False, "whisper_filter": has_filter("whisper"), "model": model[0] if model else None,
           "word_highlight": has_filter("ass")}
    if not out["whisper_filter"]:
        out["how"] = ("Your ffmpeg has no whisper filter (needs ffmpeg 8+ built with whisper.cpp). " +
                      ("ffmpeg-full has it: " + install_hint() if platform.system() == "Darwin"
                       else "Install an ffmpeg 8+ build with whisper.cpp."))
    elif not model:
        out["how"] = "Run `nookcut models install` once (downloads a 148 MB speech model; works offline after)."
    else:
        out["ready"] = True
    return out


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
    report["speech_to_text"] = speech_status()  # optional: word-by-word captions from what is said
    report["machine"] = dict(machine(), render_workers=render_workers())
    return report


# ---------- the computer itself: how many renders to run at once ----------

GB = 1024 ** 3


@functools.lru_cache(maxsize=1)
def machine() -> dict:
    """Memory, CPU count and whether this is a fanless laptop (MacBook Air), read once per process."""
    try:
        memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):  # Windows has no sysconf
        memory = 0
    model = ""
    if platform.system() == "Darwin":
        # the model identifier (Mac15,12) no longer says "Air", the model name does
        # full path: the shared server runs under launchd, whose PATH has no /usr/sbin
        tool = shutil.which("system_profiler") or "/usr/sbin/system_profiler"
        m = re.search(r"Model Name:\s*(.+)", _out([tool, "SPHardwareDataType"]))
        model = m.group(1).strip() if m else ""
    return {"memory_gb": round(memory / GB, 1) if memory else None, "cpus": os.cpu_count() or 2,
            "model": model or None, "fanless": "MacBook Air" in model}


def pick_workers(cpus: int, memory_gb: float | None, fanless: bool) -> int:
    """Segments rendered at once: half the cores, at most 4, about one per 4 GB of memory (4K segments
    with slow motion take 1-2 GB each), and at most 2 on a fanless laptop, which slows down when hot."""
    n = min(4, max(1, cpus // 2))
    if memory_gb:
        n = min(n, max(1, int(memory_gb // 4)))
    if fanless:
        n = min(n, 2)
    return n


def render_workers() -> int:
    """NOOKCUT_WORKERS wins; otherwise what this computer can take comfortably."""
    try:
        return max(1, int(os.environ["NOOKCUT_WORKERS"]))
    except (KeyError, ValueError):
        m = machine()
        return pick_workers(m["cpus"], m["memory_gb"], m["fanless"])


# ---------- ProRes for DaVinci Resolve ----------

# Apple's ProRes hardware first (10-bit 4:2:2), then ffmpeg's own encoder, which works everywhere.
PRORES = (("prores_videotoolbox", ["-profile:v", "proxy", "-pix_fmt", "p210le"], "Apple ProRes hardware"),
          ("prores_ks", ["-profile:v", "0", "-pix_fmt", "yuv422p10le"], "CPU"))


@functools.lru_cache(maxsize=1)
def pick_prores() -> tuple[str, list[str], str] | None:
    """(encoder, args, label) for ProRes 422 Proxy that really encodes here, or None."""
    for name, args, label in PRORES:
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", "color=c=gray:s=640x360:r=30:d=0.2",
               "-c:v", name, *args, "-f", "null", "-"]
        try:
            if subprocess.run(cmd, capture_output=True, timeout=30).returncode == 0:
                return name, args, label
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None
