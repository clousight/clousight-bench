import pytest

from clousight_bench.core import registry
from clousight_bench.core.plugin import DomainPack
from clousight_bench.core.registry import DuplicatePluginError, check_domain_conflicts


class _FakeEP:
    def __init__(self, name, obj):
        self.name = name
        self._obj = obj
        self.dist = None

    def load(self):
        return self._obj


def _dom(domain_name):
    class _D(DomainPack):
        domain = domain_name

        def tasks(self):
            return {}

        def adapters(self):
            return {}

    return _D


def test_duplicate_domain_name_rejected(monkeypatch):
    monkeypatch.setattr(registry, "entry_points",
                        lambda group: [_FakeEP("a", _dom("dup")), _FakeEP("b", _dom("dup"))])
    with pytest.raises(DuplicatePluginError) as ei:
        registry.load_domains()
    assert "dup" in str(ei.value) and "a" in str(ei.value) and "b" in str(ei.value)


def test_intra_domain_task_id_conflict():
    class _T1:
        task_id = "T9.9"

        def config(self, p):
            return {}

        def execute(self, a, p):
            ...

        def score(self, o):
            ...

    class _T2(_T1):
        pass

    class _D(DomainPack):
        domain = "d"

        def tasks(self):
            return {"a": _T1, "b": _T2}  # both task_id T9.9

        def adapters(self):
            return {}

    with pytest.raises(DuplicatePluginError):
        check_domain_conflicts(_D())
