"""The two read-only faces that complete the five sections.

`installed_suites` answers "what can this build measure", and `installed_plugins`
answers "what is this build made of". Both read the live registry, because a
catalogue maintained beside the thing it describes is the one that goes stale.
"""

from __future__ import annotations

import json
from pathlib import Path

from clousight_bench.viewer.catalogue import installed_plugins, installed_suites


class TestSuites:
    def test_every_installed_benchmark_is_listed_with_its_pinned_data(self, tmp_path: Path) -> None:
        by_id = {s["suite_id"]: s for s in installed_suites(tmp_path)}
        assert "tpc-h" in by_id
        # The version is the pin: the same benchmark id always means the same
        # data, and this is where a reader checks which data that is.
        assert by_id["tpc-h"]["suite_version"] != ""

    def test_a_benchmark_names_the_evaluators_that_can_score_it(self, tmp_path: Path) -> None:
        by_id = {s["suite_id"]: s for s in installed_suites(tmp_path)}
        evaluators = by_id["tpc-h"]["evaluators"]
        assert evaluators != []
        assert all("evaluator_id" in ev and "official" in ev for ev in evaluators)

    def test_a_benchmark_carries_how_often_it_has_run_here(self, tmp_path: Path) -> None:
        directory = tmp_path / "llm" / "llm-mock"
        directory.mkdir(parents=True)
        (directory / "suite-gsm8k-run-1.json").write_text(
            json.dumps(
                {
                    "run": {"run_id": "run-1", "started_at": "2026-01-01T00:00:00Z"},
                    "identity": {"domain": "llm", "task_id": "suite:gsm8k", "adapter": "llm-mock"},
                    "provenance": {"suite_id": "gsm8k"},
                    "status": "completed",
                    "measurements": {},
                }
            )
        )
        by_id = {s["suite_id"]: s for s in installed_suites(tmp_path)}
        assert by_id["gsm8k"]["runs"] == 1
        assert by_id["tpc-h"]["runs"] == 0


class TestPlugins:
    def test_it_reports_what_this_build_is_made_of(self) -> None:
        inventory = installed_plugins()
        kinds = {entry["kind"] for entry in inventory["plugins"]}
        # The four extension points a plugin can occupy. A reader debugging
        # "why can't I run X" needs to see which of them is empty.
        assert {"domain", "benchmark_suite", "evaluator"} <= kinds
        assert inventory["core_version"] != ""
        assert inventory["plugin_api"] != ""

    def test_each_plugin_says_where_it_came_from(self) -> None:
        for entry in installed_plugins()["plugins"]:
            assert entry["name"] != ""
            assert "distribution" in entry
