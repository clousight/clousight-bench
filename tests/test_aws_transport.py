"""Account-free tests for AwsAgentCoreTransport.

All boto3 clients are injected as scripted fakes; no real AWS calls occur.
Mirrors the pattern in test_aliyun_run_data_plane_probe.py and
test_dataplane_invoke.py.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

# The transport's invoke path lazy-imports requests (the aws extra). The
# no-validate CI job runs on a bare install without it, so skip this module there.
pytest.importorskip("requests")

from clousight_bench.core.observation import ObservationBundle
from clousight_bench.domains.agent_runtime.aws.transport import (
    _SESSION_HEADER,
    AwsAgentCoreTransport,
    _AwsMemory,
)

# --------------------------------------------------------------------------- #
# Helpers: fake mini HTTP server (mimics the AgentCore invoke endpoint)
# --------------------------------------------------------------------------- #


class _FakeAgent(BaseHTTPRequestHandler):
    """A minimal OpenAI-shaped HTTP server that always returns ok=True."""

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        out = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({"ok": True, "status": 200}),
                        }
                    }
                ]
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *_: Any) -> None:
        pass


def _serve() -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeAgent)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


# --------------------------------------------------------------------------- #
# Fake boto3 control + data clients
# --------------------------------------------------------------------------- #


class _FakeControl:
    """Scripted boto3 bedrock-agentcore-control client for happy-path tests."""

    def __init__(self, invoke_url: str = "http://127.0.0.1:9999") -> None:
        self._invoke_url = invoke_url
        self.created: list[dict] = []
        self.deleted: list[str] = []
        self._deleted_ids: set[str] = set()

    def create_agent_runtime(self, **kwargs: Any) -> dict[str, Any]:
        self.created.append(kwargs)
        return {"agentRuntimeId": "rt-fake-001"}

    def get_agent_runtime(self, **kwargs: Any) -> dict[str, Any]:
        rid = kwargs.get("agentRuntimeId", "rt-fake-001")
        if rid in self._deleted_ids:
            # Simulate ResourceNotFoundException after deletion → _residual_after_delete returns []
            raise Exception(f"ResourceNotFoundException: {rid} not found")
        return {"status": "READY", "agentRuntimeId": rid}

    def create_agent_runtime_endpoint(self, **kwargs: Any) -> dict[str, Any]:
        return {}

    def list_agent_runtime_endpoints(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "agentRuntimeEndpoints": [
                {
                    "agentRuntimeEndpointName": "Default",
                    "status": "ACTIVE",
                    "invokeUrl": self._invoke_url,
                }
            ]
        }

    def delete_agent_runtime_endpoint(self, **kwargs: Any) -> dict[str, Any]:
        return {}

    def delete_agent_runtime(self, **kwargs: Any) -> dict[str, Any]:
        rid = kwargs.get("agentRuntimeId", "")
        self.deleted.append(rid)
        self._deleted_ids.add(rid)
        return {}


class _FakeData:
    """Scripted bedrock-agentcore data client (not used directly — transport uses HTTP)."""


# --------------------------------------------------------------------------- #
# Adapter stub (mirrors _FakeAdapter in test_dataplane_invoke.py)
# --------------------------------------------------------------------------- #


class _FakeAdapter:
    def __init__(self, mock_base_url: str = "", endpoint_url: str = "") -> None:
        self.mock_base_url = mock_base_url
        self.target: dict[str, Any] = {
            "mock_token": "",
            "region": "us-east-1",
            "endpoint_url": endpoint_url,
            "s3_bucket": "test-bucket",
        }
        self.run_id: str | None = None


# --------------------------------------------------------------------------- #
# Factory: transport with injected fakes, endpoint pre-set
# --------------------------------------------------------------------------- #


def _transport(
    endpoint: str = "",
    mock_base_url: str = "",
    control: Any = None,
    data: Any = None,
    endpoint_url: str = "",
) -> AwsAgentCoreTransport:
    adapter = _FakeAdapter(mock_base_url=mock_base_url, endpoint_url=endpoint_url)
    t = AwsAgentCoreTransport(adapter, control=control, data=data)
    if endpoint:
        t._endpoint_url = endpoint
    return t


# --------------------------------------------------------------------------- #
# Tests: provision happy path
# --------------------------------------------------------------------------- #


def test_provision_returns_runtime_id_and_url():
    """provision() returns a ProvisionResult with the fake runtime_id and endpoint URL."""
    srv, base = _serve()
    try:
        ctrl = _FakeControl(invoke_url=base)
        t = _transport(control=ctrl, data=_FakeData())
        result = t.provision({})
    finally:
        srv.shutdown()

    assert result.runtime_id == "rt-fake-001"
    assert result.ready is True
    assert result.ready_latency_ms >= 0
    # Endpoint URL was captured from the poll loop.
    assert t._endpoint_url == base


def test_provision_stores_runtime_id():
    srv, base = _serve()
    try:
        ctrl = _FakeControl(invoke_url=base)
        t = _transport(control=ctrl, data=_FakeData())
        t.provision({})
    finally:
        srv.shutdown()
    assert t._runtime_id == "rt-fake-001"


def test_deprovision_clean_when_runtime_gone():
    """deprovision() returns clean=True when the runtime raises NotFound (already gone)."""
    srv, base = _serve()
    try:
        ctrl = _FakeControl(invoke_url=base)
        t = _transport(control=ctrl, data=_FakeData())
        t.provision({})
        result = t.deprovision("rt-fake-001")
    finally:
        srv.shutdown()
    assert result.clean is True
    assert result.residual == []


# --------------------------------------------------------------------------- #
# Tests: create_session shape
# --------------------------------------------------------------------------- #


def test_create_session_returns_uuid_string():
    t = _transport()
    sid = t.create_session()
    assert isinstance(sid, str)
    assert sid.startswith("sess-")
    assert len(sid) > 10


def test_create_session_unique():
    t = _transport()
    ids = {t.create_session() for _ in range(20)}
    assert len(ids) == 20


def test_destroy_session_removes_tracking():
    t = _transport()
    sid = t.create_session()
    assert sid in t._session_ids
    t.destroy_session(sid)
    assert sid not in t._session_ids


def test_session_cold_start_is_provision_flag():
    assert AwsAgentCoreTransport.session_cold_start_is_provision is True


# --------------------------------------------------------------------------- #
# Tests: run_tool_plan via fake HTTP server
# --------------------------------------------------------------------------- #


def test_run_tool_plan_happy_path():
    """run_tool_plan against a fake HTTP agent returns a completed InvocationTrace."""
    from clousight_bench.domains.agent_runtime.adapters.base import ToolCall

    srv, base = _serve()
    try:
        t = _transport(endpoint=base, mock_base_url=base)
        sid = t.create_session()
        plan = [ToolCall(target="prices", params={"provider": "aws"})]
        trace = t.run_tool_plan(sid, plan)
    finally:
        srv.shutdown()

    assert trace.completed is True
    assert trace.final_state == "completed"
    assert len(trace.attempts) == 1
    assert trace.attempts[0].ok is True


def test_run_tool_plan_sends_session_header():
    """Verify the AWS session-id header is sent to the endpoint."""
    received_headers: list[dict] = []

    class _CapturingAgent(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            received_headers.append(dict(self.headers))
            n = int(self.headers.get("Content-Length", 0))
            self.rfile.read(n)
            out = json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": json.dumps({"ok": True, "status": 200}),
                            }
                        }
                    ]
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *_: Any) -> None:
            pass

    srv2 = ThreadingHTTPServer(("127.0.0.1", 0), _CapturingAgent)
    threading.Thread(target=srv2.serve_forever, daemon=True).start()
    base2 = f"http://127.0.0.1:{srv2.server_address[1]}"

    from clousight_bench.domains.agent_runtime.adapters.base import ToolCall

    try:
        t = _transport(endpoint=base2, mock_base_url=base2)
        sid = t.create_session()
        plan = [ToolCall(target="prices")]
        t.run_tool_plan(sid, plan)
    finally:
        srv2.shutdown()

    assert len(received_headers) >= 1
    # Header name is case-insensitive in HTTP; BaseHTTPRequestHandler lowercases keys.
    header_lower = {k.lower(): v for k, v in received_headers[0].items()}
    assert header_lower.get(_SESSION_HEADER.lower()) == sid


# --------------------------------------------------------------------------- #
# Tests: run_data_plane_probe in-process dispatch
# --------------------------------------------------------------------------- #


def test_run_data_plane_probe_soak_in_process():
    """soak probe runs in-process against a fake agent; returns a supported bundle."""
    srv, base = _serve()
    try:
        t = _transport(endpoint=base, mock_base_url=base)
        bundle = t.run_data_plane_probe("soak", {"duration_s": 0.3})
    finally:
        srv.shutdown()

    assert isinstance(bundle, ObservationBundle)
    assert bundle.observations["capability"] == "supported"
    assert bundle.observations["availability"] == 1.0


def test_run_data_plane_probe_ttft_returns_series():
    """ttft probe returns 5 samples + series."""
    srv, base = _serve()
    try:
        t = _transport(endpoint=base, mock_base_url=base)
        bundle = t.run_data_plane_probe("ttft", {})
    finally:
        srv.shutdown()

    assert bundle.observations["capability"] == "supported"
    assert len(bundle.observations["ttft_ms"]) == 5
    assert "ttft_ms" in bundle.series


def test_run_data_plane_probe_attaches_vantage():
    """Vantage metadata is attached with carrier=local for in-process dispatch."""
    srv, base = _serve()
    try:
        t = _transport(endpoint=base, mock_base_url=base)
        bundle = t.run_data_plane_probe("soak", {"duration_s": 0.2})
    finally:
        srv.shutdown()

    vantage = bundle.observations["vantage"]
    assert vantage["carrier"] == "local"
    assert vantage["region"] == "us-east-1"
    assert vantage["in_vpc"] is False


def test_run_data_plane_probe_vantage_region_from_target():
    """Vantage region reflects the adapter's region target key."""
    srv, base = _serve()
    try:
        adapter = _FakeAdapter(mock_base_url=base)
        adapter.target["region"] = "eu-west-1"
        t = AwsAgentCoreTransport(adapter)
        t._endpoint_url = base
        bundle = t.run_data_plane_probe("soak", {"duration_s": 0.15})
    finally:
        srv.shutdown()

    assert bundle.observations["vantage"]["region"] == "eu-west-1"


