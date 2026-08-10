"""Platform layer for shell-command execution (POSIX bash vs. Windows Git Bash).

The Bash tool (and everything that reuses its spawn machinery: background
tasks, Monitor, autofix) executes commands through ``bash -lc <script>`` and
kills runaway commands by process *tree*. Both halves are platform-specific:

* **Which bash?** On POSIX, ``bash`` from ``$PATH``. On Windows, the only
  bash with the right semantics is **Git Bash** (MSYS2) from Git for
  Windows. Two lookalikes must be avoided: ``C:\\Windows\\System32\\bash.exe``
  and the WindowsApps shim — both launch **WSL**, so commands would run in a
  Linux VM against a different filesystem view, breaking every workspace
  path the agent passes. :func:`find_bash` therefore resolves Git Bash
  explicitly (env override → derive from ``git.exe`` → well-known install
  dirs) and only trusts ``PATH`` entries that are not WSL shims.

* **How to kill a tree?** POSIX children are spawned with
  ``start_new_session=True`` and killed with ``os.killpg``. Windows has no
  process groups in the POSIX sense and ``signal.SIGKILL`` does not even
  exist there — :func:`kill_process_tree` wraps the split: ``killpg`` on
  POSIX, ``taskkill /T /F`` (the same mechanism as TS ``tree-kill``) on
  Windows.

* **Login-shell cwd.** Git Bash's ``/etc/profile`` cd's to ``$HOME`` unless
  ``CHERE_INVOKING`` is set — silently moving every ``bash -lc`` out of the
  workspace. :func:`bash_env` pins it.

* **pwd for cwd-tracking.** The Bash tool appends ``pwd > file`` to track
  ``cd`` across invocations. Git Bash's plain ``pwd`` prints an MSYS path
  (``/c/Users/…``) that Python cannot resolve; ``pwd -W`` prints a real
  Windows path (``C:/Users/…``). :func:`pwd_command` picks per platform.

Import cost: stdlib only, no heavy imports — safe for the CLI fast paths.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

__all__ = [
    "BashNotFoundError",
    "bash_argv",
    "bash_env",
    "find_bash",
    "find_git",
    "kill_process_tree",
    "popen_tree_kwargs",
    "pwd_command",
    "to_shell_path",
]

#: Env overrides consulted first, in order. The CLAUDE_CODE_ name is honored
#: for parity with Claude Code on Windows, where users commonly already have
#: it pointing at a working Git Bash.
_BASH_ENV_OVERRIDES = ("CLAWCODEX_GIT_BASH_PATH", "CLAUDE_CODE_GIT_BASH_PATH")

_WINDOWS_INSTALL_HINT = (
    "Git Bash is required to run shell commands on Windows. Install Git for "
    "Windows (winget install --id Git.Git, or https://git-scm.com/download/win) "
    "or point CLAWCODEX_GIT_BASH_PATH at an existing bash.exe."
)


class BashNotFoundError(RuntimeError):
    """No usable bash interpreter on this machine (Windows without Git Bash)."""


def _is_wsl_shim(path: str) -> bool:
    """True for the two Windows bash lookalikes that launch WSL, not a shell.

    ``System32\\bash.exe`` is the WSL launcher; ``WindowsApps`` hosts the
    Store-app alias for it. Running either would execute the command inside a
    Linux distro (different filesystem, different tools) — never what the
    Bash tool means. Case-insensitive because Windows paths are.
    """
    lowered = path.replace("/", "\\").lower()
    return (
        lowered.endswith("\\windows\\system32\\bash.exe")
        or "\\microsoft\\windowsapps\\" in lowered
    )


def _git_bash_candidates() -> list[Path]:
    """Probable Git Bash locations, most-authoritative first.

    Derivation from ``git.exe`` beats the hardcoded list: it follows the
    user's actual install (including portable/scoop installs) instead of
    guessing. Git for Windows layouts place ``git.exe`` in ``<root>\\cmd`` or
    ``<root>\\mingw64\\bin``; bash lives in ``<root>\\bin`` and
    ``<root>\\usr\\bin``.
    """
    candidates: list[Path] = []
    git = shutil.which("git")
    if git:
        git_path = Path(git).resolve()
        for root in (git_path.parent.parent, git_path.parent.parent.parent):
            candidates.append(root / "bin" / "bash.exe")
            candidates.append(root / "usr" / "bin" / "bash.exe")
    for env_var, suffix in (
        ("ProgramFiles", r"Git\bin\bash.exe"),
        ("ProgramFiles(x86)", r"Git\bin\bash.exe"),
        ("LOCALAPPDATA", r"Programs\Git\bin\bash.exe"),
        ("USERPROFILE", r"scoop\apps\git\current\bin\bash.exe"),
    ):
        base = os.environ.get(env_var)
        if base:
            candidates.append(Path(base) / suffix)
    return candidates


#: Memoized result. ``False`` = not looked up yet; ``None`` = looked up and
#: absent. Module-level (not functools.cache) so tests can reset it by
#: assigning ``_bash_path = False``.
_bash_path: str | None | bool = False


def find_bash() -> str | None:
    """Absolute path of the bash to run tool commands with, or ``None``.

    POSIX: ``bash`` from ``PATH`` (present on every supported system).
    Windows: env override → PATH (excluding WSL shims — running from inside
    Git Bash puts the real one on PATH) → derived from ``git.exe`` →
    well-known install dirs. Memoized per process (including the not-found
    result) — a bash installed mid-session is rare and a restart picks it up.
    """
    global _bash_path
    if _bash_path is not False:
        return _bash_path  # type: ignore[return-value]

    for var in _BASH_ENV_OVERRIDES:
        override = os.environ.get(var)
        if override and Path(override).is_file():
            _bash_path = str(Path(override))
            return _bash_path

    if sys.platform != "win32":
        _bash_path = shutil.which("bash") or "bash"
        return _bash_path

    on_path = shutil.which("bash")
    if on_path and not _is_wsl_shim(on_path):
        _bash_path = str(Path(on_path))
        return _bash_path

    for cand in _git_bash_candidates():
        try:
            if cand.is_file():
                _bash_path = str(cand)
                return _bash_path
        except OSError:  # pragma: no cover - unreadable mount/ACL edge
            continue

    _bash_path = None
    return None


def bash_argv(script: str, *, login: bool = True) -> list[str]:
    """Argv that runs ``script`` through bash on this platform.

    Raises :class:`BashNotFoundError` (with an actionable install hint) on
    Windows machines without Git Bash — the caller surfaces it as the tool
    error instead of a FileNotFoundError from Popen deep in the stack.
    """
    bash = find_bash()
    if bash is None:
        raise BashNotFoundError(_WINDOWS_INSTALL_HINT)
    return [bash, "-lc" if login else "-c", script]


def bash_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for a bash child; adds the Windows-required MSYS knobs.

    ``CHERE_INVOKING=1`` stops Git Bash's login profile from cd'ing to
    ``$HOME`` — without it every ``bash -lc`` ignores ``cwd=`` and the Bash
    tool's persistent-cwd tracking follows ``$HOME`` around. Always returns a
    fresh dict; POSIX passes ``base`` through untouched (copied).
    """
    env = dict(os.environ if base is None else base)
    if sys.platform == "win32":
        env.setdefault("CHERE_INVOKING", "1")
    return env


