"""Admission control and live-agent bookkeeping for the subagent control plane.

Covers the two limits Codex keeps independent (``reserve_spawn_slot`` capacity
vs ``exceeds_thread_spawn_depth_limit`` nesting), the pause gate, and the
release-on-actual-exit contract that keeps an interrupted agent's slot held
until its worker really stops.
"""

from __future__ import annotations

import threading

import pytest

from src.services.swarm.agent_supervisor import (
    DEFAULT_MAX_CONCURRENT_CHILDREN,
    DEFAULT_MAX_SPAWN_DEPTH,
    AgentAdmissionError,
    AgentSupervisor,
    max_concurrent_children,
    max_spawn_depth,
)


class _FakeAbort:
    """Stand-in for AbortController — records the reason it was aborted with."""

    def __init__(self) -> None:
        self.reasons: list[str] = []

    def abort(self, reason: str = "") -> None:
        self.reasons.append(reason)


# ── capacity ─────────────────────────────────────────────────────────────


def test_admits_up_to_the_cap_then_refuses():
    sup = AgentSupervisor(max_concurrent=2)

    sup.admit(subagent_id="a")
    sup.admit(subagent_id="b")
    assert sup.live_count() == 2

    with pytest.raises(AgentAdmissionError) as exc:
        sup.admit(subagent_id="c")

    assert exc.value.reason == "capacity"
    # The refusal message is model-facing: it has to say what to do next.
    assert "limit of 2" in str(exc.value)
    assert sup.live_count() == 2, "a refused spawn must not consume a slot"


def test_releasing_frees_a_slot_for_the_next_spawn():
    sup = AgentSupervisor(max_concurrent=1)
    sup.admit(subagent_id="a")

    with pytest.raises(AgentAdmissionError):
        sup.admit(subagent_id="b")

    assert sup.release("a") is True
    sup.admit(subagent_id="b")
    assert sup.live_count() == 1


def test_release_of_an_unknown_id_reports_that_it_held_nothing():
    assert AgentSupervisor().release("never-admitted") is False


def test_readmitting_a_live_id_is_a_no_op_not_a_second_slot():
    # A retry that races its own registration must not double-count.
    sup = AgentSupervisor(max_concurrent=2)
    sup.admit(subagent_id="a", goal="first")
    sup.admit(subagent_id="a", goal="second")

    assert sup.live_count() == 1
    assert sup.snapshot()["active"][0]["goal"] == "first"


# ── depth ────────────────────────────────────────────────────────────────


def test_depth_at_the_limit_is_admitted_and_past_it_is_refused():
    sup = AgentSupervisor(max_depth=2)

    sup.admit(subagent_id="ok", depth=2)

    with pytest.raises(AgentAdmissionError) as exc:
        sup.admit(subagent_id="too-deep", depth=3)

    assert exc.value.reason == "depth"


def test_depth_and_capacity_are_independent_limits():
    # Codex keeps these separate on purpose; a deep-but-lonely agent should not
    # be refused for capacity, nor a shallow crowd for depth.
    sup = AgentSupervisor(max_concurrent=1, max_depth=99)
    sup.admit(subagent_id="deep", depth=50)

    with pytest.raises(AgentAdmissionError) as exc:
        sup.admit(subagent_id="shallow", depth=0)

    assert exc.value.reason == "capacity"


# ── pause ────────────────────────────────────────────────────────────────


def test_pause_blocks_new_spawns_but_leaves_running_ones_alone():
    sup = AgentSupervisor()
    sup.admit(subagent_id="already-running")

    assert sup.set_paused(True) is True

    with pytest.raises(AgentAdmissionError) as exc:
        sup.admit(subagent_id="blocked")

    assert exc.value.reason == "paused"
    assert sup.live_count() == 1, "pausing must not kill in-flight agents"

    sup.set_paused(False)
    sup.admit(subagent_id="allowed-again")
    assert sup.live_count() == 2


def test_pause_is_reported_in_the_snapshot():
    sup = AgentSupervisor()
    assert sup.snapshot()["paused"] is False
    sup.set_paused(True)
    assert sup.snapshot()["paused"] is True
    assert sup.is_paused() is True


# ── interrupt ────────────────────────────────────────────────────────────


def test_interrupt_aborts_the_run_and_reports_it_was_live():
    sup = AgentSupervisor()
    abort = _FakeAbort()
    sup.admit(subagent_id="a", abort_controller=abort)

    assert sup.interrupt("a") is True
    assert abort.reasons == ["interrupted by user"]


def test_interrupt_holds_the_slot_until_the_worker_actually_exits():
    # The whole point of the contract: a terminal *label* is not proof the
    # worker stopped, so the slot stays taken until release() is called.
    sup = AgentSupervisor(max_concurrent=1)
    sup.admit(subagent_id="a", abort_controller=_FakeAbort())
    sup.interrupt("a")

    assert sup.live_count() == 1
    assert sup.snapshot()["active"][0]["status"] == "interrupted"

    with pytest.raises(AgentAdmissionError):
        sup.admit(subagent_id="replacement")

    sup.release("a")
    sup.admit(subagent_id="replacement")


def test_interrupt_of_an_unknown_id_reports_not_found():
    assert AgentSupervisor().interrupt("ghost") is False


