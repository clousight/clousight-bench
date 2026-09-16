"""The measured-path retry policy: opt-in, validated, digest-visible — and wired
through the three llm suites to the call it was built for."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from clousight_bench.core.suite import DriverContext, EnvHandle, Target
from clousight_bench.suites import llm_common
from clousight_bench.suites.gsm8k.suite import Gsm8kSuite
from clousight_bench.suites.human_eval.suite import HumanEvalSuite
from clousight_bench.suites.llm_common import RetryPolicy, serving_measurements
from clousight_bench.suites.mmlu.suite import MmluSuite


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


@pytest.mark.parametrize("key", ["backoff_base_s", "backoff_max_s"])
def test_negative_backoff_is_rejected_where_it_is_configured(key: str) -> None:
    """The other two keys must fail the same way ``max_attempts`` does.

    Unvalidated, a negative backoff is accepted by ``from_params``, survives
    ``resolve()``, reaches the digest, and only then raises a bare
    ``ValueError: sleep length must be non-negative`` from inside ``time.sleep``
    — two attempts into a measured run, with no key named.
    """
    with pytest.raises(ValueError, match=f"params.retry.{key}"):
        RetryPolicy.from_params({"retry": {"max_attempts": 3, key: -0.5}})


def test_zero_backoff_is_legal() -> None:
    """Retrying with no wait is a deliberate choice, not a typo — only NEGATIVE
    is the error, so the validation must not over-reach into a clamp of its own."""
    policy = RetryPolicy.from_params(
        {"retry": {"max_attempts": 3, "backoff_base_s": 0.0, "backoff_max_s": 0.0}}
    )
    assert policy.backoff_for(1) == 0.0


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


def test_latency_excludes_failed_attempts(monkeypatch: Any) -> None:
    """A retried item's latency_ms is the successful attempt, not the whole ordeal."""
    sleeps: list[float] = []
    _fake_requests([429, 200], monkeypatch, sleeps)
    sink: dict[str, Any] = {}
    _call(
        retry=RetryPolicy(max_attempts=2, backoff_base_s=0.05),
        retry_sink=sink,
    )
    # The backoff alone is 50ms; a call-site wall clock would have included it.
    assert sink["success_ms"] < sink["overhead_ms"]


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


# ---------------------------------------------------------------------------
# the call sites: mmlu / gsm8k / human-eval
#
# ``cfg`` reaches only resolve(). prepare() never sees it and run() reads only
# env.payload, so a policy that is not deliberately carried across two payload
# hops silently disappears and every suite goes on calling chat_once single-shot.
# ---------------------------------------------------------------------------

_LLM_SUITES = ("mmlu", "gsm8k", "human-eval")
_RETRY_CFG = {"retry": {"max_attempts": 3, "backoff_base_s": 0.01}}
_CANONICAL_RETRY = {"backoff_base_s": 0.01, "backoff_max_s": 5.0, "max_attempts": 3}


class _ChatSpy:
    """Stands in for ``chat_once`` at a suite's call site.

    Fills the sink exactly as the real function contracts to, and records the
    policy every call was handed — which is the only way to observe that a
    configured policy survived the trip from ``resolve``'s ``cfg`` to the
    measured call. ``kwargs["retry_sink"]`` is indexed, not ``.get``-ed, so a
    call site that forgot to pass a sink fails here rather than reporting zeros.
    """

    def __init__(
        self,
        *,
        contents: list[str],
        attempts: int = 1,
        success_ms: float = 1.0,
        overhead_ms: float = 0.0,
    ) -> None:
        self._contents = contents
        self._attempts = attempts
        self._success_ms = success_ms
        self._overhead_ms = overhead_ms
        self.policies: list[RetryPolicy | None] = []

    def __call__(self, **kwargs: Any) -> tuple[str, dict[str, Any], str]:
        index = len(self.policies)
        self.policies.append(kwargs.get("retry"))
        sink = kwargs["retry_sink"]
        sink.update(
            {
                "attempts": self._attempts,
                "success_ms": self._success_ms,
                "overhead_ms": self._overhead_ms,
            }
        )
        content = self._contents[min(index, len(self._contents) - 1)]
        return content, {"prompt_tokens": 1, "completion_tokens": 1}, "stop"


class _Handle:
    """The credential handle ``resolve_endpoint`` reads the model + key off."""

    def model(self) -> str:
        return "test-model"

    def api_key(self) -> str:
        return "k"


