"""The stuck-background-task guard on TaskOutput.

A background command that cannot finish on its own turns TaskOutput into an
absorbing state: the model polls "still running, no new output" forever. On
terminal-bench 2.1 crack-7z-hash (deepseek-v4-pro, 2026-07-27) an agent
launched a john-the-ripper brute-force in the background and spent its final
~15 polls / 20 minutes waiting on it until the harness killed the trial;
opus-5 finished the same task in 18 seconds.

The guard nudges toward TaskStop after a run of no-progress polls, and must
not fire on progressing jobs (M1: even ones whose reported output has
saturated the 200KB read window), finished jobs, non-blocking polls, or
agent subtasks. The hint must survive large-result persistence (M2).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.tool_system.context import ToolContext
from src.tool_system.protocol import ToolResult
from src.tool_system.tools import tasks_v2
from src.tool_system.tools.tasks_v2 import (
    _STUCK_MIN_WALL_SECONDS,
    _STUCK_POLL_THRESHOLD,
    _guard_repeated_polls,
)


@pytest.fixture(autouse=True)
def _no_wall_floor(monkeypatch):
    """Drop the wall-clock floor to 0 so poll-count logic is testable directly.

    The floor itself is covered by TestWallClockFloor with the real value.
    """
    monkeypatch.setattr(tasks_v2, "_STUCK_MIN_WALL_SECONDS", 0.0)


def _ctx(tmp_path: Path) -> ToolContext:
    ctx = ToolContext(workspace_root=tmp_path)
    log = tmp_path / "b1.log"
    log.write_bytes(b"")
    ctx.background_bash_tasks["b1"] = {"output_path": str(log)}
    return ctx


def _set_log(ctx: ToolContext, nbytes: int) -> None:
    """Set the on-disk log to a given size — the guard's progress signal."""
    Path(ctx.background_bash_tasks["b1"]["output_path"]).write_bytes(b"x" * nbytes)


def _running(output: str = "") -> ToolResult:
    """A poll that timed out with the task still running (the stuck shape)."""
    return ToolResult(
        name="TaskOutput",
        output={
            "retrieval_status": "timeout",
            "task": {
                "task_id": "b1",
                "task_type": "bash_background",
                "status": "running",
                "output": output,
            },
        },
    )


def _hint_in(result: ToolResult) -> bool:
    return "stuck-task guard" in str(result.output.get("task", {}).get("output", ""))


class TestStuckGuardFires:
    def test_nudges_after_threshold_polls_with_no_growth(self, tmp_path):
        ctx = _ctx(tmp_path)
        _set_log(ctx, 100)  # silent job: size never changes
        results = [_guard_repeated_polls("b1", _running(), ctx)
                   for _ in range(_STUCK_POLL_THRESHOLD + 2)]
        assert not any(_hint_in(r) for r in results[:_STUCK_POLL_THRESHOLD])
        assert _hint_in(results[_STUCK_POLL_THRESHOLD])

    def test_hint_recommends_taskstop_with_the_id(self, tmp_path):
        ctx = _ctx(tmp_path)
        _set_log(ctx, 50)
        for _ in range(_STUCK_POLL_THRESHOLD + 2):
            out = _guard_repeated_polls("b1", _running("frozen"), ctx)
        text = out.output["task"]["output"]
        assert "TaskStop(task_id='b1')" in text
        assert "frozen" in text, "the real output must be preserved, not replaced"

    def test_hint_is_prepended_and_also_top_level(self, tmp_path):
        """M2: survive head-truncation of a persisted large result."""
        ctx = _ctx(tmp_path)
        _set_log(ctx, 50)
        for _ in range(_STUCK_POLL_THRESHOLD + 2):
            out = _guard_repeated_polls("b1", _running("REAL_OUTPUT"), ctx)
        text = out.output["task"]["output"]
        assert text.startswith("[stuck-task guard]"), "hint must be at the head"
        assert out.output["stuck_task_hint"].startswith("[stuck-task guard]")

    def test_hint_repeats_so_it_cannot_be_missed_once(self, tmp_path):
        ctx = _ctx(tmp_path)
        _set_log(ctx, 10)
        for _ in range(_STUCK_POLL_THRESHOLD + 4):
            out = _guard_repeated_polls("b1", _running("x"), ctx)
        assert _hint_in(out), "the nudge must persist across further stuck polls"


