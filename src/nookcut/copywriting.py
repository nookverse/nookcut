"""Built-in offline copy: captions, titles, descriptions and hashtags with no internet.

The words live in language packs (`copydata/<lang>.json`, English built in). A user can add or
override a pack in ~/.nookcut/copy/<lang>.json (or $NOOKCUT_COPY_DIR); `nookcut copy init <lang>`
starts one from the English pack. Packs may be partial: missing keys fall back to English.

Choices are seeded by the project (source + title), so re-planning gives the same words, and
`suggest()` lists the alternatives. Web research by the agent, when the user allows it, can replace
any of this; nothing here needs it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata

BUILTIN = os.path.join(os.path.dirname(__file__), "copydata")
SOURCE = "built-in library (offline)"
HASHTAGS_NOTE = "generic starter tags from the built-in library, not live trends"
LANG_NAMES = {"english": "en", "bengali": "bn", "bangla": "bn", "hindi": "hi", "spanish": "es", "español": "es",
              "french": "fr", "german": "de", "portuguese": "pt", "arabic": "ar", "urdu": "ur",
              "indonesian": "id", "japanese": "ja", "korean": "ko", "chinese": "zh", "russian": "ru"}
_SLOT = re.compile(r"\{(\w+)\}")
BOTTOM_EXTRA = 10  # the bottom caption line uses a smaller font, so it fits more characters


def user_dir() -> str:
    return os.path.expanduser(os.getenv("NOOKCUT_COPY_DIR") or "~/.nookcut/copy")


def language_code(lang: str) -> str:
    """'English', 'en-US', 'EN' -> 'en'. Unknown names pass through lowercased."""
    s = (lang or "en").strip().lower()
    return LANG_NAMES.get(s, re.split(r"[-_\s]", s)[0] or "en")


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _read(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load(lang: str = "en") -> tuple[dict, str, str]:
    """(pack, language code actually used, note for the user or '')."""
    code = language_code(lang)
    data = _read(os.path.join(BUILTIN, "en.json"))
    found = code == "en"
    for path in (os.path.join(BUILTIN, f"{code}.json") if code != "en" else "",
                 os.path.join(user_dir(), f"{code}.json")):
        if path and os.path.isfile(path):
            data, found = _merge(data, _read(path)), True
    if found:
        return data, code, ""
    return data, "en", (f"No '{code}' caption pack is installed, so the built-in copy is English. Translate it "
                        f"yourself, or the user can make a pack with `nookcut copy init {code}`.")


def init_pack(lang: str) -> str:
    """Write a starter pack (the English one) for the user to translate; never overwrites."""
    code = language_code(lang)
    os.makedirs(user_dir(), exist_ok=True)
    path = os.path.join(user_dir(), f"{code}.json")
    if os.path.exists(path):
        return path
    with open(os.path.join(BUILTIN, "en.json"), encoding="utf-8") as src, open(path, "w", encoding="utf-8") as dst:
        dst.write(src.read())
    return path


# ---------- filling patterns ----------

def wrap(text: str, width: int) -> str:
    if "|" in text or len(text) <= width:
        return text
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur); cur = w
        else:
            cur = f"{cur} {w}".strip()
    lines.append(cur)
    return "|".join(lines[:3])


def fill(pattern: str, slots: dict) -> str | None:
    """Fill {slot}s; {SLOT} gives the value in capitals. None when a slot is empty."""
    missing = False

    def sub(m):
        nonlocal missing
        name = m.group(1)
        value = slots.get(name.lower())
        if value in (None, ""):
            missing = True
            return ""
        return str(value).upper() if name.isupper() else str(value)
    out = _SLOT.sub(sub, pattern)
    return None if missing else out.strip()


def fit(text: str, width: int) -> str | None:
    """Keep caption lines within width (re-wrapping when needed); None if a word is too long."""
    if all(len(line) <= width for line in text.split("|")):
        return text
    w = wrap(" ".join(line.strip() for line in text.split("|")), width)
    return w if all(len(line) <= width for line in w.split("|")) else None


def options(data: dict, keys, slots: dict, width: int | None = None, section: str = "captions") -> list[str]:
    """Every usable option from the first key that has any (later keys are fallbacks)."""
    for key in keys:
        out = []
        for pattern in data.get(section, {}).get(key, []):
            text = fill(pattern, slots)
            if text is None:
                continue
            if section == "captions":
                text = text.upper()
                text = fit(text, width) if width else text
            if text and text not in out:
                out.append(text)
        if out:
            return out
    return []


def pick(choices: list[str], seed: str) -> str:
    if not choices:
        return ""
    return choices[int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(choices)]


# ---------- what goes where ----------

def caption_keys(kind: str, template: str, video_type: str, deaths: int = 0) -> tuple[tuple, tuple]:
    """(top keys, bottom keys) for a segment kind; empty for kinds whose captions are structural."""
    group = "comeback" if template in ("comeback", "victory") else "highlights"
    if template == "comeback":
        hook = ("hook.comeback",) if deaths >= 2 else ("hook.comeback.first",)
    elif template == "victory":
        hook = ("hook.victory",)
    else:
        hook = (f"hook.highlights.{video_type}", "hook.highlights.generic")
    return {
        "hook": (hook, ("hook.sub",)),
        "card": (("card.top",), ("card.bottom",)),
        "finisher": (("finisher.top",), ()),
        "outro": (("outro.top",), ()),
        "end": ((f"end.{group}.top", "end.highlights.top"), ("end.comeback.bottom",) if group == "comeback" else ()),
    }.get(kind, ((), ()))


def slots(brief, analysis: dict | None = None, edit: dict | None = None) -> dict:
    deaths = len(((analysis or {}).get("gameplay") or {}).get("deaths") or [])
    if not analysis and edit:
        deaths = sum(1 for s in edit.get("segments", []) if s["kind"] == "death")
    if brief.video_type != "gameplay":
        deaths = 0  # death detection only means something for games
    secs = ""
    if edit and edit.get("segments"):
        from .edit import total
        secs = round(total(edit))
    label = next((k.get("label", "") for k in brief.key_moments if k.get("label")), "")
    return {"title": brief.title.strip(), "game": brief.game.strip(), "n": deaths or "",
            "n1": deaths + 1 if deaths else "", "secs": secs, "label": label}


class Writer:
    """Default captions for one project, used by the story templates."""

    def __init__(self, brief, analysis: dict | None, width: int):
        self.data, self.language, self.note = load(brief.language)
        self.slots, self.width = slots(brief, analysis), width
        self.seed = f"{brief.source}|{brief.title}"

    def caption(self, keys, bottom: bool = False, **extra) -> str:
        width = self.width + BOTTOM_EXTRA if bottom else self.width
        found = options(self.data, keys, {**self.slots, **extra}, width)
        return pick(found, f"{self.seed}|{keys[0] if keys else ''}")


# ---------- publish copy ----------

def fold(text: str) -> str:
    """Drop accents from Latin letters (Ōkami -> Okami); other scripts are left as they are."""
    out = []
    for ch in text:
        base = unicodedata.normalize("NFKD", ch)
        out.append(base[0] if base[0].isascii() and base[0].isalpha() else ch)
    return "".join(out)


def tag(text: str) -> str:
    t = re.sub(r"[\W_]+", "", fold(text).lower())
    return f"#{t}" if len(t) >= 2 else ""


def hashtags(data: dict, brief, template: str) -> dict:
    h = data["hashtags"]
    topic = []
    if brief.game:
        g = tag(brief.game)
        topic += [g] + [g + s for s in h.get("game_suffix", [])] if g else []
    t = tag(brief.title)
    if t and len(t) <= 25:
        topic.append(t)
    base = topic + h["type"].get(brief.video_type, h["type"].get("generic", []))
    extra = h["template"].get(template, [])
    out = {}
    for p in brief.platforms:
        tags, seen = [], set()
        for x in topic + h["platform"].get(p, []) + base + extra:
            x = x.lower()
            if x and x not in seen:
                seen.add(x); tags.append(x)
        out[p] = tags[:6]
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(text).lower()).strip("-")[:80] or "nookcut"


def game_in_title(brief) -> bool:
    return bool(brief.game) and fold(brief.game).lower() in fold(brief.title).lower()


def file_name(brief, deaths: int) -> str:
    parts = [] if not brief.game or game_in_title(brief) else [brief.game]
    parts.append(brief.title)
    if deaths >= 2:
        parts.append(f"{deaths} deaths")
    return _slug(" ".join(parts))


def _sentence(caption: str) -> str:
    s = caption.replace("|", " ").strip().lower()
    return re.sub(r"(^|[.!?]\s+)(\w)", lambda m: m.group(1) + m.group(2).upper(), s)


def suggest(brief, analysis: dict | None, edit: dict, count: int = 5) -> dict:
    """Offline alternatives for every caption, plus titles, a description, hashtags and a file name."""
    data, code, note = load(brief.language)
    template = edit.get("template", "highlights")
    vt = edit.get("video_type") or brief.video_type
    width = 16 if edit.get("orientation") == "vertical" else 26
    s = slots(brief, analysis, edit)
    deaths = s["n"] or 0
    seed = f"{brief.source}|{brief.title}"
    count = max(1, min(int(count or 5), 10))

    caps = []
    for i, seg in enumerate(edit.get("segments", [])):
        top_keys, bottom_keys = caption_keys(seg["kind"], template, vt, deaths)
        if template == "comeback" and seg["kind"] == "highlight" and seg.get("bottom"):
            bottom_keys = ("highlight.first",)
        row = {}
        for field, keys in (("top", top_keys), ("bottom", bottom_keys)):
            w = width + BOTTOM_EXTRA if field == "bottom" else width
            alts = [o for o in options(data, keys, s, w) if o != seg.get(field, "")][:count - 1]
            if alts:
                row[f"{field}_options"] = alts
        if row:
            caps.append({"i": i, "kind": seg["kind"], "top": seg.get("top", ""), "bottom": seg.get("bottom", ""),
                         **row})

    group = template if template in ("comeback", "victory") else "highlights"
    if game_in_title(brief):
        s = {**s, "game": ""}  # "Ronin Okami duel | Ronin Okami" says the name twice
    tkeys = (template,) if group != "highlights" else (f"highlights.{vt}", "highlights.generic")
    titles = [t for t in options(data, tkeys, s, section="titles") if len(t) <= 100][:count]
    if not titles:
        titles = [brief.title]
    summary = pick(options(data, (group,), s, section="description"), seed + "|description")
    cta = pick(options(data, (group,), s, section="cta"), seed + "|cta")
    tags = hashtags(data, brief, template)
    hook = next((seg["top"] for seg in edit.get("segments", []) if seg["kind"] == "hook"), "")
    hook_line = "" if hook.replace("|", " ") == wrap(brief.title.upper(), width).replace("|", " ") else _sentence(hook)
    if hook_line and summary and hook_line.split()[0].lower() == summary.split()[0].lower():
        hook_line = ""  # "17 deaths. One win." then "17 deaths. Attempt #18." repeats itself
    first = next(iter(tags.values()), [])
    description = "\n\n".join(x for x in (hook_line, summary, cta, " ".join(first[:3])) if x)
    out = {"source": SOURCE, "language": code, "captions": caps, "titles": titles, "description": description,
           "hashtags": tags, "hashtags_note": HASHTAGS_NOTE, "file_name": file_name(brief, deaths)}
    if note:
        out["note"] = note
    return out
