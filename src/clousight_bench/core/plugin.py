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
from typing import Any, Literal

from clousight_bench.core.redaction import redact
from clousight_bench.core.schema import ResultRecord


@dataclass
class TaskOutput:
    """What a Task.run returns; the orchestrator wraps it into a ResultRecord."""

    metrics: dict[str, Any]
    evidence_layer: str
    ok: bool = True
    raw: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    series: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)


AdapterStatus = Literal["reference", "experimental", "wired", "skeleton"]


class ProviderAdapter(ABC):
    """Connects the framework to one system under test.

    Lifecycle: the orchestrator calls setup() once before the task and
    teardown() once after (even on failure). Provisioning heavy resources
    (an EMR cluster, a DB instance) belongs in setup(); adapters that target
    always-on endpoints (an agent runtime API) can make setup() a no-op.
    """

    name: str = "abstract"
    status: AdapterStatus = "experimental"
    provider: str | None = None

    def __init__(self, target: dict[str, Any] | None = None) -> None:
        self.target = target or {}

    @classmethod
    def is_runnable(cls) -> bool:
        return cls.status != "skeleton"

    def setup(self) -> None:  # noqa: B027 - optional hook
        """Provision / connect. Default no-op."""

    def teardown(self) -> None:  # noqa: B027 - optional hook
        """Release everything setup() created. Default no-op."""

    def describe(self) -> dict[str, Any]:
        """Non-secret target description, folded into the implementation fingerprint."""
        return {"adapter": self.name, "target": redact(self.target)}

    def resolve_credentials(self) -> Any:
        """Report where this adapter's credentials come from (never the secret).

        Reuses the provider's default credential chain (env / CLI profile /
        role) so users don't mint a benchmark-only secret. Powers `csbench
        doctor` and adapter self-reporting; real adapters still defer to the
        official SDK chain at call time.
        """
        from clousight_bench.core.credentials import resolve_credentials

        return resolve_credentials(self.target, platform=self.name)

    def preflight(self, task: Task | None = None) -> Any:
        """Check prerequisites BEFORE provisioning; return a PreflightReport.

        The orchestrator runs this first and aborts on any CRITICAL failure, so
        missing credentials / permissions / connectivity surface up front rather
        than mid-run. Default checks credentials + provider SDK; domain adapters
        override to add connectivity / permission probes (calling super()).

        ``task`` (when provided) lets adapters check exactly the *minimal*
        permissions that specific benchmark needs on this cloud, since the
        required permission set is a (benchmark x cloud) matrix.
        """
        from clousight_bench.core import preflight as pf

        report = pf.PreflightReport()
        report.add(pf.credential_check(self.target, self.name))
        report.add(pf.sdk_check(self.target, self.name))
        return report


class Task(ABC):
    """One benchmark dimension. Deterministic where the evidence layer says so."""

    task_id: str = "abstract"
    title: str = ""
    evidence_layer: str = "C"
    # Abstract capability tokens this benchmark exercises (cloud-independent).
    # The adapter maps these to each cloud's concrete minimal permissions and
    # verifies them at preflight. Empty = no special permissions declared.
    required_permissions: tuple[str, ...] = ()

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


class PrivateAssetResolver(ABC):
    """Fetch a private/licensed benchmark asset (dataset, held-out scoring keys).

    Open-core ships NO resolver; commercial packs register one via the
    ``clousight_bench.asset_resolvers`` entry point (e.g. token-authenticated
    download from the data service). Given an ``AssetSpec`` with
    ``source == 'private'``, return a local path to the resolved contents.
    """

    name: str = "abstract"

    @abstractmethod
    def resolve(self, spec: Any, cache_dir: Any | None = None) -> Any:
        """Return a local path (str | Path) to the private asset's contents."""
