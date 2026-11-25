"""NookCut MCP server: media tools for any MCP-capable agent platform.

Everything works offline: captions, titles and hashtags come from a built-in library, and
auto_edit makes a whole video in one call for small/local models. Web research by the agent is
optional and only when the user allows it (brief.online). The playbook is
served by this server as the `nookcut-editor` prompt and the `nookcut://skill` resource, so it
reaches every client, including ones without a skills system.

Run:
  nookcut-mcp                                   stdio (desktop/CLI agents)
  nookcut-mcp --transport http --port 8765      Streamable HTTP at http://127.0.0.1:8765/mcp
  NOOKCUT_TOKEN=... nookcut-mcp --transport http --host 0.0.0.0   remote (token required)

Environment:
  NOOKCUT_TOKEN          bearer token required on every HTTP request (mandatory off-localhost)
  NOOKCUT_TOKEN_FILE     read the token from this file instead (used by `nookcut service`)
  NOOKCUT_ALLOWED_DIRS   os.pathsep-separated folders the tools may read/write (recommended for HTTP)
"""
from __future__ import annotations

import argparse
import functools
import hmac
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

try:  # mcp >= 2
    from mcp.server.mcpserver import Image, MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server, Image
    from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__
from . import edit as ed
from . import qc
from . import pipeline as pl
from .agents import skill_body, skill_text
from .brief import Brief
from .errors import Cancelled, EnvironmentNotReady, InputError, as_nookcut
from .preflight import check
from .publish import posting_windows
from .tasks import Task, cancel_all, install_signal_handlers, request_cancel, run

WORKFLOW = ("check_environment -> create_project -> analyze_video -> plan_edit -> suggest_copy (or web "
            "research, only if the user allowed online) + update_edit -> preview_plan (get user approval) -> "
            "generate_music -> render_video -> export_resolve -> best_posting_times -> write_publish_kit")

_kwargs = {"instructions": (
    "NookCut turns raw recordings into short-form videos (Shorts, TikTok, Reels), fully offline. "
    "Quickest path: auto_edit(source, title) makes the finished video and publish kit in one call. "
    f"Step by step: {WORKFLOW}. Load the 'nookcut-editor' prompt or read resource nookcut://skill for the full playbook. "
    "Long steps accept wait=false and return a job_id: poll job_status to show the user progress (percent, "
    "stage, ETA) and use cancel_job if they want to stop. Errors carry a plain-language message and a hint: "
    "relay them to the user. Framing is band (blurred bands) by default; use framing='full' (picture fills "
    "the frame) only when the user asks, and aim each shot with update_edit focus. Never post on the user's behalf.")}
try:
    mcp = _Server("nookcut", version=__version__, **_kwargs)
except TypeError:
    mcp = _Server("nookcut", **_kwargs)

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


def _explain(e: BaseException) -> str:
    err = as_nookcut(e)
    text = err.message
    if err.hint:
        text += f"\nWhat to do: {err.hint}"
    if err.detail:
        text += f"\nDetails (for bug reports):\n{err.detail}"
    return f"[{err.code}] {text}"