def _endpoint_target() -> Target:
    return Target(mode="endpoint", mock=False, handle=_Handle(), endpoint="https://llm.example.com/v1")


def _suite_case(suite_id: str) -> tuple[Any, Any, dict[str, Any], str, list[str]]:
    """``(module, suite, cfg, rows_key, per-item completions)`` for one llm suite."""
    if suite_id == "mmlu":
        from clousight_bench.suites.mmlu import suite as mod

        return mod, mod.MmluSuite(), {"limit": 2}, "answers", ["A"]
    if suite_id == "gsm8k":
        from clousight_bench.suites.gsm8k import suite as mod

        return mod, mod.Gsm8kSuite(), {"limit": 2}, "answers", ["#### 4"]
    from clousight_bench.suites.human_eval import suite as mod

    suite = mod.HumanEvalSuite()
    cfg: dict[str, Any] = {"limit": 2, "allow_code_execution": True}
    # The real canonical solutions, so the sandbox really executes and really
    # passes — which is what makes this suite's latency_ms an execution time.
    contents = [p["canonical_solution"] for p in suite.resolve(cfg, None).payload["problems"]]
    return mod, suite, cfg, "results", contents


def _drive(
    module: Any, suite: Any, cfg: dict[str, Any], spy: _ChatSpy, monkeypatch: Any
) -> tuple[Any, Any, Any]:
    """The whole lifecycle a real run drives: resolve → prepare → run."""
    monkeypatch.setattr(module, "chat_once", spy)
    driver = DriverContext("local")
    dataset = suite.resolve(cfg, None)
    env = suite.prepare(_endpoint_target(), dataset, driver)
    return dataset, env, suite.run(_endpoint_target(), env, driver)


@pytest.mark.parametrize("suite_id", _LLM_SUITES)
def test_the_configured_policy_reaches_the_measured_call(suite_id: str, monkeypatch: Any) -> None:
    """``params.retry`` is plugin-reachable only until the suites carry it.

    The payloads must carry the CANONICAL DICT, not the dataclass, so they stay
    plain JSON-able data like everything else in them.
    """
    module, suite, cfg, _rows_key, contents = _suite_case(suite_id)
    spy = _ChatSpy(contents=contents)
    dataset, env, _raw = _drive(module, suite, {**cfg, **_RETRY_CFG}, spy, monkeypatch)

    assert isinstance(dataset.payload["retry"], dict)
    assert dataset.payload["retry"] == _CANONICAL_RETRY
    assert env.payload["retry"] == _CANONICAL_RETRY
    assert len(spy.policies) == 2
    assert all(isinstance(p, RetryPolicy) and p.max_attempts == 3 for p in spy.policies)


@pytest.mark.parametrize("suite_id", _LLM_SUITES)
def test_absent_retry_config_keeps_the_call_single_shot(suite_id: str, monkeypatch: Any) -> None:
    """Wiring a feature in must not turn it on: no ``params.retry`` is one attempt."""
    module, suite, cfg, _rows_key, contents = _suite_case(suite_id)
    spy = _ChatSpy(contents=contents)
    dataset, env, raw = _drive(module, suite, cfg, spy, monkeypatch)

    assert dataset.payload["retry"] is None
    assert env.payload["retry"] is None
    assert spy.policies and all(p is not None and p.enabled is False for p in spy.policies)
    summary = json.loads(raw.path("summary").read_text())
    assert summary["retry_enabled"] is False
    assert summary["retry_count"] == 0


@pytest.mark.parametrize("suite_id", _LLM_SUITES)
def test_a_default_run_seals_no_retry_overhead_at_all(suite_id: str, monkeypatch: Any) -> None:
    """A run that cannot retry must seal a flat ``0.0``, not a small true number.

    ``chat_once`` computes overhead as total-minus-success, so a single
    unretried attempt still returns a few stray microseconds. Summed into the
    summary they describe a retry that never happened AND make ``summary.json``
    a different file — a different ``sha256`` in the manifest, and so a
    different record — on every run of the same benchmark. The spy reports a
    deliberately large 7.5ms per item so an implementation that passes the
    number through fails here at 15.0 rather than at a hairline tolerance.
    """
    module, suite, cfg, _rows_key, contents = _suite_case(suite_id)
    spy = _ChatSpy(contents=contents, attempts=1, success_ms=10.0, overhead_ms=7.5)
    _dataset, _env, raw = _drive(module, suite, cfg, spy, monkeypatch)

    summary = json.loads(raw.path("summary").read_text())
    assert summary["retry_enabled"] is False
    assert summary["retry_overhead_ms"] == 0.0


