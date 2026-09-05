"""chat_once carries W3C traceparent and records v3 gen_ai spans when traced."""

from __future__ import annotations

import pytest

from clousight_bench.core.sut_span import validate_span
from clousight_bench.suites.llm_common import chat_once

requests = pytest.importorskip("requests")  # the [probe] extra; absent on the no-extras CI floor

_TRACE_ID = "e" * 32


class _Resp:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return {
            "choices": [{"message": {"content": "B"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3},
        }


def test_traceparent_injected_and_gen_ai_span_recorded(monkeypatch):
    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None, allow_redirects=None):  # noqa: A002, ARG001
        captured["headers"] = headers
        return _Resp()

    monkeypatch.setattr(requests, "post", fake_post)
    sink: list = []
    content, usage, finish = chat_once(
        endpoint="https://llm.example.com/v1",
        model="qwen-max",
        api_key="k",
        prompt="Q",
        max_tokens=8,
        trace_id=_TRACE_ID,
        span_sink=sink,
    )
    assert content == "B" and finish == "stop"
    tp = captured["headers"]["traceparent"]
    assert tp.startswith(f"00-{_TRACE_ID}-") and tp.endswith("-01")
    assert len(sink) == 1
    span = sink[0]
    validate_span(span)  # v3-valid
    assert span["attributes"]["gen_ai.request.model"] == "qwen-max"
    assert span["attributes"]["gen_ai.usage.input_tokens"] == 11
    assert span["status"] == "OK"
    # the traceparent span id IS the recorded span id — the SUT's APM links up
    assert tp.split("-")[2] == span["span_id"]


def test_untraced_call_has_no_traceparent_and_no_span(monkeypatch):
    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None, allow_redirects=None):  # noqa: A002, ARG001
        captured["headers"] = headers
        return _Resp()

    monkeypatch.setattr(requests, "post", fake_post)
    sink: list = []
    chat_once(
        endpoint="https://llm.example.com/v1",
        model="m",
        api_key="",
        prompt="Q",
        max_tokens=8,
        span_sink=sink,
    )
    assert "traceparent" not in captured["headers"]
    assert sink == []


def test_failed_call_records_an_error_span(monkeypatch):
    def fake_post(*a, **k):  # noqa: ARG001
        raise requests.exceptions.ConnectionError("boom")

    monkeypatch.setattr(requests, "post", fake_post)
    sink: list = []
    with pytest.raises(Exception, match="boom"):
        chat_once(
            endpoint="https://llm.example.com/v1",
            model="m",
            api_key="",
            prompt="Q",
            max_tokens=8,
            trace_id=_TRACE_ID,
            span_sink=sink,
        )
    assert len(sink) == 1 and sink[0]["status"] == "ERROR"
    validate_span(sink[0])