def _tool(**kw):
    """mcp.tool with every failure turned into a plain-language message the agent can relay."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **k):
            try:
                return fn(*a, **k)
            except Exception as e:  # ToolError: the SDK passes our text to the agent (others are masked)
                raise ToolError(_explain(e)) from e
        return mcp.tool(**kw)(wrapper)
    return deco
WRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)


# ---------- safety ----------

def _allowed_dirs() -> list[str]:
    raw = os.getenv("NOOKCUT_ALLOWED_DIRS", "")
    return [os.path.realpath(os.path.expanduser(d)) for d in raw.split(os.pathsep) if d.strip()]


def _path(p: str, must_exist: bool = False) -> str:
    """Resolve a user path and enforce NOOKCUT_ALLOWED_DIRS when set."""
    if not p:
        return p
    real = os.path.realpath(os.path.expanduser(p))
    allowed = _allowed_dirs()
    if allowed and not any(real == d or real.startswith(d + os.sep) for d in allowed):
        raise InputError(f"{p} is outside the folders this server may use.",
                         "Use a path inside NOOKCUT_ALLOWED_DIRS, or ask the server owner to allow it.")
    if must_exist and not os.path.exists(real):
        raise InputError(f"Not found: {p}", "Check the path and that the drive is mounted.")
    return real


def _ready() -> None:
    env = check()
    if not env["ok"]:
        raise EnvironmentNotReady(f"This computer is missing what NookCut needs: {', '.join(env['missing'])}.",
                                  f"Ask the user to install it: {env['install']}")


# ---------- background jobs (for clients with short tool timeouts) ----------

_pool = ThreadPoolExecutor(max_workers=2)
_jobs: dict[str, dict] = {}
_lock = threading.Lock()
KEEP_FINISHED_S = 3600


def _run(name: str, project: str, fn, wait: bool):
    """Run fn(task) as a tracked job: inline when wait=True, else in the background with a job_id.
    Either way it shows in job_status, can be cancelled, and writes progress for `nookcut watch`."""
    job_id = uuid.uuid4().hex[:12]
    task = Task(name, project)
    with _lock:
        now = time.time()
        for k in [k for k, j in _jobs.items() if j.get("finished") and now - j["finished"] > KEEP_FINISHED_S]:
            del _jobs[k]
        _jobs[job_id] = {"job_id": job_id, "tool": name, "project": project, "status": "running",
                         "started": now, "task": task}

    def work():
        exc = None
        try:
            update = {"status": "done", "result": fn(task)}
        except Exception as e:
            exc = as_nookcut(e)
            update = {"status": "cancelled" if isinstance(exc, Cancelled) else "error", "error": exc.to_dict()}
        with _lock:
            _jobs[job_id].update(update, finished=time.time())
        return update, exc

    if wait:
        update, exc = work()
        if exc:
            raise exc
        return update["result"]
    _pool.submit(work)
    return {"job_id": job_id, "status": "running",
            "next": "poll job_status with this job_id and tell the user the percent/stage; cancel_job stops it",
            "watch": f"the user can also watch live in a terminal: nookcut watch '{project}'"}


def _view(job: dict) -> dict:
    view = {k: v for k, v in job.items() if k != "task"}
    view["elapsed_s"] = round((job.get("finished") or time.time()) - job["started"], 1)
    if job["status"] == "running":
        snap = job["task"].snapshot()
        view["progress"] = {k: snap[k] for k in ("stage", "step", "percent", "stage_percent", "note",
                                                 "eta_s", "stage_eta_s") if snap.get(k) not in (None, "")}
    return view


@_tool(annotations=READ_ONLY)
def job_status(job_id: str = "") -> dict:
    """Progress of a job: status running | done (with result) | error | cancelled. While running,
    `progress` has percent, stage, step (e.g. 2/4), note and eta_s; show these to the user.
    With no job_id, lists recent jobs."""
    with _lock:
        if not job_id:
            return {"jobs": [_view(j) for j in _jobs.values()]}
        job = _jobs.get(job_id)
        if not job:
            raise InputError(f"There is no job with id {job_id}.", "Call job_status with no id to list jobs.")
        return _view(job)


@_tool(annotations=WRITES)
def cancel_job(job_id: str = "", project_dir: str = "") -> dict:
    """Stop a running job right away and end its ffmpeg processes (frees the CPU). Pass the job_id,
    or project_dir to stop whatever is running on that project (also jobs started from the CLI)."""
    with _lock:
        job = _jobs.get(job_id) if job_id else next(
            (j for j in _jobs.values() if j["status"] == "running" and project_dir
             and j["project"] == _path(project_dir)), None)
    if job and job["status"] == "running":
        job["task"].cancel()
        return {"cancelled": True, "job_id": job["job_id"], "tool": job["tool"]}
    if project_dir and request_cancel(_path(project_dir)):
        return {"cancelled": True, "project": project_dir, "note": "asked the running process to stop"}
    if job:
        return {"cancelled": False, "status": job["status"], "note": "the job had already finished"}
    raise InputError("Nothing is running for that job or project.", "Call job_status to list jobs.")


# ---------- tools ----------

@_tool(annotations=READ_ONLY)
def check_environment() -> dict:
    """Step 0. Check ffmpeg, filters, encoders and a caption font. If ok is false, show the user
    `missing` and the `install` command and wait. Nothing needs the internet."""
    env = check()
    if env["ok"]:
        from .preflight import hdr_status
        from .voice import status as voice_status
        env["hdr"] = hdr_status()  # what happens to HDR recordings here
        env["voiceover"] = voice_status()  # optional: the voice that speaks voiceover lines
    env["offline_ready"] = env["ok"]
    env["online_optional"] = ["trend research by your agent's own web search, only if the user allows it"]
    env["next"] = ("create_project, or auto_edit for the whole video in one call" if env["ok"]
                   else "show the user the install command and wait for them")
    return env


def _brief(source, title, video_type, game, orientation, max_duration, region, language, music, music_file,
           source_audio, platforms, key_moments, online, subtitles="auto", speech_language="auto",
           hdr="sdr", fps="auto", broll_dir="", voiceover="off", voice="", voice_speed=1.0, copy_pack="",
           framing="band") -> Brief:
    if not video_type:
        video_type = "gameplay" if game else "generic"
    return Brief(title=title, source=_path(source, must_exist=True), video_type=video_type, game=game,
                 orientation=orientation, max_duration=max_duration, region=region, language=language or "en",
                 music=music, music_file=_path(music_file, must_exist=bool(music_file)), source_audio=source_audio,
                 platforms=platforms or ["youtube_shorts", "tiktok", "instagram_reels"],
                 key_moments=key_moments or [], online=online, subtitles=subtitles,
                 speech_language=speech_language or "auto", hdr=hdr or "sdr", fps=fps or "auto",
                 broll_dir=_path(broll_dir, must_exist=bool(broll_dir)), voiceover=voiceover or "off",
                 voice=voice or "", voice_speed=voice_speed or 1.0, copy_pack=copy_pack or "",
                 framing=framing or "band")


@_tool(annotations=WRITES)
def create_project(project_dir: str, source: str, title: str, video_type: str = "", game: str = "",
                   orientation: str = "vertical", max_duration: float | str = 60, region: str = "",
                   language: str = "en", music: str = "ai", music_file: str = "", source_audio: str = "duck",
                   platforms: list[str] | str | None = None, key_moments: list[dict] | None = None,
                   online: bool = False, subtitles: str = "auto", speech_language: str = "auto",
                   hdr: str = "sdr", fps: str = "auto", broll_dir: str = "", voiceover: str = "off",
                   voice: str = "", voice_speed: float = 1.0, copy_pack: str = "", framing: str = "band") -> dict:
    """Step 1. Save the brief into project_dir. video_type: gameplay|talking|sports|tutorial|generic
    (empty = gameplay if game is set). orientation: vertical|square|wide. music: ai|none|file.
    source_audio: keep|duck|mute. platforms: youtube_shorts, tiktok, instagram_reels, facebook_reels,
    youtube, x. key_moments: [{"t": seconds, "label": "..."}] or ["10:42=final blow"]. online: true only if the user allowed
    web research. subtitles: auto|on|off word-by-word captions of what is said (auto = when a speech
    model is installed). speech_language: auto or a code like en, es, hi. hdr (HDR sources only):
    sdr = convert to normal colour, plays right everywhere (default); keep = HDR10/HLG HEVC output,
    only when the user asks for HDR. fps: auto (60, matching 59.94/50 sources) | 60 | 30; high
    frame rate sources (120 fps) give real-frame slow motion either way. broll_dir: a folder of the
    user's own clips/pictures to show as B-roll (never downloaded). voiceover: off | captions (the
    hook and end captions read aloud; write other lines yourself with update_edit "vo"). voice: a
    name from list_voices (empty = the best for the language); voice_speed 0.7-1.5. copy_pack: a
    caption/title style pack the user installed (suggest_copy lists the installed ones). framing: band
    (default: a sharp band over a blurred copy) | full (the picture fills the whole frame, no blur or
    borders; aim each shot with update_edit focus), only when the user picks it. Relay any warnings."""
    _ready()
    b = _brief(source, title, video_type, game, orientation, max_duration, region, language, music, music_file,
               source_audio, platforms, key_moments, online, subtitles, speech_language, hdr, fps, broll_dir,
               voiceover, voice, voice_speed, copy_pack, framing)
    out = pl.new_project(_path(project_dir), b)
    out["next"] = "analyze_video (wait=false, then poll job_status)"
    return out


@_tool(annotations=WRITES)
def auto_edit(source: str, title: str, project_dir: str = "", video_type: str = "", game: str = "",
              orientation: str = "vertical", max_duration: float | str = 60, region: str = "",
              language: str = "en", music: str = "ai", source_audio: str = "duck",
              platforms: list[str] | str | None = None, key_moments: list[dict] | None = None,
              file_name: str = "", subtitles: str = "auto", template: str = "", hdr: str = "sdr",
              broll_dir: str = "", voiceover: str = "off", voice: str = "", copy_pack: str = "",
              framing: str = "band", wait: bool = True) -> dict:
    """Make the finished short in ONE call, offline: analyze, plan, captions, original music,
    render, DaVinci Resolve files and publish kit. Only source and title are needed. Best path for
    small or local models. Speech is captioned word by word when a speech model is installed
    (subtitles: auto|on|off). template: empty = auto, or "tighten" to keep the whole recording and
    cut out its pauses (jump cuts). HDR recordings are converted to normal colour; hdr="keep" keeps
    HDR10/HLG instead. framing="full" (only when the user asks for it) fills the frame with the centre of the picture (no blurred
    bands); check the result, since the action may sit off-centre. Takes a few minutes; wait=false
    returns a job_id for job_status."""
    _ready()
    b = _brief(source, title, video_type, game, orientation, max_duration, region, language, music, "",
               source_audio, platforms, key_moments, False, subtitles, hdr=hdr, broll_dir=broll_dir,
               voiceover=voiceover, voice=voice, copy_pack=copy_pack, framing=framing)
    project = _path(project_dir) or os.path.join(os.path.dirname(b.source),
                                                  pl.slug(title).lower() + "-nookcut")

    def work(t):
        out = pl.make_all(project, b, template or None, file_name or None, task=t)
        out["project"] = project
        out["next"] = ("Show the user the video and publish_kit. To change captions: suggest_copy + "
                       "update_edit, then render_video.")
        return out
    return _run("auto_edit", project, work, wait)


@_tool(annotations=WRITES)
def script_to_video(script: str, title: str, project_dir: str = "", broll_dir: str = "",
                    orientation: str = "vertical", language: str = "en", voice: str = "", voice_speed: float = 1.0,
                    music: str = "ai", region: str = "", platforms: list[str] | str | None = None,
                    video_type: str = "generic", file_name: str = "", copy_pack: str = "", wait: bool = True) -> dict:
    """Make a video from a script, offline: each line is narrated (list_voices), shown over the
    user's own B-roll from broll_dir (a clip named after a word in the line first) or a moving
    background, with word captions, original music, checks, Resolve files and a publish kit.
    One line per shot. Use the user's words; don't invent facts. Running it again rebuilds the
    project's narration and edit. wait=false returns a job_id for job_status."""
    _ready()
    b = Brief(title=title, source="", video_type=video_type or "generic", orientation=orientation,
              region=region, language=language or "en", music=music,
              platforms=platforms or ["youtube_shorts", "tiktok", "instagram_reels"],
              broll_dir=_path(broll_dir, must_exist=bool(broll_dir)), voice=voice or "", voice_speed=voice_speed or 1.0,
              copy_pack=copy_pack or "")
    project = _path(project_dir) or _path(os.path.join(os.path.expanduser("~/NookCut"), pl.slug(title).lower()))

    def work(t):
        out = pl.make_from_script(project, b, script, file_name or None, task=t)
        out["project"] = project
        out["next"] = ("Show the user the video, checks and publish_kit. Change B-roll or captions with "
                       "update_edit, then render_video; change the words by running script_to_video again.")
        return out
    return _run("script_to_video", project, work, wait)


