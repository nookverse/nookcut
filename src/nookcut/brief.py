"""The creative brief: what the user wants, validated against platform limits."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, fields

VIDEO_TYPES = ("gameplay", "talking", "sports", "tutorial", "generic")
ORIENTATIONS = {"vertical": (1080, 1920), "square": (1080, 1080), "wide": (1920, 1080)}
MUSIC_MODES = ("ai", "none", "file")
SOURCE_AUDIO = ("keep", "duck", "mute")
SUBTITLES = ("auto", "on", "off")
HDR_MODES = ("sdr", "keep")
FPS_MODES = ("auto", "60", "30")

# Upload length caps in seconds for the short-form surfaces (check platform help pages; they change).
PLATFORM_MAX = {"youtube_shorts": 180, "tiktok": 600, "instagram_reels": 180,
                "facebook_reels": 90, "youtube": 43200, "x": 140}
# Forgiving inputs: what people (and small models) type -> the canonical value.
ALIASES = {
    "orientation": {"vertical": ("portrait", "9:16", "9x16", "tall", "shorts", "reels", "tiktok"),
                    "square": ("1:1", "1x1"), "wide": ("landscape", "horizontal", "16:9", "16x9", "widescreen")},
    "platform": {"youtube_shorts": ("shorts", "yt_shorts", "ytshorts", "youtubeshorts", "youtube_short"),
                 "tiktok": ("tik_tok", "tt"), "instagram_reels": ("instagram", "ig", "reels", "insta", "ig_reels"),
                 "facebook_reels": ("facebook", "fb", "fb_reels"), "youtube": ("yt", "youtube_long"),
                 "x": ("twitter",)},
    "video_type": {"gameplay": ("game", "gaming", "video_game", "games"),
                   "talking": ("talk", "talking_head", "podcast", "vlog", "interview", "speech"),
                   "sports": ("sport",), "tutorial": ("howto", "how_to", "guide", "lesson", "tutorials"),
                   "generic": ("other", "any", "general", "")},
    "music": {"ai": ("original", "generate", "generated", "yes", "true", "on", ""), "none": ("no", "off", "false"),
              "file": ("own", "mine", "my_file")},
    "source_audio": {"keep": ("on", "original", "full"), "duck": ("lower", "quiet", "background", ""),
                     "mute": ("off", "none", "silent", "no")},
    "hdr": {"sdr": ("", "off", "no", "false", "convert", "tonemap", "tone_map", "auto"),
            "keep": ("on", "yes", "true", "hdr", "hdr10", "keep_hdr", "10bit", "10_bit")},
    "fps": {"auto": ("", "source", "default", "match"), "60": ("60fps", "59.94", "smooth", "high"),
            "30": ("30fps", "29.97", "small", "low")},
    "subtitles": {"auto": ("", "default"), "on": ("yes", "true", "always", "speech", "captions", "required"),
                  "off": ("no", "false", "none", "never")},
}


def canonical(kind: str, value) -> str:
    """Map a loose value onto its canonical name; unknown values pass through for validate()."""
    if not isinstance(value, str):
        return value
    key = re.sub(r"[\s\-]+", "_", value.strip().lower())
    for name, alts in ALIASES[kind].items():
        if key == name or key in alts:
            return name
    return value.strip()


def seconds(value) -> float:
    """60, "60", "60s", "1m", "1:30", "1m30s", "90 sec" -> seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().lower()
    if ":" in s:
        parts = [float(p) for p in s.split(":")]
        return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))
    m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)\s*m(?:in(?:ute)?s?)?)?\s*(?:(\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds?)?)?", s)
    if not s or not m or not any(m.groups()):
        raise ValueError(f"max_duration {value!r} is not a length; use seconds like 60, '60s' or '1:00'")
    return float(m.group(1) or 0) * 60 + float(m.group(2) or 0)


PLATFORM_ORIENTATION = {"youtube_shorts": "vertical", "tiktok": "vertical", "instagram_reels": "vertical",
                        "facebook_reels": "vertical", "youtube": "wide"}