# --------------------------------------------------------------------------- #
# Tests: _AwsMemory round-trip via InMemoryOssClient-compatible fake S3Client
# --------------------------------------------------------------------------- #


class _FakeS3Client:
    """Fake S3 client backed by InMemoryOssClient for _AwsMemory tests."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    def put_object(self, key: str, data: bytes) -> None:
        self._store[key] = bytes(data)

    def get_object(self, key: str) -> bytes:
        if key not in self._store:
            raise KeyError(key)
        return self._store[key]

    def list_prefix(self, prefix: str) -> list[str]:
        return sorted(k for k in self._store if k.startswith(prefix))

    def delete_object(self, key: str) -> None:
        self._store.pop(key, None)


def test_aws_memory_round_trip():
    """_AwsMemory store → fetch round-trip with injected fake S3 client."""
    fake_s3 = _FakeS3Client()
    mem = _AwsMemory("test-bucket", "us-east-1", run_id="run-abc", s3_client=fake_s3)

    state = {"counter": 42, "values": [1, 2, 3]}
    mem.store("session-001", state)
    recovered = mem.fetch("session-001")

    assert recovered == state


def test_aws_memory_isolates_sessions():
    """Different sessions store to different keys and cannot cross-read."""
    fake_s3 = _FakeS3Client()
    mem = _AwsMemory("test-bucket", "us-east-1", run_id="run-x", s3_client=fake_s3)

    mem.store("sess-a", {"who": "a"})
    mem.store("sess-b", {"who": "b"})

    assert mem.fetch("sess-a") == {"who": "a"}
    assert mem.fetch("sess-b") == {"who": "b"}


def test_aws_memory_missing_key_raises():
    """fetch() raises KeyError for a key that was never stored."""
    fake_s3 = _FakeS3Client()
    mem = _AwsMemory("test-bucket", "us-east-1", run_id="run-y", s3_client=fake_s3)

    with pytest.raises(KeyError):
        mem.fetch("non-existent-session")


def test_aws_memory_cleanup_removes_keys():
    """cleanup() deletes all stored state keys."""
    fake_s3 = _FakeS3Client()
    mem = _AwsMemory("test-bucket", "us-east-1", run_id="run-z", s3_client=fake_s3)

    mem.store("s1", {"x": 1})
    mem.store("s2", {"x": 2})
    assert len(fake_s3._store) == 2

    mem.cleanup()
    assert len(fake_s3._store) == 0
    assert mem._keys == []


def test_aws_memory_key_path_includes_run_id():
    """S3 key includes run_id for namespacing."""
    fake_s3 = _FakeS3Client()
    mem = _AwsMemory("bucket", "us-east-1", run_id="myrun", s3_client=fake_s3)
    mem.store("mysession", {"v": 1})

    keys = list(fake_s3._store.keys())
    assert len(keys) == 1
    assert "myrun" in keys[0]
    assert "mysession" in keys[0]


# --------------------------------------------------------------------------- #
# Tests: persist_state / load_state via transport (T1.2)
# --------------------------------------------------------------------------- #


def test_transport_persist_and_load_state():
    """persist_state / load_state wires through _AwsMemory."""
    fake_s3 = _FakeS3Client()
    t = _transport()
    t._memory = _AwsMemory("b", "us-east-1", run_id="r", s3_client=fake_s3)

    sid = t.create_session()
    t.persist_state(sid, {"key": "value", "num": 99})
    recovered = t.load_state(sid)
    assert recovered == {"key": "value", "num": 99}


def test_resume_session_returns_same_id():
    t = _transport()
    sid = t.create_session()
    assert t.resume_session(sid) == sid


# --------------------------------------------------------------------------- #
# Tests: capabilities that raise CapabilityNotSupported
# --------------------------------------------------------------------------- #


def test_register_tool_raises_capability_not_supported():
    from clousight_bench.domains.agent_runtime.adapters.base import CapabilityNotSupported

    t = _transport()
    with pytest.raises(CapabilityNotSupported):
        t.register_tool("mcp", {})


def test_get_trace_raises_capability_not_supported():
    from clousight_bench.domains.agent_runtime.adapters.base import CapabilityNotSupported

    t = _transport()
    with pytest.raises(CapabilityNotSupported):
        t.get_trace("session-x")


def test_export_otel_raises_capability_not_supported():
    from clousight_bench.domains.agent_runtime.adapters.base import CapabilityNotSupported

    t = _transport()
    with pytest.raises(CapabilityNotSupported):
        t.export_otel("session-x")


def test_probe_signals_raises_capability_not_supported():
    from clousight_bench.domains.agent_runtime.adapters.base import CapabilityNotSupported

    t = _transport()
    with pytest.raises(CapabilityNotSupported):
        t.probe_signals()


# --------------------------------------------------------------------------- #
# Tests: probe_idle_cost (platform assertion, always works)
# --------------------------------------------------------------------------- #


def test_probe_idle_cost_scales_to_zero():
    t = _transport()
    result = t.probe_idle_cost()
    assert result.scales_to_zero is True
    assert result.idle_cost_per_hour == 0.0


# --------------------------------------------------------------------------- #
# Tests: probe dispatch via injected fake _PROBE_FNS
# --------------------------------------------------------------------------- #


@pytest.mark.slow  # ~17 min: exercises every probe name incl. real-sleep warm-retention/idle sweeps
def test_run_data_plane_probe_all_names_accepted():
    """Every canonical probe name is accepted by run_data_plane_probe (in-process path)."""
    from clousight_bench.domains.agent_runtime.dataplane_dispatch import PROBE_NAMES

    srv, base = _serve()
    try:
        t = _transport(endpoint=base, mock_base_url=base)
        for name in sorted(PROBE_NAMES):
            params: dict = {}
            if name in ("soak", "sustained_load"):
                params = {"duration_s": 0.1}
            if name in ("sustained_load",):
                params = {"duration_s": 0.1, "target_rps": 2.0}
            if name == "scaling":
                params = {"levels": [1]}
            # Probes that don't catch CapabilityNotSupported are expected to pass
            # when the mock server is up; just verify no ValueError is raised.
            try:
                bundle = t.run_data_plane_probe(name, params)
                assert isinstance(bundle, ObservationBundle), f"{name}: expected ObservationBundle"
            except Exception as exc:
                # Allowed: CapabilityNotSupported from probes that re-raise it,
                # or RuntimeError from warm_retention's sleep-based probing.
                # Not allowed: ValueError (unknown probe name).
                assert not isinstance(exc, ValueError), f"{name}: unexpected ValueError: {exc}"
    finally:
        srv.shutdown()


# --------------------------------------------------------------------------- #
# Tests: session header constant
# --------------------------------------------------------------------------- #


def test_session_header_value():
    assert _SESSION_HEADER == "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"
