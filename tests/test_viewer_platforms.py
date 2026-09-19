"""The platform catalogue: what the viewer says this build can measure.

Every fact on that page is read off an installed adapter, so these tests
install fake ones and assert on what comes out the other end — the shape the
UI consumes, not the internals that produce it.
"""

from __future__ import annotations

from clousight_bench.core import registry
from clousight_bench.core.plugin import DomainPack
from clousight_bench.viewer.platforms import list_platforms, platform_usage


class _Reference:
    """In-memory reference: YCSB ``binding=basic``, no external datastore.

    A second paragraph, which is an implementation note and must not surface.
    """

    status = "reference"


class _Vendor:
    """Aliyun AgentRun (GA). Sessions map to a managed runtime."""

    status = "experimental"
    DOCS = "https://help.aliyun.com/agentrun (AgentRun)"


class _Undocumented:
    pass


class _Pack(DomainPack):
    domain = "kv"
    description = "Key-value datastores."

    def adapters(self):
        return {"vendor": _Vendor, "reference": _Reference, "bare": _Undocumented}


class _BrokenPack(DomainPack):
    domain = "broken"
    description = "A domain whose adapters cannot be listed."

    def adapters(self):
        raise RuntimeError("entry point is half-installed")


def _install(monkeypatch, *packs: type[DomainPack]) -> None:
    monkeypatch.setattr(registry, "load_domains", lambda: {p.domain: p() for p in packs})


def _rows(catalogue: dict) -> dict[str, dict]:
    return {row["platform"]: row for d in catalogue["domains"] for row in d["platforms"]}


def test_summary_is_prose_not_rst(monkeypatch) -> None:
    """The page renders text, so docstring markup must not reach it.

    Adapters document themselves in RST, where ``like this`` is an inline
    literal. Rendered verbatim the reader sees the backticks — this fired on
    the real ycsb-local and benchbase-local rows.
    """
    _install(monkeypatch, _Pack)
    summary = _rows(list_platforms())["reference"]["summary"]
    assert summary == "In-memory reference: YCSB binding=basic, no external datastore"
    assert "`" not in summary


def test_summary_stops_at_the_first_sentence(monkeypatch) -> None:
    """What the thing IS, not the notes for whoever edits the adapter."""
    _install(monkeypatch, _Pack)
    assert _rows(list_platforms())["vendor"]["summary"] == "Aliyun AgentRun (GA)"


def test_undocumented_adapter_says_nothing(monkeypatch) -> None:
    _install(monkeypatch, _Pack)
    row = _rows(list_platforms())["bare"]
    assert row["summary"] == ""
    assert row["status"] == "unknown"


def test_annotated_docs_link_keeps_only_the_url(monkeypatch) -> None:
    _install(monkeypatch, _Pack)
    rows = _rows(list_platforms())
    assert rows["vendor"]["docs"] == "https://help.aliyun.com/agentrun"
    assert rows["reference"]["docs"] == ""


def test_uncounted_usage_is_null_not_zero(monkeypatch) -> None:
    """Nobody counted and counted-found-none are different claims."""
    _install(monkeypatch, _Pack)
    catalogue = list_platforms()
    assert catalogue["counted"] is False
    assert all(row["runs"] is None for row in _rows(catalogue).values())

    counted = list_platforms({"kv/vendor": 3})
    assert counted["counted"] is True
    assert _rows(counted)["vendor"]["runs"] == 3
    assert _rows(counted)["reference"]["runs"] == 0


def test_a_broken_domain_loses_its_rows_not_the_page(monkeypatch) -> None:
    """One half-installed plugin must not blank the catalogue."""
    _install(monkeypatch, _Pack, _BrokenPack)
    catalogue = list_platforms()
    assert [d["domain"] for d in catalogue["domains"]] == ["broken", "kv"]
    assert catalogue["domains"][0]["platforms"] == []
    assert len(catalogue["domains"][1]["platforms"]) == 3


def test_an_unloadable_registry_is_reported_not_swallowed(monkeypatch) -> None:
    def _boom() -> dict:
        raise RuntimeError("no entry points")

    monkeypatch.setattr(registry, "load_domains", _boom)
    catalogue = list_platforms()
    assert catalogue["domains"] == []
    assert "no entry points" in catalogue["error"]


def test_usage_counts_sealed_records_by_domain_and_platform() -> None:
    records = [
        {"domain": "llm", "adapter": "llm-mock"},
        {"domain": "llm", "adapter": "llm-mock"},
        {"domain": "key-value", "adapter": "ycsb-local"},
    ]
    assert platform_usage(records) == {"llm/llm-mock": 2, "key-value/ycsb-local": 1}


def test_usage_ignores_a_record_that_names_neither() -> None:
    """A record missing either half cannot be attributed, so it is not."""
    assert platform_usage([{"domain": "llm"}, {"adapter": "x"}, {"domain": "", "adapter": "x"}]) == {}
