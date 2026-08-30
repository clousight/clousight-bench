"""GSM8K benchmark suite plugin (llm domain).

Runs the recognized **GSM8K** grade-school math word-problem benchmark
(openai/gsm8k, MIT) against a managed LLM endpoint and scores objective accuracy
(exact numeric match) + serving dimensions. Sibling of the ``mmlu`` suite on the
same ``llm`` domain; a small real sample ships as a fixture so the mock/reference
paths need no network. The real ``run()`` POSTs to an OpenAI-compatible
``/chat/completions``.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from time import perf_counter
from typing import Any

from clousight_bench.core.suite import (
    BenchmarkSuite,
    DatasetHandle,
    DriverContext,
    EnvHandle,
    RawArtifacts,
    Target,
)
from clousight_bench.suites._llm_shared import (
    chat_once,
    resolve_endpoint,
    sha256_bytes,
    write_artifacts,
)

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_SAMPLE_FILE = _FIXTURES_DIR / "gsm8k_sample.json"
_SUITE_VERSION = "openai-gsm8k/sample-v1"


def _load_sample() -> list[dict[str, Any]]:
    return json.loads(_SAMPLE_FILE.read_text(encoding="utf-8"))


def format_prompt(q: dict[str, Any]) -> str:
    """0-shot GSM8K prompt: solve, end with the final number after '#### '."""
    return (
        "Solve the math problem. Show brief reasoning, then on the last line write "
        "the final answer as: #### <number>\n\n" + q["question"]
    )


_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def parse_number(text: str) -> str | None:
    """Extract the final numeric answer (prefer after '####'; else the last number)."""
    if not text:
        return None
    tail = text.split("####")[-1] if "####" in text else text
    nums = _NUM.findall(tail) or _NUM.findall(text)
    if not nums:
        return None
    return nums[-1].replace(",", "")


def _numeric_equal(a: str, b: str) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return a.strip() == b.strip()


class Gsm8kSuite(BenchmarkSuite):
    """GSM8K on the llm domain."""

    suite_id: str = "gsm8k"
    suite_version: str = _SUITE_VERSION

    def resolve(self, cfg: dict[str, Any], assets: Any) -> DatasetHandle:  # noqa: ARG002
        sample = _load_sample()
        limit = int(cfg.get("limit", len(sample)))
        selected = sample[:limit]
        canonical = json.dumps(
            {"ids": [q["id"] for q in selected], "version": self.suite_version}, sort_keys=True
        )
        return DatasetHandle(
            version=self.suite_version,
            digest=sha256_bytes(canonical.encode()),
            payload={"questions": selected},
        )

    def prepare(self, target: Target, dataset: DatasetHandle, driver: DriverContext) -> EnvHandle:  # noqa: ARG002
        if target.mock:
            return EnvHandle({"mock": True})
        endpoint, model, api_key = resolve_endpoint(target, suite_id="gsm8k")
        return EnvHandle(
            {
                "mock": False,
                "endpoint": endpoint,
                "model": model,
                "api_key": api_key,
                "questions": list(dataset.payload["questions"]),
            }
        )

    def run(self, target: Target, env: EnvHandle, driver: DriverContext) -> RawArtifacts:  # noqa: ARG002
        if target.mock or env.payload.get("mock"):
            return self.mock_artifacts(dict(env.payload))
        p = env.payload
        answers: list[dict[str, Any]] = []
        prompt_tokens = completion_tokens = 0
        for q in p["questions"]:
            t = perf_counter()
            content, usage, _ = chat_once(
                endpoint=p["endpoint"],
                model=p["model"],
                api_key=p["api_key"],
                prompt=format_prompt(q),
                max_tokens=512,
            )
            latency_ms = (perf_counter() - t) * 1000.0
            prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
            completion_tokens += int(usage.get("completion_tokens", 0) or 0)
            predicted = parse_number(content)
            answers.append(
                {
                    "id": q["id"],
                    "predicted": predicted,
                    "gold": q["gold"],
                    "correct": predicted is not None and _numeric_equal(predicted, q["gold"]),
                    "latency_ms": latency_ms,
                }
            )
        summary = {
            "model": p["model"],
            "suite_version": self.suite_version,
            "question_count": len(answers),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }
        tmp_dir = Path(tempfile.mkdtemp(prefix="csbench-gsm8k-art-"))
        return write_artifacts(tmp_dir, answers, summary, rows_key="answers")

    def teardown(self, env: EnvHandle) -> None:  # noqa: ARG002, B027
        """Stateless endpoint calls — no-op."""

    def mock_artifacts(self, cfg: dict[str, Any]) -> RawArtifacts:  # noqa: ARG002
        tmp_dir = Path(tempfile.mkdtemp(prefix="csbench-gsm8k-mock-"))
        answers = json.loads((_FIXTURES_DIR / "mock" / "answers.json").read_text())
        summary = json.loads((_FIXTURES_DIR / "mock" / "summary.json").read_text())
        return write_artifacts(tmp_dir, answers, summary, rows_key="answers")
