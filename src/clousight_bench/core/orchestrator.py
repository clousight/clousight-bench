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
import platform as platform_mod
import traceback
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clousight_bench import RUNNER_VERSION
from clousight_bench.core.canonical import canonical_json
from clousight_bench.core.errors import (
    AdapterNotRunnableError,
    UnknownPlatformError,
    UnknownTaskError,
)
from clousight_bench.core.fingerprints import (
    UNKNOWN,
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
    validate_observation_bundle,
)
from clousight_bench.core.plugin import DomainPack, ProviderAdapter, Task
from clousight_bench.core.publish import ResultPublisher, append_receipt
from clousight_bench.core.record import (
    Environment,
    Fingerprints,
    Identity,
    ResultRecord,
    RunInfo,
    StageError,
)
from clousight_bench.core.redaction import redact, scrub_identity_text
from clousight_bench.core.registry import get_domain, load_enrichers
from clousight_bench.core.schema import RunSpec, new_run_id, utc_now
from clousight_bench.core.store import ResultStore
from clousight_bench.core.validation import validate_run_spec

logger = logging.getLogger(__name__)

DEFAULT_RESULTS_DIR = Path("results")

# Stages whose failure means the benchmark itself did not produce a verdict.
_FATAL_STAGES = ("SETUP", "EXECUTE", "COLLECT", "SCORE")
# Stages that fail before anything is provisioned: the request never ran.
_INVALID_STAGES = ("VALIDATE", "PREFLIGHT")
_EMPTY_WORKLOAD: dict[str, Any] = {"workload": "", "workload_version": "", "assets": []}


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


