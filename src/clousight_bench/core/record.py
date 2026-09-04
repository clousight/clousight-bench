"""ResultRecord 0.4: one benchmark result, fully attributable.

Everything a reader needs to trust a number is a top-level field: which
benchmark ran (``identity`` + ``fingerprints.benchmark``), where it ran
(``environment`` + ``fingerprints.environment``), which code produced it
(``fingerprints.implementation``), what was measured (``measurements``), what
it means (``findings``), what was actually seen (``observations`` / ``series``
/ ``artifacts``), what went wrong (``errors``) and how it ended (``status``).

There is no ``ok`` flag: a run is ``completed``, ``failed``, ``invalid`` or
``unsupported``, and every one of those is a legitimate benchmark outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "0.4"

STATUSES: tuple[str, ...] = ("completed", "failed", "invalid", "unsupported", "interrupted")
STAGES: tuple[str, ...] = (
    "RESOLVE",
    "VALIDATE",
    "PREFLIGHT",
    "SETUP",
    "EXECUTE",
    "COLLECT",
    "TEARDOWN",
    "SCORE",
    "ENRICH",
    "PERSIST",
    "PUBLISH",
)
STAGE_STATES: tuple[str, ...] = ("ok", "failed", "skipped")
MODES: tuple[str, ...] = ("local", "cloud", "unknown")
EXECUTIONS: tuple[str, ...] = ("simulated", "live", "unknown")


class RecordError(ValueError):
    """A record or one of its parts violates the 0.4 contract."""


@dataclass
class StageError:
    """One lifecycle-stage failure, attributable to the stage that produced it."""

    stage: str
    code: str
    type: str
    message: str
    retryable: bool = False

    def __post_init__(self) -> None:
        if self.stage not in STAGES:
            raise RecordError(f"stage must be one of {STAGES}, got {self.stage!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "code": self.code,
            "type": self.type,
            "message": self.message,
            "retryable": self.retryable,
        }


@dataclass
class RunInfo:
    run_id: str
    started_at: str
    finished_at: str
    stages: dict[str, str] = field(default_factory=dict)
    # Per-stage wall-clock durations in ms, for spotting a slow/hung stage. Only
    # the stages that were timed appear; a key must be a known stage.
    stage_timings: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, state in self.stages.items():
            if name not in STAGES:
                raise RecordError(f"unknown stage {name!r}")
            if state not in STAGE_STATES:
                raise RecordError(f"stage {name!r} state must be one of {STAGE_STATES}")
        for name in self.stage_timings:
            if name not in STAGES:
                raise RecordError(f"unknown stage {name!r} in stage_timings")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "stages": dict(self.stages),
            "stage_timings": dict(self.stage_timings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunInfo:
        return cls(
            run_id=str(data["run_id"]),
            started_at=str(data["started_at"]),
            finished_at=str(data["finished_at"]),
            stages=dict(data.get("stages", {})),
            stage_timings=dict(data.get("stage_timings", {})),
        )


@dataclass
class Identity:
    domain: str
    task_id: str
    task_revision: str
    scorer_revision: str
    adapter: str
    adapter_status: str
    core_version: str
    workload: str = ""
    workload_version: str = ""
    plugin_versions: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "task_id": self.task_id,
            "task_revision": self.task_revision,
            "scorer_revision": self.scorer_revision,
            "adapter": self.adapter,
            "adapter_status": self.adapter_status,
            "core_version": self.core_version,
            "workload": self.workload,
            "workload_version": self.workload_version,
            "plugin_versions": dict(self.plugin_versions),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Identity:
        return cls(
            domain=str(data["domain"]),
            task_id=str(data["task_id"]),
            task_revision=str(data["task_revision"]),
            scorer_revision=str(data["scorer_revision"]),
            adapter=str(data["adapter"]),
            adapter_status=str(data["adapter_status"]),
            core_version=str(data["core_version"]),
            workload=str(data.get("workload", "")),
            workload_version=str(data.get("workload_version", "")),
            plugin_versions=dict(data.get("plugin_versions", {})),
        )


@dataclass
class Environment:
    region: str
    mode: str
    python_version: str
    os_name: str
    facts: dict[str, Any] = field(default_factory=dict)
    execution: str = "unknown"

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise RecordError(f"mode must be one of {MODES}, got {self.mode!r}")
        if self.execution not in EXECUTIONS:
            raise RecordError(f"execution must be one of {EXECUTIONS}, got {self.execution!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "region": self.region,
            "mode": self.mode,
            "python_version": self.python_version,
            "os_name": self.os_name,
            "facts": dict(self.facts),
            "execution": self.execution,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Environment:
        return cls(
            region=str(data["region"]),
            mode=str(data["mode"]),
            python_version=str(data["python_version"]),
            os_name=str(data["os_name"]),
            facts=dict(data.get("facts", {})),
            execution=str(data.get("execution", "unknown")),
        )


@dataclass
class Fingerprints:
    benchmark: str
    environment: str
    implementation: str
    record_digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark": self.benchmark,
            "environment": self.environment,
            "implementation": self.implementation,
            "record_digest": self.record_digest,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Fingerprints:
        return cls(
            benchmark=str(data["benchmark"]),
            environment=str(data["environment"]),
            implementation=str(data["implementation"]),
            record_digest=str(data.get("record_digest", "")),
        )


@dataclass
class Provenance:
    """Credibility chain: which recognized suite produced the number, whether the
    run was unmodified, and which evaluator scored it.  Empty for non-suite runs;
    feeds the opaque benchmark fingerprint.
    """

    suite_id: str = ""
    suite_version: str = ""
    dataset_digest: str = ""
    unmodified: bool = True
    evaluator_id: str = ""
    evaluator_official: bool = True
    scaffold: str = ""
    division: str = ""

    def is_empty(self) -> bool:
        return self == Provenance()

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "suite_version": self.suite_version,
            "dataset_digest": self.dataset_digest,
            "unmodified": self.unmodified,
            "evaluator_id": self.evaluator_id,
            "evaluator_official": self.evaluator_official,
            "scaffold": self.scaffold,
            "division": self.division,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Provenance:
        return cls(
            suite_id=str(data.get("suite_id", "")),
            suite_version=str(data.get("suite_version", "")),
            dataset_digest=str(data.get("dataset_digest", "")),
            unmodified=bool(data.get("unmodified", True)),
            evaluator_id=str(data.get("evaluator_id", "")),
            evaluator_official=bool(data.get("evaluator_official", True)),
            scaffold=str(data.get("scaffold", "")),
            division=str(data.get("division", "")),
        )


@dataclass
class ResultRecord:
    """One benchmark result in schema 0.4.

    ``observations`` holds the raw evidence a re-score would replay. When that
    evidence is too large to inline, a run stores an artifact pointer instead
    — ``{"trace": {"$artifact": "trace.jsonl"}}`` — where the value names an
    entry in ``artifacts``. Either shape is valid; ``observations`` must never
    be dropped just because the payload is big.

    ``items`` (schema 0.4, optional) holds per-example :class:`ItemResult`
    evidence + per-item scores; the scalar ``measurements`` are their aggregation
    (see :mod:`clousight_bench.core.aggregate`). Empty for suites that have not
    migrated to the per-item substrate.
    """

    run: RunInfo
    identity: Identity
    environment: Environment
    fingerprints: Fingerprints
    status: str
    measurements: dict[str, dict[str, Any]] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)
    observations: dict[str, Any] = field(default_factory=dict)
    series: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    extensions: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    provenance: Provenance = field(default_factory=Provenance)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise RecordError(f"status must be one of {STATUSES}, got {self.status!r}")
        if self.schema_version != SCHEMA_VERSION:
            raise RecordError(f"schema_version must be {SCHEMA_VERSION!r}, got {self.schema_version!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run": self.run.to_dict(),
            "identity": self.identity.to_dict(),
            "environment": self.environment.to_dict(),
            "fingerprints": self.fingerprints.to_dict(),
            "provenance": self.provenance.to_dict(),
            "measurements": dict(self.measurements),
            "findings": list(self.findings),
            "observations": dict(self.observations),
            "series": dict(self.series),
            "artifacts": list(self.artifacts),
            "items": list(self.items),
            "extensions": dict(self.extensions),
            "errors": list(self.errors),
            "status": self.status,
        }

    def to_json(self) -> str:
        import json

        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResultRecord:
        version = str(data.get("schema_version", ""))
        if version != SCHEMA_VERSION:
            raise RecordError(
                f"unsupported schema_version {version!r}; expected {SCHEMA_VERSION!r}"
                " (no migration path — re-run the benchmark to produce a current record)"
            )
        return cls(
            run=RunInfo.from_dict(data["run"]),
            identity=Identity.from_dict(data["identity"]),
            environment=Environment.from_dict(data["environment"]),
            fingerprints=Fingerprints.from_dict(data["fingerprints"]),
            status=str(data["status"]),
            measurements=dict(data.get("measurements", {})),
            findings=list(data.get("findings", [])),
            observations=dict(data.get("observations", {})),
            series=dict(data.get("series", {})),
            artifacts=list(data.get("artifacts", [])),
            items=list(data.get("items", [])),
            extensions=dict(data.get("extensions", {})),
            errors=list(data.get("errors", [])),
            provenance=Provenance.from_dict(data.get("provenance", {})),
        )
