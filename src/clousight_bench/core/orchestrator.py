"""Orchestrator: the auditable lifecycle every domain shares.

    RESOLVE -> VALIDATE -> PREFLIGHT -> SETUP -> EXECUTE -> COLLECT
            -> SCORE -> ENRICH -> PERSIST -> optional PUBLISH

TEARDOWN is deliberately not a step in that line. It is the mandatory
``finally`` boundary around SETUP -> COLLECT: once SETUP is entered, teardown
always runs, even when setup itself failed half-way, and a teardown failure is
recorded as its own stage error without overwriting the execute or collect
error that caused it.

RESOLVE and VALIDATE failures raise ``UserInputError`` and write no record: a
request we could not parse never measured anything. Every later failure is a
recorded outcome, because "the platform failed" is itself a benchmark finding.
"""
from __future__ import annotations

import logging
import platform as platform_mod
import traceback
from pathlib import Path
from typing import Any

from clousight_bench import RUNNER_VERSION
from clousight_bench.core.errors import (
    AdapterNotRunnableError,
    UnknownPlatformError,
    UnknownTaskError,
)
from clousight_bench.core.fingerprints import (
    benchmark_fingerprint,
    environment_fingerprint,
    implementation_fingerprint,
)
from clousight_bench.core.observation import (
    Finding,
    ObservationBundle,
    TaskExecutionError,
    TaskResult,
    collect,
)
from clousight_bench.core.plugin import DomainPack, ProviderAdapter, Task
from clousight_bench.core.record import (
    Environment,
    Fingerprints,
    Identity,
    ResultRecord,
    RunInfo,
    StageError,
)
from clousight_bench.core.redaction import redact
from clousight_bench.core.registry import get_domain, load_enrichers
from clousight_bench.core.schema import RunSpec, new_run_id, utc_now
from clousight_bench.core.store import ResultStore
from clousight_bench.core.validation import validate_run_spec

logger = logging.getLogger(__name__)

DEFAULT_RESULTS_DIR = Path("results")

# Stages whose failure means the benchmark itself did not produce a verdict.
_FATAL_STAGES = ("SETUP", "EXECUTE", "COLLECT", "SCORE")


