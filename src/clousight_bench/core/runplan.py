"""Run plans: repeat a benchmark, discard warmups, aggregate the rest.

Phase 1C sits *on top of* the Phase 1B lifecycle without reaching into it. A
``RunPlan`` runs the same ``RunSpec`` ``warmup + repeat`` times through the
ordinary :func:`clousight_bench.core.orchestrator.execute`, so every single run
is still its own auditable, digested ``0.2`` record -- no evidence is ever
collapsed away. What Phase 1C adds is a *reading* of those records:

- warmup runs are executed first and excluded from the statistics (first-call
  JIT / cache / cold-connection effects are not the steady state you publish);
- the measured runs are reduced to one distribution per measurement;
- the reduction refuses to mix records that are not the same benchmark in the
  same environment -- comparability is checked from the fingerprints, not
  assumed.

A single failed repeat never aborts the plan: it lands a ``failed`` record like
any other run and is counted honestly in ``status_counts``.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clousight_bench.core.canonical import canonical_json, digest
from clousight_bench.core.errors import UserInputError
from clousight_bench.core.fingerprints import UNKNOWN
from clousight_bench.core.orchestrator import DEFAULT_RESULTS_DIR, execute
from clousight_bench.core.persistence import atomic_write_text
from clousight_bench.core.publish import ResultPublisher
from clousight_bench.core.record import SCHEMA_VERSION, ResultRecord
from clousight_bench.core.schema import RunSpec
from clousight_bench.core.statistics import aggregate_measurements

# Aggregates live in their own subtree so the report's record loader can skip
# them wholesale: they are summaries of records, not records themselves.
AGGREGATES_DIRNAME = "aggregates"

# Only a run that produced a trustworthy verdict contributes numbers. A failed
# or invalid run is counted, but it has no measurements to pool.
SCORED_STATUSES: tuple[str, ...] = ("completed", "unsupported")


class RunPlanError(UserInputError):
    """The run plan itself is invalid (a bad repeat or warmup count)."""


@dataclass
class RunPlan:
    """Run ``spec`` ``warmup`` times to warm up, then ``repeat`` times for real."""

    spec: RunSpec
    repeat: int = 1
    warmup: int = 0

    def __post_init__(self) -> None:
        if self.repeat < 1:
            raise RunPlanError(f"repeat must be >= 1, got {self.repeat}")
        if self.warmup < 0:
            raise RunPlanError(f"warmup must be >= 0, got {self.warmup}")


@dataclass
class RunPlanAggregate:
    """A durable statistical summary of one run plan's measured repeats."""

    plan_id: str
    identity: dict[str, Any]
    fingerprints: dict[str, str]
    comparable: bool
    plan: dict[str, int]
    runs: dict[str, list[str]]
    status_counts: dict[str, int]
    measurements: dict[str, dict[str, Any]]
    notes: list[str] = field(default_factory=list)
    kind: str = "run_plan_aggregate"
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "identity": dict(self.identity),
            "fingerprints": dict(self.fingerprints),
            "comparable": self.comparable,
            "plan": dict(self.plan),
            "runs": {role: list(ids) for role, ids in self.runs.items()},
            "status_counts": dict(self.status_counts),
            "measurements": dict(self.measurements),
            "notes": list(self.notes),
        }
        payload["digest"] = digest(payload)
        return payload

    def to_json(self) -> str:
        import json

        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def new_plan_id() -> str:
    return f"plan-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _comparability_key(record: ResultRecord) -> tuple[str, str]:
    return (record.fingerprints.benchmark, record.fingerprints.environment)


