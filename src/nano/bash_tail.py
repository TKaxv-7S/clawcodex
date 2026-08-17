"""pi-style rolling-tail output capture for nano bash.

Port of pi's shell-output.ts capture model (packages/agent/src/harness/
utils/shell-output.ts). Two properties, both absent from the stock tool:

* **Tail-keep, not head-keep.** For a long build or training run the
  signal is at the END of the output (the error, the final metrics);
  stock truncation keeps ``s[:limit]`` and drops exactly that.
* **The full output survives.** Once a stream crosses the reply limit it
  spills, complete, to a temp file whose path is handed to the model —
  so the model can grep the whole log afterward instead of re-running.

The lazy spill is lossless the way pi's is: memory is allowed to hold up
to 2x the limit before any trimming, and the spill file is created the
moment the total crosses 1x — everything captured so far is still in
memory at that point, so the file is complete from byte 0 and every later
chunk is appended as it arrives. Memory stays bounded at ~2x the limit
no matter how much the command prints.

Wired only under nano (the bash tool's nano gate); stock bash keeps its
unbounded in-memory capture and head-keep truncation, byte-identical.
Each spool is normally fed by exactly one drain thread, but a detached
descendant holding the pipe open can leave that thread alive past the
reader join — ``finish()`` then races a late ``feed()`` — so the tiny
uncontended lock is load-bearing, not decoration.
"""

from __future__ import annotations

import tempfile
import threading
from collections import deque
from dataclasses import dataclass


@dataclass
class TailCapture:
    """Finished capture of one stream (stdout or stderr)."""

    text: str  # decoded rolling tail — the whole stream when small
    total_bytes: int
    total_lines: int
    spilled: bool
    # Path of the complete-output temp file; None when spilling was
    # requested but failed (disk error) — the tail is then all we have.
    spill_path: str | None


class TailSpool:
    def __init__(self, limit_bytes: int, label: str) -> None:
        self._limit = max(1, limit_bytes)
        self._label = label
        self._chunks: deque[bytes] = deque()
        self._mem_bytes = 0
        self._total_bytes = 0
        self._newlines = 0
        self._ends_with_newline = True
        self._spilled = False
        self._spill_failed = False
        self._file = None
        self._path: str | None = None
        self._lock = threading.Lock()
        self._finished = False

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        with self._lock:
            self._feed_locked(chunk)

    def _feed_locked(self, chunk: bytes) -> None:
        if self._finished:
            # A straggler chunk from an abandoned drain thread after the
            # result was already assembled — nothing left to attach it to.
            return
        self._total_bytes += len(chunk)
        self._newlines += chunk.count(b"\n")
        self._ends_with_newline = chunk.endswith(b"\n")
        self._chunks.append(chunk)
        self._mem_bytes += len(chunk)

        if not self._spilled and self._total_bytes > self._limit:
            self._spilled = True
            self._open_spill()

        if self._file is not None:
            try:
                self._file.write(chunk)
            except (OSError, ValueError):
                self._abandon_spill()

        # Trim only after the spill file exists (or failed) — before that
        # the memory copy is the only complete copy.
        if self._spilled:
            while self._mem_bytes > 2 * self._limit and len(self._chunks) > 1:
                self._mem_bytes -= len(self._chunks.popleft())

    def _open_spill(self) -> None:
        try:
            self._file = tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f"bash-{self._label}-",
                suffix=".log",
                delete=False,
            )
            self._path = self._file.name
            # Nothing has been trimmed yet, so this is the stream from
            # byte 0. The chunk that tripped the limit is already in
            # ``_chunks`` and gets written by the caller's append.
            for prior in list(self._chunks)[:-1]:
                self._file.write(prior)
        except OSError:
            self._abandon_spill()

    def _abandon_spill(self) -> None:
        self._spill_failed = True
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
        self._file = None
        self._path = None

    def finish(self) -> TailCapture:
        with self._lock:
            return self._finish_locked()

    def _finish_locked(self) -> TailCapture:
        self._finished = True
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                self._abandon_spill()
            self._file = None
        tail = b"".join(self._chunks)
        if self._spilled:
            # The front of the tail may sit mid-UTF-8-sequence after a
            # trim; skip continuation bytes so the decode starts clean
            # (pi's trimToLastUtf8Bytes).
            start = 0
            while start < len(tail) and (tail[start] & 0xC0) == 0x80:
                start += 1
            tail = tail[start:]
        total_lines = self._newlines + (
            1 if self._total_bytes and not self._ends_with_newline else 0
        )
        return TailCapture(
            text=tail.decode(errors="replace"),
            total_bytes=self._total_bytes,
            total_lines=total_lines,
            spilled=self._spilled,
            spill_path=self._path,
        )


def _format_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / (1024 * 1024):.1f}MB"


def render_tail(capture: TailCapture, limit_chars: int) -> str:
    """Reply text for one stream: the last ``limit_chars`` plus a footer
    naming the totals and the complete-output file (pi's bash.ts footer).

    Small streams (never spilled, within the limit) pass through
    untouched — byte-identical to today's small-output replies.
    """
    if not capture.spilled and len(capture.text) <= limit_chars:
        return capture.text
    shown = capture.text[-limit_chars:]
    shown_lines = shown.count("\n") + (0 if shown.endswith("\n") else 1)
    header = (
        f"[Showing last {shown_lines} of {capture.total_lines} lines "
        f"({_format_size(capture.total_bytes)} total)."
    )
    if capture.spill_path is not None:
        footer = f"{header} Full output: {capture.spill_path}]"
    else:
        footer = f"{header} Earlier output was discarded.]"
    return f"{shown}\n\n{footer}"
