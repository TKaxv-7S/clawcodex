"""Reading files and directories *inside a session's workspace*, for the web client.

`desktop_fs.py` browses the whole filesystem so the workspace picker can find a
project. This module is the opposite: once a session has a workspace, it is the
only tree the right-hand sidebar reads, one page and one directory level at a
time.

Two rules shape the whole module.

**Everything is confined to the workspace root.** A path is resolved (symlinks
and all) and must land inside the resolved root; anything else is refused. That
is not a security boundary — the agent running in this very process can read the
disk, and the socket is loopback and token-gated — it is honesty: the sidebar
says it is showing the workspace, so it must not be reachable through `..` or a
symlink pointing out of the tree.

**Failure is part of the answer, not an exception.** The gateway's error
envelope carries a message and nothing else (`desktop_gateway.py::_dispatch`),
and a reader needs the *code* to say why in terms of the file — "that file is
gone" reads differently from "that page is too large". So every function returns
either ``{"ok": True, ...}`` or ``{"ok": False, "error": {"code", "message"}}``,
with codes borrowed from the reference client's ``workspace-file/*`` vocabulary
so the two front ends can say the same sentences.

Reads are paged by line, never whole: a file has no bound, and a page does.
"""

from __future__ import annotations

import os
import stat as stat_module
from pathlib import Path
from typing import Any

# One page of lines. The reader asks for the next page when it reaches the end
# of the loaded text, so this bounds a single response, not the file.
MAX_LINES = 2000
# ...and one page's bytes, independently: 2000 lines of minified JavaScript is
# not a page anybody wants delivered over a socket that also carries the turn.
MAX_BYTES = 2 * 1024 * 1024
# Children returned for one directory level before the tail is cut.
MAX_ENTRIES = 500


def _failure(code: str, message: str, **details: Any) -> dict[str, Any]:
    error: dict[str, Any] = {"code": f"workspace-file/{code}", "message": message}
    if details:
        error["details"] = details
    return {"ok": False, "error": error}


def _inside(root: Path, path: Path) -> bool:
    """Whether ``path`` resolves inside ``root``. Both sides fully resolved, so
    a symlink pointing out of the tree is outside it however it was reached."""
    try:
        resolved = path.resolve()
    except OSError:  # pragma: no cover - platform-specific
        return False
    return resolved == root or root in resolved.parents


def _resolve(root: str, path: str | None) -> tuple[Path, Path] | dict[str, Any]:
    """The (resolved root, resolved target) pair, or the failure that stops it.

    ``path`` may be absolute or relative to the root; an empty one means the
    root itself. Both sides are fully resolved before they are compared, so a
    symlink that points out of the workspace is refused even though the link
    itself sits inside it.
    """
    if not root:
        return _failure("unknown-workspace", "This session has no workspace directory.")

    try:
        resolved_root = Path(root).expanduser().resolve()
    except OSError as exc:  # pragma: no cover - platform-specific
        return _failure("unavailable", f"cannot resolve {root}: {exc}")

    # Stripped only to decide "no path given": a POSIX filename may legally
    # begin or end with a space, and trimming it would silently read a
    # different file.
    raw = path or ""
    candidate = Path(raw).expanduser() if raw.strip() else resolved_root
    if not candidate.is_absolute():
        candidate = resolved_root / candidate

    try:
        # strict=False: a missing file must reach the reader as `not-found`
        # rather than as an unresolvable path, and its *parents* still resolve,
        # which is what the containment check needs.
        resolved = candidate.resolve()
    except OSError as exc:  # pragma: no cover - platform-specific
        return _failure("unavailable", f"cannot resolve {candidate}: {exc}")

    if not (resolved == resolved_root or resolved_root in resolved.parents):
        return _failure(
            "outside-workspace",
            "That path is outside the workspace, so the sidebar will not read it.",
        )

    return resolved_root, resolved


def _version(stat: os.stat_result) -> str:
    """A cheap identity for "the file as it was when we read it".

    Modification time and size together: enough to notice a rewrite between two
    pages, which is all the reader does with it. Not a hash — hashing a file to
    show a page of it would cost more than the page.
    """
    return f"{stat.st_mtime_ns}-{stat.st_size}"


