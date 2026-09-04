"""ResultEnricher hook: orchestrator applies registered enrichers before persist."""

from clousight_bench.core.plugin import ResultEnricher
from clousight_bench.core.record import ResultRecord


def test_orchestrator_applies_enrichers(monkeypatch, tmp_path):
    import clousight_bench.core.finalize as fin
    import clousight_bench.core.orchestrator as orch
    from clousight_bench.core.schema import RunSpec

    class Tagger(ResultEnricher):
        name = "tagger"

        def enrich(self, record: ResultRecord) -> ResultRecord:
            record.extensions["tagger"] = {"applied": True}
            return record

    monkeypatch.setattr(fin, "load_enrichers", lambda: [Tagger()])
    rec = orch.execute(RunSpec("agent-runtime", "suite:stub.ok", "local-sim"), results_dir=tmp_path)
    assert rec.extensions["tagger"] == {"applied": True}
    assert rec.run.stages["ENRICH"] == "ok"


def test_orchestrator_skips_enrichers_when_disabled(monkeypatch, tmp_path):
    import clousight_bench.core.finalize as fin
    import clousight_bench.core.orchestrator as orch
    from clousight_bench.core.schema import RunSpec

    called = {"n": 0}

    def _boom():
        called["n"] += 1
        return []

    monkeypatch.setattr(fin, "load_enrichers", _boom)
    rec = orch.execute(
        RunSpec("agent-runtime", "suite:stub.ok", "local-sim"), results_dir=tmp_path, enrich=False
    )
    assert called["n"] == 0
    assert rec.run.stages["ENRICH"] == "skipped"
