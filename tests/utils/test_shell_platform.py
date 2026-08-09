"""Tests for ``src/utils/shell_platform`` — the bash/tree-kill platform layer.

Cross-platform by construction: Windows-specific resolution logic is tested
by patching ``sys.platform``-independent seams (env overrides, the memo
slot) rather than by requiring a real Windows host, so the suite pins the
contract on every CI leg.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from src.utils import shell_platform as sp


@pytest.fixture(autouse=True)
def _reset_bash_memo():
    """Each test sees a fresh (unresolved) bash lookup."""
    before = sp._bash_path
    sp._bash_path = False
    yield
    sp._bash_path = before


# ─── find_bash ───────────────────────────────────────────────────────────────


def test_env_override_wins(monkeypatch, tmp_path):
    fake = tmp_path / "bash.exe"
    fake.write_text("")
    monkeypatch.setenv("CLAWCODEX_GIT_BASH_PATH", str(fake))
    assert sp.find_bash() == str(fake)


def test_claude_code_env_override_honored(monkeypatch, tmp_path):
    fake = tmp_path / "bash"
    fake.write_text("")
    monkeypatch.delenv("CLAWCODEX_GIT_BASH_PATH", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_GIT_BASH_PATH", str(fake))
    assert sp.find_bash() == str(fake)


def test_missing_env_override_falls_through(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWCODEX_GIT_BASH_PATH", str(tmp_path / "nope" / "bash.exe"))
    # Must not return the nonexistent override; on POSIX this lands on PATH
    # bash, on Windows on the Git Bash probe. Either way: not the bad path.
    assert sp.find_bash() != str(tmp_path / "nope" / "bash.exe")


def test_find_bash_memoized(monkeypatch, tmp_path):
    fake = tmp_path / "bash.exe"
    fake.write_text("")
    monkeypatch.setenv("CLAWCODEX_GIT_BASH_PATH", str(fake))
    assert sp.find_bash() == str(fake)
    # A changed env after the first resolution is deliberately NOT seen.
    monkeypatch.setenv("CLAWCODEX_GIT_BASH_PATH", str(tmp_path / "other.exe"))
    assert sp.find_bash() == str(fake)


def test_wsl_shim_detection():
    assert sp._is_wsl_shim(r"C:\Windows\System32\bash.exe")
    assert sp._is_wsl_shim(r"c:\windows\system32\BASH.EXE")  # case-insensitive
    assert sp._is_wsl_shim(
        r"C:\Users\u\AppData\Local\Microsoft\WindowsApps\bash.exe"
    )
    assert not sp._is_wsl_shim(r"C:\Program Files\Git\bin\bash.exe")
    assert not sp._is_wsl_shim("/usr/bin/bash")


# ─── bash_argv / bash_env / pwd / path rendering ─────────────────────────────


def test_bash_argv_shape(monkeypatch, tmp_path):
    fake = tmp_path / "bash.exe"
    fake.write_text("")
    monkeypatch.setenv("CLAWCODEX_GIT_BASH_PATH", str(fake))
    assert sp.bash_argv("echo hi") == [str(fake), "-lc", "echo hi"]
    sp._bash_path = False
    assert sp.bash_argv("echo hi", login=False) == [str(fake), "-c", "echo hi"]


def test_bash_argv_raises_when_absent(monkeypatch):
    monkeypatch.delenv("CLAWCODEX_GIT_BASH_PATH", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_GIT_BASH_PATH", raising=False)
    sp._bash_path = None  # simulate "looked up, not found" (Windows w/o Git)
    with pytest.raises(sp.BashNotFoundError) as exc_info:
        sp.bash_argv("echo hi")
    # The error must be actionable — it is what the Bash tool surfaces.
    assert "Git" in str(exc_info.value)


def test_bash_env_fresh_dict_and_windows_knob(monkeypatch):
    base = {"FOO": "bar"}
    env = sp.bash_env(base)
    assert env is not base
    assert env["FOO"] == "bar"
    if sys.platform == "win32":
        assert env["CHERE_INVOKING"] == "1"
    else:
        assert "CHERE_INVOKING" not in env


def test_pwd_command_platform():
    if sys.platform == "win32":
        assert sp.pwd_command() == "pwd -W"
    else:
        assert sp.pwd_command() == "pwd"


def test_to_shell_path():
    if sys.platform == "win32":
        assert sp.to_shell_path(r"C:\Users\x\file.txt") == "C:/Users/x/file.txt"
    else:
        assert sp.to_shell_path("/tmp/file.txt") == "/tmp/file.txt"


# ─── popen_tree_kwargs / kill_process_tree ───────────────────────────────────


def test_popen_tree_kwargs_platform_split():
    kwargs = sp.popen_tree_kwargs()
    if sys.platform == "win32":
        assert kwargs == {
            "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        }
    else:
        assert kwargs == {"start_new_session": True}


def test_popen_tree_kwargs_accepted_by_popen():
    """The kwargs must be spawnable as-is on the current platform — this is
    the regression test for ``start_new_session=True`` raising ValueError on
    Windows."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "print('ok')"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **sp.popen_tree_kwargs(),
    )
    out, _ = proc.communicate(timeout=30)
    assert proc.returncode == 0
    assert out.strip() == "ok"


def test_kill_process_tree_terminates_child():
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        **sp.popen_tree_kwargs(),
    )
    try:
        sp.kill_process_tree(proc.pid, force=True)
        # Must die well before the sleep would end.
        proc.wait(timeout=15)
    finally:
        if proc.poll() is None:  # pragma: no cover - cleanup on failure
            proc.kill()
            proc.wait(timeout=15)
    assert proc.returncode != 0


def test_kill_process_tree_missing_pid_never_raises():
    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"], **sp.popen_tree_kwargs()
    )
    proc.wait(timeout=30)
    # The pid is now dead (possibly recycled); the call must swallow it.
    sp.kill_process_tree(proc.pid, force=True)
    sp.kill_process_tree(proc.pid, force=False)
