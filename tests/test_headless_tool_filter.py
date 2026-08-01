"""Regression tests for ``--allowedTools`` / ``--disallowedTools`` filtering.

Both the headless (``--print``) and agent-server paths expose a
``_filter_registry`` helper that backs the ``--allowedTools`` /
``--disallowedTools`` CLI flags. It historically called a non-existent
``registry.unregister`` inside a bare ``try/except``, so the filtering was a
silent no-op: the flags removed nothing from the pool the model saw (tool
schemas are emitted from ``registry.list_tools()``). These tests lock in that
the helper now calls the real ``ToolRegistry.remove_tool`` so the flags take
effect.
"""

from __future__ import annotations

from src.entrypoints import headless as headless_mod
from src.server import agent_server as server_mod
from src.tool_system.defaults import build_default_registry


def _names(registry) -> set[str]:
    return {t.name for t in registry.list_tools()}


def test_registry_lacks_unregister_but_has_remove_tool():
    """The bug was calling ``unregister`` (absent) instead of ``remove_tool``.

    Guards against a future rename silently reintroducing the dead path.
    """
    registry = build_default_registry(provider="anthropic")
    assert not hasattr(registry, "unregister")
    assert hasattr(registry, "remove_tool")


def test_headless_filter_denylist_removes_from_pool():
    registry = build_default_registry(provider="anthropic")
    assert "AskUserQuestion" in _names(registry)

    deny = {"askuserquestion"}
    headless_mod._filter_registry(
        registry, keep=lambda n: n.lower() not in deny
    )

    remaining = _names(registry)
    assert "AskUserQuestion" not in remaining, (
        "disallowed tool must be dropped from list_tools() (the schema source), "
        "not left in the pool the model sees"
    )
    # Untargeted tools survive.
    assert "Bash" in remaining
    assert "Read" in remaining


def test_headless_filter_allowlist_keeps_only_allowed():
    registry = build_default_registry(provider="anthropic")
    keep_set = {"bash", "read", "write", "edit"}
    headless_mod._filter_registry(registry, keep=lambda n: n.lower() in keep_set)

    remaining = {n.lower() for n in _names(registry)}
    assert remaining == keep_set, remaining


def test_agent_server_filter_matches_headless_behavior():
    """The agent-server (TUI/interactive) path shares the same contract."""
    registry = build_default_registry(provider="anthropic")
    deny = {"workflow", "croncreate"}
    server_mod._filter_registry(registry, keep=lambda n: n.lower() not in deny)

    remaining = {n.lower() for n in _names(registry)}
    assert "workflow" not in remaining
    assert "croncreate" not in remaining
    assert "bash" in remaining


def test_filter_is_idempotent_and_survives_unknown_names():
    registry = build_default_registry(provider="anthropic")
    before = _names(registry)
    # Denying a name that isn't registered must not raise or change anything.
    headless_mod._filter_registry(registry, keep=lambda n: n.lower() != "not_a_tool")
    assert _names(registry) == before


def test_denylist_by_alias_removes_tool_from_schema_and_dispatch():
    """--disallowedTools accepts an ALIAS and still removes the real tool.

    ``TaskStop`` carries the ``KillShell`` alias. Denying the alias must drop
    ``TaskStop`` from ``list_tools()`` (the schema the model sees) AND make it
    unreachable via ``get()`` (dispatch), not leave it half-present.
    """
    registry = build_default_registry(provider="anthropic")
    assert registry.get("KillShell") is not None  # alias resolves pre-filter
    assert "TaskStop" in _names(registry)

    deny = registry.canonicalize_names(["KillShell"])
    assert deny == {"taskstop"}  # alias resolved to primary
    headless_mod._filter_registry(registry, keep=lambda n: n.lower() not in deny)

    assert "TaskStop" not in _names(registry), "alias-denied tool still in schema"
    assert registry.get("TaskStop") is None, "primary still dispatch-reachable"
    assert registry.get("KillShell") is None, "alias still dispatch-reachable"


def test_allowlist_by_alias_keeps_the_real_tool():
    """--allowedTools with an alias keeps its tool (and drops the rest)."""
    registry = build_default_registry(provider="anthropic")
    allow = registry.canonicalize_names(["Task", "Bash"])  # Task -> Agent
    assert allow == {"agent", "bash"}
    headless_mod._filter_registry(registry, keep=lambda n: n.lower() in allow)

    remaining = {n.lower() for n in _names(registry)}
    assert remaining == {"agent", "bash"}


def test_canonicalize_passes_unknown_names_through():
    registry = build_default_registry(provider="anthropic")
    assert registry.canonicalize_names(["Bash", "NotATool"]) == {"bash", "notatool"}