@dataclass
class Brief:
    title: str                                  # topic / what is in the video
    source: str                                 # path to the raw recording
    video_type: str = "gameplay"
    game: str = ""                              # game name when video_type == gameplay
    platforms: list[str] = field(default_factory=lambda: ["youtube_shorts", "tiktok", "instagram_reels"])
    orientation: str = "vertical"
    max_duration: float = 60.0
    region: str = ""                            # audience region, e.g. "US", "India", "Bangladesh"
    language: str = "en"
    music: str = "ai"                           # ai | none | file
    music_file: str = ""
    source_audio: str = "duck"                  # keep | duck | mute
    spoilers: bool = True
    key_moments: list[dict] = field(default_factory=list)  # [{"t": 642.7, "label": "final blow"}]
    online: bool = False                        # user allowed the agent to research on the web (opt-in)
    subtitles: str = "auto"                     # word-by-word speech captions: auto (if a model is installed) | on | off
    speech_language: str = "auto"               # spoken language for transcription ("auto" detects it)
    hdr: str = "sdr"                            # HDR sources: sdr (tone-mapped, plays everywhere) | keep (HDR10/HLG)
    fps: str = "auto"                           # output frame rate: auto (60, or 59.94 for NTSC sources) | 60 | 30

    def __post_init__(self):
        self.video_type = canonical("video_type", self.video_type)
        self.orientation = canonical("orientation", self.orientation)
        self.music = canonical("music", self.music)
        self.source_audio = canonical("source_audio", self.source_audio)
        if isinstance(self.subtitles, bool):
            self.subtitles = "on" if self.subtitles else "off"
        self.subtitles = canonical("subtitles", self.subtitles)
        if isinstance(self.hdr, bool):
            self.hdr = "keep" if self.hdr else "sdr"
        self.hdr = canonical("hdr", self.hdr)
        self.fps = canonical("fps", str(int(self.fps)) if isinstance(self.fps, (int, float)) else self.fps)
        if isinstance(self.platforms, str):
            self.platforms = self.platforms.split(",")
        self.platforms = list(dict.fromkeys(canonical("platform", p) for p in self.platforms if str(p).strip()))
        self.max_duration = seconds(self.max_duration)
        if isinstance(self.online, str):
            self.online = self.online.strip().lower() in ("1", "true", "yes", "on")

    def validate(self) -> list[str]:
        """Raise on hard errors, return soft warnings."""
        if self.video_type not in VIDEO_TYPES:
            raise ValueError(f"video_type {self.video_type!r} is unknown; use one of {', '.join(VIDEO_TYPES)}")
        if self.orientation not in ORIENTATIONS:
            raise ValueError(f"orientation {self.orientation!r} is unknown; use one of {', '.join(ORIENTATIONS)}")
        if self.music not in MUSIC_MODES:
            raise ValueError(f"music {self.music!r} is unknown; use one of {', '.join(MUSIC_MODES)}")
        if self.music == "file" and not self.music_file:
            raise ValueError("music='file' needs music_file")
        if self.source_audio not in SOURCE_AUDIO:
            raise ValueError(f"source_audio {self.source_audio!r} is unknown; use one of {', '.join(SOURCE_AUDIO)}")
        if self.subtitles not in SUBTITLES:
            raise ValueError(f"subtitles {self.subtitles!r} is unknown; use one of {', '.join(SUBTITLES)}")
        if self.hdr not in HDR_MODES:
            raise ValueError(f"hdr {self.hdr!r} is unknown; use one of {', '.join(HDR_MODES)}")
        if self.fps not in FPS_MODES:
            raise ValueError(f"fps {self.fps!r} is unknown; use one of {', '.join(FPS_MODES)}")
        if self.max_duration < 10:
            raise ValueError("max_duration must be at least 10 seconds")
        unknown = [p for p in self.platforms if p not in PLATFORM_MAX]
        if unknown:
            raise ValueError(f"platform {unknown[0]!r} is unknown; use any of {', '.join(PLATFORM_MAX)}")
        warnings = []
        for p in self.platforms:
            cap = PLATFORM_MAX.get(p)
            if cap and self.max_duration > cap:
                warnings.append(f"{p} allows up to {cap}s; max_duration is {self.max_duration:g}s")
            want = PLATFORM_ORIENTATION.get(p)
            if want and want != self.orientation:
                warnings.append(f"{p} performs best {want}; brief is {self.orientation}")
        if self.hdr == "keep":
            warnings.append("hdr: keep applies to HDR recordings only (SDR ones stay SDR) and makes HEVC "
                            "HDR video; YouTube shows it as HDR, other apps may convert it to SDR themselves")
        if self.video_type == "gameplay" and not self.game:
            warnings.append("game name is empty; research and captions will be weaker")
        return warnings

    @property
    def size(self) -> tuple[int, int]:
        return ORIENTATIONS[self.orientation]

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: str) -> "Brief":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})
