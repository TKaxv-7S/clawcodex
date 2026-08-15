"""Nano Edit — the stock Edit tool with pi's ladder underneath.

A registry-local variant built with ``dataclasses.replace`` on the stock
``EditTool`` — ``src/tool_system/tools/edit.py`` is not modified, so the
non-nano surface stays byte-identical. What changes under nano:

* ``edits[]`` — one call, many disjoint replacements, all matched against
  the ORIGINAL file (pi's multi-edit contract). The legacy single
  ``old_string``/``new_string`` form still works.
* the exact→fuzzy match ladder with byte-preserving overlay, BOM and
  CRLF hygiene (:mod:`src.nano.edit_ladder`).
* argument repair for observed model quirks (pi ``edit.ts:105-129``):
  ``edits`` sent as a JSON string is parsed; pi-trained models'
  ``oldText``/``newText`` keys are accepted.

What is deliberately kept from clawcodex (advantages pi lacks):

* the read-first-and-unchanged staleness gate;
* ``replace_all`` as a single-edit escape hatch (exact matching only);
* create-if-absent via legacy ``old_string=""`` (delegated to the stock
  implementation, byte-for-byte);
* the similar-file hint, .ipynb rejection, size guard, trailing-whitespace
  hygiene on ``new_string``, and the structured-patch result shape every
  renderer/persistence path already understands.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import replace
from typing import Any

from src.tool_system.build_tool import Tool
from src.tool_system.context import ToolContext
from src.tool_system.diff_utils import (
    convert_leading_tabs_to_spaces,
    record_patch_line_totals,
    unified_diff_hunks,
)
from src.tool_system.errors import ToolInputError
from src.tool_system.protocol import ToolResult
from src.tool_system.tools.edit import (
    _MAX_FILE_SIZE,
    _edit_call,
    _find_similar_file,
    _strip_trailing_whitespace,
    EditTool,
)

from .edit_ladder import (
    Edit,
    EditLadderError,
    apply_edits_to_normalized_content,
    detect_line_ending,
    normalize_to_lf,
    restore_line_endings,
    strip_bom,
)

NANO_EDIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    # Deliberately open: quirk repair happens in the call, and a strict
    # schema reject is a worse error message than the ladder's own.
    "additionalProperties": True,
    "properties": {
        "file_path": {
            "type": "string",
            "description": "The absolute path to the file to modify",
        },
        "edits": {
            # anyOf so the observed some-models-send-a-JSON-string quirk
            # survives schema validation and reaches the call's repair step
            # instead of dying on an InputValidationError round-trip.
            "anyOf": [
                {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": True,
                        "properties": {
                            "old_string": {
                                "type": "string",
                                "description": (
                                    "Exact text to replace. Must be unique "
                                    "in the file; keep it as small as "
                                    "possible while still unique."
                                ),
                            },
                            "new_string": {
                                "type": "string",
                                "description": "Replacement text for this edit.",
                            },
                        },
                    },
                },
                {"type": "string"},
            ],
            "description": (
                "One or more targeted replacements. Each old_string is "
                "matched against the original file, not incrementally — "
                "do not overlap edits; merge nearby changes into one edit."
            ),
        },
        "old_string": {
            "type": "string",
            "description": "Single-edit form: the text to replace",
        },
        "new_string": {
            "type": "string",
            "description": "Single-edit form: the replacement text",
        },
        "replace_all": {
            "type": "boolean",
            "description": (
                "Single-edit form only: replace every occurrence of "
                "old_string (default false)"
            ),
        },
    },
    "required": ["file_path"],
}

NANO_EDIT_DOC = (
    "Edits a file by exact text replacement. You must Read the file first "
    "(and it must be unchanged since). Pass one or more replacements in "
    "edits[] — each {old_string, new_string} is matched against the "
    "original file and must be unique and non-overlapping; merge nearby "
    "changes into one edit. Whitespace/Unicode near-misses are matched "
    "leniently, but prefer exact copies. The single old_string/new_string "
    "form (with optional replace_all) also works."
)


def _prepare_edits(tool_input: dict[str, Any]) -> list[Edit] | None:
    """Normalize the accepted input shapes to a list of edits.

    Returns None for the pure-legacy form that must delegate to the stock
    implementation (create-if-absent via ``old_string == ""``).
    Repairs, in order: ``edits`` as a JSON string (observed from Opus 4.6 /
    GLM-5.1 — pi ``edit.ts:113``), ``oldText``/``newText`` item keys
    (pi-trained models), then merges a legacy top-level pair as one more
    edit (pi ``edit.ts:120-128``).
    """
    raw_edits = tool_input.get("edits")
    if isinstance(raw_edits, str):
        try:
            parsed = json.loads(raw_edits)
            if isinstance(parsed, list):
                raw_edits = parsed
        except (ValueError, TypeError):
            pass

    edits: list[Edit] = []
    if isinstance(raw_edits, list):
        for i, item in enumerate(raw_edits):
            if not isinstance(item, dict):
                raise ToolInputError(
                    f"edits[{i}] must be an object with old_string and "
                    "new_string"
                )
            old = item.get("old_string", item.get("oldText"))
            new = item.get("new_string", item.get("newText"))
            if not isinstance(old, str) or not isinstance(new, str):
                raise ToolInputError(
                    f"edits[{i}] must carry string old_string and new_string"
                )
            edits.append(Edit(old, new))

    legacy_old = tool_input.get("old_string")
    legacy_new = tool_input.get("new_string")
    if isinstance(legacy_old, str) and isinstance(legacy_new, str):
        if legacy_old == "" and not edits:
            # Create-if-absent contract — stock behavior, stock code path.
            return None
        edits.append(Edit(legacy_old, legacy_new))

    if not edits:
        raise ToolInputError(
            "Edit needs either edits[] (list of {old_string, new_string}) "
            "or old_string + new_string"
        )
    return edits


def _nano_edit_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        raise ToolInputError("file_path must be a non-empty string")

    edits = _prepare_edits(tool_input)
    if edits is None:
        # Legacy empty-old_string create: byte-identical stock behavior.
        return _edit_call(tool_input, context)

    replace_all = bool(tool_input.get("replace_all", False))
    if replace_all and len(edits) > 1:
        raise ToolInputError(
            "replace_all only applies to the single old_string/new_string "
            "form — with edits[], make each old_string unique instead"
        )

    for i, e in enumerate(edits):
        if e.old_string == e.new_string:
            raise ToolInputError(
                f"edits[{i}]: old_string and new_string must differ"
                if len(edits) > 1
                else "old_string and new_string must differ"
            )

    path = context.ensure_allowed_path(file_path)
    if path.suffix.lower() == ".ipynb":
        raise ToolInputError(
            "Cannot edit .ipynb files with Edit tool. Use the NotebookEdit "
            "tool instead."
        )
    if not path.exists():
        hint = _find_similar_file(file_path, context.cwd)
        msg = f"file does not exist: {path}"
        if hint:
            msg += f'. Did you mean "{hint}"?'
        raise ToolInputError(msg)
    if not path.is_file():
        raise ToolInputError(f"path is not a file: {path}")
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if size > _MAX_FILE_SIZE:
        raise ToolInputError(
            f"file is too large ({size} bytes, max {_MAX_FILE_SIZE})"
        )
    if not context.was_file_read_and_unchanged(path):
        raise ToolInputError(
            "file must be read first and be unchanged since last read"
        )

    # Bytes, not read_text: Python text mode does universal-newline
    # translation, which would silently erase CRLF before
    # detect_line_ending ever sees it (pi reads a Buffer for the same
    # reason). The stock Edit tool LF-ifies CRLF files this way; nano
    # preserves the file's own endings.
    raw = path.read_bytes().decode("utf-8", errors="replace")
    bom, content = strip_bom(raw)
    ending = detect_line_ending(content)
    normalized = normalize_to_lf(content)

    # Stock hygiene: strip trailing whitespace on replacements outside
    # markdown (edit.py:290).
    is_markdown = path.suffix.lower() in (".md", ".mdx")
    if not is_markdown:
        edits = [
            Edit(e.old_string, _strip_trailing_whitespace(e.new_string))
            if e.new_string else e
            for e in edits
        ]

    try:
        base, updated = apply_edits_to_normalized_content(
            normalized, edits, str(path), replace_all=replace_all
        )
    except EditLadderError as err:
        raise ToolInputError(str(err)) from err

    final = bom + restore_line_endings(updated, ending)
    # write_bytes for the same reason as the bytes read above: write_text
    # with newline=None would re-translate "\n" per-platform.
    path.write_bytes(final.encode("utf-8"))
    context.mark_file_read(path)

    before_lines = convert_leading_tabs_to_spaces(base).splitlines(keepends=True)
    after_lines = convert_leading_tabs_to_spaces(updated).splitlines(keepends=True)
    diff_lines = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=str(path),
            tofile=str(path),
            n=3,
            lineterm="",
        )
    )
    hunks = unified_diff_hunks(diff_lines)
    record_patch_line_totals(hunks)

    return ToolResult(
        name="Edit",
        output={
            "type": "update",
            "filePath": str(path),
            "content": updated,
            "structuredPatch": hunks,
            "editsApplied": len(edits),
        },
    )


def _nano_classifier_input(input_data: dict) -> str:
    data = input_data or {}
    fp = data.get("file_path", "")
    edits = data.get("edits")
    if isinstance(edits, list) and edits:
        first = edits[0] if isinstance(edits[0], dict) else {}
        return (
            f"{fp}: {len(edits)} edit(s), first "
            f"{first.get('old_string', '')!r} -> {first.get('new_string', '')!r}"
        )
    return f"{fp}: {data.get('old_string', '')!r} -> {data.get('new_string', '')!r}"


NanoEditTool: Tool = replace(
    EditTool,
    input_schema=NANO_EDIT_SCHEMA,
    call=_nano_edit_call,
    prompt=lambda: NANO_EDIT_DOC,
    to_auto_classifier_input=_nano_classifier_input,
)
