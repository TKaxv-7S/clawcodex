"""Nano mode — a pi-shaped minimal profile of clawcodex (``clawcodex --nano``).

Ported from the pi agent harness study (my-docs/clawcodex-nano/): under
``--nano`` the harness sends a ~600-token system prompt and six tools
(Read, Bash, Edit, Write, Grep, Glob), injects nothing per turn, and keeps
the request prefix byte-stable. Without the flag, nothing in this package
runs and clawcodex behaves exactly as before.

Design doc: my-docs/clawcodex-nano/nano-design.md (gitignored, on-disk).
"""

from .prompt import build_nano_prompt_blocks
from .registry import NANO_TOOL_NAMES, build_nano_registry
from .state import is_nano_mode, reset_nano_mode, set_nano_mode

__all__ = [
    "NANO_TOOL_NAMES",
    "build_nano_prompt_blocks",
    "build_nano_registry",
    "is_nano_mode",
    "reset_nano_mode",
    "set_nano_mode",
]