def pwd_command() -> str:
    """The pwd invocation whose output Python can later resolve as a path.

    Git Bash's ``pwd`` prints MSYS paths (``/c/Users/…``); ``pwd -W`` prints
    the Windows form (``C:/Users/…``). POSIX keeps plain ``pwd``.
    """
    return "pwd -W" if sys.platform == "win32" else "pwd"


def to_shell_path(path: str | os.PathLike[str]) -> str:
    """Render *path* so it is safe to embed in a bash script on any platform.

    Windows paths contain backslashes, which bash treats as escapes (or
    literally inside single quotes — either way not a path separator). Git
    Bash accepts forward-slash drive paths (``C:/Users/…``) natively, so
    normalizing separators is sufficient and unambiguous. POSIX paths pass
    through unchanged.
    """
    text = str(path)
    return text.replace("\\", "/") if sys.platform == "win32" else text


def popen_tree_kwargs() -> dict[str, object]:
    """Popen kwargs that make the child a killable process tree root.

    POSIX: ``start_new_session=True`` (``setsid``) so ``os.killpg`` reaches
    the whole tree. Windows: ``CREATE_NEW_PROCESS_GROUP`` — mainly to detach
    the child from the console's Ctrl-C group (``start_new_session`` raises
    ``ValueError`` on Windows); the tree kill itself goes through
    ``taskkill /T``, which walks the child list and needs no group.
    """
    if sys.platform == "win32":
        return {
            "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        }
    return {"start_new_session": True}


def kill_process_tree(pid: int, *, force: bool = True) -> None:
    """Terminate *pid* and its descendants; never raises.

    POSIX sends SIGKILL (``force=True``) or SIGTERM to the process group —
    callers must have spawned the child with :func:`popen_tree_kwargs`.
    Windows always uses ``taskkill /T /F``: a graceful ``taskkill`` without
    ``/F`` posts WM_CLOSE, which console children have no window to receive,
    so it reliably fails — force is the only kill that means anything there
    (same choice as TS ``tree-kill``).
    """
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=10,
            )
        else:
            import signal as _signal

            sig = _signal.SIGKILL if force else _signal.SIGTERM
            os.killpg(os.getpgid(pid), sig)
    except (OSError, ProcessLookupError, PermissionError, subprocess.SubprocessError):
        # Already gone (race vs. natural exit), no taskkill on PATH, or
        # insufficient privileges — the caller's subsequent wait()/poll()
        # surfaces the child's true state either way.
        pass


