"""Portability: the skill and MCP config in each agent platform's native format.

The skill lives once in nookcut/skill/SKILL.md (open Agent Skills format). It is also served by
the MCP server as a prompt and a resource, so any MCP client gets it, and can be exported as the
instruction file each platform reads.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from importlib import resources

SKILL_NAME = "nookcut-editor"


def skill_text() -> str:
    return resources.files("nookcut").joinpath("skill/SKILL.md").read_text(encoding="utf-8")


def skill_body() -> str:
    """SKILL.md without the YAML front matter."""
    text = skill_text()
    if text.startswith("---"):
        text = text.split("---", 2)[2]
    return text.lstrip()


def skill_description() -> str:
    for line in skill_text().splitlines():
        if line.startswith("description:"):
            return line.split(":", 1)[1].strip()
    return ""


def server_command() -> str:
    exe = shutil.which("nookcut-mcp")
    if exe:
        return exe
    return os.path.join(os.path.dirname(sys.executable), "nookcut-mcp")


# Where each platform looks for skills (folder per skill containing SKILL.md).
SKILL_DIRS = {
    "claude": "~/.claude/skills",
    "codex": "~/.codex/skills",
    "agents": "~/.agents/skills",
}

# Instruction files for platforms that read project rules instead of skills.
RULE_FILES = {
    "agents-md": ("AGENTS.md", None),                               # Codex, Cursor, Copilot, Jules, Amp, Zed...
    "gemini": ("GEMINI.md", None),                                  # Gemini CLI
    "cursor": (".cursor/rules/nookcut.mdc",
               "---\ndescription: {desc}\nalwaysApply: false\n---\n\n"),
    "windsurf": (".windsurf/rules/nookcut.md", None),
    "copilot": (".github/instructions/nookcut.instructions.md", "---\napplyTo: \"**\"\n---\n\n"),
    "cline": (".clinerules/nookcut.md", None),
}


def install_skill(platform: str) -> str:
    base = os.path.expanduser(SKILL_DIRS[platform])
    target = os.path.join(base, SKILL_NAME)
    os.makedirs(target, exist_ok=True)
    path = os.path.join(target, "SKILL.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(skill_text())
    return path


# AGENTS.md and GEMINI.md are shared with the project's own instructions: the playbook sits between
# these markers, so a later export replaces it and leaves everything else in the file alone.
START = "<!-- nookcut-editor:start (written by `nookcut skill export`; run it again to update) -->"
END = "<!-- nookcut-editor:end -->"


def _section() -> str:
    return f"{START}\n{skill_body().rstrip()}\n{END}\n"


def _merge(existing: str) -> str:
    """existing with its NookCut playbook replaced by the current one (or the playbook added)."""
    if START in existing and END in existing.split(START, 1)[1]:
        before, rest = existing.split(START, 1)
        after = rest.split(END, 1)[1].lstrip("\n")
        return before + _section() + ("\n" + after if after else "")
    title = "# NookCut editor"
    found = existing.find("\n" + title + "\n")
    at = 0 if existing.startswith(title + "\n") else found + 1 if found >= 0 else -1
    if at >= 0:
        # an export from before the markers: the playbook runs to the next top-level heading
        end = existing.find("\n# ", at + len(title))
        after = existing[end + 1:] if end >= 0 else ""
        return existing[:at] + _section() + ("\n" + after if after else "")
    return existing.rstrip() + "\n\n" + _section()


def export_rules(platform: str, project_dir: str) -> str:
    rel, header = RULE_FILES[platform]
    path = os.path.join(project_dir, rel)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if platform in ("agents-md", "gemini"):
        existing = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
        body = _merge(existing) if existing.strip() else _section()
    else:   # a file of its own: written whole
        body = (header or "").format(desc=skill_description().replace("\n", " ")) + skill_body()
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


def mcp_config(platform: str, url: str = "", cmd: str = "") -> dict:
    """Config snippet + file location for a platform. url = remote HTTP endpoint instead of stdio."""
    cmd = cmd or server_command()
    stdio = {"command": cmd, "args": []}
    remote = {"url": url} if url else None
    entry = remote or stdio
    table = {
        "claude-code": {"where": "terminal",
                        "run": f"claude mcp add nookcut --scope user -- {cmd}" if not url
                        else f"claude mcp add --transport http nookcut {url}"},
        "claude-desktop": {"where": "~/Library/Application Support/Claude/claude_desktop_config.json "
                                    "(Windows: %APPDATA%\\Claude\\claude_desktop_config.json)",
                           "json": {"mcpServers": {"nookcut": stdio}}},
        "cursor": {"where": "~/.cursor/mcp.json (or .cursor/mcp.json in a project)",
                   "json": {"mcpServers": {"nookcut": entry}}},
        "windsurf": {"where": "~/.codeium/windsurf/mcp_config.json",
                     "json": {"mcpServers": {"nookcut": {"serverUrl": url} if url else stdio}}},
        "vscode": {"where": ".vscode/mcp.json (GitHub Copilot agent mode)",
                   "json": {"servers": {"nookcut": {"type": "http", "url": url} if url
                                        else {"type": "stdio", **stdio}}}},
        "cline": {"where": "Cline > MCP Servers > Configure (cline_mcp_settings.json)",
                  "json": {"mcpServers": {"nookcut": entry}}},
        "codex": {"where": "~/.codex/config.toml",
                  "toml": f'[mcp_servers.nookcut]\ncommand = "{cmd}"\nstartup_timeout_sec = 30\n'
                          f'tool_timeout_sec = 900\n' if not url else f'[mcp_servers.nookcut]\nurl = "{url}"\n'},
        "gemini": {"where": "~/.gemini/settings.json  (or: gemini extensions install <repo>)",
                   "json": {"mcpServers": {"nookcut": {"httpUrl": url} if url else {**stdio, "timeout": 900000}}}},
        "zed": {"where": "Zed settings.json",
                "json": {"context_servers": {"nookcut": {"command": {"path": cmd, "args": []}}}}},
        "goose": {"where": "terminal",
                  "run": f"goose configure  ->  Add Extension -> Command-line Extension -> {cmd}"},
        "openai-agents": {"where": "Python (OpenAI Agents SDK)",
                          "code": ("from agents.mcp import MCPServerStdio\n"
                                   f"nookcut = MCPServerStdio(params={{'command': '{cmd}'}}, "
                                   "client_session_timeout_seconds=900)")
                          if not url else
                          ("from agents.mcp import MCPServerStreamableHttp\n"
                           f"nookcut = MCPServerStreamableHttp(params={{'url': '{url}'}})")},
        "langchain": {"where": "Python (langchain-mcp-adapters)",
                      "code": ("from langchain_mcp_adapters.client import MultiServerMCPClient\n"
                               "client = MultiServerMCPClient({'nookcut': "
                               + (f"{{'transport': 'streamable_http', 'url': '{url}'}}" if url
                                  else f"{{'transport': 'stdio', 'command': '{cmd}', 'args': []}}") + "})")},
        "chatgpt": {"where": "ChatGPT > Settings > Connectors > Advanced > Developer mode > Add",
                    "note": "Needs a public HTTPS URL: run `nookcut-mcp --transport http` with NOOKCUT_TOKEN "
                            "behind a tunnel or server, and use that URL."},
        "n8n": {"where": "n8n 'MCP Client Tool' node",
                "note": "Endpoint: your http URL (…/mcp), auth: Bearer NOOKCUT_TOKEN."},
    }
    if platform not in table:
        raise KeyError(f"unknown platform; choose from {', '.join(table)}")
    return table[platform]


def platforms() -> list[str]:
    return ["claude-code", "claude-desktop", "cursor", "windsurf", "vscode", "cline", "codex", "gemini",
            "zed", "goose", "openai-agents", "langchain", "chatgpt", "n8n"]


def describe_config(platform: str, url: str = "", cmd: str = "") -> str:
    c = mcp_config(platform, url, cmd)
    out = [f"# {platform}", f"Where: {c['where']}"]
    if "run" in c:
        out.append(c["run"])
    if "json" in c:
        out.append(json.dumps(c["json"], indent=2))
    if "toml" in c:
        out.append(c["toml"])
    if "code" in c:
        out.append(c["code"])
    if "note" in c:
        out.append(c["note"])
    return "\n".join(out)
