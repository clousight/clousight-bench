"""Tests for provenance threading: SuiteTask provenance flows into benchmark_fingerprint
and into the persisted ResultRecord.
"""

import clousight_bench.core.orchestrator as orch
from clousight_bench.core.fingerprints import benchmark_fingerprint
from clousight_bench.core.observation import Measurement
from clousight_bench.core.plugin import DomainPack, ProviderAdapter
from clousight_bench.core.record import Provenance
from clousight_bench.core.schema import RunSpec
from clousight_bench.core.suite import (
    BenchmarkSuite,
    DatasetHandle,
    EnvHandle,
    Evaluator,
    RawArtifacts,
)


def test_populated_provenance_moves_the_benchmark_fingerprint():
    base = dict(
        task_id="suite:demo",
        task_revision="1",
        scorer_revision="1",
        workload="",
        workload_version="",
        assets=[],
        params={},
    )
    empty = benchmark_fingerprint(**base, provenance=Provenance().to_dict())
    populated = benchmark_fingerprint(
        **base, provenance=Provenance(suite_id="demo", suite_version="v1").to_dict()
    )
    assert empty != populated
    assert empty == benchmark_fingerprint(**base)  # empty == no-arg (regression guard)


# ---------------------------------------------------------------------------
# Orchestrator-level: a SuiteTask run persists provenance.suite_id in the record
# and the benchmark fingerprint differs from a plain (empty-provenance) task.
# ---------------------------------------------------------------------------


class _Suite(BenchmarkSuite):
    suite_id = "orch-demo"
    suite_version = "v1"

    def resolve(self, cfg, assets):
        return DatasetHandle("v1", "sha256:d", {})

    def prepare(self, target, dataset, driver):
        return EnvHandle({})

    def run(self, target, env, driver):
        raise AssertionError("mock path must not call run")

    def mock_artifacts(self, cfg):
        import json

        d = cfg.get("_tmp_dir")
        if d is None:
            import tempfile as _tf

            d = _tf.mkdtemp()
        from pathlib import Path

        p = Path(d)
        (p / "r.json").write_text(json.dumps({"resolved": 3, "total": 6}))
        return RawArtifacts(p, {"results": {"path": "r.json", "sha256": "x", "rows": None}})


class _Eval(Evaluator):
    evaluator_id = "orch-eval"
    official = True

    def supports(self, suite_id, product):
        return suite_id == "orch-demo"

    def evaluate(self, raw):
        import json

        r = json.loads(raw.path("results").read_text())
        return {
            "orch.resolved": Measurement(
                r["resolved"] / r["total"],
                "ratio",
                reproducibility_class="deterministic",
                official=True,
            )
        }


class _Adapter(ProviderAdapter):
    name = "fake"
    status = "reference"


class _Domain(DomainPack):
    domain = "fake-suite-domain"

    def tasks(self):
        # suite: task_ids are now resolved by the registry bridge, not pack.tasks()
        return {}

    def adapters(self):
        return {"fake": _Adapter}


def test_orchestrator_suite_task_record_has_provenance_and_moved_fingerprint(monkeypatch, tmp_path):
    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
    monkeypatch.setattr(orch, "get_domain", lambda name: _Domain())
    # Wire the suite and evaluator into the bridge's registry lookups.
    # The bridge imports these lazily from clousight_bench.core.registry, so patch there.
    import clousight_bench.core.registry as _reg

    monkeypatch.setattr(_reg, "load_benchmark_suites", lambda: {"orch-demo": _Suite()})
    monkeypatch.setattr(_reg, "load_evaluators", lambda: [_Eval()])

    rec = orch.execute(
        RunSpec("fake-suite-domain", "suite:orch-demo", "fake"),
        results_dir=tmp_path,
        enrich=False,
    )

    # Provenance is populated on the record
    assert rec.provenance.suite_id == "orch-demo"
    assert rec.provenance.evaluator_id == "orch-eval"
    assert rec.provenance.unmodified is True
    # dataset_digest is now real (non-empty) — populated by _dataset() via resolve()
    assert rec.provenance.dataset_digest != ""
    assert rec.provenance.dataset_digest.startswith("sha256:")

    # The benchmark fingerprint differs from a plain-task empty-provenance fingerprint
    empty_fp = benchmark_fingerprint(
        task_id="suite:orch-demo",
        task_revision=rec.identity.task_revision,
        scorer_revision=rec.identity.scorer_revision,
        workload=rec.identity.workload,
        workload_version=rec.identity.workload_version,
        assets=[],
        params={},
        provenance=Provenance().to_dict(),
    )
    assert rec.fingerprints.benchmark != empty_fp


# ---------------------------------------------------------------------------
# Orchestrator defensive: a task whose provenance() raises is recorded, run
# still produces a record with provenance_failed error and empty provenance.
# ---------------------------------------------------------------------------


def test_orchestrator_provenance_crash_recorded(monkeypatch, tmp_path):
    """A task whose provenance() raises → _prepare records provenance_failed;
    run still produces a completed (or failed) record with the error present."""
    from clousight_bench.core.observation import Measurement, ObservationBundle, TaskResult
    from clousight_bench.core.plugin import Task

    class _CrashProvenanceTask(Task):
        task_id = "crash-provenance"
        task_revision = "0"
        scorer_revision = "0"

        def config(self, params):
            return {}

        def provenance(self):
            raise RuntimeError("boom from provenance")

        def execute(self, adapter, params):
            return ObservationBundle(observations={}, artifacts=[])

        def score(self, observations):
            return TaskResult(
                measurements={
                    "dummy": Measurement(1.0, "count", reproducibility_class="deterministic", official=True)
                }
            )

    class _CrashDomain(DomainPack):
        domain = "crash-provenance-domain"

        def tasks(self):
            return {"crash-provenance": _CrashProvenanceTask}

        def adapters(self):
            return {"fake": _Adapter}

    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
    monkeypatch.setattr(orch, "get_domain", lambda name: _CrashDomain())

    rec = orch.execute(
        RunSpec("crash-provenance-domain", "crash-provenance", "fake"),
        results_dir=tmp_path,
        enrich=False,
    )

    error_codes = [e["code"] for e in rec.errors]
    assert "provenance_failed" in error_codes, f"expected provenance_failed in {error_codes}"
    # Provenance falls back to empty default
    assert rec.provenance == Provenance()


def test_suite_benchmark_fingerprint_golden_pin(tmp_path, monkeypatch):
    """Pin the suite:swe-bench benchmark fingerprint to a golden literal.

    Every input to this digest (task_id, suite_version-derived revision, workload
    identity, dataset digest, config, provenance) is deterministic, so this value
    must be byte-stable across machines and refactors.  If this test fails you
    have made a BENCHMARK-IDENTITY-CHANGING modification: verify it is deliberate
    (e.g. the slice-2 real HF pin replacing the placeholder revision), then update
    the literal in the same commit and say so in the commit message.
    """
    import clousight_bench.core.orchestrator as orch
    from clousight_bench.core.schema import RunSpec

    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
    spec = RunSpec(
        domain="agent-runtime",
        task_id="suite:swe-bench",
        platform="local-sim",
        target={"mode": "mock"},
        params={"instance_ids": ["django__django-11099", "sympy__sympy-20590"]},
    )
    rec = orch.execute(spec, results_dir=tmp_path, enrich=False, preflight=False)
    assert rec.status == "completed", f"run failed: {rec.errors}"
    assert rec.fingerprints.benchmark == (
        "sha256:c67465e8c7aa9b2a5717a0141b18bc6952e3bd2be9d1fe3a1c0d5c4cb92deb7c"
    )
