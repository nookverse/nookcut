"""Project folder layout and the high-level steps shared by the CLI and the MCP server.

<project>/
  brief.json        what the user wants
  analysis.json     signals + detected events
  transcript.json   word-level speech (when a whisper model is installed; optional)
  edit.json         the edit (agent may rewrite captions/segments here)
  preview.png       contact sheet for approval
  music.wav         original score (or the user's file, mastered)
  <name>.mp4        the render
  resolve/          EDLs + Lua builder for DaVinci Resolve
  publish_kit.md    titles, hashtags, posting plan
  check.jpg         one frame per segment of the last render (verify)
  frames.jpg        the last frame sheet asked for (get_frames)

make_all() runs every step as one job (CLI `nookcut make`, MCP `auto_edit`), fully offline.
"""
from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager

from . import analyze as an
from . import copywriting as cw
from . import edit as ed
from .brief import Brief
from .export import resolve_kit
from .music import compose
from . import broll as br
from . import qc
from . import voice as vc
from .publish import write_kit
from .render import preview, render
from .story import add_speech, auto_focus, clear_of_cuts, crop_bars, mark_emphasis, plan
from .preflight import check, speech_status
from .errors import Cancelled, EnvironmentNotReady, InputError, as_nookcut
from .models import whisper_model
from .transcribe import caption_words, envelope, sentences, speech_regions, transcribe
from .tasks import Task, project_lock, run


@contextmanager
def job(project: str, name: str, task: Task | None = None):
    """One heavy step on a project: holds the project lock, publishes progress, and turns any failure
    into a NookCutError with a readable message. Ctrl+C ends the step's ffmpeg processes."""
    task = task or Task(name, project)
    task.name, task.project = task.name or name, task.project or project
    with project_lock(project, name):  # Busy goes straight to the caller; the running job is untouched
        try:
            yield task
        except (KeyboardInterrupt, SystemExit):  # Ctrl+C, SIGTERM/SIGHUP
            task.cancel()
            task.finish()
            raise Cancelled() from None
        except BaseException as e:
            err = as_nookcut(e)
            task.finish(None if task.cancelled else err.to_dict())
            if err is e:
                raise
            raise err from e
        else:
            task.finish()


def _need(path: str, step: str, what: str) -> None:
    if not os.path.exists(path):
        raise InputError(f"There is no {what} in this project yet.", f"Run {step} first.")


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")[:80] or "nookcut"


def paths(project: str) -> dict:
    return {k: os.path.join(project, v) for k, v in {
        "brief": "brief.json", "analysis": "analysis.json", "edit": "edit.json", "preview": "preview.png",
        "music": "music.wav", "resolve": "resolve", "kit": "publish_kit.md",
        "transcript": "transcript.json"}.items()}


def set_framing(project: str, framing: str) -> dict:
    """The user's choice of framing (band | split), saved in the brief (so a new plan keeps it) and in
    the current edit, if there is one. Cuts, captions and music are untouched; render again to see it."""
    from .brief import FRAMINGS, REMOVED_FRAMINGS, canonical
    value = canonical("framing", framing)
    if value in REMOVED_FRAMINGS or str(value).replace(" ", "_") in REMOVED_FRAMINGS:
        raise InputError("Full-bleed framing isn't available; NookCut makes one vertical video.",
                         "Use band (a sharp band over a blurred copy) or split (facecam or minimap on top).")
    if value not in FRAMINGS:
        raise InputError(f"'{framing}' isn't a framing NookCut knows.",
                         "Use band (a sharp band over a blurred copy) or split (facecam or minimap on top).")
    p = paths(project)
    _need(p["brief"], "create_project (nookcut new)", "brief")
    brief = Brief.load(p["brief"])
    if value == "split" and brief.orientation != "vertical":
        raise InputError("Split framing (facecam or minimap on top) is for vertical videos.",
                         "Use band or full for square and wide videos.")
    brief.framing = value
    brief.save(p["brief"])
    out = {"framing": value}
    if os.path.exists(p["edit"]):
        edit = ed.load(p["edit"])
        edit["framing"] = value
        if value == "split" and not edit.get("panel"):
            from .story import panel_of
            edit["panel"] = panel_of(brief, _analysis(project))
        ed.save(edit, p["edit"])
        out = describe(edit)
    out["next"] = ("check each shot's aim on preview_plan (shots are aimed at the action automatically; fix with "
                   "update_edit focus)" + (" and the top panel (magenta)" if value == "split" else "") + ", then render again"
                   if value == "split" else "render again to see it")
    return out


def new_project(project: str, brief: Brief) -> dict:
    os.makedirs(project, exist_ok=True)
    warnings = brief.validate()
    brief.save(paths(project)["brief"])
    return {"project": project, "warnings": warnings}


def _not_script(project: str) -> None:
    """Script projects have no recording to scan or plan: refuse instead of replacing the script."""
    if os.path.exists(paths(project)["brief"]) and Brief.load(paths(project)["brief"]).made_from == "script":
        raise InputError("This project was made from a script, so there's no recording to analyse or plan.",
                         "Change it with update_edit (B-roll, captions), or run script_to_video again with the "
                         "new script.")


