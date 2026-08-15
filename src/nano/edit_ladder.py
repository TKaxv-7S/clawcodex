"""pi's edit pipeline, ported: multi-edit + exact→fuzzy match ladder.

Faithful Python port of pi's ``edit-diff.ts``
(reference_projects/pi/packages/coding-agent/src/core/tools/edit-diff.ts),
with clawcodex field names (``old_string``/``new_string``). Pure string
functions — no filesystem, no tool_system imports — so the algorithm is
testable in isolation and reusable.

The ladder, per edit:

1. exact ``str.find`` on the LF-normalized file;
2. on miss, fuzzy match in normalized space — NFKC, per-line trailing
   whitespace stripped, smart quotes → ASCII, Unicode dashes → ``-``,
   exotic spaces → `` `` — which absorbs the common LLM mismatch causes
   (invisible Unicode, trailing whitespace) with no semantic risk;
3. every edit is matched against the SAME original content (not applied
   incrementally), uniqueness is enforced with occurrence counts, overlaps
   are rejected with a merge hint, and replacements apply in reverse offset
   order;
4. when any edit needed fuzzy matching, changed lines are rewritten from
   normalized space while unchanged lines keep their original bytes
   (``apply_replacements_preserving_unchanged_lines``).

Errors are :class:`EditLadderError` with pi's actionable wording — each
message tells the model what to do next, because a failed edit costs a
full model round-trip.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

__all__ = [
    "Edit",
    "EditLadderError",
    "apply_edits_to_normalized_content",
    "detect_line_ending",
    "fuzzy_find_text",
    "normalize_for_fuzzy_match",
    "normalize_to_lf",
    "restore_line_endings",
    "strip_bom",
]


class EditLadderError(ValueError):
    """A ladder failure with a model-actionable message."""


@dataclass(frozen=True)
class Edit:
    old_string: str
    new_string: str


# --------------------------------------------------------------------------
# Line endings / BOM
# --------------------------------------------------------------------------

def detect_line_ending(content: str) -> str:
    """``"\\r\\n"`` when the first newline in the file is CRLF, else ``"\\n"``."""
    crlf = content.find("\r\n")
    lf = content.find("\n")
    if lf == -1:
        return "\n"
    if crlf == -1:
        return "\n"
    return "\r\n" if crlf < lf else "\n"


def normalize_to_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def restore_line_endings(text: str, ending: str) -> str:
    return text.replace("\n", "\r\n") if ending == "\r\n" else text


def strip_bom(content: str) -> tuple[str, str]:
    """Return ``(bom, text)`` — the model never includes an invisible BOM
    in old_string, so matching must run against the BOM-less text."""
    if content.startswith("\ufeff"):
        return "\ufeff", content[1:]
    return "", content


# --------------------------------------------------------------------------
# Fuzzy normalization
# --------------------------------------------------------------------------

_SMART_SINGLE = re.compile("[\u2018\u2019\u201A\u201B]")
_SMART_DOUBLE = re.compile("[\u201C\u201D\u201E\u201F]")
# U+2010 hyphen … U+2015 horizontal bar, U+2212 minus
_UNICODE_DASHES = re.compile("[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]")
# NBSP, en/em/etc. spaces, narrow NBSP, medium math space, ideographic space
_UNICODE_SPACES = re.compile("[\u00A0\u2002-\u200A\u202F\u205F\u3000]")


def normalize_for_fuzzy_match(text: str) -> str:
    """NFKC + strip trailing whitespace per line + ASCII-fold quotes,
    dashes, and exotic spaces (pi's ``normalizeForFuzzyMatch``)."""
    text = unicodedata.normalize("NFKC", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _SMART_SINGLE.sub("'", text)
    text = _SMART_DOUBLE.sub('"', text)
    text = _UNICODE_DASHES.sub("-", text)
    text = _UNICODE_SPACES.sub(" ", text)
    return text


@dataclass(frozen=True)
class FuzzyMatch:
    found: bool
    index: int
    match_length: int
    used_fuzzy: bool
    content_for_replacement: str


def fuzzy_find_text(content: str, old_string: str) -> FuzzyMatch:
    """Exact ``find`` first; on miss, search entirely in normalized space."""
    exact = content.find(old_string)
    if exact != -1:
        return FuzzyMatch(True, exact, len(old_string), False, content)

    fuzzy_content = normalize_for_fuzzy_match(content)
    fuzzy_old = normalize_for_fuzzy_match(old_string)
    idx = fuzzy_content.find(fuzzy_old)
    if idx == -1:
        return FuzzyMatch(False, -1, 0, False, content)
    return FuzzyMatch(True, idx, len(fuzzy_old), True, fuzzy_content)


def _count_occurrences(content: str, old_string: str) -> int:
    fuzzy_content = normalize_for_fuzzy_match(content)
    fuzzy_old = normalize_for_fuzzy_match(old_string)
    if not fuzzy_old:
        return 0
    return fuzzy_content.count(fuzzy_old)


# --------------------------------------------------------------------------
# Byte-preserving overlay (fuzzy path)
# --------------------------------------------------------------------------

def _split_lines_with_endings(content: str) -> list[str]:
    return re.findall(r"[^\n]*\n|[^\n]+", content)


@dataclass(frozen=True)
class _Replacement:
    match_index: int
    match_length: int
    new_string: str


def _line_spans(content: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    offset = 0
    for line in _split_lines_with_endings(content):
        spans.append((offset, offset + len(line)))
        offset += len(line)
    return spans


def _replacement_line_range(
    spans: list[tuple[int, int]], repl: _Replacement
) -> tuple[int, int]:
    start_off = repl.match_index
    end_off = repl.match_index + repl.match_length
    start_line = -1
    for i, (s, e) in enumerate(spans):
        if s <= start_off < e:
            start_line = i
            break
    if start_line == -1:
        raise EditLadderError("Replacement range is outside the base content.")
    end_line = start_line
    while end_line < len(spans) and spans[end_line][1] < end_off:
        end_line += 1
    if end_line >= len(spans):
        raise EditLadderError("Replacement range is outside the base content.")
    return start_line, end_line + 1


def _apply_replacements(
    content: str, replacements: list[_Replacement], offset: int = 0
) -> str:
    result = content
    for repl in reversed(replacements):
        idx = repl.match_index - offset
        result = result[:idx] + repl.new_string + result[idx + repl.match_length:]
    return result


def apply_replacements_preserving_unchanged_lines(
    original_content: str,
    base_content: str,
    replacements: list[_Replacement],
) -> str:
    """Rewrite only the lines a replacement touches from normalized space;
    copy every other line back from the original bytes (pi's
    ``applyReplacementsPreservingUnchangedLines``)."""
    original_lines = _split_lines_with_endings(original_content)
    base_spans = _line_spans(base_content)
    if len(original_lines) != len(base_spans):
        raise EditLadderError(
            "Cannot preserve unchanged lines because the base content has a "
            "different line count."
        )

    groups: list[dict] = []
    for repl in sorted(replacements, key=lambda r: r.match_index):
        start_line, end_line = _replacement_line_range(base_spans, repl)
        if groups and start_line < groups[-1]["end"]:
            groups[-1]["end"] = max(groups[-1]["end"], end_line)
            groups[-1]["replacements"].append(repl)
        else:
            groups.append(
                {"start": start_line, "end": end_line, "replacements": [repl]}
            )

    out: list[str] = []
    line_idx = 0
    for group in groups:
        out.extend(original_lines[line_idx:group["start"]])
        g_start = base_spans[group["start"]][0]
        g_end = base_spans[group["end"] - 1][1]
        out.append(
            _apply_replacements(
                base_content[g_start:g_end], group["replacements"], g_start
            )
        )
        line_idx = group["end"]
    out.extend(original_lines[line_idx:])
    return "".join(out)


# --------------------------------------------------------------------------
# Errors — pi's wording, clawcodex field names
# --------------------------------------------------------------------------

def _not_found_error(path: str, i: int, total: int) -> EditLadderError:
    if total == 1:
        return EditLadderError(
            f"Could not find the exact text in {path}. The old_string must "
            "match exactly including all whitespace and newlines."
        )
    return EditLadderError(
        f"Could not find edits[{i}] in {path}. The old_string must match "
        "exactly including all whitespace and newlines."
    )


def _duplicate_error(path: str, i: int, total: int, count: int) -> EditLadderError:
    if total == 1:
        return EditLadderError(
            f"Found {count} occurrences of the text in {path}. The text must "
            "be unique. Please provide more context to make it unique, or "
            "set replace_all=true to change every occurrence."
        )
    return EditLadderError(
        f"Found {count} occurrences of edits[{i}] in {path}. Each old_string "
        "must be unique. Please provide more context to make it unique."
    )


def _empty_old_error(path: str, i: int, total: int) -> EditLadderError:
    if total == 1:
        return EditLadderError(f"old_string must not be empty in {path}.")
    return EditLadderError(f"edits[{i}].old_string must not be empty in {path}.")


def _no_change_error(path: str, total: int) -> EditLadderError:
    if total == 1:
        return EditLadderError(
            f"No changes made to {path}. The replacement produced identical "
            "content. This might indicate an issue with special characters "
            "or the text not existing as expected."
        )
    return EditLadderError(
        f"No changes made to {path}. The replacements produced identical "
        "content."
    )


# --------------------------------------------------------------------------
# The ladder
# --------------------------------------------------------------------------

def apply_edits_to_normalized_content(
    normalized_content: str,
    edits: list[Edit],
    path: str,
    *,
    replace_all: bool = False,
) -> tuple[str, str]:
    """Apply one or more replacements to LF-normalized content.

    Returns ``(base_content, new_content)``. ``replace_all`` is honored only
    for a single edit (clawcodex's escape hatch; pi has none) and uses exact
    matching — a fuzzy replace-all would be too eager.
    """
    normalized_edits = [
        Edit(normalize_to_lf(e.old_string), normalize_to_lf(e.new_string))
        for e in edits
    ]

    for i, e in enumerate(normalized_edits):
        if not e.old_string:
            raise _empty_old_error(path, i, len(normalized_edits))

    if replace_all and len(normalized_edits) == 1:
        e = normalized_edits[0]
        if e.old_string not in normalized_content:
            raise _not_found_error(path, 0, 1)
        new_content = normalized_content.replace(e.old_string, e.new_string)
        if new_content == normalized_content:
            raise _no_change_error(path, 1)
        return normalized_content, new_content

    initial = [
        fuzzy_find_text(normalized_content, e.old_string)
        for e in normalized_edits
    ]
    used_fuzzy = any(m.used_fuzzy for m in initial)
    base = (
        normalize_for_fuzzy_match(normalized_content)
        if used_fuzzy else normalized_content
    )

    matched: list[_Replacement] = []
    for i, e in enumerate(normalized_edits):
        m = fuzzy_find_text(base, e.old_string)
        if not m.found:
            raise _not_found_error(path, i, len(normalized_edits))
        count = _count_occurrences(base, e.old_string)
        if count > 1:
            raise _duplicate_error(path, i, len(normalized_edits), count)
        matched.append(_Replacement(m.index, m.match_length, e.new_string))

    ordered = sorted(
        range(len(matched)), key=lambda i: matched[i].match_index
    )
    for prev_i, cur_i in zip(ordered, ordered[1:]):
        prev, cur = matched[prev_i], matched[cur_i]
        if prev.match_index + prev.match_length > cur.match_index:
            raise EditLadderError(
                f"edits[{prev_i}] and edits[{cur_i}] overlap in {path}. "
                "Merge them into one edit or target disjoint regions."
            )

    sorted_matched = [matched[i] for i in ordered]
    if used_fuzzy:
        new_content = apply_replacements_preserving_unchanged_lines(
            normalized_content, base, sorted_matched
        )
    else:
        new_content = _apply_replacements(base, sorted_matched)

    if new_content == normalized_content:
        raise _no_change_error(path, len(normalized_edits))
    return normalized_content, new_content
