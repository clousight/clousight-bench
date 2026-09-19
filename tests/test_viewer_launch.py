"""Starting a run from the browser: what the server will accept as one.

The request body is a set of whitelisted fields, never a command and never a
path. Every test here is one shape of "no" — because the thing being built is
a process launcher reachable from a web page, and the interesting behaviour of
a process launcher is everything it declines to launch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clousight_bench.viewer.launch import (
    MAX_REPEAT,
    MAX_WARMUP,
    LaunchSpec,
    launch_argv,
    launch_options,
    record_launch,
    target_usage,
    validate_launch,
)


@pytest.fixture()
def configs(tmp_path: Path) -> Path:
    d = tmp_path / "configs"
    d.mkdir()
    (d / "mock.yaml").write_text("target:\n  mode: mock\n")
    return d


def _body(**over: object) -> dict:
    body = {"domain": "llm", "task_id": "suite:gsm8k", "platform": "llm-mock"}
    body.update(over)
    return body


class TestValidation:
    def test_a_well_formed_request_is_accepted(self, configs: Path) -> None:
        spec, why = validate_launch(_body(target="mock", params={"limit": 2}), configs)
        assert why == ""
        assert spec == LaunchSpec(
            domain="llm",
            task_id="suite:gsm8k",
            platform="llm-mock",
            target="mock",
            params={"limit": 2},
            repeat=1,
            warmup=0,
        )

    def test_a_bare_benchmark_id_is_spelled_out(self, configs: Path) -> None:
        """`gsm8k` and `suite:gsm8k` are the same benchmark; the record says one."""
        spec, why = validate_launch(_body(task_id="gsm8k"), configs)
        assert why == ""
        assert spec is not None
        assert spec.task_id == "suite:gsm8k"

    @pytest.mark.parametrize(
        ("field", "value"),
        [("domain", "nope"), ("platform", "nope"), ("task_id", "suite:nope")],
    )
    def test_an_unknown_name_is_refused_and_the_legal_ones_are_named(
        self, configs: Path, field: str, value: str
    ) -> None:
        # The form is validated against what is installed right now — the same
        # registry the run itself will resolve against. Refusing without saying
        # what would have worked makes the console a guessing game.
        spec, why = validate_launch(_body(**{field: value}), configs)
        assert spec is None
        assert "nope" in why

    def test_a_platform_from_another_domain_is_not_this_domain_s(self, configs: Path) -> None:
        spec, why = validate_launch(_body(platform="duckdb-local"), configs)
        assert spec is None
        assert "duckdb-local" in why

    def test_a_benchmark_nothing_can_score_is_refused_up_front(
        self, configs: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The same check the runner makes, made before a process is started.

        Otherwise the console reports "started" and the run dies at SCORE
        minutes later with the failure buried in a subprocess log.

        Note what this does NOT check: whether this benchmark belongs on this
        platform. No installed evaluator looks at its ``product`` argument, and
        nothing in the registry maps a benchmark to a domain — so a rule here
        would be one this code invented. The form shows evidence instead (see
        `seen_platforms`), and a combination nothing has ever run is a warning
        on the confirmation step, not a refusal.
        """
        from clousight_bench.core import registry

        monkeypatch.setattr(registry, "load_evaluators", list)
        spec, why = validate_launch(_body(), configs)
        assert spec is None
        assert "evaluator" in why.lower()

    def test_params_take_scalars_and_lists_of_them_only(self, configs: Path) -> None:
        spec, why = validate_launch(_body(params={"a": 1, "b": "x", "c": True, "d": [1, 2]}), configs)
        assert why == ""
        assert spec is not None

        for bad in ({"a": {"nested": 1}}, {"a": [{"nested": 1}]}, {"a": None}):
            spec, why = validate_launch(_body(params=bad), configs)
            assert spec is None, bad
            assert "param" in why.lower()

    def test_params_must_be_an_object(self, configs: Path) -> None:
        spec, why = validate_launch(_body(params=[1, 2]), configs)
        assert spec is None
        assert "param" in why.lower()

    def test_repeat_and_warmup_have_hard_ceilings(self, configs: Path) -> None:
        # The ceiling is what stops one request from queueing an afternoon of
        # billable work on a cloud platform.
        for field, limit in (("repeat", MAX_REPEAT), ("warmup", MAX_WARMUP)):
            spec, why = validate_launch(_body(**{field: limit + 1}), configs)
            assert spec is None
            assert field in why
            spec, why = validate_launch(_body(**{field: limit}), configs)
            assert why == ""
        assert validate_launch(_body(repeat=0), configs)[0] is None
        assert validate_launch(_body(warmup=-1), configs)[0] is None

    def test_a_target_is_a_name_and_must_already_exist(self, configs: Path) -> None:
        for bad in ("../etc/passwd", "a/b", "ghost"):
            spec, why = validate_launch(_body(target=bad), configs)
            assert spec is None, bad
            assert "target" in why.lower()

    def test_no_target_is_allowed(self, configs: Path) -> None:
        """A suite with a mock platform needs no configuration at all."""
        spec, why = validate_launch(_body(), configs)
        assert why == ""
        assert spec is not None
        assert spec.target is None


