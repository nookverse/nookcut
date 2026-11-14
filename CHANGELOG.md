# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `nookcut --version`.
- README: platform support table, alpha status, update and uninstall steps, full `make` options.

### Changed
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
