"""The progress plane: best-effort, disposable telemetry for an in-flight run.

A benchmark run writes nothing observable until it finishes: the sealed record
lands at the end, and ``emit_run_trace`` reconstructs the trace afterwards from
the measured ``stage_timings``. That is right for an audit artifact and useless
for watching a thirty-minute run.

This module adds a second, deliberately separate plane under
``results/.progress/<run_id>/``:

===================  ==================================================
``state.json``       the current snapshot, atomically replaced
``stream.jsonl``     an append-only, ordered event log
``cancel``           a zero-byte marker — the one thing a reader may create
===================  ==================================================

The plane is **best-effort and disposable**. Every write is contained so that an
IO failure logs at debug level and the run carries on, the stream is bounded so
a chatty suite cannot fill a disk, and the whole directory is removed once the
sealed record exists. Nothing here can move a verdict, and nothing here is
covered by ``record_digest`` — that is the point of keeping it separate.

Naming: ``live`` is already taken in this codebase for "against a real cloud,
money is moving" (``core/live_guard.py``, ``record.extensions.core.live_run``),
so the in-flight telemetry plane is called *progress* throughout the code. Only
the UI calls it 实时/Live.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Protocol

from clousight_bench.core.logsafe import sanitize_for_log

logger = logging.getLogger(__name__)

#: Subtree of results_dir that holds the progress plane. Dot-prefixed on
#: purpose: a ``state.json`` carrying a ``status`` field sits one careless
#: ``rglob("*.json")`` away from being mistaken for a result record, and the
#: repo already marks internal sidecar state this way (``.cost_ledger.json``).
PROGRESS_DIRNAME = ".progress"

#: Bumped when the on-disk shape changes incompatibly. Readers that do not
#: recognise a schema must ignore the directory rather than guess.
SCHEMA = "progress/1"

STATE_FILE = "state.json"
STREAM_FILE = "stream.jsonl"
CANCEL_FILE = "cancel"

#: Terminal progress statuses. ``interrupted`` is the existing record status a
#: cancel produces; ``abandoned`` exists only here, for a run whose process died
#: without ever writing a record.
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "failed", "invalid", "interrupted", "unsupported", "abandoned"}
)

#: run_ids name directories, so they must be plain tokens. Same shape the viewer
#: enforces before touching the filesystem.
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+\Z")

#: Stream caps. A benchmark must never be killed by its own telemetry, so past
#: these limits the writer drops the droppable kinds instead of growing.
_MAX_LINES = int(os.environ.get("CLOUSIGHT_PROGRESS_MAX_LINES", "20000"))
_MAX_BYTES = int(os.environ.get("CLOUSIGHT_PROGRESS_MAX_BYTES", str(8 * 1024 * 1024)))

#: Kinds that are dropped (in this order) once a cap is hit. ``stage`` and
#: ``step`` are never dropped: they are the shape of the run.
_DROPPABLE = ("log", "sample")

#: ``should_cancel`` is polled from inner loops, so the stat is cached briefly.
_CANCEL_POLL_S = 0.25

#: A message longer than this is truncated before it reaches the stream.
_MAX_LOG_CHARS = 500

#: How long a finished run's directory survives so a subscriber can still read
#: the ``done`` event and the sealed record's path out of it.
TERMINAL_GRACE_S = float(os.environ.get("CLOUSIGHT_PROGRESS_GRACE_S", "120"))


def progress_root(results_dir: Path) -> Path:
    """The directory holding every run's progress plane."""
    return Path(results_dir) / PROGRESS_DIRNAME


def progress_dir(results_dir: Path, run_id: str) -> Path | None:
    """This run's progress directory, or None when ``run_id`` cannot name one.

    ``run_id`` reaches here straight off an HTTP path (the viewer's progress and
    cancel routes), so this is the containment boundary for the whole plane.

    The token pattern alone is not enough: ``".."`` matches ``[A-Za-z0-9._-]+``
    perfectly well, and ``<results>/.progress/..`` is ``<results>`` — which
    would have let a cancel request create ``<results>/cancel`` and a stream
    read ``<results>/stream.jsonl``. So the relative segments are rejected by
    name, and the result is then resolved and required to stay under the
    progress root, belt and braces.
    """
    if not _RUN_ID_RE.match(run_id) or run_id in (".", ".."):
        return None
    root = progress_root(results_dir)
    candidate = root / run_id
    try:
        if not candidate.resolve().is_relative_to(root.resolve()):
            return None
    except OSError:  # unresolvable path (broken symlink, permissions)
        return None
    return candidate