@_tool(annotations=READ_ONLY)
def suggest_copy(project_dir: str, count: int = 5) -> dict:
    """Offline caption options for each segment, plus titles, a description, hashtags and a file
    name, from NookCut's built-in library (no internet). Apply picks with update_edit."""
    out = pl.suggest_copy(_path(project_dir, must_exist=True), count)
    from .copywriting import list_packs
    out["installed_packs"] = [p_["name"] for p_ in list_packs()]
    out["next"] = ("Apply chosen captions with update_edit ({'i': n, 'top': ...}), then preview_plan; use "
                   "titles/description/hashtags in write_publish_kit.")
    return out


@_tool(annotations=WRITES)
def analyze_video(project_dir: str, wait: bool = True) -> dict:
    """Step 2. Scan the recording (1-3 min for a 10-min 4K file): deaths, win, best moments, scene
    cuts, silent audio, and what is said (when a speech model is installed; see `speech` in the result).
    wait=false returns a job_id for job_status."""
    _ready()
    p = _path(project_dir, must_exist=True)

    def work(t):
        out = pl.run_analysis(p, t)
        out["next"] = "plan_edit"
        return out
    return _run("analyze_video", p, work, wait)


@_tool(annotations=READ_ONLY)
def get_transcript(project_dir: str, start: float = 0, end: float = 0) -> dict:
    """What is said in the recording, as sentences with source times (seconds), optionally only
    between start and end. Use it to find quotes for the hook, captions or titles, and to move
    cuts (update_edit src_in/src_out) so they don't chop a sentence."""
    return pl.transcript(_path(project_dir, must_exist=True), float(start), float(end))


