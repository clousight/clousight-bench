"""The measured-path retry policy: opt-in, validated, and digest-visible."""

from __future__ import annotations

import time
from typing import Any

import pytest

from clousight_bench.suites import llm_common
from clousight_bench.suites.llm_common import RetryPolicy


def test_absent_config_is_one_attempt() -> None:
    """The default must be indistinguishable from today's single-shot call."""
    for params in (None, {}, {"retry": None}):
        policy = RetryPolicy.from_params(params)
        assert policy.max_attempts == 1
        assert policy.enabled is False
        assert policy.canonical() is None


def test_explicit_policy_is_read() -> None:
    policy = RetryPolicy.from_params(
        {"retry": {"max_attempts": 4, "backoff_base_s": 0.5, "backoff_max_s": 2.0}}
    )
    assert (policy.max_attempts, policy.backoff_base_s, policy.backoff_max_s) == (4, 0.5, 2.0)
    assert policy.enabled is True


def test_partial_policy_keeps_defaults_for_the_rest() -> None:
    policy = RetryPolicy.from_params({"retry": {"max_attempts": 2}})
    assert policy.max_attempts == 2
    assert policy.backoff_base_s == 0.2
    assert policy.backoff_max_s == 5.0


@pytest.mark.parametrize("bad", [0, -1])
def test_max_attempts_below_one_is_an_error_not_a_clamp(bad: int) -> None:
    """A typo must fail the run, not silently disable what the user asked for."""
    with pytest.raises(ValueError, match="max_attempts"):
        RetryPolicy.from_params({"retry": {"max_attempts": bad}})


def test_backoff_is_exponential_and_capped() -> None:
    policy = RetryPolicy.from_params(
        {"retry": {"max_attempts": 9, "backoff_base_s": 1.0, "backoff_max_s": 4.0}}
    )
    # 1-based attempt: 1 -> base, 2 -> 2x, 3 -> 4x, then the cap holds
    assert [policy.backoff_for(n) for n in (1, 2, 3, 4, 5)] == [1.0, 2.0, 4.0, 4.0, 4.0]


def test_canonical_is_none_when_disabled_and_stable_when_enabled() -> None:
    """The clean-run digest must not move; an enabled policy must be reproducible."""
    assert RetryPolicy.from_params({"retry": {"max_attempts": 1}}).canonical() is None
    first = RetryPolicy.from_params({"retry": {"max_attempts": 3}}).canonical()
    second = RetryPolicy.from_params({"retry": {"max_attempts": 3}}).canonical()
    assert first == second
    assert first == {"backoff_base_s": 0.2, "backoff_max_s": 5.0, "max_attempts": 3}


