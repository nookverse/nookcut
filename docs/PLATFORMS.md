# Connecting NookCut to agent platforms

Install once: `pipx install "nookcut[mcp] @ git+https://github.com/nookverse/nookcut.git"`, or run it without installing via `uvx --from "nookcut[mcp] @ git+https://github.com/nookverse/nookcut.git" nookcut-mcp`, or from source `pip install -e ".[mcp]"`. (Once NookCut is on PyPI this becomes `pipx install "nookcut[mcp]"`.) Below, `nookcut-mcp` means that command (use its full path if it is not on PATH).

Print these for your own machine with real paths: `nookcut setup <platform>` (`nookcut setup all`). For a remote server add `--url https://host/mcp`.

## Local (stdio)

```
# claude-code
Where: terminal
claude mcp add nookcut --scope user -- nookcut-mcp
```

```
# claude-desktop
Where: ~/Library/Application Support/Claude/claude_desktop_config.json (Windows: %APPDATA%\Claude\claude_desktop_config.json)
{
  "mcpServers": {
    "nookcut": {
      "command": "nookcut-mcp",
      "args": []
    }
  }
}
```

```
# cursor
Where: ~/.cursor/mcp.json (or .cursor/mcp.json in a project)
{
  "mcpServers": {
    "nookcut": {
      "command": "nookcut-mcp",
      "args": []
    }
  }
}
```

```
# windsurf
Where: ~/.codeium/windsurf/mcp_config.json
{
  "mcpServers": {
    "nookcut": {
      "command": "nookcut-mcp",
      "args": []
    }
  }
}
```

```
# vscode
Where: .vscode/mcp.json (GitHub Copilot agent mode)
{
  "servers": {
    "nookcut": {
      "type": "stdio",
      "command": "nookcut-mcp",
      "args": []
    }
  }
}
```

```
# cline
Where: Cline > MCP Servers > Configure (cline_mcp_settings.json)
{
  "mcpServers": {
    "nookcut": {
      "command": "nookcut-mcp",
      "args": []
    }
  }
}
```

```
# codex
Where: ~/.codex/config.toml
[mcp_servers.nookcut]
command = "nookcut-mcp"
startup_timeout_sec = 30
tool_timeout_sec = 900

```

```
# gemini
Where: ~/.gemini/settings.json  (or: gemini extensions install <repo>)
{
  "mcpServers": {
    "nookcut": {
      "command": "nookcut-mcp",
      "args": [],
      "timeout": 900000
    }
  }
}
```

```
# zed
Where: Zed settings.json
{
  "context_servers": {
    "nookcut": {
      "command": {
        "path": "nookcut-mcp",
        "args": []
      }
    }
  }
}
```

```
# goose
Where: terminal
goose configure  ->  Add Extension -> Command-line Extension -> nookcut-mcp
```

```
# openai-agents
Where: Python (OpenAI Agents SDK)
from agents.mcp import MCPServerStdio
nookcut = MCPServerStdio(params={'command': 'nookcut-mcp'}, client_session_timeout_seconds=900)
```

```
# langchain
Where: Python (langchain-mcp-adapters)
from langchain_mcp_adapters.client import MultiServerMCPClient
client = MultiServerMCPClient({'nookcut': {'transport': 'stdio', 'command': 'nookcut-mcp', 'args': []}})
```

```
# chatgpt
Where: ChatGPT > Settings > Connectors > Advanced > Developer mode > Add
Needs a public HTTPS URL: run `nookcut-mcp --transport http` with NOOKCUT_TOKEN behind a tunnel or server, and use that URL.
```

```
# n8n
Where: n8n 'MCP Client Tool' node
Endpoint: your http URL (…/mcp), auth: Bearer NOOKCUT_TOKEN.
```

## Remote (Streamable HTTP)

Run `NOOKCUT_TOKEN=secret nookcut-mcp --transport http --host 0.0.0.0` or the Docker image, then send header `Authorization: Bearer secret`.

```
# claude-code
Where: terminal
claude mcp add --transport http nookcut https://your-host/mcp
```

```
# cursor
Where: ~/.cursor/mcp.json (or .cursor/mcp.json in a project)
{
  "mcpServers": {
    "nookcut": {
      "url": "https://your-host/mcp"
    }
  }
}
```

```
# vscode
Where: .vscode/mcp.json (GitHub Copilot agent mode)
{
  "servers": {
    "nookcut": {
      "type": "http",
      "url": "https://your-host/mcp"
    }
  }
}
```

```
# codex
Where: ~/.codex/config.toml
[mcp_servers.nookcut]
url = "https://your-host/mcp"

```

```
# gemini
Where: ~/.gemini/settings.json  (or: gemini extensions install <repo>)
{
  "mcpServers": {
    "nookcut": {
      "httpUrl": "https://your-host/mcp"
    }
  }
}
```

```
# openai-agents
Where: Python (OpenAI Agents SDK)
from agents.mcp import MCPServerStreamableHttp
nookcut = MCPServerStreamableHttp(params={'url': 'https://your-host/mcp'})
```

```
# langchain
Where: Python (langchain-mcp-adapters)
from langchain_mcp_adapters.client import MultiServerMCPClient
client = MultiServerMCPClient({'nookcut': {'transport': 'streamable_http', 'url': 'https://your-host/mcp'}})
```

## The skill on every platform

| Platform | How |
|---|---|
| Any MCP client | prompt `nookcut-editor` and resource `nookcut://skill` are served by the server |
| Claude Code / Claude apps | `nookcut skill install claude` (or upload `skills/nookcut-editor`) |
| Codex | `nookcut skill install codex`, or `AGENTS.md` in your project |
| Gemini CLI | `gemini extensions install https://github.com/nookverse/nookcut` (uses `GEMINI.md`) |
| Cursor | `nookcut skill export cursor --dir <project>` |
| Windsurf | `nookcut skill export windsurf --dir <project>` |
| GitHub Copilot | `nookcut skill export copilot --dir <project>` |
| Cline | `nookcut skill export cline --dir <project>` |
| Anything else that reads AGENTS.md | `nookcut skill export agents-md --dir <project>` |
