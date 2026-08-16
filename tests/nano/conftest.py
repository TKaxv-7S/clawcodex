"""Shared fixtures for nano-mode tests.

Nano and eco are process-global toggles; every test must leave them off so
the default-path test suite keeps seeing stock behavior.
"""

from __future__ import annotations

import pytest

from src.eco.state import reset_eco
from src.nano.state import reset_nano_mode


@pytest.fixture(autouse=True)
def _reset_nano_state():
    reset_nano_mode()
    reset_eco()
    yield
    reset_nano_mode()
    reset_eco()


@pytest.fixture(autouse=True)
def _vision_unconfigured(monkeypatch):
    """Hermetic default: the dev machine's real vision config must not flip
    the conditional seventh tool on. Vision-specific tests override."""
    import src.providers.vision_config as vision_config

    monkeypatch.setattr(vision_config, "vision_is_configured", lambda: False)


@pytest.fixture
def vision_configured(monkeypatch):
    import src.providers.vision_config as vision_config

    monkeypatch.setattr(vision_config, "vision_is_configured", lambda: True)


@pytest.fixture
def no_skills(monkeypatch):
    """Hermetic prompt tests: no developer-machine skills leak in."""
    import src.command_system as command_system

    monkeypatch.setattr(
        command_system, "get_skill_tool_commands", lambda cwd=None: ()
    )


@pytest.fixture
def no_project_context(monkeypatch):
    """Hermetic prompt tests: no ~/.clawcodex/CLAWCODEX.md leak."""
    import src.context_system as context_system

    monkeypatch.setattr(
        context_system, "build_context_prompt_parts",
        lambda root, cwd=None: ("", ""),
    )