class TestStuckGuardStaysQuiet:
    def test_progressing_job_is_never_nudged(self, tmp_path):
        ctx = _ctx(tmp_path)
        for i in range(_STUCK_POLL_THRESHOLD + 5):
            _set_log(ctx, 100 * (i + 1))  # file grows every poll
            out = _guard_repeated_polls("b1", _running("line"), ctx)
            assert not _hint_in(out)

    def test_saturated_output_but_growing_file_is_not_nudged(self, tmp_path):
        """M1: reported output length is constant (200KB cap) but the file
        grows — a chatty progressing job. Must NOT nudge."""
        ctx = _ctx(tmp_path)
        capped = "y" * 200_000  # what the poll returns, unchanged each time
        for i in range(_STUCK_POLL_THRESHOLD + 5):
            _set_log(ctx, 200_000 + 5_000 * i)  # underlying file keeps growing
            out = _guard_repeated_polls("b1", _running(capped), ctx)
            assert not _hint_in(out), "file growth means progress, not stuck"

    def test_counter_resets_when_file_advances(self, tmp_path):
        ctx = _ctx(tmp_path)
        sizes = [100, 100, 200, 200]  # two stalls, growth, two stalls — never 3
        for s in sizes:
            _set_log(ctx, s)
            out = _guard_repeated_polls("b1", _running("o"), ctx)
        assert not _hint_in(out)

    def test_finished_task_is_not_nudged(self, tmp_path):
        ctx = _ctx(tmp_path)
        _set_log(ctx, 100)
        done = ToolResult(
            name="TaskOutput",
            output={
                "retrieval_status": "success",
                "task": {"task_id": "b1", "task_type": "bash_background",
                         "status": "completed", "output": "result"},
            },
        )
        for _ in range(_STUCK_POLL_THRESHOLD + 2):
            out = _guard_repeated_polls("b1", done, ctx)
        assert not _hint_in(out)

    def test_non_blocking_polls_DO_count(self, tmp_path):
        """block=False returns retrieval_status="success" on a running task,
        but the guard MUST still count it — 13 of the 15 crack-7z-hash polls
        were block=False, so gating on the timeout status would make the guard
        inert for the exact failure it exists to catch.
        """
        ctx = _ctx(tmp_path)
        _set_log(ctx, 100)
        nb = ToolResult(
            name="TaskOutput",
            output={
                "retrieval_status": "success",  # what block=False returns
                "task": {"task_id": "b1", "task_type": "bash_background",
                         "status": "running", "output": ""},
            },
        )
        results = [_guard_repeated_polls("b1", nb, ctx)
                   for _ in range(_STUCK_POLL_THRESHOLD + 2)]
        assert _hint_in(results[_STUCK_POLL_THRESHOLD]), (
            "block=False polls on a stuck job must fire the guard"
        )

    def test_agent_subtask_is_not_guarded(self, tmp_path):
        """A local-agent poll with no new output is normal, not stuck.

        The entry IS registered so the type gate — not a missing-entry
        short-circuit — is what keeps it quiet.
        """
        ctx = _ctx(tmp_path)
        log = tmp_path / "a1.log"
        log.write_bytes(b"data")
        ctx.background_bash_tasks["a1"] = {"output_path": str(log)}
        agent_poll = ToolResult(
            name="TaskOutput",
            output={
                "retrieval_status": "not_ready",
                "task": {"task_id": "a1", "task_type": "local_agent",
                         "status": "running", "output": ""},
            },
        )
        for _ in range(_STUCK_POLL_THRESHOLD + 2):
            out = _guard_repeated_polls("a1", agent_poll, ctx)
        assert "stuck-task guard" not in str(out.output)


class TestWallClockFloor:
    def test_tight_polls_do_not_fire_before_the_floor(self, tmp_path, monkeypatch):
        """m4: block=True with a 1s timeout can hit the poll count in seconds;
        the wall-clock floor must hold the nudge back."""
        monkeypatch.setattr(tasks_v2, "_STUCK_MIN_WALL_SECONDS", 60.0)
        ctx = _ctx(tmp_path)
        _set_log(ctx, 100)
        # Many polls, but no monotonic-time advance in the test → 0 wall seconds.
        for _ in range(_STUCK_POLL_THRESHOLD + 6):
            out = _guard_repeated_polls("b1", _running(), ctx)
        assert not _hint_in(out), "poll count reached but wall floor not met"


class TestHintSurvivesPersistence:
    """M2 end-to-end: a >50KB guarded result is persisted with a head
    preview; the hint must land in what the model still sees inline."""

    def test_hint_survives_large_result_persistence(self, tmp_path, monkeypatch):
        import json
        from src.services.tool_execution.tool_result_persistence import (
            maybe_persist_large_tool_result,
        )

        monkeypatch.setattr(tasks_v2, "_STUCK_MIN_WALL_SECONDS", 0.0)
        ctx = _ctx(tmp_path)
        _set_log(ctx, 300)  # silent brute-force: constant size
        big = "Z" * 200_000  # what the poll returns — far over the 50K threshold
        for _ in range(_STUCK_POLL_THRESHOLD + 2):
            out = _guard_repeated_polls("b1", _running(big), ctx)

        serialized = json.dumps(out.output)
        assert len(serialized) > 50_000, "precondition: result is large enough to persist"

        block = {"type": "tool_result", "content": serialized}
        persisted = maybe_persist_large_tool_result(
            block,
            "TaskOutput",
            threshold=50_000,
            tool_results_dir=tmp_path / "tr",
        )
        rendered = json.dumps(persisted)
        assert "stuck-task guard" in rendered, (
            "the nudge was truncated away by head-preview — the model would "
            "poll on, defeating the guard on exactly the large-output jobs"
        )
        assert "TaskStop(task_id='b1')" in rendered