def run_analysis(project: str, task: Task | None = None) -> dict:
    _need(paths(project)["brief"], "create_project (nookcut new)", "brief")
    _not_script(project)
    with job(project, "analyze", task) as t:
        return _analyze(project, t)


VOICE_STAGE = ("finding speech", 0.5)          # pauses, for every recording with sound (fast)
SPEECH_STAGES = (("transcribing speech", 4),)  # only with a speech model


def _analyze(project: str, t: Task) -> dict:
    p = paths(project)
    brief = Brief.load(p["brief"])
    hw = check().get("hardware_decode")
    speech = speech_status() if brief.subtitles != "off" else {"ready": False, "how": "subtitles are off"}
    if brief.subtitles == "on" and not speech["ready"]:
        raise EnvironmentNotReady("Speech captions are turned on, but speech-to-text isn't set up.", speech["how"])
    from .brief import box, looks_like_box
    ignore = [box(brief.facecam)] if brief.facecam and looks_like_box(brief.facecam) else []
    result = an.analyze(brief.source, p["analysis"], key_moments=brief.key_moments, hwaccel=hw, task=t, cues=brief.cues,
                        ignore=ignore,
                        more_stages=(VOICE_STAGE, *(SPEECH_STAGES if speech["ready"] else ())))
    info = result["signals"]["info"]
    tracks = [a["index"] for a in info["audio_tracks"] if a["peak_db"] > -60]
    env = None
    if tracks:
        t.begin("finding speech", note="where the talking and the pauses are")
        env = envelope(brief.source, tracks, t)
        result["voice"] = speech_regions(env, pad=0.06, join=0.3)
        with open(p["analysis"], "w") as f:
            json.dump(result, f)
    out = an.summary(result)
    voice = result.get("voice")
    quiet = info["duration"] - sum(b - a for a, b in voice) if voice else 0
    out["pauses"] = ("none to cut (non-stop sound)" if tracks and voice is None else
                     "the recording is silent" if not voice else
                     f"{quiet:.0f} s of pauses; template 'tighten' cuts them out" if brief.video_type in
                     ("talking", "tutorial", "generic") else f"{quiet:.0f} s of quiet moments")
    out["speech"] = _transcribe(project, brief, tracks, env, speech, t, info["duration"])
    out["hdr"] = _hdr_note(info.get("hdr"), brief)
    return out


def _hdr_note(hdr: dict | None, brief: Brief) -> str:
    if not hdr:
        return "no (SDR source)"
    kind = "HDR10 (PQ)" if hdr["transfer"] == "pq" else "HLG"
    if brief.hdr == "keep":
        return f"{kind}; kept as HDR (10-bit HEVC)"
    return f"{kind}; converted to normal colour (SDR) so it plays right everywhere; hdr='keep' keeps it HDR"


def _transcribe(project: str, brief: Brief, tracks: list, env, speech: dict, t: Task, duration: float) -> dict:
    """Word-level transcript of the live audio tracks, when a model is installed (never required)."""
    p = paths(project)
    if os.path.exists(p["transcript"]):
        os.remove(p["transcript"])  # never keep a transcript of a different analysis
    if not speech["ready"]:
        return {"words": 0, "note": "no speech captions: " + speech.get("how", "")}
    if not tracks:
        return {"words": 0, "note": "the recording is silent"}
    tr = transcribe(brief.source, tracks, whisper_model(), p["transcript"], brief.speech_language, t,
                    duration, env=env)
    words = tr["words"]
    check = []
    if brief.terms and words:   # the user's names, where speech-to-text misheard them
        from .transcribe import apply_terms
        words, check = apply_terms(words, brief.terms)
        tr["words"] = words
        _save_json(p["transcript"], tr)
    if not words:
        return {"words": 0, "model": tr["model"], "note": "no speech found"}
    out = {"words": len(words), "model": tr["model"], "language": tr["language"],
           "preview": " ".join(w[2] for w in words[:30]) + (" ..." if len(words) > 30 else "")}
    if check:
        out["check"] = [f"{c['t']:.1f}s: '{c['said']}' may be {c['term']} (fix with update_edit words)"
                        for c in check[:10]]
    return out


def hook_from_speech(edit: dict, words: list[list], analysis: dict) -> None:
    """The hook caption becomes a strong line said later in the video (a teaser), when there is one;
    otherwise the library's hook stays. Talking videos cut on sentences keep theirs (the hook is spoken)."""
    sig = analysis["signals"]
    best = cw.quotes(edit, words, sig.get("loudness"), sig.get("rate", 2.0), 1)
    hook = next((s for s in edit["segments"] if s["kind"] == "hook"), None)
    if hook and best and best[0]["score"] >= cw.QUOTE_MIN:
        hook["top"], hook["top_from"] = best[0]["text"], "speech"
        edit["hook_note"] = (f"The hook caption is a line said at {best[0]['t']:.0f}s: \"{best[0]['said']}\". "
                             "Speech-to-text can mishear words; check it in preview_plan.")


