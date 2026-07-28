"""Mid-stream transport drops must be retried on the OpenAI-compatible wire.

The Anthropic provider has retried these since #747. This path did not, and
on terminal-bench 2.1 with deepseek-v4-pro (2026-07-27) that asymmetry ended
8 of 89 trials -- 16% of every failure -- on the identical error string the
Anthropic path already survived.
"""

from __future__ import annotations

import pytest

from src.providers.base import ChatResponse
from src.providers.openai_compatible import OpenAICompatibleProvider
from src.providers.stream_retry import is_transient_stream_drop


DROP = "peer closed connection without sending complete message body (incomplete chunked read)"


class _Concrete(OpenAICompatibleProvider):
    """Minimal concrete subclass — the ABC needs these two implemented."""

    def _create_client(self):  # pragma: no cover - never called
        raise AssertionError("client must not be built in these tests")

    def get_available_models(self):
        return ["deepseek-v4-pro"]


def _provider(monkeypatch, attempts: list, outcomes):
    """A provider whose single streaming attempt yields ``outcomes`` in order."""
    p = _Concrete.__new__(_Concrete)
    seq = iter(outcomes)

    def fake_attempt(self, *a, **kw):
        attempts.append(kw)
        nxt = next(seq)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt

    monkeypatch.setattr(OpenAICompatibleProvider, "_stream_attempt", fake_attempt)
    return p


def _ok(text="done"):
    return ChatResponse(
        content=text,
        model="deepseek-v4-pro",
        usage={"input_tokens": 1, "output_tokens": 1},
        finish_reason="stop",
    )


class TestTransientDropRetry:
    def test_drop_is_retried_and_succeeds(self, monkeypatch):
        attempts: list = []
        p = _provider(monkeypatch, attempts, [Exception(DROP), _ok()])
        out = p.chat_stream_response([{"role": "user", "content": "hi"}])
        assert out.content == "done"
        assert len(attempts) == 2, "the drop should have been re-issued once"

    def test_retry_is_bounded_and_reraises_the_original(self, monkeypatch):
        attempts: list = []
        original = Exception(DROP)
        p = _provider(monkeypatch, attempts, [original, original])
        with pytest.raises(Exception) as ei:
            p.chat_stream_response([{"role": "user", "content": "hi"}])
        assert ei.value is original, "callers must see the real cause, not a wrapper"
        assert len(attempts) == 2, "must not retry unboundedly"

    def test_http_status_errors_are_never_retried(self, monkeypatch):
        """A 400 is a server verdict — re-issuing it just burns a request.

        This is the guard that keeps the DeepSeek image_url 400 (a separate,
        real bug) from being retried into a second identical rejection.
        """
        class Status400(Exception):
            status_code = 400

        attempts: list = []
        err = Status400("unknown variant `image_url`, expected `text`")
        p = _provider(monkeypatch, attempts, [err])
        with pytest.raises(Status400):
            p.chat_stream_response([{"role": "user", "content": "hi"}])
        assert len(attempts) == 1

    def test_no_retry_once_text_reached_the_caller(self, monkeypatch):
        """Otherwise the retry replays a prefix the user already saw."""
        attempts: list = []
        seen: list[str] = []

        def fake_attempt(self, *a, **kw):
            attempts.append(kw)
            cb = kw.get("on_text_chunk")
            if cb:
                cb("partial ")
            raise Exception(DROP)

        monkeypatch.setattr(OpenAICompatibleProvider, "_stream_attempt", fake_attempt)
        p = _Concrete.__new__(_Concrete)
        with pytest.raises(Exception, match="peer closed"):
            p.chat_stream_response(
                [{"role": "user", "content": "hi"}],
                on_text_chunk=seen.append,
            )
        assert len(attempts) == 1, "streamed output must not be duplicated"
        assert seen == ["partial "]

    def test_thinking_output_also_suppresses_retry(self, monkeypatch):
        attempts: list = []

        def fake_attempt(self, *a, **kw):
            attempts.append(kw)
            cb = kw.get("on_thinking_chunk")
            if cb:
                cb("reasoning ")
            raise Exception(DROP)

        monkeypatch.setattr(OpenAICompatibleProvider, "_stream_attempt", fake_attempt)
        p = _Concrete.__new__(_Concrete)
        with pytest.raises(Exception, match="peer closed"):
            p.chat_stream_response(
                [{"role": "user", "content": "hi"}],
                on_thinking_chunk=lambda _t: None,
            )
        assert len(attempts) == 1


class TestSharedPredicate:
    """Both wires must classify identically — that was the whole defect."""

    @pytest.mark.parametrize("msg", [
        DROP,
        "Server disconnected without sending a response",
        "Connection reset by peer",
        "connection aborted",
    ])
    def test_transport_drops_match(self, msg):
        assert is_transient_stream_drop(Exception(msg))

    def test_status_bearing_errors_do_not_match(self):
        class E(Exception):
            status_code = 429
        assert not is_transient_stream_drop(E("rate limited"))

    def test_unrelated_errors_do_not_match(self):
        assert not is_transient_stream_drop(ValueError("bad tool schema"))

    def test_anthropic_alias_is_the_shared_function(self):
        from src.providers.anthropic_provider import _is_transient_stream_drop
        assert _is_transient_stream_drop is is_transient_stream_drop
