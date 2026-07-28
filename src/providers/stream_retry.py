"""Shared classifier for mid-stream transport drops.

Both wire families lose streams the same way and must make the same call
about re-issuing, so the predicate lives here rather than in either
provider. It was previously Anthropic-only, which is why DeepSeek trials
died on the identical error the Anthropic path had already learned to
survive.

Kept dependency-free on purpose: ``openai_compatible`` importing
``anthropic_provider`` (or the reverse) would pull a heavy SDK into the
fast-path CLI's import graph and risk a cycle.
"""

from __future__ import annotations

#: Substrings of a connection that died mid-response, with no HTTP status
#: and no model output to salvage. httpx raises these as
#: ``RemoteProtocolError``/``ReadError``; the SDKs surface them as
#: ``APIConnectionError``. Matched on the message as well as the type
#: because the SDKs wrap and re-word some of them.
TRANSIENT_STREAM_DROP_MARKERS = (
    "peer closed connection",
    "incomplete chunked read",
    "server disconnected",
    "connection reset",
    "connection aborted",
)

#: Exception type names that are always a transport drop regardless of text.
_TRANSIENT_STREAM_DROP_TYPES = frozenset({
    "APIConnectionError",
    "RemoteProtocolError",
    "ReadError",
    "ConnectError",
    "ChunkedEncodingError",
})


def is_transient_stream_drop(exc: BaseException) -> bool:
    """True for a mid-stream disconnect that is safe to re-attempt.

    Deliberately narrow. A dropped connection carries no HTTP status and no
    partial result worth keeping, so re-issuing the request is the same
    decision the idle watchdog already makes — whereas a 4xx (auth, bad
    request) must never be retried, and those arrive as ``APIStatusError``
    subclasses that this predicate rejects via the status-code check.

    Motivation, one instance per wire:

    * Anthropic — terminal-bench 2.1 regex-chess (2026-07-25): a single
      ``peer closed connection without sending complete message body
      (incomplete chunked read)`` killed a 24-minute trial outright, just
      after a passing 1500-position fuzz run.
    * DeepSeek — the same benchmark (2026-07-27): the identical error ended
      8 of 89 trials, 16% of all failures, because this logic lived on the
      Anthropic provider only.
    """
    # Anything carrying an HTTP status is a server verdict, not a drop.
    if getattr(exc, "status_code", None) is not None:
        return False
    if type(exc).__name__ in _TRANSIENT_STREAM_DROP_TYPES:
        return True
    text = str(exc).lower()
    return any(marker in text for marker in TRANSIENT_STREAM_DROP_MARKERS)
