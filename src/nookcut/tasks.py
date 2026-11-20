"""Running ffmpeg safely: progress, cancellation, cleanup and one job per project.

A Task is one unit of user-visible work (analyze, render, ...). Every ffmpeg process started through
run() belongs to a task, so cancelling the task, a failure, Ctrl+C, SIGTERM or the server shutting
down ends those processes instead of leaving them burning CPU.

Progress is published three ways: Task.snapshot() (MCP job_status), an on_update callback (CLI bar),
and <project>/.nookcut/progress.json, which `nookcut watch PROJECT` shows live from another terminal.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import signal
import subprocess
import threading
import time
from contextlib import contextmanager

from .errors import Busy, Cancelled, MediaError, explain_ffmpeg

STATE_DIR = ".nookcut"
_PROGRESS_KEY = re.compile(r"^[a-z_0-9]+=\S*$")

_live: set[subprocess.Popen] = set()
_tasks: set["Task"] = set()
_reg_lock = threading.Lock()


def state_dir(project: str) -> str:
    return os.path.join(project, STATE_DIR)


def progress_path(project: str) -> str:
    return os.path.join(state_dir(project), "progress.json")


def cancel_path(project: str) -> str:
    return os.path.join(state_dir(project), "cancel")


def request_cancel(project: str) -> bool:
    """Ask whatever job is running on this project (in any process) to stop. False if none is running."""
    try:
        with open(progress_path(project)) as f:
            snap = json.load(f)
    except (OSError, ValueError):
        return False
    if snap.get("state") != "running" or not _alive(int(snap.get("pid", 0) or 0)):
        return False
    open(cancel_path(project), "w").close()
    return True


class Task:
    def __init__(self, name: str = "", project: str | None = None, on_update=None, track: bool = True):
        self.name, self.project, self.on_update = name, project, on_update
        self.stages: list[tuple[str, float]] = []
        self.stage, self.stage_i, self.done, self.total, self.note = "", -1, 0.0, 0.0, ""
        self.state, self.error = "running", None
        self.started = self.stage_started = time.time()
        self._cancel = threading.Event()
        self._procs: set[subprocess.Popen] = set()
        self._lock = threading.Lock()
        self._last_emit = 0.0
        self.fixed_plan = False  # set by multi-step jobs (auto_edit) so inner steps keep the overall plan
        if track:
            with _reg_lock:
                _tasks.add(self)

    # ----- progress -----

    def plan(self, *stages) -> None:
        """Stages in order, as names or (name, weight); the weights drive the overall percent."""
        if self.fixed_plan:
            return
        self.stages = [(s, 1.0) if isinstance(s, str) else (s[0], float(s[1])) for s in stages]

    def begin(self, stage: str, total: float = 0.0, note: str = "") -> None:
        self.check()
        with self._lock:
            names = [n for n, _ in self.stages]
            if stage not in names:
                self.stages.append((stage, 1.0))
                names.append(stage)
            self.stage, self.stage_i = stage, names.index(stage)
            self.done, self.total, self.note = 0.0, float(total), note
            self.stage_started = time.time()
        self._emit(force=True)

    def advance(self, amount: float) -> None:
        with self._lock:
            self.done = min(self.total or float("inf"), self.done + amount)
        self._emit()

    def set(self, value: float, note: str | None = None) -> None:
        with self._lock:
            self.done = min(self.total, value) if self.total else value
            if note is not None:
                self.note = note
        self._emit()

    def snapshot(self) -> dict:
        with self._lock:
            now = time.time()
            frac = min(1.0, self.done / self.total) if self.total else 0.0
            weights = [w for _, w in self.stages] or [1.0]
            before = sum(weights[:max(0, self.stage_i)])
            current = weights[self.stage_i] if 0 <= self.stage_i < len(weights) else 0.0
            overall = 1.0 if self.state == "done" else (before + current * frac) / sum(weights)
            elapsed = now - self.started
            snap = {"task": self.name, "state": self.state, "stage": self.stage,
                    "step": f"{self.stage_i + 1}/{len(self.stages)}" if self.stages else "",
                    "stage_percent": round(frac * 100, 1), "percent": round(overall * 100, 1),
                    "elapsed_s": round(elapsed, 1), "note": self.note, "pid": os.getpid(), "updated": now}
            stage_elapsed = now - self.stage_started
            if 0.02 < frac < 1 and stage_elapsed > 2:
                snap["stage_eta_s"] = round(stage_elapsed / frac * (1 - frac))
            if 0.05 < overall < 1 and elapsed > 5:
                snap["eta_s"] = round(elapsed / overall * (1 - overall))
            if self.error:
                snap["error"] = self.error
            return snap

    def _emit(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_emit < 0.25:
            return
        self._last_emit = now
        if self.project and self.state == "running" and os.path.exists(cancel_path(self.project)):
            self.cancel()  # `nookcut cancel PROJECT` or the watch screen asked to stop
        snap = self.snapshot()
        if self.project and self.stage_i >= 0:  # never overwrite another job's file before this one starts
            try:
                os.makedirs(state_dir(self.project), exist_ok=True)
                tmp = progress_path(self.project) + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(snap, f)
                os.replace(tmp, progress_path(self.project))
            except OSError:
                pass
        if self.on_update:
            try:
                self.on_update(snap)
            except Exception:
                pass

    def finish(self, error: dict | None = None) -> None:
        self.state = "cancelled" if self.cancelled else ("error" if error else "done")
        self.error = error
        self._kill_all()
        self._emit(force=True)
        with _reg_lock:
            _tasks.discard(self)

    # ----- cancellation -----

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        self._cancel.set()
        self._kill_all()

    def check(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()

    def stop_processes(self) -> None:
        """End this task's ffmpeg processes without marking the task cancelled (used after a failure)."""
        self._kill_all()

    def _kill_all(self) -> None:
        with self._lock:
            procs = list(self._procs)
        for p in procs:
            _stop(p)


