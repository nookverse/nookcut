"""Story templates: turn a brief + analysis into an edit.

  comeback    repeated deaths, then the win   (hook -> intro -> death montage -> attempt card
              -> best moments of the winning run -> slow-mo finisher -> outro -> question)
  victory     a win without detected deaths   (hook -> best moments -> finisher -> outro)
  highlights  anything else                   (hook -> top moments in order -> question)
              talking/tutorial videos with a transcript pick whole sentences instead of fixed windows
  tighten     the whole recording minus its pauses (jump cuts; ask for it, never picked automatically)

Captions come from the offline copy library (copywriting.py) in the brief's language. The agent
may pick other options (suggest_copy) or, when the user allows web research, rewrite them.
"""
from __future__ import annotations

import re
from bisect import bisect_left

from .analyze import best_windows, peak_times
from .brief import Brief
from .copywriting import Writer, caption_keys, wrap
from .edit import seg, total
from .transcribe import caption_words, sentences, snap

__all__ = ["add_speech", "choose_template", "plan", "wrap"]


def choose_template(brief: Brief, analysis: dict) -> str:
    g = analysis["gameplay"]
    # A fade is only trusted as the win when a death pattern precedes it or the user marked it;
    # otherwise (e.g. battle royale "spectating" fades) fall back to highlights.
    if brief.video_type == "gameplay" and g["win"]:
        if len(g["deaths"]) >= 2:
            return "comeback"
        if g["win"].get("by") == "user":
            return "victory"
    return "highlights"


def _base(brief: Brief, analysis: dict, template: str) -> dict:
    w, h = brief.size
    info = analysis["signals"]["info"]
    return {"source": brief.source, "title": brief.title, "game": brief.game, "template": template,
            "orientation": brief.orientation, "width": w, "height": h, "fps": 60,
            "language": brief.language, "music": brief.music, "music_file": brief.music_file,
            "video_type": brief.video_type,
            "music_style": "taiko" if brief.video_type == "gameplay" else "pulse",
            "source_audio": brief.source_audio if analysis["signals"]["loudness"] else "mute",
            "source_timecode": info["timecode"], "source_fps": info["fps"],
            "source_duration": info["duration"], "segments": []}


def _highlights(analysis, lo, hi, count, length=3.0):
    scores, rate = analysis["scores"], analysis["signals"]["rate"]
    out = []
    for a, b in best_windows(scores, rate, lo, hi, length, count):
        out.append(seg(a, b, "highlight", hits=peak_times(scores, rate, a, b, 2)))
    return out