def reaim(project: str, edit: dict) -> None:
    """Redo the automatic aims (after cuts moved); focus set by hand stays."""
    analysis = _analysis(project)
    if analysis:
        auto_focus(edit, analysis)
        mark_emphasis(edit, _words(project), analysis)


def refill_words(project: str, s: dict) -> None:
    """Fill a segment's speech words from the transcript after its cut points changed."""
    path = paths(project)["transcript"]
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            s["words"] = caption_words(json.load(f)["words"], s["src_in"], s["src_out"])


def transcript(project: str, start: float = 0.0, end: float = 0.0) -> dict:
    """Sentences with source times, optionally only between start and end (seconds)."""
    p = paths(project)
    if not os.path.exists(p["transcript"]):
        raise InputError("This project has no transcript.",
                         "Install a speech model (`nookcut models install`), then run analyze_video again.")
    with open(p["transcript"], encoding="utf-8") as f:
        tr = json.load(f)
    words = [w for w in tr["words"] if w[1] > start and (not end or w[0] < end)]
    return {"model": tr["model"], "language": tr["language"], "sentences": [
        {"start": s[0][0], "end": s[-1][1], "text": " ".join(w[2] for w in s)} for s in sentences(words)]}


def make_plan(project: str, template: str | None = None) -> dict:
    _not_script(project)
    p = paths(project)
    _need(p["analysis"], "analyze_video (nookcut analyze)", "analysis")
    brief = Brief.load(p["brief"])
    with open(p["analysis"]) as f:
        result = json.load(f)
    words = None
    if os.path.exists(p["transcript"]) and brief.subtitles != "off":
        with open(p["transcript"], encoding="utf-8") as f:
            words = json.load(f)["words"]
    edit = plan(brief, result, template, words)
    if words is not None:
        add_speech(edit, words, brief)
    clear_of_cuts(edit, result["signals"].get("scenes"))
    crop_bars(edit, result)
    auto_focus(edit, result)   # after every step that moves cuts or crops
    mark_emphasis(edit, words, result)
    if brief.label and edit["segments"]:
        edit["label"] = brief.label
    if words and edit.get("cut_by") not in ("sentences", "pauses"):
        hook_from_speech(edit, words, result)
    if brief.voiceover == "captions":   # read the hook and the end card aloud (agents write other lines)
        for s in edit["segments"]:
            if s["kind"] in ("hook", "end") and s.get("top") and not s.get("vo"):
                s["vo"] = spoken(s["top"])
    if brief.broll_dir:
        files = broll_files(project)
        notes = br.auto_place(edit, files) if brief.hdr != "keep" or not edit.get("source_hdr") else []
        edit["broll_notes"] = notes or [
            ("B-roll isn't used with HDR output (hdr: keep)" if brief.hdr == "keep" and edit.get("source_hdr") else
             "none placed automatically: " + ("no clip name matched a word the speaker says" if words else
                                              "automatic placement needs speech (a speech model)") +
             "; place B-roll with update_edit")]
    ed.save(edit, p["edit"])
    return describe(edit)


def panel_note(p: dict) -> str:
    """What the split layout's top panel shows, and how sure NookCut is about it."""
    if p.get("kind") == "facecam":
        return ("facecam from " + (f"the file {os.path.basename(p['file'])}" if p.get("file") else f"box {p.get('box')}")
                + " (yours)")
    others = f" (other still corners: {', '.join(p['others'])})" if p.get("others") else ""
    where = {"user": "the box you gave", "guess": f"a guess: the still {p.get('corner')} corner of the screen{others}",
             "default": "the top-left corner (no still corner was found)"}.get(p.get("by"), "")
    return (f"the minimap, from {where} {p.get('box')}. Check preview_plan (magenta outline); if it's wrong, "
            "set update_edit panel {\"box\": [x, y, w, h]}, or give a facecam")


def describe(edit: dict) -> dict:
    out = {"template": edit["template"], "length": ed.total(edit), "segments": [
        {"i": i, "kind": s["kind"], "at": st, "dur": round(d, 2), "src": [s["src_in"], s["src_out"]],
         "speed": s["speed"], "top": s["top"], "bottom": s["bottom"],
         **({"speech": " ".join(w[2] for w in s["words"])} if s.get("words") else {}),
         **({"broll": s["broll"]} if s.get("broll") else {}),
         **({"vo": s["vo"]} if s.get("vo") else {}),
         **({"focus": s.get("focus") or [0.5, 0.5], **({"focus_end": s["focus_end"]} if s.get("focus_end") else {}),
             **({"aimed": "automatically"} if s.get("focus_by") == "auto" else {}),
             **({"zoom": s["zoom"]} if s.get("zoom", 1) != 1 else {})}
            if edit.get("framing") == "split" else {})}
        for i, (st, d, s) in enumerate(ed.timeline(edit))], "speech_captions": bool(edit.get("subtitles")),
        "framing": edit.get("framing", "band")}
    if edit.get("framing") == "split":
        out["panel"] = panel_note(edit.get("panel") or {})
    if edit.get("hook_note"):
        out["hook"] = edit["hook_note"]
    if edit.get("broll_notes"):
        out["broll"] = edit["broll_notes"]
    if edit["template"] == "clip":
        out["note"] = ("One continuous clip, ending just after its biggest moment; no cards and no generated "
                       "music. Move src_in/src_out with update_edit to pick another stretch.")
    if edit["template"] == "tighten":
        out["note"] = (f"{edit.get('removed_s', 0):.0f} s of pauses cut out; the whole talk is kept, so max_duration "
                       "doesn't apply. No generated music under jump cuts.")
        from .brief import VERTICAL_MAX
        if out["length"] > VERTICAL_MAX and (edit.get("orientation") or "vertical") == "vertical":
            out["note"] += (f" It runs {out['length']:.0f} s, over the {VERTICAL_MAX:g} s limit for vertical videos: "
                            "use a short template, or make it wide.")
    return out