@_tool(annotations=WRITES)
def plan_edit(project_dir: str, template: str = "") -> dict:
    """Step 3. Build the edit from a story template (comeback | victory | highlights; empty = auto),
    with captions from the built-in library. "tighten" instead keeps the whole recording and cuts out
    its pauses (jump cuts for talks, podcasts, tutorials); the analysis `pauses` line says if it fits."""
    out = pl.make_plan(_path(project_dir, must_exist=True), template or None)
    out["next"] = "suggest_copy for other caption options (or keep these), then preview_plan"
    return out


@_tool(annotations=READ_ONLY)
def get_edit(project_dir: str) -> dict:
    """Return the current edit as a readable list of segments."""
    return pl.describe(ed.load(pl.paths(_path(project_dir, must_exist=True))["edit"]))


@_tool(annotations=WRITES)
def update_edit(project_dir: str, changes: list[dict], music_style: str = "", framing: str = "") -> dict:
    """Change segments. Each change: {"i": index, ...fields} to update (top, bottom, src_in, src_out,
    speed, zoom, kind, words, focus, focus_end), {"i": index, "delete": true}, or
    {"insert_at": index, "segment": {...}}. framing: band | full for the whole video, only when the user
asks (e.g. "full screen, no blur" or "back to the blurred style"); then render again. With framing full,
    focus [x, y] is where the shot is aimed, as fractions of the picture (0.5, 0.5 = middle; x 0.3 =
    left of centre); focus_end [x, y] pans there over the segment (null = no pan); zoom 1-4 frames
    tighter. preview_plan outlines what each shot shows.
    '|' in a caption forces a line break; keep vertical lines <= 16 characters. words are the
    speech captions as [[start, end, "word"], ...] in source seconds: fix a misheard word there,
    or set [] to hide speech captions on that segment. crop is the black-bar crop [x, y, w, h]
    found by the analysis; set null to keep the bars. broll shows one of the user's B-roll files
    over the picture: {"file": name from list_broll, "layout": "cover"|"inset"|"split",
    "main": "broll"|"source" (inset: which is full-size), "start": s, "end": s (within the
    segment's output time; end null = to its end), "in": s (where the clip starts)}; null removes it.
    vo is a voiceover line spoken over the segment (short: about 2.5 words a second fits); vo_at
    when it starts (seconds into the segment, default 0.1); "vo": null removes it. After moving src_in/src_out, words are
    refilled from the transcript. music_style: taiko | pulse | cinematic."""
    project_dir_real = _path(project_dir, must_exist=True)
    path = pl.paths(project_dir_real)["edit"]
    edit = ed.load(path)
    segs = edit["segments"]
    for c in sorted([c for c in changes if c.get("delete")], key=lambda c: -c["i"]):
        segs.pop(c["i"])
    for c in changes:
        if c.get("delete"):
            continue
        if "insert_at" in c:
            s = c["segment"]
            segs.insert(c["insert_at"], ed.seg(s["src_in"], s["src_out"], s.get("kind", "highlight"),
                                               s.get("speed", 1.0), s.get("top", ""), s.get("bottom", ""),
                                               s.get("zoom", 1.0), s.get("hits"), s.get("impact")))
            for extra in ("words", "crop", "broll", "vo", "vo_at", "focus", "focus_end"):   # seg() keeps only the basic fields
                if extra in s:
                    segs[c["insert_at"]][extra] = s[extra]
            if "words" not in s:
                pl.refill_words(project_dir_real, segs[c["insert_at"]])
        else:
            segs[c["i"]].update({k: v for k, v in c.items() if k != "i"})
            if ("src_in" in c or "src_out" in c) and "words" not in c:
                pl.refill_words(project_dir_real, segs[c["i"]])
    if music_style:
        edit["music_style"] = music_style
    errors = ed.validate(edit, edit.get("source_duration"))
    if errors:
        raise ValueError("; ".join(errors))
    ed.save(edit, path)
    out = pl.set_framing(project_dir_real, framing) if framing else pl.describe(edit)
    out["next"] = "preview_plan to check the frames, then get the user's approval"
    return out


