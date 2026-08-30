"""R6: content-addressed judge cache — reuse verdicts, never re-pay LLM calls."""

from __future__ import annotations

from pathlib import Path

from clousight_bench.core.judge import CachingJudge, JudgeModel, judge_emit
from clousight_bench.core.registry import build_judge


class _CountingJudge(JudgeModel):
    def __init__(self, model: str = "m") -> None:
        self._model = model
        self.calls = 0

    def model_id(self) -> str:
        return self._model

    def generate(self, prompt: str) -> str:
        self.calls += 1
        return f'{{"verdict": "good", "for": "{prompt}"}}'


def test_cache_hits_skip_the_inner_call(tmp_path: Path) -> None:
    inner = _CountingJudge()
    cache = tmp_path / "jc.json"
    j = CachingJudge(inner, cache)
    a = j.generate("p1")
    b = j.generate("p1")  # cache hit
    assert a == b
    assert inner.calls == 1  # inner called only once
    assert cache.exists()


def test_cache_misses_on_different_prompt_or_model(tmp_path: Path) -> None:
    inner = _CountingJudge()
    j = CachingJudge(inner, tmp_path / "jc.json")
    j.generate("p1")
    j.generate("p2")  # different prompt → miss
    assert inner.calls == 2
    # different model id → different key even for the same prompt
    other = CachingJudge(_CountingJudge(model="other"), tmp_path / "jc.json")
    other.generate("p1")
    assert other._inner.calls == 1  # noqa: SLF001 - white-box: not served from m's entry


def test_cache_persists_across_instances(tmp_path: Path) -> None:
    cache = tmp_path / "jc.json"
    inner1 = _CountingJudge()
    CachingJudge(inner1, cache).generate("p1")
    assert inner1.calls == 1
    # a fresh wrapper over a fresh inner, same file → served from disk, no inner call
    inner2 = _CountingJudge()
    out = CachingJudge(inner2, cache).generate("p1")
    assert inner2.calls == 0
    assert '"verdict": "good"' in out


def test_corrupt_cache_file_is_discarded_not_fatal(tmp_path: Path) -> None:
    cache = tmp_path / "jc.json"
    cache.write_text("{not json", encoding="utf-8")
    inner = _CountingJudge()
    out = CachingJudge(inner, cache).generate("p1")  # no crash; recomputes
    assert inner.calls == 1 and out


def test_caching_judge_works_through_judge_emit(tmp_path: Path) -> None:
    inner = _CountingJudge()
    j = CachingJudge(inner, tmp_path / "jc.json")
    for _ in range(3):
        v = judge_emit(j, "rate this", {}, lambda d: d["verdict"])
        assert v == "good"
    assert inner.calls == 1  # 3 emits, 1 real call


def test_build_judge_wraps_in_cache_when_configured(tmp_path: Path) -> None:
    cache = tmp_path / "jc.json"
    j = build_judge(
        {
            "provider": "openai-compatible",
            "endpoint": "https://api.example.com/v1",
            "model": "m",
            "cache": str(cache),
        }
    )
    assert isinstance(j, CachingJudge)
    # no cache key → plain judge (not wrapped)
    j2 = build_judge(
        {"provider": "openai-compatible", "endpoint": "https://api.example.com/v1", "model": "m"}
    )
    assert not isinstance(j2, CachingJudge)
