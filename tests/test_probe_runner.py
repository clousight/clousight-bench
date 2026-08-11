import time

from clousight_bench.domains.agent_runtime.probe.jobs import JobSpec, JobProgress, TERMINAL_STATUSES
from clousight_bench.domains.agent_runtime.probe.runner import JobRunner
from clousight_bench.core.observation import ObservationBundle


def _wait_terminal(runner, job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        rec = runner.get(job_id)
        if rec and rec.status in TERMINAL_STATUSES:
            return rec
        time.sleep(0.01)
    raise AssertionError("job did not reach terminal state")


def test_submit_runs_probe_and_captures_observations():
    def fake_probe(spec, progress_cb):
        progress_cb(JobProgress(phase="go", completed=1, total=1, elapsed_s=0.0), {"rps": 5})
        return ObservationBundle(observations={"ok": True}, series={})

    runner = JobRunner({"fake": fake_probe})
    job_id = runner.submit(JobSpec(probe="fake", params={}, target_endpoint="u"))
    rec = _wait_terminal(runner, job_id)
    assert rec.status == "completed"
    assert rec.observations == {"observations": {"ok": True}, "series": {}, "artifacts": []}
    assert rec.progress.completed == 1
    assert rec.live_metrics == {"rps": 5}


def test_probe_exception_marks_job_failed():
    def boom(spec, progress_cb):
        raise RuntimeError("kaboom")

    runner = JobRunner({"boom": boom})
    job_id = runner.submit(JobSpec(probe="boom", params={}, target_endpoint="u"))
    rec = _wait_terminal(runner, job_id)
    assert rec.status == "failed"
    assert "kaboom" in rec.error
    assert rec.observations is None


def test_unknown_probe_is_rejected_at_submit():
    runner = JobRunner({})
    try:
        runner.submit(JobSpec(probe="nope", params={}, target_endpoint="u"))
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_get_unknown_job_returns_none():
    assert JobRunner({}).get("job-missing") is None
