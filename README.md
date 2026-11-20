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
- [Brief options](#brief-options)
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

One command does everything:

```bash
nookcut make PROJECT --source raw.mp4 --title "..." [brief options] [--template NAME] [--name file-name]
```

`--source` and `--title` are required; every other [brief option](#brief-options) has a default.
`--template` is `comeback`, `victory`, `highlights` or `tighten` (empty picks one), and `--name` sets
the video's file name.

Or step by step, editing `edit.json` (or using `nookcut copy show`) between steps:

| Command | What it does |
|---|---|
| `nookcut new PROJECT --source ... --title ... [brief options]` | Saves the brief |
| `nookcut analyze PROJECT` | Finds the moments, the pauses and (with a speech model) what is said |
| `nookcut plan PROJECT [--template NAME]` | Builds `edit.json` |
| `nookcut preview PROJECT` | Contact sheet of every segment |
| `nookcut music PROJECT [--style taiko\|pulse\|cinematic]` | Composes the score |
| `nookcut render PROJECT` | Renders the video |
| `nookcut resolve PROJECT [--render video.mp4]` | DaVinci Resolve EDLs, Lua builder and `captions.srt` |

Everything else:

| Command | What it does |
|---|---|
| `nookcut check` | Checks ffmpeg, fonts, GPU, speech-to-text and HDR support, with install hints |
| `nookcut copy show PROJECT` | Other caption, title and hashtag options for a planned project |
| `nookcut copy init LANG` | Starts a caption language pack to translate |
| `nookcut transcript PROJECT [--start S] [--end S]` | What is said, with times |
| `nookcut verify PROJECT [--video FILE]` | Checks the last render (exit code 1 if something needs attention) |
| `nookcut frames PROJECT [--at 1:30 ...] [--count 8] [--source]` | A frame sheet to look at |
| `nookcut models list \| install [NAME] \| remove NAME` | Optional speech models (the only download NookCut makes) |
| `nookcut when REGION [--tz ZONE]` | Estimated posting windows, in your time zone |
| `nookcut watch PROJECT` | Live progress of any job on that project, even one your agent started |
| `nookcut cancel PROJECT` | Stops it and frees the CPU at once |
| `nookcut setup PLATFORM [--url URL]` | MCP config for an agent platform (`all` lists every one) |
| `nookcut skill install\|export\|print [TARGET]` | Installs or exports the editing playbook |
| `nookcut service install\|status\|restart\|connect\|uninstall` | One shared background server ([below](#running-it-as-a-shared-or-remote-server)) |

Add `-v` for full error details or `-q` to hide the progress bar; `nookcut --version` prints the
version. Inputs are forgiving: `--orientation 9:16`, `--max 1:30` and `--platforms shorts,reels` all work.

Exit codes: 0 ok, 1 error, 2 environment missing, 3 project busy, 130 cancelled.

## Brief options

The same options work as CLI flags (`nookcut new` / `make`) and as arguments of the MCP tools
`create_project` and `auto_edit`.

| Option (CLI flag) | Default | Values |
|---|---|---|
| `title` (`--title`) | required | What's in the video; used in captions, titles and the file name |
| `source` (`--source`) | required | Path to the recording |
| `video_type` (`--type`) | gameplay if a game is set, else generic | `gameplay`, `talking`, `sports`, `tutorial`, `generic` |
| `game` (`--game`) | none | Game name, for gameplay |
| `orientation` (`--orientation`) | `vertical` | `vertical` (9:16), `square` (1:1), `wide` (16:9) |
| `max_duration` (`--max`) | `60` | Length limit: `45`, `45s`, `1:30` |
| `platforms` (`--platforms`) | Shorts, TikTok, Reels | `youtube_shorts`, `tiktok`, `instagram_reels`, `facebook_reels`, `youtube`, `x` |
| `region` (`--region`) | global | Audience region for posting times, e.g. `India`, `United States` |
| `language` (`--language`) | `en` | Caption language ([packs](#captions-in-other-languages)) |
| `music` (`--music`) | `ai` | `ai` (original score), `none`, `file` (with `--music-file`) |
| `source_audio` (`--source-audio`) | `duck` | Original sound: `keep`, `duck` (under the music), `mute` |
| `subtitles` (`--subtitles`) | `auto` | Word-by-word speech captions: `auto` (when a speech model is installed), `on`, `off` |
| `speech_language` (`--speech-language`) | `auto` | Spoken language for transcription, e.g. `en`, `es`, `hi` |
| `hdr` (`--hdr`) | `sdr` | HDR recordings: `sdr` (converted, plays right everywhere) or `keep` (10-bit HDR10/HLG) |
| `fps` (`--fps`) | `auto` | Output frame rate: `auto` (60, or 59.94/50 to match the source), `60`, `30` |
| `online` (`--online`) | off | The user allows the agent to research trends on the web |
| `key_moments` (`--moment`) | none | Moments you know: `--moment "10:42=final blow"` (repeatable), or `["10:42=final blow"]` / `[{"t": 642.7, "label": "final blow"}]` for agents. A label with win, final, kill, victory or blow marks the win; on non-game videos the label becomes that moment's caption |

## MCP tools

| Tool | What it does |
|---|---|
| `check_environment` | Checks ffmpeg, filters, encoders and fonts; shows the install command if anything is missing |
| `auto_edit` | **The whole video in one call**: analyze, plan, captions, music, render, Resolve files and publish kit (takes the brief options and `template`) |
| `create_project` | Saves the brief (see [Brief options](#brief-options)) |
| `analyze_video` | Finds deaths, the win, action peaks, scene cuts, pauses and silent audio, and transcribes speech (when a model is installed) |
| `get_transcript` | What is said, as sentences with times, for quotes and cut points |
| `plan_edit` | Builds the edit from a story template with built-in captions, or `tighten` (the whole recording minus its pauses) |
| `suggest_copy` | Offline caption options per segment, plus titles, description, hashtags and a file name |
| `get_edit` / `update_edit` | Read or change segments, captions and the spoken words shown as captions |
| `preview_plan` | A contact sheet image of every segment for approval |
| `generate_music` | Composes the original score (or masters your own track) |
| `render_video` | Renders the final video and checks it (see `verify_video`) |
| `verify_video` | Checks a render: size, frame rate, length against the plan, colour, loudness, black or frozen picture, silence; plus one frame per segment as an image |
| `get_frames` | A labelled sheet of frames from the render or the source, at given times or spread evenly, for agents that can see images |
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
  preview_small.jpg  smaller copy the MCP server shows to agents
  music.wav          the original score
  <name>.mp4         the finished video
  resolve/           EDLs per frame rate, markers, build_timeline.lua and captions.srt
  publish_kit.md     titles, description, hashtags, posting plan
  check.jpg          one frame per segment of the last render (from the checks)
  frames.jpg         the last frame sheet you asked for
  .nookcut/          job progress and lock (for nookcut watch and cancel; safe to delete when idle)
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
   **highlights**. In the same decode, ffmpeg's `scdet` finds **scene cuts** (to 0.1 s): a cut doesn't
   count as action, and no segment starts or ends with a few frames of another shot. `cropdetect`
   finds **black bars** (cinematic letterbox, 4:3 pillarbox), which vertical and square videos cut
   off so the picture fills the frame; wide videos keep them. Thresholds live in `analyze.DEFAULTS`.
2. **Story templates.**
   - **comeback**: hook → death montage with a counter → "ATTEMPT #N" → best moments → slow-mo finisher → question
   - **victory**: a win without deaths
   - **highlights**: any video, top moments in order
   - **tighten**: the whole recording with its pauses cut out (jump cuts), for talks, podcasts and
     tutorials; only when you ask for it, and not shortened to your length limit

   comeback, victory and highlights are fitted to your length limit; tighten keeps the whole talk.
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
   with the music (the original sound is kept, ducked under it, or muted, as you chose), and the
   mix is normalised to -14 LUFS with true peaks under -1 dBTP. HDR sources
   are tone-mapped to SDR (on the Apple GPU when it can) or kept as HDR10/HLG. The frame rate
   follows the source's family, and slow motion uses real frames when the source has enough
   (120 fps), interpolating only when it doesn't.

7. **Check.** Every render is checked against the plan: size, frame rate, length, colour tags,
   loudness, black or frozen picture and silence, as plain numbers any agent can read, plus a sheet
   with one frame per segment for agents that can see images.

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