def make_preview(project: str, task: Task | None = None) -> str:
    p = paths(project)
    _need(p["edit"], "plan_edit (nookcut plan)", "edit plan")
    with job(project, "preview", task) as t:
        return preview(ed.load(p["edit"]), p["preview"], task=t)


def _music(project: str, style: str | None, task: Task) -> str | None:
    p = paths(project)
    edit = ed.load(p["edit"])
    if edit["music"] == "none":
        return None
    length = ed.total(edit)
    task.begin("making music", length, note="mastering your music file" if edit["music"] == "file"
               else f"composing an original {style or edit.get('music_style') or 'auto'} score")
    if edit["music"] == "file":
        if not os.path.isfile(edit["music_file"]):
            raise InputError(f"The music file can't be found: {edit['music_file']}",
                             "Check the path, or set music to 'ai' to compose an original score.")
        run(["ffmpeg", "-v", "error", "-y", "-i", edit["music_file"], "-t", f"{length:.3f}",
             "-af", f"afade=t=out:st={max(0, length - 1.5):.2f}:d=1.5,loudnorm=I=-14:TP=-1",
             "-ar", "48000", "-ac", "2", p["music"]], task, "preparing the music file", progress=task.set)
        return p["music"]
    return compose(edit, p["music"], style, task=task)


def make_music(project: str, style: str | None = None, task: Task | None = None) -> str | None:
    _need(paths(project)["edit"], "plan_edit (nookcut plan)", "edit plan")
    with job(project, "music", task) as t:
        return _music(project, style, t)


def make_render(project: str, out_name: str | None = None, task: Task | None = None) -> str:
    p = paths(project)
    _need(p["edit"], "plan_edit (nookcut plan)", "edit plan")
    edit = ed.load(p["edit"])
    errors = ed.validate(edit, edit.get("source_duration"))
    if errors:
        raise InputError("The edit has problems: " + "; ".join(errors), "Fix them with update_edit, then render.")
    with job(project, "render", task) as t:
        return _render(project, out_name, t)


VO_AT = 0.1          # a line starts this long into its segment unless vo_at says otherwise
VO_TEMPO_MAX = 1.25  # a line that doesn't fit is sped up at most this much, then reported


def spoken(caption: str) -> str:
    """A caption as a line to say: "IT TOOK ME|17 TRIES" -> "It took me 17 tries."."""
    text = re.sub(r"\s+", " ", caption.replace("|", " ")).strip().lower()
    text = re.sub(r"(\d+)\s*[-–]\s*(\d+)", r"\1 to \2", text)   # "1-10" is said "one to ten", not "minus"
    text = text.replace("#", "number ")
    text = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    return text if not text or text[-1] in ".!?" else text + "."


