"""Integration tests for the agent-server's workflow surfaces.

The dynamic-workflow UX used to live in the deleted Rich REPL / Textual TUI
(#566); these tests pin its agent-server replacements:

  * the ``ultracode`` keyword appends the authoring ``<system-reminder>`` to
    the model-visible user turn (and only to that turn),
  * ``set_effort`` handles ``ultracode`` (session mode on/off, read-only
    report, workflows-disabled gating),
  * the ``workflows`` / ``list_workflow_commands`` / ``workflow_command``
    controls (report text, catalog, directive expansion, gating), and
  * the worker loop drains finished-task ``<task-notification>`` envelopes:
    one banner frame per task + ONE internal summarization turn that skips
    the ultracode reminder.

They reuse the spawn-handle harness from ``test_agent_server_e2e`` (real
``_build_runtime`` with the provider stubbed — no network).
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from src.server.agent_server import AgentServerConfig, make_spawn_agent
from src.utils.message_queue_manager import (
    clear_pending_notifications,
    enqueue_pending_notification,
)
from src.workflow.ultracode import is_ultracode_session, reset_ultracode
from tests.server.test_agent_server_e2e import (
    _RECORDED_TURNS,
    _patches,
    _RecordingProvider,
    _TextProvider,
    _wait_for,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _workflow_state_hygiene(monkeypatch):
    """The ultracode session flag and the notification queue are process-global;
    isolate every test from its neighbors (and from the developer's env)."""
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_WORKFLOWS", raising=False)
    reset_ultracode()
    clear_pending_notifications()
    yield
    reset_ultracode()
    clear_pending_notifications()


@contextlib.asynccontextmanager
async def _spawned(tmp_path, provider_cls, config: AgentServerConfig | None = None):
    """A live agent handle (worker running) + its message generator."""
    from src.tool_system.registry import ToolRegistry

    with contextlib.ExitStack() as stack:
        for p in _patches(provider_cls, ToolRegistry([])):
            stack.enter_context(p)
        spawn = make_spawn_agent(config or AgentServerConfig())
        handle = await spawn("wf_test", str(tmp_path), None)
        gen = handle.messages_from_agent()
        init = await asyncio.wait_for(gen.__anext__(), timeout=10)
        assert init["subtype"] == "init"
        try:
            yield handle, gen
        finally:
            await handle.shutdown()
            with contextlib.suppress(Exception):
                await gen.aclose()


async def _control(handle, gen, rid: str, request: dict) -> dict:
    """Send one control_request and return its reply payload."""
    await handle.send_to_agent({"type": "control_request", "request_id": rid, "request": request})
    for _ in range(20):
        msg = await asyncio.wait_for(gen.__anext__(), timeout=5)
        if msg.get("type") == "control_response" and msg["response"].get("request_id") == rid:
            return msg["response"]["response"]
    raise AssertionError(f"no reply for {rid}")


def _session_of(handle):
    """The underlying ``_AgentSession`` (send_to_agent is a bound method)."""
    return handle.send_to_agent.__self__


def _last_user_message(turn: str) -> str:
    """Last message of a ``_RecordingProvider`` turn record (`` || ``-joined)."""
    return turn.split(" || ")[-1]


# ─── ultracode keyword injection ──────────────────────────────────────────────


async def test_ultracode_keyword_appends_reminder(tmp_path):
    _RECORDED_TURNS.clear()
    async with _spawned(tmp_path, _RecordingProvider) as (handle, gen):
        await handle.send_to_agent(
            {"type": "user", "message": {"role": "user", "content": "ultracode: build a report tool"}}
        )
        assert await _wait_for(lambda: len(_RECORDED_TURNS) == 1)
        turn = _last_user_message(_RECORDED_TURNS[0])
        assert "ultracode: build a report tool" in turn
        assert "WRITE a reusable" in turn, "keyword did not append the authoring reminder"

        await handle.send_to_agent(
            {"type": "user", "message": {"role": "user", "content": "just a plain question"}}
        )
        assert await _wait_for(lambda: len(_RECORDED_TURNS) == 2)
        assert "WRITE a reusable" not in _last_user_message(_RECORDED_TURNS[1])


async def test_ultracode_session_mode_reminds_every_turn(tmp_path):
    _RECORDED_TURNS.clear()
    async with _spawned(tmp_path, _RecordingProvider) as (handle, gen):
        r = await _control(handle, gen, "e1", {"subtype": "set_effort", "effort": "ultracode"})
        assert r == {"ok": True, "effort": "ultracode", "ultracode": True}
        assert is_ultracode_session()

        await handle.send_to_agent(
            {"type": "user", "message": {"role": "user", "content": "refactor the parser"}}
        )
        assert await _wait_for(lambda: len(_RECORDED_TURNS) == 1)
        assert "Ultracode is on for this session" in _last_user_message(_RECORDED_TURNS[0])


# ─── set_effort: ultracode + levels + gating ──────────────────────────────────


async def test_set_effort_levels_and_ultracode_roundtrip(tmp_path):
    async with _spawned(tmp_path, _TextProvider) as (handle, gen):
        sess = _session_of(handle)

        r = await _control(handle, gen, "e1", {"subtype": "set_effort", "effort": "ultracode"})
        assert r["ok"] is True and r["effort"] == "ultracode"
        assert is_ultracode_session()
        assert sess._effort is None, "ultracode must not touch the reasoning level"

        # A real level exits ultracode mode.
        r = await _control(handle, gen, "e2", {"subtype": "set_effort", "effort": "high"})
        assert r == {"ok": True, "effort": "high", "ultracode": False}
        assert not is_ultracode_session()
        assert sess._effort == "high"

        # Bare /effort is a read-only report (no clearing).
        r = await _control(handle, gen, "e3", {"subtype": "set_effort"})
        assert r == {"ok": True, "effort": "high", "ultracode": False}
        assert sess._effort == "high"

        # Explicit auto clears the level (and would exit ultracode mode).
        r = await _control(handle, gen, "e4", {"subtype": "set_effort", "effort": "auto"})
        assert r == {"ok": True, "effort": "default", "ultracode": False}
        assert sess._effort is None

        # Unknown value → error, nothing mutated.
        r = await _control(handle, gen, "e5", {"subtype": "set_effort", "effort": "bogus"})
        assert r["ok"] is False and "invalid effort" in r["error"]


async def test_set_effort_accepts_the_full_claude_ladder(tmp_path):
    """``xhigh``/``max`` are real Claude levels and must be settable here.

    They were rejected by a hardcoded ``(minimal|low|medium|high)`` list,
    so ``/effort xhigh`` failed in the interactive TUI while the same value
    worked via ``--effort``, ``/effort`` on the other surfaces, and
    settings.effort. Both probed on claude-opus-5 2026-07-25. The ladder is
    now exactly VALID_EFFORT_VALUES — see
    test_set_effort_rejects_minimal_on_the_claude_ladder for the one value
    deliberately NOT carried over.
    """
    async with _spawned(tmp_path, _TextProvider) as (handle, gen):
        sess = _session_of(handle)
        for i, level in enumerate(("xhigh", "max", "low", "medium", "high")):
            r = await _control(
                handle, gen, f"l{i}", {"subtype": "set_effort", "effort": level}
            )
            assert r == {"ok": True, "effort": level, "ultracode": False}, level
            assert sess._effort == level, level

        # The error text must enumerate what is actually accepted, so a
        # rejected value tells the user the real ladder.
        r = await _control(handle, gen, "bad", {"subtype": "set_effort", "effort": "bogus"})
        assert r["ok"] is False
        for level in ("low", "medium", "high", "xhigh", "max"):
            assert level in r["error"], f"{level} missing from {r['error']!r}"


async def test_effort_routing_matches_the_provider_wire_shape(tmp_path):
    """Anthropic takes ``output_config.effort``; OpenAI-compat takes a body field.

    Sending the OpenAI shape to Anthropic is a hard 400 (probed 2026-07-25:
    ``reasoning_effort: Extra inputs are not permitted``), which used to
    break every request after a ``/effort`` in an interactive Anthropic
    session. Pin both directions of the split.
    """
    from unittest.mock import MagicMock

    from src.providers.anthropic_provider import AnthropicProvider
    from src.server.agent_server import _EffortProvider

    async with _spawned(tmp_path, _TextProvider) as (handle, gen):
        sess = _session_of(handle)

        # No effort set → untouched provider, no thinking_effort.
        sess._effort = None
        assert sess._turn_effort_routing() == (sess.provider, None)

        # Anthropic → the real provider plus output_config.effort. The
        # provider must NOT be wrapped: wrapping is what injected the
        # rejected body field.
        sess.provider = AnthropicProvider(api_key="sk-test", model="claude-opus-5")
        sess._effort = "xhigh"
        provider, thinking_effort = sess._turn_effort_routing()
        assert provider is sess.provider
        assert not isinstance(provider, _EffortProvider)
        assert thinking_effort == "xhigh"

        # OpenAI-compatible → wrapped, and effort stays out of the query
        # loop's Anthropic-only parameter.
        sess.provider = MagicMock(name="openai-compat")
        provider, thinking_effort = sess._turn_effort_routing()
        assert isinstance(provider, _EffortProvider)
        assert thinking_effort is None
        injected = provider._inject({})
        assert injected["extra_body"]["reasoning_effort"] == "xhigh"


async def test_effort_reaches_the_query_loop_kwarg(tmp_path, monkeypatch):
    """The seam that actually delivers interactive effort to the wire.

    ``_turn_effort_routing`` returning ``"xhigh"`` is inert unless the turn
    passes it to ``run_query_as_agent_loop`` as ``thinking_effort`` — that
    single kwarg is what ``resolve_thinking_effort`` turns into
    ``output_config.effort``. Deleting it leaves every other effort test
    green, so spy on the call itself.

    Two harness details this test exists to encode: the spy must be a
    coroutine function (the worker invokes the loop via ``asyncio.run(...)``,
    which rejects an async generator with ``ValueError: a coroutine was
    expected``), and it must be patched at its SOURCE module — the worker
    imports it locally inside ``_run_turn`` (agent_server.py:3393), so
    ``src.server.agent_server.run_query_as_agent_loop`` does not exist as a
    module attribute to patch.
    """
    seen: dict = {}

    from src.providers.anthropic_provider import AnthropicProvider
    from src.utils.abort_controller import AbortError

    class _AnthropicShaped(AnthropicProvider):
        """Real Anthropic class (so is_anthropic_wire is True), never called
        over the network — the spy replaces the whole query loop."""

        def __init__(self, *args, **kwargs):
            super().__init__(api_key="sk-test", model="claude-opus-5")

    async def _spy(*args, **kwargs):
        seen.update(kwargs)
        raise AbortError()  # unwind the turn cleanly, no envelope assertions

    async with _spawned(tmp_path, _AnthropicShaped) as (handle, gen):
        sess = _session_of(handle)
        r = await _control(handle, gen, "e1", {"subtype": "set_effort", "effort": "xhigh"})
        assert r["ok"] is True

        monkeypatch.setattr(
            "src.query.agent_loop_compat.run_query_as_agent_loop", _spy
        )
        await handle.send_to_agent(
            {"type": "user", "message": {"role": "user", "content": "hi"}}
        )
        assert await _wait_for(lambda: "thinking_effort" in seen), f"loop not called: {seen!r}"

    assert seen["thinking_effort"] == "xhigh", (
        "interactive /effort must arrive as thinking_effort — without it the "
        "level never becomes output_config.effort"
    )


async def test_launch_effort_flag_seeds_the_session(tmp_path):
    """``--effort`` must apply interactively, not only on headless ``-p``.

    The flag was plumbed solely into HeadlessOptions, so
    ``clawcodex --model claude-opus-5 --effort xhigh`` (no ``-p``) parsed it
    and silently discarded it. It now rides AgentServerConfig into the
    session's ``/effort`` level.
    """
    config = AgentServerConfig(effort="xhigh")
    async with _spawned(tmp_path, _TextProvider, config) as (handle, gen):
        sess = _session_of(handle)
        assert sess._effort == "xhigh"
        # _TextProvider is not Anthropic-shaped, so the level routes down
        # the OpenAI-compat branch — the point here is only that the launch
        # flag SEEDED a level at all. Per-family routing is pinned by
        # test_effort_routing_matches_the_provider_wire_shape.
        provider, thinking_effort = sess._turn_effort_routing()
        assert provider is not sess.provider and thinking_effort is None
        assert provider._inject({})["extra_body"]["reasoning_effort"] == "xhigh"

        # A later /effort still wins over the launch flag, and auto clears.
        r = await _control(handle, gen, "e1", {"subtype": "set_effort", "effort": "low"})
        assert r["effort"] == "low" and sess._effort == "low"
        r = await _control(handle, gen, "e2", {"subtype": "set_effort", "effort": "auto"})
        assert r["effort"] == "default" and sess._effort is None


@pytest.mark.parametrize("seed", ["minimal", "bogus", "", "  ", 5, True])
async def test_launch_effort_flag_ignores_off_ladder_values(tmp_path, seed):
    """An off-ladder ``--effort`` must be dropped, not seeded verbatim.

    Seeding it would resurrect the trap ``_do_set_effort`` rejects: a value
    outside VALID_THINKING_EFFORT_LEVELS makes resolve_thinking_effort fall
    back to settings.effort, so ``--effort minimal`` could put ``max`` on
    the wire while the init frame's badge showed "minimal". Validated at the
    seed rather than only in argparse because --stdio/--print-connect
    callers reach AgentServerConfig without passing a parser — which is also
    why non-str values are covered: a ``.strip()`` on an int would raise
    inside _build_runtime, and that turns into init_error, failing the whole
    session over a cosmetic setting.
    """
    async with _spawned(tmp_path, _TextProvider, AgentServerConfig(effort=seed)) as (
        handle,
        gen,
    ):
        assert _session_of(handle)._effort is None


async def test_launch_effort_flag_is_normalized(tmp_path):
    """Case is normalized at the seed, matching /effort's ``.lower()``.

    ``_EffortProvider`` injects the level verbatim into the request body, so
    an unnormalized "MAX" would go out on the OpenAI-compat wire as-is.
    """
    async with _spawned(tmp_path, _TextProvider, AgentServerConfig(effort=" MAX ")) as (
        handle,
        gen,
    ):
        assert _session_of(handle)._effort == "max"


async def test_set_effort_rejects_minimal_on_the_claude_ladder(tmp_path):
    """``minimal`` is a GPT-5 level, and accepting it here was a trap.

    It is absent from VALID_THINKING_EFFORT_LEVELS, so on the Anthropic path
    ``resolve_thinking_effort`` would treat it as "nothing requested" and
    silently substitute ``settings.effort`` — ``/effort minimal`` could emit
    ``max`` while the TUI echoed "minimal".
    """
    async with _spawned(tmp_path, _TextProvider) as (handle, gen):
        sess = _session_of(handle)
        r = await _control(handle, gen, "m1", {"subtype": "set_effort", "effort": "minimal"})
        assert r["ok"] is False
        assert "minimal" not in r["error"].split("(")[-1], r["error"]
        assert sess._effort is None


async def test_set_effort_ultracode_gated_when_workflows_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", "1")
    async with _spawned(tmp_path, _TextProvider) as (handle, gen):
        r = await _control(handle, gen, "e1", {"subtype": "set_effort", "effort": "ultracode"})
        assert r["ok"] is False and "disabled" in r["error"]
        assert not is_ultracode_session()


# ─── workflows control (the /workflows report) ────────────────────────────────


async def test_workflows_control_reports_runs(tmp_path):
    from types import SimpleNamespace

    async with _spawned(tmp_path, _TextProvider) as (handle, gen):
        r = await _control(handle, gen, "w1", {"subtype": "workflows"})
        assert r["ok"] is True and "No workflow runs" in r["text"]

        # Seed a run into the session's live registry — same object the
        # Workflow tool records into.
        sess = _session_of(handle)
        sess.tool_context.runtime_tasks.upsert(
            SimpleNamespace(
                id="local_workflow_9",
                type="local_workflow",
                status="running",
                workflow_name="deep-research",
                run_id="wf_seed01",
                progress=None,
            )
        )
        r = await _control(handle, gen, "w2", {"subtype": "workflows"})
        assert r["ok"] is True
        assert "deep-research  [running]" in r["text"]
        assert "(run: wf_seed01)" in r["text"]


async def test_workflows_control_gated_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", "1")
    async with _spawned(tmp_path, _TextProvider) as (handle, gen):
        r = await _control(handle, gen, "w1", {"subtype": "workflows"})
        assert r["ok"] is False and "disabled" in r["error"]


# ─── workflow command catalog + dispatch ──────────────────────────────────────

_SAVED_WF = 'meta = {"name": "triage", "description": "Sort issues", "phases": []}\nreturn 1\n'


async def test_list_workflow_commands_includes_bundled_and_saved(tmp_path):
    wf_dir = tmp_path / ".clawcodex" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "triage.py").write_text(_SAVED_WF, encoding="utf-8")

    async with _spawned(tmp_path, _TextProvider) as (handle, gen):
        r = await _control(handle, gen, "l1", {"subtype": "list_workflow_commands"})
        assert r["ok"] is True
        by_name = {c["name"]: c for c in r["commands"]}
        assert "deep-research" in by_name  # bundled
        assert by_name["triage"]["description"] == "Sort issues"
        # The interactive /workflows viewer is NOT a prompt command.
        assert "workflows" not in by_name