def test_remove_tool_by_alias_clears_canonical_dispatch_key():
    """remove_tool given an ALIAS must also drop the canonical key.

    Defense-in-depth for a now load-bearing method: a future caller passing an
    alias must not leave the tool dispatch-reachable via its primary name.
    """
    registry = build_default_registry(provider="anthropic")
    assert registry.get("TaskStop") is not None
    assert registry.remove_tool("KillShell") is True  # removed by ALIAS
    assert "TaskStop" not in _names(registry)
    assert registry.get("TaskStop") is None  # canonical key cleared
    assert registry.get("KillShell") is None  # alias key cleared


def test_canonicalize_skips_blanks_so_allowlist_cannot_wipe_all():
    """A stray "" must not become a match-nothing allowlist removing everything."""
    registry = build_default_registry(provider="anthropic")
    assert registry.canonicalize_names(["", "  ", "Bash"]) == {"bash"}

    allow = registry.canonicalize_names([""])
    assert allow == set()
    # An empty allow set means "no allowlist constraint given" at the call
    # sites (they gate on truthiness), so nothing is filtered here.
    before = _names(registry)
    if allow:
        headless_mod._filter_registry(registry, keep=lambda n: n.lower() in allow)
    assert _names(registry) == before


def test_headless_unregisters_ask_user_question_source():
    """Headless must not merely stub AskUserQuestion — it must unregister it.

    Stubbing only ``tool_context.ask_user`` leaves the tool in the registry,
    so the model can still reach it (via ToolSearch even when deferred
    loading keeps it out of the initial set), spend turns on it, and hand
    the task back to a user who cannot exist on this surface. Observed on
    terminal-bench 2.1 (fix-git, 2026-07-25): two calls, then a "let me
    explain what I found" ending with the work undone.

    Asserted against the source because ``run_headless`` needs a live
    provider/session to reach the line.
    """
    import inspect

    src = inspect.getsource(headless_mod.run_headless)
    assert 'remove_tool("AskUserQuestion")' in src, (
        "headless must unregister AskUserQuestion, not just stub ask_user"
    )
    # And the removal must precede the allow/deny filtering, so an explicit
    # --allowed-tools list cannot resurrect it.
    assert src.index('remove_tool("AskUserQuestion")') < src.index(
        "_filter_registry"
    ), "unregister AskUserQuestion before the allow/deny filters run"


def test_remove_tool_is_idempotent_for_ask_user_question():
    """The headless unregister must be safe when the tool is already absent
    (e.g. a provider whose default registry never included it)."""
    registry = build_default_registry(provider="anthropic")
    assert registry.remove_tool("AskUserQuestion") is True
    assert registry.remove_tool("AskUserQuestion") is False
    assert "AskUserQuestion" not in _names(registry)


def test_headless_unregisters_the_plan_mode_pair_source():
    """Headless must unregister BOTH plan-mode tools, not just ExitPlanMode.

    ExitPlanMode is the other ``requires_user_interaction`` tool, which makes
    it bypass-immune, so headlessly its ask reaches ``handle_permission_ask``
    with no handler and returns a DENY carrying the raw dialog question,
    "Exit plan mode?", as a tool error. EnterPlanMode auto-allows. The pair
    is therefore a one-way door: the model enters plan mode and can never
    leave, so every following write is refused.

    Upstream fixed this the same way — ExitPlanModeV2Tool.ts:167-178 and
    EnterPlanModeTool.ts:52-63 both disable when the user is not watching the
    TUI, explicitly "so plan mode isn't a trap the model can enter but never
    leave." Removing only the exit would leave entry reachable and make the
    trap worse, which is why both are asserted here.

    Measured on terminal-bench 2.1 (2026-08-01): 41 ExitPlanMode calls, a
    100% error rate, across 16 of 32 trials.
    """
    import inspect

    src = inspect.getsource(headless_mod.run_headless)
    for name in ("ExitPlanMode", "EnterPlanMode"):
        assert f'remove_tool("{name}")' in src, (
            f"headless must unregister {name}: plan mode cannot complete "
            "without an interactive approval surface"
        )
        assert src.index(f'remove_tool("{name}")') < src.index("_filter_registry"), (
            f"unregister {name} before the allow/deny filters run"
        )


def test_plan_mode_pair_removal_is_idempotent():
    registry = build_default_registry(provider="anthropic")
    for name in ("ExitPlanMode", "EnterPlanMode"):
        assert registry.remove_tool(name) is True
        assert registry.remove_tool(name) is False
        assert name not in _names(registry)