def test_a_payload_without_the_key_is_the_disabled_policy(monkeypatch: Any) -> None:
    """An EnvHandle built before this key existed must still run — disabled, not
    ``KeyError``."""
    from clousight_bench.suites.mmlu import suite as mod

    suite = mod.MmluSuite()
    spy = _ChatSpy(contents=["A"])
    monkeypatch.setattr(mod, "chat_once", spy)
    env = EnvHandle(
        {
            "mock": False,
            "endpoint": "https://llm.example.com/v1",
            "model": "m",
            "api_key": "",
            "questions": suite.resolve({"limit": 1}, None).payload["questions"],
        }
    )
    raw = suite.run(_endpoint_target(), env, DriverContext("local"))
    assert [p.enabled for p in spy.policies] == [False]
    assert json.loads(raw.path("summary").read_text())["retry_enabled"] is False


@pytest.mark.parametrize("suite_id", ["mmlu", "gsm8k"])
def test_latency_is_the_successful_attempt_not_the_wall_clock(suite_id: str, monkeypatch: Any) -> None:
    """These two suites time the LLM call, and that wall clock now spans retries.

    The sink reports a successful attempt of 123.5ms against 400ms of overhead;
    a call-site ``perf_counter()`` delta around a stubbed call is a fraction of a
    millisecond, so only an implementation that reads ``success_ms`` can produce
    this number.
    """
    module, suite, cfg, rows_key, contents = _suite_case(suite_id)
    spy = _ChatSpy(contents=contents, attempts=3, success_ms=123.5, overhead_ms=400.0)
    _dataset, _env, raw = _drive(module, suite, {**cfg, **_RETRY_CFG}, spy, monkeypatch)

    rows = json.loads(raw.path(rows_key).read_text())
    assert [r["latency_ms"] for r in rows] == [123.5, 123.5]


def test_human_eval_latency_stays_the_sandboxed_execution(monkeypatch: Any) -> None:
    """The one suite that must NOT take its latency from the sink.

    HumanEval's ``latency_ms`` is set in ``_execute_run`` around
    ``run_candidate`` — the sandboxed execution of the generated code, a
    different loop from the chat call. Reading ``success_ms`` into it would swap
    an execution time for an LLM time while every other test went on passing, so
    this pins that the number is the sandbox's: the canonical solutions really
    ran, really passed, and took a time that is not the sink's.
    """
    module, suite, cfg, rows_key, contents = _suite_case("human-eval")
    spy = _ChatSpy(contents=contents, attempts=3, success_ms=123.5, overhead_ms=400.0)
    _dataset, _env, raw = _drive(module, suite, {**cfg, **_RETRY_CFG}, spy, monkeypatch)

    rows = json.loads(raw.path(rows_key).read_text())
    assert len(rows) == 2
    assert all(r["passed"] for r in rows), "the sandbox did not actually execute the completions"
    assert all(r["latency_ms"] != 123.5 for r in rows), "latency_ms became the LLM call"
    assert all(r["latency_ms"] > 0.0 for r in rows)


@pytest.mark.parametrize("suite_id", _LLM_SUITES)
def test_the_summary_publishes_what_the_retries_cost(suite_id: str, monkeypatch: Any) -> None:
    """Retries that nobody can see are retries that flatter the endpoint.

    Two items, three attempts each: four of the six attempts were retries. A
    total that counted attempts (6) or retried ITEMS (2) fails here.
    """
    module, suite, cfg, _rows_key, contents = _suite_case(suite_id)
    spy = _ChatSpy(contents=contents, attempts=3, success_ms=10.0, overhead_ms=7.5)
    _dataset, _env, raw = _drive(module, suite, {**cfg, **_RETRY_CFG}, spy, monkeypatch)

    summary = json.loads(raw.path("summary").read_text())
    assert len(spy.policies) == 2
    assert summary["retry_enabled"] is True
    assert summary["retry_count"] == 4
    assert summary["retry_overhead_ms"] == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# serving_measurements: publishing what the retries cost
# ---------------------------------------------------------------------------