async def test_list_workflow_commands_empty_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", "1")
    async with _spawned(tmp_path, _TextProvider) as (handle, gen):
        r = await _control(handle, gen, "l1", {"subtype": "list_workflow_commands"})
        assert r["ok"] is True and r["commands"] == []


async def test_workflow_command_expands_directive(tmp_path):
    wf_dir = tmp_path / ".clawcodex" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "triage.py").write_text(_SAVED_WF, encoding="utf-8")

    async with _spawned(tmp_path, _TextProvider) as (handle, gen):
        r = await _control(
            handle, gen, "d1",
            {"subtype": "workflow_command", "name": "triage", "args": "the open bug list"},
        )
        assert r["ok"] is True
        assert str(wf_dir / "triage.py") in r["prompt"]
        assert "the open bug list" in r["prompt"], "$ARGUMENTS was not substituted"
        assert "$ARGUMENTS" not in r["prompt"]
        assert r["notice"] == "⚡ launching workflow /triage"

        r = await _control(
            handle, gen, "d2", {"subtype": "workflow_command", "name": "nope", "args": ""}
        )
        assert r["ok"] is False and "unknown workflow command" in r["error"]

        r = await _control(handle, gen, "d3", {"subtype": "workflow_command"})
        assert r["ok"] is False


async def test_workflow_command_gated_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", "1")
    async with _spawned(tmp_path, _TextProvider) as (handle, gen):
        r = await _control(
            handle, gen, "d1", {"subtype": "workflow_command", "name": "deep-research", "args": "x"}
        )
        assert r["ok"] is False and "disabled" in r["error"]


