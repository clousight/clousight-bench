import importlib.util
import json
from pathlib import Path

import pytest

from clousight_bench.core.fingerprints import record_digest
from clousight_bench.core.observation import Measurement, ObservationBundle, TaskResult
from clousight_bench.core.plugin import Task


class _StubTask(Task):
    """Minimal concrete Task used to drive orchestrator-level machinery tests.

    Tests that exercise generic orchestrator behaviour (lifecycle, tracing,
    runplan, timeout, …) register this stub as "stub.ok" (and "stub.alt").
    The stub produces a single "ok" measurement, which is sufficient for
    infra assertions.
    """

    task_id = "stub.ok"
    title = "stub task (suite-first pivot)"
    task_revision = "0"
    scorer_revision = "0"
    requires_mock_server = False

    def config(self, params):
        return {"params": dict(params)}

    def execute(self, adapter, params):
        return ObservationBundle(observations={"ok": True})

    def score(self, bundle):
        return TaskResult(measurements={"ok": Measurement(True, "", reproducibility_class="deterministic")})


class _StubTask11(_StubTask):
    """A second stub id for tests that need two distinct tasks."""

    task_id = "stub.alt"


_STUB_TASKS_SKIP = frozenset(
    [
        # These tests check the PRODUCTION registry (docs and CLI surface) and must
        # see the real (zero-task) domain, not the stub. The autouse skips them.
        "test_docs_inventory",
    ]
)


@pytest.fixture(autouse=True)
def _inject_stub_tasks(request, monkeypatch):
    """Register _StubTask as "stub.ok" and "stub.alt" in AgentRuntimeDomain for each test.

    Suite-first pivot retired the 27 T-code dimensions; tests that exercise
    generic orchestrator behaviour (runplan, tracing, timeout, interrupt, …)
    register these stubs so their RunSpec("agent-runtime", "stub.ok", …) strings
    stay unchanged. Tests that verify the production registry (docs inventory)
    are exempted so they see the real zero-task domain.
    """
    module = request.module.__name__.split(".")[-1]
    if module in _STUB_TASKS_SKIP:
        return  # don't patch — let the test see the real (empty) domain
    if request.node.get_closest_marker("real_registry") is not None:
        return  # per-test opt-out: the test asserts on the real (zero-task) domain

    from clousight_bench.domains.agent_runtime import AgentRuntimeDomain

    monkeypatch.setattr(
        AgentRuntimeDomain,
        "tasks",
        lambda self: {_StubTask.task_id: _StubTask, _StubTask11.task_id: _StubTask11},
    )


# Skip optional-dependency tests when their extra isn't installed. The in-region
# probe + Aliyun provider modules import `requests` (the [probe] / [aliyun]
# extras); a bare core+[dev] install should skip those tests cleanly instead of
# erroring at collection, so the no-extras CI floor keeps working.
collect_ignore_glob: list[str] = []
if importlib.util.find_spec("requests") is None:
    collect_ignore_glob += [
        "test_probe_*.py",
        "test_aliyun_*.py",
        "test_eci*.py",
        "test_dataplane_*.py",
        "test_reaper*.py",
        "test_campaign_carrier_lifecycle.py",
        # Reliability probes drive the agent→mock HTTP path (probe/dataplane.py
        # imports `requests`), so skip them on the bare core+[dev] floor too.
        "test_reliability_*.py",
    ]


def _write_analytics_record(root: Path, run_id: str = "r1") -> None:
    """Write one digest-valid 0.3 record for the analytics tests."""
    payload = {
        "schema_version": "0.4",
        "run": {
            "run_id": run_id,
            "started_at": "2026-01-01T00:00:00Z",
            "finished_at": "2026-01-01T00:00:01Z",
            "stages": {},
        },
        "identity": {
            "domain": "agent-runtime",
            "task_id": "stub.ok",
            "adapter": "local-sim",
            "task_revision": "2",
            "scorer_revision": "2",
        },
        "environment": {"region": "cn-hangzhou", "mode": "mock"},
        "fingerprints": {"benchmark": "sha256:a", "environment": "sha256:b", "implementation": "sha256:c"},
        "measurements": {
            "cold_start_ms": {
                "value": 42.0,
                "unit": "ms",
                "reproducibility_class": "environmental",
                "aggregation": "p50",
                "sample_count": 5,
            },
            "recovery_mode": {"value": "auto-retry", "unit": "", "reproducibility_class": "deterministic"},
        },
        "findings": [
            {
                "code": "agent_runtime.scaling_knee",
                "severity": "warning",
                "summary": "knee at 8",
            }
        ],
        "observations": {},
        "series": {},
        "artifacts": [],
        "extensions": {"pricing": {"cost_usd": 0.0123}},
        "errors": [],
        "status": "completed",
    }
    payload["fingerprints"]["record_digest"] = record_digest(payload)
    p = root / "agent-runtime" / "local-sim"
    p.mkdir(parents=True, exist_ok=True)
    (p / f"stub.ok-{run_id}.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def write_record():
    """Return the analytics-record writer: write_record(root, run_id='r1')."""
    return _write_analytics_record


def _make_report_record(
    adapter, task_id, *, execution="simulated", measurements=None, domain="agent-runtime", extensions=None
):
    from clousight_bench.core.record import ResultRecord

    payload = {
        "schema_version": "0.4",
        "run": {
            "run_id": f"{adapter}-{task_id}-{execution}",
            "started_at": "2026-01-01T00:00:00Z",
            "finished_at": "2026-01-01T00:00:01Z",
            "stages": {},
        },
        "identity": {
            "domain": domain,
            "task_id": task_id,
            "adapter": adapter,
            "task_revision": "1",
            "scorer_revision": "1",
            "core_version": "0.2.0",
            "adapter_status": "reference",
            "plugin_versions": {},
        },
        "environment": {
            "region": "",
            "mode": "cloud",
            "python_version": "3.12.0",
            "os_name": "Linux",
            "facts": {},
            "execution": execution,
        },
        "fingerprints": {
            "benchmark": f"sha256:{task_id}",
            "environment": f"sha256:{execution}",
            "implementation": "sha256:c",
        },
        "measurements": {
            k: {"value": v, "unit": "", "reproducibility_class": "environmental"}
            for k, v in (measurements or {}).items()
        },
        "findings": [],
        "observations": {},
        "series": {},
        "artifacts": [],
        "extensions": extensions or {},
        "errors": [],
        "status": "completed",
    }
    payload["fingerprints"]["record_digest"] = record_digest(payload)
    return ResultRecord.from_dict(payload)


@pytest.fixture
def report_record():
    """Factory: report_record(adapter, task_id, execution=..., measurements=..., ...)."""
    return _make_report_record