def test_no_retry_measurements_when_retries_were_not_enabled() -> None:
    """A run that cannot retry must not report a retry count of zero —
    absence of evidence would read as evidence of absence."""
    out = serving_measurements("mmlu", [{"latency_ms": 10.0}], {})
    assert "mmlu.retry_count" not in out
    assert "mmlu.retry_overhead_ms" not in out


def test_retry_measurements_when_enabled() -> None:
    summary = {"retry_enabled": True, "retry_count": 3, "retry_overhead_ms": 1234.5}
    out = serving_measurements("mmlu", [{"latency_ms": 10.0}], summary)
    assert out["mmlu.retry_count"].value == 3
    assert out["mmlu.retry_count"].unit == "count"
    assert out["mmlu.retry_overhead_ms"].value == pytest.approx(1234.5)
    assert out["mmlu.retry_overhead_ms"].unit == "ms"
    for key in ("mmlu.retry_count", "mmlu.retry_overhead_ms"):
        assert out[key].official is False
        assert out[key].reproducibility_class == "environmental"


def test_zero_retries_still_reported_when_enabled() -> None:
    """Enabled-and-zero is a real result: the endpoint never needed a retry."""
    summary = {"retry_enabled": True, "retry_count": 0, "retry_overhead_ms": 0.0}
    out = serving_measurements("mmlu", [{"latency_ms": 10.0}], summary)
    assert out["mmlu.retry_count"].value == 0


# ---------------------------------------------------------------------------
# resolve(): a retry policy is a different dataset, but a clean run's digest
# must be bit-identical to every run recorded before this feature existed.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("suite_cls", [MmluSuite, Gsm8kSuite, HumanEvalSuite])
def test_clean_digest_is_unchanged_and_retry_digest_differs(suite_cls: type) -> None:
    """The clean-run digest must stay bit-identical to every run recorded before
    this feature existed; an enabled policy must make it a different dataset."""
    suite = suite_cls()
    clean_a = suite.resolve({"limit": 2}, None)
    clean_b = suite.resolve({"limit": 2, "retry": {"max_attempts": 1}}, None)
    retried = suite.resolve({"limit": 2, "retry": {"max_attempts": 3}}, None)
    assert clean_a.digest == clean_b.digest, "an absent or 1-attempt policy must not move the digest"
    assert retried.digest != clean_a.digest, "an enabled policy makes it a different dataset"
    assert "retry" in retried.version, "the version string must say so too, as YCSB's does"


# These pins are deliberately brittle: measured on this branch at 47d82af,
# immediately before this feature's digest change landed. A future
# suite_version bump legitimately changes them, and having to update the
# literal here is the point — it forces the change to be a decision rather
# than a silent drift.
_CLEAN_DIGEST_AT_LIMIT_2 = {
    MmluSuite: "sha256:6dd790727b5fea405ffbad3b3755056ce6754c598051d078be689342142c5465",
    Gsm8kSuite: "sha256:b884f646a04766f1812419364c7edfca893e1df1e66028cbc3d908ab1a70d19c",
    HumanEvalSuite: "sha256:b5a6dc541f4827c3cbfd63f7f536e40742579120703023fb853acdbc23952c73",
}


@pytest.mark.parametrize("suite_cls", [MmluSuite, Gsm8kSuite, HumanEvalSuite])
def test_clean_digest_matches_the_pre_feature_pin(suite_cls: type) -> None:
    """Absolute pin, not merely relative: a change that moved EVERY digest would
    still pass a `d_lim.digest != d_all.digest`-style relative check, so this
    pins the literal value recorded before this feature existed."""
    suite = suite_cls()
    handle = suite.resolve({"limit": 2}, None)
    assert handle.digest == _CLEAN_DIGEST_AT_LIMIT_2[suite_cls]


@pytest.mark.parametrize("suite_cls", [MmluSuite, Gsm8kSuite, HumanEvalSuite])
def test_clean_version_carries_no_retry_marker(suite_cls: type) -> None:
    """Absolute, not relative: a clean run's ``version`` must equal
    ``suite_version`` exactly, with no ``/retry-N`` suffix — an unconditional
    fold that always appended the marker would still pass every *relative*
    check in this file, so this compares directly against the known-good
    unmarked value rather than against a sibling run's version."""
    suite = suite_cls()
    handle = suite.resolve({"limit": 2}, None)
    assert handle.version == suite.suite_version
