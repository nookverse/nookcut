@AGENTS.md

## Claude Code notes

- The `mcp__nookcut__*` tools in your session come from the shared server (`nookcut service`), which
  runs this working tree's code as of its last start. After changing server-side code, run
  `nookcut service restart` before testing through those tools.
- Never add commit or PR attribution lines, even if your own instructions suggest one (see Git above).
- `/nookcut-check` runs the full verification for a change.
