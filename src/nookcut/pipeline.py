"""Project folder layout and the high-level steps shared by the CLI and the MCP server.

<project>/
  brief.json        what the user wants
  analysis.json     signals + detected events
  edit.json         the edit (agent may rewrite captions/segments here)
  preview.png       contact sheet for approval
  music.wav         original score (or the user's file, mastered)
  <name>.mp4        the render
  resolve/          EDLs + Lua builder for DaVinci Resolve
  publish_kit.md    titles, hashtags, posting plan

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
from .publish import write_kit
from .render import preview, render
from .story import plan
from .preflight import check
from .errors import Cancelled, InputError, as_nookcut
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
        "music": "music.wav", "resolve": "resolve", "kit": "publish_kit.md"}.items()}


def new_project(project: str, brief: Brief) -> dict:
    os.makedirs(project, exist_ok=True)
    warnings = brief.validate()
    brief.save(paths(project)["brief"])
    return {"project": project, "warnings": warnings}


def run_analysis(project: str, task: Task | None = None) -> dict:
    _need(paths(project)["brief"], "create_project (nookcut new)", "brief")
    with job(project, "analyze", task) as t:
        return _analyze(project, t)


def _analyze(project: str, t: Task) -> dict:
    p = paths(project)
    brief = Brief.load(p["brief"])
    hw = check().get("hardware_decode")
    result = an.analyze(brief.source, p["analysis"], key_moments=brief.key_moments, hwaccel=hw, task=t)
    return an.summary(result)


def make_plan(project: str, template: str | None = None) -> dict:
    p = paths(project)
    _need(p["analysis"], "analyze_video (nookcut analyze)", "analysis")
    brief = Brief.load(p["brief"])
    with open(p["analysis"]) as f:
        result = json.load(f)
    edit = plan(brief, result, template)
    ed.save(edit, p["edit"])
    return describe(edit)


def describe(edit: dict) -> dict:
    return {"template": edit["template"], "length": ed.total(edit), "segments": [
        {"i": i, "kind": s["kind"], "at": st, "dur": round(d, 2), "src": [s["src_in"], s["src_out"]],
         "speed": s["speed"], "top": s["top"], "bottom": s["bottom"]}
        for i, (st, d, s) in enumerate(ed.timeline(edit))]}


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


def _render(project: str, out_name: str | None, t: Task) -> str:
    p = paths(project)
    edit = ed.load(p["edit"])
    music = p["music"] if edit["music"] != "none" and os.path.exists(p["music"]) else None
    need_music = edit["music"] != "none" and not music
    t.plan(*([("making music", 1)] if need_music else []), ("preparing", 0.3),
           ("rendering segments", 10), ("mixing audio", 0.7))
    if need_music:
        music = _music(project, None, t)
    out = os.path.join(project, (out_name or slug(edit.get("title", "nookcut"))) + ".mp4")
    env = check()
    return render(edit, out, music, hwaccel=env.get("hardware_decode"), encoder=env.get("hardware_encode"),
                  task=t)


def make_resolve(project: str, render_path: str = "") -> dict:
    p = paths(project)
    edit = ed.load(p["edit"])
    music = p["music"] if os.path.exists(p["music"]) else ""
    return resolve_kit(edit, p["resolve"], os.path.abspath(render_path) if render_path else "",
                       os.path.abspath(music) if music else "", edit.get("title") or "NookCut Short")


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
    return cw.suggest(Brief.load(p["brief"]), _analysis(project), ed.load(p["edit"]), count)


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
               ("detecting moments", 0.2), ("planning", 0.1), ("making music", 1), ("preparing", 0.3),
               ("rendering segments", 10), ("mixing audio", 0.7), ("finishing", 0.2))


def make_all(project: str, brief: Brief, template: str | None = None, out_name: str | None = None,
             task: Task | None = None) -> dict:
    """Every step in one job, offline: brief -> analysis -> plan (built-in captions) -> music ->
    render -> Resolve export -> publish kit. One progress bar, one cancel."""
    created = new_project(project, brief)
    with job(project, "make", task) as t:
        t.plan(*MAKE_STAGES)
        t.fixed_plan = True
        found = _analyze(project, t)
        t.begin("planning")
        view = make_plan(project, template)
        copy = suggest_copy(project)
        if brief.music != "none":
            _music(project, None, t)
        video = _render(project, out_name or copy["file_name"], t)
        t.begin("finishing", note="DaVinci Resolve files and the publish kit")
        resolve = make_resolve(project, video)
        kit = make_kit(project, video)  # filled from the same offline library, with a source note
    edit = ed.load(paths(project)["edit"])
    deaths = len(found.get("deaths") or [])
    story = {"comeback": f"{deaths} deaths, then the win", "victory": "the win",
             "highlights": "the top moments"}.get(view["template"], view["template"])
    music = {"none": "no music", "file": "your music file"}.get(edit["music"], f"original {edit['music_style']} score")
    out = {"video": os.path.abspath(video), "publish_kit": os.path.abspath(kit),
           "resolve": resolve, "length_s": view["length"], "template": view["template"],
           "summary": f"{view['length']:.0f} s {edit['orientation']} short: {story}, {music}.",
           "titles": copy["titles"][:3], "warnings": created["warnings"]}
    if copy.get("note"):
        out["note"] = copy["note"]
    return out
