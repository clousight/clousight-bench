"""Plugin contracts: DomainPack / ProviderAdapter / Task.

The framework's abstraction cut: workloads differ wildly across cloud products,
but the *pipeline* is identical --

    resolve -> validate -> preflight -> setup -> execute -> collect
            -> score -> enrich -> persist -> publish

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
from typing import Any, Literal

from clousight_bench.core.observation import ObservationBundle, TaskResult
from clousight_bench.core.redaction import redact
from clousight_bench.core.schema import ResultRecord

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
    # Set by the orchestrator before setup() to this run's run_id, so an adapter
    # can tag the resources it creates (for cost/billing reconciliation). None
    # outside a run (construction, mock/tests). Not part of any fingerprint.
    run_id: str | None = None
    # Set by the orchestrator before setup() to the run's remaining stage deadline
    # (--timeout, seconds), so a wired adapter can bound each SDK call by it -- the
    # real guard against a hung live call, since the SIGALRM stage deadline does
    # not interrupt a threaded probe. None when no deadline was set.
    deadline_s: float | None = None
    # Set by the orchestrator before setup() to the run's results dir, so an
    # adapter can book the resources it creates in the run's ResourceLedger for
    # tag-based teardown reconciliation. None outside a run.
    results_dir: Any | None = None

    def __init__(self, target: dict[str, Any] | None = None) -> None:
        self.target = target or {}

    @classmethod
    def is_runnable(cls) -> bool:
        return cls.status != "skeleton"

    def execution_mode(self) -> str:
        """'simulated' | 'live' -- whether this run's numbers come from a simulated
        runtime or a real cloud. Default 'live'; simulators / mock adapters
        override. Folded into the environment fingerprint so simulated and live
        data never pool."""
        return "live"

    def is_runnable_instance(self) -> bool:
        """Instance-level runnability gate, aware of this run's ``target``.

        Class-level ``is_runnable()`` only sees ``status``; an adapter whose
        runnability depends on config (e.g. a skeleton cloud that is still fully
        exercisable in a simulated ``mode: mock``) overrides this to decide from
        ``self.target``. Default keeps the class-level verdict, so adapters that
        do not distinguish modes are unaffected."""
        return type(self).is_runnable()

    def setup(self) -> None:  # noqa: B027 - optional hook
        """Provision / connect. Default no-op."""

    def teardown(self) -> None:  # noqa: B027 - optional hook
        """Release everything setup() created. Default no-op."""

    def describe(self) -> dict[str, Any]:
        """Non-secret target description, folded into the implementation fingerprint."""
        return {"adapter": self.name, "target": redact(self.target)}

    def resource_tags(self) -> dict[str, Any]:
        """Tags a wired adapter must stamp on every cloud resource it creates.

        The single source of the run-id / managed-by convention (``core/
        resource_tags.py``), so an orphaned resource from a crashed run is
        findable and reap-able by ``csbench sweep``. ``target['resource_tags']``
        adds caller tags; the reserved keys are never overridden."""
        from clousight_bench.core.resource_tags import run_tags

        extra = self.target.get("resource_tags") or {}
        return run_tags(self.run_id, extra)

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
    """One benchmark dimension, split into observation and scoring.

    ``execute`` may talk to the cloud; it returns only raw, replayable evidence.
    ``score`` is a pure function of that evidence: it must not read credentials,
    create resources or mutate the bundle it is given, which is exactly what
    makes a stored observation re-scorable after a scorer fix.
    """

    task_id: str = "abstract"
    title: str = ""
    evidence_layer: str = "C"
    # Bumped whenever the observation procedure or the scoring rules change, so
    # a published number stays attributable to the code that produced it.
    task_revision: str = "0"
    scorer_revision: str = "0"
    # Abstract capability tokens this benchmark exercises (cloud-independent).
    # The adapter maps these to each cloud's concrete minimal permissions and
    # verifies them at preflight. Empty = no special permissions declared.
    required_permissions: tuple[str, ...] = ()

    @abstractmethod
    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        """The controlled inputs that determine the result -> benchmark fingerprint."""

    @abstractmethod
    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        """Drive the system under test and return raw observations only."""

    @abstractmethod
    def score(self, observations: ObservationBundle) -> TaskResult:
        """Turn observations into measurements and findings. Pure function."""

    def environment_facts(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Non-sensitive environment facts this benchmark depends on.

        Folded into the environment fingerprint. Never return a credential,
        hostname, username or raw environment variable.
        """
        return {}

    def workload_identity(self, params: dict[str, Any]) -> dict[str, Any]:
        """Workload and asset identity folded into the benchmark fingerprint.

        Tasks that drive a WorkloadEngine override this; the default declares no
        workload. Keys are exactly ``workload``, ``workload_version`` and
        ``assets``.
        """
        return {"workload": "", "workload_version": "", "assets": []}