def _voice(project: str, edit: dict, t: Task) -> dict | None:
    """Speak every segment's vo line (cached), place it in output time and write voice.wav plus the
    timing in .nookcut/voice.json. Lines that run long are sped up a little, then reported."""
    from .tasks import state_dir
    out_wav, info = os.path.join(project, "voice.wav"), os.path.join(state_dir(project), "voice.json")
    lines = [(i, start, dur, s) for i, (start, dur, s) in enumerate(ed.timeline(edit)) if (s.get("vo") or "").strip()]
    for stale in (out_wav, info):
        if os.path.exists(stale):
            os.remove(stale)   # never mix an old voice track after the lines changed
    if not lines:
        return None
    voice = edit.get("voice") or {}
    name = vc.pick_voice(edit.get("language", "en"), voice.get("name", ""))
    warnings: list[str] = []
    if name is None:
        warnings.append(f"no voice on this computer speaks '{edit.get('language', 'en')}', so the voiceover was "
                        "left out; install one in the system settings, or set NOOKCUT_TTS_COMMAND")
        _save_json(info, {"lines": [], "warnings": warnings})
        return {"path": None, "lines": [], "warnings": warnings}
    speed = float(voice.get("speed") or 1.0)
    total_len = ed.total(edit)
    t.begin("speaking the voiceover", len(lines), note=f"{len(lines)} lines, voice {name or 'your command'}")
    placed = []
    for k, (i, start, dur, s) in enumerate(lines):
        wav = vc.cached(s["vo"].strip(), name or "", speed, os.path.join(state_dir(project), "voice"), t,
                        edit.get("language", "en"))
        length = float(qc._probe(wav, t)["format"]["duration"])
        at = start + float(s.get("vo_at") if s.get("vo_at") is not None else VO_AT)
        nxt = lines[k + 1] if k + 1 < len(lines) else None
        until = (nxt[1] + float(nxt[3].get("vo_at") if nxt[3].get("vo_at") is not None else VO_AT) - 0.15) if nxt \
            else total_len - 0.1
        room = max(0.3, until - at)
        tempo = min(VO_TEMPO_MAX, length / room) if length > room else 1.0
        said = length / tempo
        if said > room + 0.05:
            warnings.append(f"the voice line on segment {i + 1} runs {said - room:.1f} s past its slot: shorten it "
                            "or give it a longer segment")
        if s.get("words"):
            warnings.append(f"segment {i + 1}: the voice line plays over the recording's own speech")
        placed.append({"segment": i, "start": round(at, 3), "length": round(said, 3), "tempo": round(tempo, 3),
                       "text": s["vo"].strip(), "file": wav})
        t.set(k + 1)
    if edit.get("template") == "tighten":
        warnings.append("a voiceover over a tightened talk competes with the speaker")
    cmd = ["ffmpeg", "-v", "error", "-y"]
    graph = []
    for k, line in enumerate(placed):
        cmd += ["-i", line["file"]]
        ms = round(line["start"] * 1000)
        tempo = f"atempo={line['tempo']}," if line["tempo"] != 1.0 else ""
        graph.append(f"[{k}:a]{tempo}adelay={ms}:all=1[v{k}]")
    mix = "".join(f"[v{k}]" for k in range(len(placed)))
    graph.append(f"{mix}amix=inputs={len(placed)}:normalize=0:duration=longest,apad=whole_dur={total_len:.3f},"
                 f"atrim=0:{total_len:.3f}[vo]")
    run(cmd + ["-filter_complex", ";".join(graph), "-map", "[vo]", "-ar", "48000", "-ac", "1", out_wav], t,
        "building the voice track")
    result = {"path": out_wav, "voice": name or "your voice program's default for the language",
              "lines": placed, "warnings": warnings}
    _save_json(info, result)
    return result


def _save_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def _vo_words(edit: dict, placed: list[dict]) -> None:
    """Caption words for each spoken line, spread over it by length (the text is known exactly)."""
    starts = {i: st for i, (st, _, _) in enumerate(ed.timeline(edit))}
    for line in placed:
        s = edit["segments"][line["segment"]]
        words = line["text"].split()
        weights = [len(w) + 1 for w in words]
        t, out = line["start"] - starts[line["segment"]], []
        for w, wt in zip(words, weights):
            d = line["length"] * wt / sum(weights)
            out.append([round(t, 3), round(t + d * 0.92, 3), w])
            t += d
        dur = ed.seg_duration(s)
        s["_vo_words"] = [[a, min(b, dur), w] for a, b, w in out if a < dur]


def _render(project: str, out_name: str | None, t: Task) -> str:
    p = paths(project)
    edit = ed.load(p["edit"])
    music = p["music"] if edit["music"] != "none" and os.path.exists(p["music"]) else None
    need_music = edit["music"] != "none" and not music
    has_vo = any((s.get("vo") or "").strip() for s in edit["segments"])
    t.plan(*([("making music", 1)] if need_music else []), *([("speaking the voiceover", 0.5)] if has_vo else []),
           ("preparing", 0.3), ("rendering segments", 10), ("mixing audio", 0.7))
    if need_music:
        music = _music(project, None, t)
    vo = _voice(project, edit, t)
    if vo and vo["lines"]:
        _vo_words(edit, vo["lines"])
    out = os.path.join(project, (out_name or slug(edit.get("title", "nookcut"))) + ".mp4")
    env = check()
    return render(edit, out, music, hwaccel=env.get("hardware_decode"), encoder=env.get("hardware_encode"),
                  task=t, voice_wav=vo["path"] if vo else None)


def broll_files(project: str, task: Task | None = None) -> list[dict]:
    """The project's B-roll folder, indexed (probes cached in .nookcut/broll.json)."""
    from .tasks import state_dir
    brief = Brief.load(paths(project)["brief"])
    if not brief.broll_dir:
        raise InputError("This project has no B-roll folder.", "Set broll_dir in the brief (create_project).")
    return br.index(brief.broll_dir, os.path.join(state_dir(project), "broll.json"), task)


def latest_render(project: str) -> str:
    """The newest video in the project folder (the last render)."""
    videos = [os.path.join(project, f) for f in os.listdir(project) if f.lower().endswith(".mp4")]
    if not videos:
        raise InputError("This project has no rendered video yet.", "Run render_video (nookcut render) first.")
    return max(videos, key=os.path.getmtime)