def comeback(brief: Brief, analysis: dict, template="comeback") -> dict:
    g, cw = analysis["gameplay"], 16 if brief.orientation == "vertical" else 26
    edit = _base(brief, analysis, template)
    win, fa = g["win"]["onset"], g["final_attempt"]
    deaths = g["deaths"]
    n = len(deaths)
    dur = analysis["signals"]["info"]["duration"]
    fin = (win - 1.6, win)
    impact = round(win - 0.8, 2)
    w = Writer(brief, analysis, cw)

    def text(kind, which, **extra):
        return w.caption(caption_keys(kind, template, brief.video_type, n)[which], bottom=which == 1, **extra)

    hook = seg(*fin, "hook", 0.5, text("hook", 0), text("hook", 1), 1.25, impact=impact)
    intro = None
    first_play = deaths[0]["onset"] if deaths else win
    if g["intro_cut"] and g["intro_cut"]["end"] + 4 < min(first_play, win - 10):
        a = g["intro_cut"]["end"] + 1.0
        intro = seg(a, a + 3.0, "intro", top=wrap(brief.title.upper(), cw), bottom=brief.game.upper())

    shown = deaths
    if n > 8:
        idx = sorted({round(i * (n - 1) / 7) for i in range(8)})
        shown = [deaths[i] for i in idx]
    montage = []
    for d in shown:
        i = deaths.index(d) + 1
        length = 1.8 if i == n else 1.3
        montage.append(seg(d["onset"] - length - 0.3, d["onset"] - 0.3, "death",
                           top=w.caption(("death.top",), i=i), bottom=w.caption(("death.bottom",), True, i=i)))
    card = seg(fa["start"], fa["start"] + 1.1, "card", top=text("card", 0),
               bottom=text("card", 1)) if n else None
    finisher = seg(*fin, "finisher", 0.4, text("finisher", 0), "", 1.25, impact=impact)
    after = g["win"]["end"]
    outro = seg(after + 1.0, min(after + 5.0, dur - 0.1), "outro", top=text("outro", 0))
    later = [e for e in analysis["events"] if e["onset"] > after + 8]
    end_at = later[-1]["onset"] if later else min(after + 9.0, dur - 0.1)
    end = seg(end_at - 4.0, end_at - 1.0, "end", top=text("end", 0), bottom=text("end", 1))

    fixed = [x for x in (hook, intro, *montage, card, finisher, outro, end) if x]

    def assemble(hl):
        return [x for x in (hook, intro, *montage, card, *hl, finisher, outro, end) if x]

    budget = brief.max_duration
    lo, hi = fa["start"] + 1.2, win - 1.6
    count = max(2, min(12, int((budget - sum((x["src_out"] - x["src_in"]) / x["speed"] for x in fixed)) // 3)))
    while True:
        hl = _highlights(analysis, lo, hi, count)
        edit["segments"] = assemble(hl)
        if total(edit) <= budget:
            break
        if count > 2:
            count -= 1
        elif intro:
            intro = None
        elif end and end in fixed:
            fixed.remove(end); end = None
        elif len(montage) > 3:
            montage = montage[:1] + montage[-2:]
        else:
            break
    if hl:
        hl[0]["bottom"] = w.caption(("highlight.first",), True) if template == "comeback" else ""
    return edit


def highlights(brief: Brief, analysis: dict) -> dict:
    edit = _base(brief, analysis, "highlights")
    cw = 16 if brief.orientation == "vertical" else 26
    scores, rate = analysis["scores"], analysis["signals"]["rate"]
    dur = analysis["signals"]["info"]["duration"]
    best = peak_times(scores, rate, 1, dur - 1, 1)[0]
    w = Writer(brief, analysis, cw)
    top_keys, bottom_keys = caption_keys("hook", "highlights", brief.video_type)
    hook = seg(max(0, best - 1.2), best + 0.4, "hook", 0.5, w.caption(top_keys) or wrap(brief.title.upper(), cw),
               w.caption(bottom_keys, True), 1.2, impact=best)
    count = max(3, int((brief.max_duration - 3.2 - 3) // 3))
    while True:
        hl = _highlights(analysis, 0.5, dur - 0.5, count)
        last = hl[-1] if hl else hook
        end = seg(last["src_out"], min(last["src_out"] + 3, dur), "end",
                  top=w.caption(caption_keys("end", "highlights", brief.video_type)[0]))
        edit["segments"] = [hook, *hl, end]
        if total(edit) <= brief.max_duration or count <= 3:
            break
        count -= 1
    for k, km in enumerate(brief.key_moments):     # user-marked moments become captions
        for s in edit["segments"]:
            if s["src_in"] <= km["t"] < s["src_out"] and km.get("label"):
                s["top"] = wrap(km["label"].upper(), cw)
    return edit


SPEECH_TYPES = ("talking", "tutorial")
# a sign-off ("let me know in the comments", "subscribe") makes a poor hook and a good last line
_CTA = re.compile(r"\b(comment|comments|subscribe|follow|like and|link in|let me know|see you|thanks for)\b", re.I)


def _sentence_spans(words: list[list], end: float) -> list[tuple[float, float, str]]:
    """Sentences as cut ranges: a little air before, a short tail after, never into a neighbour."""
    sents = sentences(words, gap=0.6)
    out = []
    for k, s in enumerate(sents):
        prev_end = sents[k - 1][-1][1] if k else 0.0
        next_start = sents[k + 1][0][0] if k + 1 < len(sents) else end
        a = max(prev_end, s[0][0] - 0.15, 0.0)
        b = min(next_start, s[-1][1] + 0.3, end)
        out.append((round(a, 3), round(b, 3), " ".join(w[2] for w in s)))
    return out


def speech_highlights(brief: Brief, analysis: dict, words: list[list]) -> dict:
    """Talking/tutorial videos: whole sentences, never cut mid-word. Hook = the liveliest short
    sentence; then the liveliest sentences that fit the length, in their original order; the
    last one carries the comment question."""
    edit = _base(brief, analysis, "highlights")
    edit["cut_by"] = "sentences"
    cw = 16 if brief.orientation == "vertical" else 26
    scores, rate = analysis["scores"], analysis["signals"]["rate"]
    dur = analysis["signals"]["info"]["duration"]
    w = Writer(brief, analysis, cw)

    def score(a, b):
        part = scores[int(a * rate):max(int(a * rate) + 1, int(b * rate))]
        return sum(part) / len(part) if part else 0.0

    spans = [x for x in _sentence_spans(words, dur) if 1.0 <= x[1] - x[0] <= 12.0]
    if not spans:
        return highlights(brief, analysis)
    top = max(score(a, b) for a, b, _ in spans) or 1.0

    def rank(x):  # liveliness, nudged up by questions/exclamations and numbers
        return score(x[0], x[1]) + 0.1 * top * (bool(re.search(r"[?!]", x[2])) + bool(re.search(r"\d", x[2])))

    ranked = sorted(spans, key=rank, reverse=True)
    pool = [x for x in ranked if not _CTA.search(x[2])] or ranked
    hook_span = next((x for x in pool if x[1] - x[0] <= 4.5), pool[0])
    sign_off = next((x for x in reversed(spans[-3:]) if _CTA.search(x[2]) and x is not hook_span), None)
    top_keys, bottom_keys = caption_keys("hook", "highlights", brief.video_type)
    hook = seg(hook_span[0], hook_span[1], "hook", 1.0, w.caption(top_keys) or wrap(brief.title.upper(), cw),
               w.caption(bottom_keys, True))
    budget = brief.max_duration - (hook["src_out"] - hook["src_in"])
    chosen, used = [], 0.0
    if sign_off and sign_off[1] - sign_off[0] <= budget / 3:
        chosen, used = [sign_off], sign_off[1] - sign_off[0]
    for x in ranked:
        if x is hook_span or x in chosen or _CTA.search(x[2]) or used + (x[1] - x[0]) > budget:
            continue
        chosen.append(x)
        used += x[1] - x[0]
    chosen.sort()
    body = [seg(a, b, "highlight") for a, b, _ in chosen]
    if body:
        body[-1]["kind"] = "end"
        body[-1]["top"] = w.caption(caption_keys("end", "highlights", brief.video_type)[0])
    edit["segments"] = [hook, *body]
    return edit


TEMPLATES = ("comeback", "victory", "highlights", "tighten")


def tighten(brief: Brief, analysis: dict, words: list[list] | None = None) -> dict:
    """Keep everything that is said and cut out the pauses between, at normal speed. Cut points
    come from the analysis' voice regions (the audio energy, which is more exact here than word
    times). The length budget isn't applied: the result is as long as the talking is."""
    voice = analysis.get("voice")
    if not voice:
        raise ValueError("there are no pauses to cut: the recording is silent or has non-stop sound "
                         "(music or game audio) under the voice; use the 'highlights' template")
    edit = _base(brief, analysis, "tighten")
    edit["cut_by"] = "pauses"
    if edit["music"] == "ai":
        edit["music"] = "none"  # a score under every jump cut would stutter; the voice carries it
    dur = analysis["signals"]["info"]["duration"]
    if words:  # with a transcript, a stretch without words is noise (game sounds, music): cut it too
        mids = sorted((w[0] + w[1]) / 2 for w in words)
        voice = [(a, b) for a, b in voice if any(a <= m < b for m in mids[bisect_left(mids, a):][:1])]
    segs = []
    for a, b in voice:
        if b - a >= 0.25:
            segs.append(seg(a, min(b, dur), "highlight"))
    edit["segments"] = segs
    edit["removed_s"] = round(dur - total(edit), 1)
    return edit


def plan(brief: Brief, analysis: dict, template: str | None = None, words: list[list] | None = None) -> dict:
    """words: the transcript (optional); talking/tutorial videos are then cut on sentences."""
    if template and template not in TEMPLATES:
        raise ValueError(f"template {template!r} is unknown; use one of {', '.join(TEMPLATES)}")
    if template == "tighten":
        return tighten(brief, analysis, words)
    if (words and len(words) >= 12 and brief.video_type in SPEECH_TYPES and template in (None, "highlights")):
        return speech_highlights(brief, analysis, words)
    template = template or choose_template(brief, analysis)
    if template in ("comeback", "victory"):
        if not analysis["gameplay"]["win"]:
            raise ValueError("no win detected; pass a key moment like {'t': 642.7, 'label': 'final blow'} "
                             "or use the 'highlights' template")
        return comeback(brief, analysis, template)
    return highlights(brief, analysis)


# cuts that must stay on the frame they were picked for (a death, the attempt card)
_FIXED_CUTS = ("death", "card")


def add_speech(edit: dict, words: list[list], brief: Brief) -> dict:
    """Attach each segment's spoken words (for word-by-word captions) and move cut points out of
    the middle of words (talking and tutorial videos allow a longer stretch to finish a word). The
    length budget still holds: cuts are trimmed instead of extended when it would run over."""
    edit["subtitles"] = bool(words)
    if not words:
        return edit
    end = edit.get("source_duration") or words[-1][1] + 1
    tol = 1.5 if brief.video_type in ("talking", "tutorial") else 0.6
    movable = [s for s in edit["segments"] if s["kind"] not in _FIXED_CUTS and edit["template"] != "tighten"]
    original = [(s["src_in"], s["src_out"]) for s in movable]

    def place(s, cut, tolerance):
        a, b = snap(*cut, words, tolerance, end)
        s["src_in"], s["src_out"] = (a, b) if b - a >= 0.5 else cut

    for s, cut in zip(movable, original):
        place(s, cut, tol)
    for s, cut in reversed(list(zip(movable, original))):
        if edit["template"] == "tighten" or total(edit) <= brief.max_duration:
            break
        place(s, cut, 0.0)  # over budget: drop partial words instead of finishing them
    if edit["template"] == "tighten":
        _assign_words(edit["segments"], words)
    else:
        for s in edit["segments"]:
            s["words"] = caption_words(words, s["src_in"], s["src_out"])
    return edit


def _assign_words(segs: list[dict], words: list[list]) -> None:
    """Jump cuts follow the audio energy, which can end a moment before whisper's word times do:
    give every word to its nearest segment and clamp it inside, so no word goes missing."""
    for s in segs:
        s["words"] = []
    if not segs:
        return
    for a, b, text in words:
        mid = (a + b) / 2
        s = min(segs, key=lambda s: 0 if s["src_in"] <= mid < s["src_out"]
                else min(abs(mid - s["src_in"]), abs(mid - s["src_out"])))
        if min(abs(mid - s["src_in"]), abs(mid - s["src_out"])) > 1.0 and not s["src_in"] <= mid < s["src_out"]:
            continue  # spoken in a removed stretch (too quiet to keep): nothing to caption
        lo, hi = s["src_in"], s["src_out"]
        na, nb = min(max(a, lo), hi - 0.15), min(max(b, lo + 0.15), hi)
        s["words"].append([round(na, 3), round(max(nb, na + 0.1), 3), text])