class ProgressReporter(Protocol):
    """What a :class:`~clousight_bench.core.suite.BenchmarkSuite` may call.

    Handed to suites on ``DriverContext.progress``. Every method is a no-op on
    the null implementation, so a suite written against plugin API 3.0 — which
    never saw this field — keeps working untouched.
    """

    def phase(self, label: str, total: int = 0, *, unit: str = "", reports_progress: bool = True) -> None:
        """Start a named phase of ``total`` units (0 when the size is unknown).

        Set ``reports_progress=False`` when the phase knows its size but cannot
        tick through it — the classic case being a window whose own wall clock
        is the measurement, where writing anything inside would perturb the
        number. The viewer then shows an indeterminate bar and says why, rather
        than a 0% bar that reads as a hang.
        """

    def advance(self, n: int = 1, *, label: str = "") -> None:
        """Complete ``n`` more units of the current phase."""

    def step(
        self,
        name: str,
        start_ms: float,
        end_ms: float,
        *,
        status: str = "ok",
        parent: str = "",
    ) -> None:
        """Record a finished sub-step, so it can be drawn on the live waterfall."""

    def sample(self, key: str, value: float) -> None:
        """Publish an in-flight observation. Never a measurement: nothing scored
        is derived from a sample, and the UI labels it 初步/preliminary."""

    def log(self, msg: str, *, level: str = "INFO") -> None:
        """Publish one human-readable line to the live log pane."""

    def should_cancel(self) -> bool:
        """True once a cancel has been requested. Poll it in long loops and
        return early; the orchestrator also checks at every stage boundary."""


class NullProgressReporter:
    """The inert default. Costs a call and does nothing."""

    __slots__ = ()

    def phase(self, label: str, total: int = 0, *, unit: str = "", reports_progress: bool = True) -> None:
        return None

    def advance(self, n: int = 1, *, label: str = "") -> None:
        return None

    def step(
        self,
        name: str,
        start_ms: float,
        end_ms: float,
        *,
        status: str = "ok",
        parent: str = "",
    ) -> None:
        return None

    def sample(self, key: str, value: float) -> None:
        return None

    def log(self, msg: str, *, level: str = "INFO") -> None:
        return None

    def should_cancel(self) -> bool:
        return False


#: Shared inert instance — ``DriverContext.progress`` defaults to this so call
#: sites never need a ``if progress is not None`` guard.
NULL_PROGRESS: ProgressReporter = NullProgressReporter()


