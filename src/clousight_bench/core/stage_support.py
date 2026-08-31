"""Low-level stage plumbing shared by the orchestrator and the finalize stages.

These are the identity-scrubbing StageError builders and the debug-log writer
used across the stage machine (``core.orchestrator``) AND the post-run enrich /
publish stages (``core.finalize``). They live here so both can import them
without a cycle — this module depends only on record + redaction primitives.
"""

from __future__ import annotations

import logging
import traceback
from pathlib import Path

from clousight_bench.core.record import StageError
from clousight_bench.core.redaction import scrub_cloud_identifiers, scrub_identity_text

# Logger name intentionally pinned to the orchestrator (not __name__) so stage
# log records keep a single, stable source after this extraction — do not "fix".
logger = logging.getLogger("clousight_bench.core.orchestrator")


def scrubbed(error: StageError) -> StageError:
    """Stage messages quote paths, hosts and (from a real cloud SDK) account ids.

    The record must identify neither the operator's machine nor their cloud
    account: scrub the machine identity, then any embedded ARN / account id."""
    error.message = scrub_cloud_identifiers(scrub_identity_text(error.message))
    return error


def stage_error(stage: str, exc: BaseException, code: str | None = None) -> StageError:
    return scrubbed(
        StageError(
            stage=stage,
            code=code or f"{stage.lower()}_failed",
            type=type(exc).__name__,
            message=str(exc),
            retryable=isinstance(exc, (ConnectionError, TimeoutError, OSError)),
        )
    )


def log_traceback(results_dir: Path, run_id: str, debug: bool, exc: BaseException) -> None:
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