def build_aggregate(
    plan_id: str,
    plan: RunPlan,
    warmup_records: list[ResultRecord],
    measured_records: list[ResultRecord],
) -> RunPlanAggregate:
    """Reduce a plan's runs to one comparability-checked statistical summary."""
    status_counts: dict[str, int] = {}
    for record in measured_records:
        status_counts[record.status] = status_counts.get(record.status, 0) + 1

    notes: list[str] = []
    scorable = [r for r in measured_records if r.status in SCORED_STATUSES]

    groups: dict[tuple[str, str], list[ResultRecord]] = {}
    for record in scorable:
        groups.setdefault(_comparability_key(record), []).append(record)

    comparable = len(groups) <= 1
    if len(groups) > 1:
        # The benchmark or environment changed mid-plan. Aggregate only the
        # largest self-consistent group and say which repeats were dropped.
        ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        chosen_key, chosen = ranked[0]
        dropped = sum(len(recs) for key, recs in ranked[1:])
        notes.append(
            f"benchmark/environment changed across repeats: "
            f"{len(groups)} distinct fingerprint pairs; aggregated the largest "
            f"group of {len(chosen)} run(s), excluded {dropped}"
        )
    elif groups:
        chosen = next(iter(groups.values()))
    else:
        chosen = []
        notes.append("no completed runs to aggregate")

    reference = chosen[0] if chosen else (measured_records[0] if measured_records else None)
    if reference is not None:
        identity = {
            "domain": reference.identity.domain,
            "task_id": reference.identity.task_id,
            "adapter": reference.identity.adapter,
            "core_version": reference.identity.core_version,
        }
        implementations = {r.fingerprints.implementation for r in chosen}
        if len(implementations) > 1:
            implementation = "mixed"
            notes.append(
                "implementation fingerprint varies across repeats: the code "
                "changed, so these numbers are comparable only with that caveat"
            )
        else:
            implementation = reference.fingerprints.implementation
        fingerprints = {
            "benchmark": reference.fingerprints.benchmark,
            "environment": reference.fingerprints.environment,
            "implementation": implementation,
        }
    else:  # pragma: no cover - repeat >= 1 guarantees a measured record
        identity = {}
        fingerprints = {
            "benchmark": UNKNOWN,
            "environment": UNKNOWN,
            "implementation": UNKNOWN,
        }

    measurements = aggregate_measurements([r.measurements for r in chosen])

    return RunPlanAggregate(
        plan_id=plan_id,
        identity=identity,
        fingerprints=fingerprints,
        comparable=comparable,
        plan={"repeat": plan.repeat, "warmup": plan.warmup},
        runs={
            "measured": [r.run.run_id for r in measured_records],
            "warmup": [r.run.run_id for r in warmup_records],
        },
        status_counts=status_counts,
        measurements=measurements,
        notes=notes,
    )


def aggregate_path(results_dir: Path, aggregate: RunPlanAggregate) -> Path:
    identity = aggregate.identity
    domain = str(identity.get("domain", "unknown"))
    adapter = str(identity.get("adapter", "unknown"))
    task_id = str(identity.get("task_id", "unknown"))
    return (
        Path(results_dir)
        / AGGREGATES_DIRNAME
        / domain
        / adapter
        / f"{task_id}-{aggregate.plan_id}.json"
    )


def persist_aggregate(results_dir: Path, aggregate: RunPlanAggregate) -> Path:
    path = aggregate_path(results_dir, aggregate)
    payload = aggregate.to_dict()
    canonical_json(payload)  # reject NaN / non-encodable before writing
    import json

    atomic_write_text(
        path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return path


def execute_plan(
    plan: RunPlan,
    results_dir: Path | None = None,
    enrich: bool = True,
    preflight: bool = True,
    publisher: ResultPublisher | None = None,
    debug: bool = False,
    plan_id: str | None = None,
) -> RunPlanAggregate:
    """Run a plan end to end: warmups, repeats, aggregate, and persist it."""
    results_dir = Path(results_dir or DEFAULT_RESULTS_DIR)
    plan_id = plan_id or new_plan_id()

    def _run(role: str, index: int) -> ResultRecord:
        context = {
            "plan_id": plan_id,
            "role": role,
            "index": index,
            "repeat": plan.repeat,
            "warmup": plan.warmup,
        }
        return execute(
            plan.spec,
            results_dir=results_dir,
            enrich=enrich,
            preflight=preflight,
            publisher=publisher,
            debug=debug,
            run_context=context,
        )

    warmup_records = [_run("warmup", i) for i in range(plan.warmup)]
    measured_records = [_run("measured", i) for i in range(plan.repeat)]

    aggregate = build_aggregate(plan_id, plan, warmup_records, measured_records)
    persist_aggregate(results_dir, aggregate)
    return aggregate