def verify_render(project: str, video: str | None = None, task: Task | None = None) -> dict:
    """Checks of a render against its edit, plus a sheet with one frame per segment."""
    p = paths(project)
    _need(p["edit"], "plan_edit (nookcut plan)", "edit plan")
    video = video or latest_render(project)
    out = qc.verify(ed.load(p["edit"]), video, os.path.join(project, "check.jpg"), task)
    out["video"] = os.path.abspath(video)
    from .tasks import state_dir
    vo_info = os.path.join(state_dir(project), "voice.json")
    if os.path.exists(vo_info):
        with open(vo_info, encoding="utf-8") as f:
            vo = json.load(f)
        out["issues"] += vo.get("warnings", [])
        out["ok"] = not out["issues"]
        if vo.get("lines"):
            out["facts"]["voiceover"] = f"{len(vo['lines'])} lines, voice {vo.get('voice')}"
    return out


def frame_sheet(project: str, times: list | None = None, source: bool = False, count: int = 8,
                video: str | None = None, task: Task | None = None) -> dict:
    """A labelled grid of frames from the last render (or the source, with source=True), at the given
    times (seconds or "m:ss" in that file) or spread evenly."""
    from .brief import seconds
    from .render import color_plan
    p = paths(project)
    pre, args, tile = "", [], (480, 270)
    if source:
        _need(p["brief"], "create_project (nookcut new)", "brief")
        video = Brief.load(p["brief"]).source
        look = color_plan({"source": video}, sdr_only=True)   # HDR sources tone-mapped, as in the render
        pre, args = look["pre"], look["args"]
    else:
        video = video or latest_render(project)
        if os.path.exists(p["edit"]):
            e = ed.load(p["edit"])
            tile = qc.TILE.get(e.get("orientation"), tile)
    info = qc._probe(video, task)
    length = float(info.get("format", {}).get("duration") or 0)
    marks = [min(length - 0.05, seconds(t)) for t in times] if times else qc.spread(length, count)
    sheet = qc.frames(video, os.path.join(project, "frames.jpg"), marks, None, tile, pre, args, task)
    return {"frames": os.path.abspath(sheet), "times": marks, "from": "source" if source else "render",
            "video": os.path.abspath(video)}


def make_resolve(project: str, render_path: str = "", proxy: bool = False, task: Task | None = None) -> dict:
    """The Resolve kit; proxy=True also makes a light ProRes copy of the recording for smooth editing."""
    p = paths(project)
    _need(p["edit"], "plan_edit (nookcut plan)", "edit plan")
    edit = ed.load(p["edit"])
    music = p["music"] if os.path.exists(p["music"]) else ""
    proxy_path = ""
    if proxy:
        with job(project, "resolve", task) as t:
            proxy_path = _make_proxy(edit, p["resolve"], t)
    return resolve_kit(edit, p["resolve"], os.path.abspath(render_path) if render_path else "",
                       os.path.abspath(music) if music else "", edit.get("title") or "NookCut Short", proxy_path)


def _make_proxy(edit: dict, kit_dir: str, t: Task) -> str:
    """resolve/proxy/<recording>_proxy.mov, made once (again only if the recording changed)."""
    from .export import proxy_cmd
    from .preflight import pick_prores
    encoder = pick_prores()
    if not encoder:
        raise EnvironmentNotReady("This ffmpeg can't write ProRes, which the Resolve proxy needs.",
                                  "Install a full ffmpeg build, or export without the proxy.")
    source = edit["source"]
    out = os.path.abspath(os.path.join(kit_dir, "proxy", slug(os.path.splitext(os.path.basename(source))[0])
                                       + "_proxy.mov"))
    if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(source):
        return out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    part = out[:-4] + ".part.mov"
    step = f"making the ProRes proxy ({encoder[2]})"
    t.begin(step, edit.get("source_duration") or 0)
    try:
        run(proxy_cmd(edit, part, encoder, apple=encoder[0].endswith("videotoolbox")), t, step, progress=t.set)
        os.replace(part, out)
    finally:
        if os.path.exists(part):
            os.remove(part)
    return out


def _analysis(project: str) -> dict | None:
    path = paths(project)["analysis"]
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def suggest_copy(project: str, count: int = 5) -> dict:
    """Offline captions/titles/description/hashtags for the current edit (no internet)."""
    p = paths(project)
    _need(p["edit"], "plan_edit (nookcut plan)", "edit plan")
    return cw.suggest(Brief.load(p["brief"]), _analysis(project), ed.load(p["edit"]), count, _words(project))


def _words(project: str) -> list[list] | None:
    path = paths(project)["transcript"]
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)["words"]