def execute(
    spec: RunSpec,
    results_dir: Path | None = None,
    enrich: bool = True,
    preflight: bool = True,
    publisher: ResultPublisher | None = None,
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
    stages["RESOLVE"] = "ok"

    # VALIDATE -- raises UserInputError; no record is written. The validated
    # task config is reused below, so config() is called exactly once per run.
    config = validate_run_spec(spec, task)
    stages["VALIDATE"] = "ok"

    logger.info("run %s: %s/%s on %s", run_id, spec.domain, spec.task_id, spec.platform)

    prepared = _prepare(spec, pack, task, adapter_cls, config, results_dir, run_id, debug)
    findings: list[Finding] = []
    if prepared.errors or prepared.adapter is None:
        # Plugin code could not describe this run. Nothing was provisioned.
        stages["VALIDATE"] = "failed"
        errors.extend(prepared.errors)
        record = _build_record(
            run_id, started_at, stages, prepared, "invalid", None, findings,
            ObservationBundle(), errors,
        )
        return _finish(
            record,
            results_dir,
            enrich=False,
            publisher=publisher,
            debug=debug,
        )

    adapter = prepared.adapter
    bundle = ObservationBundle()
    result: TaskResult | None = None

    # PREFLIGHT -- a critical failure (or a crash in the check itself) means the
    # request could not be measured here, so the record is `invalid` and nothing
    # is ever provisioned.
    if preflight:
        gate_error, gate_finding = _preflight(adapter, task, run_id, results_dir, debug)
        if gate_error is not None:
            stages["PREFLIGHT"] = "failed"
            errors.append(gate_error)
            if gate_finding is not None:
                findings.append(gate_finding)
            record = _build_record(
                run_id, started_at, stages, prepared, "invalid", None, findings,
                ObservationBundle(), errors,
            )
            return _finish(
                record,
                results_dir,
                enrich=False,
                publisher=publisher,
                debug=debug,
            )
        stages["PREFLIGHT"] = "ok"
    else:
        stages["PREFLIGHT"] = "skipped"

    environment_error = _complete_environment(
        prepared, spec, task, results_dir, run_id, debug
    )
    if environment_error is not None:
        stages["VALIDATE"] = "failed"
        errors.append(environment_error)
        record = _build_record(
            run_id,
            started_at,
            stages,
            prepared,
            "invalid",
            None,
            findings,
            ObservationBundle(),
            errors,
        )
        return _finish(
            record,
            results_dir,
            enrich=False,
            publisher=publisher,
            debug=debug,
        )

    # SETUP -> EXECUTE -> COLLECT, with TEARDOWN as the mandatory finally boundary.
    try:
        adapter.setup()
        stages["SETUP"] = "ok"
        bundle = task.execute(adapter, spec.params)
        stages["EXECUTE"] = "ok"
        bundle = collect(bundle)
        stages["COLLECT"] = "ok"
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
    except Exception as exc:  # noqa: BLE001 - every failure is a recorded outcome
        stage = _failed_stage(stages)
        stages[stage] = "failed"
        errors.append(_stage_error(stage, exc))
        _log_traceback(results_dir, run_id, debug, exc)
        if stage == "COLLECT":
            bundle = ObservationBundle()
    finally:
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
            candidate = task.score(bundle)
            _validate_task_result(candidate)
            result = candidate
            stages["SCORE"] = "ok"
        except Exception as exc:  # noqa: BLE001
            stages["SCORE"] = "failed"
            errors.append(_stage_error("SCORE", exc))
            _log_traceback(results_dir, run_id, debug, exc)
    else:
        stages["SCORE"] = "skipped"  # nothing was collected to score

    record = _build_record(
        run_id, started_at, stages, prepared, _status_for(errors, result), result,
        findings, bundle, errors,
    )
    return _finish(
        record,
        results_dir,
        enrich=enrich,
        publisher=publisher,
        debug=debug,
    )


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


def _prepare(
    spec: RunSpec,
    pack: DomainPack,
    task: Task,
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
    )

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
    except Exception as exc:  # noqa: BLE001 - an unhashable input is still recordable
        record_failure("fingerprint_failed", exc)
        fingerprints = Fingerprints(
            benchmark=UNKNOWN, environment=UNKNOWN, implementation=UNKNOWN
        )

    return _Prepared(
        adapter=adapter,
        identity=identity,
        environment=environment,
        fingerprints=fingerprints,
        errors=errors,
    )


def _complete_environment(
    prepared: _Prepared,
    spec: RunSpec,
    task: Task,
    results_dir: Path,
    run_id: str,
    debug: bool,
) -> StageError | None:
    """Collect environment facts only after the preflight gate has passed."""
    assert prepared.adapter is not None
    try:
        declared_facts = task.environment_facts(prepared.adapter, spec.params)
        if not isinstance(declared_facts, Mapping):
            raise TypeError(
                "environment_facts() must return a mapping, got "
                f"{type(declared_facts).__name__}"
            )
        prepared.environment.facts = redact(dict(declared_facts))
        prepared.fingerprints.environment = environment_fingerprint(
            region=prepared.environment.region,
            mode=prepared.environment.mode,
            facts=prepared.environment.facts,
        )
    except Exception as exc:  # noqa: BLE001 - broken plugin metadata is recordable
        _log_traceback(results_dir, run_id, debug, exc)
        return _stage_error("VALIDATE", exc, code="environment_facts_failed")
    return None


def _preflight(
    adapter: ProviderAdapter,
    task: Task,
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
        evidence="B",
        details={"checks": checks},
    )
    return error, finding


def _plugin_versions(
    pack: DomainPack, adapter_cls: type[ProviderAdapter]
) -> dict[str, str]:
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


def _scrubbed(error: StageError) -> StageError:
    """Stage messages quote paths and hosts; the record must not identify a machine."""
    error.message = scrub_identity_text(error.message)
    return error


def _stage_error(stage: str, exc: BaseException, code: str | None = None) -> StageError:
    return _scrubbed(
        StageError(
            stage=stage,
            code=code or f"{stage.lower()}_failed",
            type=type(exc).__name__,
            message=str(exc),
            retryable=isinstance(exc, (ConnectionError, TimeoutError, OSError)),
        )
    )


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
        raise TypeError(
            f"score() must return a TaskResult, got {type(result).__name__}"
        )
    payload = {
        "measurements": {
            name: measurement.to_dict()
            for name, measurement in result.measurements.items()
        },
        "findings": [finding.to_dict() for finding in result.findings],
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
        identity=prepared.identity,
        environment=prepared.environment,
        fingerprints=prepared.fingerprints,
        status=status,
        measurements={
            name: m.to_dict()
            for name, m in (result.measurements if result else {}).items()
        },
        findings=[f.to_dict() for f in all_findings],
        observations=(
            dict(bundle.observations) if isinstance(bundle.observations, dict) else {}
        ),
        series=dict(bundle.series) if isinstance(bundle.series, dict) else {},
        artifacts=list(bundle.artifacts) if isinstance(bundle.artifacts, list) else [],
        extensions=extensions,
        errors=[e.to_dict() for e in errors],
    )


def _finish(
    record: ResultRecord,
    results_dir: Path,
    enrich: bool,
    publisher: ResultPublisher | None,
    debug: bool,
) -> ResultRecord:
    if enrich:
        record = _enrich(record, results_dir, debug)
    else:
        record.run.stages["ENRICH"] = "skipped"
    if publisher is None:
        # A disabled optional stage is known before persistence. Recording the
        # skip here keeps the returned/printed record identical to durable JSON.
        record.run.stages["PUBLISH"] = "skipped"
    record.run.finished_at = utc_now()
    path = ResultStore(results_dir).persist(record)
    if record.run.stages.get("PERSIST") == "ok":
        logger.info("result -> %s", path)
    else:
        logger.error("result NOT written to %s; degraded record -> %s", results_dir, path)
    _publish(record, results_dir, publisher, debug)
    return record


def _enrich(record: ResultRecord, results_dir: Path, debug: bool) -> ResultRecord:
    """Apply third-party enrichers. Their bugs are recorded, never fatal."""
    run_id = record.run.run_id
    failed = False
    try:
        enrichers = load_enrichers()
    except Exception as exc:  # noqa: BLE001 - a broken plugin must not eat the result
        record.errors.append(_stage_error("ENRICH", exc, code="enricher_load_failed").to_dict())
        _log_traceback(results_dir, run_id, debug, exc)
        enrichers = []
        failed = True

    for enricher in enrichers:
        name = getattr(enricher, "name", type(enricher).__name__)
        baseline = record
        try:
            enriched = enricher.enrich(deepcopy(baseline))
        except Exception as exc:  # noqa: BLE001
            record.errors.append(
                _scrubbed(
                    StageError(
                        stage="ENRICH",
                        code="enricher_failed",
                        type=type(exc).__name__,
                        message=f"{name}: {exc}",
                        retryable=False,
                    )
                ).to_dict()
            )
            _log_traceback(results_dir, run_id, debug, exc)
            failed = True
            continue
        if not isinstance(enriched, ResultRecord):
            exc = TypeError(
                f"enricher {name!r} returned "
                f"{type(enriched).__name__}, not a ResultRecord"
            )
            record.errors.append(
                _scrubbed(
                    StageError(
                        stage="ENRICH",
                        code="enricher_failed",
                        type=type(exc).__name__,
                        message=str(exc),
                        retryable=False,
                    )
                ).to_dict()
            )
            failed = True
            continue
        try:
            _validate_enriched_record(enriched, baseline)
        except Exception as exc:  # noqa: BLE001 - discard every malformed candidate
            record.errors.append(
                _stage_error(
                    "ENRICH",
                    exc,
                    code=f"enricher_invalid_record:{name}",
                ).to_dict()
            )
            failed = True
            continue
        record = enriched

    record.run.stages["ENRICH"] = "failed" if failed else "ok"
    return record


def _publish(
    record: ResultRecord,
    results_dir: Path,
    publisher: ResultPublisher | None,
    debug: bool,
) -> None:
    """Publish a copy of the durable record and keep the outcome in a receipt."""
    if publisher is None:
        return
    if record.run.stages.get("PERSIST") != "ok":
        record.run.stages["PUBLISH"] = "skipped"
        return

    receipt: dict[str, Any] = {
        "run_id": record.run.run_id,
        "publisher": publisher.name,
        "at": utc_now(),
    }
    try:
        detail = publisher.publish(deepcopy(record))
        if not isinstance(detail, dict):
            raise TypeError(
                f"publisher {publisher.name!r} returned "
                f"{type(detail).__name__}, not a dict"
            )
        record.run.stages["PUBLISH"] = "ok"
        receipt.update({"ok": True, "detail": detail})
    except Exception as exc:  # noqa: BLE001 - upload failure is not a benchmark failure
        record.run.stages["PUBLISH"] = "failed"
        receipt.update(
            {
                "ok": False,
                "code": "publish_failed",
                "type": type(exc).__name__,
                "message": scrub_identity_text(str(exc)),
            }
        )
        _log_traceback(results_dir, record.run.run_id, debug, exc)

    try:
        append_receipt(results_dir, receipt)
    except Exception as exc:  # noqa: BLE001 - the durable local result must survive
        record.run.stages["PUBLISH"] = "failed"
        _log_traceback(results_dir, record.run.run_id, debug, exc)


def _validate_enriched_record(candidate: Any, baseline: ResultRecord) -> None:
    """Accept canonical records while protecting lifecycle-owned fields."""
    if not isinstance(candidate, ResultRecord):
        raise TypeError(
            f"enricher returned {type(candidate).__name__}, not a ResultRecord"
        )
    payload = candidate.to_dict()
    canonical_json(payload)
    ResultRecord.from_dict(payload)
    for name, measurement in payload["measurements"].items():
        if not isinstance(name, str) or not isinstance(measurement, dict):
            raise TypeError("measurement names and values must be strings and objects")
        missing = {"value", "unit", "evidence"} - measurement.keys()
        if missing:
            raise ValueError(f"measurement {name!r} missing keys {sorted(missing)}")
        if measurement["evidence"] not in ("A", "B", "C", "D"):
            raise ValueError(f"measurement {name!r} has invalid evidence")
    for finding in payload["findings"]:
        if not isinstance(finding, dict):
            raise TypeError("findings must contain objects")
        missing = {"code", "severity", "summary", "evidence", "details"} - finding.keys()
        if missing:
            raise ValueError(f"finding missing keys {sorted(missing)}")
    for error in payload["errors"]:
        if not isinstance(error, dict):
            raise TypeError("errors must contain objects")
        fields = {"stage", "code", "type", "message", "retryable"}
        missing = fields - error.keys()
        if missing:
            raise ValueError(f"stage error missing keys {sorted(missing)}")
        extra = error.keys() - fields
        if extra:
            raise ValueError(f"stage error has unknown keys {sorted(extra)}")
        StageError(**error)
    before = baseline.to_dict()
    protected = (
        "schema_version",
        "run",
        "identity",
        "environment",
        "fingerprints",
        "status",
        "measurements",
        "findings",
        "observations",
        "series",
        "artifacts",
        "errors",
    )
    changed = [key for key in protected if payload[key] != before[key]]
    if changed:
        raise ValueError(f"enricher changed lifecycle-owned field(s): {changed}")


def _log_traceback(
    results_dir: Path, run_id: str, debug: bool, exc: BaseException
) -> None:
    """Tracebacks belong in a local log, never in a shareable record."""
    logger.exception("run %s stage failure", run_id, exc_info=exc)
    if not debug:
        return
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        log_dir = Path(results_dir) / "debug"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / f"{run_id}.log").open("a", encoding="utf-8") as handle:
            handle.write(text)
    except OSError as log_exc:  # the log is a convenience; the error it logs is not
        logger.warning("run %s: could not write the debug log: %s", run_id, log_exc)
