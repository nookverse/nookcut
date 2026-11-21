# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Speech captions, offline: `analyze` transcribes speech with ffmpeg's `whisper` filter (whisper.cpp)
  and videos get word-by-word captions with the spoken word highlighted (libass; plain phrases
  without it). Quiet stretches are cut out before transcribing so word times stay accurate.
- `nookcut models list|install|remove`: the optional speech models (tiny to large-v3-turbo), verified
  by SHA-256. This user-run command is NookCut's only network access.
- Talking and tutorial videos are cut on whole sentences, with sign-offs ("let me know in the
  comments") kept out of the hook and used as the last line. Other videos never cut mid-word.
- `tighten` template: the whole recording with its pauses cut out (jump cuts, click-free joins), from
  a speech-band energy scan that every analysis now runs (no model needed). `auto_edit` takes a
  `template`.
- `get_transcript` MCP tool and `nookcut transcript PROJECT`.
- `captions.srt` in the Resolve kit for platforms and editors.
- Brief options `subtitles` (auto | on | off) and `speech_language`; `check_environment` reports
  `speech_to_text`.
- `hdr: keep` / `--hdr keep`: 10-bit HEVC HDR10/HLG output with the source's mastering and
  light-level metadata, on the GPU (VideoToolbox, NVENC, AMF, Quick Sync, VA-API; each test-encoded
  first) or `libx265`, with captions at paper white. `check_environment` reports `hdr`.
- Scene cuts: ffmpeg's `scdet` runs on a 10 fps copy in the same decode as the scan (cut times to
  0.1 s, no second decode). Cuts no longer count as action, and segment edges move onto a nearby cut
  so no segment opens or closes with a flash of another shot. The analysis reports `scene_cuts`.
- Style packs: named caption/title/hashtag packs for a niche, laid over the language pack
  (`nookcut copy install FILE`, `nookcut copy packs`, `copy_pack` / `--copy-pack`). Packs are
  checked before install (known sections, known `{slots}`); `suggest_copy` lists installed ones.
- Script to video (`script_to_video`, `nookcut script`): each line of the user's script narrated
  offline, joined with short pauses, over a generated moving background that becomes the project's
  "recording", with an exact transcript (word captions, `captions.srt`, `get_transcript`). One
  segment per line; B-roll per line from the user's folder (named clips first, then unused ones;
  a named clip continues where it left off); otherwise the line's opening words over the background.
  `analyze_video` and `plan_edit` refuse script projects instead of replacing the script.
- B-roll `fit` layout: the whole clip over a blurred copy of itself (landscape clips in vertical).
- Voiceover: a `vo` line per segment, spoken on your computer (macOS `say`; espeak-ng and
  Windows' voice untested) or by your own program (`NOOKCUT_TTS_COMMAND`, no shell, server-side only).
  Lines are placed in output time, sped up at most 1.25x when they don't fit and reported if they
  still don't; the bed ducks about 12 dB under the voice; spoken words are captioned; cached per
  line. `--voiceover captions` reads the hook and end captions aloud. `list_voices` / `nookcut voices`.
- `nookcut config kokoro`: Kokoro as a natural offline voice, through a bundled adapter your Kokoro
  Python runs (NookCut gains no dependency). Lines get the video's language (`{language}`).
- A settings file, `~/.config/nookcut/config.env` (`nookcut config show/set/unset`), read by the CLI
  and the MCP server, so the shared background server gets settings like `NOOKCUT_TTS_COMMAND`.
- B-roll from your own folder (`broll_dir`, `--broll-dir`): `cover`, `inset` (picture-in-picture,
  either picture full-size) and `split` layouts over a segment's picture, for a time window inside
  it. Placed automatically where the speaker names a clip (by file name), capped at a third of the
  video; agents place it with `update_edit`. `list_broll` / `nookcut broll` list the files with a
  picture of each. Paths can't leave the folder; short clips loop; pictures drift slowly.
- Checks after every render (`verify_video`, `nookcut verify`): size, frame rate and length against
  the plan, colour tags, loudness, true peak, black or frozen picture and silence, plus one frame per
  segment. `auto_edit` and `render_video` include them.
- `get_frames` / `nookcut frames`: a labelled frame sheet from the render or the source (HDR
  sources tone-mapped), for agents that can see images.
- Black bars: ffmpeg's `cropdetect` runs in the same scan; vertical and square videos cut off
  cinematic letterbox and 4:3 pillarbox bars so the picture fills the frame (subtitles inside a
  bar are kept). Only bars that hold through a segment and recur in the recording are cut; side
  bars must be symmetric. Wide videos keep their bars. `update_edit` `crop: null` keeps them too.
- `fps` option (`--fps auto|60|30`).
- `--moment "10:42=final blow"` sets key moments from the CLI; agents may pass the same text form.
- `CREDITS.md`: the open-source projects and models NookCut uses, with their licenses.
- `nookcut --version`.
- README: platform support table, alpha status, update and uninstall steps, full `make` options.

### Fixed
- HDR recordings (HDR10/HLG, e.g. PS5 captures) rendered grey and washed out, as 8-bit video that
  was still labelled HDR. They're now tone-mapped to SDR BT.709 (on the Apple GPU via `scale_vt`
  when available, else zscale + Hable on the CPU), and every output is tagged BT.709 limited range.
