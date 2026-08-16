"""PR3 deterministic guards: truncation guard + compaction ledger.

The truncation guard is pi's agent-loop.ts:381-406 semantic — a
max_tokens-cut response's tool calls are failed, never executed — proven
end-to-end through run_headless with a scripted fake provider, in both
directions (nano fails them; default still runs them).
"""

from __future__ import annotations

import pytest

from src.nano.compact_ledger import (
    append_file_ops_ledger,
    extract_file_ops,
    format_file_operations,
    parse_previous_ledger,
)
from src.nano.state import set_nano_mode

from .test_nano_headless import (  # reuse the fake-provider wiring
    ChatResponse,
    _message_text,
    _non_system_messages,
    _run,
    _text,
    _tool_use,
    fake_wiring,  # noqa: F401  (pytest fixture)
)

# ---------------------------------------------------------------------------
# Truncation guard
# ---------------------------------------------------------------------------


def _truncated_tool_use(name: str, tool_input: dict) -> ChatResponse:
    return ChatResponse(
        content="",
        model="fake-model",
        usage={"input_tokens": 5, "output_tokens": 3},
        finish_reason="max_tokens",
        tool_uses=[{"id": "toolu_cut", "name": name, "input": tool_input}],
    )


MARKER = "guard-should-prevent-this"


def test_nano_fails_tool_calls_from_truncated_response(
    fake_wiring, tmp_path
):
    target = tmp_path / "victim.txt"
    fake_wiring.extend([
        _truncated_tool_use(
            "Write", {"file_path": str(target), "content": MARKER}
        ),
        _text("re-issued nothing, done"),
    ])
    code, _out, err = _run(
        "write it", tmp_path, nano=True, skip_permissions=True,
        permission_mode="bypassPermissions",
        is_bypass_permissions_mode_available=True,
    )
    assert code == 0, err
    # The Write must NOT have executed…
    assert not target.exists()
    # …and the model must have been told to re-issue.
    provider = fake_wiring.created[-1]
    followup = "".join(
        _message_text(m) for m in _non_system_messages(provider.calls[1])
    )
    assert "was not executed" in followup
    assert "Re-issue the tool call" in followup


def test_default_still_runs_truncated_survivors(fake_wiring, tmp_path):
    # Non-nano keeps the documented run-the-survivors behavior
    # (query.py max_tokens tagging rationale) — the guard must not leak.
    target = tmp_path / "victim.txt"
    fake_wiring.extend([
        _truncated_tool_use(
            "Write", {"file_path": str(target), "content": MARKER}
        ),
        _text("done"),
    ])
    code, _out, err = _run(
        "write it", tmp_path, skip_permissions=True,
        permission_mode="bypassPermissions",
        is_bypass_permissions_mode_available=True,
    )
    assert code == 0, err
    assert target.exists()
    assert target.read_text() == MARKER


def test_nano_normal_stop_still_runs_tools(fake_wiring, tmp_path):
    # The guard keys on stop_reason, not on nano alone.
    fake_wiring.extend([
        _tool_use("Bash", {"command": "echo guard-clear"}),
        _text("finished"),
    ])
    code, _out, err = _run(
        "run it", tmp_path, nano=True, skip_permissions=True,
        permission_mode="bypassPermissions",
        is_bypass_permissions_mode_available=True,
    )
    assert code == 0, err
    provider = fake_wiring.created[-1]
    followup = "".join(
        _message_text(m) for m in _non_system_messages(provider.calls[1])
    )
    assert "guard-clear" in followup
    assert "was not executed" not in followup


# ---------------------------------------------------------------------------
# Compaction file-op ledger
# ---------------------------------------------------------------------------


class _Msg:
    def __init__(self, role, content):
        self.role = role
        self.content = content


def _assistant_with_tools(*calls):
    return _Msg(
        "assistant",
        [
            {"type": "tool_use", "name": name, "input": {"file_path": path}}
            for name, path in calls
        ],
    )


