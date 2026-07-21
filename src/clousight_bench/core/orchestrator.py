"""Orchestrator: the lifecycle state machine every domain shares.

    RESOLVE -> SETUP -> EXECUTE -> TEARDOWN -> RECORD

- RESOLVE : look up the domain pack, task and adapter for a RunSpec.
- SETUP   : adapter.setup() -- provision or connect to the system under test.
- EXECUTE : task.run(adapter, params) -- the task owns workload + scoring.
- TEARDOWN: adapter.teardown() -- guaranteed even when EXECUTE fails.
- RECORD  : wrap the TaskOutput into a ResultRecord (config_hash +
            runner_version mandatory) and write it to the results directory.

Failures never crash the suite: they are captured as ok=False records, because
"the platform failed" is itself a benchmark finding.
"""
from __future__ import annotations

import logging
from pathlib import Path

from clousight_bench.core.registry import get_domain
from clousight_bench.core.schema import ResultRecord, RunSpec, config_hash, new_run_id, utc_now
from clousight_bench.core.store import ResultStore

logger = logging.getLogger(__name__)

DEFAULT_RESULTS_DIR = Path("results")


def execute(spec: RunSpec, results_dir: Path | None = None) -> ResultRecord:
    """Run one RunSpec through the full lifecycle and persist the result."""
    results_dir = Path(results_dir or DEFAULT_RESULTS_DIR)

    # RESOLVE
    pack = get_domain(spec.domain)
    task_classes = pack.tasks()
    if spec.task_id not in task_classes:
        raise KeyError(f"task {spec.task_id!r} not in domain {spec.domain!r}: {sorted(task_classes)}")
    adapter_classes = pack.adapters()
    if spec.platform not in adapter_classes:
        raise KeyError(f"platform {spec.platform!r} not in domain {spec.domain!r}: {sorted(adapter_classes)}")
    task = task_classes[spec.task_id]()
    adapter = adapter_classes[spec.platform](spec.target)

    full_config = {
        "domain": spec.domain,
        "task": task.config(spec.params),
        "adapter": adapter.describe(),
    }
    run_id = new_run_id()
    started_at = utc_now()
    logger.info("run %s: %s/%s on %s", run_id, spec.domain, spec.task_id, spec.platform)

    # SETUP -> EXECUTE -> TEARDOWN
    output = None
    error: str | None = None
    try:
        adapter.setup()
        try:
            output = task.run(adapter, spec.params)
        finally:
            adapter.teardown()
    except Exception as exc:
        logger.exception("run %s failed", run_id)
        error = f"{type(exc).__name__}: {exc}"

    # RECORD
    if output is not None:
        record = ResultRecord(
            domain=spec.domain,
            task_id=spec.task_id,
            platform=spec.platform,
            run_id=run_id,
            started_at=started_at,
            finished_at=utc_now(),
            config_hash=config_hash(full_config),
            evidence_layer=output.evidence_layer,
            metrics=output.metrics,
            ok=output.ok,
            raw=output.raw,
            notes=output.notes,
        )
    else:
        record = ResultRecord(
            domain=spec.domain,
            task_id=spec.task_id,
            platform=spec.platform,
            run_id=run_id,
            started_at=started_at,
            finished_at=utc_now(),
            config_hash=config_hash(full_config),
            evidence_layer=task.evidence_layer,
            metrics={},
            ok=False,
            error=error,
        )

    _persist(record, results_dir)
    return record


def _persist(record: ResultRecord, results_dir: Path) -> Path:
    path = ResultStore(results_dir).persist(record)
    logger.info("result -> %s", path)
    return path