def execute(
    spec: RunSpec,
    results_dir: Path | None = None,
    enrich: bool = True,
    preflight: bool = True,
    debug: bool = False,
) -> ResultRecord:
    """Run one RunSpec through the full lifecycle and persist the result."""
    results_dir = Path(results_dir or DEFAULT_RESULTS_DIR)
    run_id = new_run_id()
    started_at = utc_now()
    stages: dict[str, str] = {}
    errors: list[StageError] = []

    # RESOLVE -- raises UserInputError; no record is written.
    pack, task, adapter_cls = _resolve(spec)
    adapter = adapter_cls(spec.target)
    stages["RESOLVE"] = "ok"

    # VALIDATE -- raises UserInputError; no record is written.
    validate_run_spec(spec, task)
    stages["VALIDATE"] = "ok"

    logger.info("run %s: %s/%s on %s", run_id, spec.domain, spec.task_id, spec.platform)

    workload = task.workload_identity(spec.params)
    facts = task.environment_facts(adapter, spec.params)
    identity = Identity(
        domain=spec.domain,
        task_id=task.task_id,
        task_revision=task.task_revision,
        scorer_revision=task.scorer_revision,
        adapter=adapter_cls.name,
        adapter_status=adapter_cls.status,
        core_version=RUNNER_VERSION,
        workload=str(workload["workload"]),
        workload_version=str(workload["workload_version"]),
        plugin_versions=_plugin_versions(pack, adapter_cls),
    )
    environment = Environment(
        region=str(spec.target.get("region", "")),
        mode="cloud" if adapter_cls.provider else "local",
        python_version=platform_mod.python_version(),
        os_name=platform_mod.system(),
        facts=redact(facts),
    )
    fingerprints = Fingerprints(
        benchmark=benchmark_fingerprint(
            task_id=task.task_id,
            task_revision=task.task_revision,
            scorer_revision=task.scorer_revision,
            workload=identity.workload,
            workload_version=identity.workload_version,
            assets=list(workload["assets"]),
            params=task.config(spec.params),
        ),
        environment=environment_fingerprint(
            region=environment.region, mode=environment.mode, facts=environment.facts
        ),
        implementation=implementation_fingerprint(
            core_version=RUNNER_VERSION,
            domain=spec.domain,
            adapter=identity.adapter,
            adapter_status=identity.adapter_status,
            plugin_versions=identity.plugin_versions,
        ),
    )

    findings: list[Finding] = []
    bundle = ObservationBundle()
    result: TaskResult | None = None

    # PREFLIGHT -- a critical failure means the request could not be measured
    # here, so the record is `invalid` and nothing is ever provisioned.
    if preflight:
        report = adapter.preflight(task)
        if not report.ok:
            logger.error("run %s aborted at preflight:\n%s", run_id, report.format())
            stages["PREFLIGHT"] = "failed"
            errors.append(
                StageError(
                    stage="PREFLIGHT",
                    code="preflight_failed",
                    type="PreflightFailure",
                    message=report.summary(),
                    retryable=True,
                )
            )
            findings.append(
                Finding(
                    code="core.preflight_failed",
                    severity="critical",
                    summary=report.summary(),
                    evidence="B",
                    details={"checks": [c.line() for c in report.checks]},
                )
            )
            record = _build_record(
                run_id, started_at, stages, identity, environment, fingerprints,
                "invalid", None, findings, ObservationBundle(), errors,
            )
            return _finish(record, results_dir, enrich=False)
        stages["PREFLIGHT"] = "ok"
    else:
        stages["PREFLIGHT"] = "skipped"

    # SETUP -> EXECUTE -> COLLECT, with TEARDOWN as the mandatory finally boundary.
    entered_setup = False
    try:
        entered_setup = True
        adapter.setup()
        stages["SETUP"] = "ok"
        bundle = task.execute(adapter, spec.params)
        stages["EXECUTE"] = "ok"
        bundle = collect(bundle)
        stages["COLLECT"] = "ok"
    except TaskExecutionError as exc:
        bundle = exc.observations
        stages["EXECUTE"] = "failed"
        errors.append(
            StageError(
                stage="EXECUTE",
                code=exc.code,
                type=type(exc).__name__,
                message=str(exc),
                retryable=exc.retryable,
            )
        )
        _log_traceback(results_dir, run_id, debug, exc)
    except Exception as exc:  # noqa: BLE001 - every failure is a recorded outcome
        stage = _failed_stage(stages)
        stages[stage] = "failed"
        errors.append(_stage_error(stage, exc))
        _log_traceback(results_dir, run_id, debug, exc)
    finally:
        if entered_setup:
            try:
                adapter.teardown()
                stages["TEARDOWN"] = "ok"
            except Exception as exc:  # noqa: BLE001 - never mask the primary error
                stages["TEARDOWN"] = "failed"
                errors.append(_stage_error("TEARDOWN", exc))
                _log_traceback(results_dir, run_id, debug, exc)

    # SCORE -- pure; observations already collected survive a scorer failure.
    if stages.get("COLLECT") == "ok":
        try:
            result = task.score(bundle)
            stages["SCORE"] = "ok"
        except Exception as exc:  # noqa: BLE001
            stages["SCORE"] = "failed"
            errors.append(_stage_error("SCORE", exc))
            _log_traceback(results_dir, run_id, debug, exc)
    else:
        stages["SCORE"] = "skipped"

    record = _build_record(
        run_id, started_at, stages, identity, environment, fingerprints,
        _status_for(errors, result), result, findings, bundle, errors,
    )
    return _finish(record, results_dir, enrich=enrich)


