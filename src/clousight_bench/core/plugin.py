"""Plugin contracts: DomainPack / ProviderAdapter (+ lifecycle hooks).

The framework's abstraction cut: workloads differ wildly across cloud products,
but the *pipeline* is identical --

    resolve -> validate -> preflight -> setup -> execute -> collect
            -> score -> enrich -> persist -> publish

So the core only orchestrates that lifecycle; everything product-specific lives
in plugins:

- DomainPack   : one per cloud product category (agent-runtime, database,
                 compute, messaging...). Declares the domain's adapters.
- ProviderAdapter : one per (domain, cloud provider). Knows how to provision,
                 talk to, and tear down the system under test. May shell out
                 to Terraform, call an SDK, or hit HTTP endpoints.

Benchmarks are NOT declared here: the one public way to add a benchmark is a
``BenchmarkSuite`` + ``Evaluator`` (``clousight_bench.core.suite``), registered
under the ``clousight_bench.benchmark_suites`` / ``.evaluators`` entry points
and addressed as ``suite:<id>``.

Third-party plugins register via the ``clousight_bench.domains`` entry point;
in-tree domains are registered the same way (see pyproject.toml), so external
and built-in packs are loaded identically.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

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
    # Example target dict shown in `csbench list --verbose` and `--json`.
    # Adapters that have a non-trivial target override this as a class variable.
    target_example: dict = {}
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

    def provisions_resources(self) -> bool:
        """Whether THIS run creates billable cloud resources the framework must
        gate, budget, and reap — the explicit "provisioned-cloud" capability.

        The single seam the orchestrator uses to decide whether to run the
        provisioned-cloud machinery: the live-run confirmation gate, the cost
        budget/ledger, and resource reconciliation (`csbench sweep`-style
        reaping). When it returns ``False`` the run is **connect-only** — it
        attaches to an already-running service (config-connect via
        ``target.endpoint`` / ``credentials_ref``) or a simulated/local reference
        — and none of that machinery runs (the three lifecycle phases
        create/connect/destroy collapse to just connect).

        The default derives the answer: a real cloud (``provider`` set) executed
        live. Config-connect adapters (``*-endpoint``) and simulators leave
        ``provider`` unset / ``execution_mode`` simulated, so they are
        connect-only by default. A provisioning adapter may instead inherit
        :class:`ProvisionedCloudAdapter`, which declares this ``True``.
        """
        # bool(self.provider): an empty-string provider counts as no provider
        # (matches the historical truthiness check this seam replaced).
        return self.execution_mode() == "live" and bool(self.provider)

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

    def preflight(self, task: Any | None = None) -> Any:
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


class ProvisionedCloudAdapter(ProviderAdapter):
    """A ProviderAdapter that provisions billable cloud resources.

    Opt-in base for adapters whose ``setup()`` creates real, billed resources
    (a runtime, a cluster, a DB instance). Declares
    :meth:`provisions_resources` ``True`` unconditionally, so the orchestrator
    always runs the live-gate / cost budget / reaper for it — no reliance on the
    ``provider``/``execution_mode`` derivation. Connect-only adapters simply do
    NOT inherit this (they attach to an already-running service and skip the whole
    provisioned-cloud machinery)."""

    def provisions_resources(self) -> bool:
        return True


class DomainPack(ABC):
    """A cloud product category: its adapters and its vocabulary.

    Benchmarks are not declared on the domain — they are ``BenchmarkSuite``
    plugins on the suite registry; a RunSpec pairs a domain/platform (the SUT
    connection) with a ``suite:<id>`` benchmark.
    """

    domain: str = "abstract"
    description: str = ""
    # Plugin-API version range this plugin was built against. The registry
    # refuses to load a plugin whose range does not contain the core's
    # PLUGIN_API_VERSION. Default = compatible with the current major.
    requires_plugin_api: str = ">=1.0,<2.0"

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


@dataclass(frozen=True)
class ControllerTfSpec:
    """Terraform surface of one provider's prod-controller profile.

    ``tf_targets`` are the exact resource addresses ``csbench submit`` /
    ``teardown`` pass as ``-target`` flags (never a bare apply/destroy, which
    would in-place touch everything else in the module). ``driver_tf_vars``
    maps plan-yaml ``driver:`` keys to the terraform vars they set, in ``-var``
    emission order. Declared by the provider (see
    ``RuntimeProviderPlugin.controller_tf_spec``) so the core stays free of any
    cloud's resource vocabulary.
    """

    tf_targets: tuple[str, ...]
    driver_tf_vars: Mapping[str, str]


@dataclass(frozen=True)
class ControllerReaperSpec:
    """Live delete callables for one provider's prod-controller reaper.

    The prod controller self-destructs on watchdog-terminal by deleting, in a
    fixed order, everything the run created: residual runtimes → the NAT/EIP →
    the controller's own instance (last). Each callable here is the provider's
    SDK-backed delete for one of those stages, ready to hand to the neutral
    ``RestrictedReaper`` (which owns only the ORDER + best-effort semantics).
    ``self_instance_id`` reads THIS controller's own instance id (delete_self's
    argument) from the cloud's metadata service — vendor-specific, so it lives
    here too. Declared by the provider (see
    ``RuntimeProviderPlugin.controller_reaper_spec``) so ``core`` stays free of
    any cloud's SDK, resource names, endpoints or metadata host.
    """

    delete_runtime: Callable[[str], None]
    delete_nat: Callable[[], None]
    delete_self: Callable[[str], None]
    self_instance_id: Callable[[], str]


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

    def controller_tf_spec(self) -> ControllerTfSpec | None:
        """Terraform surface of this provider's prod-controller profile; None =
        the provider has no wired prod-controller path (``csbench submit`` then
        fails loudly instead of guessing another cloud's resources)."""
        return None

    def controller_reaper_spec(self, region: str, log: Callable[[str], None]) -> ControllerReaperSpec | None:
        """Live delete callables for this provider's prod-controller reaper; None
        = the provider has no wired prod-controller reaper (the controller then
        degrades to a no-op reap and leaves teardown to the local backstop)."""
        return None


class CampaignProbeHook(ABC):
    """Optional per-campaign data-plane probe lifecycle (probe-sink §7).

    A provider that supports an in-region probe implements this so the run-plan
    loop can bring one probe up per campaign, expose it to every task's freshly
    built transport (via target stamping), sync its OSS telemetry, and reap it —
    interrupt-safe. Open-core defines only the seam; the aliyun impl lives in the
    pack. A provider without this returns None from campaign_probe_hook()."""

    @abstractmethod
    def start_campaign_probe(self, target: dict[str, Any]) -> dict[str, str]:
        """Provision the probe. Return keys to merge into every task target,
        e.g. {"probe_url": ..., "probe_blob_prefix": ...}. Raise on failure
        (spec §9: no silent fallback)."""

    @abstractmethod
    def sync_probe_artifacts(self, results_dir: Any) -> None:
        """Mirror the probe's OSS prefix into results_dir (channel ②)."""

    @abstractmethod
    def stop_campaign_probe(self) -> None:
        """Reap the probe. Idempotent + best-effort (called from a finally)."""


def campaign_probe_hook(provider: str) -> CampaignProbeHook | None:
    """Look up the CampaignProbeHook for *provider*, or None if unsupported."""
    from clousight_bench.core.registry import get_runtime_provider

    plugin = get_runtime_provider(provider)
    if plugin is None:
        return None
    fn = getattr(plugin, "campaign_probe_hook", None)
    return fn() if callable(fn) else None


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
    def sweep(self, *, dry_run: bool, older_than_s: float | None = None) -> list[dict[str, Any]]:
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