def make_kit(project: str, video: str, titles: list[str] | None = None, description: str = "",
             hashtags=None, notes: str = "", creator_tz: str | None = None) -> str:
    """publish_kit.md; anything left empty is filled from the offline copy library."""
    p = paths(project)
    brief, edit = Brief.load(p["brief"]), ed.load(p["edit"])
    if not (titles and description and hashtags):
        copy = cw.suggest(brief, _analysis(project), edit)
        titles, description = titles or copy["titles"], description or copy["description"]
        hashtags = hashtags or copy["hashtags"]
        if not notes:
            notes = (f"Titles, description and hashtags come from NookCut's {copy['source']}; "
                     f"hashtags are {copy['hashtags_note']}. {copy.get('note', '')}").strip()
    return write_kit(p["kit"], edit, video, brief.region, titles, description, hashtags, notes, creator_tz)


MAKE_STAGES = (("reading the source", 1), ("scanning the video", 6), ("measuring loudness", 2),
               ("detecting moments", 0.2), VOICE_STAGE, *SPEECH_STAGES, ("planning", 0.1), ("making music", 1), ("preparing", 0.3),
               ("rendering segments", 10), ("mixing audio", 0.7), ("checking the video", 0.5), ("finishing", 0.2))


SCRIPT_STAGES = (("narrating", 3), ("making the background", 1.5), ("planning", 0.1), ("making music", 1),
                 ("preparing", 0.3), ("rendering segments", 10), ("mixing audio", 0.7), ("checking the video", 0.5),
                 ("finishing", 0.2))


def make_from_script(project: str, brief: Brief, script_text: str, out_name: str | None = None,
                     task: Task | None = None) -> dict:
    """A video from a script, offline: narrate every line, build a moving background with the
    narration as its sound, plan one segment per line with the user's B-roll, then the usual music,
    render, checks, Resolve files and publish kit. Running it again rebuilds the narration (unchanged
    lines are cached), base.mp4, transcript.json and edit.json."""
    from . import script as sc
    from .edit import output_fps
    from .preflight import has_filter
    from .tasks import state_dir
    script_lines = sc.lines(script_text)
    if not vc.engine():
        raise EnvironmentNotReady("Script videos need a voice, and none works on this computer.",
                                  "On macOS the built-in voice works; or run `nookcut config kokoro`, or set "
                                  "NOOKCUT_TTS_COMMAND.")
    name = vc.pick_voice(brief.language, brief.voice)
    if name is None:
        raise EnvironmentNotReady(f"No voice on this computer speaks '{brief.language}'.",
                                  "Install one in the system settings, or run `nookcut config kokoro`.")
    os.makedirs(project, exist_ok=True)
    p = paths(project)
    brief.made_from, brief.source = "script", os.path.join(os.path.abspath(project), "base.mp4")
    brief.subtitles = "on"
    warnings = brief.validate()
    warnings = [w for w in warnings if "max_duration" not in w]   # a script is as long as it is
    guess = sc.estimate(script_lines)
    from .brief import PLATFORM_MAX, VERTICAL_MAX
    if brief.orientation == "vertical" and guess > VERTICAL_MAX:
        warnings.append(f"the script runs about {guess:.0f} s; vertical videos should be {VERTICAL_MAX:g} s at most, "
                        "so shorten the script")
    for plat in brief.platforms:
        if PLATFORM_MAX.get(plat) and guess > PLATFORM_MAX[plat]:
            warnings.append(f"the script runs about {guess:.0f} s; {plat} allows up to {PLATFORM_MAX[plat]} s")
    with open(os.path.join(project, "script.txt"), "w", encoding="utf-8") as f:
        f.write(script_text.strip() + "\n")
    with job(project, "script", task) as t:
        t.plan(*SCRIPT_STAGES)
        t.fixed_plan = True
        t.begin("narrating", len(script_lines), note=f"{len(script_lines)} lines, voice {name or 'your voice program'}")
        cache = os.path.join(state_dir(project), "voice")
        placed, at = [], 0.0
        for k, line in enumerate(script_lines):
            wav = vc.cached(sc.say_form(line), name or "", brief.voice_speed, cache, t, brief.language)
            tight = wav[:-4] + ".trim.wav"
            if not os.path.exists(tight):   # no silence before or after the words: line times are exact
                run(["ffmpeg", "-v", "error", "-y", "-i", wav, "-af",
                     "silenceremove=start_periods=1:start_threshold=-50dB,areverse,"
                     "silenceremove=start_periods=1:start_threshold=-50dB,areverse", tight], t, "trimming a line")
            length = float(qc._probe(tight, t)["format"]["duration"])
            placed.append({"text": line, "file": tight, "start": round(at, 3), "end": round(at + length, 3),
                           "words": sc.word_times(line, at, length)})
            at += length + sc.GAP
            t.set(k + 1)
        total_len = round(placed[-1]["end"] + 0.6, 3)
        t.begin("making the background", total_len)
        narration = os.path.join(project, "narration.wav")
        cmd, graph = ["ffmpeg", "-v", "error", "-y"], []
        for k, line in enumerate(placed):
            cmd += ["-i", line["file"]]
            graph.append(f"[{k}:a]adelay={round(line['start'] * 1000)}:all=1[l{k}]")
        graph.append("".join(f"[l{k}]" for k in range(len(placed))) +
                     f"amix=inputs={len(placed)}:normalize=0:duration=longest,apad=whole_dur={total_len},"
                     f"atrim=0:{total_len}[n]")
        run(cmd + ["-filter_complex", ";".join(graph), "-map", "[n]", "-ar", "48000", "-ac", "1", narration], t,
            "joining the narration")
        w, h = brief.size
        background = (f"gradients=s={w}x{h}:c0=0x0f1420:c1=0x1d2b4a:c2=0x2a1f3d:nb_colors=3:speed=0.004:rate=30"
                      if has_filter("gradients") else f"color=c=0x141a26:s={w}x{h}:r=30,noise=alls=3:allf=t")
        part = brief.source + ".part.mp4"
        try:
            run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{background},format=yuv420p", "-i", narration,
                 "-t", f"{total_len}", "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast",
                 "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-color_primaries", "bt709", "-color_trc", "bt709",
                 "-colorspace", "bt709", "-color_range", "tv", part], t, "making the background", progress=t.set)
            os.replace(part, brief.source)
        finally:
            if os.path.exists(part):
                os.remove(part)
        brief.save(p["brief"])
        words = [w for line in placed for w in line["words"]]
        _save_json(p["transcript"], {"model": "script", "language": brief.language, "tracks": [0], "words": words})
        t.begin("planning")
        files = broll_files(project, t) if brief.broll_dir else []
        edit, notes = sc.build_edit(brief, {"duration": total_len, "fps": 30.0}, placed, files,
                                    output_fps(30.0, brief.fps))
        edit["broll_notes"] = notes
        ed.save(edit, p["edit"])
        copy = suggest_copy(project)
        if brief.music != "none":
            _music(project, None, t)
        video = _render(project, out_name or copy["file_name"], t)
        t.begin("checking the video", note="size, length, loudness, black or frozen picture")
        checks = verify_render(project, video, t)
        t.begin("finishing", note="DaVinci Resolve files and the publish kit")
        resolve = make_resolve(project, video)
        resolve["note"] = ("The Resolve timeline's main clip is the generated background (base.mp4); the narration "
                           "is its sound. " + resolve.get("note", "")).strip()
        kit = make_kit(project, video)
    return {"video": os.path.abspath(video), "publish_kit": os.path.abspath(kit), "resolve": resolve,
            "length_s": ed.total(edit), "lines": len(placed), "voice": name or "your voice program",
            "broll": notes, "summary": f"{ed.total(edit):.0f} s {brief.orientation} video from {len(placed)} lines",
            "titles": copy["titles"][:3], "warnings": warnings,
            "checks": {"ok": checks["ok"], "issues": checks["issues"], "facts": checks["facts"],
                       "frames": os.path.abspath(checks["frames"])}}


