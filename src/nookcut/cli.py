"""nookcut command line.

  nookcut check
  nookcut new PROJECT --source raw.mov --title "..." [--game ...] [--orientation vertical] [--max 60]
                     [--region India] [--language en] [--music ai|none|file] [--music-file x.mp3]
  nookcut analyze PROJECT
  nookcut plan PROJECT [--template comeback|victory|highlights]
  nookcut preview PROJECT
  nookcut music PROJECT [--style taiko|pulse|cinematic]
  nookcut render PROJECT
  nookcut resolve PROJECT [--render file.mp4]
  nookcut when REGION
  nookcut make PROJECT ...   everything in one go, offline (same as the MCP tool auto_edit)
  nookcut copy show PROJECT  built-in caption/title/hashtag options for a planned project
  nookcut copy init LANG     start a caption language pack (~/.nookcut/copy/LANG.json) to translate
  nookcut watch PROJECT    live progress of any job on PROJECT (also ones your agent started)
  nookcut cancel PROJECT   stop the job running on PROJECT and its ffmpeg processes
  nookcut service install|status|restart|connect|uninstall   one shared server for every agent session

Exit codes: 0 ok, 1 error, 2 environment not ready, 3 project busy, 130 cancelled.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import pipeline as pl
from .brief import Brief
from .errors import Busy, Cancelled, EnvironmentNotReady, NookCutError, as_nookcut
from .preflight import check
from .progress_ui import LineBar, _c, watch
from .publish import posting_windows
from .tasks import Task, install_signal_handlers, request_cancel


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _brief_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--source", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--type", default="gameplay", dest="video_type")
    p.add_argument("--game", default="")
    p.add_argument("--orientation", default="vertical")
    p.add_argument("--max", default="60", dest="max_duration", help="seconds, e.g. 60, 45s or 1:30")
    p.add_argument("--region", default="")
    p.add_argument("--language", default="en")
    p.add_argument("--music", default="ai")
    p.add_argument("--music-file", default="")
    p.add_argument("--source-audio", default="duck")
    p.add_argument("--platforms", default="youtube_shorts,tiktok,instagram_reels")
    p.add_argument("--online", action="store_true", help="the user allows the agent to research on the web")


def _brief(a) -> Brief:
    return Brief(title=a.title, source=a.source, video_type=a.video_type, game=a.game,
                 platforms=a.platforms.split(","), orientation=a.orientation, max_duration=a.max_duration,
                 region=a.region, language=a.language, music=a.music, music_file=a.music_file,
                 source_audio=a.source_audio, online=a.online)


def _fail(e: BaseException, verbose: bool) -> int:
    err = as_nookcut(e)
    print(_c("33", f"■ {err.message}") if isinstance(err, Cancelled) else _c("31", f"✖ {err.message}"),
          file=sys.stderr)
    if err.hint:
        print(f"  What to do: {err.hint}", file=sys.stderr)
    if err.detail and (verbose or err.code == "media"):
        print(_c("2", "  ffmpeg said:\n    " + err.detail.replace("\n", "\n    ")), file=sys.stderr)
    if not isinstance(e, NookCutError) and verbose:
        import traceback
        traceback.print_exception(e)
    return {Cancelled: 130, EnvironmentNotReady: 2, Busy: 3}.get(type(err), 1)


def main(argv: list[str] | None = None) -> int:
    install_signal_handlers()
    try:
        return _main(argv)
    except KeyboardInterrupt:
        print(_c("33", "\n■ Stopped. All ffmpeg processes for this job were ended."), file=sys.stderr)
        return 130
    except Exception as e:
        return _fail(e, "-v" in (argv if argv is not None else sys.argv) or
                     "--verbose" in (argv if argv is not None else sys.argv))


def _task(name: str, project: str, quiet: bool):
    ui = None if quiet else LineBar()
    return Task(name, project, on_update=ui), ui


def _step(fn, name: str, project: str, quiet: bool, *args):
    task, ui = _task(name, project, quiet)
    try:
        return fn(project, *args, task=task)
    finally:
        if ui:
            ui.close()


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nookcut", description="Raw recording in, viral short out.")
    ap.add_argument("-v", "--verbose", action="store_true", help="show full error details")
    ap.add_argument("-q", "--quiet", action="store_true", help="no progress bar")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    n = sub.add_parser("new"); n.add_argument("project"); _brief_args(n)
    m = sub.add_parser("make"); m.add_argument("project"); _brief_args(m)
    m.add_argument("--template"); m.add_argument("--name", default="", help="output file name (no extension)")
    cp = sub.add_parser("copy", help="built-in offline captions, titles and hashtags")
    cp.add_argument("action", choices=("show", "init")); cp.add_argument("target", help="project (show) or language (init)")
    for name in ("analyze", "preview", "render"):
        sub.add_parser(name).add_argument("project")
    pp = sub.add_parser("plan"); pp.add_argument("project"); pp.add_argument("--template")
    mu = sub.add_parser("music"); mu.add_argument("project"); mu.add_argument("--style")
    rs = sub.add_parser("resolve"); rs.add_argument("project"); rs.add_argument("--render", default="")
    w = sub.add_parser("when"); w.add_argument("region"); w.add_argument("--tz")
    st = sub.add_parser("setup", help="print MCP config for an agent platform ('all' lists every one)")
    st.add_argument("platform"); st.add_argument("--url", default="", help="remote HTTP endpoint instead of stdio")
    sk = sub.add_parser("skill", help="install or export the nookcut-editor skill")
    sk.add_argument("action", choices=("install", "export", "print"))
    sk.add_argument("target", nargs="?", default="",
                    help="install: claude|codex|agents   export: agents-md|gemini|cursor|windsurf|copilot|cline")
    sk.add_argument("--dir", default=".", help="project folder for export")
    sub.add_parser("watch", help="live progress screen for a project").add_argument("project")
    sv = sub.add_parser("service", help="one shared background server for every agent session")
    sv.add_argument("action", choices=("install", "status", "restart", "connect", "uninstall"))
    sub.add_parser("cancel", help="stop the job running on a project").add_argument("project")
    a = ap.parse_args(argv)
    if a.cmd == "watch":
        return watch(a.project)
    if a.cmd == "service":
        from . import service
        print({"install": service.install, "status": service.status, "connect": service.connect_help, "restart": service.restart,
               "uninstall": service.uninstall}[a.action]())
        return 0
    if a.cmd == "cancel":
        ok = request_cancel(a.project)
        print("Cancel requested; the job will stop within a second." if ok else "Nothing is running on that project.")
        return 0 if ok else 1

    if a.cmd == "copy":
        if a.action == "init":
            from .copywriting import init_pack
            path = init_pack(a.target)
            print(f"Language pack: {path}\nTranslate the values (keep the keys and {{slots}}); missing keys fall back "
                  "to English. Set the project's language to use it.")
        else:
            _print(pl.suggest_copy(a.target))
        return 0
    if a.cmd == "check":
        r = check(); _print(r); return 0 if r["ok"] else 1
    if a.cmd == "setup":
        from . import agents
        names = agents.platforms() if a.platform == "all" else [a.platform]
        print("\n\n".join(agents.describe_config(n, a.url) for n in names))
        return 0
    if a.cmd == "skill":
        from . import agents
        if a.action == "print":
            print(agents.skill_text()); return 0
        if a.action == "install":
            print(agents.install_skill(a.target or "claude")); return 0
        print(agents.export_rules(a.target or "agents-md", a.dir)); return 0
    if a.cmd in ("new", "make", "analyze", "plan", "preview", "music", "render", "resolve"):
        env = check()
        if not env["ok"]:
            raise EnvironmentNotReady(f"This computer is missing what NookCut needs: {', '.join(env['missing'])}.",
                                      f"Install it with: {env['install']}")
    if a.cmd == "new":
        _print(pl.new_project(a.project, _brief(a)))
    elif a.cmd == "analyze":
        _print(_step(pl.run_analysis, "analyze", a.project, a.quiet))
    elif a.cmd == "plan":
        _print(pl.make_plan(a.project, a.template))
    elif a.cmd == "preview":
        print(_step(pl.make_preview, "preview", a.project, a.quiet))
    elif a.cmd == "music":
        print(_step(pl.make_music, "music", a.project, a.quiet, a.style))
    elif a.cmd == "render":
        print(_step(pl.make_render, "render", a.project, a.quiet))
    elif a.cmd == "resolve":
        _print(pl.make_resolve(a.project, a.render))
    elif a.cmd == "when":
        _print(posting_windows(a.region, a.tz))
    elif a.cmd == "make":
        task, ui = _task("make", a.project, a.quiet)
        try:
            _print(pl.make_all(a.project, _brief(a), a.template, a.name or None, task=task))
        finally:
            if ui:
                ui.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
