"""HumanEval benchmark suite plugin (llm domain).

Registers as the ``human-eval`` suite under the
``clousight_bench.benchmark_suites`` entry-point group.  Runs the recognized
**HumanEval** code-generation benchmark (openai/openai_humaneval, MIT): the SUT
produces a function body for each problem's signature+docstring, and correctness
is scored by EXECUTING ``prompt + completion`` together with the problem's
self-contained ``check(candidate)`` unit test.  The canonical ``pass@1`` metric
is the fraction of problems whose test passes.

A small real HumanEval sample ships as a fixture (``fixtures/humaneval_sample.json``,
MIT), so both the offline mock path and a reference execution need no network.

Three paths, by adapter + a ``params.execute`` flag:

- ``llm-mock`` (default): replay the bundled pre-scored fixture — no execution,
  no model.  This is the conformance / CI-smoke path.
- ``llm-mock`` + ``params.execute=true``: REALLY execute the dataset's own
  CANONICAL solutions through the sandboxed executor and score them live — no
  model needed, safe (vetted bundled code), and proves the execution+scoring
  harness genuinely works.  CI runs this too.
- ``llm-endpoint``: query an OpenAI-compatible endpoint for each completion, then
  execute the MODEL's code.  Model output is untrusted — see
  :mod:`clousight_bench.suites.human_eval.executor` for the isolation caveat; run
  it in your own isolated environment.
"""

from __future__ import annotations

import json
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
from clousight_bench.suites.human_eval.executor import run_candidate
from clousight_bench.suites.llm_common import (
    chat_once,
    extract_code,
    resolve_endpoint,
    sha256_bytes,
    validate_endpoint,
    write_artifacts,
)

# Re-exported for callers/tests that import them from this suite module; the
# implementations live in the shared llm module.
__all__ = ["HumanEvalSuite", "extract_code", "validate_endpoint", "format_prompt"]

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_SAMPLE_FILE = _FIXTURES_DIR / "humaneval_sample.json"

# Pins the bundled HumanEval sample (openai/openai_humaneval, MIT) to the real
# dataset main-commit. The full-164-problem fetch path is a future enhancement.
_SUITE_VERSION = "openai/openai_humaneval@7dce6050a7d6d172f3cc5c32aa97f52fa1a2e544"


def _load_sample() -> list[dict[str, Any]]:
    return json.loads(_SAMPLE_FILE.read_text(encoding="utf-8"))


def format_prompt(problem: dict[str, Any]) -> str:
    """The standard HumanEval completion prompt: the signature + docstring verbatim.

    The model is asked to continue the function body; only its completion (not the
    echoed prompt) is scored, matching the upstream harness.
    """
    return (
        "Complete the following Python function. Reply with ONLY the function body "
        "code that continues the signature (no explanation, no markdown fences):\n\n" + problem["prompt"]
    )


