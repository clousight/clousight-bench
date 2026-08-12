"""T1.6 / T1.7 / T1.8 reliability dimensions against local-sim.

Each dimension is exercised through deterministic local-sim knobs so both a
healthy and a degraded runtime score, with no cloud account.
"""

from clousight_bench.core.orchestrator import execute
from clousight_bench.core.schema import RunSpec


def test_t1_6_soak_reports_availability(tmp_path):
    spec = RunSpec(
        "agent-runtime", "T1.6", "local-sim", target={"soak": {"availability": 0.9995, "error_rate": 0.0005}}
    )
    rec = execute(spec, results_dir=tmp_path)
    assert rec.status == "completed"
    m = rec.measurements
    assert m["soak_capability"]["value"] == "supported"
    assert m["availability"]["value"] == 0.9995
    assert m["soak_error_rate"]["value"] == 0.0005
    assert m["soak_requests"]["value"] > 0


def test_t1_6_low_availability_is_flagged(tmp_path):
    spec = RunSpec(
        "agent-runtime", "T1.6", "local-sim", target={"soak": {"error_rate": 0.05}}
    )  # availability -> 0.95
    rec = execute(spec, results_dir=tmp_path)
    assert rec.measurements["availability"]["value"] == 0.95
    assert any(f["code"] == "agent_runtime.availability_below_sla" for f in rec.findings)


def test_t1_7_rate_limit_reports_onset_and_429(tmp_path):
    spec = RunSpec(
        "agent-runtime",
        "T1.7",
        "local-sim",
        target={"rate_limit": {"onset_rps": 100, "retry_after_ms": 200, "honors_429": True}},
    )
    rec = execute(spec, results_dir=tmp_path)
    assert rec.status == "completed"
    assert rec.measurements["throttle_onset_rps"]["value"] == 100
    assert rec.measurements["honors_429"]["value"] is True


def test_t1_7_throttle_without_429_is_flagged(tmp_path):
    spec = RunSpec(
        "agent-runtime", "T1.7", "local-sim", target={"rate_limit": {"onset_rps": 50, "honors_429": False}}
    )
    rec = execute(spec, results_dir=tmp_path)
    assert rec.measurements["honors_429"]["value"] is False
    assert any(f["code"] == "agent_runtime.throttle_without_429" for f in rec.findings)


def test_t1_7_no_throttle_observed_reads_none(tmp_path):
    spec = RunSpec("agent-runtime", "T1.7", "local-sim", target={"rate_limit": {"onset_rps": 0}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.measurements["throttle_onset_rps"]["value"] == "none"


def test_t1_8_cancellation_clean(tmp_path):
    spec = RunSpec(
        "agent-runtime",
        "T1.8",
        "local-sim",
        target={
            "cancellation": {"honors_cancel": True, "teardown_on_cancel": True, "residual_on_cancel": []}
        },
    )
    rec = execute(spec, results_dir=tmp_path)
    assert rec.status == "completed"
    assert rec.measurements["cancellation_honored"]["value"] is True
    assert rec.measurements["teardown_on_cancel"]["value"] is True
    assert rec.measurements["residual_on_cancel"]["value"] == 0
    assert not rec.findings


def test_t1_8_teardown_leak_on_cancel_is_flagged(tmp_path):
    spec = RunSpec(
        "agent-runtime",
        "T1.8",
        "local-sim",
        target={
            "cancellation": {
                "honors_cancel": True,
                "teardown_on_cancel": False,
                "residual_on_cancel": ["orphan-1"],
            }
        },
    )
    rec = execute(spec, results_dir=tmp_path)
    assert rec.measurements["teardown_on_cancel"]["value"] is False
    assert rec.measurements["residual_on_cancel"]["value"] == 1
    assert any(f["code"] == "agent_runtime.cancel_teardown_leak" for f in rec.findings)