def _stop(p: subprocess.Popen) -> None:
    if p.poll() is not None:
        return
    try:
        p.terminate()
        p.wait(timeout=3)
    except subprocess.TimeoutExpired:
        p.kill()
    except OSError:
        pass


def cancel_all() -> None:
    """End every running task and ffmpeg process (server shutdown, Ctrl+C, SIGTERM)."""
    with _reg_lock:
        tasks, procs = list(_tasks), list(_live)
    for t in tasks:
        t.cancel()
    for p in procs:
        _stop(p)


atexit.register(cancel_all)


def install_signal_handlers() -> None:
    """Turn SIGTERM/SIGHUP into a normal exit so cleanup runs (call from the main thread)."""
    def handler(signum, frame):
        cancel_all()
        raise SystemExit(128 + signum)
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), handler)
            except ValueError:  # not the main thread
                pass


def filter_quote(path: str) -> str:
    """A file path as a quoted value inside an ffmpeg filter graph."""
    return "'" + path.replace("\\", "/").replace("'", r"'\''").replace(":", r"\:") + "'"


def _nice(cmd: list[str]) -> list[str]:
    """Run ffmpeg at lower CPU priority so the computer stays usable (NOOKCUT_NICE=0 turns it off)."""
    level = os.getenv("NOOKCUT_NICE", "10")
    if os.name == "posix" and level not in ("", "0"):
        return ["nice", "-n", level] + cmd
    return cmd


def run(cmd: list[str], task: Task | None = None, step: str = "processing", *, progress=None,
        on_chunk=None, capture: bool = False, timeout: float | None = None) -> bytes:
    """Run an ffmpeg/ffprobe command under a task.

    progress(seconds)  called with the output position (adds `-progress pipe:2`; ffmpeg only)
    on_chunk(bytes)    stream stdout to a callback (e.g. raw frames) instead of buffering it
    capture            return stdout as bytes
    Raises MediaError with a readable message on failure and Cancelled if the task was cancelled.
    """
    task = task or Task(track=False)
    task.check()
    if progress is not None:
        cmd = cmd[:1] + ["-progress", "pipe:2", "-nostats"] + cmd[1:]
    p = subprocess.Popen(_nice(cmd), stdin=subprocess.DEVNULL, stderr=subprocess.PIPE,
                         stdout=subprocess.PIPE if (capture or on_chunk) else subprocess.DEVNULL)
    with _reg_lock:
        _live.add(p)
    with task._lock:
        task._procs.add(p)
    if task.cancelled:  # cancelled between check() and Popen
        _stop(p)
    errors: list[str] = []

    def read_stderr():
        for raw in p.stderr:
            line = raw.decode("utf-8", "replace").rstrip()
            if _PROGRESS_KEY.match(line):
                if progress and line.startswith("out_time_us="):
                    try:
                        progress(max(0, int(line.split("=", 1)[1])) / 1e6)
                    except ValueError:
                        pass
            elif line:
                errors.append(line)

    reader = threading.Thread(target=read_stderr, daemon=True)
    reader.start()
    out = bytearray()
    timed_out = threading.Event()
    timer = None
    if timeout:
        timer = threading.Timer(timeout, lambda: (timed_out.set(), _stop(p)))
        timer.daemon = True
        timer.start()
    try:
        if p.stdout:
            for chunk in iter(lambda: p.stdout.read(1 << 20), b""):
                if on_chunk:
                    on_chunk(chunk)
                else:
                    out += chunk
        p.wait()
        reader.join(timeout=2)
    finally:
        if timer:
            timer.cancel()
        _stop(p)
        with _reg_lock:
            _live.discard(p)
        with task._lock:
            task._procs.discard(p)
    if timed_out.is_set():
        raise MediaError(f"{step.capitalize()} took longer than {timeout:.0f}s and was stopped.",
                         "The file may be very long, on a slow drive, or damaged.")
    task.check()
    if p.returncode:
        if p.returncode < 0 and not errors:  # killed by a signal: a crash inside ffmpeg itself
            errors.append(f"ffmpeg crashed (signal {-p.returncode}) without an error message")
        raise explain_ffmpeg("\n".join(errors), step)
    return bytes(out)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


@contextmanager
def project_lock(project: str, what: str):
    """One heavy job per project at a time, across the CLI and every server process."""
    os.makedirs(state_dir(project), exist_ok=True)
    path = os.path.join(state_dir(project), "lock")
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                with open(path) as f:
                    info = json.load(f)
            except (OSError, ValueError):
                info = {}
            if info.get("pid") and _alive(int(info["pid"])):
                raise Busy(f"This project is already busy: {info.get('what', 'a job')} is running "
                           f"(process {info['pid']}).",
                           "Wait for it to finish, or cancel it (cancel_job, or Ctrl+C in its terminal).")
            os.remove(path)  # stale lock from a crashed run
    else:
        raise Busy("Couldn't lock the project folder.", "Try again in a moment.")
    with os.fdopen(fd, "w") as f:
        json.dump({"pid": os.getpid(), "what": what, "since": time.time()}, f)
    if os.path.exists(cancel_path(project)):
        os.remove(cancel_path(project))  # leftover request from an earlier job
    try:
        yield
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
