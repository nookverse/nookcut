"""nookcut command line.

  nookcut check
  nookcut new PROJECT --source raw.mov --title "..." [--game ...] [--orientation vertical] [--max 60]
                     [--region India] [--language en] [--music ai|none|file] [--music-file x.mp3]
  nookcut analyze PROJECT
  nookcut plan PROJECT [--template comeback|victory|highlights|tighten]
  nookcut framing PROJECT band|full   blurred bands (default) or the picture filling the frame
  nookcut preview PROJECT
  nookcut music PROJECT [--style taiko|pulse|cinematic]
  nookcut render PROJECT
  nookcut resolve PROJECT [--render file.mp4] [--proxy]   --proxy: ProRes copy for smooth editing
  nookcut when REGION
  nookcut make PROJECT ...   everything in one go, offline (same as the MCP tool auto_edit)
  nookcut copy show PROJECT  built-in caption/title/hashtag options for a planned project
  nookcut copy init LANG     start a caption language pack (~/.nookcut/copy/LANG.json) to translate
  nookcut models list|install [NAME]|remove NAME   optional speech-to-text models (the only download)
  nookcut transcript PROJECT what is said in the recording, with times (after analyze)
  nookcut frames PROJECT [--at 1:30 ...] [--count 8] [--source]   a frame sheet to look at
  nookcut verify PROJECT [--video file.mp4]                       checks of the last render
  nookcut watch PROJECT    live progress of any job on PROJECT (also ones your agent started)
  nookcut cancel PROJECT   stop the job running on PROJECT and its ffmpeg processes
  nookcut service install|status|restart|connect|uninstall   one shared server for every agent session

Exit codes: 0 ok, 1 error, 2 environment not ready, 3 project busy, 130 cancelled.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
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
    p.add_argument("--type", default="", dest="video_type", help="gameplay | talking | sports | tutorial | generic "
                   "(default: gameplay if --game is set, else generic)")
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
    p.add_argument("--moment", action="append", default=[], dest="key_moments", metavar="TIME[=LABEL]",
                   help='a moment you know, e.g. --moment "10:42=final blow" (repeatable; a label with '
                        'win/final/kill/victory/blow marks the win)')
    p.add_argument("--subtitles", default="auto", help="word-by-word speech captions: auto | on | off")
    p.add_argument("--speech-language", default="auto", help="spoken language, e.g. en or es (auto detects)")
    p.add_argument("--fps", default="auto", help="output frame rate: auto (60, or 59.94/50 to match the source) | 60 | 30")
    p.add_argument("--voiceover", default="off", help="off | captions (read the hook and end captions aloud)")
    p.add_argument("--voice", default="", help="voice name (nookcut voices); default: the best for the language")
    p.add_argument("--voice-speed", type=float, default=1.0)
    p.add_argument("--copy-pack", default="", help="a caption/title style pack (nookcut copy packs)")
    p.add_argument("--broll-dir", default="", help="folder of your own clips/pictures to show as B-roll")
    p.add_argument("--hdr", default="sdr", help="HDR sources: sdr (convert, plays everywhere) | keep (HDR10/HLG HEVC)")
    p.add_argument("--framing", default="band", help="band (sharp band over a blurred copy) | full (the picture "
                   "fills the frame; aim shots with focus in edit.json)")


def _brief(a) -> Brief:
    return Brief(title=a.title, source=a.source, video_type=a.video_type or ("gameplay" if a.game else "generic"),
                 game=a.game,
                 platforms=a.platforms.split(","), orientation=a.orientation, max_duration=a.max_duration,
                 region=a.region, language=a.language, music=a.music, music_file=a.music_file,
                 source_audio=a.source_audio, online=a.online, subtitles=a.subtitles, key_moments=a.key_moments,
                 speech_language=a.speech_language, hdr=a.hdr, fps=a.fps, broll_dir=a.broll_dir,
                 voiceover=a.voiceover, voice=a.voice, voice_speed=a.voice_speed, copy_pack=a.copy_pack,
                 framing=a.framing)


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
    from .config import load
    load()
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


def _config(a) -> int:
    from . import config
    if a.action == "show":
        for k, v in config.read().items():
            print(f"{k}={v}")
        print(f"(file: {config.PATH})")
        return 0
    if a.action in ("set", "unset"):
        if not a.key or (a.action == "set" and not a.value):
            raise SystemExit("usage: nookcut config set NOOKCUT_KEY VALUE | nookcut config unset NOOKCUT_KEY")
        print(config.write(a.key, a.value if a.action == "set" else None))
        return 0
    # kokoro: find the Python and models, test-speak a line, then save the command
    import os
    from . import voice
    models = os.path.expanduser(a.models or "~/.local/share/kokoro-tts")
    python = os.path.expanduser(a.python or os.path.join(models, ".venv", "bin", "python"))
    if not os.path.isfile(python):
        raise SystemExit(f"No Python at {python}. Pass --python with the Python that has kokoro-onnx installed.")
    cmd = voice.kokoro_command(python, models)
    os.environ["NOOKCUT_TTS"], os.environ["NOOKCUT_TTS_COMMAND"] = "command", cmd
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        print("Testing Kokoro (the first line takes a few seconds)...")
        voice.speak("NookCut voiceover test.", "", 1.0, os.path.join(d, "t.wav"), language="en")
    config.write("NOOKCUT_TTS", "command")
    path = config.write("NOOKCUT_TTS_COMMAND", cmd)
    print(f"Kokoro works. Saved to {path}.\nRestart the shared server so it uses it: nookcut service restart")
    return 0


def _models(a) -> int:
    from . import models
    if a.action == "list":
        for m in models.listing():
            mark = "in use" if m["in_use"] else ("installed" if m["installed"] else "")
            print(f"  {m['name']:<15} {m['size_mb']:>5} MB  {mark:<9}  {m['about']}")
        print(f"\nFolder: {models.models_dir()}\nInstall one with: nookcut models install [NAME]  "
              f"(default {models.DEFAULT})")
        return 0
    if a.action == "remove":
        if not a.name:
            raise SystemExit("Which model? e.g. nookcut models remove base")
        print("Removed." if models.remove(a.name) else "That model isn't installed.")
        return 0
    name = a.name or models.DEFAULT
    size = models.WHISPER[name][1] / 1e6 if name in models.WHISPER else 0
    print(f"Downloading the {name} speech model ({size:.0f} MB) from huggingface.co (whisper.cpp). "
          "This is the only download NookCut makes; after it, speech captions work offline.", file=sys.stderr)

    def bar(done, total):
        pct = done * 100 // max(total, 1)
        print(f"\r  {pct:3d}%  {done / 1e6:6.0f} / {total / 1e6:.0f} MB", end="", file=sys.stderr, flush=True)
    path = models.install(name, bar)
    print(f"\nInstalled: {path}\nSpeech captions are now on for new analyses (subtitles: auto).", file=sys.stderr)
    return 0


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nookcut", description="Raw recording in, viral short out.")
    ap.add_argument("--version", action="version", version=f"nookcut {__version__}")
    ap.add_argument("-v", "--verbose", action="store_true", help="show full error details")
    ap.add_argument("-q", "--quiet", action="store_true", help="no progress bar")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    n = sub.add_parser("new"); n.add_argument("project"); _brief_args(n)
    m = sub.add_parser("make"); m.add_argument("project"); _brief_args(m)
    m.add_argument("--template"); m.add_argument("--name", default="", help="output file name (no extension)")
    sc = sub.add_parser("script", help="make a video from a script: narrated, over your B-roll")
    sc.add_argument("project"); sc.add_argument("--script", required=True, help="text file, one line per shot")
    sc.add_argument("--title", required=True)
    for flag, default in (("--orientation", "vertical"), ("--language", "en"), ("--voice", ""), ("--music", "ai"),
                          ("--broll-dir", ""), ("--region", ""), ("--type", "generic"), ("--name", ""), ("--fps", "auto"),
                          ("--platforms", "youtube_shorts,tiktok,instagram_reels")):
        sc.add_argument(flag, default=default)
    sc.add_argument("--voice-speed", type=float, default=1.0)
    cp = sub.add_parser("copy", help="built-in offline captions, titles and hashtags")
    cp.add_argument("action", choices=("show", "init", "install", "packs"))
    cp.add_argument("target", nargs="?", default="", help="project (show), language (init) or pack file (install)")
    md = sub.add_parser("models", help="optional local models (speech-to-text); the only thing NookCut downloads")
    md.add_argument("action", choices=("list", "install", "remove")); md.add_argument("name", nargs="?", default="")
    fr = sub.add_parser("frames", help="a sheet of frames from the last render (or --source) to look at")
    fr.add_argument("project"); fr.add_argument("--at", action="append", default=[], help="a time, e.g. 1:30 (repeatable)")
    fr.add_argument("--count", type=int, default=8); fr.add_argument("--source", action="store_true")
    cf = sub.add_parser("config", help="settings file (~/.config/nookcut/config.env): show, set, unset, kokoro")
    cf.add_argument("action", choices=("show", "set", "unset", "kokoro"))
    cf.add_argument("key", nargs="?", default=""); cf.add_argument("value", nargs="?", default="")
    cf.add_argument("--python", default="", help="kokoro: the Python that has kokoro-onnx")
    cf.add_argument("--models", default="", help="kokoro: folder with kokoro-v1.0.onnx and voices-v1.0.bin")
    vs = sub.add_parser("voices", help="voices that can speak voiceover lines")
    vs.add_argument("--language", default="")
    bl = sub.add_parser("broll", help="list the project's B-roll files")
    bl.add_argument("project")
    vr = sub.add_parser("verify", help="check the last render: size, length, loudness, black/frozen picture")
    vr.add_argument("project"); vr.add_argument("--video", default="")
    tr = sub.add_parser("transcript", help="what is said in the recording (after analyze)")
    tr.add_argument("project"); tr.add_argument("--start", type=float, default=0); tr.add_argument("--end", type=float, default=0)
    for name in ("analyze", "preview", "render"):
        sub.add_parser(name).add_argument("project")
    pp = sub.add_parser("plan"); pp.add_argument("project"); pp.add_argument("--template")
    fm = sub.add_parser("framing", help="switch a project between band (blurred bands) and full (fills the frame)")
    fm.add_argument("project"); fm.add_argument("framing", help="band | full")
    mu = sub.add_parser("music"); mu.add_argument("project"); mu.add_argument("--style")
    rs = sub.add_parser("resolve"); rs.add_argument("project"); rs.add_argument("--render", default="")
    rs.add_argument("--proxy", action="store_true", help="also a ProRes proxy of the recording for Resolve")
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
        if a.action == "packs":
            from .copywriting import list_packs, packs_dir
            for pk in list_packs():
                print(f"  {pk['name']:<24} {pk['version']:<8} {pk['about']}")
            print(f"(folder: {packs_dir()})")
            return 0
        if a.action == "install":
            from .copywriting import install_pack
            name = install_pack(a.target)
            print(f"Installed the '{name}' pack. Use it with --copy-pack {name} (or copy_pack: \"{name}\").")
            return 0
        if a.action == "init":
            from .copywriting import init_pack
            path = init_pack(a.target)
            print(f"Language pack: {path}\nTranslate the values (keep the keys and {{slots}}); missing keys fall back "
                  "to English. Set the project's language to use it.")
        else:
            _print(pl.suggest_copy(a.target))
        return 0
    if a.cmd == "models":
        return _models(a)
    if a.cmd == "frames":
        print(pl.frame_sheet(a.project, a.at or None, a.source, a.count)["frames"])
        return 0
    if a.cmd == "config":
        return _config(a)
    if a.cmd == "voices":
        from . import voice
        st = voice.status()
        print(f"Engine: {st['engine']}" + ("" if st["ready"] else f" (not working: {st.get('how')})"))
        for v in (voice.command_voices() if voice.engine() == "command" else voice.voices()):
            if not a.language or v["language"].lower().startswith(a.language.lower()[:2]):
                print(f"  {v['name']:<32} {v['language']}")
        return 0
    if a.cmd == "broll":
        for f in pl.broll_files(a.project):
            kind = "picture" if f["still"] else f"{f['duration']:.1f} s"
            print(f"  {f['file']:<40} {kind}")
        return 0
    if a.cmd == "verify":
        r = pl.verify_render(a.project, a.video or None)
        _print(r)
        return 0 if r["ok"] else 1
    if a.cmd == "transcript":
        for x in pl.transcript(a.project, a.start, a.end)["sentences"]:
            print(f"{x['start']:8.2f}  {x['text']}")
        return 0
    if a.cmd == "check":
        r = check()
        if r["ok"]:
            from .preflight import hdr_status
            r["hdr"] = hdr_status()
        _print(r); return 0 if r["ok"] else 1
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
    if a.cmd in ("new", "make", "script", "analyze", "plan", "preview", "music", "render", "resolve"):
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
    elif a.cmd == "framing":
        _print(pl.set_framing(a.project, a.framing))
    elif a.cmd == "preview":
        print(_step(pl.make_preview, "preview", a.project, a.quiet))
    elif a.cmd == "music":
        print(_step(pl.make_music, "music", a.project, a.quiet, a.style))
    elif a.cmd == "render":
        print(_step(pl.make_render, "render", a.project, a.quiet))
    elif a.cmd == "resolve":
        _print(_step(pl.make_resolve, "resolve", a.project, a.quiet, a.render, a.proxy) if a.proxy
               else pl.make_resolve(a.project, a.render))
    elif a.cmd == "when":
        _print(posting_windows(a.region, a.tz))
    elif a.cmd == "script":
        with open(a.script, encoding="utf-8") as f:
            text = f.read()
        b = Brief(title=a.title, source="", video_type=a.type, orientation=a.orientation, language=a.language,
                  music=a.music, broll_dir=a.broll_dir, region=a.region, voice=a.voice, voice_speed=a.voice_speed,
                  fps=a.fps, platforms=a.platforms.split(","))
        task, ui = _task("script", a.project, a.quiet)
        try:
            _print(pl.make_from_script(a.project, b, text, a.name or None, task=task))
        finally:
            if ui:
                ui.close()
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