def test_default_registry_keeps_plan_mode():
    """The removal is headless-only, applied to the registry run_headless
    builds. The DEFAULT registry — what the TUI/agent-server path starts
    from, and that path installs a real round-trip approval handler — must
    still carry both. This is the guard against 'fixing' the trap by
    deleting plan mode everywhere."""
    registry = build_default_registry(provider="anthropic")
    names = _names(registry)
    assert "EnterPlanMode" in names
    assert "ExitPlanMode" in names


def test_removed_tool_is_not_resurrected_by_the_execution_fallback():
    """Removal must mean UNREACHABLE, not merely unadvertised.

    ``run_tool_use`` falls back to the full base-tool list when a called name
    is absent from the session's tools. TS accepts that fallback only when the
    name is one of the tool's deprecated ``aliases`` (toolExecution.ts:415-426,
    "Only fall back for tools where the name matches an alias, not the primary
    name"); the port had dropped that guard, so any removed tool resolved by
    primary name and executed anyway.

    That silently un-did every deliberate removal — ``--disallowed-tools``,
    and headless's unregistering of the tools that cannot work without a user.
    It mattered most for ExitPlanMode, which the plan-mode reminder explicitly
    tells the model to call.
    """
    import inspect

    from src.services.tool_execution import tool_execution as te

    src = inspect.getsource(te)
    assert 'getattr(fallback, "aliases", None)' in src, (
        "the base-tool fallback must be gated on the called name being a "
        "deprecated ALIAS, or removing a tool does not disable it"
    )


def test_alias_fallback_still_resolves_deprecated_names():
    """The guard must not break the case the fallback exists for.

    Drives the REAL fallback: TaskStop is removed from the active pool, so
    resolving its deprecated alias ``KillShell`` can only succeed through the
    base-list fallback. Behavioural rather than a source string-match, and it
    fails if the guard is over-tightened to reject aliases too.
    """
    from src.tool_system.build_tool import find_tool_by_name

    registry = build_default_registry(provider="anthropic")
    registry.remove_tool("TaskStop")
    assert find_tool_by_name(registry.list_tools(), "KillShell") is None, (
        "precondition: the alias must be gone from the active pool"
    )
    base = find_tool_by_name(build_default_registry().list_tools(), "KillShell")
    assert base is not None
    assert "KillShell" in (getattr(base, "aliases", None) or ()), (
        "the fallback guard keys off the called name being in .aliases"
    )
    # A removed PRIMARY name has no alias entry, so the same guard rejects it.
    plan = find_tool_by_name(build_default_registry().list_tools(), "ExitPlanMode")
    assert "ExitPlanMode" not in (getattr(plan, "aliases", None) or ())


def test_plan_addendum_reads_the_registry_not_options_tools():
    """The non-interactive plan addendum must key off ``tool_registry``.

    ``ToolContext.options.tools`` defaults to ``[]`` and is not populated
    until ``query()`` assigns it (query.py) from this same registry — which
    happens AFTER the attachment block runs. Reading it there returns ``[]``
    on the FIRST query of every session, so the interactive TUI would be told
    "the AskUserQuestion and ExitPlanMode tools are NOT available here" on
    exactly the turn that carries the full plan text, then silently correct
    itself from turn 2 on. Intermittent, order-dependent, and aimed at the
    surface where plan mode actually works.
    """
    import inspect

    from src.query import agent_loop_compat

    src = inspect.getsource(agent_loop_compat)
    marker = '_interactive = ('
    assert marker in src
    window = src[src.index(marker): src.index(marker) + 300]
    assert "tool_registry.list_tools()" in window, (
        "read availability from tool_registry (populated now), not from "
        "options.tools (empty until query() assigns it)"
    )
    assert "options" not in window, (
        "options.tools is empty on the first query — it must not be the source"
    )


def test_plan_addendum_absent_for_a_full_registry():
    """Behavioural companion: a full (interactive) registry must not get the
    non-interactive correction, and a headless one must."""
    from src.context_system.plan_mode import (
        NON_INTERACTIVE_PLAN_ADDENDUM,
        build_plan_mode_attachments,
    )
    from src.tool_system.build_tool import find_tool_by_name
    from src.types.messages import create_user_message

    msgs = [create_user_message(content="hi")]
    full = build_default_registry(provider="anthropic")
    headless = build_default_registry(provider="anthropic")
    for name in ("AskUserQuestion", "ExitPlanMode", "EnterPlanMode"):
        headless.remove_tool(name)

    def addendum_for(registry):
        interactive = (
            find_tool_by_name(registry.list_tools(), "ExitPlanMode") is not None
        )
        texts = build_plan_mode_attachments(msgs, "plan", interactive=interactive)
        return any(NON_INTERACTIVE_PLAN_ADDENDUM in t for t in texts)

    assert addendum_for(full) is False, "the TUI must not get the correction"
    assert addendum_for(headless) is True, "headless must get the correction"