def test_interrupt_survives_an_abort_controller_that_raises():
    class Exploding:
        def abort(self, reason: str = "") -> None:
            raise RuntimeError("boom")

    sup = AgentSupervisor()
    sup.admit(subagent_id="a", abort_controller=Exploding())

    # Still reports the agent was live; the failure to abort must not escape
    # into the caller's control-request handler.
    assert sup.interrupt("a") is True


def test_interrupt_all_signals_every_live_agent():
    sup = AgentSupervisor()
    aborts = {name: _FakeAbort() for name in ("a", "b", "c")}
    for name, abort in aborts.items():
        sup.admit(subagent_id=name, abort_controller=abort)

    assert sup.interrupt_all() == 3
    assert all(a.reasons for a in aborts.values())


# ── snapshot ─────────────────────────────────────────────────────────────


def test_snapshot_matches_the_delegation_status_wire_shape():
    # These key names are the contract ui-tui/src/gatewayTypes.ts declares as
    # DelegationStatusResponse; drift here silently empties the overlay.
    sup = AgentSupervisor(max_concurrent=7, max_depth=4)
    sup.admit(
        subagent_id="a1",
        parent_id="root",
        depth=1,
        goal="audit the store",
        model="claude-opus-5",
    )
    sup.set_tool_count("a1", 3)

    snap = sup.snapshot()
    assert set(snap) == {"active", "max_concurrent_children", "max_spawn_depth", "paused"}
    assert snap["max_concurrent_children"] == 7
    assert snap["max_spawn_depth"] == 4

    (entry,) = snap["active"]
    assert set(entry) == {
        "subagent_id", "parent_id", "depth", "goal",
        "model", "status", "started_at", "tool_count",
    }
    assert entry["subagent_id"] == "a1"
    assert entry["parent_id"] == "root"
    assert entry["depth"] == 1
    assert entry["goal"] == "audit the store"
    assert entry["model"] == "claude-opus-5"
    assert entry["status"] == "running"
    assert entry["tool_count"] == 3


def test_snapshot_is_ordered_by_start_time_not_dict_order():
    sup = AgentSupervisor()
    for name in ("c", "a", "b"):
        sup.admit(subagent_id=name)

    ids = [e["subagent_id"] for e in sup.snapshot()["active"]]
    assert ids == ["c", "a", "b"], "poll-to-poll reshuffling would make the overlay jump"


def test_snapshot_is_a_copy_not_a_live_view():
    sup = AgentSupervisor()
    sup.admit(subagent_id="a")
    snap = sup.snapshot()
    sup.release("a")

    assert len(snap["active"]) == 1, "a taken snapshot must not mutate underneath its reader"


def test_status_and_tool_count_updates_are_dropped_after_release():
    sup = AgentSupervisor()
    sup.admit(subagent_id="a")
    sup.release("a")

    # Late progress from a finished worker must not resurrect a row.
    sup.set_status("a", "running")
    sup.set_tool_count("a", 99)
    assert sup.snapshot()["active"] == []


# ── configuration ────────────────────────────────────────────────────────


def test_limits_default_to_the_module_constants(monkeypatch):
    monkeypatch.delenv("CLAWCODEX_MAX_CONCURRENT_AGENTS", raising=False)
    monkeypatch.delenv("CLAWCODEX_MAX_AGENT_DEPTH", raising=False)

    sup = AgentSupervisor()
    assert sup.max_concurrent == DEFAULT_MAX_CONCURRENT_CHILDREN
    assert sup.max_depth == DEFAULT_MAX_SPAWN_DEPTH


@pytest.mark.parametrize(
    "raw,expected",
    [("3", 3), ("1", 1), ("0", 1), ("-4", 1)],
)
def test_env_override_is_read_and_clamped_to_at_least_one(monkeypatch, raw, expected):
    monkeypatch.setenv("CLAWCODEX_MAX_CONCURRENT_AGENTS", raw)
    assert max_concurrent_children() == expected


@pytest.mark.parametrize("raw", ["", "   ", "lots", "3.5"])
def test_garbage_env_falls_back_to_the_default(monkeypatch, raw):
    monkeypatch.setenv("CLAWCODEX_MAX_CONCURRENT_AGENTS", raw)
    monkeypatch.setenv("CLAWCODEX_MAX_AGENT_DEPTH", raw)
    assert max_concurrent_children() == DEFAULT_MAX_CONCURRENT_CHILDREN
    assert max_spawn_depth() == DEFAULT_MAX_SPAWN_DEPTH


def test_explicit_limits_beat_the_environment(monkeypatch):
    monkeypatch.setenv("CLAWCODEX_MAX_CONCURRENT_AGENTS", "99")
    assert AgentSupervisor(max_concurrent=2).max_concurrent == 2


# ── concurrency ──────────────────────────────────────────────────────────


def test_concurrent_admissions_never_exceed_the_cap():
    # Agents are admitted from worker threads, so the check and the insert have
    # to be atomic — a TOCTOU window here would over-admit under fan-out.
    cap = 5
    sup = AgentSupervisor(max_concurrent=cap)
    admitted: list[str] = []
    lock = threading.Lock()
    start = threading.Barrier(20)

    def worker(i: int) -> None:
        start.wait()
        try:
            sup.admit(subagent_id=f"agent-{i}")
        except AgentAdmissionError:
            return
        with lock:
            admitted.append(f"agent-{i}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(admitted) == cap
    assert sup.live_count() == cap
