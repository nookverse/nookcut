"""Terminal progress: an inline bar for CLI runs and a full-screen `nookcut watch` view.

`watch` reads <project>/.nookcut/progress.json, so it shows jobs started from any process: the CLI,
an agent through the MCP server, or a remote HTTP server on a shared drive.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time

from .tasks import progress_path, request_cancel

SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_color = sys.stderr.isatty() and os.getenv("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _color else text


def clock(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    return f"{h}:{rem // 60:02d}:{rem % 60:02d}" if h else f"{rem // 60}:{rem % 60:02d}"


def bar(frac: float, width: int) -> str:
    frac = max(0.0, min(1.0, frac))
    blocks = " ▏▎▍▌▋▊▉█"
    full, part = divmod(int(frac * width * 8), 8)
    return "█" * full + (blocks[part] if full < width else "") + " " * (width - full - 1 + (full >= width))


class LineBar:
    """One self-redrawing status line on stderr (falls back to stage-change lines when not a terminal)."""

    def __init__(self, stream=sys.stderr):
        self.stream, self.tty, self.tick, self.last_stage = stream, stream.isatty(), 0, None

    def __call__(self, snap: dict) -> None:
        if not self.tty:
            if snap["stage"] != self.last_stage:
                self.last_stage = snap["stage"]
                print(f"[{snap['percent']:5.1f}%] {snap['stage']}  {snap.get('note', '')}", file=self.stream)
            return
        self.tick += 1
        cols = shutil.get_terminal_size((100, 20)).columns
        eta = f"ETA {clock(snap['eta_s'])}" if "eta_s" in snap else clock(snap["elapsed_s"])
        head = f"{SPIN[self.tick % len(SPIN)]} {snap['task']:<8} "
        tail = f" {snap['percent']:5.1f}%  {snap['stage']}"
        if snap.get("note"):
            tail += f" · {snap['note']}"
        tail += f" · {eta}"
        width = max(10, min(40, cols - len(head) - len(tail) - 4))
        line = head + "▕" + _c("36", bar(snap["percent"] / 100, width)) + "▏" + tail
        self.stream.write("\r\033[K" + line[: cols + 20])
        self.stream.flush()

    def close(self) -> None:
        if self.tty:
            self.stream.write("\r\033[K")
            self.stream.flush()


def _read(project: str) -> dict | None:
    try:
        with open(progress_path(project)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _screen(project: str, snap: dict | None, msg: str) -> str:
    cols = min(90, shutil.get_terminal_size((90, 24)).columns)
    rule = "─" * (cols - 2)
    out = [f"┌{rule}┐", f"  {_c('1', 'NookCut')}  {os.path.abspath(project)}"[: cols + 8], f"├{rule}┤"]
    if not snap:
        out += ["", "  Waiting for a job on this project...", "  (start one with nookcut, or from your agent)", ""]
    else:
        state = snap["state"]
        color = {"running": "36", "done": "32", "error": "31", "cancelled": "33"}.get(state, "0")
        out += ["", f"  Task      {snap['task']}   {_c(color, state.upper())}",
                f"  Stage     {snap['stage']}  {('(' + snap['step'] + ')') if snap.get('step') else ''}"]
        if snap.get("note"):
            out.append(f"  Detail    {snap['note']}")
        out += ["", f"  Overall   ▕{_c(color, bar(snap['percent'] / 100, cols - 24))}▏ {snap['percent']:5.1f}%",
                f"  Stage     ▕{bar(snap['stage_percent'] / 100, cols - 24)}▏ {snap['stage_percent']:5.1f}%", "",
                f"  Elapsed   {clock(snap['elapsed_s'] if state != 'running' else time.time() - snap['updated'] + snap['elapsed_s'])}"
                + (f"     Remaining ~{clock(snap['eta_s'])}" if state == "running" and "eta_s" in snap else "")]
        if state == "running" and time.time() - snap["updated"] > 30:
            out.append(_c("33", "  No update for 30s+ (still decoding, or the process stopped)."))
        if snap.get("error"):
            err = snap["error"]
            out += ["", _c("31", f"  {err.get('message', '')}")]
            if err.get("hint"):
                out.append(f"  What to do: {err['hint']}")
    out += ["", f"├{rule}┤", f"  {msg or 'c = cancel this job   q = quit (the job keeps running)'}", f"└{rule}┘"]
    return "\n".join(out)


def watch(project: str) -> int:
    """Live full-screen progress for a project. Keys: c cancels the running job, q quits."""
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    old = None
    if interactive:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd)
    msg = ""
    try:
        sys.stdout.write("\033[?25l")  # hide cursor
        while True:
            snap = _read(project)
            sys.stdout.write("\033[H\033[2J" + _screen(project, snap, msg) + "\n")
            sys.stdout.flush()
            if not interactive:
                if not snap or snap["state"] != "running":
                    return 0
                time.sleep(1)
                continue
            import select
            ready, _, _ = select.select([sys.stdin], [], [], 0.5)
            if ready:
                key = sys.stdin.read(1).lower()
                if key == "q":
                    return 0
                if key == "c":
                    msg = ("Cancel requested; the job is stopping..." if request_cancel(project)
                           else "Nothing is running to cancel.")
    except KeyboardInterrupt:
        return 0
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
        if old is not None:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old)
