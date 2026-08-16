"""Nano tool registry — pi's surface: six tools, nothing deferred.

pi ships four default tools (read, bash, edit, write) plus grep/find/ls on
request; nano keeps clawcodex's closest six (Grep/Glob replace pi's
grep/find — same roles, existing implementations). Registering the raw
static instances (no :func:`_apply_initial_loading_policy`) keeps every
``should_defer`` False, so ``query()``'s ``<available-deferred-tools>``
first-user-message block — which busts the message-history cache prefix
whenever its contents change — is never emitted (it gates on a non-empty
deferred list, ``src/query/query.py:1024``).

Deliberately absent, per the study (my-docs/clawcodex-nano/pi-vs-clawcodex.md
§5): Agent/Workflow (orchestration), TaskV2 (pi: "todos confuse models"),
Skill (skills load via Read — progressive disclosure), ToolSearch (nothing
to defer at six tools), MCP/user tools (pi has neither in core), and every
interactive-only tool.
"""

from __future__ import annotations

from dataclasses import replace

from src.tool_system.registry import ToolRegistry
from src.tool_system.tools import (
    BashTool,
    GlobTool,
    GrepTool,
    ReadTool,
    WriteTool,
)

from .edit_tool import NanoEditTool
from .tool_docs import NANO_TOOL_DOCS

# Order mirrors pi's system-prompt tool list (read/bash/edit/write first).
# Edit is the nano variant carrying pi's multi-edit + fuzzy ladder
# (src/nano/edit_tool.py); it brings its own doc and schema.
NANO_TOOLS = (ReadTool, BashTool, NanoEditTool, WriteTool, GrepTool, GlobTool)
NANO_TOOL_NAMES: tuple[str, ...] = tuple(t.name for t in NANO_TOOLS)


def build_nano_registry() -> ToolRegistry:
    """Registry holding the six nano tools, none deferred.

    Each tool is a registry-local copy carrying its pi-length doc
    (``NANO_TOOL_DOCS``) as ``prompt`` — ``query()`` sends ``tool.prompt()``
    as the API description (query.py:1049), so this alone cuts the tool
    payload from ~2K to ~250 tokens without touching the default registry's
    instances. Schemas are kept in full: parameter shapes are behavioral,
    docs are advisory.

    Plus one conditional seventh: ``vision_analyze``, registered only when
    a vision model is explicitly configured (the tool's own is_enabled
    gate — global config ``vision.enabled``, seeded by the harbor
    adapter's ``--ak vision=provider:model``). This mirrors pi's own TB
    extension, which adds the same tool for text-only main models; an
    unconfigured nano stays exactly six. Conditional REGISTRATION rather
    than registering-disabled is load-bearing: a registered-but-disabled
    tool would resurrect the ``<available-deferred-tools>`` message-0
    block (query.py's deferred list includes disabled tools) and bust
    nano's byte-stability.
    """
    registry = ToolRegistry()
    for tool in NANO_TOOLS:
        doc = NANO_TOOL_DOCS.get(tool.name)
        if doc is not None:
            tool = replace(tool, prompt=lambda _doc=doc: _doc)
        if tool.name == "Bash":
            tool = replace(tool, input_schema=_nano_bash_schema(tool.input_schema))
        registry.register(tool)
    try:
        from src.tool_system.tools import VisionAnalyzeTool

        if VisionAnalyzeTool.is_enabled():
            registry.register(VisionAnalyzeTool)
    except Exception:  # noqa: BLE001 — a broken vision config never blocks nano
        pass
    try:
        from src.tool_system.tools import WebSearchTool

        if _nano_websearch_configured() and WebSearchTool.is_enabled():
            doc = NANO_TOOL_DOCS.get(WebSearchTool.name)
            registry.register(
                replace(WebSearchTool, prompt=lambda _doc=doc: _doc)
                if doc else WebSearchTool
            )
    except Exception:  # noqa: BLE001 — a broken config never blocks nano
        pass
    return registry


def _nano_bash_schema(schema) -> dict:
    """Bash schema minus the background-execution trap.

    ``run_in_background`` needs the TaskOutput tool to retrieve results —
    absent from the nano surface, so a backgrounded command strands its
    output and forces the poll loops the no-timeout contract exists to
    eliminate (tb21-nano-flash-max-2: 29 background launches across 9
    trials, all long-running-work tasks). With no default timeout, one
    blocking call is strictly better. The stale "(1-600)" cap text on the
    legacy ``timeout_s`` goes too. additionalProperties stays false, so a
    model that still passes run_in_background gets an actionable
    validation error naming the property.
    """
    out = dict(schema)
    props = dict(out.get("properties") or {})
    props.pop("run_in_background", None)
    if "timeout_s" in props:
        ts = dict(props["timeout_s"])
        ts["description"] = "Timeout in seconds"
        props["timeout_s"] = ts
    out["properties"] = props
    return out


def _nano_websearch_configured() -> bool:
    """Explicit opt-in for WebSearch on the nano surface.

    Reads global config ``nano.websearch is True`` (seeded by the harbor
    adapter's ``--ak websearch=1``). Deliberately NOT keyed on the
    TAVILY_API_KEY alone: keys are forwarded into benchmark containers by
    default, and a key that happens to exist must not silently grow nano's
    surface — the same explicitness bar as the vision config block. pi's
    TB extension ships the same tool for the same reason (its runbook
    exports TAVILY_API_KEY as a deliberate act).
    """
    try:
        from src import config as cfg_mod

        block = cfg_mod._get_default_manager().load_global().get("nano")
        return isinstance(block, dict) and block.get("websearch") is True
    except Exception:  # noqa: BLE001
        return False