@_tool(annotations=WRITES)
def preview_plan(project_dir: str) -> list:
    """Step 4. Contact sheet: one frame per segment with its caption. Returned as an image you can
    look at, plus the full-size PNG path. Show the user and get approval before rendering."""
    project = _path(project_dir, must_exist=True)
    png = os.path.abspath(_run("preview_plan", project, lambda t: pl.make_preview(project, t), True))
    small = os.path.join(project, "preview_small.jpg")
    run(["ffmpeg", "-v", "error", "-y", "-i", png, "-vf", "scale=1280:-2", "-q:v", "4", small],
        step="shrinking the preview")
    return [Image(path=small), f"Full-size preview: {png}\nNext: show the user and get approval, then render_video."]


@_tool(annotations=WRITES)
def generate_music(project_dir: str, style: str = "") -> str:
    """Compose the original score synced to the cuts (or master the user's music file).
    style: taiko (samurai/martial) | pulse (shooters/sci-fi/esports) | cinematic (bosses/epic/sports)."""
    p = _path(project_dir, must_exist=True)
    return _run("generate_music", p, lambda t: pl.make_music(p, style or None, t), True) or "music disabled in brief"


@_tool(annotations=WRITES)
def render_video(project_dir: str, file_name: str = "", wait: bool = True) -> dict:
    """Step 5. Render the final video (~20-60 s per minute of output). file_name: suggest_copy's
    file_name or your own. wait=false returns a job_id for job_status."""
    _ready()
    p = _path(project_dir, must_exist=True)

    def work(t):
        video = pl.make_render(p, file_name or None, t)
        checks = pl.verify_render(p, video, t)
        return {"video": os.path.abspath(video), "checks": {k: checks[k] for k in ("ok", "issues", "facts", "frames")},
                "next": ("fix the issues in checks first (tell the user), or " if checks["issues"] else "") +
                        "verify_video to look at the frames, then export_resolve and write_publish_kit"}
    return _run("render_video", p, work, wait)


