"""Tests for the HumanEval suite + evaluator + sandboxed executor.

Covers suite identity, the real bundled sample, the offline fixture path, the
REFERENCE-EXECUTION path (really running the dataset's canonical solutions
through the sandbox), the executor's pass/fail/timeout behavior, and the
evaluator's namespace/measurements.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clousight_bench.core.suite import DriverContext, EnvHandle, RawArtifacts, Target
from clousight_bench.suites.human_eval.evaluator import OfficialHumanEvalEvaluator
from clousight_bench.suites.human_eval.executor import build_program, run_candidate
from clousight_bench.suites.human_eval.suite import (
    _SUITE_VERSION,
    HumanEvalSuite,
    extract_code,
    validate_endpoint,
)

_FIXTURES = Path(__file__).parent.parent / "src" / "clousight_bench" / "suites" / "human_eval" / "fixtures"


# ---------------------------------------------------------------------------
# identity + real sample
# ---------------------------------------------------------------------------


def test_suite_identity() -> None:
    assert HumanEvalSuite.suite_id == "human-eval"
    assert HumanEvalSuite.suite_version == _SUITE_VERSION


def test_hf_pin_is_real() -> None:
    assert _SUITE_VERSION == "openai/openai_humaneval@7dce6050a7d6d172f3cc5c32aa97f52fa1a2e544"
    assert "@7dce6050" in _SUITE_VERSION


def test_sample_is_real_and_complete() -> None:
    sample = json.loads((_FIXTURES / "humaneval_sample.json").read_text())
    assert len(sample) >= 5
    required = {"task_id", "prompt", "canonical_solution", "test", "entry_point"}
    for p in sample:
        assert set(p) == required, f"{p.get('task_id')}: keys {sorted(p)}"
        assert p["task_id"].startswith("HumanEval/")
        assert "def check(" in p["test"]
        assert p["canonical_solution"].strip()
        assert p["entry_point"]


# ---------------------------------------------------------------------------
# executor (the code-execution substrate)
# ---------------------------------------------------------------------------


def test_build_program_assembles_prompt_completion_test_call() -> None:
    prob = {"prompt": "def f(x):\n", "test": "def check(c):\n    assert c(1)==2\n", "entry_point": "f"}
    prog = build_program(prob["prompt"], "    return x+1\n", prob["test"], prob["entry_point"])
    assert prog.startswith("def f(x):\n    return x+1\n")
    assert prog.rstrip().endswith("check(f)")


def test_executor_passes_a_correct_candidate() -> None:
    sample = json.loads((_FIXTURES / "humaneval_sample.json").read_text())
    prob = sample[0]
    out = run_candidate(prob, prob["canonical_solution"])
    assert out["passed"] is True
    assert out["error"] == ""


def test_executor_fails_a_wrong_candidate() -> None:
    sample = json.loads((_FIXTURES / "humaneval_sample.json").read_text())
    prob = sample[0]
    # A body that satisfies the signature but returns the wrong constant.
    wrong = "    return None\n"
    out = run_candidate(prob, wrong)
    assert out["passed"] is False
    assert out["error"]  # non-empty diagnostic


def test_executor_times_out_on_infinite_loop() -> None:
    prob = {
        "task_id": "synthetic/loop",
        "prompt": "def spin():\n",
        "test": "def check(c):\n    c()\n",
        "entry_point": "spin",
    }
    out = run_candidate(prob, "    while True:\n        pass\n", timeout_s=1.0)
    assert out["passed"] is False
    assert "timeout" in out["error"]


# ---------------------------------------------------------------------------
# suite paths: fixture replay vs reference execution
# ---------------------------------------------------------------------------


def test_mock_artifacts_replays_fixture(tmp_path: Path) -> None:
    ra = HumanEvalSuite().mock_artifacts({"_tmp_dir": str(tmp_path)})
    assert set(ra.manifest) == {"results", "summary"}
    results = json.loads(ra.path("results").read_text())
    assert len(results) == 6
    summary = json.loads(ra.path("summary").read_text())
    assert summary["executed"] is False


def test_reference_execution_runs_canonical_solutions() -> None:
    """execute=True really runs the gold solutions → every one passes."""
    ra = HumanEvalSuite().mock_artifacts({"execute": True})
    results = json.loads(ra.path("results").read_text())
    assert results, "no results produced"
    assert all(r["passed"] is True for r in results), [r for r in results if not r["passed"]]
    summary = json.loads(ra.path("summary").read_text())
    assert summary["executed"] is True
    assert summary["model"] == "reference-canonical"
    # real execution → real latency recorded, no LLM tokens
    assert all(isinstance(r["latency_ms"], float) for r in results)
    assert summary["prompt_tokens"] == 0


def test_reference_execution_respects_limit() -> None:
    ra = HumanEvalSuite().mock_artifacts({"execute": True, "limit": 2})
    results = json.loads(ra.path("results").read_text())
    assert len(results) == 2


def test_resolve_carries_execute_and_limit() -> None:
    dh = HumanEvalSuite().resolve({"limit": 3, "execute": True}, assets=None)
    assert dh.version == _SUITE_VERSION
    assert len(dh.payload["problems"]) == 3
    assert dh.payload["execute"] is True


# ---------------------------------------------------------------------------
# evaluator
# ---------------------------------------------------------------------------


def test_evaluator_emits_pass_at_1_and_serving_dims(tmp_path: Path) -> None:
    results = [
        {"task_id": "HumanEval/0", "passed": True, "latency_ms": 10.0},
        {"task_id": "HumanEval/1", "passed": False, "latency_ms": 20.0},
    ]
    (tmp_path / "results.json").write_text(json.dumps(results))
    (tmp_path / "summary.json").write_text(json.dumps({"prompt_tokens": 100, "completion_tokens": 50}))
    raw = RawArtifacts(
        dir=tmp_path,
        manifest={"results": {"path": "results.json"}, "summary": {"path": "summary.json"}},
    )
    out = OfficialHumanEvalEvaluator().evaluate(raw)
    assert out["human-eval.pass_at_1"].value == 0.5
    assert out["human-eval.pass_at_1"].reproducibility_class == "deterministic"
    assert out["human-eval.avg_latency_ms"].value == 15.0
    assert out["human-eval.total_tokens"].value == 150
    assert "human-eval.cost_usd" in out
    # every emitted key is namespaced + official (conformance contract)
    assert all(k.startswith("human-eval.") for k in out)
    assert all(m.official is True for m in out.values())


def test_evaluator_supports_only_human_eval() -> None:
    ev = OfficialHumanEvalEvaluator()
    assert ev.supports("human-eval", "any")
    assert not ev.supports("mmlu", "any")


def test_evaluator_omits_tokens_when_absent(tmp_path: Path) -> None:
    """Reference/fixture runs with no usage → no token/cost dims, no crash."""
    (tmp_path / "results.json").write_text(
        json.dumps([{"task_id": "HumanEval/0", "passed": True, "latency_ms": 5.0}])
    )
    (tmp_path / "summary.json").write_text(json.dumps({"prompt_tokens": 0, "completion_tokens": 0}))
    raw = RawArtifacts(
        dir=tmp_path,
        manifest={"results": {"path": "results.json"}, "summary": {"path": "summary.json"}},
    )
    out = OfficialHumanEvalEvaluator().evaluate(raw)
    assert set(out) == {"human-eval.pass_at_1", "human-eval.avg_latency_ms"}


def test_evaluator_fail_safe_on_missing_results(tmp_path: Path) -> None:
    raw = RawArtifacts(dir=tmp_path, manifest={"results": {"path": "nope.json"}})
    assert OfficialHumanEvalEvaluator().evaluate(raw) == {}


# ---------------------------------------------------------------------------
# fence stripping (real endpoints wrap code in ```python fences)
# ---------------------------------------------------------------------------


def test_extract_code_strips_python_fence() -> None:
    assert extract_code("```python\n    return 1\n```") == "    return 1"
    assert extract_code("```\n    return 1\n```") == "    return 1"


def test_extract_code_leaves_bare_body_untouched() -> None:
    body = "    return x + 1\n"
    assert extract_code(body) == body


def test_fenced_correct_completion_still_passes() -> None:
    """A correct solution wrapped in a ```python fence must NOT be mis-scored."""
    sample = json.loads((_FIXTURES / "humaneval_sample.json").read_text())
    prob = sample[0]
    fenced = f"```python\n{prob['canonical_solution']}```"
    out = run_candidate(prob, extract_code(fenced))
    assert out["passed"] is True


# ---------------------------------------------------------------------------
# SSRF endpoint guard
# ---------------------------------------------------------------------------


def test_validate_endpoint_allows_https_and_loopback() -> None:
    validate_endpoint("https://dashscope.aliyuncs.com/compatible-mode/v1")
    validate_endpoint("http://127.0.0.1:8000/v1")  # self-hosted gateway ok


def test_validate_endpoint_rejects_metadata_and_bad_scheme() -> None:
    for bad in (
        "http://169.254.169.254/latest/meta-data/",  # AWS/GCP/Azure (link-local)
        "http://100.100.100.200/latest/meta-data/",  # Alibaba Cloud metadata
        "http://metadata.google.internal/",
        "http://2852039166/",  # decimal-encoded 169.254.169.254
        "http://0xA9FEA9FE/",  # hex-encoded 169.254.169.254
        "http://169.254.169.254./",  # trailing-dot bypass
        "ftp://example.com/v1",
        "file:///etc/passwd",
    ):
        with pytest.raises(RuntimeError):
            validate_endpoint(bad)


# ---------------------------------------------------------------------------
# secret-stripped execution environment
# ---------------------------------------------------------------------------


def test_executor_strips_secrets_from_child_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Untrusted code must not see the operator's API keys in os.environ."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-super-secret")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-secret")
    prob = {
        "task_id": "synthetic/env",
        "prompt": "import os\ndef leak():\n",
        # test passes only if NEITHER secret is visible to the child
        "test": (
            "def check(c):\n"
            "    import os\n"
            "    assert 'OPENAI_API_KEY' not in os.environ\n"
            "    assert 'DASHSCOPE_API_KEY' not in os.environ\n"
            "    c()\n"
        ),
        "entry_point": "leak",
    }
    out = run_candidate(prob, "    return None\n")
    assert out["passed"] is True, out["error"]


# ---------------------------------------------------------------------------
# endpoint run() path — mocked requests + opt-in gate
# ---------------------------------------------------------------------------


class _StubAdapter:
    def __init__(self, model: str = "test-model", key: str = "k") -> None:
        self._m, self._k = model, key

    def model(self) -> str:
        return self._m

    def api_key(self) -> str:
        return self._k


def _endpoint_env(suite: HumanEvalSuite, *, allow_exec: bool, limit: int = 1) -> EnvHandle:
    dataset = suite.resolve({"limit": limit, "allow_code_execution": allow_exec}, assets=None)
    target = Target(mode="endpoint", mock=False, handle=_StubAdapter(), endpoint="https://api.example.com/v1")
    return suite.prepare(target, dataset, DriverContext(placement="local"))


def test_endpoint_requires_opt_in() -> None:
    suite = HumanEvalSuite()
    env = _endpoint_env(suite, allow_exec=False)
    target = Target(mode="endpoint", mock=False, handle=_StubAdapter(), endpoint="https://api.example.com/v1")
    with pytest.raises(RuntimeError, match="allow_code_execution"):
        suite.run(target, env, DriverContext(placement="local"))


def test_endpoint_run_executes_mocked_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end endpoint path with requests mocked: fenced completion is
    unwrapped, executed, scored, and tokens/truncation land in the summary."""
    suite = HumanEvalSuite()
    sample = json.loads((_FIXTURES / "humaneval_sample.json").read_text())
    gold = sample[0]["canonical_solution"]

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [{"message": {"content": f"```python\n{gold}```"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 12},
            }

    import requests

    captured: dict = {}

    def _fake_post(url, json, headers, timeout, **kwargs):  # noqa: A002
        captured["url"] = url
        captured["auth"] = headers.get("Authorization")
        captured["allow_redirects"] = kwargs.get("allow_redirects")
        return _Resp()

    monkeypatch.setattr(requests, "post", _fake_post)
    env = _endpoint_env(suite, allow_exec=True, limit=1)
    target = Target(mode="endpoint", mock=False, handle=_StubAdapter(), endpoint="https://api.example.com/v1")
    raw = suite.run(target, env, DriverContext(placement="local"))
    results = json.loads(raw.path("results").read_text())
    summary = json.loads(raw.path("summary").read_text())
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["auth"] == "Bearer k"
    assert captured["allow_redirects"] is False  # key never follows a redirect (SSRF)
    assert results[0]["passed"] is True  # fenced gold executed correctly
    assert summary["prompt_tokens"] == 40
    assert summary["completion_tokens"] == 12
    assert summary["truncated"] == 0
