"""The mmlu BenchmarkSuite (mock path + resolve + prompt/parse helpers)."""

from __future__ import annotations

import json

from clousight_bench.core.registry import load_benchmark_suites
from clousight_bench.core.suite import DriverContext, RawArtifacts, Target
from clousight_bench.suites.mmlu.suite import MmluSuite, format_prompt, parse_letter


def test_suite_registered_via_entry_point() -> None:
    suites = load_benchmark_suites()
    assert "mmlu" in suites
    assert isinstance(suites["mmlu"], MmluSuite)


def test_parse_letter() -> None:
    assert parse_letter("B") == 1
    assert parse_letter("The answer is C.") == 2
    assert parse_letter("d") == 3
    assert parse_letter("no letter here 123") is None


def test_format_prompt_has_lettered_choices() -> None:
    q = {"question": "2+2?", "choices": ["3", "4", "5", "6"], "answer": 1}
    p = format_prompt(q)
    assert "A. 3" in p and "B. 4" in p and "Answer:" in p


def test_resolve_limit_and_subject_filter() -> None:
    suite = MmluSuite()
    d_all = suite.resolve({}, None)
    n_all = len(d_all.payload["questions"])
    assert n_all >= 3
    d_lim = suite.resolve({"limit": 2}, None)
    assert len(d_lim.payload["questions"]) == 2
    assert d_lim.digest != d_all.digest
    d_subj = suite.resolve({"subjects": ["abstract_algebra"]}, None)
    assert all(q["subject"] == "abstract_algebra" for q in d_subj.payload["questions"])


def test_mock_artifacts_are_valid_and_offline() -> None:
    raw = MmluSuite().mock_artifacts({})
    assert isinstance(raw, RawArtifacts)
    assert {"answers", "summary"} <= set(raw.manifest)
    answers = json.loads(raw.path("answers").read_text())
    assert answers and all({"id", "predicted", "gold", "correct"} <= set(a) for a in answers)


def test_run_delegates_to_mock_when_target_mock() -> None:
    suite = MmluSuite()
    env = suite.prepare(Target(mode="runtime", mock=True), suite.resolve({}, None), DriverContext("local"))
    assert env.payload.get("mock") is True
    raw = suite.run(Target(mode="runtime", mock=True), env, DriverContext("local"))
    assert {"answers", "summary"} <= set(raw.manifest)


def test_prepare_real_without_endpoint_fails_loud() -> None:
    suite = MmluSuite()
    import pytest

    with pytest.raises(RuntimeError, match="endpoint"):
        suite.prepare(
            Target(mode="endpoint", mock=False, endpoint="", handle=None),
            suite.resolve({}, None),
            DriverContext("local"),
        )
