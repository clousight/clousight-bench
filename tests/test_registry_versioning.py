import pytest

from clousight_bench.core import registry
from clousight_bench.core.plugin import DomainPack
from clousight_bench.core.registry import IncompatiblePluginError


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
