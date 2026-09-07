"""Agent-server delegation control plane: ``delegation_status``,
``delegation_pause``, and ``subagent_interrupt``.

The TUI has called these three (as ``delegation.status`` / ``delegation.pause``
/ ``subagent.interrupt``) since the agents overlay was written, but no backend
served them, so the overlay's readout was empty and its pause and kill keys did
nothing. These tests pin the replies to the response shapes
``ui-tui/src/gatewayTypes.ts`` declares, since a drifted key name would silently
restore exactly that dead-control behaviour.
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.server.agent_server import AgentServerConfig, _AgentSession
from src.services.swarm.agent_supervisor import AgentSupervisor


class _FakeAbort:
    def __init__(self) -> None:
        self.reasons: list[str] = []

    def abort(self, reason: str = "") -> None:
        self.reasons.append(reason)


def _make_session(supervisor: AgentSupervisor | None = None):
    emitted: list[dict] = []
    sess = _AgentSession(
        session_id="deleg-sess", cwd="/tmp",
        config=AgentServerConfig(single_session=False),
        loop=MagicMock(), out_queue=MagicMock(),
    )
    sess._emit = lambda env: emitted.append(env)  # type: ignore[method-assign]
    sess.tool_context = SimpleNamespace(
        workspace_trusted=True,
        agent_supervisor=supervisor if supervisor is not None else AgentSupervisor(),
    )
    return sess, emitted


def _control(sess: _AgentSession, subtype: str, **params) -> None:
    asyncio.run(sess._handle_control_request({
        "type": "control_request",
        "request_id": "req-1",
        "request": {"subtype": subtype, **params},
    }))


def _last_reply(emitted: list[dict]) -> dict:
    for env in reversed(emitted):
        if env.get("type") == "control_response":
            return env["response"]["response"]
    raise AssertionError(f"no control_response in {emitted!r}")


class TestDelegationStatus(unittest.TestCase):
    def test_reports_live_agents_and_caps(self) -> None:
        sup = AgentSupervisor(max_concurrent=6, max_depth=2)
        sup.admit(
            subagent_id="a1", parent_id="root", depth=1,
            goal="audit the store", model="claude-opus-5",
        )
        sup.set_tool_count("a1", 4)
        sess, emitted = _make_session(sup)

        _control(sess, "delegation_status")
        reply = _last_reply(emitted)

        self.assertEqual(reply["max_concurrent_children"], 6)
        self.assertEqual(reply["max_spawn_depth"], 2)
        self.assertFalse(reply["paused"])
        (entry,) = reply["active"]
        self.assertEqual(entry["subagent_id"], "a1")
        self.assertEqual(entry["parent_id"], "root")
        self.assertEqual(entry["depth"], 1)
        self.assertEqual(entry["goal"], "audit the store")
        self.assertEqual(entry["model"], "claude-opus-5")
        self.assertEqual(entry["status"], "running")
        self.assertEqual(entry["tool_count"], 4)

    def test_empty_session_reports_an_empty_list_not_an_error(self) -> None:
        sess, emitted = _make_session()
        _control(sess, "delegation_status")
        self.assertEqual(_last_reply(emitted)["active"], [])

    def test_without_a_supervisor_the_caps_are_omitted_rather_than_zeroed(self) -> None:
        # A client reading 0/0 would render "at capacity" for a session that
        # simply has no supervisor yet.
        sess, emitted = _make_session()
        sess.tool_context = SimpleNamespace(workspace_trusted=True)

        _control(sess, "delegation_status")
        reply = _last_reply(emitted)

        self.assertEqual(reply["active"], [])
        self.assertNotIn("max_concurrent_children", reply)

    def test_survives_a_session_with_no_tool_context_at_all(self) -> None:
        sess, emitted = _make_session()
        sess.tool_context = None

        _control(sess, "delegation_status")
        self.assertEqual(_last_reply(emitted)["active"], [])


class TestDelegationPause(unittest.TestCase):
    def test_explicit_pause_and_resume_echo_the_resulting_state(self) -> None:
        sup = AgentSupervisor()
        sess, emitted = _make_session(sup)

        _control(sess, "delegation_pause", paused=True)
        self.assertTrue(_last_reply(emitted)["paused"])
        self.assertTrue(sup.is_paused())

        _control(sess, "delegation_pause", paused=False)
        self.assertFalse(_last_reply(emitted)["paused"])
        self.assertFalse(sup.is_paused())

    def test_omitting_paused_flips_the_current_value(self) -> None:
        # The overlay's single pause key sends no explicit value.
        sup = AgentSupervisor()
        sess, emitted = _make_session(sup)

        _control(sess, "delegation_pause")
        self.assertTrue(_last_reply(emitted)["paused"])

        _control(sess, "delegation_pause")
        self.assertFalse(_last_reply(emitted)["paused"])

    def test_pausing_is_visible_to_a_following_status_call(self) -> None:
        sup = AgentSupervisor()
        sess, emitted = _make_session(sup)

        _control(sess, "delegation_pause", paused=True)
        _control(sess, "delegation_status")

        self.assertTrue(_last_reply(emitted)["paused"])


class TestSubagentInterrupt(unittest.TestCase):
    def test_interrupting_a_live_agent_aborts_it_and_reports_found(self) -> None:
        sup = AgentSupervisor()
        abort = _FakeAbort()
        sup.admit(subagent_id="a1", abort_controller=abort)
        sess, emitted = _make_session(sup)

        _control(sess, "subagent_interrupt", subagent_id="a1")
        reply = _last_reply(emitted)

        self.assertTrue(reply["found"])
        self.assertEqual(reply["subagent_id"], "a1")
        self.assertEqual(abort.reasons, ["interrupted by user"])

    def test_unknown_id_reports_not_found_rather_than_claiming_success(self) -> None:
        # The overlay can be pointed at a row whose agent already finished;
        # answering "interrupted" there would be a lie.
        sess, emitted = _make_session()

        _control(sess, "subagent_interrupt", subagent_id="ghost")
        reply = _last_reply(emitted)

        self.assertFalse(reply["found"])
        self.assertEqual(reply["subagent_id"], "ghost")

    def test_missing_id_is_rejected_without_touching_the_supervisor(self) -> None:
        sup = AgentSupervisor()
        abort = _FakeAbort()
        sup.admit(subagent_id="a1", abort_controller=abort)
        sess, emitted = _make_session(sup)

        _control(sess, "subagent_interrupt")

        self.assertFalse(_last_reply(emitted)["found"])
        self.assertEqual(abort.reasons, [], "no id must never fan out to live agents")

    def test_a_background_agent_is_killed_through_the_registry_not_just_aborted(self) -> None:
        """The bug this guards: a bare controller.abort() is not enough.

        query() RETURNS rather than raising when aborted, so
        _background_lifecycle takes its SUCCESS branch and calls
        complete_agent_task — which only no-ops if the registry was ALREADY
        marked terminal. Without going through kill_async_agent the model is
        told a delegation the user killed "completed" with an empty result.
        """
        from src.task_registry import RuntimeTaskRegistry
        from src.tasks.local_agent import register_async_agent

        sup = AgentSupervisor()
        abort = _FakeAbort()
        registry = RuntimeTaskRegistry()
        register_async_agent(
            agent_id="bg1", description="d", prompt="p", agent_type="general-purpose",
            model=None, abort_controller=abort, registry=registry,
        )
        sup.admit(subagent_id="bg1", abort_controller=abort)

        sess, emitted = _make_session(sup)
        sess.tool_context = SimpleNamespace(
            workspace_trusted=True, agent_supervisor=sup, runtime_tasks=registry,
        )

        _control(sess, "subagent_interrupt", subagent_id="bg1")

        self.assertTrue(_last_reply(emitted)["found"])
        # The registry — not just the controller — has to carry the verdict, so
        # complete_agent_task no-ops when the lifecycle reaches its finally.
        self.assertEqual(registry.get("bg1").status, "killed")

    def test_a_foreground_only_agent_is_still_interrupted_without_a_registry_entry(self) -> None:
        # Sync agents never enter runtime_tasks; the supervisor is their only home.
        from src.task_registry import RuntimeTaskRegistry

        sup = AgentSupervisor()
        abort = _FakeAbort()
        sup.admit(subagent_id="fg1", abort_controller=abort)

        sess, emitted = _make_session(sup)
        sess.tool_context = SimpleNamespace(
            workspace_trusted=True, agent_supervisor=sup,
            runtime_tasks=RuntimeTaskRegistry(),
        )

        _control(sess, "subagent_interrupt", subagent_id="fg1")

        self.assertTrue(_last_reply(emitted)["found"])
        self.assertEqual(abort.reasons, ["interrupted by user"])

    def test_a_non_agent_task_id_is_not_reported_as_interrupted(self) -> None:
        # runtime_tasks holds shell, workflow and teammate states too.
        # kill_async_agent's own isinstance guard makes killing one a no-op, so
        # reporting found=True would claim a kill that did not happen.
        from src.task_registry import RuntimeTaskRegistry
        from src.tasks.local_shell import LocalShellTaskState

        registry = RuntimeTaskRegistry()
        registry.upsert(LocalShellTaskState(
            id="sh1", type="local_bash", status="running", description="a shell",
            start_time=0.0, output_file="/tmp/sh1.log",
        ))
        sup = AgentSupervisor()
        sess, emitted = _make_session(sup)
        sess.tool_context = SimpleNamespace(
            workspace_trusted=True, agent_supervisor=sup, runtime_tasks=registry,
        )

        _control(sess, "subagent_interrupt", subagent_id="sh1")

        self.assertFalse(_last_reply(emitted)["found"])
        self.assertEqual(registry.get("sh1").status, "running")

    def test_an_already_finished_agent_is_not_reported_as_interrupted(self) -> None:
        from src.task_registry import RuntimeTaskRegistry
        from src.tasks.local_agent import complete_agent_task, register_async_agent

        registry = RuntimeTaskRegistry()
        register_async_agent(
            agent_id="bg1", description="d", prompt="p", agent_type="general-purpose",
            model=None, abort_controller=_FakeAbort(), registry=registry,
        )
        complete_agent_task("bg1", result_text="done", registry=registry)

        sup = AgentSupervisor()
        sess, emitted = _make_session(sup)
        sess.tool_context = SimpleNamespace(
            workspace_trusted=True, agent_supervisor=sup, runtime_tasks=registry,
        )

        _control(sess, "subagent_interrupt", subagent_id="bg1")

        self.assertFalse(_last_reply(emitted)["found"])

    def test_interrupted_agent_still_holds_its_slot_in_the_next_status(self) -> None:
        # Release happens when the worker actually exits, so the row must still
        # be there — marked interrupted, not vanished.
        sup = AgentSupervisor()
        sup.admit(subagent_id="a1", abort_controller=_FakeAbort())
        sess, emitted = _make_session(sup)

        _control(sess, "subagent_interrupt", subagent_id="a1")
        _control(sess, "delegation_status")
        (entry,) = _last_reply(emitted)["active"]

        self.assertEqual(entry["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
