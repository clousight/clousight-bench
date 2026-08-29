"""The gsm8k BenchmarkSuite (mock path + resolve + number parsing)."""

from __future__ import annotations

import json

from clousight_bench.core.registry import load_benchmark_suites
from clousight_bench.core.suite import DriverContext, RawArtifacts, Target
from clousight_bench.suites.gsm8k.suite import Gsm8kSuite, parse_number


def test_suite_registered() -> None:
    assert isinstance(load_benchmark_suites()["gsm8k"], Gsm8kSuite)


def test_parse_number_prefers_hash_then_last() -> None:
    assert parse_number("reasoning...\n#### 42") == "42"
    assert parse_number("the answer is 1,024 dollars") == "1024"
    assert parse_number("3.5") == "3.5"
    assert parse_number("first 10 then 20 finally #### 30") == "30"
    assert parse_number("no digits here") is None


def test_resolve_limit_sensitive() -> None:
    s = Gsm8kSuite()
    a = s.resolve({}, None)
    b = s.resolve({"limit": 2}, None)
    assert len(b.payload["questions"]) == 2
    assert a.digest != b.digest


def test_mock_artifacts_valid_offline() -> None:
    raw = Gsm8kSuite().mock_artifacts({})
    assert isinstance(raw, RawArtifacts)
    answers = json.loads(raw.path("answers").read_text())
    assert answers and all({"id", "predicted", "gold", "correct"} <= set(a) for a in answers)


def test_run_delegates_to_mock() -> None:
    s = Gsm8kSuite()
    env = s.prepare(Target(mode="runtime", mock=True), s.resolve({}, None), DriverContext("local"))
    raw = s.run(Target(mode="runtime", mock=True), env, DriverContext("local"))
    assert {"answers", "summary"} <= set(raw.manifest)