class TestArgv:
    def test_the_command_is_built_from_fields_never_from_a_string(self, tmp_path: Path) -> None:
        spec = LaunchSpec(
            domain="llm",
            task_id="suite:gsm8k",
            platform="llm-mock",
            target="mock",
            params={"limit": 2, "flag": True},
            repeat=3,
            warmup=1,
        )
        argv = launch_argv(spec, tmp_path / "results", tmp_path / "configs", "run-x")
        assert argv[1:4] == ["-m", "clousight_bench", "run"]
        assert "--domain" in argv and "llm" in argv
        assert "--benchmark" in argv and "gsm8k" in argv
        assert "--run-id" in argv and "run-x" in argv
        assert str(tmp_path / "configs" / "mock.yaml") in argv
        # Params go one --param per key, so no value can carry a second flag.
        assert argv.count("--param") == 2
        assert "limit=2" in argv
        assert "flag=true" in argv

    def test_a_repeatless_run_passes_no_repeat_flag(self, tmp_path: Path) -> None:
        # --run-id and --repeat cannot be combined; the default must not send
        # a --repeat 1 that would trip that refusal.
        spec = LaunchSpec("llm", "suite:gsm8k", "llm-mock", None, {}, 1, 0)
        argv = launch_argv(spec, tmp_path / "results", tmp_path / "configs", "run-x")
        assert "--repeat" not in argv
        assert "--config" not in argv


class TestBookkeeping:
    def test_a_launch_records_what_it_was_given(self, tmp_path: Path) -> None:
        """Sealed records do not carry their config file's name; this does.

        It is the console's own plane, beside the results rather than in them:
        a record is covered by record_digest and must not grow fields because
        a UI wanted a count.
        """
        spec = LaunchSpec("llm", "suite:gsm8k", "llm-mock", "mock", {}, 1, 0)
        record_launch(tmp_path, "run-x", spec)
        saved = json.loads((tmp_path / ".launches" / "run-x.json").read_text())
        assert saved["target"] == "mock"
        assert saved["task_id"] == "suite:gsm8k"
        assert target_usage(tmp_path) == {"mock": 1}

    def test_nothing_launched_counts_as_nothing_used(self, tmp_path: Path) -> None:
        assert target_usage(tmp_path) == {}

    def test_a_launch_without_a_target_counts_towards_none(self, tmp_path: Path) -> None:
        record_launch(tmp_path, "run-y", LaunchSpec("llm", "suite:gsm8k", "llm-mock", None, {}, 1, 0))
        assert target_usage(tmp_path) == {}


class TestOptions:
    def test_the_form_is_offered_only_what_is_installed(self, tmp_path: Path) -> None:
        options = launch_options(tmp_path)
        domains = {d["domain"]: d for d in options["domains"]}
        assert "llm" in domains
        assert "llm-mock" in [p["platform"] for p in domains["llm"]["platforms"]]
        assert "gsm8k" in [s["suite_id"] for s in options["suites"]]

    def test_each_benchmark_carries_where_it_has_actually_run(self, tmp_path: Path) -> None:
        """Evidence, not a rule.

        Nothing in the registry says which platform a benchmark belongs on, so
        the form shows what this results directory has actually produced and
        leaves the choice to the reader. An invented whitelist would be the
        one thing that goes stale the week a new adapter lands.
        """
        record_dir = tmp_path / "llm" / "llm-mock"
        record_dir.mkdir(parents=True)
        (record_dir / "suite-gsm8k-run-1.json").write_text(
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
        by_id = {s["suite_id"]: s for s in launch_options(tmp_path)["suites"]}
        assert by_id["gsm8k"]["seen_platforms"] == ["llm-mock"]
        assert by_id["tpc-h"]["seen_platforms"] == []
