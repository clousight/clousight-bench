"""Orchestrator: the auditable lifecycle every domain shares.

    RESOLVE -> VALIDATE -> PREFLIGHT -> SETUP -> EXECUTE -> COLLECT
            -> SCORE -> ENRICH -> PERSIST -> optional PUBLISH

TEARDOWN is deliberately not a step in that line. It is the mandatory
``finally`` boundary around SETUP -> COLLECT: once SETUP is entered, teardown
always runs, even when setup itself failed half-way, and a teardown failure is
recorded as its own stage error without overwriting the execute or collect
error that caused it.

Three kinds of failure, three different answers:

- a **request** we cannot parse (RESOLVE / VALIDATE) raises ``UserInputError``
  and writes no record -- it never measured anything;
- **plugin** code that crashes while describing or checking the benchmark is
  recorded as a ``VALIDATE`` / ``PREFLIGHT`` stage error with status
  ``invalid``, because nothing was provisioned and no number was produced;
- the **platform** failing under test is a recorded outcome (``failed``),
  because "the platform failed" is itself a benchmark finding.

``run.stages`` reads exactly like that: ``ok`` / ``failed`` means the stage ran,
``skipped`` means it was deliberately not run (a flag, or nothing to do), and an
absent stage was never reached because an earlier one failed.
"""

from __future__ import annotations

import logging
import os
import platform as platform_mod
import signal
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clousight_bench import RUNNER_VERSION
from clousight_bench.core.canonical import canonical_json
from clousight_bench.core.cost_budget import (
    CostLedger,
    budget_would_exceed,
    run_cost_usd,
)
from clousight_bench.core.errors import (
    AdapterNotRunnableError,
    UnknownPlatformError,
    UnknownTaskError,
)
from clousight_bench.core.finalize import _enrich, _publish
from clousight_bench.core.fingerprints import (
    UNKNOWN,
    benchmark_fingerprint,
    environment_fingerprint,
    implementation_fingerprint,
)
from clousight_bench.core.live_guard import ENV_ALLOW_LIVE, live_decision
from clousight_bench.core.observation import (
    Finding,
    ObservationBundle,
    TaskExecutionError,
    TaskResult,
    collect,
    validate_observation_bundle,
)
from clousight_bench.core.plugin import DomainPack, ProviderAdapter
from clousight_bench.core.publish import (
    ResultPublisher,
)
from clousight_bench.core.record import (
    Environment,
    Fingerprints,
    Identity,
    Provenance,
    ResultRecord,
    RunInfo,
    StageError,
)
from clousight_bench.core.redaction import (
    redact,
)
from clousight_bench.core.registry import (
    get_domain,
    get_resource_reaper,
)
from clousight_bench.core.resource_reconcile import reconcile_run_resources
from clousight_bench.core.schema import RunSpec, new_run_id, utc_now
from clousight_bench.core.stage_support import log_traceback as _log_traceback
from clousight_bench.core.stage_support import scrubbed as _scrubbed
from clousight_bench.core.stage_support import stage_error as _stage_error
from clousight_bench.core.store import ResultStore
from clousight_bench.core.suite_runner import SuiteRunner
from clousight_bench.core.tracing import emit_run_trace, new_trace_id
from clousight_bench.core.validation import validate_run_spec

logger = logging.getLogger(__name__)

DEFAULT_RESULTS_DIR = Path("results")

# Stages whose failure means the benchmark itself did not produce a verdict.
_FATAL_STAGES = ("SETUP", "EXECUTE", "COLLECT", "SCORE")
# Stages that fail before anything is provisioned: the request never ran.
_INVALID_STAGES = ("VALIDATE", "PREFLIGHT")
_EMPTY_WORKLOAD: dict[str, Any] = {"workload": "", "workload_version": "", "assets": []}


def _max_persisted_items() -> int:
    """Cap on per-item ItemResults written into a record (schema 0.4). The full
    set stays in artifacts; env-overridable via CSBENCH_MAX_PERSISTED_ITEMS."""
    try:
        return max(0, int(os.environ.get("CSBENCH_MAX_PERSISTED_ITEMS", "1000")))
    except ValueError:
        return 1000


_MAX_PERSISTED_ITEMS = _max_persisted_items()


