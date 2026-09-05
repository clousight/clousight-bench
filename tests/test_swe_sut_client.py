"""Tests for SweSutClient — the real-SUT invocation + span/usage mapping client.

All tests are offline: the transport is either a hand stub (returning a canned
OpenAI-shaped agent response) or the shared MockRuntimeTransport, whose invoke
path runs the bundled agent in-process (oracle mode needs no network).
"""

from __future__ import annotations

import hashlib
import time
from types import SimpleNamespace
from typing import Any

import pytest

from clousight_bench.core.sut_span import validate_span
from clousight_bench.domains.agent_runtime import protocol

GOLD = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"

INSTANCE = {
    "instance_id": "django__django-11099",
    "repo": "django/django",
    "base_commit": "abc123",
    "problem_statement": "UsernameValidator allows trailing newline",
    "hints_text": "regex $ matches before a trailing newline",
    "patch": GOLD,
}

ZERO_USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _oracle_span(**over: Any) -> dict[str, Any]:
    """A canned agent span in the shape _swe_span_dict / lc_agent produce."""
    span = {
        "trace_id": "a" * 48,
        "span_id": "b" * 16,
        "parent_span_id": "",
        "name": "swe-oracle",
        "kind": "CHAIN",
        "status": "ok",
        "attributes": {"openinference.span.kind": "CHAIN", "swe.instance_id": INSTANCE["instance_id"]},
    }
    span.update(over)
    return span


def _llm_span(**over: Any) -> dict[str, Any]:
    span = _oracle_span(
        name="swe-llm",
        kind="LLM",
        attributes={"openinference.span.kind": "LLM", "llm.model_name": "qwen-plus"},
    )
    span.update(over)
    return span


