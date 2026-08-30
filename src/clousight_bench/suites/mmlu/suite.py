"""MMLU benchmark suite plugin (llm domain).

Registers as the ``mmlu`` suite under the ``clousight_bench.benchmark_suites``
entry-point group. Runs the recognized **MMLU** multiple-choice benchmark
(cais/mmlu, MIT) against a managed LLM endpoint and scores objective accuracy +
serving dimensions (latency, token cost).

A small real MMLU sample ships as a fixture (``fixtures/mmlu_sample.json``, MIT)
so the offline mock path and the reference platform need no network. The real
``run()`` path (``llm-endpoint``) POSTs to an OpenAI-compatible
``/chat/completions``; ``mock_artifacts()`` / ``resolve()`` need nothing.

We run MMLU unmodified and report objective accuracy (deterministic) — no
subjective judging.
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
_SAMPLE_FILE = _FIXTURES_DIR / "mmlu_sample.json"

# Pins the bundled MMLU sample (cais/mmlu, MIT). The full-dataset fetch path is a
# future enhancement; bump when the sample changes.
_SUITE_VERSION = "cais-mmlu/sample-v1"

_LETTERS = ["A", "B", "C", "D"]


def _load_sample() -> list[dict[str, Any]]:
    return json.loads(_SAMPLE_FILE.read_text(encoding="utf-8"))


def format_prompt(q: dict[str, Any]) -> str:
    """The standard 0-shot MMLU prompt: question + lettered choices, ask for a letter."""
    lines = [
        "The following is a multiple choice question. Reply with ONLY the letter "
        "(A, B, C, or D) of the correct answer.",
        "",
        q["question"],
    ]
    for letter, choice in zip(_LETTERS, q["choices"]):
        lines.append(f"{letter}. {choice}")
    lines.append("Answer:")
    return "\n".join(lines)


def parse_letter(text: str) -> int | None:
    """Extract the first STANDALONE A–D answer letter → choice index (0-3).

    Word-boundary matching so a letter embedded in a word (e.g. the 'A' in
    "Answer") is not mistaken for the answer; "The answer is C." → C.
    """
    m = re.search(r"\b([A-D])\b", (text or "").strip().upper())
    return _LETTERS.index(m.group(1)) if m else None


class MmluSuite(BenchmarkSuite):
    """MMLU on the llm domain. Runs the recognized benchmark against an LLM endpoint."""

    suite_id: str = "mmlu"
    suite_version: str = _SUITE_VERSION

    # ------------------------------------------------------------------ resolve
    def resolve(self, cfg: dict[str, Any], assets: Any) -> DatasetHandle:  # noqa: ARG002
        """Pick the subject filter + question limit (offline; reads the bundled sample)."""
        sample = _load_sample()
        subjects = cfg.get("subjects")
        if subjects:
            wanted = set(subjects)
            sample = [q for q in sample if q["subject"] in wanted]
        limit = int(cfg.get("limit", len(sample)))
        selected = sample[:limit]
        ids = [q["id"] for q in selected]
        canonical = json.dumps({"ids": ids, "version": self.suite_version}, sort_keys=True)
        return DatasetHandle(
            version=self.suite_version,
            digest=sha256_bytes(canonical.encode()),
            payload={"questions": selected},
        )

    # ------------------------------------------------------------------ prepare
    def prepare(self, target: Target, dataset: DatasetHandle, driver: DriverContext) -> EnvHandle:  # noqa: ARG002
        """Resolve the endpoint/model/key (mock → empty EnvHandle)."""
        if target.mock:
            return EnvHandle({"mock": True})
        endpoint, model, api_key = resolve_endpoint(target, suite_id="mmlu")
        return EnvHandle(
            {
                "mock": False,
                "endpoint": endpoint,
                "model": model,
                "api_key": api_key,
                "questions": list(dataset.payload["questions"]),
            }
        )

    # ---------------------------------------------------------------------- run
    def run(self, target: Target, env: EnvHandle, driver: DriverContext) -> RawArtifacts:  # noqa: ARG002
        """Query the LLM endpoint per question; record answer + latency + tokens."""
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
                max_tokens=8,
            )
            latency_ms = (perf_counter() - t) * 1000.0
            prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
            completion_tokens += int(usage.get("completion_tokens", 0) or 0)
            predicted = parse_letter(content)
            answers.append(
                {
                    "id": q["id"],
                    "subject": q["subject"],
                    "predicted": predicted,
                    "gold": int(q["answer"]),
                    "correct": predicted == int(q["answer"]),
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
        tmp_dir = Path(tempfile.mkdtemp(prefix="csbench-mmlu-art-"))
        return write_artifacts(tmp_dir, answers, summary, rows_key="answers")

    # ----------------------------------------------------------------- teardown
    def teardown(self, env: EnvHandle) -> None:  # noqa: ARG002, B027
        """No persistent resources (stateless endpoint calls). No-op."""

    # ------------------------------------------------------------ mock_artifacts
    def mock_artifacts(self, cfg: dict[str, Any]) -> RawArtifacts:  # noqa: ARG002
        """Copy the bundled mock answer fixture — no network, no model."""
        tmp_dir = Path(tempfile.mkdtemp(prefix="csbench-mmlu-mock-"))
        answers = json.loads((_FIXTURES_DIR / "mock" / "answers.json").read_text())
        summary = json.loads((_FIXTURES_DIR / "mock" / "summary.json").read_text())
        return write_artifacts(tmp_dir, answers, summary, rows_key="answers")