def _sheet(path: str) -> Image:
    """A frame sheet, shrunk for the conversation (the full-size file stays on disk)."""
    small = path[:-4] + "_small.jpg"
    run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vf", "scale='min(1600,iw)':-2", "-q:v", "4", small],
        step="shrinking the frame sheet")
    return Image(path=small)


@_tool(annotations=READ_ONLY)
def list_voices(language: str = "") -> dict:
    """Voices on this computer that can speak voiceover lines (name, language), optionally only one
    language (e.g. en, es). The engine is set on the server; check_environment shows it."""
    from . import voice
    names = voice.command_voices() if voice.engine() == "command" else voice.voices()
    if language:
        names = [v for v in names if v["language"].lower().startswith(language.lower()[:2])]
    return {"engine": voice.status()["engine"], "voices": names,
            "suggested": voice.pick_voice(language or "en") if voice.engine() == "system" else None}


@_tool(annotations=READ_ONLY)
def list_broll(project_dir: str, sheet: bool = True) -> list:
    """The user's own B-roll files (the brief's broll_dir): name, clip or still, length. With
    sheet=true also a labelled picture of each (first 24) so you can choose what fits. Put one on a
    segment with update_edit {"i": n, "broll": {"file": name, ...}}. Never suggest downloading."""
    p = _path(project_dir, must_exist=True)
    files = pl.broll_files(p)
    out = [json.dumps({"files": files, "next": "update_edit with a broll on the segments where they fit"})]
    if sheet and files:
        from .brief import Brief
        folder = Brief.load(pl.paths(p)["brief"]).broll_dir
        shown = files[:24]
        path = qc.thumbnails([(os.path.join(folder, f["file"]), 0.0 if f["still"] else min(1.0, (f["duration"] or 0) / 2),
                               f["file"]) for f in shown], os.path.join(p, "broll.jpg"))
        out.insert(0, _sheet(path))
    return out