class ProgressWriter:
    """Owns one run's progress directory.

    Thread-safe: TPC throughput tests drive concurrent query streams through the
    same reporter, so every mutation takes the lock and the stream stays ordered
    by ``seq``.

    Constructed by the orchestrator, which alone may call :meth:`begin`,
    :meth:`stage_start`, :meth:`stage_end` and :meth:`finish`. Suites receive the
    same object typed as a :class:`ProgressReporter`, which exposes only the
    six suite-facing methods.
    """

    def __init__(
        self,
        results_dir: Path,
        run_id: str,
        *,
        trace_id: str = "",
        domain: str = "",
        task_id: str = "",
        adapter: str = "",
        suite_id: str = "",
        mode: str = "",
        started_at: str = "",
    ) -> None:
        self._dir = progress_dir(results_dir, run_id)
        self._lock = threading.Lock()
        self._seq = 0
        self._t0 = time.monotonic()
        self._lines = 0
        self._bytes = 0
        self._dropped: dict[str, int] = {}
        self._cancel_checked_at = 0.0
        self._cancel_seen = False
        self._enabled = self._dir is not None
        self._handler: logging.Handler | None = None
        self._state: dict[str, Any] = {
            "schema": SCHEMA,
            "run_id": run_id,
            "trace_id": trace_id,
            "pid": os.getpid(),
            "domain": domain,
            "task_id": task_id,
            "suite_id": suite_id,
            "adapter": adapter,
            "mode": mode,
            "started_at": started_at,
            "updated_at": started_at,
            "seq": 0,
            "lifecycle_phase": "",
            "stage": "",
            "stages": {},
            "stage_timings": {},
            "stage_started_ms": None,
            "step": None,
            "status": "running",
            "cancel_requested": False,
            "dropped": {},
            "record_path": None,
        }

    # ------------------------------------------------------------------
    # Lifecycle — orchestrator only
    # ------------------------------------------------------------------

    def begin(self) -> None:
        """Create the directory and publish the first snapshot."""
        if self._dir is None:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            (self._dir / STREAM_FILE).write_text("", encoding="utf-8")
        except OSError as exc:
            self._disable("create", exc)
            return
        with self._lock:
            self._write_state()
        self._attach_log_handler()

    def stage_start(self, stage: str) -> None:
        with self._lock:
            self._state["stage"] = stage
            self._state["lifecycle_phase"] = PHASE_OF_STAGE.get(stage, "")
            self._state["stage_started_ms"] = self._elapsed_ms()
            self._state["step"] = None
            self._append({"kind": "stage", "stage": stage, "status": "start"})
            self._write_state()

    def stage_end(self, stage: str, status: str, ms: float | None = None) -> None:
        with self._lock:
            stages = dict(self._state["stages"])
            stages[stage] = status
            self._state["stages"] = stages
            if ms is not None:
                timings = dict(self._state["stage_timings"])
                timings[stage] = ms
                self._state["stage_timings"] = timings
            self._state["stage_started_ms"] = None
            event: dict[str, Any] = {"kind": "stage", "stage": stage, "status": status}
            if ms is not None:
                event["ms"] = ms
            self._append(event)
            self._write_state()

    def stage_time(self, stage: str, ms: float) -> None:
        """Attach a measured duration to a stage. Timings are assigned separately
        from statuses in the orchestrator, so this is its own entry point."""
        with self._lock:
            timings = dict(self._state["stage_timings"])
            timings[stage] = ms
            self._state["stage_timings"] = timings
            self._write_state()

    def finish(
        self,
        status: str,
        record_path: str | None = None,
        *,
        stages: dict[str, str] | None = None,
        timings: dict[str, float] | None = None,
    ) -> None:
        """Publish the terminal snapshot and stop writing.

        The directory deliberately survives for :data:`TERMINAL_GRACE_S`. A
        subscriber polling on its own schedule must be able to observe the
        ``done`` event and the ``record_path`` it carries — deleting here would
        make the last thing the viewer sees a directory that vanished, which is
        indistinguishable from a crash. The reaper collects it afterwards.
        """
        if self._dir is None:
            return
        with self._lock:
            self._state["status"] = status
            self._state["record_path"] = record_path
            self._state["stage"] = ""
            self._state["step"] = None
            # The record is authoritative about how the run ended: the last few
            # stages (ENRICH/PERSIST/PUBLISH) are decided inside the persistence
            # path, on the record's own copy of the dict, and never reach the
            # mirror that feeds this plane.
            if stages is not None:
                self._state["stages"] = dict(stages)
            if timings is not None:
                self._state["stage_timings"] = dict(timings)
            self._append({"kind": "done", "status": status, "record_path": record_path})
            self._write_state()
        self._detach_log_handler()

    def discard(self) -> None:
        """Remove this run's progress directory now, without a grace period."""
        self._detach_log_handler()
        if self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)

    def _attach_log_handler(self) -> None:
        """Mirror this package's log records into the live log pane.

        Attached to the ``clousight_bench`` logger rather than root, so a chatty
        dependency cannot flood the stream, and detached on every exit path —
        a run plan drives many runs through one process, and a leaked handler
        would send run N's logs to run N-1's stream.
        """
        if not self._enabled or self._handler is not None:
            return
        self._handler = ProgressLogHandler(self)
        logging.getLogger("clousight_bench").addHandler(self._handler)

    def _detach_log_handler(self) -> None:
        if self._handler is not None:
            logging.getLogger("clousight_bench").removeHandler(self._handler)
            self._handler = None

    # ------------------------------------------------------------------
    # ProgressReporter surface — suites
    # ------------------------------------------------------------------

    def phase(self, label: str, total: int = 0, *, unit: str = "", reports_progress: bool = True) -> None:
        with self._lock:
            self._state["step"] = {
                "label": str(label),
                "completed": 0,
                "total": max(0, int(total)),
                "unit": str(unit),
                "reports_progress": bool(reports_progress),
                "started_ms": self._elapsed_ms(),
            }
            self._append(
                {"kind": "progress", "label": str(label), "completed": 0, "total": max(0, int(total))}
            )
            self._write_state()

    def advance(self, n: int = 1, *, label: str = "") -> None:
        with self._lock:
            step = dict(
                self._state["step"]
                or {"label": "", "completed": 0, "total": 0, "unit": "", "reports_progress": True}
            )
            step["completed"] = int(step.get("completed", 0)) + int(n)
            if label:
                step["label"] = str(label)
            step.setdefault("started_ms", self._elapsed_ms())
            self._state["step"] = step
            self._append(
                {
                    "kind": "progress",
                    "label": step["label"],
                    "completed": step["completed"],
                    "total": step.get("total", 0),
                }
            )
            self._write_state()

    def step(
        self,
        name: str,
        start_ms: float,
        end_ms: float,
        *,
        status: str = "ok",
        parent: str = "",
    ) -> None:
        with self._lock:
            self._append(
                {
                    "kind": "step",
                    "name": str(name),
                    "start_ms": float(start_ms),
                    "end_ms": float(end_ms),
                    "status": str(status),
                    "parent": str(parent),
                }
            )

    def sample(self, key: str, value: float) -> None:
        with self._lock:
            self._append({"kind": "sample", "key": str(key), "value": float(value)})

    def log(self, msg: str, *, level: str = "INFO") -> None:
        text = sanitize_for_log(msg, limit=_MAX_LOG_CHARS)
        with self._lock:
            self._append({"kind": "log", "level": str(level), "msg": text})

    def should_cancel(self) -> bool:
        if self._dir is None or self._cancel_seen:
            return self._cancel_seen
        now = time.monotonic()
        if now - self._cancel_checked_at < _CANCEL_POLL_S:
            return False
        self._cancel_checked_at = now
        if not (self._dir / CANCEL_FILE).exists():
            return False
        self._cancel_seen = True
        with self._lock:
            self._state["cancel_requested"] = True
            self._append({"kind": "cancel"})
            self._write_state()
        return True

    # ------------------------------------------------------------------
    # Internals — all callers already hold the lock
    # ------------------------------------------------------------------

    def _elapsed_ms(self) -> float:
        return round((time.monotonic() - self._t0) * 1000.0, 3)

    def _disable(self, what: str, exc: OSError) -> None:
        """Turn the plane off for the rest of the run. Telemetry is never fatal."""
        logger.debug("progress: %s failed, disabling progress plane: %s", what, exc)
        self._enabled = False

    def _append(self, event: dict[str, Any]) -> None:
        if not self._enabled or self._dir is None:
            return
        kind = str(event.get("kind", ""))
        if (self._lines >= _MAX_LINES or self._bytes >= _MAX_BYTES) and kind in _DROPPABLE:
            self._dropped[kind] = self._dropped.get(kind, 0) + 1
            self._state["dropped"] = dict(self._dropped)
            return
        self._seq += 1
        event["seq"] = self._seq
        event["t"] = self._elapsed_ms()
        line = json.dumps(event, ensure_ascii=False, default=str) + "\n"
        try:
            with (self._dir / STREAM_FILE).open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError as exc:
            self._disable("append", exc)
            return
        self._lines += 1
        self._bytes += len(line.encode("utf-8"))

    def _write_state(self) -> None:
        if not self._enabled or self._dir is None:
            return
        self._state["seq"] = self._seq
        self._state["updated_at"] = _utc_now()
        tmp = self._dir / (STATE_FILE + ".tmp")
        try:
            tmp.write_text(json.dumps(self._state, ensure_ascii=False, default=str), encoding="utf-8")
            os.replace(tmp, self._dir / STATE_FILE)
        except OSError as exc:
            self._disable("state write", exc)


