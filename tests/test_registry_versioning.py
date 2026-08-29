import pytest

from clousight_bench.core import registry
from clousight_bench.core.plugin import DomainPack
from clousight_bench.core.registry import IncompatiblePluginError
from clousight_bench.core.suite import BenchmarkSuite, Evaluator


class _FakeEP:
    def __init__(self, name, obj):
        self.name = name
        self._obj = obj
        self.dist = None

    def load(self):
        return self._obj


class _GoodDomain(DomainPack):
    domain = "good"
    requires_plugin_api = ">=1.0,<2.0"

    def tasks(self):
        return {}

    def adapters(self):
        return {}


class _FutureDomain(DomainPack):
    domain = "future"
    requires_plugin_api = ">=2.0,<3.0"

    def tasks(self):
        return {}

    def adapters(self):
        return {}


def test_incompatible_domain_rejected(monkeypatch):
    monkeypatch.setattr(registry, "entry_points", lambda group: [_FakeEP("future", _FutureDomain)])
    with pytest.raises(IncompatiblePluginError) as ei:
        registry.load_domains()
    assert "future" in str(ei.value) and "1.0" in str(ei.value)


def test_compatible_domain_loads(monkeypatch):
    monkeypatch.setattr(registry, "entry_points", lambda group: [_FakeEP("good", _GoodDomain)])
    assert "good" in registry.load_domains()


class _SuiteBase(BenchmarkSuite):
    def resolve(self, cfg, assets):
        raise NotImplementedError

    def prepare(self, target, dataset, driver):
        raise NotImplementedError

    def run(self, target, env, driver):
        raise NotImplementedError

    def mock_artifacts(self, cfg):
        raise NotImplementedError


class _GoodSuite(_SuiteBase):
    suite_id = "good-suite"
    requires_plugin_api = ">=1.0,<2.0"


class _FutureSuite(_SuiteBase):
    suite_id = "future-suite"
    requires_plugin_api = ">=2.0,<3.0"


class _EvaluatorBase(Evaluator):
    def supports(self, suite_id, product):
        return False

    def evaluate(self, raw):
        return {}


class _GoodEvaluator(_EvaluatorBase):
    evaluator_id = "good-eval"
    requires_plugin_api = ">=1.0,<2.0"


class _FutureEvaluator(_EvaluatorBase):
    evaluator_id = "future-eval"
    requires_plugin_api = ">=2.0,<3.0"


def test_incompatible_suite_rejected(monkeypatch):
    monkeypatch.setattr(registry, "entry_points", lambda group: [_FakeEP("future", _FutureSuite)])
    with pytest.raises(IncompatiblePluginError) as ei:
        registry.load_benchmark_suites()
    assert "future" in str(ei.value) and "1.0" in str(ei.value)


def test_compatible_suite_loads(monkeypatch):
    monkeypatch.setattr(registry, "entry_points", lambda group: [_FakeEP("good", _GoodSuite)])
    assert "good-suite" in registry.load_benchmark_suites()


def test_incompatible_evaluator_rejected(monkeypatch):
    monkeypatch.setattr(registry, "entry_points", lambda group: [_FakeEP("future", _FutureEvaluator)])
    with pytest.raises(IncompatiblePluginError) as ei:
        registry.load_evaluators()
    assert "future" in str(ei.value) and "1.0" in str(ei.value)


def test_compatible_evaluator_loads(monkeypatch):
    monkeypatch.setattr(registry, "entry_points", lambda group: [_FakeEP("good", _GoodEvaluator)])
    evaluators = registry.load_evaluators()
    assert [e.evaluator_id for e in evaluators] == ["good-eval"]