@dataclass
class _Prepared:
    """Everything the record needs about a run, assembled defensively.

    Each piece comes from plugin code that may crash; a crash here is recorded
    (``errors``) instead of raised, and the missing piece falls back to a value
    that still produces a well-formed, comparable record.
    """

    adapter: ProviderAdapter | None
    identity: Identity
    environment: Environment
    fingerprints: Fingerprints
    errors: list[StageError] = field(default_factory=list)
    provenance: Provenance = field(default_factory=Provenance)


def _ms(start: float) -> float:
    """Milliseconds elapsed since a ``time.perf_counter()`` mark, for stage timing."""
    return round((time.perf_counter() - start) * 1000, 3)


@contextmanager
def _terminate_as_interrupt() -> Iterator[None]:
    """Make SIGTERM raise KeyboardInterrupt, so a ``kill`` is handled exactly like
    Ctrl-C: teardown runs and an interrupted record is persisted instead of
    orphaning resources. No-op off the main thread (signal handlers can only be
    installed there) or where SIGTERM is unavailable, so it is safe to wrap any
    call site (a worker thread, a run-plan loop, a platform without SIGTERM)."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def _raise(signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt(f"terminated by signal {signum}")

    try:
        previous = signal.signal(signal.SIGTERM, _raise)
    except (ValueError, OSError, AttributeError):  # not main thread / unsupported
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


@contextmanager
def _stage_deadline(timeout_s: float | None) -> Iterator[None]:
    """Raise TimeoutError if the wrapped stages exceed ``timeout_s``, so a hung
    provision/setup/execute cannot block a pipeline forever. Uses SIGALRM, so it
    is a no-op off the main thread or where SIGALRM is unavailable (Windows);
    always covers only the measured stages, never teardown.

    NOTE: because SIGALRM only fires on the main thread, this does NOT interrupt a
    call made inside a worker thread -- which is exactly what a threaded load /
    elasticity probe does. The real guard against a hung *live* call is the
    per-request timeout in ``ClientPolicy`` (bounded by ``adapter.deadline_s``),
    not this stage deadline. This wall-clock deadline is the coarse backstop."""
    if (
        not timeout_s
        or timeout_s <= 0
        or not hasattr(signal, "SIGALRM")
        or threading.current_thread() is not threading.main_thread()
    ):
        yield
        return

    def _timeout(signum: int, _frame: Any) -> None:
        raise TimeoutError(f"stage exceeded the {timeout_s:g}s deadline")

    previous = signal.signal(signal.SIGALRM, _timeout)
    signal.setitimer(signal.ITIMER_REAL, timeout_s)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)  # cancel before teardown runs
        signal.signal(signal.SIGALRM, previous)


def execute(
    spec: RunSpec,
    results_dir: Path | None = None,
    enrich: bool = True,
    preflight: bool = True,
    publisher: ResultPublisher | None = None,
    debug: bool = False,
    run_context: Mapping[str, Any] | None = None,
    timeout_s: float | None = None,
    allow_live: bool = False,
    cost_budget: float | None = None,
) -> ResultRecord:
    """Run one RunSpec through the full lifecycle and persist the result.

    ``run_context`` tags this run's membership in a run plan. When
    provided it is recorded verbatim under ``extensions["core"]["run_plan"]``,
    so it is covered by ``record_digest`` (auditable, tamper-evident) but never
    folded into a benchmark/environment/implementation fingerprint -- a warmup
    and a measured repeat of the same benchmark must share those fingerprints.
    """
    results_dir = Path(results_dir or DEFAULT_RESULTS_DIR)
    run_id = new_run_id()
    trace_id = new_trace_id()
    root_start_ns = time.time_ns()
    started_at = utc_now()
    stages: dict[str, str] = {}
    timings: dict[str, float] = {}
    errors: list[StageError] = []

    # RESOLVE -- raises UserInputError; no record is written.
    pack, task, adapter_cls = _resolve(spec, results_dir, trace_id)
    stages["RESOLVE"] = "ok"

    # VALIDATE -- raises UserInputError; no record is written. The validated
    # task config is reused below, so config() is called exactly once per run.
    config = validate_run_spec(spec, task)
    stages["VALIDATE"] = "ok"

    logger.info("run %s: %s/%s on %s", run_id, spec.domain, spec.task_id, spec.platform)

    prepared = _prepare(spec, pack, task, adapter_cls, config, results_dir, run_id, debug)
    findings: list[Finding] = []

    def _record_and_finish(
        status: str,
        result: TaskResult | None = None,
        bundle: ObservationBundle | None = None,
        *,
        enrich_record: bool = False,
        live_run: Mapping[str, Any] | None = None,
    ) -> ResultRecord:
        """Build + persist a record from the current run state — the ONE exit path.

        Every early exit (invalid input, failed preflight, interrupt) and the
        final success path funnel through here so the record shape can never
        drift between exits.
        """
        record = _build_record(
            run_id,
            started_at,
            stages,
            prepared,
            status,
            result,
            findings,
            bundle if bundle is not None else ObservationBundle(),
            errors,
            run_context,
            timings,
            live_run,
        )
        return _finish(
            record,
            results_dir,
            enrich=enrich_record,
            publisher=publisher,
            debug=debug,
            trace_id=trace_id,
            root_start_ns=root_start_ns,
        )

    if prepared.errors or prepared.adapter is None:
        # Plugin code could not describe this run. Nothing was provisioned.
        stages["VALIDATE"] = "failed"
        errors.extend(prepared.errors)
        return _record_and_finish("invalid")

    adapter = prepared.adapter
    # Hand the adapter this run's id (after fingerprints are fixed, so it never
    # perturbs them) so it can tag the cloud resources it creates for later
    # cost/billing reconciliation, and the run's deadline so a wired adapter can
    # bound each SDK call by it.
    adapter.run_id = run_id
    adapter.deadline_s = timeout_s
    adapter.results_dir = results_dir
    bundle = ObservationBundle()
    result: TaskResult | None = None

    # PREFLIGHT -- a critical failure (or a crash in the check itself) means the
    # request could not be measured here, so the record is `invalid` and nothing
    # is ever provisioned.
    if preflight:
        _pf = time.perf_counter()
        gate_error, gate_finding = _preflight(adapter, task, run_id, results_dir, debug)
        timings["PREFLIGHT"] = _ms(_pf)
        if gate_error is not None:
            stages["PREFLIGHT"] = "failed"
            errors.append(gate_error)
            if gate_finding is not None:
                findings.append(gate_finding)
            return _record_and_finish("invalid")
        stages["PREFLIGHT"] = "ok"
    else:
        stages["PREFLIGHT"] = "skipped"

    environment_error = _complete_environment(prepared, spec, task, results_dir, run_id, debug)
    if environment_error is not None:
        stages["VALIDATE"] = "failed"
        errors.append(environment_error)
        return _record_and_finish("invalid")

    # PROVISIONED-CLOUD MACHINERY GATE -- the live-run confirmation, the cost
    # budget/ledger, and resource reconciliation ALL apply only to a run that
    # provisions billable cloud resources. That is an explicit adapter capability
    # (`provisions_resources()`), not an inline guess: connect-only adapters
    # (config-connect to an already-running service) and simulators return False,
    # so a "just attach to an existing service" run skips this entire machinery.
    provisioning = adapter.provisions_resources()
    # LIVE-RUN GATE -- a provisioning run spends real money / can trip quota; it
    # must not provision unless the operator acknowledged the cost (--allow-live /
    # CSBENCH_ALLOW_LIVE). Blocked -> `invalid`, SETUP never entered.
    decision = live_decision("live" if provisioning else "simulated", spec.target, allow_live)
    live_run: dict[str, Any] | None = None
    if decision.is_live:
        live_run = {"acknowledged": decision.acknowledged, "limits": decision.limits}
    if decision.blocked:
        stages["SETUP"] = "skipped"
        findings.append(
            Finding(
                code="live.unconfirmed",
                severity="critical",
                summary="live run not confirmed: real-cloud execution incurs cost",
                details={
                    "execution": "live",
                    "remediation": (
                        "re-run with --allow-live (or export "
                        f"{ENV_ALLOW_LIVE}=1) once you accept the cost; "
                        "use mode: mock to exercise the harness for free"
                    ),
                    "limits": decision.limits,
                },
            )
        )
        return _record_and_finish("invalid", live_run=live_run)

    # COST BUDGET -- the live gate stops accidental spend; this stops runaway
    # spend. For a provisioning run, if the spend-so-far (this results dir) plus
    # this run's estimate would cross the budget, stop BEFORE provisioning.
    budget = _resolve_cost_budget(cost_budget, spec.target)
    if provisioning and budget is not None:
        spent = CostLedger(results_dir).total()
        estimate = float(spec.target.get("estimated_cost_usd", 0.0) or 0.0)
        if budget_would_exceed(spent, estimate, budget):
            stages["SETUP"] = "skipped"
            findings.append(
                Finding(
                    code="cost.budget_exceeded",
                    severity="critical",
                    summary="cost budget would be exceeded: run stopped before provisioning",
                    details={
                        "spent_usd": round(spent, 9),
                        "estimate_usd": round(estimate, 9),
                        "budget_usd": budget,
                        "remediation": (
                            "raise --cost-budget / CSBENCH_COST_BUDGET, or start a "
                            "fresh results dir; the ledger is <results>/.cost_ledger.json"
                        ),
                    },
                )
            )
            if live_run is not None:
                live_run["budget_usd"] = budget
                live_run["spent_usd"] = round(spent, 9)
            return _record_and_finish("invalid", live_run=live_run)

    # SETUP -> EXECUTE -> COLLECT, with TEARDOWN as the mandatory finally boundary.
    # A SIGINT/SIGTERM in this window must still run teardown and persist an
    # interrupted record -- never orphan a provisioned resource or lose progress.
    interrupted: BaseException | None = None
    with _terminate_as_interrupt():
        try:
            with _stage_deadline(timeout_s):
                _st = time.perf_counter()
                adapter.setup()
                stages["SETUP"] = "ok"
                timings["SETUP"] = _ms(_st)
                _st = time.perf_counter()
                bundle = task.execute(adapter, spec.params)
                stages["EXECUTE"] = "ok"
                timings["EXECUTE"] = _ms(_st)
                _st = time.perf_counter()
                bundle = collect(bundle)
                stages["COLLECT"] = "ok"
                timings["COLLECT"] = _ms(_st)
        except TaskExecutionError as exc:
            # The task kept its partial evidence; attribute it to whichever stage
            # was running, since setup and collect can raise this too.
            stage = _failed_stage(stages)
            if isinstance(exc.observations, ObservationBundle):
                bundle = exc.observations
                if stage == "COLLECT":
                    try:
                        validate_observation_bundle(bundle)
                    except Exception:  # noqa: BLE001 - invalid partial evidence is unsafe
                        bundle = ObservationBundle()
            stages[stage] = "failed"
            errors.append(
                _scrubbed(
                    StageError(
                        stage=stage,
                        code=exc.code,
                        type=type(exc).__name__,
                        message=str(exc),
                        retryable=exc.retryable,
                    )
                )
            )
            _log_traceback(results_dir, run_id, debug, exc)
        except (KeyboardInterrupt, SystemExit) as exc:
            # Interrupted mid-flight: record it, keep going to teardown (finally),
            # then persist the interrupted record below and re-raise so the caller
            # sees the interruption -- but with resources released and progress saved.
            stage = _failed_stage(stages)
            stages[stage] = "failed"
            errors.append(_stage_error(stage, exc, code="interrupted"))
            _log_traceback(results_dir, run_id, debug, exc)
            bundle = ObservationBundle()
            interrupted = exc
        except Exception as exc:  # noqa: BLE001 - every failure is a recorded outcome
            stage = _failed_stage(stages)
            stages[stage] = "failed"
            errors.append(_stage_error(stage, exc))
            _log_traceback(results_dir, run_id, debug, exc)
            if stage == "COLLECT":
                bundle = ObservationBundle()
        finally:
            # Reconcile BEFORE teardown stops the transport: destroy + confirm by
            # tag anything this run created but left behind (a crash between
            # provision and deprovision, a wired setup that provisioned a runtime).
            # Only a provisioning run can leave resources behind, so connect-only
            # runs skip reconciliation entirely (nothing was created to reap).
            if provisioning:
                try:
                    findings.extend(
                        reconcile_run_resources(
                            adapter,
                            run_id,
                            getattr(adapter, "provider", None),
                            results_dir,
                            reaper=get_resource_reaper(getattr(adapter, "provider", None)),
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - reconcile must never break teardown
                    errors.append(_stage_error("TEARDOWN", exc, code="reconcile_failed"))
                    _log_traceback(results_dir, run_id, debug, exc)
            _td = time.perf_counter()
            try:
                adapter.teardown()
                stages["TEARDOWN"] = "ok"
            except Exception as exc:  # noqa: BLE001 - never mask the primary error
                stages["TEARDOWN"] = "failed"
                errors.append(_stage_error("TEARDOWN", exc))
                _log_traceback(results_dir, run_id, debug, exc)
            timings["TEARDOWN"] = _ms(_td)

    if interrupted is not None:
        # Teardown has run; persist an interrupted record (no enrich/publish) so
        # progress and stage state survive, then propagate the interruption.
        stages.setdefault("SCORE", "skipped")
        _record_and_finish("interrupted", None, bundle, live_run=live_run)
        raise interrupted

    # SCORE -- pure; observations already collected survive a scorer failure.
    if stages.get("COLLECT") == "ok":
        _sc = time.perf_counter()
        try:
            candidate = task.score(bundle)
            _validate_task_result(candidate)
            result = candidate
            stages["SCORE"] = "ok"
        except Exception as exc:  # noqa: BLE001
            stages["SCORE"] = "failed"
            errors.append(_stage_error("SCORE", exc))
            _log_traceback(results_dir, run_id, debug, exc)
        timings["SCORE"] = _ms(_sc)
    else:
        stages["SCORE"] = "skipped"  # nothing was collected to score

    record = _record_and_finish(
        _status_for(errors, result), result, bundle, live_run=live_run, enrich_record=enrich
    )
    # A provisioning run that actually executed accrues cost against the budget
    # ledger (realized price if the enricher ran, else the caller's estimate).
    if provisioning:
        CostLedger(results_dir).add(
            run_id, getattr(adapter, "provider", None), run_cost_usd(record, spec.target)
        )
    return record


def _resolve_cost_budget(cost_budget: float | None, target: Mapping[str, Any]) -> float | None:
    """Budget from (in order) the explicit arg, ``target.cost_budget``, or the
    ``CSBENCH_COST_BUDGET`` env var. None -> no cap."""
    import os

    if cost_budget is not None:
        return cost_budget
    if target.get("cost_budget") is not None:
        return float(target["cost_budget"])
    env = os.environ.get("CSBENCH_COST_BUDGET")
    return float(env) if env else None


_BENCHMARK_KIND_PREFIX = "suite:"  # id-namespace marking the benchmark-suite kind


def _is_benchmark_task_id(task_id: str) -> bool:
    """True when ``task_id`` names a registered benchmark suite (the public unit)
    rather than a domain-internal native task."""
    return task_id.startswith(_BENCHMARK_KIND_PREFIX)


def _resolve_benchmark(spec: RunSpec, results_dir: Path | None, trace_id: str = "") -> SuiteRunner:
    """Resolve a ``suite:<id>`` task_id to a runnable SuiteRunner via the
    benchmark-suite + evaluator registries."""
    from clousight_bench.core.registry import load_benchmark_suites, load_evaluators

    suite_id = spec.task_id.removeprefix(_BENCHMARK_KIND_PREFIX)
    suites = load_benchmark_suites()
    if suite_id not in suites:
        raise UnknownTaskError(f"suite {suite_id!r} is not a registered benchmark suite: {sorted(suites)}")
    suite = suites[suite_id]
    wanted = spec.params.get("evaluator")  # explicit evaluator_id override
    candidates = [
        e
        for e in load_evaluators()
        if e.supports(suite_id, spec.platform) and (wanted is None or e.evaluator_id == wanted)
    ]
    if not candidates:
        raise UnknownTaskError(
            f"no registered evaluator supports suite {suite_id!r}"
            + (f" with evaluator_id {wanted!r}" if wanted else "")
        )
    # Prefer official evaluators; load_evaluators() is name-sorted so ties are stable.
    evaluator = sorted(candidates, key=lambda e: (not e.official, e.evaluator_id))[0]
    mock = str(spec.target.get("mode", "mock")) == "mock"
    artifacts_root = (Path(results_dir) / "artifacts") if results_dir is not None else None
    return SuiteRunner(
        suite,
        evaluator,
        mock=mock,
        params=dict(spec.params),
        artifacts_root=artifacts_root,
        trace_id=trace_id,
    )


def _resolve(
    spec: RunSpec,
    results_dir: Path | None = None,
    trace_id: str = "",
) -> tuple[DomainPack, SuiteRunner, type[ProviderAdapter]]:
    """Resolve a RunSpec to (DomainPack, SuiteRunner, adapter_cls).

    ``results_dir`` is forwarded to SuiteRunner as ``artifacts_root=results_dir/artifacts``
    so that all suite artifacts are staged under the run's results directory and persisted
    records contain only relative paths (no absolute temp paths).
    """
    pack = get_domain(spec.domain)

    # A benchmark is the public unit: task_ids are ``suite:<id>``, resolved
    # against the benchmark-suite registry (the one documented way to add a
    # benchmark). Bare ids have no rail any more — fail with the suite form.
    if not _is_benchmark_task_id(spec.task_id):
        raise UnknownTaskError(
            f"unknown benchmark {spec.task_id!r}: benchmarks run as 'suite:<id>' — see csbench list"
        )
    task = _resolve_benchmark(spec, results_dir, trace_id)

    # --- Shared adapter lookup + instance-level runnability gate ---
    adapter_classes = pack.adapters()
    if spec.platform not in adapter_classes:
        raise UnknownPlatformError(
            f"platform {spec.platform!r} not in domain {spec.domain!r}: {sorted(adapter_classes)}"
        )
    adapter_cls = adapter_classes[spec.platform]
    # Instance-level gate: skeleton adapters may still be runnable in a simulated
    # mode (e.g. a cloud in ``mode: mock``), which only the target reveals. Only
    # a successfully constructed instance is gated; if construction itself fails
    # that is not a runnability question -- defer it to _prepare, which records
    # it as ``adapter_init_failed`` rather than crashing here.
    try:
        instance = adapter_cls(spec.target)
    except Exception:  # noqa: BLE001 - construction failure is recorded downstream
        return pack, task, adapter_cls
    if not instance.is_runnable_instance():
        raise AdapterNotRunnableError(
            f"platform {spec.platform!r} is a skeleton and cannot run as configured; "
            "if it supports a simulated runtime, set target.mode: mock; otherwise "
            "choose a reference/wired adapter or implement this adapter first"
        )
    return pack, task, adapter_cls


def _prepare(
    spec: RunSpec,
    pack: DomainPack,
    task: SuiteRunner,
    adapter_cls: type[ProviderAdapter],
    config: dict[str, Any],
    results_dir: Path,
    run_id: str,
    debug: bool,
) -> _Prepared:
    """Assemble identity, environment and fingerprints without trusting plugins."""
    errors: list[StageError] = []

    def record_failure(code: str, exc: BaseException) -> None:
        errors.append(_stage_error("VALIDATE", exc, code=code))
        _log_traceback(results_dir, run_id, debug, exc)

    adapter: ProviderAdapter | None = None
    try:
        adapter = adapter_cls(spec.target)
    except Exception as exc:  # noqa: BLE001 - a broken adapter is a recorded outcome
        record_failure("adapter_init_failed", exc)

    workload = dict(_EMPTY_WORKLOAD)
    try:
        declared = task.workload_identity(spec.params)
        workload = {
            "workload": str(declared["workload"]),
            "workload_version": str(declared["workload_version"]),
            "assets": list(declared["assets"]),
        }
    except Exception as exc:  # noqa: BLE001
        record_failure("workload_identity_failed", exc)

    identity = Identity(
        domain=spec.domain,
        task_id=task.task_id,
        task_revision=task.task_revision,
        scorer_revision=task.scorer_revision,
        adapter=adapter_cls.name,
        adapter_status=adapter_cls.status,
        core_version=RUNNER_VERSION,
        workload=workload["workload"],
        workload_version=workload["workload_version"],
        plugin_versions=_plugin_versions(pack, adapter_cls),
    )
    environment = Environment(
        region=str(spec.target.get("region", "")),
        mode="cloud" if adapter_cls.provider else "local",
        python_version=platform_mod.python_version(),
        os_name=platform_mod.system(),
        facts={},
        execution=adapter.execution_mode() if adapter is not None else "unknown",
    )

    _task_provenance = Provenance().to_dict()
    try:
        _task_provenance = task.provenance().to_dict()
    except Exception as exc:  # noqa: BLE001
        record_failure("provenance_failed", exc)
    try:
        fingerprints = Fingerprints(
            benchmark=benchmark_fingerprint(
                task_id=task.task_id,
                task_revision=task.task_revision,
                scorer_revision=task.scorer_revision,
                workload=identity.workload,
                workload_version=identity.workload_version,
                assets=list(workload["assets"]),
                params=config,
                provenance=_task_provenance,
            ),
            environment=environment_fingerprint(
                region=environment.region,
                mode=environment.mode,
                facts=environment.facts,
                execution=environment.execution,
            ),
            implementation=implementation_fingerprint(
                core_version=RUNNER_VERSION,
                domain=spec.domain,
                adapter=identity.adapter,
                adapter_status=identity.adapter_status,
                plugin_versions=identity.plugin_versions,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - an unhashable input is still recordable
        record_failure("fingerprint_failed", exc)
        fingerprints = Fingerprints(benchmark=UNKNOWN, environment=UNKNOWN, implementation=UNKNOWN)

    return _Prepared(
        adapter=adapter,
        identity=identity,
        environment=environment,
        fingerprints=fingerprints,
        errors=errors,
        provenance=Provenance.from_dict(_task_provenance),
    )


def _complete_environment(
    prepared: _Prepared,
    spec: RunSpec,
    task: SuiteRunner,
    results_dir: Path,
    run_id: str,
    debug: bool,
) -> StageError | None:
    """Collect environment facts only after the preflight gate has passed."""
    assert prepared.adapter is not None
    try:
        declared_facts = task.environment_facts(prepared.adapter, spec.params)
        if not isinstance(declared_facts, Mapping):
            raise TypeError(f"environment_facts() must return a mapping, got {type(declared_facts).__name__}")
        prepared.environment.facts = redact(dict(declared_facts))
        prepared.fingerprints.environment = environment_fingerprint(
            region=prepared.environment.region,
            mode=prepared.environment.mode,
            facts=prepared.environment.facts,
            execution=prepared.environment.execution,
        )
    except Exception as exc:  # noqa: BLE001 - broken plugin metadata is recordable
        _log_traceback(results_dir, run_id, debug, exc)
        return _stage_error("VALIDATE", exc, code="environment_facts_failed")
    return None


def _preflight(
    adapter: ProviderAdapter,
    task: SuiteRunner,
    run_id: str,
    results_dir: Path,
    debug: bool,
) -> tuple[StageError | None, Finding | None]:
    """Run the gate. Returns the blocking error, or (None, None) when it passes."""
    try:
        report = adapter.preflight(task)
        if report.ok:
            return None, None
        logger.error("run %s aborted at preflight:\n%s", run_id, report.format())
        summary = report.summary()
        checks = [check.line() for check in report.checks]
        code, exc_type, retryable = "preflight_failed", "PreflightFailure", True
    except Exception as exc:  # noqa: BLE001 - a crashing gate still blocks the run
        _log_traceback(results_dir, run_id, debug, exc)
        summary = f"preflight check raised {type(exc).__name__}: {exc}"
        checks = []
        code, exc_type = "preflight_error", type(exc).__name__
        retryable = isinstance(exc, (ConnectionError, TimeoutError, OSError))

    error = _scrubbed(
        StageError(
            stage="PREFLIGHT",
            code=code,
            type=exc_type,
            message=summary,
            retryable=retryable,
        )
    )
    finding = Finding(
        code="core.preflight_failed",
        severity="critical",
        summary=error.message,
        details={"checks": checks},
    )
    return error, finding


def _plugin_versions(pack: DomainPack, adapter_cls: type[ProviderAdapter]) -> dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version

    modules = {
        type(pack).__module__.split(".")[0],
        adapter_cls.__module__.split(".")[0],
    }
    versions: dict[str, str] = {}
    for module in sorted(modules):
        distribution = module.replace("_", "-")
        try:
            versions[distribution] = version(distribution)
        except (PackageNotFoundError, ValueError):
            versions[distribution] = UNKNOWN
    return versions


def _failed_stage(stages: dict[str, str]) -> str:
    for stage in ("SETUP", "EXECUTE", "COLLECT"):
        if stage not in stages:
            return stage
    return "COLLECT"


def _status_for(errors: list[StageError], result: TaskResult | None) -> str:
    if any(e.stage in _INVALID_STAGES for e in errors):
        return "invalid"
    if any(e.stage in _FATAL_STAGES for e in errors):
        return "failed"
    if result is not None and result.unsupported:
        return "unsupported"
    return "completed"


def _validate_task_result(result: TaskResult) -> None:
    """SCORE succeeds only when its complete output fits the public contract."""
    if not isinstance(result, TaskResult):
        raise TypeError(f"score() must return a TaskResult, got {type(result).__name__}")
    payload = {
        "measurements": {name: measurement.to_dict() for name, measurement in result.measurements.items()},
        "findings": [finding.to_dict() for finding in result.findings],
        "items": [item.to_dict() for item in result.items],
        "notes": result.notes,
        "task_revision": result.task_revision,
        "scorer_revision": result.scorer_revision,
        "unsupported": result.unsupported,
    }
    canonical_json(payload)


def _build_record(
    run_id: str,
    started_at: str,
    stages: dict[str, str],
    prepared: _Prepared,
    status: str,
    result: TaskResult | None,
    findings: list[Finding],
    bundle: ObservationBundle,
    errors: list[StageError],
    run_context: Mapping[str, Any] | None = None,
    timings: Mapping[str, float] | None = None,
    live_run: Mapping[str, Any] | None = None,
) -> ResultRecord:
    all_findings = list(findings) + list(result.findings if result else [])
    core_extension: dict[str, Any] = {}
    if result is not None and result.notes:
        core_extension["notes"] = result.notes
    # Per-item substrate (schema 0.4). Capped so a large run doesn't write a huge
    # record; the full set stays in artifacts. Truncation is RECORDED (never
    # silent) under extensions.core.items_meta.
    item_dicts = [it.to_dict() for it in (result.items if result else [])]
    if len(item_dicts) > _MAX_PERSISTED_ITEMS:
        core_extension["items_meta"] = {
            "total": len(item_dicts),
            "persisted": _MAX_PERSISTED_ITEMS,
            "truncated": True,
        }
        item_dicts = item_dicts[:_MAX_PERSISTED_ITEMS]
    if run_context is not None:
        core_extension["run_plan"] = dict(run_context)
    if live_run is not None:
        core_extension["live_run"] = dict(live_run)
    extensions: dict[str, Any] = {}
    if core_extension:
        # "core" is the reserved extension namespace; plugins use their own name.
        extensions["core"] = core_extension
    return ResultRecord(
        run=RunInfo(
            run_id=run_id,
            started_at=started_at,
            finished_at=utc_now(),
            stages=dict(stages),
            stage_timings=dict(timings or {}),
        ),
        identity=prepared.identity,
        environment=prepared.environment,
        fingerprints=prepared.fingerprints,
        status=status,
        measurements={name: m.to_dict() for name, m in (result.measurements if result else {}).items()},
        findings=[f.to_dict() for f in all_findings],
        observations=(dict(bundle.observations) if isinstance(bundle.observations, dict) else {}),
        series=dict(bundle.series) if isinstance(bundle.series, dict) else {},
        artifacts=list(bundle.artifacts) if isinstance(bundle.artifacts, list) else [],
        items=item_dicts,
        extensions=extensions,
        errors=[e.to_dict() for e in errors],
        provenance=prepared.provenance,
    )


def _finish(
    record: ResultRecord,
    results_dir: Path,
    enrich: bool,
    publisher: ResultPublisher | None,
    debug: bool,
    trace_id: str | None = None,
    root_start_ns: int | None = None,
) -> ResultRecord:
    if trace_id is not None:
        # Link this result to its execution trace before persisting, so a record
        # points at its spans under <results>/traces/<trace_id>.jsonl.
        record.extensions.setdefault("core", {})["trace_id"] = trace_id
    if enrich:
        record = _enrich(record, results_dir, debug)
    else:
        record.run.stages["ENRICH"] = "skipped"
    # Publishing is an out-of-record side effect. Receipts are its only source
    # of truth, so the core record always durably says this optional stage was
    # skipped and its digest remains valid after execute() returns.
    record.run.stages["PUBLISH"] = "skipped"
    record.run.finished_at = utc_now()
    path = ResultStore(results_dir).persist(record)
    if record.run.stages.get("PERSIST") == "ok":
        logger.info("result -> %s", path)
    else:
        logger.error("result NOT written to %s; degraded record -> %s", results_dir, path)
    _publish(path, results_dir, publisher, debug)
    _emit_trace(record, results_dir, trace_id, root_start_ns)
    return record


def _emit_trace(
    record: ResultRecord, results_dir: Path, trace_id: str | None, root_start_ns: int | None
) -> None:
    """Build the run's OTel spans and hand them to the registered exporters.
    Telemetry never breaks a run -- any failure is logged and swallowed."""
    if trace_id is None or root_start_ns is None:
        return
    try:
        emit_run_trace(record, results_dir, trace_id, root_start_ns, time.time_ns())
    except Exception as exc:  # noqa: BLE001 - a trace must never fail a run
        logger.warning("run %s: trace export failed: %s", record.run.run_id, exc)
