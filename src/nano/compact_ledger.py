"""pi's file-operation ledger for compaction summaries (nano mode).

pi appends ``<read-files>`` / ``<modified-files>`` path lists to every
compaction summary and seeds them from the previous compaction, so after
any number of compactions the model still knows what it has seen and
touched (compaction/utils.ts ``extractFileOpsFromMessage`` /
``formatFileOperations``; cumulative seeding in compaction.ts:42-70).
Paths are cheap insurance against the post-compaction re-exploration
spiral — a few dozen tokens instead of clawcodex's full-content
post-compact re-injection.

Clawcodex has no compaction-entry metadata store, so cumulativity works
by parsing the previous summary's own ledger block out of the message
stream (the previous summary is a user message that is itself part of
the span being re-summarized).
"""

from __future__ import annotations

import re
from typing import Any

# Tool → which ledger its file_path lands in. NotebookEdit is modify;
# Write and Edit both count as modified (pi: edited ∪ written).
_READ_TOOLS = {"Read"}
_MODIFY_TOOLS = {"Edit", "Write", "NotebookEdit"}

# Keep the ledger bounded: a very long session must not grow the summary
# without limit. Newest-first retention when over the cap.
MAX_PATHS_PER_LIST = 100

_READ_BLOCK_RE = re.compile(r"<read-files>\n(.*?)</read-files>", re.DOTALL)
_MODIFIED_BLOCK_RE = re.compile(
    r"<modified-files>\n(.*?)</modified-files>", re.DOTALL
)


def _block_path(block: Any) -> tuple[str, str] | None:
    """(tool_name, file_path) for a tool_use block, else None."""
    if isinstance(block, dict):
        if block.get("type") != "tool_use":
            return None
        name = str(block.get("name", ""))
        tool_input = block.get("input") or {}
    else:
        if getattr(block, "type", None) != "tool_use":
            return None
        name = str(getattr(block, "name", ""))
        tool_input = getattr(block, "input", None) or {}
    if not isinstance(tool_input, dict):
        return None
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(path, str) or not path:
        return None
    return name, path


def extract_file_ops(messages: list[Any]) -> tuple[list[str], list[str]]:
    """(read_paths, modified_paths) from assistant tool_use blocks,
    in first-seen order. A path both read and modified counts as
    modified only (pi's computeFileLists)."""
    read: dict[str, None] = {}
    modified: dict[str, None] = {}
    for msg in messages:
        if getattr(msg, "role", None) != "assistant":
            continue
        content = getattr(msg, "content", None)
        if not isinstance(content, list):
            continue
        for block in content:
            hit = _block_path(block)
            if hit is None:
                continue
            name, path = hit
            if name in _MODIFY_TOOLS:
                modified.setdefault(path, None)
            elif name in _READ_TOOLS:
                read.setdefault(path, None)
    read_only = [p for p in read if p not in modified]
    return read_only, list(modified)


def _message_text(msg: Any) -> str:
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text", "") or ""))
            else:
                parts.append(str(getattr(block, "text", "") or ""))
        return "\n".join(parts)
    return ""


def parse_previous_ledger(messages: list[Any]) -> tuple[list[str], list[str]]:
    """Paths from the most recent summary's ledger block, if any."""
    for msg in reversed(messages):
        text = _message_text(msg)
        if "<read-files>" not in text and "<modified-files>" not in text:
            continue
        read_m = _READ_BLOCK_RE.search(text)
        mod_m = _MODIFIED_BLOCK_RE.search(text)
        read = (
            [ln.strip() for ln in read_m.group(1).splitlines() if ln.strip()]
            if read_m else []
        )
        modified = (
            [ln.strip() for ln in mod_m.group(1).splitlines() if ln.strip()]
            if mod_m else []
        )
        return read, modified
    return [], []


def _merge_capped(older: list[str], newer: list[str]) -> list[str]:
    """Older-first order, de-duped; newest entries win the cap."""
    merged: dict[str, None] = {}
    for p in older:
        merged.setdefault(p, None)
    for p in newer:
        merged.setdefault(p, None)
    paths = list(merged)
    if len(paths) > MAX_PATHS_PER_LIST:
        paths = paths[-MAX_PATHS_PER_LIST:]
    return paths


def format_file_operations(read: list[str], modified: list[str]) -> str:
    """pi's formatFileOperations: tagged path lists, empty sections omitted."""
    parts: list[str] = []
    if read:
        parts.append("<read-files>\n" + "\n".join(read) + "\n</read-files>")
    if modified:
        parts.append(
            "<modified-files>\n" + "\n".join(modified) + "\n</modified-files>"
        )
    if not parts:
        return ""
    return "\n\n" + "\n\n".join(parts)


def append_file_ops_ledger(
    summary_text: str,
    all_messages: list[Any],
    compacted_messages: list[Any],
) -> str:
    """Summary + cumulative ledger (previous ledger ∪ this span's ops)."""
    prev_read, prev_modified = parse_previous_ledger(all_messages)
    new_read, new_modified = extract_file_ops(compacted_messages)
    modified = _merge_capped(prev_modified, new_modified)
    modified_set = set(modified)
    read = [
        p for p in _merge_capped(prev_read, new_read) if p not in modified_set
    ]
    ledger = format_file_operations(read, modified)
    if not ledger:
        return summary_text
    return summary_text + ledger
