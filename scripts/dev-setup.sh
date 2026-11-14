#!/usr/bin/env bash
# One-time setup for working on NookCut from a fresh clone.
#   ./scripts/dev-setup.sh                venv + editable install + PATH links + skill + shared server
#   ./scripts/dev-setup.sh --no-service   skip the background server (agents start their own)
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

if ! command -v ffmpeg >/dev/null; then
  echo "ffmpeg is missing. Install it first (macOS: brew install ffmpeg; Linux: sudo apt install ffmpeg)." >&2
  exit 1
fi

[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -e ".[mcp]"
echo "installed nookcut into $ROOT/.venv"

mkdir -p "$HOME/.local/bin"
ln -sf "$ROOT/.venv/bin/nookcut" "$HOME/.local/bin/nookcut"
ln -sf "$ROOT/.venv/bin/nookcut-mcp" "$HOME/.local/bin/nookcut-mcp"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "add ~/.local/bin to your PATH to use 'nookcut' anywhere";; esac

.venv/bin/nookcut check >/dev/null || { .venv/bin/nookcut check; exit 1; }
.venv/bin/nookcut skill install claude >/dev/null && echo "installed the nookcut-editor skill for Claude Code"

if [ "${1:-}" != "--no-service" ]; then
  .venv/bin/nookcut service install | grep -v "Bearer"
  if command -v claude >/dev/null; then
    claude mcp remove nookcut -s user >/dev/null 2>&1 || true
    claude mcp add --transport http nookcut http://127.0.0.1:8765/mcp -s user \
      --header "Authorization: Bearer $(cat "$HOME/.config/nookcut/token")" >/dev/null
    echo "Claude Code now uses the shared server in every project"
  fi
elif command -v claude >/dev/null; then
  claude mcp remove nookcut -s user >/dev/null 2>&1 || true
  claude mcp add nookcut -s user -- "$ROOT/.venv/bin/nookcut-mcp" >/dev/null
  echo "Claude Code starts its own NookCut server per session"
fi

.venv/bin/python -m unittest discover tests 2>&1 | tail -1
echo "ready. Start a new agent session in $ROOT"
