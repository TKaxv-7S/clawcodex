"""pi-length tool documentation for nano mode.

The default ``tool.prompt()`` docs for the six nano tools total ~8K chars
(~2K tokens) — Bash alone carries ~3.3K chars of styling and commit
etiquette. pi's descriptions are 1–3 sentences and the Databricks result
says that is enough for frontier models. These replacements keep every
**enforced** contract (read-before-write/edit staleness gates, working-dir
persistence, uniqueness rule, output truncation) and drop the etiquette.

Applied registry-locally via ``dataclasses.replace`` in
:func:`src.nano.registry.build_nano_registry` — the default registry's
instances and docs are untouched.
"""

from __future__ import annotations

NANO_TOOL_DOCS: dict[str, str] = {
    "Read": (
        "Reads a file. Returns text with line numbers (cat -n style); "
        "images/PDFs/notebooks are returned in a form you can view. Long "
        "files are truncated — use offset/limit to page through them."
    ),
    "Bash": (
        "Executes a bash command and returns stdout+stderr. The working "
        "directory persists between commands; shell state does not. Long "
        "output is truncated. Quote paths containing spaces."
    ),
    # Edit is intentionally absent: the nano registry swaps in
    # NanoEditTool (src/nano/edit_tool.py), which carries its own doc for
    # the multi-edit + fuzzy-ladder contract.
    "Write": (
        "Writes a file, overwriting any existing content. To overwrite an "
        "existing file you must Read it first. Prefer Edit for modifying "
        "existing files."
    ),
    "Grep": (
        "Searches file contents with a regex (ripgrep). Filter with path/"
        "glob/type; -A/-B/-C add context lines; output_mode selects "
        "matching lines, file lists, or counts. Results are capped."
    ),
    "Glob": (
        "Finds files by glob pattern (e.g. '**/*.py'), sorted by "
        "modification time. Use for locating files by name or path shape."
    ),
}
