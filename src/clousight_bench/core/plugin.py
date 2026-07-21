"""Plugin contracts: DomainPack / ProviderAdapter / Task.

The framework's abstraction cut: workloads differ wildly across cloud products,
but the *pipeline* is identical --

    provision -> setup -> execute -> collect -> teardown -> score -> report

So the core only orchestrates that lifecycle; everything product-specific lives
in plugins:

- DomainPack   : one per cloud product category (agent-runtime, bigdata-emr,
                 database, compute, messaging...). Declares tasks + adapters.
- ProviderAdapter : one per (domain, cloud provider). Knows how to provision,
                 talk to, and tear down the system under test. May shell out
                 to Terraform, call an SDK, or hit HTTP endpoints.
- Task         : one per benchmark dimension. Written against the domain's
                 adapter interface only -- NEVER against a specific cloud.
                 Owns its scoring and declares its evidence layer.

Third-party plugins register via the ``clousight_bench.domains`` entry point;
in-tree domains are registered the same way (see pyproject.toml), so external
and built-in packs are loaded identically.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from clousight_bench.core.schema import ResultRecord


@dataclass
class TaskOutput:
    """What a Task.run returns; the orchestrator wraps it into a ResultRecord."""

    metrics: dict[str, Any]
    evidence_layer: str
    ok: bool = True
    raw: dict[str, Any] = field(default_factory=dict)
    notes: str = ""


class ProviderAdapter(ABC):
    """Connects the framework to one system under test.

    Lifecycle: the orchestrator calls setup() once before the task and
    teardown() once after (even on failure). Provisioning heavy resources
    (an EMR cluster, a DB instance) belongs in setup(); adapters that target
    always-on endpoints (an agent runtime API) can make setup() a no-op.
    """

    name: str = "abstract"

    def __init__(self, target: dict[str, Any] | None = None) -> None:
        self.target = target or {}

    def setup(self) -> None:  # noqa: B027 - optional hook
        """Provision / connect. Default no-op."""

    def teardown(self) -> None:  # noqa: B027 - optional hook
        """Release everything setup() created. Default no-op."""

    def describe(self) -> dict[str, Any]:
        """Non-secret target description, folded into config_hash."""
        return {"adapter": self.name, "target": _redact(self.target)}


class Task(ABC):
    """One benchmark dimension. Deterministic where the evidence layer says so."""

    task_id: str = "abstract"
    title: str = ""
    evidence_layer: str = "C"

    @abstractmethod
    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        """Everything that determines the result -> hashed for reproducibility."""

    @abstractmethod
    def run(self, adapter: ProviderAdapter, params: dict[str, Any]) -> TaskOutput:
        """Execute against the adapter and score the observation."""


class DomainPack(ABC):
    """A cloud product category: its tasks, its adapters, its vocabulary."""

    domain: str = "abstract"
    description: str = ""

    @abstractmethod
    def tasks(self) -> dict[str, type[Task]]:
        """task_id -> Task class."""

    @abstractmethod
    def adapters(self) -> dict[str, type[ProviderAdapter]]:
        """platform name -> Adapter class."""


class ResultEnricher(ABC):
    """Post-run enrichment hook: annotate a ResultRecord (e.g. cost estimate).

    Open-core ships NO enricher implementations; commercial plugins register
    theirs via the ``clousight_bench.enrichers`` entry point. Enrichers must be
    deterministic and side-effect-free beyond the returned record.
    """

    name: str = "abstract"

    @abstractmethod
    def enrich(self, record: ResultRecord) -> ResultRecord:
        """Return the record, possibly with extra metrics / raw annotations."""


_SECRET_HINTS = ("key", "secret", "token", "password", "credential")


def _redact(target: dict[str, Any]) -> dict[str, Any]:
    """Best-effort scrub so secrets never reach config_hash / result files."""
    clean: dict[str, Any] = {}
    for k, v in target.items():
        if any(hint in k.lower() for hint in _SECRET_HINTS):
            clean[k] = "<redacted>"
        elif isinstance(v, dict):
            clean[k] = _redact(v)
        else:
            clean[k] = v
    return clean
