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

**Can my agent see the video?**
If it can read images: `get_frames` returns a labelled sheet of frames from the render or the
source, and `verify_video` returns one frame per segment. Every agent, including small local
models that can't see, gets the checks as numbers after each render: size, frame rate, length,
colour, loudness, true peak, black or frozen picture and silence.

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

**Can it caption what I say?**
Yes. Run `nookcut models install` once (a 148 MB speech model; `nookcut models list` shows smaller
and more accurate ones). After that, `analyze` transcribes speech on your computer with ffmpeg's
built-in Whisper filter and the video gets word-by-word captions, with the spoken word highlighted.
Talking and tutorial videos are cut on whole sentences. Your ffmpeg needs the `whisper` filter
(ffmpeg 8+ built with whisper.cpp; Homebrew's `ffmpeg-full` has it). Turn it off with
`--subtitles off` (`subtitles: "off"` for agents).

**Can it remove the pauses and dead air from a whole video?**
Yes: use the `tighten` template (`nookcut make ... --template tighten`, or `template: "tighten"` in
`auto_edit` / `plan_edit`). It keeps everything that's said and cuts each pause down to a short
breath, with clean jump cuts. It needs no speech model. It doesn't shorten the talk to your length
limit, and it adds no generated music. It can't separate a voice from non-stop music or game audio;
the analysis `pauses` line tells you whether it applies.

**Is the transcription sent anywhere?**
No. The model runs inside ffmpeg on your machine. The model download is the only network access
NookCut ever makes, and only when you run `nookcut models install` yourself.

**A word in the captions is wrong.**
Edit the segment's `words` with `update_edit` (or in `edit.json`), then render again. A bigger model
(`nookcut models install small`) makes fewer mistakes. If the language is detected wrongly, set
`speech_language` (for example `es`).

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

**My recording is HDR (PS5, Xbox, iPhone). Will it look right?**
Yes. NookCut detects HDR10 and HLG and converts them to normal colour (SDR), so the short looks right
on every phone and platform. On Apple silicon the GPU does the conversion; elsewhere ffmpeg's
`zscale` filter does it on the CPU. Before this, HDR sources came out grey and washed out.

**Can I keep HDR?**
Yes: `--hdr keep` (or `hdr: "keep"`). You get 10-bit HEVC (HDR10 or HLG) with the source's
mastering and light-level metadata, encoded on the GPU when it can do 10-bit HEVC, else with
`libx265`. Captions are drawn at paper white (203 nits) so they don't glare. YouTube shows HDR;
other apps may convert it to SDR themselves, so check how it looks after posting. Dolby Vision and
HDR10+ dynamic metadata aren't kept.

**What about Dolby Vision and HDR10+?**
Dolby Vision from an iPhone (profile 8.4) or with an HDR10 base (8.1, 7) works: NookCut uses the
HLG or HDR10 picture inside. Profile 5 has no standard picture inside, so NookCut stops and asks you
to export it as HDR10 or SDR. The shot-by-shot Dolby Vision and HDR10+ metadata isn't kept: after
cropping, zooming and captions it would describe a different picture, and GPU encoders can't write
it. HDR10+ files play as plain HDR10, and with `hdr: keep` the static HDR10 metadata is kept.

**My recording is 120 fps. Is that useful?**
Yes, for slow motion. At half speed a 120 fps source has 60 real frames per second, so NookCut uses
them instead of inventing frames (sharper, and about 3 times faster to render). The output stays
60 fps, which as far as we know is the most Shorts, TikTok and Reels play. It matches the source's
family: 59.94 fps sources (PS5, Xbox, most cameras) give 59.94, 25/50 fps sources give 50. Use
`--fps 30` for smaller files.

**My cutscenes have black bars. Will they show in the short?**
Not in vertical or square videos: NookCut detects the bars and cuts them off, so the picture fills
the frame. Subtitles a game draws inside a bar are kept. It only cuts a bar that stays put through
most of the segment and appears often in the recording, so a dark scene isn't mistaken for one.
Wide (16:9) videos keep the bars, since cropping a cinematic picture to 16:9 would cut its sides.
To keep the bars on a segment, set its `crop` to `null` with `update_edit`.

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
Give it the moments you know: `--moment "10:42.7=final blow"` on the command line, or
`key_moments: ["10:42.7=final blow"]` (or `[{"t": 642.7, "label": "final blow"}]`) in
`create_project`, or pick the `highlights` template.

**The tools stopped responding after I updated NookCut.**
If you use the shared server, run `nookcut service restart` so it loads the new code.

**Where do I report a bug?**
[Open an issue](https://github.com/nookverse/nookcut/issues) with `nookcut check` output and the
error text. `nookcut -v ...` prints the full ffmpeg details. Report security problems privately (see
[SECURITY.md](../SECURITY.md)).
