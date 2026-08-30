"""R4: judge base (JudgeModel + judge_emit + repair) and the response_quality metric."""

from __future__ import annotations

import pytest

from clousight_bench.core.judge import JudgeError, JudgeModel, _trim_and_load_json, judge_emit
from clousight_bench.core.metric import MetricContext
from clousight_bench.core.metric_runner import run_metrics
from clousight_bench.core.observation import ItemResult
from clousight_bench.core.registry import load_metrics
from clousight_bench.metrics.response_quality import ResponseQualityMetric


class _ScriptedJudge(JudgeModel):
    """Returns canned replies in order (offline stand-in for a real judge)."""

    def __init__(self, replies: list[str], model: str = "mock-judge") -> None:
        self._replies = list(replies)
        self._model = model
        self.calls = 0

    def model_id(self) -> str:
        return self._model

    def generate(self, prompt: str) -> str:  # noqa: ARG002
        r = self._replies[self.calls % len(self._replies)]
        self.calls += 1
        return r


class _NativeJudge(JudgeModel):
    """Advertises native json-schema mode (judge_emit takes the native path)."""

    def model_id(self) -> str:
        return "native"

    def generate(self, prompt: str) -> str:  # noqa: ARG002
        raise AssertionError("native path must not call generate()")

    def generate_schema(self, prompt, schema):  # noqa: ARG002
        return {"verdict": "good", "rationale": "structured"}

    def capabilities(self):
        return {"json_schema": True, "logprobs": False}


# --- _trim_and_load_json / judge_emit ----------------------------------------


def test_trim_and_load_json_slices_prose_and_repairs_trailing_comma() -> None:
    text = 'Sure! Here is my verdict:\n```json\n{"verdict": "good", "n": 1,}\n```\nthanks'
    assert _trim_and_load_json(text) == {"verdict": "good", "n": 1}


def test_trim_and_load_json_raises_on_no_object() -> None:
    with pytest.raises(JudgeError):
        _trim_and_load_json("no json here")
    with pytest.raises(JudgeError):
        _trim_and_load_json("{not valid}")


def test_judge_emit_fallback_path_parses_text() -> None:
    judge = _ScriptedJudge(['{"verdict": "fair"}'])
    out = judge_emit(judge, "p", {}, lambda d: d["verdict"])
    assert out == "fair"
    assert judge.calls == 1


def test_judge_emit_native_path_used_when_supported() -> None:
    out = judge_emit(_NativeJudge(), "p", {}, lambda d: d["verdict"])
    assert out == "good"


# --- response_quality metric --------------------------------------------------


def _item(output: str = "an answer", input_: str = "a question") -> ItemResult:
    return ItemResult(item_id="q1", input=input_, output=output)


def test_response_quality_skips_without_judge() -> None:
    score = ResponseQualityMetric().score_item(_item(), MetricContext())
    assert score is not None and score.status == "skip"


def test_response_quality_scores_via_judge() -> None:
    judge = _ScriptedJudge(['{"verdict": "excellent", "rationale": "great"}'])
    score = ResponseQualityMetric().score_item(_item(), MetricContext(judge=judge))
    assert score is not None and score.status == "ok"
    assert score.value == 1.0
    assert "mock-judge" in score.reason and "excellent" in score.reason


def test_response_quality_self_consistency_majority() -> None:
    # 3 samples: good, good, poor → majority good (0.75)
    judge = _ScriptedJudge(['{"verdict":"good"}', '{"verdict":"good"}', '{"verdict":"poor"}'])
    ctx = MetricContext(judge=judge, params={"judge_samples": 3})
    score = ResponseQualityMetric().score_item(_item(), ctx)
    assert score is not None and score.value == 0.75
    assert judge.calls == 3


def test_response_quality_out_of_rubric_verdict_isolated_as_error() -> None:
    """A junk verdict raises inside extract → the runner isolates it as error."""
    judge = _ScriptedJudge(['{"verdict": "spectacular"}'])
    items = [_item()]
    out_items, ms = run_metrics(
        items, [ResponseQualityMetric()], namespace="x", ctx=MetricContext(judge=judge)
    )
    score = out_items[0].scores[0]
    assert score.status == "error" and "out-of-rubric" in score.error
    assert "x.response_quality" not in ms  # all-error → no aggregate


def test_response_quality_end_to_end_judge_based_measurement() -> None:
    """Full path: items → judge metric via runner → a judge-based Measurement."""
    judge = _ScriptedJudge(['{"verdict":"good"}', '{"verdict":"fair"}'])
    items = [_item(output="a"), _item(output="b")]
    _, ms = run_metrics(items, [ResponseQualityMetric()], namespace="llm", ctx=MetricContext(judge=judge))
    m = ms["llm.response_quality"]
    assert m.reproducibility_class == "judge-based"
    assert m.value == pytest.approx((0.75 + 0.5) / 2)
    assert m.sample_count == 2


def test_response_quality_registered() -> None:
    assert "response_quality" in load_metrics()


# --- EndpointJudge SSRF guard -------------------------------------------------


def test_endpoint_judge_ssrf_guard_on_construction() -> None:
    from clousight_bench.suites._llm_shared import EndpointJudge

    with pytest.raises(RuntimeError):
        EndpointJudge(endpoint="http://169.254.169.254/v1", model="m")
    # a public https endpoint constructs fine (no network call at construction)
    j = EndpointJudge(endpoint="https://api.example.com/v1", model="m")
    assert j.model_id() == "m"
