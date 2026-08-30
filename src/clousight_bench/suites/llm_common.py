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

import ipaddress
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from clousight_bench.core.canonical import sha256_bytes  # re-exported for suites
from clousight_bench.core.judge import JudgeModel
from clousight_bench.core.observation import ItemResult, ItemScore, Measurement
from clousight_bench.core.suite import RawArtifacts
from clousight_bench.enrichers.pricing import tokens_1k_price

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
]


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
    """Turn per-answer artifact rows into per-item :class:`ItemResult`s (R1).

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
    tmp_dir: Path, rows: list[dict[str, Any]], summary: dict[str, Any], *, rows_key: str
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
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
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


def chat_once(
    *, endpoint: str, model: str, api_key: str, prompt: str, max_tokens: int, timeout: float = 120.0
) -> tuple[str, dict[str, Any], str]:
    """One OpenAI-compatible ``/chat/completions`` call at ``temperature=0``.

    Returns ``(content, usage, finish_reason)``. Lazily imports ``requests`` so
    the offline paths never need it.
    """
    import requests  # noqa: PLC0415 - lazy; only the real path needs it

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    # allow_redirects=False: a validated endpoint that 302s to a metadata/other
    # host must not carry the Bearer key there (redirect / DNS-rebind SSRF guard).
    resp = requests.post(
        f"{endpoint}/chat/completions",
        json=body,
        headers=headers,
        timeout=timeout,
        allow_redirects=False,
    )
    resp.raise_for_status()
    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    content = choice.get("message", {}).get("content", "")
    usage = data.get("usage", {}) or {}
    return content, usage, str(choice.get("finish_reason") or "")


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
