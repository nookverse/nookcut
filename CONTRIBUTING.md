# Contributing to NookCut

Thanks for helping! Bug reports, fixes, language packs, docs and new story templates are all welcome.

## Ground rules

These are product decisions; please don't change them in a pull request without discussing it in an
issue first:

- **Free and offline.** No paid APIs, API keys or network calls. At runtime: the Python standard library
  and ffmpeg only (`mcp` is the one optional dependency, for the server).
- **Licensed music only.** Music is synthesized from scratch or supplied by the user. Never add samples
  or copyrighted tracks.
- **Never post or upload** on the user's behalf.
- **Generic, not game-specific.** Features must work for any topic.
- **Gentle on the user's computer.** Every ffmpeg call is cancellable and runs at lower priority.

[AGENTS.md](AGENTS.md) has the full list, the code layout and the conventions. It's written for
AI coding agents, and humans will find it useful too.

## Set up

You need Python 3.10+ and ffmpeg.

```bash
git clone https://github.com/nookverse/nookcut.git && cd nookcut
./scripts/dev-setup.sh --no-service     # venv, editable install, skill; skips the background server
```

Or by hand: `python3 -m venv .venv && .venv/bin/pip install -e ".[mcp]"`.

## Check your change

```bash
.venv/bin/python -m unittest discover tests     # always (about 1 s)
.venv/bin/python tests/mcp_smoke.py             # if you touched the MCP server
```

For changes to analysis or rendering, also run a real clip end to end and look at the result. A
synthetic clip is enough:

```bash
ffmpeg -v error -f lavfi -i testsrc2=s=1920x1080:r=30:d=90 -f lavfi -i sine=d=90 -c:v libx264 -c:a aac /tmp/clip.mp4
nookcut make /tmp/proj --source /tmp/clip.mp4 --title Test --type generic --max 30
```

If you edit `src/nookcut/skill/SKILL.md`, sync its copies (a test fails if they drift):

```bash
cp src/nookcut/skill/SKILL.md skills/nookcut-editor/SKILL.md
.venv/bin/python -c "from nookcut.agents import skill_body; open('GEMINI.md','w').write(skill_body())"
```

## Pull requests

- Keep each PR focused on one change, and add or update tests for it.
- Write errors for non-technical users: raise a `NookCutError` subclass with a plain `message` and a
  `hint` saying what to do (see `errors.py`).
- Run every ffmpeg call through `tasks.run()` so it can be cancelled.
- **Never commit footage, renders, tokens or paths from your own machine.** `.gitignore` covers the
  usual outputs; check `git status` before committing.
- Describe what you changed and how you tested it.

## Language packs

Translations of `src/nookcut/copydata/en.json` are welcome. Copy it to `copydata/<code>.json` (ISO
639-1, e.g. `bn`, `es`), translate the values only, keep every `{slot}` and keep captions short (16
characters per line for vertical video). Partial packs are fine; missing keys fall back to English.

## Licensing

By contributing you agree that your work is released under the [MIT License](LICENSE).