#: Which lifecycle phase each stage belongs to, mirroring the record's grouping
#: so the live view and the sealed view label a stage the same way.
PHASE_OF_STAGE: dict[str, str] = {
    "VALIDATE": "prepare",
    "DESCRIBE": "prepare",
    "PREFLIGHT": "prepare",
    "SETUP": "connect",
    "TEARDOWN": "connect",
    "EXECUTE": "measure",
    "SEAL": "measure",
    "SCORE": "conclude",
    "ENRICH": "conclude",
    "PERSIST": "conclude",
    "PUBLISH": "conclude",
}


class ProgressLogHandler(logging.Handler):
    """Forwards ``clousight_bench`` log records into the run's live log pane.

    Attached to the package logger (not root) for the duration of a run, so a
    noisy dependency cannot flood the stream. Formatting failures are swallowed:
    a broken log line must not break a benchmark.
    """

    def __init__(self, writer: ProgressWriter) -> None:
        super().__init__()
        self._writer = writer

    def emit(self, record: logging.LogRecord) -> None:
        # This module logs when a write fails. Forwarding those back into the
        # writer would call the failing path again from inside its own handler.
        if record.name == __name__:
            return
        try:
            msg = record.getMessage()
        except (TypeError, ValueError):  # bad %-format in a caller's log call
            return
        self._writer.log(msg, level=record.levelname)


def _utc_now() -> str:
    from clousight_bench.core.schema import utc_now

    return utc_now()


