"""Speech captions: word-by-word .ass files for the render, and an .srt for editors and platforms.

Two styles:
  highlight  the active word is highlighted (yellow, slight pop) inside a short phrase of up to three
             words
  streamer   cards of one or two words, switched with hard cuts as they are said (the way stream
             clips are captioned); shouted or sworn words turn red and drop lower on the screen
Both are drawn by libass (ffmpeg's `ass` filter). Without libass the phrase is drawn with drawtext
instead, without the highlight. Swear words can be censored (F*CK) in either style.
The context label (a short yellow note in the first second) is drawn the same way.
"""
from __future__ import annotations

import os
import re
import struct

from .transcribe import phrases

HIGHLIGHT_RGB = "0xFFD400"  # the spoken word (ASS writes colours as &HAABBGGRR)
HOLD = 0.35                 # keep a phrase up across gaps shorter than this (no flicker)
EMPHASIS_RGB = "0xFF2A2A"   # streamer style: shouted or sworn words
LABEL_RGB = "0xFFD400"      # the context label
CARD_WORDS, CARD_CHARS = 2, 12   # streamer style: at most this many words and letters on screen
# English swear words (other languages pass through; an agent can censor with update_edit words)
SWEAR = re.compile(r"^(mother|cluster|dumb|bull|horse)?(f+u+c+k+(?:s|ed|er|ers|ing|in|face|head)?|sh+i+t+(?:s|ty|ting|head)?|"
                   r"bitch(?:es|ing|y)?|cunts?|dick(?:s|head)?|bastards?|assholes?|pussy|pussies|whores?)$", re.I)


def is_swear(word: str) -> bool:
    return bool(SWEAR.match(re.sub(r"[^\w]", "", word)))


def censor(word: str) -> str:
    """F*CKING, SH*T, MOTHERF*CKER: the first vowel of the swear itself becomes *."""
    letters = re.sub(r"[^\w]", "", word)
    m = SWEAR.match(letters)
    if not m:
        return word
    root = len(m.group(1) or "")   # letters before the swear (mother-, bull-)
    seen = 0
    for k, ch in enumerate(word):
        if ch.isalnum() or ch == "_":
            if seen > root and ch.lower() in "aeiou":
                return word[:k] + "*" + word[k + 1:]
            seen += 1
    return word


def font_family(path: str) -> tuple[str, bool]:
    """(family name, is bold) from a .ttf/.otf name table; falls back to the file name."""
    fallback = (re.sub(r"[-_ ]?(Bold|Regular|bd)$", "", os.path.splitext(os.path.basename(path))[0], flags=re.I),
                "bold" in path.lower())
    try:
        with open(path, "rb") as f:
            data = f.read()
        n = struct.unpack(">H", data[4:6])[0]
        for i in range(n):
            tag, _, off, _ = struct.unpack(">4sIII", data[12 + 16 * i:28 + 16 * i])
            if tag != b"name":
                continue
            _, count, strings = struct.unpack(">HHH", data[off:off + 6])
            names = {}
            for r in range(count):
                pid, eid, _, nid, length, soff = struct.unpack(">HHHHHH", data[off + 6 + 12 * r:off + 18 + 12 * r])
                if nid in (1, 2) and (pid, nid) not in names:
                    raw = data[off + strings + soff:off + strings + soff + length]
                    names[(pid, nid)] = raw.decode("utf-16-be" if pid in (0, 3) else "mac_roman", "replace")
            family = names.get((3, 1)) or names.get((1, 1)) or names.get((0, 1))
            sub = names.get((3, 2)) or names.get((1, 2)) or ""
            if family:
                return family, "bold" in sub.lower()
    except (OSError, struct.error, IndexError):
        pass
    return fallback


def _clean(word: str, hide_swears: bool = False) -> str:
    w = re.sub(r"[,.;:…]+$", "", word.strip()).upper()
    if hide_swears:
        w = censor(w)
    return w.replace("\\", "/").replace("{", "(").replace("}", ")")


