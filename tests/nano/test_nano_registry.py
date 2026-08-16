"""Nano registry invariants — the surface IS the contract."""

from __future__ import annotations

import json

from src.nano.registry import NANO_TOOL_NAMES, build_nano_registry
from src.nano.tool_docs import NANO_TOOL_DOCS
from src.tool_system.defaults import build_default_registry
from src.tool_system.tools import BashTool


def test_exactly_six_tools_in_pi_order():
    reg = build_nano_registry()
    assert [t.name for t in reg.list_tools()] == [
        "Read", "Bash", "Edit", "Write", "Grep", "Glob",
    ]
    assert NANO_TOOL_NAMES == ("Read", "Bash", "Edit", "Write", "Grep", "Glob")


def test_nothing_deferred_so_no_deferred_tools_message():
    # query.py:1024 emits the cache-busting <available-deferred-tools>
    # first-user-message only for deferred-or-disabled registered tools;
    # nano must never have any.
    reg = build_nano_registry()
    assert [t.name for t in reg.list_tools() if t.should_defer] == []
    for t in reg.list_tools():
        assert t.is_enabled()


def test_no_orchestration_or_task_surface():
    reg = build_nano_registry()
    names = {t.name for t in reg.list_tools()}
    for absent in (
        "Agent", "Workflow", "TaskCreate", "TaskUpdate", "TaskList",
        "TaskGet", "TaskOutput", "TaskStop", "ToolSearch", "Skill",
        "AskUserQuestion", "EnterPlanMode", "ExitPlanMode", "SendMessage",
        "CronCreate", "WebFetch", "WebSearch", "TodoWrite", "Memory",
    ):
        assert absent not in names


def test_pi_length_docs_but_full_schemas():
    from src.nano.edit_tool import NANO_EDIT_DOC

    reg = build_nano_registry()
    for t in reg.list_tools():
        if t.name == "Edit":
            # The nano Edit variant carries its own multi-edit doc.
            assert t.prompt() == NANO_EDIT_DOC
        else:
            assert t.prompt() == NANO_TOOL_DOCS[t.name]
        # docs are pi-length: a few sentences, not the multi-KB defaults
        assert len(t.prompt()) < 500
        # schemas are untouched (parameter shape is behavioral)
        assert json.dumps(dict(t.input_schema))


def test_vision_analyze_joins_only_when_configured(vision_configured):
    reg = build_nano_registry()
    names = [t.name for t in reg.list_tools()]
    assert names == [
        "Read", "Bash", "Edit", "Write", "Grep", "Glob", "vision_analyze",
    ]
    vision = next(t for t in reg.list_tools() if t.name == "vision_analyze")
    # Registered live, never deferred/disabled — a registered-but-disabled
    # tool would resurrect the <available-deferred-tools> message-0 block.
    assert not vision.should_defer


def test_vision_snippet_renders_in_prompt(vision_configured, tmp_path):
    from src.nano.prompt import build_nano_prompt_text

    names = tuple(t.name for t in build_nano_registry().list_tools())
    text = build_nano_prompt_text(cwd=str(tmp_path), tool_names=names)
    assert "- vision_analyze: " in text


def test_websearch_joins_only_when_opted_in(monkeypatch):
    import src.nano.registry as registry_mod

    monkeypatch.setattr(
        registry_mod, "_nano_websearch_configured", lambda: True
    )
    names = [t.name for t in build_nano_registry().list_tools()]
    assert names[-1] == "WebSearch"
    ws = next(t for t in build_nano_registry().list_tools() if t.name == "WebSearch")
    assert len(ws.prompt()) < 400  # pi-length doc override applied


def test_websearch_absent_without_explicit_config():
    # WebSearch.is_enabled defaults True (key errors surface at call
    # time), so registration must hinge purely on the explicit
    # nano.websearch config — a resolvable TAVILY key alone (the default
    # forwarded-keys benchmark environment) must not grow the surface.
    names = {t.name for t in build_nano_registry().list_tools()}
    assert "WebSearch" not in names


def test_default_registry_instances_untouched():
    # The nano registry carries copies; the shared static instances (and
    # therefore the default registry) must keep their full docs.
    build_nano_registry()
    assert len(BashTool.prompt()) > 1000
    default = build_default_registry()
    bash = next(t for t in default.all_tools() if t.name == "Bash")
    assert len(bash.prompt()) > 1000
