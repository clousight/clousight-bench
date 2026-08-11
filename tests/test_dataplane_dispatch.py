import pytest

from clousight_bench.core.observation import ObservationBundle
from clousight_bench.domains.agent_runtime.adapters.base import (
    AgentRuntimeAdapter,
    CapabilityNotSupported,
    FaultRecoveryResult,
    HOLResult,
    LoadResult,
    RetentionResult,
    RetryStormResult,
)
from clousight_bench.domains.agent_runtime.dataplane_dispatch import (
    DATA_PLANE_PACKERS,
    PROBE_NAMES,
    run_data_plane_probe,
)


class _FakeAdapter(AgentRuntimeAdapter):
    """Minimal adapter exposing just the probe_X methods the packers call."""

    def create_session(self, spec=None):
        return "s"

    def run_tool_plan(self, session_id, plan):
        raise NotImplementedError

    def destroy_session(self, session_id):
        pass

    # supported probes
    def probe_sustained_load(self, duration_s, target_rps):
        return LoadResult(
            throughput_rps=10.0,
            p50_ms=5.0,
            p99_ms=9.0,
            jitter_ms=4.0,
            error_rate=0.0,
            requests=100,
            duration_s=duration_s,
        )

    def probe_warm_retention(self):
        return RetentionResult(retention_ms=1000.0, keeps_warm=True)

    # re-raise probes: three probes that do NOT catch CapabilityNotSupported
    def probe_fault_recovery(self) -> FaultRecoveryResult:
        return FaultRecoveryResult(
            recovered=True,
            observed_attempts=3,
            recovery_ms=42.0,
            platform_terminated=False,
        )

    def probe_retry_storm(self, max_window_s: float = 30.0) -> RetryStormResult:
        return RetryStormResult(
            capability="supported",
            total_attempts=3,
            storm_bounded_by="agent",
            duration_ms=42.0,
        )

    def probe_hol_blocking(self) -> HOLResult:
        return HOLResult(
            blocked=False,
            fast_p50_ms=5.0,
            slow_p50_ms=200.0,
            hol_ratio=0.025,
        )


class _IncapableAdapter(_FakeAdapter):
    """Adapter whose re-raise probes raise CapabilityNotSupported."""

    def probe_fault_recovery(self) -> FaultRecoveryResult:  # type: ignore[override]
        raise CapabilityNotSupported("probe_fault_recovery")

    def probe_retry_storm(self, max_window_s: float = 30.0) -> RetryStormResult:  # type: ignore[override]
        raise CapabilityNotSupported("probe_retry_storm")

    def probe_hol_blocking(self) -> HOLResult:  # type: ignore[override]
        raise CapabilityNotSupported("probe_hol_blocking")


def test_registry_has_all_eleven_probe_names():
    assert set(DATA_PLANE_PACKERS) == PROBE_NAMES
    assert len(PROBE_NAMES) == 11


def test_supported_probe_packs_expected_observations():
    b = run_data_plane_probe(_FakeAdapter(), "sustained_load", {"duration_s": 30.0, "target_rps": 50.0})
    assert isinstance(b, ObservationBundle)
    o = b.observations
    assert o["capability"] == "supported"
    assert o["throughput_rps"] == 10.0 and o["target_rps"] == 50.0 and o["duration_s"] == 30.0
    # every documented key present
    for k in (
        "p50_ms",
        "p99_ms",
        "jitter_ms",
        "error_rate",
        "transport_error_rate",
        "runtime_error_rate",
        "tool_error_rate",
        "requests",
    ):
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
    assert b.observations == {"capability": "supported", "retention_ms": 1000.0, "keeps_warm": True}


# ---------------------------------------------------------------------------
# Happy-path tests for the three re-raise packers (fault_recovery, retry_storm,
# hol_blocking) — these packers do NOT catch CapabilityNotSupported and have
# NO "capability" key in their output.
# ---------------------------------------------------------------------------


def test_fault_recovery_happy_path_new_shape():
    b = run_data_plane_probe(_FakeAdapter(), "fault_recovery", {})
    o = b.observations
    assert o["capability"] == "supported"
    assert isinstance(o["recovered"], bool)
    assert isinstance(o["observed_attempts"], int)
    assert isinstance(o["recovery_ms"], float)
    assert isinstance(o["platform_terminated"], bool)


def test_retry_storm_happy_path_new_shape():
    b = run_data_plane_probe(_FakeAdapter(), "retry_storm", {"max_window_s": 30.0})
    o = b.observations
    assert o["capability"] == "supported"
    assert isinstance(o["total_attempts"], int)
    assert isinstance(o["storm_bounded_by"], str)
    assert isinstance(o["duration_ms"], float)


def test_hol_blocking_happy_path_no_capability_key():
    b = run_data_plane_probe(_FakeAdapter(), "hol_blocking", {})
    o = b.observations
    assert "capability" not in o, "hol_blocking must NOT include a 'capability' key"
    assert isinstance(o["blocked"], bool)
    assert isinstance(o["fast_p50_ms"], float)
    assert isinstance(o["slow_p50_ms"], float)
    assert isinstance(o["hol_ratio"], float)


# ---------------------------------------------------------------------------
# Re-raise tests: these packers must propagate CapabilityNotSupported rather
# than swallowing it.
# ---------------------------------------------------------------------------


def test_fault_recovery_reraises_capability_not_supported():
    with pytest.raises(CapabilityNotSupported):
        run_data_plane_probe(_IncapableAdapter(), "fault_recovery", {})


def test_retry_storm_reraises_capability_not_supported():
    with pytest.raises(CapabilityNotSupported):
        run_data_plane_probe(_IncapableAdapter(), "retry_storm", {})


def test_hol_blocking_reraises_capability_not_supported():
    with pytest.raises(CapabilityNotSupported):
        run_data_plane_probe(_IncapableAdapter(), "hol_blocking", {})


def test_managed_adapter_delegates_to_transport_when_present(monkeypatch):
    from clousight_bench.domains.agent_runtime.adapters.local_sim import LocalSimAdapter

    a = LocalSimAdapter(target={})
    # MockRuntimeTransport has no run_data_plane_probe -> base packer path is used.
    b = a.run_data_plane_probe("warm_retention", {})
    assert b.observations["capability"] == "supported"  # local-sim keeps working

    # If the transport DOES define run_data_plane_probe, it wins.
    sentinel = ObservationBundle(observations={"capability": "supported", "via": "transport"})
    t = a._transport_()
    monkeypatch.setattr(t, "run_data_plane_probe", lambda name, params: sentinel, raising=False)
    assert a.run_data_plane_probe("warm_retention", {}).observations.get("via") == "transport"