class _StubTransport:
    """Transport stub implementing the PUBLIC invoke_openai/last_trace_id contract."""

    def __init__(self, result: dict[str, Any], last_trace_id: str | None = None) -> None:
        self._result = result
        self.last_trace_id = last_trace_id
        self.invocations: list[tuple[str, dict[str, Any]]] = []

    def invoke_openai(self, session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.invocations.append((session_id, body))
        return protocol.encode_result(self._result)


class _StubAdapter:
    """Adapter stub mirroring the ManagedAgentRuntimeAdapter surface the client uses."""

    def __init__(self, transport: Any) -> None:
        self._t = transport
        self.provisioned: list[dict[str, Any]] = []
        self.deprovisioned: list[str] = []
        self.destroyed: list[str] = []
        self._seq = 0

    def transport(self) -> Any:
        return self._t

    def create_session(self, spec: dict[str, Any] | None = None) -> str:
        self._seq += 1
        return f"s-{self._seq}"

    def destroy_session(self, session_id: str) -> None:
        self.destroyed.append(session_id)

    def provision(self, spec: dict[str, Any] | None = None) -> Any:
        self.provisioned.append(dict(spec or {}))
        return SimpleNamespace(runtime_id="rt-1")

    def deprovision(self, runtime_id: str) -> Any:
        self.deprovisioned.append(runtime_id)
        return SimpleNamespace()


def _client(result: dict[str, Any], last_trace_id: str | None = None) -> tuple[Any, _StubAdapter]:
    from clousight_bench.suites.swe_bench.sut_client import SweSutClient

    adapter = _StubAdapter(_StubTransport(result, last_trace_id))
    return SweSutClient(adapter), adapter


# ---------------------------------------------------------------------------
# solve(): patch + spans + usage against a stubbed transport
# ---------------------------------------------------------------------------


def test_solve_returns_patch_spans_and_no_usage_for_oracle() -> None:
    client, _ = _client({"model_patch": GOLD, "usage": dict(ZERO_USAGE), "_spans": [_oracle_span()]})
    out = client.solve(INSTANCE, "oracle")
    assert out["model_patch"] == GOLD
    assert len(out["spans"]) == 1
    assert out["usage_events"] == []  # zero tokens -> no event


def test_llm_span_maps_to_llm_call_and_usage_event() -> None:
    usage = {"prompt_tokens": 120, "completion_tokens": 45, "total_tokens": 165}
    client, _ = _client({"model_patch": GOLD, "usage": usage, "_spans": [_llm_span()]})
    out = client.solve(INSTANCE, "llm")
    (span,) = out["spans"]
    assert span["attributes"]["gen_ai.operation.name"] == "chat"
    assert out["usage_events"] == [
        {
            "kind": "llm_tokens",
            "value": 165,
            "instance_id": INSTANCE["instance_id"],
            "mode": "llm",
        }
    ]


def test_chain_span_maps_to_tool_call() -> None:
    client, _ = _client({"model_patch": GOLD, "usage": dict(ZERO_USAGE), "_spans": [_oracle_span()]})
    (span,) = client.solve(INSTANCE, "oracle")["spans"]
    assert span["attributes"]["gen_ai.operation.name"] == "execute_tool"


def test_every_mapped_span_passes_validate_span() -> None:
    spans = [_oracle_span(), _llm_span(span_id="c" * 16)]
    client, _ = _client({"model_patch": GOLD, "usage": dict(ZERO_USAGE), "_spans": spans})
    out = client.solve(INSTANCE, "oracle")
    assert len(out["spans"]) == 2
    for span in out["spans"]:
        validate_span(span)  # must not raise


def test_span_timestamps_are_invoke_wall_clock_bounds() -> None:
    client, _ = _client({"model_patch": GOLD, "usage": dict(ZERO_USAGE), "_spans": [_oracle_span()]})
    before = time.time()
    (span,) = client.solve(INSTANCE, "oracle")["spans"]
    after = time.time()
    assert int(before * 1e9) <= span["start_unix_nano"] <= span["end_unix_nano"] <= int(after * 1e9)


def test_trace_id_from_span_wins() -> None:
    client, _ = _client(
        {"model_patch": GOLD, "usage": dict(ZERO_USAGE), "_spans": [_oracle_span()]},
        last_trace_id="live-trace",
    )
    (span,) = client.solve(INSTANCE, "oracle")["spans"]
    assert span["trace_id"] == hashlib.sha256(("a" * 48).encode()).hexdigest()[:32]


def test_trace_id_falls_back_to_transport_last_trace() -> None:
    client, _ = _client(
        {"model_patch": GOLD, "usage": dict(ZERO_USAGE), "_spans": [_oracle_span(trace_id="")]},
        last_trace_id="live-trace",
    )
    (span,) = client.solve(INSTANCE, "oracle")["spans"]
    assert span["trace_id"] == hashlib.sha256(b"live-trace").hexdigest()[:32]


def test_trace_id_falls_back_to_instance_id() -> None:
    client, _ = _client(
        {"model_patch": GOLD, "usage": dict(ZERO_USAGE), "_spans": [_oracle_span(trace_id="")]}
    )
    (span,) = client.solve(INSTANCE, "oracle")["spans"]
    assert span["trace_id"] == hashlib.sha256(f"trace-{INSTANCE['instance_id']}".encode()).hexdigest()[:32]


def test_error_span_maps_status_and_error() -> None:
    err_span = _llm_span(status="error", error="DASHSCOPE_API_KEY not set")
    client, _ = _client({"model_patch": "", "usage": dict(ZERO_USAGE), "_spans": [err_span]})
    (span,) = client.solve(INSTANCE, "llm")["spans"]
    assert span["status"] == "ERROR"
    assert span["attributes"]["error.message"] == "DASHSCOPE_API_KEY not set"
    validate_span(span)


def test_missing_status_defaults_to_ok() -> None:
    span_in = _oracle_span()
    del span_in["status"]
    client, _ = _client({"model_patch": GOLD, "usage": dict(ZERO_USAGE), "_spans": [span_in]})
    (span,) = client.solve(INSTANCE, "oracle")["spans"]
    assert span["status"] == "OK"


def test_parent_span_id_maps_to_parent_id() -> None:
    spans = [_oracle_span(), _llm_span(span_id="c" * 16, parent_span_id="b" * 16)]
    client, _ = _client({"model_patch": GOLD, "usage": dict(ZERO_USAGE), "_spans": spans})
    root, child = client.solve(INSTANCE, "oracle")["spans"]
    assert root["parent_span_id"] == ""
    assert child["parent_span_id"] == "b" * 16


def test_attr_string_values_truncated_to_8kb() -> None:
    big = "x" * 20000
    span_in = _oracle_span(attributes={"openinference.span.kind": "CHAIN", "blob": big})
    client, _ = _client({"model_patch": GOLD, "usage": dict(ZERO_USAGE), "_spans": [span_in]})
    (span,) = client.solve(INSTANCE, "oracle")["spans"]
    assert len(span["attributes"]["blob"].encode("utf-8")) <= 8192
    assert span["attributes"]["blob"] == "x" * 8192
    validate_span(span)


def test_invalid_span_raises_loudly() -> None:
    """A span that cannot be made valid must raise, never be silently dropped.

    v3 mapping normalizes ids/status/kind, so the remaining hard limit is the
    total-attributes byte cap: many near-8KiB values still blow the 64KiB cap.
    """
    bomb = {f"blob{i}": "x" * 8000 for i in range(10)}  # ~80KiB after truncation
    client, _ = _client(
        {"model_patch": GOLD, "usage": dict(ZERO_USAGE), "_spans": [_oracle_span(attributes=bomb)]}
    )
    with pytest.raises(ValueError, match="MAX_ATTRS_BYTES"):
        client.solve(INSTANCE, "oracle")


# ---------------------------------------------------------------------------
# request encoding: the instance row goes straight through encode_swe_invoke
# ---------------------------------------------------------------------------


def test_oracle_request_carries_gold_patch() -> None:
    client, adapter = _client({"model_patch": GOLD, "usage": dict(ZERO_USAGE), "_spans": []})
    client.solve(INSTANCE, "oracle")
    ((_, body),) = adapter.transport().invocations
    decoded = protocol.decode_request(body)
    assert decoded["swe"]["gold_patch"] == GOLD
    assert decoded["swe"]["agent_mode"] == "oracle"


def test_llm_request_never_carries_gold_patch() -> None:
    client, adapter = _client({"model_patch": "", "usage": dict(ZERO_USAGE), "_spans": []})
    client.solve(INSTANCE, "llm")
    ((_, body),) = adapter.transport().invocations
    decoded = protocol.decode_request(body)
    assert "gold_patch" not in decoded["swe"]
    assert decoded["swe"]["agent_mode"] == "llm"


def test_session_created_per_instance_and_destroyed() -> None:
    client, adapter = _client({"model_patch": GOLD, "usage": dict(ZERO_USAGE), "_spans": []})
    client.solve(INSTANCE, "oracle")
    client.solve(INSTANCE, "oracle")
    transport = adapter.transport()
    assert [sid for sid, _ in transport.invocations] == ["s-1", "s-2"]
    assert adapter.destroyed == ["s-1", "s-2"]


# ---------------------------------------------------------------------------
# provision / close lifecycle
# ---------------------------------------------------------------------------


def test_provision_and_close_delegate_to_adapter() -> None:
    client, adapter = _client({"model_patch": GOLD, "usage": dict(ZERO_USAGE), "_spans": []})
    client.provision()
    assert len(adapter.provisioned) == 1
    client.close()
    assert adapter.deprovisioned == ["rt-1"]
    client.close()  # second close is a no-op, never a double deprovision
    assert adapter.deprovisioned == ["rt-1"]


def test_provision_llm_mode_forwards_driver_dashscope_key(monkeypatch) -> None:
    """llm mode + DASHSCOPE_API_KEY on the DRIVER → the key rides ONLY in the
    provision spec's environment_variables (to the CreateAgentRuntime API)."""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-driver-key")
    client, adapter = _client({"model_patch": GOLD, "usage": dict(ZERO_USAGE), "_spans": []})
    client.provision(agent_mode="llm")
    assert adapter.provisioned == [{"environment_variables": {"DASHSCOPE_API_KEY": "sk-driver-key"}}]


def test_provision_oracle_mode_never_forwards_key(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-driver-key")
    client, adapter = _client({"model_patch": GOLD, "usage": dict(ZERO_USAGE), "_spans": []})
    client.provision(agent_mode="oracle")
    assert adapter.provisioned == [{}]


def test_provision_llm_mode_without_key_sends_empty_spec(monkeypatch) -> None:
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    client, adapter = _client({"model_patch": GOLD, "usage": dict(ZERO_USAGE), "_spans": []})
    client.provision(agent_mode="llm")
    assert adapter.provisioned == [{}]


def test_close_without_provision_is_noop() -> None:
    client, adapter = _client({"model_patch": GOLD, "usage": dict(ZERO_USAGE), "_spans": []})
    client.close()
    assert adapter.deprovisioned == []


def test_close_swallows_deprovision_failure() -> None:
    client, adapter = _client({"model_patch": GOLD, "usage": dict(ZERO_USAGE), "_spans": []})
    client.provision()

    def boom(runtime_id: str) -> Any:
        raise RuntimeError("cloud hiccup")

    adapter.deprovision = boom  # type: ignore[method-assign]
    client.close()  # must not raise (best-effort by contract)


# ---------------------------------------------------------------------------
# MockRuntimeTransport end-to-end: the whole path is testable offline
# ---------------------------------------------------------------------------


def test_real_transport_without_invoke_seam_refuses_in_process_fallback() -> None:
    """A non-mock transport that never wired invoke_openai must fail loudly
    (CapabilityNotSupported from the RuntimeTransport base) — a misconfigured
    stack must never silently fake a cloud run in-process."""
    from clousight_bench.domains.agent_runtime.adapters.base import CapabilityNotSupported
    from clousight_bench.domains.agent_runtime.adapters.transport import RuntimeTransport
    from clousight_bench.suites.swe_bench.sut_client import SweSutClient

    class _SeamlessTransport(RuntimeTransport):
        """Real-shaped transport with no invoke_openai override."""

        def create_session(self, spec=None):
            return "s-1"

        def run_tool_plan(self, session_id, plan):
            raise NotImplementedError

        def destroy_session(self, session_id):
            return None

    client = SweSutClient(_StubAdapter(_SeamlessTransport()))
    with pytest.raises(CapabilityNotSupported, match="invoke_openai"):
        client.solve(INSTANCE, "oracle")


def test_mock_runtime_transport_end_to_end_oracle() -> None:
    """LocalSimAdapter (MockRuntimeTransport, explicit isinstance opt-in) runs the
    bundled agent in-process — oracle mode round-trips the gold patch offline."""
    from clousight_bench.domains.agent_runtime.adapters.local_sim import LocalSimAdapter
    from clousight_bench.suites.swe_bench.sut_client import SweSutClient

    client = SweSutClient(LocalSimAdapter({}))
    out = client.solve(INSTANCE, "oracle")
    assert out["model_patch"] == GOLD
    (span,) = out["spans"]
    validate_span(span)
    assert span["attributes"]["gen_ai.operation.name"] == "execute_tool"  # oracle emits a CHAIN span
    assert span["status"] == "OK"
    assert out["usage_events"] == []


def test_out_of_vocab_status_raises_loudly() -> None:
    """Status normalization must never coerce an unknown status to OK."""
    client, _ = _client(
        {"model_patch": GOLD, "usage": dict(ZERO_USAGE), "_spans": [_oracle_span(status="weird")]}
    )
    with pytest.raises(ValueError, match="out-of-vocab status"):
        client.solve(INSTANCE, "oracle")


def test_uppercase_error_status_maps_to_error() -> None:
    client, _ = _client(
        {
            "model_patch": GOLD,
            "usage": dict(ZERO_USAGE),
            "_spans": [_oracle_span(status="ERROR", error="x")],
        }
    )
    out = client.solve(INSTANCE, "oracle")
    assert out["spans"][0]["status"] == "ERROR"
