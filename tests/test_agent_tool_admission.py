"""The Agent tool consults the session supervisor before spawning.

These exercise the wiring rather than the supervisor itself (that has its own
suite in tests/services/test_agent_supervisor.py): that a refused spawn comes
back as a model-facing tool error instead of an exception, that the refusal
never reaches the provider, and that child contexts share the parent's
supervisor so nesting is admitted against one registry.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from src.services.swarm.agent_supervisor import AgentSupervisor
from src.tool_system.context import ToolContext
from src.tool_system.registry import ToolRegistry
from src.tool_system.tools.agent import make_agent_tool


class _ExplodingProvider:
    """Any real use of the provider fails the test loudly.

    Admission is refused before the run starts, so a refused spawn must not
    touch this object at all.
    """

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"provider was used ({name!r}) on a refused spawn")


@pytest.fixture
def agent_tool():
    return make_agent_tool(ToolRegistry(), provider=_ExplodingProvider())


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(workspace_root=tmp_path, cwd=tmp_path)


def _call(tool, ctx: ToolContext, **overrides: Any):
    payload = {"description": "d", "prompt": "do a thing", **overrides}
    return tool.call(payload, ctx)


def test_spawn_is_refused_once_the_session_is_at_capacity(agent_tool, ctx):
    ctx.agent_supervisor = AgentSupervisor(max_concurrent=1)
    ctx.agent_supervisor.admit(subagent_id="already-running")

    result = _call(agent_tool, ctx)

    assert result.is_error is True
    assert result.output["status"] == "refused"
    assert result.output["reason"] == "capacity"
    # The model reads this string and has to know what to do with it.
    assert "limit of 1" in result.output["error"]


def test_spawn_is_refused_while_delegation_is_paused(agent_tool, ctx):
    ctx.agent_supervisor.set_paused(True)

    result = _call(agent_tool, ctx)

    assert result.is_error is True
    assert result.output["reason"] == "paused"


def test_a_refused_spawn_leaves_no_trace_in_the_registry(agent_tool, ctx):
    ctx.agent_supervisor = AgentSupervisor(max_concurrent=1)
    ctx.agent_supervisor.admit(subagent_id="already-running")

    _call(agent_tool, ctx)

    # A half-registered agent id would show up in the overlay as a phantom row.
    assert ctx.agent_supervisor.live_count() == 1
    assert len(ctx.runtime_tasks) == 0


def test_refusal_is_returned_not_raised(agent_tool, ctx):
    # A raised exception would abort the turn; the model needs to see this as a
    # tool result it can react to.
    ctx.agent_supervisor.set_paused(True)
    result = _call(agent_tool, ctx)
    assert result.output["status"] == "refused"


def test_a_launch_that_fails_after_admission_gives_the_slot_back(agent_tool, ctx, monkeypatch):
    # A name collision raises out of the background wrapper AFTER admission.
    # Without the guard the slot would be stranded for the life of the session.
    from src.services.swarm import agent_name_registry as reg

    def boom(*args, **kwargs):
        raise reg.AgentNameAlreadyClaimedError("taken", "other-agent")

    monkeypatch.setattr(ctx.agent_name_registry, "claim_or_raise", boom)

    with pytest.raises(Exception):
        _call(agent_tool, ctx, name="taken", run_in_background=True)

    assert ctx.agent_supervisor.live_count() == 0


def test_top_level_spawns_have_no_parent(agent_tool, ctx):
    # ctx.agent_id is None on the root session, which is what makes a
    # top-level delegation a tree root rather than an orphan.
    ctx.agent_supervisor = AgentSupervisor(max_concurrent=1)
    ctx.agent_supervisor.admit(subagent_id="filler")
    assert ctx.agent_id is None


def test_a_child_spawn_records_its_parent_agent_id(tmp_path: Path):
    # The parent id has to be an id that appears as a subagent_id elsewhere in
    # the snapshot, or the clients cannot nest the row under anything.
    sup = AgentSupervisor()
    sup.admit(subagent_id="parent-1")
    sup.admit(subagent_id="child-1", parent_id="parent-1", depth=1)

    rows = {e["subagent_id"]: e for e in sup.snapshot()["active"]}
    assert rows["child-1"]["parent_id"] == "parent-1"
    assert rows["parent-1"]["parent_id"] is None
    assert rows["child-1"]["parent_id"] in rows


# ── the foreground path, driven end to end ───────────────────────────────
#
# These run a real _run_sync_agent by faking only the model round trip, because
# the headline claim — that a *synchronous* delegation is now visible and
# interruptible — lives entirely on that path and is invisible to the
# supervisor's own unit tests.


@pytest.fixture
def fake_run(monkeypatch):
    """Replace the agent's message collection with a caller-supplied hook."""
    import src.tool_system.tools.agent as agentmod
    from src.types.messages import AssistantMessage

    def install(hook=None):
        async def collect(params):
            if hook is not None:
                hook(params)
            return [AssistantMessage(content=[{"type": "text", "text": "done"}])]

        monkeypatch.setattr(agentmod, "_collect_agent_messages", collect)

    return install


