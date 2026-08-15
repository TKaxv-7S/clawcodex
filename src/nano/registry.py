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
    """Registry holding exactly the six nano tools, none deferred.

    Each tool is a registry-local copy carrying its pi-length doc
    (``NANO_TOOL_DOCS``) as ``prompt`` — ``query()`` sends ``tool.prompt()``
    as the API description (query.py:1049), so this alone cuts the tool
    payload from ~2K to ~250 tokens without touching the default registry's
    instances. Schemas are kept in full: parameter shapes are behavioral,
    docs are advisory.
    """
    registry = ToolRegistry()
    for tool in NANO_TOOLS:
        doc = NANO_TOOL_DOCS.get(tool.name)
        if doc is not None:
            tool = replace(tool, prompt=lambda _doc=doc: _doc)
        registry.register(tool)
    return registry
