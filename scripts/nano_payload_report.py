#!/usr/bin/env python3
"""Fixed-payload report: nano vs default first-request token estimates.

Prints what each mode sends before any conversation exists — system
prompt blocks + tool docs + tool schemas — using the same chars/4
estimate the payload budget test enforces (tests/nano/test_nano_payload.py).

Run from the repo root:  uv run python scripts/nano_payload_report.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _est(chars: int) -> int:
    return chars // 4


def _tools_chars(tools) -> tuple[int, int]:
    docs = schemas = 0
    for t in tools:
        prompt = t.prompt() if callable(t.prompt) else str(t.prompt or "")
        docs += len(prompt)
        try:
            schemas += len(json.dumps(dict(t.input_schema)))
        except Exception:
            schemas += len(str(t.input_schema))
    return docs, schemas


def main() -> int:
    cwd = str(Path.cwd())

    from src.nano.prompt import build_nano_prompt_blocks
    from src.nano.registry import build_nano_registry

    nano_blocks = build_nano_prompt_blocks(
        cwd=cwd, workspace_root=cwd, non_interactive=True, query_source="sdk"
    )
    nano_prompt = sum(len(b["text"]) for b in nano_blocks)
    nano_tools = build_nano_registry().list_tools()
    nano_docs, nano_schemas = _tools_chars(nano_tools)

    from src.context_system.prompt_assembly import (
        build_full_system_prompt_blocks,
    )
    from src.tool_system.defaults import (
        ESSENTIAL_INITIAL_TOOL_NAMES,
        build_default_registry,
    )

    default_blocks = build_full_system_prompt_blocks(
        cwd=cwd, non_interactive=True, use_cache=False
    )
    default_prompt = sum(len(b.get("text", "")) for b in default_blocks)
    default_tools = [
        t for t in build_default_registry().all_tools()
        if t.name in ESSENTIAL_INITIAL_TOOL_NAMES
    ]
    default_docs, default_schemas = _tools_chars(default_tools)

    rows = [
        ("", "nano", "default"),
        ("tools", str(len(nano_tools)), str(len(default_tools))),
        (
            "system prompt",
            f"~{_est(nano_prompt):,} tok",
            f"~{_est(default_prompt):,} tok",
        ),
        (
            "tool docs",
            f"~{_est(nano_docs):,} tok",
            f"~{_est(default_docs):,} tok",
        ),
        (
            "tool schemas",
            f"~{_est(nano_schemas):,} tok",
            f"~{_est(default_schemas):,} tok",
        ),
        (
            "TOTAL fixed",
            f"~{_est(nano_prompt + nano_docs + nano_schemas):,} tok",
            f"~{_est(default_prompt + default_docs + default_schemas):,} tok",
        ),
    ]
    width = max(len(r[0]) for r in rows) + 2
    for label, nano_v, default_v in rows:
        print(f"{label:<{width}}{nano_v:>14}{default_v:>16}")

    total_nano = nano_prompt + nano_docs + nano_schemas
    total_default = default_prompt + default_docs + default_schemas
    if total_nano:
        print(f"\nratio: {total_default / total_nano:.1f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