def make_all(project: str, brief: Brief, template: str | None = None, out_name: str | None = None,
             task: Task | None = None) -> dict:
    """Every step in one job, offline: brief -> analysis -> plan (built-in captions) -> music ->
    render -> Resolve export -> publish kit. One progress bar, one cancel."""
    created = new_project(project, brief)
    with job(project, "make", task) as t:
        speech = brief.subtitles != "off" and speech_status()["ready"]
        t.plan(*(s for s in MAKE_STAGES if speech or s not in SPEECH_STAGES))
        t.fixed_plan = True
        found = _analyze(project, t)
        t.begin("planning")
        view = make_plan(project, template)
        copy = suggest_copy(project)
        if brief.music != "none":
            _music(project, None, t)
        video = _render(project, out_name or copy["file_name"], t)
        t.begin("checking the video", note="size, length, loudness, black or frozen picture")
        checks = verify_render(project, video, t)
        t.begin("finishing", note="DaVinci Resolve files and the publish kit")
        resolve = make_resolve(project, video)
        kit = make_kit(project, video)  # filled from the same offline library, with a source note
    edit = ed.load(paths(project)["edit"])
    deaths = len(found.get("deaths") or [])
    story = {"comeback": f"{deaths} deaths, then the win", "victory": "the win",
             "highlights": "the top moments", "tighten": "the whole talk without its pauses",
             "clip": "one continuous clip"}.get(view["template"], view["template"])
    music = {"none": "no music", "file": "your music file"}.get(edit["music"], f"original {edit['music_style']} score")
    out = {"video": os.path.abspath(video), "publish_kit": os.path.abspath(kit),
           "resolve": resolve, "length_s": view["length"], "template": view["template"],
           "summary": f"{view['length']:.0f} s {edit['orientation']} short: {story}, {music}.",
           "titles": copy["titles"][:3], "warnings": created["warnings"],
           "checks": {"ok": checks["ok"], "issues": checks["issues"], "facts": checks["facts"],
                      "frames": os.path.abspath(checks["frames"])},
           "speech_captions": "word by word" if edit.get("subtitles") else found["speech"].get("note", "none"),
           **{k: view[k] for k in ("panel", "hook") if view.get(k)}}
    if copy.get("note"):
        out["note"] = copy["note"]
    return out
