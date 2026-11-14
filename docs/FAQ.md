# NookCut FAQ

## General

**What is NookCut?**
A tool that turns a long recording into a short-form video (YouTube Shorts, TikTok, Instagram and
Facebook Reels). It comes as a command-line tool, an MCP server that AI agents can drive, and an
editing playbook (a "skill") for those agents.

**Is it free?**
Yes. It's MIT-licensed, has no paid tiers, and needs no API keys, accounts or subscriptions.

**Does it need the internet?**
No. Detection, captions, music, rendering and the publish kit all run on your computer. If you let
it, your AI agent can use its *own* web search to look up trends, but that's optional and off by
default. NookCut itself never makes network calls.

**Does it upload or post my videos?**
Never. You get files: the video, a publish kit and a DaVinci Resolve timeline. You post them yourself.

**What kinds of videos does it work with?**
Anything: gameplay, talking head, podcasts, sports, tutorials. Detection is tuned best for action
games (it recognizes deaths and the final win). Other videos use the highlights template, which
picks the moments with the most motion and sound.

## Agents and models

**Which AI agents work with it?**
Any MCP client: Claude (Code and Desktop), ChatGPT, Codex, Gemini CLI, Cursor, Windsurf, VS Code
Copilot, Cline, Zed, Goose, OpenAI Agents SDK, LangChain, n8n and others. See
[PLATFORMS.md](PLATFORMS.md).

**Can I use a local model, for example through Ollama or LM Studio?**
Yes, through any MCP-capable client. Small models should call `auto_edit(source, title)`: it makes
the whole video in one step, so the model doesn't have to plan a long chain of tool calls. Every
tool result also says what to do next.

**Do I need an agent at all?**
No. `nookcut make` does everything from the command line.

**My agent's tool calls time out on long renders.**
Pass `wait=false` to `analyze_video`, `render_video` or `auto_edit`. You get a job id back; poll
`job_status` for progress and use `cancel_job` to stop.

## Output

**Will the music get copyright claims?**
The score is synthesized from scratch for each video, with no samples or existing recordings, so
there's no source track for a matching system to find. Automated systems occasionally misfire on
original music; if that happens, dispute the claim and explain the score was generated for your
video. If you supply your own track (`music: file`), you're responsible for having the rights to
it. Trending platform sounds have to be added in the platform's own app.

**Can I change the captions?**
Yes. `suggest_copy` (or `nookcut copy show PROJECT`) lists alternatives for every caption. Apply
them with `update_edit`, or edit `edit.json` by hand, then render again.

**Can I get captions in my language?**
English ships built in. Run `nookcut copy init <language>` to create a pack, translate the values,
and set the project's `language`. Anything you don't translate falls back to English. An agent can
also translate the captions for you.

**Are the hashtags trending?**
No. Offline hashtags are generic starter tags built from your topic and game name, and the publish
kit says so. For live trends, allow your agent to research online.

**How accurate are the posting times?**
They're estimates based on common short-form patterns in your audience's time zone, converted to
yours. After 5-10 posts, use your own analytics instead; the publish kit says where to find them.

**Can I fine-tune the edit in a real editor?**
Yes. `export_resolve` writes EDLs and a Lua script that rebuild the timeline in DaVinci Resolve,
including the free edition (Workspace > Console > Lua).

**Which orientations and lengths are supported?**
Vertical (9:16), square (1:1) and wide (16:9), with any length limit of 10 seconds or more. NookCut
warns you if a platform's limit is shorter than your setting.

**Does it work on Windows?**
The CLI and MCP server should work (install ffmpeg with `winget install Gyan.FFmpeg`), but Windows
isn't covered by CI yet, and `nookcut service` (the shared background server) supports only macOS and
Linux. Reports from Windows users are very welcome.

## Performance

**Will it slow my computer down?**
ffmpeg runs at lower priority, and by default at most half your CPU cores (up to 4) render in
parallel. Set `NOOKCUT_WORKERS` and `NOOKCUT_NICE` to change that.

**Does it use my GPU?**
Yes, when it can. It test-encodes on NVIDIA, AMD, Intel or Apple hardware first and falls back to
the CPU if none works or the GPU fails mid-render. `nookcut check` shows which encoder it picked.

**How do I stop a job?**
Use `cancel_job`, `nookcut cancel PROJECT`, or Ctrl+C. Every ffmpeg process of that job ends within
about a second, and half-written files are removed.

## Troubleshooting

**`nookcut check` says something is missing.**
It prints the install command for your system. Install ffmpeg (a full build), or set
`NOOKCUT_FONT` if only the font is missing. On macOS use `brew install ffmpeg-full` (and put it on your
PATH): Homebrew's plain `ffmpeg` has no `drawtext`, so it can't draw captions.

**It didn't find the win or the deaths in my video.**
Give it the moments you know: `key_moments: [{"t": 642.7, "label": "final blow"}]` in
`create_project`, or pick the `highlights` template.

**The tools stopped responding after I updated NookCut.**
If you use the shared server, run `nookcut service restart` so it loads the new code.

**Where do I report a bug?**
[Open an issue](https://github.com/nookverse/nookcut/issues) with `nookcut check` output and the
error text. `nookcut -v ...` prints the full ffmpeg details. Report security problems privately (see
[SECURITY.md](../SECURITY.md)).
