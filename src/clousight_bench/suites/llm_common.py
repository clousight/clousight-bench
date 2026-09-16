"""Reusable building blocks for ``llm``-domain benchmark suites (public).

The bundled mmlu / gsm8k / human-eval suites are built from these; a third-party
or commercial LLM suite is expected to reuse them too — hence this is a public
module (not underscore-prefixed). It provides: artifact writing (``sha256_bytes``,
``write_artifacts``), per-item construction + aggregation (``rows_to_items``,
``serving_measurements``), and the SSRF-guarded OpenAI-compatible transport
(``resolve_endpoint``, ``chat_once``, ``EndpointJudge``, ``extract_code``,
``validate_endpoint``). Requires the ``[llm]`` extra (``requests``) for the real
endpoint path; offline/mock paths need nothing.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from clousight_bench.core.canonical import sha256_bytes  # re-exported for suites
from clousight_bench.core.judge import JudgeModel
from clousight_bench.core.observation import ItemResult, ItemScore, Measurement
from clousight_bench.core.progress import NULL_PROGRESS, ProgressReporter
from clousight_bench.core.suite import RawArtifacts
from clousight_bench.enrichers.pricing import tokens_1k_price
from clousight_bench.suites._progress import StepClock, raise_if_cancelled

__all__ = [
    "sha256_bytes",
    "rows_to_items",
    "write_artifacts",
    "serving_measurements",
    "extract_code",
    "validate_endpoint",
    "resolve_endpoint",
    "chat_once",
    "EndpointJudge",
    "ItemProgress",
    "RetryPolicy",
]


@dataclass(frozen=True)
class RetryPolicy:
    """Retry bounds for the MEASURED path — deliberately not ``ClientPolicy``.

    ``core/clients.py::ClientPolicy`` is the control-plane policy: creating a
    runtime or polling its status should retry by default, because retrying
    those changes no measurement. A measured ``/chat/completions`` call is the
    opposite — an endpoint that rate-limits you *is* worse, and silently
    retrying past that flatters the thing under test. Two correct-but-opposite
    defaults do not belong in one object, and ``ClientPolicy.max_attempts``
    already defaults to 3, so sharing it would have turned retries on for every
    existing run in silence.

    Absent config means exactly one attempt: today's behaviour, byte for byte.
    """

    max_attempts: int = 1
    backoff_base_s: float = 0.2
    backoff_max_s: float = 5.0

    @classmethod
    def from_params(cls, params: dict[str, Any] | None) -> RetryPolicy:
        retry = (params or {}).get("retry") or {}
        base = cls()
        max_attempts = int(retry.get("max_attempts", base.max_attempts))
        if max_attempts < 1:
            raise ValueError(f"params.retry.max_attempts must be >= 1, got {max_attempts}")
        return cls(
            max_attempts=max_attempts,
            backoff_base_s=float(retry.get("backoff_base_s", base.backoff_base_s)),
            backoff_max_s=float(retry.get("backoff_max_s", base.backoff_max_s)),
        )

    @property
    def enabled(self) -> bool:
        return self.max_attempts > 1

    def backoff_for(self, attempt: int) -> float:
        """Seconds to wait after 1-based ``attempt`` failed, exponential and capped."""
        delay = self.backoff_base_s * (2 ** max(0, attempt - 1))
        return min(delay, self.backoff_max_s)

    def canonical(self) -> dict[str, Any] | None:
        """The digest contribution — ``None`` when disabled, so a clean run's
        dataset digest stays bit-identical to every run recorded before this
        feature existed."""
        if not self.enabled:
            return None
        return {
            "backoff_base_s": self.backoff_base_s,
            "backoff_max_s": self.backoff_max_s,
            "max_attempts": self.max_attempts,
        }


class ItemProgress:
    """Live progress for an llm suite's per-item loop.

    One of these announces the loop's size up front, then draws one step per
    finished item, publishes that item's latency as a sample, advances the
    counter and polls for a cancel. It is built from the marks the suite already
    timed the item with, so it never takes a second, disagreeing measurement, and
    it reports strictly BETWEEN items — the next item's timer has not started, so
    nothing that reaches ``avg_latency_ms`` can move.

    Steps are named ``<suite_id>.<item_id>``. This is the one place the live and
    the sealed waterfall deliberately differ: the trajectory's spans are
    ``gen_ai`` call spans all named ``chat /chat/completions``, which is exactly
    right for an OTel consumer and useless as a row label — the live view needs a
    name that says WHICH item.

    Constructed with the default inert reporter it costs one ``perf_counter``
    read and does nothing, which is what the mock/offline paths get.
    """

    __slots__ = ("_clock", "_reporter", "_suite_id")

    def __init__(
        self,
        reporter: ProgressReporter = NULL_PROGRESS,
        *,
        suite_id: str,
        total: int,
        label: str = "Items",
        unit: str = "item",
    ) -> None:
        self._reporter = reporter
        self._suite_id = suite_id
        self._clock = StepClock()
        reporter.phase(label, total, unit=unit)

    def item(self, item_id: str, start: float, end: float, *, status: str = "ok") -> None:
        """A finished item: its step, its latency as a sample, one unit advanced."""
        start_ms = self._clock.ms(start)
        end_ms = self._clock.ms(end)
        self._reporter.step(f"{self._suite_id}.{item_id}", start_ms, end_ms, status=status)
        self._reporter.sample(f"{self._suite_id}.latency_ms", end_ms - start_ms)
        self._reporter.advance()

    def check_cancel(self) -> None:
        """Abort the loop if a cancel was requested (raises ``RunCancelled``)."""
        raise_if_cancelled(self._reporter, f"{self._suite_id} item loop")

    def now(self) -> float:
        """A clock mark in this loop's frame, for a suite that does not time
        its items itself (there is no measurement to reuse)."""
        return self._clock.now()


def rows_to_items(
    rows: list[dict[str, Any]],
    *,
    metric: str,
    id_key: str,
    correct_key: str,
    group_key: str | None = None,
    output_key: str | None = None,
    reference_key: str | None = None,
    latency_key: str = "latency_ms",
) -> list[ItemResult]:
    """Turn per-answer artifact rows into per-item :class:`ItemResult`s.

    Each row carries a boolean correctness flag (``correct_key``); it becomes one
    ``ItemScore`` valued 1.0/0.0 with status ``ok``/``fail``. The whole-run
    Measurement is then ``aggregate(items, metric, "ratio")`` — numerically the
    same ``correct/total`` ratio as before, now with a per-item substrate + CI.
    """
    items: list[ItemResult] = []
    for r in rows:
        ok = bool(r.get(correct_key))
        usage: dict[str, Any] = {}
        lat = r.get(latency_key)
        if isinstance(lat, (int, float)):
            usage["latency_ms"] = float(lat)
        items.append(
            ItemResult(
                item_id=str(r.get(id_key, "")),
                group=str(r.get(group_key) or "") if group_key else "",
                output=r.get(output_key) if output_key else None,
                reference=r.get(reference_key) if reference_key else None,
                scores=[ItemScore(metric=metric, value=1.0 if ok else 0.0, status="ok" if ok else "fail")],
                usage=usage,
            )
        )
    return items


def write_artifacts(
    tmp_dir: Path,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    *,
    rows_key: str,
    spans: list[dict[str, Any]] | None = None,
) -> RawArtifacts:
    """Write ``<rows_key>.json`` + ``summary.json`` and build the manifest.

    ``rows_key`` is the suite's per-item logical name — ``"answers"`` for the
    multiple-choice / numeric suites, ``"results"`` for HumanEval — and becomes
    both the filename stem and the manifest key the evaluator reads.
    """
    r_path = tmp_dir / f"{rows_key}.json"
    s_path = tmp_dir / "summary.json"
    r_path.write_text(json.dumps(rows), encoding="utf-8")
    s_path.write_text(json.dumps(summary), encoding="utf-8")
    manifest: dict[str, dict[str, Any]] = {
        rows_key: {
            "path": f"{rows_key}.json",
            "sha256": sha256_bytes(r_path.read_bytes()),
            "rows": len(rows),
        },
        "summary": {"path": "summary.json", "sha256": sha256_bytes(s_path.read_bytes()), "rows": None},
    }
    if spans:
        t_path = tmp_dir / "trajectory.jsonl"
        t_path.write_text("".join(json.dumps(s) + "\n" for s in spans), encoding="utf-8")
        manifest["trajectory"] = {
            "path": "trajectory.jsonl",
            "sha256": sha256_bytes(t_path.read_bytes()),
            "rows": len(spans),
        }
    return RawArtifacts(dir=tmp_dir, manifest=manifest)


def serving_measurements(
    prefix: str, rows: list[dict[str, Any]], summary: dict[str, Any], *, latency_key: str = "latency_ms"
) -> dict[str, Measurement]:
    """The ``avg_latency_ms`` / ``total_tokens`` / ``cost_usd`` block shared by the
    llm suites' official evaluators. Every key is ``<prefix>.``-namespaced and
    ``official=True``; a dimension is omitted when its data is absent."""
    out: dict[str, Measurement] = {}
    latencies = [float(r[latency_key]) for r in rows if isinstance(r.get(latency_key), (int, float))]
    if latencies:
        out[f"{prefix}.avg_latency_ms"] = Measurement(
            value=sum(latencies) / len(latencies),
            unit="ms",
            reproducibility_class="environmental",
            official=True,
            aggregation="mean",
            sample_count=len(latencies),
        )
    prompt_t = int(summary.get("prompt_tokens", 0) or 0)
    completion_t = int(summary.get("completion_tokens", 0) or 0)
    total_tokens = prompt_t + completion_t
    if total_tokens > 0:
        out[f"{prefix}.total_tokens"] = Measurement(
            value=total_tokens, unit="tokens", reproducibility_class="environmental", official=True
        )
        price_1k, source = tokens_1k_price()
        out[f"{prefix}.cost_usd"] = Measurement(
            value=(total_tokens / 1000.0) * price_1k,
            unit="usd",
            reproducibility_class="environmental",
            official=True,
            notes=f"tokens_1k price {price_1k} ({source})",
        )
    return out


# --- endpoint plumbing ------------------------------------------------------

_FENCE_RE = re.compile(r"^\s*```[^\n]*\n(.*?)\n?```\s*$", re.DOTALL)


def extract_code(text: str) -> str:
    """Strip a single wrapping markdown code fence from a model completion.

    Real chat endpoints routinely wrap code in ```` ```python … ``` ```` despite a
    "no fences" instruction; an un-stripped fence is a ``SyntaxError`` that would
    silently mis-score the candidate. Returns the inner code when the whole reply
    is one fenced block, else the text unchanged (a bare body is left as-is).
    """
    m = _FENCE_RE.match(text or "")
    return m.group(1) if m else (text or "")


_BLOCKED_ENDPOINT_HOSTS = {"metadata", "metadata.google.internal"}
# Cloud instance-metadata IPs not caught by the range checks: 169.254.169.254 is
# link-local (caught), but Alibaba Cloud's 100.100.100.200 is RFC 6598 shared
# space (not is_private on all Pythons), so block it explicitly.
_BLOCKED_METADATA_IPS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
}


def _host_to_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse a URL host to an IP, covering dotted/IPv6 AND the integer encodings
    (decimal / ``0x`` hex) that ``requests`` resolves but ``ip_address(str)``
    rejects — so a metadata IP cannot slip past as ``http://2852039166/``."""
    with contextlib.suppress(ValueError):
        return ipaddress.ip_address(host)
    try:
        return ipaddress.ip_address(int(host, 0))
    except (ValueError, TypeError):
        return None


