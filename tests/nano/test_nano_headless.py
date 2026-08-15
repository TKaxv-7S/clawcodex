"""End-to-end nano headless run against a scripted fake provider.

Reuses the fake-wiring pattern of tests/entrypoints/test_headless_goal.py,
but keeps the REAL registry construction path — the point is to observe
exactly what a nano session sends on the wire: six tools, the nano system
prompt, and a first message that is the user's prompt (no
<available-deferred-tools> block).
"""

from __future__ import annotations

import io

import pytest

from src.entrypoints import HeadlessOptions, run_headless
from src.entrypoints import headless as headless_mod
from src.providers.base import ChatResponse


class _FakeProvider:
    def __init__(self, api_key: str, base_url=None, model=None, *, responses=None):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model or "fake-model"
        self._responses = list(responses or [])
        self.calls: list[dict] = []

    def chat(self, messages, tools=None, **kwargs):
        self.calls.append(
            {"messages": messages, "tools": tools, "kwargs": kwargs}
        )
        if not self._responses:
            raise AssertionError("FakeProvider ran out of scripted responses")
        return self._responses.pop(0)

    def chat_stream(self, messages, tools=None, **kwargs):
        raise NotImplementedError


class _Wiring:
    def __init__(self) -> None:
        self.script: list[ChatResponse] = []
        self.created: list[_FakeProvider] = []

    def extend(self, items) -> None:
        self.script.extend(items)


@pytest.fixture
def fake_wiring(monkeypatch):
    wiring = _Wiring()

    def _fake_provider_class(provider_name):
        def _ctor(api_key, base_url=None, model=None):
            p = _FakeProvider(
                api_key, base_url, model, responses=list(wiring.script)
            )
            wiring.created.append(p)
            return p

        return _ctor

    monkeypatch.setattr(headless_mod, "get_provider_class", _fake_provider_class)
    monkeypatch.setattr(
        headless_mod,
        "get_provider_config",
        lambda name: {"api_key": "test-key", "default_model": "fake-model"},
    )
    monkeypatch.setattr(headless_mod, "get_default_provider", lambda: "anthropic")
    monkeypatch.setattr(
        "src.entrypoints.provider_validation.get_provider_validation_error",
        lambda name: None,
    )

    import src.settings.settings as settings_mod
    from src.settings.types import SettingsSchema

    monkeypatch.setattr(
        "src.services.startup_gates.check_trust_accepted", lambda root: True
    )
    monkeypatch.setattr(
        settings_mod, "load_settings", lambda *a, **k: SettingsSchema()
    )
    monkeypatch.setattr(settings_mod, "_settings_cache", None)
    yield wiring
    settings_mod._settings_cache = None


def _text(text: str) -> ChatResponse:
    return ChatResponse(
        content=text,
        model="fake-model",
        usage={"input_tokens": 5, "output_tokens": 3},
        finish_reason="end_turn",
        tool_uses=None,
    )


def _tool_use(name: str, tool_input: dict, tool_id: str = "toolu_1") -> ChatResponse:
    return ChatResponse(
        content="",
        model="fake-model",
        usage={"input_tokens": 5, "output_tokens": 3},
        finish_reason="tool_use",
        tool_uses=[{"id": tool_id, "name": name, "input": tool_input}],
    )


def _run(prompt: str, tmp_path, **opt_kwargs):
    stdout, stderr = io.StringIO(), io.StringIO()
    code = run_headless(HeadlessOptions(
        prompt=prompt,
        output_format="text",
        stdout=stdout,
        stderr=stderr,
        workspace_root=tmp_path,
        **opt_kwargs,
    ))
    return code, stdout.getvalue(), stderr.getvalue()


def _role(msg) -> str:
    return str(msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", ""))


def _system_text(call: dict) -> str:
    # Anthropic wire: kwargs["system"] (str or block list). Every other
    # provider: a leading {"role": "system"} message (query.py:1189).
    system = call["kwargs"].get("system", "")
    if isinstance(system, list):
        return "\n".join(
            b.get("text", "") for b in system if isinstance(b, dict)
        )
    if system:
        return str(system)
    msgs = call["messages"]
    if msgs and _role(msgs[0]) == "system":
        return _message_text(msgs[0])
    return ""


def _non_system_messages(call: dict) -> list:
    return [m for m in call["messages"] if _role(m) != "system"]


def _content_text(content) -> str:
    """Recursively flatten message/block content to text.

    Handles the nesting that matters here: a ``tool_result`` block carries
    its payload under its own ``content`` key (str or block list), not
    under ``text``.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_content_text(b) for b in content)
    if isinstance(content, dict):
        if "text" in content:
            return str(content.get("text") or "")
        if "content" in content:
            return _content_text(content.get("content"))
        return ""
    return str(content)


def _message_text(msg) -> str:
    content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
    return _content_text(content)


def test_nano_sends_six_tools_and_nano_prompt(fake_wiring, tmp_path):
    fake_wiring.extend([_text("done")])
    code, out, err = _run("hello", tmp_path, nano=True)
    assert code == 0, err
    assert "done" in out

    provider = fake_wiring.created[-1]
    call = provider.calls[0]

    tool_names = [t["name"] for t in (call["tools"] or call["kwargs"].get("tools") or [])]
    assert tool_names == ["Read", "Bash", "Edit", "Write", "Grep", "Glob"]

    system_text = _system_text(call)
    assert "nano mode" in system_text
    assert "# Doing tasks" not in system_text
    assert "# auto memory" not in system_text

    # Byte-stability invariant: no <available-deferred-tools> preamble —
    # the first conversation message is the user's prompt itself.
    convo = _non_system_messages(call)
    assert convo, "no conversation messages reached the provider"
    assert "<available-deferred-tools>" not in _message_text(convo[0])
    assert "hello" in "".join(_message_text(m) for m in convo)


def test_nano_turns_eco_on(fake_wiring, tmp_path):
    from src.eco.state import is_eco_session

    fake_wiring.extend([_text("done")])
    code, _, err = _run("hello", tmp_path, nano=True)
    assert code == 0, err
    assert is_eco_session()


def test_default_run_is_unchanged(fake_wiring, tmp_path):
    from src.eco.state import is_eco_session
    from src.nano.state import is_nano_mode

    fake_wiring.extend([_text("done")])
    code, _, err = _run("hello", tmp_path)
    assert code == 0, err
    assert not is_nano_mode()
    assert not is_eco_session()

    provider = fake_wiring.created[-1]
    call = provider.calls[0]
    tool_names = {t["name"] for t in (call["tools"] or call["kwargs"].get("tools") or [])}
    # The maximal surface: orchestration present, far more than nano's six.
    # (The exact set is environment-dependent — is_enabled()/settings gates —
    # so assert shape, not membership roll-call.)
    assert "Agent" in tool_names
    assert len(tool_names) > 15
    assert "# Doing tasks" in _system_text(call)


def test_nano_bash_round_trip(fake_wiring, tmp_path):
    fake_wiring.extend([
        _tool_use("Bash", {"command": "echo nano-ok"}),
        _text("finished"),
    ])
    code, out, err = _run(
        "run it", tmp_path, nano=True, skip_permissions=True,
        permission_mode="bypassPermissions",
        is_bypass_permissions_mode_available=True,
    )
    assert code == 0, err
    assert "finished" in out

    provider = fake_wiring.created[-1]
    assert len(provider.calls) == 2
    followup = "".join(_message_text(m) for m in provider.calls[1]["messages"])
    assert "nano-ok" in followup
