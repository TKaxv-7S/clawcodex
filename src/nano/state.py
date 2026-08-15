"""Process-global nano-mode toggle.

Same shape as :mod:`src.eco.state` (the ``/eco`` session switch): one
process, one switch, set once at startup from ``--nano`` and consulted at
the few chokepoints that differ in nano mode (registry construction, system
prompt assembly, per-turn reminder injection). Subagents run in the same
process and inherit the mode — intended: a nano session should not spawn
maximal subagents (it has no Agent tool to do so anyway).

Default off, so a process that never calls :func:`set_nano_mode` follows
the standard clawcodex paths untouched.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_nano_on = False


def set_nano_mode(on: bool) -> None:
    """Turn nano mode on/off for this process (startup wiring only)."""
    global _nano_on
    with _lock:
        _nano_on = bool(on)


def is_nano_mode() -> bool:
    """Whether nano mode is active."""
    with _lock:
        return _nano_on


def reset_nano_mode() -> None:
    """Clear the toggle (test/teardown helper)."""
    set_nano_mode(False)
