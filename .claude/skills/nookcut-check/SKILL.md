---
name: nookcut-check
description: Verify a NookCut change end to end before saying it works - unit tests, skill copy sync, MCP smoke test, a real clip render when media code changed, and a shared-server restart. Use after editing anything under src/nookcut, or when asked to check, test or verify NookCut.
---

# Verify a NookCut change

Run only what the change needs, but never skip step 1. Report what you ran and what you could not.

1. **Unit tests:** `.venv/bin/python -m unittest discover tests`
2. **Skill edited?** (`src/nookcut/skill/SKILL.md`) Sync the copies, then re-run step 1:
   `cp src/nookcut/skill/SKILL.md skills/nookcut-editor/SKILL.md` and
   `.venv/bin/python -c "from nookcut.agents import skill_body; open('GEMINI.md','w').write(skill_body())"`,
   then `nookcut skill install claude`.
3. **MCP server touched?** `.venv/bin/python tests/mcp_smoke.py` (stdio + HTTP, auth, prompt, resource).
4. **Media code touched?** (analyze, render, music, probe, tasks, pipeline) Make a test clip in the
   scratchpad, never in the repo:
   `ffmpeg -v error -y -f lavfi -i testsrc2=s=1920x1080:r=30:d=90 -f lavfi -i sine=d=90 -c:v libx264 -preset ultrafast -c:a aac clip.mp4`
   then `.venv/bin/python tests/mcp_smoke.py clip.mp4 <scratch>/smoke` and, for render changes,
   `nookcut make <scratch>/proj --source clip.mp4 --title Test --type generic --max 30`.
   Check the output exists with the expected size and length (`ffprobe`).
5. **Cancel/cleanup touched?** Start a render with `NOOKCUT_WORKERS=1`, stop it with
   `nookcut cancel <proj>`, and confirm `pgrep -f "ffmpeg.*nookcut_"` finds nothing.
6. **Server code changed?** `nookcut service restart`, then `nookcut service status`.

Commit only if the owner asked, and never with AI attribution (see AGENTS.md > Git).