def test_a_foreground_agent_is_visible_while_it_runs(agent_tool, ctx, fake_run):
    # Before this, a sync delegation registered nowhere: the overlay could not
    # see it and nothing in the process could stop it.
    seen: dict[str, Any] = {}

    def during(params):
        snap = ctx.agent_supervisor.snapshot()
        seen["active"] = snap["active"]
        seen["abort"] = params.abort_controller

    fake_run(during)
    _call(agent_tool, ctx, description="audit the store")

    (entry,) = seen["active"]
    assert entry["goal"] == "audit the store"
    assert entry["status"] == "running"
    assert entry["depth"] == 0
    # A reachable abort handle is what makes `subagent.interrupt` work here.
    assert seen["abort"] is not None


def test_a_foreground_agent_is_interruptible_while_it_runs(agent_tool, ctx, fake_run):
    aborted: list[str] = []

    def during(params):
        agent_id = ctx.agent_supervisor.snapshot()["active"][0]["subagent_id"]
        params.abort_controller.abort = lambda reason="": aborted.append(reason)
        assert ctx.agent_supervisor.interrupt(agent_id) is True

    fake_run(during)
    _call(agent_tool, ctx)

    assert aborted == ["interrupted by user"]


def test_the_slot_is_freed_when_a_foreground_agent_finishes(agent_tool, ctx, fake_run):
    fake_run()
    result = _call(agent_tool, ctx)

    assert result.output["status"] == "completed"
    assert ctx.agent_supervisor.live_count() == 0


def test_the_slot_is_freed_when_a_foreground_agent_raises(agent_tool, ctx, fake_run):
    def explode(params):
        raise RuntimeError("model exploded")

    fake_run(explode)

    with pytest.raises(RuntimeError):
        _call(agent_tool, ctx)

    # A stranded slot would shrink the session's capacity for good.
    assert ctx.agent_supervisor.live_count() == 0


def test_finishing_one_agent_lets_the_next_in(agent_tool, ctx, fake_run):
    ctx.agent_supervisor = AgentSupervisor(max_concurrent=1)
    fake_run()

    assert _call(agent_tool, ctx).output["status"] == "completed"
    assert _call(agent_tool, ctx).output["status"] == "completed"


# ── the background path ──────────────────────────────────────────────────


async def _drain(predicate, tries: int = 200) -> bool:
    """Yield to the loop until ``predicate()`` holds, or give up."""
    for _ in range(tries):
        if predicate():
            return True
        await asyncio.sleep(0)
    return predicate()


@pytest.mark.asyncio
async def test_a_background_agent_frees_its_slot_when_the_worker_exits(
    agent_tool, ctx, monkeypatch,
):
    # The release lives in _background_lifecycle's finally, which only runs
    # once the detached coroutine has actually finished — so a leak here would
    # be invisible to every synchronous test.
    import src.tool_system.tools.agent as agentmod
    from src.types.messages import AssistantMessage

    async def fake_run_agent(params):
        yield AssistantMessage(content=[{"type": "text", "text": "done"}])

    monkeypatch.setattr(agentmod, "run_agent", fake_run_agent)

    result = _call(agent_tool, ctx, run_in_background=True)
    assert result.output["status"] == "async_launched"

    assert await _drain(lambda: ctx.agent_supervisor.live_count() == 0), (
        "background worker exited without releasing its slot"
    )


@pytest.mark.asyncio
async def test_a_background_agent_frees_its_slot_when_the_worker_fails(
    agent_tool, ctx, monkeypatch,
):
    import src.tool_system.tools.agent as agentmod

    async def exploding_run_agent(params):
        raise RuntimeError("model exploded")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(agentmod, "run_agent", exploding_run_agent)

    _call(agent_tool, ctx, run_in_background=True)

    assert await _drain(lambda: ctx.agent_supervisor.live_count() == 0), (
        "a failed background worker stranded its slot"
    )


@pytest.mark.asyncio
async def test_a_background_agent_is_visible_and_interruptible_while_it_runs(
    agent_tool, ctx, monkeypatch,
):
    import src.tool_system.tools.agent as agentmod
    from src.types.messages import AssistantMessage

    release = asyncio.Event()

    async def slow_run_agent(params):
        await release.wait()
        yield AssistantMessage(content=[{"type": "text", "text": "done"}])

    monkeypatch.setattr(agentmod, "run_agent", slow_run_agent)

    _call(agent_tool, ctx, run_in_background=True, description="long job")
    assert await _drain(lambda: ctx.agent_supervisor.live_count() == 1)

    (entry,) = ctx.agent_supervisor.snapshot()["active"]
    assert entry["goal"] == "long job"
    assert ctx.agent_supervisor.interrupt(entry["subagent_id"]) is True
    # Still held: the worker has not exited yet.
    assert ctx.agent_supervisor.live_count() == 1

    release.set()
    assert await _drain(lambda: ctx.agent_supervisor.live_count() == 0)


def test_child_contexts_share_the_parents_supervisor(tmp_path: Path):
    # Without sharing, a nested spawn would admit against its own empty
    # registry and bypass the cap entirely.
    from src.agent.subagent_context import create_subagent_context

    parent = ToolContext(workspace_root=tmp_path, cwd=tmp_path)
    child = create_subagent_context(parent)

    assert child.agent_supervisor is parent.agent_supervisor


def test_a_fresh_context_gets_its_own_supervisor(tmp_path: Path):
    a = ToolContext(workspace_root=tmp_path)
    b = ToolContext(workspace_root=tmp_path)
    assert a.agent_supervisor is not b.agent_supervisor
