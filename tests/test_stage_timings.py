"""Per-stage durations are recorded so a slow or hung stage is visible."""

from clousight_bench.core.orchestrator import execute
from clousight_bench.core.record import RunInfo
from clousight_bench.core.schema import RunSpec


def test_lifecycle_stages_are_timed(tmp_path):
    rec = execute(
        RunSpec("agent-runtime", "T1.3", "local-sim", target={"recovery": {"mode": "auto-retry"}}),
        results_dir=tmp_path,
    )
    timings = rec.run.stage_timings
    for stage in ("SETUP", "EXECUTE", "COLLECT", "TEARDOWN", "SCORE"):
        assert stage in timings, f"{stage} not timed"
        assert isinstance(timings[stage], (int, float)) and timings[stage] >= 0.0


def test_stage_timings_round_trip():
    info = RunInfo(
        run_id="run-x",
        started_at="t0",
        finished_at="t1",
        stages={"SETUP": "ok"},
        stage_timings={"SETUP": 12.5},
    )
    assert RunInfo.from_dict(info.to_dict()).stage_timings == {"SETUP": 12.5}


def test_unknown_stage_timing_key_is_rejected():
    import pytest

    from clousight_bench.core.record import RecordError

    with pytest.raises(RecordError, match="stage_timings"):
        RunInfo(run_id="r", started_at="t0", finished_at="t1", stage_timings={"NOT_A_STAGE": 1.0})
