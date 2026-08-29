"""LLM (foundation-model serving) domain pack.

Benchmarks the *managed large language model* a cloud vendor sells (Bedrock,
DashScope/Qwen, Vertex, …) as a product: knowledge / reasoning / code accuracy on
recognized benchmarks, plus the serving dimensions (latency, token cost). This is
distinct from `agent-runtime` (which benchmarks runtime engineering — sessions,
tools, recovery) — here the system under test is the MODEL endpoint itself.

Suite-first: no self-designed task dimensions ship here. Recognized LLM benchmarks
(MMLU, GSM8K, HumanEval …) drive this domain via the benchmark_suite / evaluator
contract. The SUT is an OpenAI-compatible endpoint resolved from the run
``Target`` — the config-connect abstraction: ``llm-mock`` (offline reference) and
``llm-endpoint`` (base_url + model + credentials of an already-running managed
LLM). We run recognized benchmarks unmodified and report objective accuracy +
cloud dimensions; we do NOT subjectively judge output quality.
"""

from __future__ import annotations

from clousight_bench.core.plugin import DomainPack, ProviderAdapter, Task
from clousight_bench.domains.llm.adapters.openai_compatible import (
    LlmEndpointAdapter,
    LlmMockAdapter,
)


class LlmDomain(DomainPack):
    domain = "llm"
    description = "Managed LLM endpoints: recognized-benchmark accuracy + serving latency/cost."

    def tasks(self) -> dict[str, type[Task]]:
        # Suite-first: recognized LLM benchmarks (MMLU …) drive this domain via
        # the benchmark_suite / evaluator contract. No dimensions ship here.
        return {}

    def adapters(self) -> dict[str, type[ProviderAdapter]]:
        return {
            LlmMockAdapter.name: LlmMockAdapter,
            LlmEndpointAdapter.name: LlmEndpointAdapter,
        }
