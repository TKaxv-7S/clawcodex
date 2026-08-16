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