def validate_endpoint(url: str) -> None:
    """SSRF guard before the operator's Bearer key is sent to ``url``.

    Requires an http(s) scheme and refuses cloud-metadata / link-local / reserved
    / multicast targets (incl. the Alibaba Cloud metadata IP and integer-encoded
    IP forms). Loopback / private hosts are allowed so self-hosted gateways keep
    working; https is recommended.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise RuntimeError(f"endpoint must be http(s), got {parsed.scheme!r}: {url}")
    host = (parsed.hostname or "").strip("[]").rstrip(".").lower()
    if not host:
        raise RuntimeError(f"endpoint has no host: {url}")
    if host in _BLOCKED_ENDPOINT_HOSTS:
        raise RuntimeError(f"endpoint host not allowed (SSRF guard): {host}")
    ip = _host_to_ip(host)
    if ip is not None and (
        ip in _BLOCKED_METADATA_IPS
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        raise RuntimeError(f"endpoint host not allowed (SSRF guard): {host} ({ip})")


def resolve_endpoint(target: Any, *, suite_id: str) -> tuple[str, str, str]:
    """Resolve ``(endpoint, model, api_key)`` from a run ``Target`` for the real
    path, SSRF-validating the endpoint. Raises if endpoint/model are missing."""
    handle = target.handle
    model = str(handle.model()) if handle is not None and hasattr(handle, "model") else ""
    api_key = str(handle.api_key()) if handle is not None and hasattr(handle, "api_key") else ""
    endpoint = str(target.endpoint or "")
    if not endpoint or not model:
        raise RuntimeError(
            f"the {suite_id} real run() path needs target.endpoint "
            "(OpenAI-compatible base URL) + target.model"
        )
    validate_endpoint(endpoint)
    return endpoint.rstrip("/"), model, api_key


def _retry_summary(model: str, attempts: int) -> dict[str, Any]:
    """Attributes for the wrapper span over a retried call. No ``gen_ai.usage.*``:
    those belong to the one attempt that reported them."""
    return {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": model,
        "csbench.retry.attempts": attempts,
    }


def _retryable_status(status_code: int | None) -> bool:
    """429 (rate limited) and 5xx (the server is having a moment) are worth
    another attempt; every other 4xx is the request's own fault and retrying it
    only hides a bug behind three identical failures."""
    if status_code is None:
        return False
    return status_code == 429 or 500 <= status_code < 600


def _retryable_error(exc: BaseException, requests_mod: Any) -> bool:
    """A timeout or a dropped connection has no response to read a status off,
    so those two are classified by exception type.

    The classes are looked up on ``requests.exceptions`` with a fallback to the
    top-level names, and anything missing is simply skipped: a transport double
    that spells them either way works, and one that spells them neither way is
    "nothing is retryable", not an ``AttributeError`` thrown from inside the
    failure handler.
    """
    source = getattr(requests_mod, "exceptions", None) or requests_mod
    classes: list[type[BaseException]] = []
    for name in ("Timeout", "ConnectionError"):
        cls = getattr(source, name, None) or getattr(requests_mod, name, None)
        if isinstance(cls, type) and issubclass(cls, BaseException):
            classes.append(cls)
    return bool(classes) and isinstance(exc, tuple(classes))


def chat_once(
    *,
    endpoint: str,
    model: str,
    api_key: str,
    prompt: str,
    max_tokens: int,
    timeout: float = 120.0,
    trace_id: str = "",
    span_sink: list[dict[str, Any]] | None = None,
    retry: RetryPolicy | None = None,
    retry_sink: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], str]:
    """One OpenAI-compatible ``/chat/completions`` call at ``temperature=0``.

    Returns ``(content, usage, finish_reason)``. Lazily imports ``requests`` so
    the offline paths never need it.

    When ``trace_id`` (32-hex) is given the request carries a W3C ``traceparent``
    header — an OTel-instrumented endpoint continues the run's trace inside the
    operator's own APM — and, when ``span_sink`` is also given, a schema-v3
    ``gen_ai.*`` span for the call is appended to it (status ERROR on failure).

    ``retry`` is opt-in and defaults to a one-attempt :class:`RetryPolicy`, i.e.
    exactly today's behaviour: one request, no sleeping, and one span named
    ``chat /chat/completions`` with ``parent_span_id=""`` carrying the same
    ``span_id`` that went into the ``traceparent`` header. Ask for more than one
    attempt and that span becomes the PARENT: each attempt appends a child
    (``chat /chat/completions attempt N``) with its own id, its own status and,
    on an HTTP failure, ``http.response.status_code``. Each attempt's request
    carries ITS OWN span id in ``traceparent``, not the parent's — one HTTP
    request is one span, so an instrumented endpoint nests its server-side spans
    under the attempt that actually reached it.

    ``retry_sink``, when given, is always filled with ``{"attempts",
    "success_ms", "overhead_ms"}`` before this function returns OR raises --
    ``success_ms`` is 0.0 on a call that never succeeded — so a caller may read
    its keys unguarded. It is an out-parameter mirroring ``span_sink`` rather
    than a widened return tuple, so every caller keeps unpacking three values.
    The suites time ``latency_ms`` at the call site, so they need the successful
    attempt's own duration back — folding failed attempts and backoff sleeps
    into the measured latency would corrupt the very number being benchmarked.
    """
    import requests  # noqa: PLC0415 - lazy; only the real path needs it

    policy = retry or RetryPolicy()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    span_id = ""
    start_ns = 0
    if trace_id:
        from clousight_bench.core.tracing import new_span_id  # noqa: PLC0415

        span_id = new_span_id()
        headers["traceparent"] = f"00-{trace_id}-{span_id}-01"
        start_ns = time.time_ns()
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
    }

    def _gen_ai_attributes(usage: dict[str, Any], finish_reason: str) -> dict[str, Any]:
        attributes: dict[str, Any] = {
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": model,
        }
        if usage.get("prompt_tokens") is not None:
            attributes["gen_ai.usage.input_tokens"] = int(usage.get("prompt_tokens") or 0)
        if usage.get("completion_tokens") is not None:
            attributes["gen_ai.usage.output_tokens"] = int(usage.get("completion_tokens") or 0)
        if finish_reason:
            attributes["gen_ai.response.finish_reasons"] = [finish_reason]
        return attributes

    def _record_span(
        sid: str, parent: str, name: str, status: str, begin_ns: int, attributes: dict[str, Any]
    ) -> None:
        if not (trace_id and span_sink is not None):
            return
        span_sink.append(
            {
                "trace_id": trace_id,
                "span_id": sid,
                "parent_span_id": parent,
                "name": name,
                "start_unix_nano": begin_ns,
                "end_unix_nano": time.time_ns(),
                "status": status,
                "attributes": attributes,
            }
        )

    def _fill_retry_sink(attempts: int, ok_ms: float, began: float) -> None:
        if retry_sink is None:
            return
        total_ms = (time.perf_counter() - began) * 1000.0
        retry_sink["attempts"] = attempts
        retry_sink["success_ms"] = ok_ms
        retry_sink["overhead_ms"] = max(0.0, total_ms - ok_ms)

    call_t0 = time.perf_counter()
    for attempt in range(1, policy.max_attempts + 1):
        attempt_span_id, attempt_parent, attempt_start_ns = span_id, "", start_ns
        attempt_name = "chat /chat/completions"
        if policy.enabled:
            attempt_parent = span_id
            attempt_name = f"{attempt_name} attempt {attempt}"
            if trace_id:
                # Each attempt is its own HTTP request, so it gets its own span
                # id and its own traceparent -- the endpoint's server-side spans
                # must hang off the attempt that reached it, not off the parent.
                attempt_span_id = new_span_id()
                headers["traceparent"] = f"00-{trace_id}-{attempt_span_id}-01"
                attempt_start_ns = time.time_ns()
        attempt_t0 = time.perf_counter()
        status_code: int | None = None
        # allow_redirects=False: a validated endpoint that 302s to a metadata/other
        # host must not carry the Bearer key there (redirect / DNS-rebind SSRF guard).
        try:
            resp = requests.post(
                f"{endpoint}/chat/completions",
                json=body,
                headers=headers,
                timeout=timeout,
                allow_redirects=False,
            )
            status_code = getattr(resp, "status_code", None)
            resp.raise_for_status()
        except Exception as exc:
            attributes = _gen_ai_attributes({}, "")
            if policy.enabled and status_code is not None:
                attributes["http.response.status_code"] = int(status_code)
            _record_span(attempt_span_id, attempt_parent, attempt_name, "ERROR", attempt_start_ns, attributes)
            # Read the status off the response where there is one: it is the same
            # information without depending on which exception raise_for_status
            # happens to throw. Only responseless failures need exception types.
            retryable = (
                _retryable_status(status_code) if status_code is not None else _retryable_error(exc, requests)
            )
            if retryable and attempt < policy.max_attempts:
                time.sleep(policy.backoff_for(attempt))
                continue
            if policy.enabled:
                summary = _retry_summary(model, attempt)
                _record_span(span_id, "", "chat /chat/completions", "ERROR", start_ns, summary)
            _fill_retry_sink(attempt, 0.0, call_t0)
            raise
        try:
            data = resp.json()
            choice = (data.get("choices") or [{}])[0]
            content = choice.get("message", {}).get("content", "")
            usage = data.get("usage", {}) or {}
            finish = str(choice.get("finish_reason") or "")
        except Exception:
            # A 200 whose body cannot be read is NOT retried: the endpoint
            # answered, it just answered nonsense, and a second identical
            # request will get the same nonsense. But the sink's contract is
            # that a call which never succeeds still fills it, so a caller can
            # read sink["attempts"] unguarded -- leaving it {} here would make
            # that a KeyError. No span is recorded, matching the pre-retry
            # behaviour of this path exactly.
            _fill_retry_sink(attempt, 0.0, call_t0)
            raise
        success_ms = (time.perf_counter() - attempt_t0) * 1000.0
        _record_span(
            attempt_span_id,
            attempt_parent,
            attempt_name,
            "OK",
            attempt_start_ns,
            _gen_ai_attributes(usage, finish),
        )
        if policy.enabled:
            # The usage attributes stay on the attempt that reported them, so a
            # backend summing gen_ai.usage.* over the trace cannot double-count.
            summary = _retry_summary(model, attempt)
            _record_span(span_id, "", "chat /chat/completions", "OK", start_ns, summary)
        _fill_retry_sink(attempt, success_ms, call_t0)
        return content, usage, finish
    raise RuntimeError("unreachable: max_attempts >= 1, so the loop returns or raises")


class EndpointJudge(JudgeModel):
    """A :class:`JudgeModel` backed by a config-connected OpenAI-compatible endpoint.

    Reuses the SSRF-guarded :func:`chat_once` transport. Uses the robust
    prompt+parse fallback (``capabilities().json_schema`` False) so it works
    against any gateway; native JSON-schema mode is a future enhancement.
    """

    def __init__(self, *, endpoint: str, model: str, api_key: str = "", max_tokens: int = 512) -> None:
        validate_endpoint(endpoint)  # SSRF guard before any Bearer key is sent
        self._endpoint = endpoint.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._max_tokens = max_tokens

    def model_id(self) -> str:
        return self._model

    def generate(self, prompt: str) -> str:
        content, _usage, _finish = chat_once(
            endpoint=self._endpoint,
            model=self._model,
            api_key=self._api_key,
            prompt=prompt,
            max_tokens=self._max_tokens,
        )
        return content
