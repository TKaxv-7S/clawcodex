"""Background task registry: detached shell commands in subprocesses."""

from __future__ import annotations

import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass

from src.utils.shell_platform import (
    bash_env,
    find_bash,
    kill_process_tree,
    popen_tree_kwargs,
)

_MAX_OUTPUT = 8000


@dataclass
class BgTask:
    id: str
    command: str
    status: str = "running"  # running | done | failed | killed
    output: str = ""
    exit_code: int | None = None
    started: float = 0.0


class BackgroundTasks:
    """Thread-safe registry of background subprocess tasks."""

    def __init__(self) -> None:
        self._tasks: dict[str, BgTask] = {}
        self._procs: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    def start(self, command: str, cwd: str, now: float = 0.0) -> BgTask:
        tid = uuid.uuid4().hex[:8]
        task = BgTask(id=tid, command=command, status="running", started=now)
        # On Windows ``shell=True`` means cmd.exe — wrong for /bg commands
        # written with POSIX semantics (same policy as the Bash tool and
        # hooks). Route through Git Bash there when available; a bash-less
        # Windows falls back to cmd.exe. ``popen_tree_kwargs`` keeps the
        # spawned tree killable as a unit (see ``kill``).
        win_bash = find_bash() if sys.platform == "win32" else None
        if win_bash is not None:
            spawn_args: object = [win_bash, "-c", command]
            spawn_kwargs: dict[str, object] = {"shell": False, "env": bash_env()}
        else:
            spawn_args = command
            spawn_kwargs = {"shell": True}
        proc = subprocess.Popen(  # noqa: S602 - intentional shell task, user-initiated
            spawn_args,
            cwd=cwd or None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            **spawn_kwargs,
            **popen_tree_kwargs(),
        )
        with self._lock:
            self._tasks[tid] = task
            self._procs[tid] = proc
        threading.Thread(target=self._wait, args=(tid, proc), daemon=True).start()
        return task

    def _wait(self, tid: str, proc: subprocess.Popen) -> None:
        out = ""
        try:
            out, _ = proc.communicate()
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            t = self._tasks.get(tid)
            if t is not None:
                t.output = (out or "")[-_MAX_OUTPUT:]
                t.exit_code = proc.returncode
                if t.status != "killed":
                    t.status = "done" if proc.returncode == 0 else "failed"
            self._procs.pop(tid, None)

    def list(self) -> list[BgTask]:
        with self._lock:
            return list(self._tasks.values())

    def get(self, tid: str) -> BgTask | None:
        with self._lock:
            return self._tasks.get(tid)

    def output(self, tid: str) -> str | None:
        with self._lock:
            t = self._tasks.get(tid)
            return t.output if t is not None else None

    def kill(self, tid: str) -> bool:
        with self._lock:
            proc = self._procs.get(tid)
            t = self._tasks.get(tid)
            if t is not None and t.status == "running":
                t.status = "killed"
        if proc is not None:
            # Tree kill first (group on POSIX / taskkill on Windows) so the
            # shell's children die with it; terminate() covers the edge where
            # no group exists. Both are no-ops on an already-dead process.
            kill_process_tree(proc.pid, force=False)
            try:
                proc.terminate()
                return True
            except Exception:  # noqa: BLE001
                return False
        return False
