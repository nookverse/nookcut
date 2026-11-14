"""One shared NookCut server for the whole computer, instead of one copy per agent session.

  nookcut service install     start the server at login (macOS launchd / Linux systemd --user)
  nookcut service status      is it running, and where
  nookcut service restart     load new NookCut code after an update
  nookcut service uninstall   stop it and remove it

The server listens on 127.0.0.1 only and requires a bearer token kept in ~/.config/nookcut/token
(readable only by you). Agents connect over Streamable HTTP; `install` prints the exact commands.
"""
from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

LABEL = "com.nookcut.mcp"
HOST, PORT, PATH = "127.0.0.1", 8765, "/mcp"
URL = f"http://{HOST}:{PORT}{PATH}"
CONFIG_DIR = os.path.expanduser("~/.config/nookcut")
TOKEN_FILE = os.path.join(CONFIG_DIR, "token")
LOG_FILE = os.path.expanduser("~/Library/Logs/nookcut-mcp.log" if sys.platform == "darwin"
                              else "~/.local/state/nookcut/nookcut-mcp.log")
PLIST = os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")
UNIT = os.path.expanduser("~/.config/systemd/user/nookcut-mcp.service")


def token() -> str:
    """The shared server's token, created on first use (file mode 600)."""
    if not os.path.exists(TOKEN_FILE):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        fd = os.open(TOKEN_FILE, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(32))
    with open(TOKEN_FILE) as f:
        return f.read().strip()


def _server_cmd() -> list[str]:
    exe = shutil.which("nookcut-mcp") or os.path.join(os.path.dirname(sys.executable), "nookcut-mcp")
    return [exe, "--transport", "http", "--host", HOST, "--port", str(PORT), "--path", PATH]


def _path_env() -> str:
    """PATH for the service: launchd/systemd start with a bare PATH, so keep where ffmpeg lives."""
    dirs = [os.path.dirname(p) for p in (shutil.which("ffmpeg"), shutil.which("ffprobe")) if p]
    dirs += ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    return os.pathsep.join(dict.fromkeys(dirs))


def _plist() -> str:
    args = "".join(f"\n    <string>{a}</string>" for a in _server_cmd())
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>{args}
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>NOOKCUT_TOKEN_FILE</key><string>{TOKEN_FILE}</string>
    <key>PATH</key><string>{_path_env()}</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
  <key>StandardOutPath</key><string>{LOG_FILE}</string>
  <key>StandardErrorPath</key><string>{LOG_FILE}</string>
</dict>
</plist>
"""


def _unit() -> str:
    return f"""[Unit]
Description=NookCut MCP server (shared, localhost only)

[Service]
ExecStart={" ".join(_server_cmd())}
Environment=NOOKCUT_TOKEN_FILE={TOKEN_FILE}
Environment=PATH={_path_env()}
Restart=on-failure
Nice=5

[Install]
WantedBy=default.target
"""


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def running() -> bool:
    """True when something answers on the service URL (401 without a token still means it's up)."""
    try:
        urllib.request.urlopen(urllib.request.Request(URL, method="GET"), timeout=2)
        return True
    except urllib.error.HTTPError:
        return True
    except OSError:
        return False


def connect_help() -> str:
    t = token()
    return "\n".join([
        f"Shared server: {URL}  (token in {TOKEN_FILE})",
        "",
        "Claude Code (all projects):",
        "  claude mcp remove nookcut -s user",
        f'  claude mcp add --transport http nookcut {URL} -s user --header "Authorization: Bearer {t}"',
        "",
        "Other agents: `nookcut setup <platform> --url " + URL + "` and add the same Authorization header.",
    ])


def install() -> str:
    token()
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    if sys.platform == "darwin":
        os.makedirs(os.path.dirname(PLIST), exist_ok=True)
        domain = f"gui/{os.getuid()}"
        _run(["launchctl", "bootout", f"{domain}/{LABEL}"])  # replace an older install
        with open(PLIST, "w") as f:
            f.write(_plist())
        r = _run(["launchctl", "bootstrap", domain, PLIST])
        if r.returncode:
            raise RuntimeError(f"launchctl couldn't start the service: {r.stderr.strip()}")
        where = PLIST
    elif sys.platform.startswith("linux"):
        os.makedirs(os.path.dirname(UNIT), exist_ok=True)
        with open(UNIT, "w") as f:
            f.write(_unit())
        for cmd in (["systemctl", "--user", "daemon-reload"],
                    ["systemctl", "--user", "enable", "--now", "nookcut-mcp.service"]):
            r = _run(cmd)
            if r.returncode:
                raise RuntimeError(f"{' '.join(cmd)} failed: {r.stderr.strip()}")
        where = UNIT
    else:
        return ("Automatic service install supports macOS and Linux. On Windows, add a Task Scheduler task "
                f"'At log on' that runs:\n  {' '.join(_server_cmd())}\nwith NOOKCUT_TOKEN_FILE={TOKEN_FILE}\n\n"
                + connect_help())
    return f"Installed {where}; the server starts at login and restarts if it crashes.\n\n" + connect_help()


def uninstall() -> str:
    if sys.platform == "darwin":
        _run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"])
        if os.path.exists(PLIST):
            os.remove(PLIST)
    elif sys.platform.startswith("linux"):
        _run(["systemctl", "--user", "disable", "--now", "nookcut-mcp.service"])
        if os.path.exists(UNIT):
            os.remove(UNIT)
            _run(["systemctl", "--user", "daemon-reload"])
    return ("Service removed. Agents set up for the shared server need the stdio config again "
            "(`nookcut setup <platform>`).")


def restart() -> str:
    if sys.platform == "darwin":
        r = _run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LABEL}"])
    elif sys.platform.startswith("linux"):
        r = _run(["systemctl", "--user", "restart", "nookcut-mcp.service"])
    else:
        return "Restart the NookCut task in Task Scheduler."
    if r.returncode:
        raise RuntimeError(f"Couldn't restart the service: {r.stderr.strip() or 'is it installed?'}")
    return "Restarted; agents reconnect automatically on their next call."


def status() -> str:
    installed = os.path.exists(PLIST) or os.path.exists(UNIT)
    return "\n".join([f"installed: {'yes' if installed else 'no'}",
                      f"running:   {'yes' if running() else 'no'}  ({URL})",
                      f"log:       {LOG_FILE}"])