@_tool(annotations=READ_ONLY)
def get_frames(project_dir: str, times: list[float | str] | None = None, source: bool = False, count: int = 8) -> list:
    """Look at the video: a labelled grid of frames from the last render, or from the original
    recording with source=true. times: seconds or "m:ss" in that file; empty = `count` frames spread
    evenly (up to 24). Use it to check captions, crops and moments yourself; if you can't see
    images, use verify_video's numbers instead."""
    out = pl.frame_sheet(_path(project_dir, must_exist=True), times or None, source, count)
    return [_sheet(out["frames"]), json.dumps(out)]


@_tool(annotations=READ_ONLY)
def verify_video(project_dir: str, video_path: str = "") -> list:
    """Check a render (default: the last one): size, frame rate, length against the plan, colour,
    loudness (LUFS, true peak), black or frozen picture and silence. issues lists what to fix or tell
    the user; also returns one frame per segment as an image. render_video runs this already."""
    p = _path(project_dir, must_exist=True)
    out = pl.verify_render(p, _path(video_path) or None)
    out["next"] = ("tell the user about each issue and offer a fix (update_edit, then render_video)"
                   if out["issues"] else "all checks passed; look at the frames, then export_resolve")
    return [_sheet(out["frames"]), json.dumps(out)]


@_tool(annotations=WRITES)
def export_resolve(project_dir: str, render_path: str = "") -> dict:
    """Write EDLs + a Lua script that rebuilds the edit as an editable DaVinci Resolve timeline
    (free Resolve: Workspace > Console > Lua). Returns how to run it."""
    out = pl.make_resolve(_path(project_dir, must_exist=True), _path(render_path))
    out["next"] = "write_publish_kit"
    return out