class DomainPack(ABC):
    """A cloud product category: its tasks, its adapters, its vocabulary."""

    domain: str = "abstract"
    description: str = ""
    # Plugin-API version range this plugin was built against. The registry
    # refuses to load a plugin whose range does not contain the core's
    # PLUGIN_API_VERSION. Default = compatible with the current major.
    requires_plugin_api: str = ">=1.0,<2.0"

    @abstractmethod
    def tasks(self) -> dict[str, type[Task]]:
        """task_id -> Task class."""

    @abstractmethod
    def adapters(self) -> dict[str, type[ProviderAdapter]]:
        """platform name -> Adapter class."""


class ResultEnricher(ABC):
    """Post-run enrichment hook: annotate a ResultRecord (e.g. cost estimate).

    The core ships one reference enricher -- cost attribution in
    ``clousight_bench.enrichers.pricing`` -- registered via the
    ``clousight_bench.enrichers`` entry point; commercial packs register
    additional enrichers the same way. Enrichers must be deterministic and
    side-effect-free beyond the returned record.
    """

    name: str = "abstract"
    requires_plugin_api: str = ">=1.0,<2.0"

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
    requires_plugin_api: str = ">=1.0,<2.0"

    @abstractmethod
    def resolve(self, spec: Any, cache_dir: Any | None = None) -> Any:
        """Return a local path (str | Path) to the private asset's contents."""


class RuntimeProviderPlugin(ABC):
    """The wired (real) runtime implementation for one cloud in a domain.

    Open-core ships NONE: a skeleton cloud adapter falls back to a not-wired
    transport and stays un-runnable in real mode. A commercial pack registers a
    plugin via the ``clousight_bench.runtime_providers`` entry point, which both
    flips that provider's real mode to runnable and supplies the live,
    SDK-backed transport -- so the open adapter class is never edited to "wire"
    a cloud; installing the pack is the wiring.

    ``build_transport`` returns a domain-specific transport object (e.g. the
    agent-runtime ``RuntimeTransport``); it is typed ``Any`` here so the core
    stays free of any domain import. The consuming adapter knows the concrete
    type.
    """

    provider: str = "abstract"
    requires_plugin_api: str = ">=1.0,<2.0"

    @abstractmethod
    def build_transport(self, adapter: Any) -> Any:
        """Build a live transport for ``adapter`` (called only in real mode)."""


class ResourceReaper(ABC):
    """Reconciles orphaned cloud resources a run left behind (``csbench sweep``).

    A run that dies before ``teardown`` (SIGKILL, crashed host) can leak a
    provisioned runtime that keeps billing. Every resource this harness creates
    is tagged with the run id (``core/resource_tags.py``), so a reaper can list
    tagged resources for one cloud and delete the stale ones. Open-core ships
    NONE -- listing/deleting needs the provider SDK + credentials -- so ``sweep``
    without an installed reaper fails clearly. A commercial pack registers one
    via the ``clousight_bench.resource_reapers`` entry point.
    """

    provider: str = "abstract"
    requires_plugin_api: str = ">=1.0,<2.0"

    @abstractmethod
    def sweep(
        self, *, dry_run: bool, older_than_s: float | None = None
    ) -> list[dict[str, Any]]:
        """Find harness-tagged resources and, unless ``dry_run``, delete them.

        Returns one dict per resource acted on (id + run_id + whatever the cloud
        exposes). ``older_than_s`` restricts to resources older than a window, so
        an in-flight run's resources are never reaped out from under it."""

    def verify(self, run_id: str) -> list[dict[str, Any]]:
        """Authoritative post-teardown check: cloud resources still tagged with
        ``run_id`` (i.e. NOT reclaimed). Empty list = confirmed clean. Default
        delegates to a read-only sweep filtered to this run; a reaper may override
        with a cheaper direct tag query."""
        return [r for r in self.sweep(dry_run=True) if r.get("run_id") == run_id]