class _FakeResponse:
    def __init__(self, status: int, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise _FakeHTTPError(self)

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeHTTPError(Exception):
    def __init__(self, response: _FakeResponse) -> None:
        super().__init__(f"HTTP {response.status_code}")
        self.response = response


_OK_BODY = {
    "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
}


class _FakeTimeout(Exception):
    """Stands in for ``requests.exceptions.Timeout``."""


class _FakeConnectionError(Exception):
    """Stands in for ``requests.exceptions.ConnectionError``."""


class _FakeUnknownError(Exception):
    """A failure the classifier has never heard of, and must therefore not retry."""


def _fake_requests(statuses: list[int], monkeypatch: Any, sleeps: list[float]) -> list[int]:
    """Serve ``statuses`` in order; record every backoff sleep. Returns the call log.

    The sleep stub records the requested delay **and still sleeps it**. Recording
    alone would make `overhead_ms` measure a clock that never advanced, so the
    one test that discriminates success time from overhead could never pass.
    Every test but that one uses a millisecond-scale backoff, so the cost is
    negligible.

    The fake module carries `exceptions.Timeout` / `exceptions.ConnectionError`
    as well as `HTTPError`, because the retry classifier has to recognise a
    timeout and a dropped connection, not only an HTTP status — and an
    attribute the implementation reaches for and the fake lacks is an
    `AttributeError` masquerading as a test failure.
    """
    return _fake_transport(list(statuses), monkeypatch, sleeps)


def _fake_transport(
    outcomes: list[Any],
    monkeypatch: Any,
    sleeps: list[float],
    *,
    traceparents: list[str] | None = None,
    per_call_s: float = 0.0,
) -> list[Any]:
    """The double behind `_fake_requests`, one notch more general.

    Each element of ``outcomes`` is an HTTP status to answer with, an exception
    INSTANCE to raise, or a ready-made `_FakeResponse` (the way to serve a 200
    whose body cannot be parsed). A timeout or a dropped connection never
    produces a response, so those failures cannot be expressed as a status and
    the classifier has to reach them by type. Outcomes run out at 200.

    ``traceparents``, when passed, collects the header each request actually
    carried, which is the only way to observe which span id reached the server.

    ``per_call_s`` makes the request itself take real wall time, slept off the
    REAL clock so it is never recorded as a backoff. Without it every attempt
    costs ~0ms, and a test cannot tell `success_ms` measured per-attempt from
    `success_ms` measured over the whole call — the difference between them is
    exactly the time an attempt takes.
    """
    import time as _real_time

    # Bind the REAL sleep before the patch lands: llm_common.time IS the stdlib
    # time module, so patching its .sleep would otherwise make this stub call
    # itself (RecursionError, not a slept backoff).
    _real_sleep = _real_time.sleep

    calls: list[Any] = []

    def post(*_args: Any, **kwargs: Any) -> _FakeResponse:
        outcome = outcomes[len(calls)] if len(calls) < len(outcomes) else 200
        calls.append(outcome)
        if traceparents is not None:
            traceparents.append(str((kwargs.get("headers") or {}).get("traceparent", "")))
        if per_call_s:
            _real_sleep(per_call_s)
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, _FakeResponse):
            return outcome
        return _FakeResponse(outcome, _OK_BODY if outcome < 400 else None)

    exceptions = type(
        "exceptions",
        (),
        {
            "HTTPError": _FakeHTTPError,
            "Timeout": _FakeTimeout,
            "ConnectionError": _FakeConnectionError,
        },
    )
    module = type(
        "requests",
        (),
        {
            "post": staticmethod(post),
            "HTTPError": _FakeHTTPError,
            "Timeout": _FakeTimeout,
            "ConnectionError": _FakeConnectionError,
            "exceptions": exceptions,
        },
    )
    monkeypatch.setitem(__import__("sys").modules, "requests", module)

    def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        _real_sleep(seconds)

    monkeypatch.setattr(llm_common.time, "sleep", _sleep)
    return calls


def _call(**kwargs: Any) -> tuple[str, dict[str, Any], str]:
    base = dict(endpoint="http://127.0.0.1:1/v1", model="m", api_key="", prompt="p", max_tokens=8)
    base.update(kwargs)
    return llm_common.chat_once(**base)


def test_default_does_not_retry(monkeypatch: Any) -> None:
    """The headline guarantee: no policy means one request, and the failure stands."""
    sleeps: list[float] = []
    calls = _fake_requests([429, 200], monkeypatch, sleeps)
    with pytest.raises(_FakeHTTPError):
        _call()
    assert calls == [429]
    assert sleeps == []


def test_opt_in_retries_until_success(monkeypatch: Any) -> None:
    sleeps: list[float] = []
    calls = _fake_requests([429, 503, 200], monkeypatch, sleeps)
    sink: dict[str, Any] = {}
    content, _usage, _finish = _call(
        retry=RetryPolicy(max_attempts=3, backoff_base_s=0.01, backoff_max_s=0.04),
        retry_sink=sink,
    )
    assert content == "hi"
    assert calls == [429, 503, 200]
    assert sink["attempts"] == 3
    assert len(sleeps) == 2  # one per failed attempt, none after the success


def test_attempts_are_bounded(monkeypatch: Any) -> None:
    sleeps: list[float] = []
    calls = _fake_requests([429, 429, 429, 429], monkeypatch, sleeps)
    with pytest.raises(_FakeHTTPError):
        _call(retry=RetryPolicy(max_attempts=2, backoff_base_s=0.01), retry_sink={})
    assert calls == [429, 429]
    assert len(sleeps) == 1  # no sleep after the final, giving-up attempt


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_errors_are_never_retried(status: int, monkeypatch: Any) -> None:
    """A malformed request will not succeed on a second try; retrying only hides it."""
    sleeps: list[float] = []
    calls = _fake_requests([status, 200], monkeypatch, sleeps)
    with pytest.raises(_FakeHTTPError):
        _call(retry=RetryPolicy(max_attempts=4, backoff_base_s=0.01), retry_sink={})
    assert calls == [status]
    assert sleeps == []


