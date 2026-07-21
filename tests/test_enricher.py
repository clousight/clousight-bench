"""ResultEnricher hook: orchestrator applies registered enrichers before persist."""
from clousight_bench.core.plugin import ResultEnricher
from clousight_bench.core.schema import ResultRecord, utc_now


def test_enricher_is_abstract_and_subclassable():
    class Add(ResultEnricher):
        name = "add"

        def enrich(self, record: ResultRecord) -> ResultRecord:
            record.metrics["added"] = 1
            return record

    rec = ResultRecord(
        domain="d", task_id="t", platform="p", run_id="r",
        started_at=utc_now(), finished_at=utc_now(),
        config_hash="sha256:x", evidence_layer="C", metrics={},
    )
    out = Add().enrich(rec)
    assert out.metrics["added"] == 1


def test_orchestrator_applies_enrichers(monkeypatch, tmp_path):
    import clousight_bench.core.orchestrator as orch

    class Tagger(ResultEnricher):
        name = "tagger"

        def enrich(self, record: ResultRecord) -> ResultRecord:
            record.metrics["enriched_by"] = "tagger"
            return record

    monkeypatch.setattr(orch, "load_enrichers", lambda: [Tagger()])
    from clousight_bench.core.schema import RunSpec

    rec = orch.execute(
        RunSpec("agent-runtime", "T1.3", "local-sim"), results_dir=tmp_path
    )
    assert rec.metrics["enriched_by"] == "tagger"


def test_orchestrator_skips_enrichers_when_disabled(monkeypatch, tmp_path):
    import clousight_bench.core.orchestrator as orch
    from clousight_bench.core.schema import RunSpec

    called = {"n": 0}

    def _boom():
        called["n"] += 1
        return []

    monkeypatch.setattr(orch, "load_enrichers", _boom)
    orch.execute(RunSpec("agent-runtime", "T1.3", "local-sim"), results_dir=tmp_path, enrich=False)
    assert called["n"] == 0
