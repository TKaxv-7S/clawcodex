"""Nano mode on the interactive TUI path (agent-server backend).

Covers the whole flag chain — CLI kwarg → launcher command → agent-server
argparse → AgentServerConfig → ``_build_runtime`` — plus the mid-session
provider switch, using the same real-``_AgentSession``/keyless-``ollama``
harness as tests/server/test_bypass_permissions_wiring.py.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.bootstrap.state import reset_state_for_tests
from src.eco.state import is_eco_session, reset_eco
from src.nano.state import is_nano_mode, reset_nano_mode
from src.services.startup_gates import reset_session_trust_for_testing

pytestmark = pytest.mark.integration

NANO_NAMES = ["Read", "Bash", "Edit", "Write", "Grep", "Glob"]


# ---------------------------------------------------------------------------
# Flag plumbing: launcher command + server CLI parse
# ---------------------------------------------------------------------------


def _launcher_args(**over):
    base = dict(
        provider=None, model=None, effort=None, permission_mode="default",
        is_bypass_available=False, bypass_selectable=False,
        workspace=None, tui_dir=None, print_connect=False,
        worktree_session=None, nano=False,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_agent_server_cmd_carries_nano_flag():
    from src.entrypoints.tui_launcher import _agent_server_cmd

    assert "--nano" in _agent_server_cmd(_launcher_args(nano=True))
    assert "--nano" not in _agent_server_cmd(_launcher_args())


def test_server_cli_parses_nano_into_config(monkeypatch):
    import src.entrypoints.agent_server_cli as cli_mod

    captured: dict = {}

    async def _fake_serve_stdio(workspace, agent_config):
        captured["config"] = agent_config
        return 0

    monkeypatch.setattr(cli_mod, "_serve_stdio", _fake_serve_stdio)
    assert cli_mod.run_agent_server_subcommand(["--stdio", "--nano"]) == 0
    assert captured["config"].nano is True

    captured.clear()
    assert cli_mod.run_agent_server_subcommand(["--stdio"]) == 0
    assert captured["config"].nano is False


# ---------------------------------------------------------------------------
# _build_runtime behavior (real session harness)
# ---------------------------------------------------------------------------


def _reset_all() -> None:
    reset_state_for_tests()
    reset_session_trust_for_testing()
    reset_nano_mode()
    reset_eco()
    from src.state.app_state import set_active_provider_supplier

    set_active_provider_supplier(None)


def _flatten_prompt(system_prompt) -> str:
    if isinstance(system_prompt, str):
        return system_prompt
    if isinstance(system_prompt, list):
        return "\n".join(
            str(b.get("text", "")) for b in system_prompt if isinstance(b, dict)
        )
    return str(system_prompt or "")


class _SessionHarness(unittest.TestCase):
    """Real ``_AgentSession`` runtime against the keyless ``ollama`` provider
    with global config redirected to a temp dir (the ch03/bypass shape)."""

    def setUp(self) -> None:
        _reset_all()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.ws = root / "ws"
        self.ws.mkdir()
        self.config_dir = root / "config-home"
        self.config_dir.mkdir()
        global_path = self.config_dir / "config.json"
        global_path.write_text(json.dumps({}), encoding="utf-8")
        self._patches = [
            patch("src.config.get_global_config_path", return_value=global_path),
            patch("src.config.GLOBAL_CONFIG_DIR", str(self.config_dir)),
        ]
        for p in self._patches:
            p.start()
        from src.settings.settings import invalidate_settings_cache

        invalidate_settings_cache()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        from src.settings.settings import invalidate_settings_cache

        invalidate_settings_cache()
        _reset_all()
        self._tmp.cleanup()

    def _build(self, **cfg_kwargs):
        from src.server.agent_server import (
            AgentServerConfig,
            _AgentSession,
            _build_runtime,
        )

        sess = _AgentSession(
            session_id="s-nano-tui",
            cwd=str(self.ws),
            config=AgentServerConfig(
                provider_name="ollama",
                single_session=True,
                **cfg_kwargs,
            ),
            loop=MagicMock(),
            out_queue=MagicMock(),
        )
        _build_runtime(sess, None)
        self.assertIsNone(sess.init_error, f"runtime build failed: {sess.init_error}")
        return sess


class TestNanoBuildRuntime(_SessionHarness):
    def test_nano_session_gets_nano_surface(self) -> None:
        sess = self._build(nano=True)

        self.assertTrue(is_nano_mode())
        self.assertTrue(is_eco_session())
        self.assertEqual(
            [t.name for t in sess.tool_registry.list_tools()], NANO_NAMES
        )
        self.assertIsNone(getattr(sess, "_mcp_runtime", None))

        prompt = _flatten_prompt(sess.system_prompt)
        self.assertIn("nano mode", prompt)
        self.assertNotIn("# Doing tasks", prompt)
        self.assertNotIn("# auto memory", prompt)
        # Interactive session: the headless-only note must NOT appear.
        self.assertNotIn("running non-interactively", prompt)

    def test_default_session_unchanged(self) -> None:
        sess = self._build()

        self.assertFalse(is_nano_mode())
        self.assertFalse(is_eco_session())
        names = {t.name for t in sess.tool_registry.list_tools()}
        self.assertIn("Agent", names)
        self.assertGreater(len(names), 15)
        prompt = _flatten_prompt(sess.system_prompt)
        self.assertIn("# Doing tasks", prompt)
        self.assertNotIn("nano mode", prompt)

    def test_provider_switch_keeps_nano_surface(self) -> None:
        sess = self._build(nano=True)

        new_provider = MagicMock()
        new_provider.get_available_models = MagicMock(return_value=[])
        sess._install_provider(new_provider, "ollama", "some-model")

        self.assertEqual(
            [t.name for t in sess.tool_registry.list_tools()], NANO_NAMES
        )

    def test_allowed_tools_filter_still_applies_in_nano(self) -> None:
        sess = self._build(nano=True, disallowed_tools=("Glob",))
        names = [t.name for t in sess.tool_registry.list_tools()]
        self.assertEqual(names, ["Read", "Bash", "Edit", "Write", "Grep"])

    def _emitted_frames(self, sess) -> list[dict]:
        """Frames enqueued through the mocked loop by ``_emit``
        (``loop.call_soon_threadsafe(out_queue.put_nowait, frame)``)."""
        return [
            call.args[1]
            for call in sess.loop.call_soon_threadsafe.call_args_list
            if len(call.args) == 2 and isinstance(call.args[1], dict)
        ]

    def _init_frame(self, sess) -> dict:
        sess.emit_init()
        frames = [
            f for f in self._emitted_frames(sess)
            if f.get("type") == "system" and f.get("subtype") == "init"
        ]
        self.assertTrue(frames, "emit_init produced no system/init frame")
        return frames[-1]

    def test_init_frame_carries_nano_for_the_badge(self) -> None:
        # The Ink client renders the "nano" chip from this field
        # (ui-tui/src/components/appChrome.tsx modelLabel).
        sess = self._build(nano=True)
        init = self._init_frame(sess)
        self.assertIs(init["nano"], True)
        self.assertEqual(
            [t["name"] for t in init["tools"]], NANO_NAMES
        )

    def test_init_frame_nano_false_by_default(self) -> None:
        sess = self._build()
        init = self._init_frame(sess)
        self.assertIs(init["nano"], False)
