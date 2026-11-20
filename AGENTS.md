# Working on NookCut

Guidance for AI coding agents (and humans) developing this repository. This is contributor
guidance. The playbook NookCut gives agents for *editing videos* is a separate file:
`src/nookcut/skill/SKILL.md`.

## What NookCut is

An MCP server, a CLI and an agent skill that turn a raw recording (gameplay, talking head, sports,
tutorial) into a short-form video built to perform: detect the key moments, plan a story, compose
copyright-free music, render vertical/square/wide with captions, export an editable DaVinci Resolve
timeline, and give region-aware posting times. It works fully offline with any MCP agent (small
local models included): captions, titles and hashtags come from a built-in copy library, and
`auto_edit` makes a whole video in one call. Web research is optional, done by the calling agent
with its own search only when the user allows it; NookCut never does network calls of its own.

## Product rules (decided with the owner; don't change without asking)

- **Free for the user.** No paid APIs, no API keys, no extra subscriptions.
- **Offline first.** Every feature works with no internet and with any local agent. Going online
  (agent web research) is opt-in per project (`brief.online`, default off). Optional local models
  (Whisper today, Piper planned) may be fetched only by a user-run `nookcut models install`, never
  automatically. Python standard library + ffmpeg only at runtime (`mcp` is the one
  optional dependency, for the server).
- **Licensed music only.** AI music is synthesized from scratch in `music.py` (no samples), or the
  user brings their own file. Never download or suggest copyrighted tracks.
- **Never post or upload** on the user's behalf. Hand over files and a publish kit.
- **Generic, not game-specific.** Every feature must work for any topic; game-specific behaviour
  only through the brief (`game`, `key_moments`) and story templates.
- **Check the environment first.** If ffmpeg or a required filter/encoder/font is missing, stop and
  show the user the install command (`preflight.py`); never install things silently.
- **Posting times are estimates** and must be labelled as such, with pointers to real analytics.
- **DaVinci Resolve free edition has no external scripting.** Integration is through files: EDLs and
  a Lua script the user runs from Workspace > Console. Don't build features that need Resolve Studio.
- **Gentle on the user's computer.** All ffmpeg work is cancellable, runs at lower priority and
  stops immediately on cancel, failure, Ctrl+C, SIGTERM or shutdown.

## Layout

```
src/nookcut/
  preflight.py   ffmpeg/ffprobe/filters/encoders/font checks, install hints per OS
  brief.py       Brief: what the user wants (title, game, orientation, max length, region, music...)
  probe.py       ffprobe wrapper; audio peaks
  analyze.py     64x36 @ 2 fps signal scan + scdet scene cuts + cropdetect bars -> deaths/win/moments (no ML)
  story.py       story templates (comeback | victory | highlights) fitted to the length budget
  copywriting.py offline captions/titles/descriptions/hashtags from language packs (copydata/*.json)
  models.py      optional local models (whisper); `nookcut models install` is the only network code
  color.py       HDR: detect HDR10/HLG, tone map to SDR (Apple GPU or zscale), HDR10 output, caption colours
  transcribe.py  speech to text with ffmpeg's whisper filter; quiet parts cut out for accurate times
  subtitles.py   word-by-word .ass captions for the render, .srt for export
  edit.py        the edit.json contract (segments, kinds, validation)
  music.py       procedural score (taiko | pulse | cinematic), mastered with ffmpeg
  render.py      parallel per-segment render + concat + audio mix; preview contact sheet
  export.py      CMX3600 EDLs, markers, Resolve Lua builder
  publish.py     region -> time zone/weekend, posting windows, publish kit
  pipeline.py    project folder layout; each heavy step runs as a locked, tracked job
  errors.py      NookCutError types (message + hint + raw detail), ffmpeg error translation
  tasks.py       Task (progress, cancel), run() for every ffmpeg call, project lock, cleanup
  progress_ui.py CLI progress bar and the `nookcut watch` screen
  mcp_server.py  MCP tools/prompt/resource; stdio + Streamable HTTP; bearer auth; background jobs
  service.py     `nookcut service`: one shared localhost server (launchd / systemd --user)
  agents.py      per-platform MCP configs and skill/rule-file installers
  cli.py         the `nookcut` command
  skill/SKILL.md the editing playbook (canonical copy; see "Skill copies")
tests/test_core.py   fast unit tests (synthetic signals, no decoding)
tests/mcp_smoke.py   real MCP client over stdio and HTTP, optionally a full clip run
```