#: Env override for the git binary, then the CLAUDE_CODE_ twin (parity).
_GIT_ENV_OVERRIDES = ("CLAWCODEX_GIT_PATH", "CLAUDE_CODE_GIT_PATH")

_git_path: str | None | bool = False


def _git_candidates() -> list[Path]:
    """Well-known Git-for-Windows ``git.exe`` locations, most-authoritative first.

    Includes the desktop app's own managed git
    (``%LOCALAPPDATA%\\clawcodex\\git``) so a desktop-provisioned git is found
    even when nothing is on PATH.
    """
    candidates: list[Path] = []
    for env_var, suffixes in (
        ("ProgramFiles", (r"Git\cmd\git.exe", r"Git\bin\git.exe")),
        ("ProgramFiles(x86)", (r"Git\cmd\git.exe", r"Git\bin\git.exe")),
        ("LOCALAPPDATA", (
            r"Programs\Git\cmd\git.exe",
            r"clawcodex\git\cmd\git.exe",
            r"clawcodex\git\bin\git.exe",
        )),
        ("USERPROFILE", (r"scoop\apps\git\current\cmd\git.exe",)),
    ):
        base = os.environ.get(env_var)
        if base:
            for suffix in suffixes:
                candidates.append(Path(base) / suffix)
    return candidates


def find_git() -> str:
    """Absolute path of the git binary, or the bare name ``"git"`` as a fallback.

    Why this exists: a bare ``["git", ...]`` relies on ``git`` being on the
    process PATH. That holds for a shell-launched CLI, but a **GUI-launched**
    parent (the desktop app's Electron process) inherits only the minimal
    login PATH, which on Windows routinely omits Git for Windows
    (``C:\\Program Files\\Git\\cmd``). The Python backend it spawns then fails
    every ``git`` call — ``git_repo_root`` comes back ``None``, sessions never
    group under a repo, and the desktop's worktree lanes/selection vanish.
    Resolving the binary explicitly (env override → PATH → known install
    dirs) makes backend git work regardless of the spawning process's PATH.

    POSIX: ``git`` from PATH (present on every supported system), falling back
    to the bare name. Memoized per process (including the fallback).
    """
    global _git_path
    if _git_path is not False:
        return _git_path  # type: ignore[return-value]

    for var in _GIT_ENV_OVERRIDES:
        override = os.environ.get(var)
        if override and Path(override).is_file():
            _git_path = str(Path(override))
            return _git_path

    on_path = shutil.which("git")
    if on_path:
        _git_path = str(Path(on_path))
        return _git_path

    if sys.platform == "win32":
        for cand in _git_candidates():
            try:
                if cand.is_file():
                    _git_path = str(cand)
                    return _git_path
            except OSError:  # pragma: no cover - unreadable mount/ACL edge
                continue

    # Nothing resolved — return the bare name so callers behave exactly as
    # before (their own error handling reports "git not found").
    _git_path = "git"
    return _git_path
