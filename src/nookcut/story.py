"""Story templates: turn a brief + analysis into an edit.

  comeback    repeated deaths, then the win   (hook -> intro -> death montage -> attempt card
              -> best moments of the winning run -> slow-mo finisher -> outro -> question)
  victory     a win without detected deaths   (hook -> best moments -> finisher -> outro)
  highlights  anything else                   (hook -> top moments in order -> question)

Captions come from the offline copy library (copywriting.py) in the brief's language. The agent
may pick other options (suggest_copy) or, when the user allows web research, rewrite them.
"""
from __future__ import annotations

from .analyze import best_windows, peak_times
from .brief import Brief
from .copywriting import Writer, caption_keys, wrap
from .edit import seg, total

__all__ = ["choose_template", "plan", "wrap"]


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


def plan(brief: Brief, analysis: dict, template: str | None = None) -> dict:
    template = template or choose_template(brief, analysis)
    if template in ("comeback", "victory"):
        if not analysis["gameplay"]["win"]:
            raise ValueError("no win detected; pass a key moment like {'t': 642.7, 'label': 'final blow'} "
                             "or use the 'highlights' template")
        return comeback(brief, analysis, template)
    return highlights(brief, analysis)