# ─── task-notification delivery (worker loop drain) ───────────────────────────

_WF_ENVELOPE = (
    "<task-notification><task-id>local_workflow_7</task-id>"
    "<status>completed</status><summary>Workflow deep-research completed</summary>"
    "<output-file>/tmp/wf_7.jsonl</output-file>"
    "<result>saved to /tmp/report.md</result></task-notification>"
)
_AGENT_ENVELOPE = (
    "<task-notification><task-id>local_agent_3</task-id>"
    "<status>completed</status><summary>Background agent finished: map the auth module</summary>"
    "<result>see notes</result></task-notification>"
)


async def test_notification_drain_emits_banner_and_summary_turn(tmp_path):
    _RECORDED_TURNS.clear()
    frames: list[dict] = []
    async with _spawned(tmp_path, _RecordingProvider) as (handle, gen):
        async def _collect():
            with contextlib.suppress(Exception):
                async for msg in gen:
                    frames.append(msg)

        collector = asyncio.get_running_loop().create_task(_collect())
        try:
            enqueue_pending_notification(value=_WF_ENVELOPE)
            enqueue_pending_notification(value=_AGENT_ENVELOPE)

            # Worker's idle poll (0.5s) drains both → 2 banners + ONE turn.
            assert await _wait_for(
                lambda: len([f for f in frames if f.get("subtype") == "task_notification"]) == 2,
                timeout=10,
            ), "banner frames not emitted"
            banners = [f for f in frames if f.get("subtype") == "task_notification"]
            assert banners[0]["type"] == "system"
            assert banners[0]["task_id"] == "local_workflow_7"
            assert "✔ Workflow deep-research completed" in banners[0]["message"]
            assert "run journal → /tmp/wf_7.jsonl" in banners[0]["message"]
            # The agent envelope banners as its own summary — not as "workflow".
            assert banners[1]["task_id"] == "local_agent_3"
            assert "map the auth module" in banners[1]["message"]

            # Both envelopes are delivered (normally as ONE batched turn; a
            # worker poll landing between the two enqueues may split them —
            # assert delivery across all turns rather than the batch shape).
            assert await _wait_for(
                lambda: "local_workflow_7" in "".join(_RECORDED_TURNS)
                and "local_agent_3" in "".join(_RECORDED_TURNS),
                timeout=10,
            ), "both envelopes must be delivered to the model"
            assert "background tasks you launched have finished" in _RECORDED_TURNS[0]
            # The summarization turn also streams a normal result frame.
            assert await _wait_for(
                lambda: any(f.get("type") == "result" for f in frames), timeout=10
            )
        finally:
            collector.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await collector


async def test_notification_turn_is_internal_no_ultracode_reminder(tmp_path):
    """Session-mode ultracode must not decorate system-generated turns — an
    envelope can never trigger workflow authoring."""
    _RECORDED_TURNS.clear()
    async with _spawned(tmp_path, _RecordingProvider) as (handle, gen):
        r = await _control(handle, gen, "e1", {"subtype": "set_effort", "effort": "ultracode"})
        assert r["ok"] is True

        enqueue_pending_notification(value=_WF_ENVELOPE)
        assert await _wait_for(lambda: len(_RECORDED_TURNS) >= 1, timeout=10)
        turn = _last_user_message(_RECORDED_TURNS[0])
        assert "background tasks you launched have finished" in turn
        assert "Ultracode is on for this session" not in turn