def test_sink_separates_success_from_overhead(monkeypatch: Any) -> None:
    """latency_ms must be the successful attempt alone; the cost is reported beside it.

    The backoff is the discriminator. A 200ms sleep between the failed attempt
    and the successful one means overhead_ms must clear 200ms while success_ms
    stays far below it — an implementation that folded the whole ordeal into
    success_ms, or that reported a single total in both fields, fails here.
    Asserting `>= 0.0` on either field would not: that passes for every
    implementation, including a broken one.

    Those bounds alone are still not enough, which is why the request itself now
    costs 100ms and the last assertion exists. An implementation that reported
    the GRAND TOTAL as overhead_ms clears 200ms too, and still differs from
    success_ms, so every bound above holds for it. What it cannot do is
    PARTITION: the two fields have to add up to the wall time the caller
    measured, and a total-plus-a-part adds up to one attempt too much.
    """
    sleeps: list[float] = []
    _fake_transport([429, 200], monkeypatch, sleeps, per_call_s=0.1)
    sink: dict[str, Any] = {}
    began = time.perf_counter()
    _call(retry=RetryPolicy(max_attempts=2, backoff_base_s=0.2), retry_sink=sink)
    measured_ms = (time.perf_counter() - began) * 1000.0
    assert sink["attempts"] == 2
    assert sink["overhead_ms"] >= 200.0, "the backoff sleep belongs in overhead"
    assert sink["success_ms"] < 200.0, "the successful attempt did not wait"
    assert sink["success_ms"] != sink["overhead_ms"]
    assert sink["success_ms"] >= 100.0, "the winning attempt's own 100ms is in success_ms"
    assert abs((sink["success_ms"] + sink["overhead_ms"]) - measured_ms) < 50.0, (
        "success_ms and overhead_ms must partition the call, not both report the total"
    )


def test_one_attempt_emits_exactly_one_span(monkeypatch: Any) -> None:
    """The default trace shape must not move because a disabled feature exists."""
    sleeps: list[float] = []
    _fake_requests([200], monkeypatch, sleeps)
    spans: list[dict[str, Any]] = []
    _call(trace_id="a" * 32, span_sink=spans)
    assert len(spans) == 1
    assert spans[0]["name"] == "chat /chat/completions"
    assert spans[0]["parent_span_id"] == ""


def test_retried_call_nests_attempts_under_one_parent(monkeypatch: Any) -> None:
    sleeps: list[float] = []
    _fake_requests([429, 200], monkeypatch, sleeps)
    spans: list[dict[str, Any]] = []
    _call(
        trace_id="a" * 32,
        span_sink=spans,
        retry=RetryPolicy(max_attempts=2, backoff_base_s=0.01),
        retry_sink={},
    )
    parents = [s for s in spans if s["parent_span_id"] == ""]
    children = [s for s in spans if s["parent_span_id"] != ""]
    assert len(parents) == 1
    assert len(children) == 2
    assert {c["parent_span_id"] for c in children} == {parents[0]["span_id"]}
    assert [c["status"] for c in children] == ["ERROR", "OK"]


@pytest.mark.parametrize("error", [_FakeTimeout("timed out"), _FakeConnectionError("connection reset")])
def test_responseless_transport_failures_are_retried(error: Exception, monkeypatch: Any) -> None:
    """A timeout and a dropped connection are the failures a retry exists for.

    Neither carries a response, so neither can be classified by status — only by
    exception type. Nothing above exercises that branch: the status-driven tests
    would pass just as well against a classifier that only ever looked at
    `status_code` and gave up on everything else.
    """
    sleeps: list[float] = []
    calls = _fake_transport([error, 200], monkeypatch, sleeps)
    content, _usage, _finish = _call(retry=RetryPolicy(max_attempts=2, backoff_base_s=0.01))
    assert content == "hi"
    assert calls == [error, 200]
    assert sleeps == [0.01]


def test_each_attempt_sends_its_own_traceparent(monkeypatch: Any) -> None:
    """One HTTP request is one span, so each attempt must put ITS OWN id in the
    header — an OTel-instrumented endpoint nests its server-side spans under the
    attempt that actually reached it, not under the parent that wraps them all."""
    sleeps: list[float] = []
    sent: list[str] = []
    spans: list[dict[str, Any]] = []
    _fake_transport([429, 200], monkeypatch, sleeps, traceparents=sent)
    _call(
        trace_id="a" * 32,
        span_sink=spans,
        retry=RetryPolicy(max_attempts=2, backoff_base_s=0.01),
        retry_sink={},
    )
    parent = next(s for s in spans if s["parent_span_id"] == "")
    children = [s for s in spans if s["parent_span_id"] != ""]
    ids_sent = [tp.split("-")[2] for tp in sent]
    assert ids_sent == [c["span_id"] for c in children]
    assert parent["span_id"] not in ids_sent


