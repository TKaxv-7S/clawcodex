"""The number nano exists for: fixed per-request payload ≤ ~2K tokens.

The pi-vs-clawcodex study measured pi at ~1,050 tokens and default
clawcodex at ~17,000. Nano's budget is 2,500 estimated tokens (chars/4,
conservative) for prompt blocks + tool docs + tool schemas — comfortably
an order of magnitude under the default, with headroom so an innocuous
doc tweak doesn't flap the suite. If this test fails, something resident
crept back into the first request; shrink it rather than raising the bound.
"""

from __future__ import annotations

import json

from src.nano.prompt import build_nano_prompt_blocks
from src.nano.registry import build_nano_registry

PAYLOAD_BUDGET_EST_TOKENS = 2_500


def _est_tokens(chars: int) -> int:
    return chars // 4


def test_fixed_payload_within_budget(no_skills, no_project_context, tmp_path):
    blocks = build_nano_prompt_blocks(
        cwd=str(tmp_path), workspace_root=str(tmp_path),
        non_interactive=True, query_source="sdk",
    )
    prompt_chars = sum(len(b["text"]) for b in blocks)

    tools_chars = 0
    for tool in build_nano_registry().list_tools():
        tools_chars += len(tool.prompt())
        tools_chars += len(json.dumps(dict(tool.input_schema)))

    total = _est_tokens(prompt_chars + tools_chars)
    assert total <= PAYLOAD_BUDGET_EST_TOKENS, (
        f"nano fixed payload grew to ~{total} est. tokens "
        f"(prompt {prompt_chars} ch + tools {tools_chars} ch); "
        f"budget is {PAYLOAD_BUDGET_EST_TOKENS}"
    )