def _ts(t: float) -> str:
    cs = max(0, round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def local_words(s: dict) -> list[list]:
    """A segment's words in its own output time (after speed), clipped to the segment."""
    dur = (s["src_out"] - s["src_in"]) / s["speed"]
    out = []
    for a, b, w in s.get("words") or []:
        la, lb = (a - s["src_in"]) / s["speed"], (b - s["src_in"]) / s["speed"]
        la, lb = max(0.0, la), min(dur, lb)
        if lb - la > 0.02 and _clean(w):
            out.append([round(la, 3), round(lb, 3), w])
    return out


def timed_phrases(words: list[list], dur: float, max_chars: int, max_words: int = 3) -> list[tuple]:
    """[(start, end, [words])]: each phrase stays up until the next one when the gap is short."""
    groups = phrases(words, max_chars, max_words)
    out = []
    for k, g in enumerate(groups):
        a, b = g[0][0], g[-1][1]
        nxt = groups[k + 1][0][0] if k + 1 < len(groups) else dur
        if nxt - b < HOLD:
            b = nxt
        out.append((a, min(dur, max(b, a + 0.3)), g))
    return out


def _ass_colour(hex_rgb: str) -> str:
    """0xRRGGBB -> ASS &H00BBGGRR."""
    v = hex_rgb.removeprefix("0x")
    return f"&H00{v[4:6]}{v[2:4]}{v[0:2]}"


def cards(words: list[list], dur: float, emphasis=()) -> list[tuple]:
    """Streamer style: [(start, end, [words], emphasised)], one or two words a card, each up until
    the next one starts (a short gap holds it). An emphasised word gets a card of its own."""
    groups, cur = [], []
    for w in words:
        loud = w[0] in emphasis or is_swear(w[2])
        if cur and (loud or cur[-1][3] or len(cur) >= CARD_WORDS or w[0] - cur[-1][1] > 0.3
                    or len(" ".join(x[2] for x in cur + [w])) > CARD_CHARS or cur[-1][2].endswith(_ENDS)):
            groups.append(cur)
            cur = []
        cur.append([*w[:3], loud])
    if cur:
        groups.append(cur)
    out = []
    for k, g in enumerate(groups):
        a, b = g[0][0], g[-1][1]
        nxt = groups[k + 1][0][0] if k + 1 < len(groups) else dur
        if nxt - b < HOLD:
            b = nxt
        out.append((a, min(dur, max(b, a + 0.2)), [x[:3] for x in g], any(x[3] for x in g)))
    return out


_ENDS = (".", "?", "!", "…", ",")


def _header(family, bold, width, height, styles) -> list[str]:
    return ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
            "WrapStyle: 2", "ScaledBorderAndShadow: yes",
            "YCbCr Matrix: None",  # no BT.601 colour correction: colours land as written, in SDR and HDR
            "", "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding",
            *styles, "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]


BOX_ALPHA = "80"   # the translucent slab behind captions: ASS alpha 00 = solid, FF = clear (80 ~ half)


def _style(name, family, size, colour, bold, boxed: bool = False) -> str:
    """boxed: a translucent dark slab behind each line (ASS opaque box, BorderStyle 3) instead of an
    outline; the slab's padding is the 'outline' width."""
    if boxed:
        return (f"Style: {name},{family},{size},{colour},{colour},&H{BOX_ALPHA}000000,&H{BOX_ALPHA}000000,"
                f"{-1 if bold else 0},0,0,0,100,100,1,0,3,{max(8, size // 7)},0,8,40,40,0,1")
    return (f"Style: {name},{family},{size},{colour},{colour},&H00000000,&H90000000,{-1 if bold else 0},"
            f"0,0,0,100,100,1,0,1,{max(3, size // 12)},{max(2, size // 24)},8,40,40,0,1")


def label_events(text: str, width: int, height: int, x: int, y: int, end: float) -> list[str]:
    """The context label: pops in, wobbles a little, then swipes down and away (blurred) by `end`."""
    end = max(0.6, end)
    out_at = int((end - 0.18) * 1000)
    wobble = "".join(f"\\t({t},{t + 180},\\frz{z})" for t, z in ((120, 2.5), (300, 5), (480, 3), (660, 5.5), (840, 3.5))
                     if t + 180 < out_at)
    body = "\\N".join(_clean(part) for part in text.split("|") if part.strip())
    return [f"Dialogue: 1,{_ts(0.05)},{_ts(end)},Label,,0,0,0,,"
            f"{{\\an5\\move({x},{y},{x},{y + height // 6},{out_at},{int(end * 1000)})\\frz4"
            f"\\fscx135\\fscy135\\t(0,110,\\fscx100\\fscy100){wobble}"
            f"\\t({out_at},{int(end * 1000)},\\fscy190\\blur8\\alpha&HFF&)}}{body}"]


def ass(words: list[list], dur: float, width: int, height: int, y: int, size: int, font: str,
        max_chars: int, colour=None, style: str = "highlight", emphasis=(), emphasis_y: int | None = None,
        hide_swears: bool = False, label: dict | None = None, boxed: bool = False) -> str:
    """An .ass script for one segment: phrase by phrase, the spoken word highlighted (or, in the
    streamer style, word cards with emphasis), plus the context label when `label` is given
    ({"text", "x", "y", "size", "end"}). y is the middle of the caption line. boxed puts a translucent
    slab behind each caption. colour(0xRRGGBB) -> 0xRRGGBB adapts the text colours to the output (HDR:
    paper white)."""
    family, bold = font_family(font)
    colour = colour or (lambda c: c)
    white, highlight = _ass_colour(colour("0xFFFFFF")), _ass_colour(colour(HIGHLIGHT_RGB)) + "&"
    styles = [_style("Speech", family, size, white, bold, boxed)]
    if boxed and style != "streamer":
        # highlight style: one slab per phrase underneath (invisible text), the coloured words on top
        styles.append(_style("Slab", family, size, "&HFF000000", bold, True))
        styles.append(f"Style: Words,{family},{size},{white},{white},&H40000000,&H00000000,{-1 if bold else 0},"
                      f"0,0,0,100,100,1,0,1,2,0,8,40,40,0,1")
    if style == "streamer":
        styles.append(_style("Loud", family, int(size * 1.15), _ass_colour(colour(EMPHASIS_RGB)), bold, boxed))
    if label:
        styles.append(_style("Label", family, label["size"], _ass_colour(colour(LABEL_RGB)), bold))
    lines = _header(family, bold, width, height, styles)
    if label:
        lines += label_events(label["text"], width, height, label["x"], label["y"], label["end"])
    if style == "streamer":
        for a, b, g, loud in cards(words, dur, set(emphasis)):
            text = " ".join(_clean(x[2], hide_swears) for x in g)
            at = f"{{\\an5\\pos({width // 2},{emphasis_y if loud and emphasis_y else y})}}"
            lines.append(f"Dialogue: 0,{_ts(a)},{_ts(b)},{'Loud' if loud else 'Speech'},,0,0,0,,{at}{text}")
        return "\n".join(lines) + "\n"
    pos = f"{{\\an5\\pos({width // 2},{y})}}"
    pop = "" if boxed else "\\fscx108\\fscy108\\t(0,90,\\fscx100\\fscy100)"   # a pop would break the slab
    for a, b, g in timed_phrases(words, dur, max_chars):
        for k, w in enumerate(g):
            start = a if k == 0 else w[0]
            end = g[k + 1][0] if k + 1 < len(g) else b
            if end - start < 0.01:
                continue
            text = " ".join(
                f"{{\\c{highlight}{pop}}}{_clean(x[2], hide_swears)}{{\\r}}"
                if j == k else _clean(x[2], hide_swears) for j, x in enumerate(g))
            if boxed:
                if k == 0:
                    plain = " ".join(_clean(x[2], hide_swears) for x in g)
                    lines.append(f"Dialogue: 0,{_ts(a)},{_ts(b)},Slab,,0,0,0,,{pos}{plain}")
                lines.append(f"Dialogue: 1,{_ts(start)},{_ts(end)},Words,,0,0,0,,{pos}{text}")
            else:
                lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Speech,,0,0,0,,{pos}{text}")
    return "\n".join(lines) + "\n"


def plain(words: list[list], dur: float, max_chars: int) -> list[tuple[float, float, str]]:
    """(start, end, text) per phrase, for the drawtext fallback."""
    return [(a, b, " ".join(_clean(w[2]) for w in g)) for a, b, g in timed_phrases(words, dur, max_chars)]


def srt(edit: dict) -> str:
    """Speech captions for the whole edit, in output time (YouTube, TikTok and Resolve import .srt)."""
    from .edit import timeline
    cues = []
    for start, dur, s in timeline(edit):
        for a, b, g in timed_phrases(local_words(s), dur, 42, 8):
            cues.append((start + a, start + b, " ".join(re.sub(r"\s+", " ", w[2]) for w in g)))

    def tc(t):
        ms = max(0, round(t * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    return "".join(f"{n}\n{tc(a)} --> {tc(b)}\n{t}\n\n" for n, (a, b, t) in enumerate(cues, 1))
