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

import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from clousight_bench.core.suite import (
    BenchmarkSuite,
    DatasetHandle,
    DriverContext,
    EnvHandle,
    RawArtifacts,
    Target,
)

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_SAMPLE_FILE = _FIXTURES_DIR / "mmlu_sample.json"

# Pins the bundled MMLU sample (cais/mmlu, MIT). The full-dataset fetch path is a
# future enhancement; bump when the sample changes.
_SUITE_VERSION = "cais-mmlu/sample-v1"

_LETTERS = ["A", "B", "C", "D"]


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


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


def _write_artifacts(tmp_dir: Path, answers: list[dict[str, Any]], summary: dict[str, Any]) -> RawArtifacts:
    a_path = tmp_dir / "answers.json"
    s_path = tmp_dir / "summary.json"
    a_path.write_text(json.dumps(answers), encoding="utf-8")
    s_path.write_text(json.dumps(summary), encoding="utf-8")
    manifest: dict[str, dict[str, Any]] = {
        "answers": {
            "path": "answers.json",
            "sha256": _sha256_bytes(a_path.read_bytes()),
            "rows": len(answers),
        },
        "summary": {"path": "summary.json", "sha256": _sha256_bytes(s_path.read_bytes()), "rows": None},
    }
    return RawArtifacts(dir=tmp_dir, manifest=manifest)


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
            digest=_sha256_bytes(canonical.encode()),
            payload={"questions": selected},
        )

    # ------------------------------------------------------------------ prepare
    def prepare(self, target: Target, dataset: DatasetHandle, driver: DriverContext) -> EnvHandle:  # noqa: ARG002
        """Resolve the endpoint/model/key (mock → empty EnvHandle)."""
        if target.mock:
            return EnvHandle({"mock": True})
        handle = target.handle
        model = str(handle.model()) if handle is not None and hasattr(handle, "model") else ""
        api_key = str(handle.api_key()) if handle is not None and hasattr(handle, "api_key") else ""
        endpoint = str(target.endpoint or "")
        if not endpoint or not model:
            raise RuntimeError(
                "the mmlu real run() path needs target.endpoint (OpenAI-compatible base URL) + target.model"
            )
        return EnvHandle(
            {
                "mock": False,
                "endpoint": endpoint.rstrip("/"),
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
        from time import perf_counter  # noqa: PLC0415

        import requests  # noqa: PLC0415 - lazy; only the real path needs it

        p = env.payload
        url = f"{p['endpoint']}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if p["api_key"]:
            headers["Authorization"] = f"Bearer {p['api_key']}"
        answers: list[dict[str, Any]] = []
        prompt_tokens = completion_tokens = 0
        for q in p["questions"]:
            body = {
                "model": p["model"],
                "messages": [{"role": "user", "content": format_prompt(q)}],
                "temperature": 0,
                "max_tokens": 8,
            }
            t = perf_counter()
            resp = requests.post(url, json=body, headers=headers, timeout=60)
            latency_ms = (perf_counter() - t) * 1000.0
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {}) or {}
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
        return _write_artifacts(tmp_dir, answers, summary)

    # ----------------------------------------------------------------- teardown
    def teardown(self, env: EnvHandle) -> None:  # noqa: ARG002, B027
        """No persistent resources (stateless endpoint calls). No-op."""

    # ------------------------------------------------------------ mock_artifacts
    def mock_artifacts(self, cfg: dict[str, Any]) -> RawArtifacts:  # noqa: ARG002
        """Copy the bundled mock answer fixture — no network, no model."""
        tmp_dir = Path(tempfile.mkdtemp(prefix="csbench-mmlu-mock-"))
        answers = json.loads((_FIXTURES_DIR / "mock" / "answers.json").read_text())
        summary = json.loads((_FIXTURES_DIR / "mock" / "summary.json").read_text())
        return _write_artifacts(tmp_dir, answers, summary)
