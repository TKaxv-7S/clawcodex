"""Nano bash: no timeout ceiling (pi parity for long-running work).

TB 2.1 evidence (tb21-nano-flash-max-2): the 2-minute default / 10-minute
cap forced nohup-and-poll loops — 225 poll-pattern commands across the 25
failed tasks; compile-compcert burned 111 polls and hit the 300-turn
ceiling mid-build. Under nano a long build blocks in ONE call; the abort
signal and the task wall-clock remain the backstops. Stock behavior is
byte-identical.
"""

from __future__ import annotations

import pytest

from src.nano.state import set_nano_mode
from src.tool_system.errors import ToolInputError
from src.tool_system.tools import BashTool


@pytest.fixture
def ctx(tmp_path):
    from src.tool_system.context import ToolContext

    return ToolContext(cwd=tmp_path, workspace_root=tmp_path)


def _run(ctx, **extra):
    return BashTool.call({"command": "echo long-ok", **extra}, ctx)


def test_nano_accepts_timeouts_beyond_the_stock_cap(ctx):
    set_nano_mode(True)
    result = _run(ctx, timeout=1_200_000)  # 20 minutes
    assert "long-ok" in str(result.output)


def test_stock_still_rejects_beyond_cap(ctx):
    with pytest.raises(ToolInputError, match="must not exceed"):
        _run(ctx, timeout=1_200_000)


def test_nano_default_runs_without_explicit_timeout(ctx):
    set_nano_mode(True)
    result = _run(ctx)
    assert "long-ok" in str(result.output)


def test_stock_default_unchanged(ctx):
    result = _run(ctx)
    assert "long-ok" in str(result.output)


def test_minimum_floor_still_enforced_in_nano(ctx):
    set_nano_mode(True)
    with pytest.raises(ToolInputError, match="at least 1000 ms"):
        _run(ctx, timeout=10)


def test_idle_watchdog_kills_silent_stuck_command(ctx, monkeypatch):
    import time

    monkeypatch.setenv("NANO_BASH_IDLE_TIMEOUT_S", "5")
    set_nano_mode(True)
    t0 = time.monotonic()
    result = BashTool.call({"command": "sleep 60"}, ctx)
    elapsed = time.monotonic() - t0
    assert elapsed < 15, f"watchdog too slow: {elapsed:.0f}s"
    assert result.is_error
    assert "no output" in str(result.output)
    assert "re-run" in str(result.output)


def test_idle_watchdog_spares_chatty_long_command(ctx, monkeypatch):
    monkeypatch.setenv("NANO_BASH_IDLE_TIMEOUT_S", "5")
    set_nano_mode(True)
    result = BashTool.call(
        {"command": "for i in 1 2 3 4; do echo tick-$i; sleep 2; done; echo chatty-done"},
        ctx,
    )
    assert not result.is_error
    assert "chatty-done" in str(result.output)


def test_pipe_drain_handles_output_beyond_pipe_buffer(ctx):
    # >64KB written before exit used to fill the un-drained pipe and block
    # the child forever (masked by the stock hard timeout as a bogus
    # "timed out"). The concurrent readers drain it.
    set_nano_mode(True)
    result = BashTool.call(
        {"command": "echo drained-ok; python3 -c \"print('x'*300000)\""}, ctx
    )
    assert not result.is_error
    assert "drained-ok" in str(result.output)
    assert result.output.get("exit_code") == 0


def test_pipe_drain_fixes_stock_mode_too(ctx):
    result = BashTool.call(
        {"command": "echo stock-ok; python3 -c \"print('y'*300000)\""}, ctx
    )
    assert not result.is_error
    assert "stock-ok" in str(result.output)
    assert result.output.get("exit_code") == 0


def test_stock_has_no_idle_watchdog(ctx, monkeypatch):
    # Stock keeps its hard-timeout model: a 7s-silent command with a 15s
    # timeout completes even with the env set (the watchdog is nano-only).
    monkeypatch.setenv("NANO_BASH_IDLE_TIMEOUT_S", "5")
    result = BashTool.call({"command": "sleep 7 && echo stock-quiet-ok", "timeout": 15000}, ctx)
    assert not result.is_error
    assert "stock-quiet-ok" in str(result.output)


def test_nano_bash_schema_drops_background_trap():
    # run_in_background needs TaskOutput (absent in nano) — a backgrounded
    # command strands its output; the nano schema must not advertise it.
    from src.nano.registry import build_nano_registry

    bash = next(t for t in build_nano_registry().list_tools() if t.name == "Bash")
    assert "run_in_background" not in bash.input_schema["properties"]
    assert "1-600" not in str(bash.input_schema["properties"].get("timeout_s", {}))
    # Stock schema untouched.
    assert "run_in_background" in BashTool.input_schema["properties"]