def _pid_alive(pid: int) -> bool:
    """True when a process with ``pid`` still exists (POSIX signal-0 probe)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # exists, owned by someone else
        return True
    except OSError:
        return True
    return True


# ----------------------------------------------------------------------
# Readers — used by the viewer, never by the run
# ----------------------------------------------------------------------


def read_state(results_dir: Path, run_id: str) -> dict[str, Any] | None:
    """One run's current snapshot, or None when absent/unreadable/foreign-schema.

    Applies the same liveness check as :func:`list_active`: a snapshot still
    claiming to run while its process is gone is reported as ``abandoned``.
    """
    directory = progress_dir(results_dir, run_id)
    if directory is None:
        return None
    state = _read_state_file(directory / STATE_FILE)
    if state is None:
        return None
    if str(state.get("status") or "") not in TERMINAL_STATUSES and not _pid_alive(int(state.get("pid") or 0)):
        return dict(state, status="abandoned")
    return state


def _read_state_file(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        # A snapshot caught mid-replace is normal, not an error worth warning on.
        return None
    if not isinstance(loaded, dict) or loaded.get("schema") != SCHEMA:
        return None
    return loaded


def list_active(results_dir: Path) -> list[dict[str, Any]]:
    """Every run the progress plane still knows about, newest first.

    Includes a run that finished within :data:`TERMINAL_GRACE_S`, so a
    subscriber can still observe the handoff to the sealed record. A run whose
    process is gone without a terminal status is reported as ``abandoned``
    rather than left claiming to be running forever.
    """
    return [state for _, state in _scan(results_dir, reap=True)]


def _scan(results_dir: Path, *, reap: bool) -> list[tuple[Path, dict[str, Any]]]:
    """Walk the progress plane once, optionally collecting what is past due.

    The single place that decides whether a directory is live, abandoned, or
    garbage — so the listing and the reaper can never disagree about it.
    """
    root = progress_root(results_dir)
    if not root.is_dir():
        return []
    found: list[tuple[Path, dict[str, Any]]] = []
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        state = _read_state_file(directory / STATE_FILE)
        if state is None:
            # No readable snapshot. Only garbage-collect it once it is old
            # enough that it cannot be a directory being created right now.
            if reap and _age_s(directory) > TERMINAL_GRACE_S:
                _sweep(directory)
            continue
        status = str(state.get("status") or "")
        if status not in TERMINAL_STATUSES and not _pid_alive(int(state.get("pid") or 0)):
            status = "abandoned"
            state = dict(state, status=status)
        if status in TERMINAL_STATUSES and _age_s(directory / STATE_FILE) > TERMINAL_GRACE_S:
            if reap:
                _sweep(directory)
            continue
        found.append((directory, state))
    found.sort(key=lambda pair: str(pair[1].get("started_at", "")), reverse=True)
    return found


def _age_s(path: Path) -> float:
    """Seconds since ``path`` was last modified; +inf when it cannot be stat'd."""
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return float("inf")


def _sweep(directory: Path) -> None:
    shutil.rmtree(directory, ignore_errors=True)


def reap_stale(results_dir: Path) -> int:
    """Collect progress directories that are past due. Returns the count swept.

    Called when a run starts and when the viewer starts, so a machine that lost
    power mid-benchmark does not show a phantom "running" row forever.
    """
    before = _count_dirs(results_dir)
    _scan(results_dir, reap=True)
    return max(0, before - _count_dirs(results_dir))


def _count_dirs(results_dir: Path) -> int:
    root = progress_root(results_dir)
    if not root.is_dir():
        return 0
    return sum(1 for d in root.iterdir() if d.is_dir() and not d.name.startswith("."))


def read_events(results_dir: Path, run_id: str, *, since_seq: int = 0) -> list[dict[str, Any]]:
    """Stream events with ``seq > since_seq``. Tolerates a partially-written tail."""
    directory = progress_dir(results_dir, run_id)
    if directory is None:
        return []
    try:
        text = (directory / STREAM_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # The last line can be a partial append; anything earlier that fails
            # to parse is a line we simply skip rather than fail the stream on.
            continue
        if isinstance(event, dict) and int(event.get("seq") or 0) > since_seq:
            events.append(event)
    return events


def request_cancel(results_dir: Path, run_id: str) -> bool:
    """Create the cancel marker for a running run. False when there is nothing
    to cancel (unknown run_id, no progress directory, already terminal)."""
    directory = progress_dir(results_dir, run_id)
    if directory is None or not directory.is_dir():
        return False
    state = _read_state_file(directory / STATE_FILE)
    if state is None or state.get("status") in TERMINAL_STATUSES:
        return False
    try:
        (directory / CANCEL_FILE).touch()
    except OSError as exc:
        logger.warning("progress: could not request cancel for %s: %s", sanitize_for_log(run_id), exc)
        return False
    return True
