"""Response-quality metric — the first real judge-based metric.

An LLM-as-judge rates a SUT response against the task input on a fixed
categorical rubric; the verdict maps to a score by fixed arithmetic (no logprob
weighting → reproducible-by-construction given the verdict). Optional
self-consistency: sample the judge N times and take the majority verdict.

This metric is judge-based (``reproducibility_class="judge-based"``): it needs a
:class:`JudgeModel` on the :class:`MetricContext`. With no judge configured it
returns ``status="skip"`` for every item — so it is safe to bind/run offline
(e.g. in CI) where no judge is available; supply ``ctx.judge`` (an
``EndpointJudge`` for a real run, a recorded/mock judge in tests) to score.
"""

from __future__ import annotations

from collections import Counter

from clousight_bench.core.judge import judge_emit
from clousight_bench.core.metric import Metric, MetricContext
from clousight_bench.core.observation import ItemResult, ItemScore

# Fixed categorical rubric → score. Deterministic given the verdict.
_RUBRIC: dict[str, float] = {"excellent": 1.0, "good": 0.75, "fair": 0.5, "poor": 0.25}
_VERDICT_SCHEMA = {
    "type": "object",
    "required": ["verdict"],
    "properties": {"verdict": {"enum": sorted(_RUBRIC)}, "rationale": {"type": "string"}},
}


def _extract_verdict(data: dict) -> tuple[str, str]:
    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in _RUBRIC:
        raise ValueError(f"judge returned out-of-rubric verdict {verdict!r}; allowed: {sorted(_RUBRIC)}")
    return verdict, str(data.get("rationale", ""))


def _prompt(item: ItemResult) -> str:
    task = "" if item.input is None else str(item.input)
    return (
        "You are a strict evaluator. Rate the RESPONSE to the TASK on this rubric: "
        "excellent / good / fair / poor. Reply with ONLY a JSON object "
        '{"verdict": "<one of the four>", "rationale": "<one sentence>"}.\n\n'
        f"TASK:\n{task}\n\nRESPONSE:\n{item.output}\n"
    )


class ResponseQualityMetric(Metric):
    metric_id = "response_quality"
    reproducibility_class = "judge-based"
    unit = "ratio"
    default_how = "mean"
    required_inputs = ("output",)

    def score_item(self, item: ItemResult, ctx: MetricContext) -> ItemScore | None:
        judge = ctx.judge
        if judge is None:
            return ItemScore(metric=self.metric_id, value=0.0, status="skip", reason="no judge configured")
        samples = max(1, int(ctx.params.get("judge_samples", 1)))
        prompt = _prompt(item)
        verdicts: list[str] = []
        rationale = ""
        for _ in range(samples):
            verdict, why = judge_emit(judge, prompt, _VERDICT_SCHEMA, _extract_verdict)
            verdicts.append(verdict)
            rationale = rationale or why
        # self-consistency: majority verdict. Counter.most_common breaks ties by
        # first-seen order (deterministic within a run, given the judge outputs).
        winner = Counter(verdicts).most_common(1)[0][0]
        return ItemScore(
            metric=self.metric_id,
            value=_RUBRIC[winner],
            status="ok",
            reason=f"{judge.model_id()} verdict={winner} (n={samples}): {rationale}"[:500],
        )
