import importlib.util
import json
import tempfile
from pathlib import Path

import pytest

from clousight_bench.core.canonical import sha256_bytes
from clousight_bench.core.fingerprints import record_digest
from clousight_bench.core.observation import Measurement
from clousight_bench.core.suite import (
    BenchmarkSuite,
    DatasetHandle,
    EnvHandle,
    Evaluator,
    RawArtifacts,
)


def stub_artifacts() -> RawArtifacts:
    """A fresh one-file artifact dir (the runner stages then removes it)."""
    d = Path(tempfile.mkdtemp(prefix="csbench-stub-"))
    body = json.dumps({"ok": True}).encode()
    (d / "result.json").write_bytes(body)
    return RawArtifacts(
        dir=d,
        manifest={"result": {"path": "result.json", "sha256": sha256_bytes(body), "rows": None}},
    )


def make_stub_suite(suite_id: str = "stub.ok", *, run_hook=None, **attrs) -> type[BenchmarkSuite]:
    """Build a stub BenchmarkSuite class driving orchestrator machinery tests.

    ``run_hook(target, env, driver)`` runs on the REAL ``run()`` path before the
    artifacts are produced (the mock path never calls it) — the seam for tests
    that need a mid-run failure or an adapter interaction. Extra ``attrs`` land
    as class attributes (e.g. ``required_permissions``).
    """

    class _Stub(BenchmarkSuite):
        suite_version = "0"

        def resolve(self, cfg, assets):  # noqa: ARG002
            return DatasetHandle(version="0", digest="sha256:stub", payload={})

        def prepare(self, target, dataset, driver):  # noqa: ARG002
            return EnvHandle({})

        def run(self, target, env, driver):
            if run_hook is not None:
                run_hook(target, env, driver)
            return stub_artifacts()

        def teardown(self, env):  # noqa: ARG002
            return None

        def mock_artifacts(self, cfg):  # noqa: ARG002
            return stub_artifacts()

    _Stub.suite_id = suite_id
    for key, value in attrs.items():
        setattr(_Stub, key, value)
    _Stub.__name__ = f"_StubSuite_{suite_id.replace('.', '_')}"
    return _Stub


class StubEvaluator(Evaluator):
    """Scores any ``stub.*`` suite's artifacts into one deterministic "ok"."""

    evaluator_id = "stub-evaluator"
    official = True

    def supports(self, suite_id, product):  # noqa: ARG002
        return suite_id.startswith("stub.")

    def evaluate(self, raw):  # noqa: ARG002
        return {"ok": Measurement(True, "", reproducibility_class="deterministic")}


_StubSuiteOk = make_stub_suite("stub.ok")
_StubSuiteAlt = make_stub_suite("stub.alt")


def register_stub_suites(monkeypatch, *suite_classes, evaluators=()):
    """Append stub suites (+ optional extra evaluators) to the real registries.

    The single benchmark rail resolves ``suite:<id>`` through
    ``registry.load_benchmark_suites`` / ``load_evaluators``; tests that need a
    bespoke stub (failing run, custom permissions) build one with
    ``make_stub_suite`` and register it here.
    """
    from clousight_bench.core import registry as _reg

    real_suites = _reg.load_benchmark_suites
    real_evaluators = _reg.load_evaluators

    def _suites():
        out = real_suites()
        for cls in suite_classes:
            out[cls.suite_id] = cls()
        return out

    def _evaluators():
        return [*real_evaluators(), StubEvaluator(), *[e() for e in evaluators]]

    monkeypatch.setattr(_reg, "load_benchmark_suites", _suites)
    monkeypatch.setattr(_reg, "load_evaluators", _evaluators)


_STUB_SUITES_SKIP = frozenset(
    [
        # These tests check the PRODUCTION registry (docs and CLI surface) and must
        # see the real suite registry, not the stubs. The autouse skips them.
        "test_docs_inventory",
    ]
)


@pytest.fixture(autouse=True)
def _inject_stub_suites(request, monkeypatch):
    """Register the "stub.ok" / "stub.alt" suites (addressed as suite:<id>) for each test.

    Tests that exercise generic orchestrator behaviour (runplan, tracing,
    timeout, interrupt, …) address them as ``suite:stub.ok`` / ``suite:stub.alt``
    on the single benchmark rail. Tests that verify the production registry
    (docs inventory, ``real_registry`` marker) are exempted.
    """
    module = request.module.__name__.split(".")[-1]
    if module in _STUB_SUITES_SKIP:
        return  # don't patch — let the test see the real registry
    if request.node.get_closest_marker("real_registry") is not None:
        return  # per-test opt-out: the test asserts on the real registry

    register_stub_suites(monkeypatch, _StubSuiteOk, _StubSuiteAlt)


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
            "task_id": "suite:stub.ok",
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
    (p / f"suite:stub.ok-{run_id}.json").write_text(json.dumps(payload), encoding="utf-8")


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