## Setup

```bash
./scripts/dev-setup.sh          # venv, editable install, PATH links, skill, shared server
```

Or by hand: `python3 -m venv .venv && .venv/bin/pip install -e ".[mcp]"`. Needs ffmpeg with
drawtext (on macOS: `brew install ffmpeg-full` and put it on PATH; plain `ffmpeg` lacks drawtext).

## Check your change

```bash
.venv/bin/python -m unittest discover tests                    # always (about 1 s)
.venv/bin/python tests/mcp_smoke.py                            # after touching mcp_server.py
.venv/bin/python tests/mcp_smoke.py /path/clip.mp4 /tmp/smoke  # after touching media code: full run
nookcut service restart                                        # so the shared server loads new code
```

For render/analyze changes, also run a real clip end to end (`nookcut make ...`) and look at the
output. A 1-3 minute clip is enough; `NOOKCUT_WORKERS=1` makes a render slow enough to test cancel.

## Code conventions

- **Every ffmpeg/ffprobe call goes through `tasks.run()`** with the current `Task`, a human step
  name ("rendering segment 3") and, when the output length is known, a `progress` callback.
  Never call `subprocess` for ffmpeg directly; it would escape cancel and cleanup.
- **Raise `NookCutError` subclasses** (`InputError`, `MediaError`, `EnvironmentNotReady`, `Busy`,
  `Cancelled`) with a plain-language `message` a non-technical user understands and a `hint` that
  says what to do. Raw ffmpeg output goes in `detail`, never in the message. New common ffmpeg
  failures get a pattern in `errors._FFMPEG`.
- MCP tools use `@_tool(...)`, which turns failures into `ToolError` text the agent can relay.
  Heavy tools go through `_run(name, project, fn, wait)` so they show in `job_status` and can be
  cancelled.
- Pipeline steps that decode or encode use `pipeline.job(...)` (project lock + progress + error
  wrapping). Never leave partial outputs behind on failure.
- Match the surrounding style: small functions, short docstrings, no new runtime dependencies.

## Skill copies

`src/nookcut/skill/SKILL.md` is the source of truth. After editing it:

```bash
cp src/nookcut/skill/SKILL.md skills/nookcut-editor/SKILL.md
.venv/bin/python -c "from nookcut.agents import skill_body; open('GEMINI.md','w').write(skill_body())"
nookcut skill install claude      # refresh the installed copy
nookcut service restart           # the server serves it as a prompt and resource
```

A unit test fails if the copies drift. Note: root `GEMINI.md` is the Gemini CLI *extension's*
playbook (see `gemini-extension.json`), not contributor guidance.

## Git

- **Never mention Claude, Codex or any AI agent in commits or branch names**: no `Co-Authored-By`
  lines, no "Generated with" footers, no agent prefixes or suffixes. This is the owner's standing
  rule for this repository.
- Commit or push only when the owner asks. The repository is public (`nookverse/nookcut`, branch
  `main`): never commit personal data, absolute paths from your machine, tokens, footage or renders.
  Use your GitHub noreply address as the commit email.
- Don't publish to PyPI, the MCP Registry or Docker Hub without asking.

## Known gaps

- Not published yet (PyPI name `nookcut`, registry entry `io.github.nookverse/nookcut` in `server.json`).
- The Docker image has never been built or run (the README marks it experimental).
- Windows isn't covered by CI.
- The `c`/`q` keys of `nookcut watch` haven't been tested in a real terminal.
- Speech captions need ffmpeg 8+ with the `whisper` filter; many Linux distro and Windows builds
  lack it (`speech_to_text.how` says so). Several audio tracks are mixed before transcribing, so a
  separate mic track isn't singled out. Hooks don't yet come from the transcript automatically
  (agents can pick quotes with `get_transcript`).
- HDR: GPU tone mapping is Apple-only (`scale_vt`); NVIDIA/Intel/AMD tone-map on the CPU (zscale)
  and still encode on the GPU. NVENC/AMF/QSV/VA-API 10-bit HEVC and their HDR10 metadata are
  untested (test-encoded before use). Moment detection reads HDR brightness without tone mapping.
  Dolby Vision / HDR10+ dynamic metadata are dropped (the base picture is used); Dolby Vision
  profile 5 is refused. Output is at most 60 fps (high frame rate sources feed real-frame slow motion).
- Detection is tuned on action games (death = fade after a red vignette); other genres fall back
  to the generic highlights template.