- Speech captions drawn by libass used BT.601 colour correction; colours now land as written.
- Sources tagged BT.601 (Elgato 4K captures label their video `smpte170m`) are converted to BT.709
  explicitly, so older ffmpeg builds don't shift their colours either.
- ffmpeg 9.0 crashes when converting the colour matrix of VideoToolbox-decoded 4K HEVC frames
  directly; frames are converted to software format first. A crash inside ffmpeg is now reported
  as one instead of an empty error.

- Every render was 60 fps: 59.94 fps sources (PS5, Xbox, cameras) repeated a frame about every 17 s,
  and 25/50 fps sources stuttered. Output now matches the source's family (60000/1001, 60 or 50).
- Slow motion always invented frames with minterpolate, even from 120 fps sources. Real frames are
  used whenever the source has enough (about 3 times faster for that segment).
- Dolby Vision profile 5 (no standard picture inside) is now refused with a clear message instead
  of rendering wrong colours.

- The final mix had no loudness normalisation (a test render came out at -17.6 LUFS). It's now
  normalised to -14 LUFS with true peaks under -1 dBTP after AAC encoding.

### Changed
- Removed the brief's `spoilers` option, which nothing used (old brief files still load).
- Built-in titles no longer restate the user's title ("It took me 17 tries took me 18 tries").
- The preview contact sheet shows at most 40 segments, sampled evenly.
- macOS install hint is now `brew install ffmpeg-full` (Homebrew's plain `ffmpeg` lacks `drawtext`).
- Music wording no longer promises immunity from copyright claims (it has no source track to match).
- Docker image marked experimental until it's tested in CI.
- Editor skill: clearer analyze/research step, guidance for non-game videos and language packs, and a
  rule against invented facts in captions.

## [0.1.0] - 2026-11-14

First public release.

### Added
- Moment detection without ML: deaths, the win, action peaks and silent audio from a 64×36, 2 fps scan.
- Story templates (comeback, victory, highlights) fitted to a length budget.
- Built-in offline captions, titles, descriptions and hashtags, with user-made language packs
  (`nookcut copy init <lang>`).
- Original copyright-free music (taiko, pulse, cinematic) synthesized from scratch and synced to cuts.
- Parallel rendering with GPU encoding (NVENC, AMF, Quick Sync, VA-API, VideoToolbox) and CPU fallback.
- DaVinci Resolve export (EDLs and a Lua timeline builder that works in the free edition).
- Region-aware posting-time estimates and a publish kit.
- MCP server (stdio and Streamable HTTP with bearer-token auth) with 16 tools, including one-call
  `auto_edit` for small and local models, background jobs, progress and instant cancel.
- `nookcut service`: one shared localhost server for every agent session (launchd / systemd).
- Setup helpers and skill exports for Claude, Codex, Gemini CLI, Cursor, Windsurf, VS Code Copilot, Cline and more.
- Optional, opt-in web research by the agent (`online`, off by default).