def test_extract_file_ops_read_vs_modified():
    msgs = [
        _assistant_with_tools(("Read", "/a.py"), ("Read", "/b.py")),
        _assistant_with_tools(("Edit", "/b.py"), ("Write", "/c.py")),
        _Msg("user", "irrelevant"),
    ]
    read, modified = extract_file_ops(msgs)
    # /b.py was read AND modified → modified only (pi's computeFileLists).
    assert read == ["/a.py"]
    assert modified == ["/b.py", "/c.py"]


def test_ledger_is_cumulative_across_compactions():
    prev_summary = _Msg(
        "user",
        "Earlier summary…\n\n<read-files>\n/old_read.py\n</read-files>\n\n"
        "<modified-files>\n/old_mod.py\n</modified-files>",
    )
    span = [prev_summary, _assistant_with_tools(("Edit", "/new.py"))]
    out = append_file_ops_ledger("SUMMARY", span, span)
    assert "<read-files>\n/old_read.py\n</read-files>" in out
    assert "/old_mod.py" in out and "/new.py" in out
    assert out.startswith("SUMMARY")


def test_parse_previous_ledger_takes_most_recent():
    msgs = [
        _Msg("user", "<modified-files>\n/first.py\n</modified-files>"),
        _Msg("user", "<modified-files>\n/second.py\n</modified-files>"),
    ]
    _read, modified = parse_previous_ledger(msgs)
    assert modified == ["/second.py"]


def test_format_file_operations_omits_empty_sections():
    assert format_file_operations([], []) == ""
    only_mod = format_file_operations([], ["/m.py"])
    assert "<read-files>" not in only_mod
    assert "<modified-files>\n/m.py\n</modified-files>" in only_mod


# ---------------------------------------------------------------------------
# Advisor gate — nano never runs a reviewer model, whatever settings say
# ---------------------------------------------------------------------------


def _advisor_settings():
    from src.settings.types import SettingsSchema

    return SettingsSchema(
        advisor_enabled=True,
        advisor_model="deepseek-v4-pro",
        advisor_provider="deepseek",
        advisor_client_mode=True,
    )


def _patch_advisor_settings(monkeypatch):
    import src.settings.settings as settings_mod

    monkeypatch.setattr(
        settings_mod, "load_settings", lambda *a, **k: _advisor_settings()
    )
    monkeypatch.setattr(settings_mod, "_settings_cache", None)


def _sent_tool_names(call: dict) -> list[str]:
    return [t["name"] for t in (call["tools"] or call["kwargs"].get("tools") or [])]


def test_nano_never_activates_the_advisor(fake_wiring, tmp_path, monkeypatch):
    # Even with a fully configured, enabled advisor in settings (as a host
    # config leak or container seed would produce), a nano session must send
    # exactly the six tools and no advisor instructions — the gate sits above
    # the settings read (query.py advisor resolution).
    _patch_advisor_settings(monkeypatch)
    fake_wiring.extend([_text("done")])
    code, _out, err = _run("hello", tmp_path, nano=True)
    assert code == 0, err

    call = fake_wiring.created[-1].calls[0]
    assert _sent_tool_names(call) == [
        "Read", "Bash", "Edit", "Write", "Grep", "Glob",
    ]
    all_text = "".join(_message_text(m) for m in call["messages"]) + str(
        call["kwargs"].get("system", "")
    )
    assert "advisor" not in all_text.lower()


def test_default_mode_advisor_still_works(fake_wiring, tmp_path, monkeypatch):
    # The gate must be nano-scoped: the same settings in default mode
    # resolve CLIENT_SIDE (force_client + registered provider class) and
    # append the advisor tool schema.
    _patch_advisor_settings(monkeypatch)
    fake_wiring.extend([_text("done")])
    code, _out, err = _run("hello", tmp_path)
    assert code == 0, err

    call = fake_wiring.created[-1].calls[0]
    assert "advisor" in _sent_tool_names(call)


def test_compact_prompt_addendum_is_nano_gated():
    from src.services.compact.prompt import (
        NANO_ITERATIVE_ADDENDUM,
        get_compact_prompt,
    )

    assert NANO_ITERATIVE_ADDENDUM not in get_compact_prompt()
    set_nano_mode(True)
    try:
        assert NANO_ITERATIVE_ADDENDUM in get_compact_prompt()
    finally:
        set_nano_mode(False)