def test_the_lone_attempt_still_sends_the_id_of_its_own_span(monkeypatch: Any) -> None:
    """Disabled, the header id and the span id are one id — as they are today."""
    sleeps: list[float] = []
    sent: list[str] = []
    spans: list[dict[str, Any]] = []
    _fake_transport([200], monkeypatch, sleeps, traceparents=sent)
    _call(trace_id="a" * 32, span_sink=spans)
    assert [tp.split("-")[2] for tp in sent] == [spans[0]["span_id"]]


def test_an_unrecognised_failure_is_not_retried(monkeypatch: Any) -> None:
    """The permissive direction is the dangerous one.

    Every other classifier test drives a failure that SHOULD be retried, so a
    classifier that simply said "yes" to everything would pass them all. This
    one drives a failure type the classifier has never heard of — the shape a
    client-side bug takes — and a benchmark that retried past it would publish a
    number the endpoint never earned.
    """
    sleeps: list[float] = []
    calls = _fake_transport([_FakeUnknownError("a bug, not a blip"), 200], monkeypatch, sleeps)
    with pytest.raises(_FakeUnknownError):
        _call(retry=RetryPolicy(max_attempts=4, backoff_base_s=0.01), retry_sink={})
    assert calls == [calls[0]]  # exactly one request: the 200 was never reached
    assert sleeps == []


def test_a_failed_span_carries_its_status_only_once_retries_are_on(monkeypatch: Any) -> None:
    """`http.response.status_code` is new information, and new information on the
    DEFAULT path is a changed trace for every existing run.

    Attaching it unconditionally reads like a pure improvement, which is exactly
    why it needs pinning: today's ERROR span carries two attributes and must go
    on carrying two. An attempt span, which never existed before, is free to say
    more.
    """
    sleeps: list[float] = []
    _fake_requests([429, 429], monkeypatch, sleeps)
    disabled: list[dict[str, Any]] = []
    with pytest.raises(_FakeHTTPError):
        _call(trace_id="a" * 32, span_sink=disabled)
    assert len(disabled) == 1
    assert disabled[0]["attributes"] == {"gen_ai.operation.name": "chat", "gen_ai.request.model": "m"}

    _fake_requests([429, 429], monkeypatch, sleeps)
    enabled: list[dict[str, Any]] = []
    with pytest.raises(_FakeHTTPError):
        _call(
            trace_id="a" * 32,
            span_sink=enabled,
            retry=RetryPolicy(max_attempts=2, backoff_base_s=0.01),
            retry_sink={},
        )
    children = [s for s in enabled if s["parent_span_id"] != ""]
    assert [c["attributes"]["http.response.status_code"] for c in children] == [429, 429]


def test_the_sink_is_filled_when_a_200_body_cannot_be_read(monkeypatch: Any) -> None:
    """The sink's contract is "always filled", and a caller reads it unguarded.

    A 200 carrying a body that cannot be parsed is the one failure that does not
    come through the transport's error path, so it is the one that would leave
    the sink empty and turn a caller's `sink["attempts"]` into a KeyError. It is
    also not retryable: the endpoint answered, it just answered nonsense, and
    asking again gets the same nonsense.
    """
    sleeps: list[float] = []
    sink: dict[str, Any] = {}
    _fake_transport([_FakeResponse(200, {"choices": 5})], monkeypatch, sleeps)
    with pytest.raises(TypeError):
        _call(retry=RetryPolicy(max_attempts=3, backoff_base_s=0.01), retry_sink=sink)
    assert sink == {"attempts": 1, "success_ms": 0.0, "overhead_ms": sink["overhead_ms"]}
    assert sink["overhead_ms"] >= 0.0
    assert sleeps == []


def test_an_unreadable_body_records_no_span_on_the_default_path(monkeypatch: Any) -> None:
    """...and filling the sink must not have bought that at the cost of a span
    this path has never emitted. The default trace shape is the invariant."""
    sleeps: list[float] = []
    spans: list[dict[str, Any]] = []
    sink: dict[str, Any] = {}
    _fake_transport([_FakeResponse(200, {"choices": 5})], monkeypatch, sleeps)
    with pytest.raises(TypeError):
        _call(trace_id="a" * 32, span_sink=spans, retry_sink=sink)
    assert spans == []
    assert sink["attempts"] == 1