class HumanEvalSuite(BenchmarkSuite):
    """HumanEval on the llm domain — executes candidate code against unit tests."""

    suite_id: str = "human-eval"
    suite_version: str = _SUITE_VERSION

    # ------------------------------------------------------------------ resolve
    def resolve(self, cfg: dict[str, Any], assets: Any) -> DatasetHandle:  # noqa: ARG002
        """Pick the problem limit (offline; reads the bundled sample)."""
        sample = _load_sample()
        limit = int(cfg.get("limit", len(sample)))
        selected = sample[:limit]
        ids = [p["task_id"] for p in selected]
        canonical = json.dumps({"ids": ids, "version": self.suite_version}, sort_keys=True)
        return DatasetHandle(
            version=self.suite_version,
            digest=sha256_bytes(canonical.encode()),
            payload={
                "problems": selected,
                "execute": bool(cfg.get("execute", False)),
                "allow_code_execution": bool(cfg.get("allow_code_execution", False)),
            },
        )

    # ------------------------------------------------------------------ prepare
    def prepare(self, target: Target, dataset: DatasetHandle, driver: DriverContext) -> EnvHandle:  # noqa: ARG002
        """Resolve endpoint/model/key for the real path; carry the execute flag."""
        problems = list(dataset.payload["problems"])
        execute = bool(dataset.payload.get("execute", False))
        allow_exec = bool(dataset.payload.get("allow_code_execution", False))
        if target.mock:
            return EnvHandle({"mock": True, "problems": problems, "execute": execute})
        endpoint, model, api_key = resolve_endpoint(target, suite_id="human-eval")
        return EnvHandle(
            {
                "mock": False,
                "endpoint": endpoint,
                "model": model,
                "api_key": api_key,
                "problems": problems,
                "allow_code_execution": allow_exec,
            }
        )

    # ---------------------------------------------------------------------- run
    def run(self, target: Target, env: EnvHandle, driver: DriverContext) -> RawArtifacts:  # noqa: ARG002
        """Endpoint mode → query the LLM then execute the completions.

        Mock mode does not reach here (the orchestrator calls ``mock_artifacts``
        directly); the guard below keeps direct callers/tests correct and routes
        the ``execute`` flag to ``mock_artifacts``.
        """
        p = env.payload
        if target.mock or p.get("mock"):
            return self.mock_artifacts(dict(p))

        # Endpoint mode executes UNTRUSTED model-generated code locally. Require
        # an explicit opt-in so choosing the endpoint platform is never an
        # accidental "run arbitrary code on my machine" (the sandbox is
        # defence-in-depth, not a container — see executor.py).
        if not p.get("allow_code_execution"):
            raise RuntimeError(
                "human-eval endpoint mode executes untrusted model-generated code to score it; "
                "set params.allow_code_execution: true to acknowledge, and run in an isolated "
                "environment (the sandbox bounds CPU/mem/output but not filesystem/network)."
            )

        completions: list[str] = []
        prompt_tokens = completion_tokens = truncated = 0
        for prob in p["problems"]:
            content, usage, finish_reason = chat_once(
                endpoint=p["endpoint"],
                model=p["model"],
                api_key=p["api_key"],
                prompt=format_prompt(prob),
                max_tokens=1024,
            )
            completions.append(extract_code(content))
            if finish_reason == "length":
                truncated += 1  # completion was cut at max_tokens — visible in summary
            prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
            completion_tokens += int(usage.get("completion_tokens", 0) or 0)
        return self._execute_run(
            p["problems"],
            completions=completions,
            model=p["model"],
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            extra_summary={"truncated": truncated},
        )

    def _execute_run(
        self,
        problems: list[dict[str, Any]],
        *,
        completions: list[str],
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        extra_summary: dict[str, Any] | None = None,
    ) -> RawArtifacts:
        """Execute each ``(problem, completion)`` in the sandbox and write artifacts."""
        results: list[dict[str, Any]] = []
        # strict=True: one completion per problem is an invariant of both callers;
        # a length mismatch is a bug, not something to silently truncate.
        for prob, completion in zip(problems, completions, strict=True):
            t = perf_counter()
            outcome = run_candidate(prob, completion)
            outcome["latency_ms"] = (perf_counter() - t) * 1000.0
            results.append(outcome)
        summary = {
            "model": model,
            "suite_version": self.suite_version,
            "problem_count": len(results),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "executed": True,
            **(extra_summary or {}),
        }
        tmp_dir = Path(tempfile.mkdtemp(prefix="csbench-humaneval-art-"))
        return write_artifacts(tmp_dir, results, summary, rows_key="results")

    # ----------------------------------------------------------------- teardown
    def teardown(self, env: EnvHandle) -> None:  # noqa: ARG002, B027
        """No persistent resources (subprocess exec is self-cleaning). No-op."""

    # ------------------------------------------------------------ mock_artifacts
    def mock_artifacts(self, cfg: dict[str, Any]) -> RawArtifacts:
        """Offline path — no cloud, no model.

        Default: replay the bundled pre-scored fixture (no execution) — this is
        the conformance / CI-smoke path.  With ``cfg["execute"]`` truthy, REALLY
        execute the dataset's own CANONICAL solutions through the sandboxed
        executor and score them live (safe: vetted bundled code, no LLM); this is
        how the execution+scoring harness is exercised without a paid endpoint.
        """
        if cfg.get("execute"):
            sample = _load_sample()
            limit = int(cfg.get("limit", len(sample)))
            problems = sample[:limit]
            return self._execute_run(
                problems,
                completions=[p["canonical_solution"] for p in problems],
                model="reference-canonical",
                prompt_tokens=0,
                completion_tokens=0,
            )
        tmp_dir = Path(tempfile.mkdtemp(prefix="csbench-humaneval-mock-"))
        results = json.loads((_FIXTURES_DIR / "mock" / "results.json").read_text())
        summary = json.loads((_FIXTURES_DIR / "mock" / "summary.json").read_text())
        return write_artifacts(tmp_dir, results, summary, rows_key="results")