@_tool(annotations=READ_ONLY)
def best_posting_times(region: str, creator_timezone: str = "") -> dict:
    """Region-aware posting windows for the next 7 days, in the audience's and the creator's time
    (IANA zone like Asia/Dhaka; default = this machine). Labeled estimates; relay the analytics pointers."""
    return posting_windows(region, creator_timezone or None)


@_tool(annotations=WRITES)
def write_publish_kit(project_dir: str, video_path: str, titles: list[str] | None = None, description: str = "",
                      hashtags: list[str] | None = None, research_notes: str = "", creator_timezone: str = "") -> str:
    """Step 6. Save publish_kit.md: titles, description, hashtags, posting plan and analytics
    pointers. Anything left empty is filled from the built-in library. Never post for the user."""
    return os.path.abspath(pl.make_kit(_path(project_dir, must_exist=True), _path(video_path), titles or None,
                                       description, hashtags or None, research_notes, creator_timezone or None))


# ---------- the skill, for every client ----------

@mcp.resource("nookcut://skill", name="nookcut-editor skill", mime_type="text/markdown",
              description="The full NookCut editing + research playbook (SKILL.md).")
def skill_resource() -> str:
    return skill_text()


@mcp.prompt(name="nookcut-editor", description="Edit a raw recording into a researched short-form video.")
def make_short(source: str, title: str, game: str = "", region: str = "", max_duration: str = "60",
               orientation: str = "vertical") -> str:
    brief = "\n".join(f"- {k}: {v}" for k, v in {
        "source": source, "title": title, "game": game, "region": region,
        "max_duration": max_duration, "orientation": orientation}.items() if v)
    return f"{skill_body()}\n\n---\n\nMake a short from this brief (ask only for what is missing):\n{brief}\n"


# ---------- entry point ----------

class _BearerAuth:
    """ASGI middleware: require Authorization: Bearer <NOOKCUT_TOKEN> on every HTTP request."""

    def __init__(self, app, token: str):
        self.app, self.token = app, token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            got = headers.get(b"authorization", b"")
            if not hmac.compare_digest(got, b"Bearer " + self.token):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain"), (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self.app(scope, receive, send)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="nookcut-mcp", description="NookCut MCP server")
    ap.add_argument("--transport", choices=("stdio", "http"), default=os.getenv("NOOKCUT_TRANSPORT", "stdio"))
    ap.add_argument("--host", default=os.getenv("NOOKCUT_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.getenv("NOOKCUT_PORT", "8765")))
    ap.add_argument("--path", default="/mcp")
    a = ap.parse_args(argv)
    install_signal_handlers()
    from .config import load
    load()   # ~/.config/nookcut/config.env: the shared server doesn't see the shell profile
    try:
        _serve(a)
    finally:  # client gone, Ctrl+C or SIGTERM: stop every job and ffmpeg process now
        cancel_all()
        _pool.shutdown(wait=False, cancel_futures=True)


def _serve(a) -> None:
    if a.transport == "stdio":
        mcp.run()
        return
    token = os.getenv("NOOKCUT_TOKEN", "")
    if not token and os.getenv("NOOKCUT_TOKEN_FILE"):
        with open(os.path.expanduser(os.environ["NOOKCUT_TOKEN_FILE"])) as f:
            token = f.read().strip()
    local = a.host in ("127.0.0.1", "localhost", "::1")
    if not local and not token:
        raise SystemExit("Refusing to listen on a non-local address without NOOKCUT_TOKEN.")
    if not local and not _allowed_dirs():
        print("warning: NOOKCUT_ALLOWED_DIRS is not set; remote callers can read any file this user can.")
    import uvicorn
    try:
        app = mcp.streamable_http_app(streamable_http_path=a.path, host=a.host)
    except TypeError:  # mcp 1.x
        mcp.settings.streamable_http_path = a.path
        app = mcp.streamable_http_app()
    if token:
        app = _BearerAuth(app, token)
    print(f"NookCut MCP on http://{a.host}:{a.port}{a.path}" + (" (bearer token required)" if token else ""))
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
