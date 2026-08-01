from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ..types.messages import Message
from ..tool_system.context import ToolContext


ContinueReason = Literal[
    "next_turn",
    "max_output_tokens_recovery",
    "max_output_tokens_escalate",
    "reactive_compact_retry",
    "collapse_drain_retry",
    "stop_hook_blocking",
    "token_budget_continuation",
    "continuation_nudge",
]


TerminalReason = Literal[
    "blocking_limit",
    "image_error",
    "model_error",
    "aborted_streaming",
    "prompt_too_long",
    "completed",
    "stop_hook_prevented",
    "aborted_tools",
    "hook_stopped",
    "max_turns",
    "tool_failure_loop",
]


# Terminal reasons where the AGENT LOOP ended the run rather than the model
# finishing, mapped to the reference's result subtypes (QueryEngine.ts:891
# ``error_max_turns``, :1142 ``error_during_execution``). Every one of these
# used to be reported as ``subtype: "success", is_error: false`` by headless,
# so a batch runner recorded a clean completion that merely scored zero.
#
# Lives here, beside the taxonomy it is keyed on, rather than in an
# entrypoint: more than one surface has to agree on it (headless today, the
# agent-server's turn outcome next), and importing an entrypoint for one dict
# would invert the layering.
#
# Accounts for the WHOLE taxonomy above. Not mapped, deliberately:
#   * ``completed``                     — the normal path;
#   * ``aborted_streaming`` / ``aborted_tools`` / ``model_error`` — re-raised
#     by agent_loop_compat, so they already reach exit 130 / 1 and the
#     cancelled / error subtypes;
#   * ``hook_stopped`` / ``stop_hook_prevented`` — a hook refusing to continue
#     is the operator's own policy working as configured, not the harness
#     cutting a run short. Neither can fire without an installed hook that
#     emits those signals. Listed rather than omitted so the exclusion reads
#     as a decision.
#
# ``blocking_limit`` and ``prompt_too_long`` are the worst of the set: their
# explanation goes to ``last_api_error_text``, which agent_loop_compat only
# surfaces as response_text for ``tool_failure_loop``, so those runs reported
# success with an EMPTY result — a clean completion with no evidence at all.
EARLY_STOP_SUBTYPES: dict[str, str] = {
    "tool_failure_loop": "error_during_execution",
    "blocking_limit": "error_during_execution",
    "prompt_too_long": "error_during_execution",
    "image_error": "error_during_execution",
    "max_turns": "error_max_turns",
}


@dataclass(frozen=True)
class Transition:
    reason: ContinueReason
    attempt: int | None = None
    committed: int | None = None


@dataclass(frozen=True)
class Terminal:
    reason: TerminalReason
    error: Exception | None = None
    turn_count: int | None = None


class TerminalHolder:
    """Mutable container an async generator appends its Terminal to
    just before its bare ``return``. Callers read ``.value`` after
    consuming the generator.

    Required because Python's async generator protocol does not
    expose the return value of a ``return`` statement, and Python
    forbids ``return value`` inside ``async def`` generators
    (SyntaxError on 3.10-3.14; PEP 828 still Draft).
    """

    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value: Terminal | None = None


def set_terminal(
    holder: TerminalHolder,
    flag: list[bool],
    terminal: Terminal,
) -> None:
    """Set holder.value and mark the exit as natural.

    Use at every ``return`` site inside the query loop's inner
    generator. Call as the last action before ``return``.

    The ``flag`` argument is a single-element list[bool] used as a
    mutable container that the outer wrapper can inspect after
    iteration to distinguish natural termination from
    ``.aclose()`` / exception unwinds. Phase A introduces the helper
    and the parameter; a future phase wires the outer wrapper that
    reads the flag — until then ``flag`` is write-only and the
    parameter is preserved for forward compatibility.
    """
    holder.value = terminal
    flag[0] = True


ToolUseContext = ToolContext


@dataclass
class QueryState:
    messages: list[Message]
    tool_use_context: ToolUseContext
    auto_compact_tracking: Any | None = None
    max_output_tokens_recovery_count: int = 0
    has_attempted_reactive_compact: bool = False
    max_output_tokens_override: int | None = None
    stop_hook_active: bool | None = None
    turn_count: int = 1
    # Promise from previous turn's Haiku summary, resolved during current streaming.
    # Mirrors TS State.pendingToolUseSummary at query.ts:212. Currently unused in
    # Python (tool-use summary is out-of-scope for ch5) but reserved so callers
    # can plumb a future Haiku promise through state without a struct change.
    pending_tool_use_summary: Any | None = None
    # Count of consecutive continuation nudges within the current turn.
    # Capped at MAX_CONTINUATION_NUDGES to prevent infinite nudge loops
    # when the model keeps matching continuation signals without tool calls.
    # Mirrors TS State.continuationNudgeCount at query.ts:218.
    continuation_nudge_count: int = 0
    transition: Transition | None = None
