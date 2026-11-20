# NookCut

**Raw recording in, finished short out. It runs fully offline and works with any AI agent.**

[![CI](https://github.com/nookverse/nookcut/actions/workflows/ci.yml/badge.svg)](https://github.com/nookverse/nookcut/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![MCP](https://img.shields.io/badge/MCP-server-8A2BE2)

NookCut turns a long recording (gameplay, a talk, a match, a tutorial) into a short-form video for
YouTube Shorts, TikTok and Reels. It finds the moments that matter, cuts them into a story,
composes **original copyright-free music** synced to the cuts, burns in captions (including
**word-by-word captions of what is said**, transcribed on your computer), renders
vertical/square/wide video, exports an editable **DaVinci Resolve** timeline (free edition
included), and writes a publish kit with titles, hashtags and region-aware posting times.

- **Offline by design.** No cloud services, no API keys, no accounts. Python's standard library and
  ffmpeg are all it needs. It never uploads or posts anything.
- **Works with any agent.** It's an [MCP](https://modelcontextprotocol.io) server, so Claude, ChatGPT,
  Codex, Gemini CLI, Cursor, VS Code Copilot, local models through an MCP client, and more can drive it.
  Small local models can make a whole video with a single `auto_edit` call.
- **Speech captions, offline.** With a one-time `nookcut models install`, NookCut transcribes speech
  with ffmpeg's built-in Whisper filter and captions it word by word, with the spoken word
  highlighted. Talking videos are cut on whole sentences. Without the model, everything else still works.
- **HDR handled.** PS5/Xbox HDR captures and iPhone HDR video are tone-mapped to normal colour (on the
  Apple GPU when available), or kept as 10-bit HDR10/HLG with `--hdr keep`.
- **High frame rate.** 120 fps recordings give slow motion from real frames instead of invented ones,
  and output matches the source's rate family (59.94, 60 or 50 fps).
- **Dead air out.** The `tighten` template keeps the whole talk and cuts every pause, with clean
  jump cuts (no model needed).
- **Online is optional.** If you allow it, your agent can research trends with its own web search to
  sharpen hooks and hashtags. Nothing depends on it.

> **Status: alpha (0.1).** It works end to end and is tested on macOS and Linux. Expect rough edges,
> and please [report them](https://github.com/nookverse/nookcut/issues).

## Contents

- [Quick start](#quick-start)
- [Requirements](#requirements)
- [Install](#install) · [Update and uninstall](#update-and-uninstall)
- [Connect an agent](#connect-an-agent)
- [Use it from the command line](#use-it-from-the-command-line)
- [MCP tools](#mcp-tools)
- [What a project folder contains](#what-a-project-folder-contains)
- [Captions in other languages](#captions-in-other-languages)
- [Configuration](#configuration)
- [Running it as a shared or remote server](#running-it-as-a-shared-or-remote-server)
- [How it works](#how-it-works)
- [Privacy and security](#privacy-and-security)
- [FAQ](#faq)
- [Contributing](#contributing) · [Credits](#credits) · [License](#license)

## Quick start

```bash
pipx install "nookcut[mcp] @ git+https://github.com/nookverse/nookcut.git"
nookcut check                      # checks ffmpeg and tells you how to fix anything missing
nookcut make ./my-short --source ~/Videos/raw.mp4 --title "Final boss, first clear" --max 45s
```

You get an `.mp4` named after your title in `./my-short/`, a `publish_kit.md`, and a `resolve/`
folder you can open in DaVinci Resolve. To let an agent do it, see [Connect an agent](#connect-an-agent).

## Requirements

- **Python 3.10+**
- **ffmpeg and ffprobe** with `libx264`, `drawtext`, `minterpolate`, `gblur` and `loudnorm`. Most
  builds include them. `nookcut check` tells you what's missing and how to install it:
  - macOS: `brew install ffmpeg-full`, then put it on your PATH (it's keg-only):
    `echo 'export PATH="$(brew --prefix ffmpeg-full)/bin:$PATH"' >> ~/.zprofile`. Homebrew's plain
    `ffmpeg` is a minimal build without `drawtext`, so captions wouldn't work.
  - Windows: `winget install Gyan.FFmpeg`
  - Debian/Ubuntu: `sudo apt install ffmpeg`
- **A bold font for captions.** Impact is found automatically on macOS and Windows; elsewhere, set
  `NOOKCUT_FONT` to any bold `.ttf`.
- Optional, for speech captions: an ffmpeg **8.0+ built with whisper.cpp** (the `whisper` filter;
  Homebrew's `ffmpeg-full` has it, and `nookcut check` shows whether yours does) and a speech model
  from `nookcut models install` (148 MB by default, downloaded once). `libass` (the `ass` filter)
  adds the highlighted-word style; without it, phrases are shown plain.
- For **HDR recordings**: ffmpeg's `zscale` filter (libzimg) to convert them to normal colour; on
  Apple silicon the GPU (`scale_vt`) does it instead. `--hdr keep` needs a 10-bit HEVC encoder (the
  GPU's, or `libx265`). `nookcut check` shows what works under `hdr`.
- Optional: a GPU. NookCut encodes on NVIDIA (NVENC), AMD (AMF / VA-API), Intel (Quick Sync / VA-API)
  or Apple (VideoToolbox) when one works, and falls back to the CPU otherwise.

| Platform | Status |
|---|---|
| macOS | Supported and tested (CI + daily use) |
| Linux | Supported and tested in CI |
| Windows | CLI and MCP server should work but aren't tested in CI yet; `nookcut service` is macOS/Linux only |

## Install

```bash
# From GitHub (until the PyPI release)
pipx install "nookcut[mcp] @ git+https://github.com/nookverse/nookcut.git"

# Or run without installing
uvx --from "nookcut[mcp] @ git+https://github.com/nookverse/nookcut.git" nookcut-mcp

# From source
git clone https://github.com/nookverse/nookcut.git && cd nookcut
python3 -m venv .venv && .venv/bin/pip install -e ".[mcp]"
```

The `[mcp]` extra installs the MCP SDK for the server. The CLI alone needs no third-party packages.
Check it worked with `nookcut --version` and `nookcut check`.

## Update and uninstall

```bash
pipx upgrade nookcut               # or: pipx install --force "nookcut[mcp] @ git+https://github.com/nookverse/nookcut.git"
nookcut service restart            # only if you use the shared server
```

To remove everything NookCut set up:

```bash
nookcut service uninstall          # if you installed the shared server
claude mcp remove nookcut -s user  # or remove "nookcut" from your agent's MCP config
pipx uninstall nookcut
rm -rf ~/.config/nookcut ~/.nookcut ~/.claude/skills/nookcut-editor
```

Your project folders (videos, publish kits) are yours; delete them by hand if you want.

## Connect an agent

```bash
nookcut setup claude-code          # prints the exact command or config for your machine
nookcut setup all                  # every supported platform
nookcut skill install claude       # installs the editing playbook as a skill (also: codex | agents)
nookcut skill export cursor --dir ./my-project   # agents-md | gemini | cursor | windsurf | copilot | cline
```

For example, with Claude Code:

```bash
claude mcp add nookcut --scope user -- nookcut-mcp
```

Gemini CLI can install it as an extension: `gemini extensions install https://github.com/nookverse/nookcut`.

See **[docs/PLATFORMS.md](docs/PLATFORMS.md)** for copy-paste setup for Claude Desktop, Cursor,
Windsurf, VS Code, Cline, Codex, Zed, Goose, OpenAI Agents SDK, LangChain, ChatGPT and n8n.

Then ask your agent something like *"Make a 45-second vertical short from ~/Videos/raw.mp4. It's my
first clear of the final boss."* The server also ships the editing playbook as the `nookcut-editor`
prompt and the `nookcut://skill` resource, so agents without a skills system still get it.

## Use it from the command line

```bash
nookcut make PROJECT --source raw.mp4 --title "..." [--game "..."] [--type gameplay|talking|sports|tutorial|generic]
             [--orientation vertical|square|wide] [--max 60] [--region "United States"] [--language en]
             [--platforms youtube_shorts,tiktok,instagram_reels] [--music ai|none|file] [--music-file song.mp3]
             [--source-audio keep|duck|mute] [--template comeback|victory|highlights|tighten] [--name file-name] [--online]
             [--subtitles auto|on|off] [--speech-language auto|en|es|...] [--hdr sdr|keep] [--fps auto|60|30]
nookcut when India                 # posting windows in your own time zone
nookcut copy show PROJECT          # other caption, title and hashtag options
nookcut models install             # one-time 148 MB speech model for word-by-word captions (optional)
nookcut transcript PROJECT         # what is said in the recording, with times
nookcut watch PROJECT              # live progress of any job on that project, even one your agent started
nookcut cancel PROJECT             # stop it and free the CPU at once
```

Step by step: `new` → `analyze` → `plan` → (edit `edit.json`) → `preview` → `music` → `render` → `resolve`.
Inputs are forgiving: `--orientation 9:16`, `--max 1:30` and `--platforms shorts,reels` all work.

Exit codes: 0 ok, 1 error, 2 environment missing, 3 project busy, 130 cancelled.

## MCP tools

| Tool | What it does |
|---|---|
| `check_environment` | Checks ffmpeg, filters, encoders and fonts; shows the install command if anything is missing |
| `auto_edit` | **The whole video in one call**: analyze, plan, captions, music, render, Resolve files and publish kit |
| `create_project` | Saves the brief (title, type, length, orientation, region, music, speech captions, `online` opt-in) |
| `analyze_video` | Finds deaths, the win, action peaks and silent audio, and transcribes speech (when a model is installed) |
| `get_transcript` | What is said, as sentences with times, for quotes and cut points |
| `plan_edit` | Builds the edit from a story template with built-in captions, or `tighten` (the whole recording minus its pauses) |
| `suggest_copy` | Offline caption options per segment, plus titles, description, hashtags and a file name |
| `get_edit` / `update_edit` | Read or change segments and captions |
| `preview_plan` | A contact sheet image of every segment for approval |
| `generate_music` | Composes the original score (or masters your own track) |
| `render_video` | Renders the final video |
| `export_resolve` | EDLs and a Lua script that rebuild the edit in DaVinci Resolve, plus `captions.srt` when there is speech |
| `best_posting_times` | Estimated posting windows for a region, in your time zone |
| `write_publish_kit` | `publish_kit.md` with titles, description, hashtags and posting plan |
| `job_status` / `cancel_job` | Progress (percent, stage, ETA) and instant cancel for long jobs |

Long tools accept `wait=false` and return a job id, so clients with short tool timeouts still work.
Every result includes a `next` hint, so even small models know what to call next.

## What a project folder contains

```
my-short/
  brief.json         what you asked for
  analysis.json      detected moments
  transcript.json    what is said, word by word (with a speech model)
  edit.json          the edit: segments, speeds, captions (safe to edit by hand)
  preview.png        contact sheet for approval
  music.wav          the original score
  <name>.mp4         the finished video
  resolve/           EDLs per frame rate, markers, build_timeline.lua and captions.srt
  publish_kit.md     titles, description, hashtags, posting plan
```

Your source file is never modified.

## Captions in other languages

English captions, titles and hashtags ship built in. For another language, create a pack and
translate it:

```bash
nookcut copy init bn               # writes ~/.nookcut/copy/bn.json, a copy of the English pack
```

Translate the values (keep the keys and `{slots}`), then set the project's `language` to `bn`. Keys
you leave out fall back to English. Packs are plain JSON, so they work offline too.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `NOOKCUT_FONT` | auto | Bold `.ttf`/`.otf` for captions |
| `NOOKCUT_ENCODER` | auto | Force `cpu`, `nvenc`, `amf`, `qsv`, `vaapi` or `videotoolbox` |
| `NOOKCUT_WORKERS` | half your cores, max 4 | Segments rendered in parallel |
| `NOOKCUT_NICE` | `10` | ffmpeg priority (0 turns lowering off) |
| `NOOKCUT_ALLOWED_DIRS` | unrestricted | Folders the MCP tools may read and write (`:`-separated; `;` on Windows) |
| `NOOKCUT_COPY_DIR` | `~/.nookcut/copy` | Where your language packs live |
| `NOOKCUT_TONEMAP` | auto | HDR to SDR: `videotoolbox` (Apple GPU) or `cpu` (zscale); auto tries the GPU first |
| `NOOKCUT_MODELS_DIR` | `~/.nookcut/models` | Where `nookcut models install` puts speech models |
| `NOOKCUT_WHISPER_MODEL` | best installed | Speech model to use: a name (`base`, `small`, ...) or a `.bin` path |
| `NOOKCUT_TOKEN` / `NOOKCUT_TOKEN_FILE` | none | Bearer token for the HTTP transport |
| `NOOKCUT_TRANSPORT` / `NOOKCUT_HOST` / `NOOKCUT_PORT` | `stdio` / `127.0.0.1` / `8765` | Server transport settings |

## Running it as a shared or remote server

**One shared server for every agent session on your computer** (optional):

```bash
nookcut service install            # starts at login (macOS launchd / Linux systemd --user)
nookcut service connect            # prints the command that connects Claude Code to it
nookcut service status | restart | uninstall
```

It listens on `127.0.0.1:8765` only and requires a token stored in `~/.config/nookcut/token`
(readable only by you). In this mode a job keeps running if the agent session closes; stop it
with `cancel_job` or `nookcut cancel PROJECT`.

**Remote** (ChatGPT, n8n, web agents):

```bash
NOOKCUT_TOKEN=<long-random-string> NOOKCUT_ALLOWED_DIRS=/srv/media nookcut-mcp --transport http --host 0.0.0.0
# or with Docker (ffmpeg and a font included; experimental, the image isn't tested in CI yet)
docker build -t nookcut . && docker run -p 8765:8765 -e NOOKCUT_TOKEN=<long-random-string> -v /srv/media:/media nookcut
```

Clients connect to `http://host:8765/mcp` with `Authorization: Bearer <token>`. The server refuses
to listen off-localhost without a token. Put it behind HTTPS (a reverse proxy) before exposing it
to the internet.

## How it works

1. **Detection, no ML.** The video is decoded at 2 fps to 64×36 pixels and measured: brightness, a red
   vignette at the screen edges, motion and loudness. A fade to black after a red damage vignette is a
   **death**; the first long fade after the last death is the **win**; motion and loudness peaks are
   **highlights**. Thresholds live in `analyze.DEFAULTS`.
2. **Story templates.**
   - **comeback**: hook → death montage with a counter → "ATTEMPT #N" → best moments → slow-mo finisher → question
   - **victory**: a win without deaths
   - **highlights**: any video, top moments in order
   - **tighten**: the whole recording with its pauses cut out (jump cuts), for talks, podcasts and
     tutorials; only when you ask for it, and not shortened to your length limit

   Each template is fitted to your length limit.
3. **Speech (optional).** With a model installed, ffmpeg's `whisper` filter (whisper.cpp) transcribes
   the audio word by word. Quiet stretches are cut out first, so word times stay accurate. Talking
   and tutorial videos are cut on whole sentences; for other videos, cuts are moved out of the
   middle of words.
4. **Copy.** Captions, titles, descriptions and hashtags come from a built-in library, filled with
   facts from your brief and the analysis (death count, game name, length) and wrapped to fit the
   screen.
5. **Music.** A score is synthesized from scratch (taiko, pulse or cinematic), with hits on the cuts,
   then mastered. It uses no samples or existing recordings, so there's no source track to match.
6. **Render.** Segments render in parallel on your GPU when possible, then they're joined and mixed
   with the music. The original audio is ducked under it.

## Privacy and security

- NookCut makes **no network calls**. Analysis, transcription, music and rendering all happen on your
  computer. The one exception is `nookcut models install`, which you run yourself: it downloads a
  speech model from Hugging Face (whisper.cpp's official files) and checks its SHA-256.
- It **never uploads or posts**. You get files and a publish kit.
- The HTTP server binds to localhost by default, needs a token anywhere else, and can be fenced
  into specific folders with `NOOKCUT_ALLOWED_DIRS`.
- ffmpeg runs at lower priority and every job can be cancelled instantly.

Report vulnerabilities privately; see [SECURITY.md](SECURITY.md).

## FAQ

See **[docs/FAQ.md](docs/FAQ.md)**. Short answers:

- **Does it need the internet?** No. Every step works offline.
- **Do I need an API key or subscription?** No.
- **Will the music get copyright claims?** It's synthesized from scratch for each video, with no
  samples, so there's no existing track to match. Automated systems can still misfire now and then;
  if one does, you can dispute it. If you use your own track, you need the rights to it.
- **Does it only work for games?** No. Detection is best tuned for action games, and other videos use
  the highlights template.

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) and the
[Code of Conduct](CODE_OF_CONDUCT.md). Notes for AI coding agents working on this repo are in
[AGENTS.md](AGENTS.md). Release notes are in [CHANGELOG.md](CHANGELOG.md).

## Credits

NookCut stands on FFmpeg, whisper.cpp, libass, the MCP Python SDK and more. See
[CREDITS.md](CREDITS.md) for every project it uses and their licenses.

## License

[MIT](LICENSE) © 2026 Nookverse
