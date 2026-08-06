import pytest

from clousight_bench.core.observation import ObservationBundle
from clousight_bench.domains.agent_runtime.adapters.base import (
    AgentRuntimeAdapter,
    CapabilityNotSupported,
)
from clousight_bench.domains.agent_runtime.adapters.base import (
    LoadResult,
    RetentionResult,
)
from clousight_bench.domains.agent_runtime.dataplane_dispatch import (
    DATA_PLANE_PACKERS,
    run_data_plane_probe,
)

PROBE_NAMES = {
    "ttft", "sustained_load", "soak", "warm_retention", "rate_limit",
    "concurrency_ceiling", "cancellation", "scaling", "hol_blocking",
    "fault_recovery", "retry_storm",
}


class _FakeAdapter(AgentRuntimeAdapter):
    """Minimal adapter exposing just the probe_X methods the packers call."""
    def create_session(self, spec=None): return "s"
    def run_tool_plan(self, session_id, plan): raise NotImplementedError
    def destroy_session(self, session_id): pass
    # supported probes
    def probe_sustained_load(self, duration_s, target_rps):
        return LoadResult(throughput_rps=10.0, p50_ms=5.0, p99_ms=9.0, jitter_ms=4.0,
                          error_rate=0.0, requests=100, duration_s=duration_s)
    def probe_warm_retention(self):
        return RetentionResult(retention_ms=1000.0, keeps_warm=True)


def test_registry_has_all_eleven_probe_names():
    assert set(DATA_PLANE_PACKERS) == PROBE_NAMES


def test_supported_probe_packs_expected_observations():
    b = run_data_plane_probe(_FakeAdapter(), "sustained_load",
                             {"duration_s": 30.0, "target_rps": 50.0})
    assert isinstance(b, ObservationBundle)
    o = b.observations
    assert o["capability"] == "supported"
    assert o["throughput_rps"] == 10.0 and o["target_rps"] == 50.0 and o["duration_s"] == 30.0
    # every documented key present
    for k in ("p50_ms", "p99_ms", "jitter_ms", "error_rate", "transport_error_rate",
              "runtime_error_rate", "tool_error_rate", "requests"):
        assert k in o


def test_unsupported_probe_returns_unsupported_bundle():
    # _FakeAdapter does not implement probe_rate_limit -> base raises CapabilityNotSupported
    b = run_data_plane_probe(_FakeAdapter(), "rate_limit", {})
    assert b.observations["capability"] == "unsupported"
    assert "reason" in b.observations


def test_unknown_probe_name_raises_value_error():
    with pytest.raises(ValueError):
        run_data_plane_probe(_FakeAdapter(), "nope", {})


def test_adapter_method_delegates_to_registry():
    b = _FakeAdapter().run_data_plane_probe("warm_retention", {})
    assert b.observations == {"capability": "supported", "retention_ms": 1000.0,
                              "keeps_warm": True}