def _resolve(spec: RunSpec) -> tuple[DomainPack, Task, type[ProviderAdapter]]:
    pack = get_domain(spec.domain)
    task_classes = pack.tasks()
    if spec.task_id not in task_classes:
        raise UnknownTaskError(
            f"task {spec.task_id!r} not in domain {spec.domain!r}: {sorted(task_classes)}"
        )
    adapter_classes = pack.adapters()
    if spec.platform not in adapter_classes:
        raise UnknownPlatformError(
            f"platform {spec.platform!r} not in domain {spec.domain!r}: "
            f"{sorted(adapter_classes)}"
        )
    adapter_cls = adapter_classes[spec.platform]
    if not adapter_cls.is_runnable():
        raise AdapterNotRunnableError(
            f"platform {spec.platform!r} is a skeleton and cannot run; "
            "choose a reference/wired adapter or implement this adapter first"
        )
    return pack, task_classes[spec.task_id](), adapter_cls


def _plugin_versions(
    pack: DomainPack, adapter_cls: type[ProviderAdapter]
) -> dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version

    from clousight_bench.core.fingerprints import UNKNOWN

    modules = {
        type(pack).__module__.split(".")[0],
        adapter_cls.__module__.split(".")[0],
    }
    versions: dict[str, str] = {}
    for module in sorted(modules):
        distribution = module.replace("_", "-")
        try:
            versions[distribution] = version(distribution)
        except PackageNotFoundError:
            versions[distribution] = UNKNOWN
    return versions


def _failed_stage(stages: dict[str, str]) -> str:
    for stage in ("SETUP", "EXECUTE", "COLLECT"):
        if stage not in stages:
            return stage
    return "COLLECT"


def _stage_error(stage: str, exc: BaseException) -> StageError:
    return StageError(
        stage=stage,
        code=f"{stage.lower()}_failed",
        type=type(exc).__name__,
        message=str(exc),
        retryable=isinstance(exc, (ConnectionError, TimeoutError, OSError)),
    )


def _status_for(errors: list[StageError], result: TaskResult | None) -> str:
    if any(e.stage == "PREFLIGHT" for e in errors):
        return "invalid"
    if any(e.stage in _FATAL_STAGES for e in errors):
        return "failed"
    if result is not None and result.unsupported:
        return "unsupported"
    return "completed"


def _build_record(
    run_id: str,
    started_at: str,
    stages: dict[str, str],
    identity: Identity,
    environment: Environment,
    fingerprints: Fingerprints,
    status: str,
    result: TaskResult | None,
    findings: list[Finding],
    bundle: ObservationBundle,
    errors: list[StageError],
) -> ResultRecord:
    all_findings = list(findings) + list(result.findings if result else [])
    extensions: dict[str, Any] = {}
    if result is not None and result.notes:
        # "core" is the reserved extension namespace; plugins use their own name.
        extensions["core"] = {"notes": result.notes}
    return ResultRecord(
        run=RunInfo(
            run_id=run_id,
            started_at=started_at,
            finished_at=utc_now(),
            stages=dict(stages),
        ),
        identity=identity,
        environment=environment,
        fingerprints=fingerprints,
        status=status,
        measurements={
            name: m.to_dict()
            for name, m in (result.measurements if result else {}).items()
        },
        findings=[f.to_dict() for f in all_findings],
        observations=dict(bundle.observations),
        series=dict(bundle.series),
        artifacts=list(bundle.artifacts),
        extensions=extensions,
        errors=[e.to_dict() for e in errors],
    )


def _finish(record: ResultRecord, results_dir: Path, enrich: bool) -> ResultRecord:
    if enrich:
        for enricher in load_enrichers():
            record = enricher.enrich(record)
        record.run.stages["ENRICH"] = "ok"
    else:
        record.run.stages["ENRICH"] = "skipped"
    record.run.finished_at = utc_now()
    path = ResultStore(results_dir).persist(record)
    logger.info("result -> %s", path)
    return record


def _log_traceback(
    results_dir: Path, run_id: str, debug: bool, exc: BaseException
) -> None:
    """Tracebacks belong in a local log, never in a shareable record."""
    logger.exception("run %s stage failure", run_id, exc_info=exc)
    if not debug:
        return
    log_dir = Path(results_dir) / "debug"
    log_dir.mkdir(parents=True, exist_ok=True)
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    with (log_dir / f"{run_id}.log").open("a", encoding="utf-8") as handle:
        handle.write(text)