def read_file(
    root: str,
    path: str,
    *,
    offset: int = 1,
    limit: int | None = None,
) -> dict[str, Any]:
    """One page of a text file's lines, starting at the 1-based ``offset``.

    The page is the lines ``[offset, offset+limit)``, joined with ``\\n`` and
    carrying its own ``lines`` count — so "one empty line" and "past the end of
    the file" are different answers (``lines`` 1 against 0) rather than the same
    empty string. ``eof`` says whether the file ends inside this page.

    ``limit`` only ever narrows: a caller asking for more than ``MAX_LINES``
    gets ``MAX_LINES``.
    """
    resolved = _resolve(root, path)
    if isinstance(resolved, dict):
        return resolved
    _, target = resolved

    try:
        stat = target.stat()
    except FileNotFoundError:
        return _failure("not-found", "That file is gone. It may have been moved or deleted.")
    except OSError as exc:
        return _failure("unavailable", f"cannot read {target}: {exc}")

    # Checked before opening: a FIFO would block `open` itself, and this socket
    # also carries the turn's stream.
    if not stat_module.S_ISREG(stat.st_mode):
        return _failure(
            "not-regular-file", "That is not a regular file, so it has no text to show."
        )

    start = max(1, int(offset or 1))
    count = MAX_LINES if limit is None else max(1, min(int(limit), MAX_LINES))

    try:
        # Read as bytes and decode the page, not the file: a binary file has to
        # fail as `not-text` rather than raise out of the line iterator halfway
        # through, and the byte cap has to be measured on bytes.
        with target.open("rb") as handle:
            # Re-stat the open handle: the version has to describe the bytes
            # this page came from. A write between the stat above and this open
            # would otherwise label new content with the old version, and the
            # client would merge two files into one.
            stat = os.fstat(handle.fileno())
            for _ in range(start - 1):
                if handle.readline() == b"":
                    break
            collected: list[bytes] = []
            size = 0
            eof = True
            for _ in range(count):
                line = handle.readline()
                if line == b"":
                    break
                size += len(line)
                if size > MAX_BYTES:
                    return _failure(
                        "too-large",
                        "That page is too large; the sidebar does not read pages above the limit.",
                        limit=MAX_BYTES,
                    )
                collected.append(line)
            else:
                eof = handle.readline() == b""
    except PermissionError:
        return _failure("unavailable", f"permission denied: {target}")
    except OSError as exc:
        return _failure("unavailable", f"cannot read {target}: {exc}")

    try:
        decoded = b"".join(collected).decode("utf-8")
    except UnicodeDecodeError:
        return _failure("not-text", "That is not a text file, so it cannot be shown here.")

    # CRLF is a line ending, not a character in the line: the client splits on
    # "\n" and would otherwise render a stray carriage return at every line end
    # of a file written on Windows.
    text = decoded.replace("\r\n", "\n")
    # The page's terminator is not part of its last line either: the client
    # renders one block per line and adds the break itself, and keeping the
    # "\n" here would give every page a phantom empty last line.
    lines = len(collected)
    if text.endswith("\n"):
        text = text[:-1]

    return {
        "ok": True,
        "absolute_path": str(target),
        "version": _version(stat),
        "bytes": stat.st_size,
        "offset": start,
        "text": text,
        "lines": lines,
        "eof": eof,
    }


def list_dir(root: str, path: str | None = None) -> dict[str, Any]:
    """One directory level under the workspace root: its direct children.

    Every entry is reported, dotfiles included — this tree is the workspace as
    it is, not a filtered view; the client decides what to draw. Entries that
    are neither a file nor a directory are typed ``other`` and shown as
    unopenable rather than hidden, so a directory is described whole.
    """
    resolved = _resolve(root, path)
    if isinstance(resolved, dict):
        return resolved
    resolved_root, target = resolved

    entries: list[dict[str, Any]] = []
    truncated = False
    # No `exists()` / `is_dir()` probe first: both swallow the OSError, so an
    # unreadable directory would be reported as a missing one. `scandir` raises
    # the error that actually happened.
    try:
        with os.scandir(target) as scan:
            for item in scan:
                if len(entries) >= MAX_ENTRIES:
                    truncated = True
                    break
                try:
                    # A link whose target is outside the tree reads as `other`:
                    # the reads below would refuse it, and a row that always
                    # fails when clicked is worse than one that says it cannot
                    # be opened.
                    if item.is_symlink() and not _inside(resolved_root, Path(item.path)):
                        entries.append({"name": item.name, "type": "other"})
                        continue
                    if item.is_dir(follow_symlinks=True):
                        entries.append({"name": item.name, "type": "directory"})
                        continue
                    if item.is_file(follow_symlinks=True):
                        entries.append(
                            {
                                "name": item.name,
                                "type": "file",
                                "size": item.stat(follow_symlinks=True).st_size,
                            }
                        )
                        continue
                except OSError:
                    # A broken link or an unstattable mount: still a row in the
                    # directory, just not one that can be opened.
                    pass
                entries.append({"name": item.name, "type": "other"})
    except FileNotFoundError:
        return _failure("not-found", "That directory is gone. It may have been moved or deleted.")
    except NotADirectoryError:
        return _failure("not-directory", "That is not a directory.")
    except PermissionError:
        return _failure("unavailable", f"permission denied: {target}")
    except OSError as exc:
        return _failure("unavailable", f"cannot read {target}: {exc}")

    return {
        "ok": True,
        "absolute_path": str(target),
        "entries": entries,
        "truncated": truncated,
    }


__all__ = ["MAX_BYTES", "MAX_ENTRIES", "MAX_LINES", "list_dir", "read_file"]
