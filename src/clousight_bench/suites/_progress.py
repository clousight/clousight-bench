"""Suite-side helpers for reporting into the progress plane.

:mod:`clousight_bench.core.progress` owns the plane itself; this module holds the
small amount of shared machinery a *suite* needs to talk to it honestly:

``StepClock``
    One millisecond origin per run. A suite reports ``step(name, start_ms,
    end_ms)`` windows that the viewer lays out on a waterfall, so every step of a
    run must be measured against the SAME origin — including the ones emitted
    from ``prepare()`` (data load) and the ones emitted from ``run()``.

``raise_if_cancelled``
    Turns a cancel request into :class:`~clousight_bench.core.errors.RunCancelled`
    (a ``KeyboardInterrupt``), which the orchestrator already handles exactly
    right: teardown runs, an ``interrupted`` record is persisted, nothing is
    orphaned. A suite must never *return* on cancel — a truncated query set that
    looked like a finished run would be scored as one.

``TpcProgress``
    The reporting surface the engine-agnostic TPC official phase machine uses, so
    ``phases.py`` takes one optional argument instead of five. Its step names are
    built from the suite id to match, exactly, the span names
    ``_tpc_official/trace.py`` reconstructs into ``trajectory.jsonl`` — the live
    waterfall and the sealed waterfall must name the same thing the same way.

**Where reporting is allowed.** Progress writes are file appends: cheap, but not
free. Anything inside a region whose *wall clock* feeds a measurement stays
un-reported at fine grain. Concretely:

* per-query reporting in the Power test / the plain query set is safe — those
  metrics are built from per-query intervals measured tightly around each query,
  and a report lands in the gap between two queries, which nothing measures;
* the Throughput test's ``elapsed_s`` IS the metric (``Throughput@Size``,
  ``T_TT``), so nothing is written inside that window. Its per-stream, per-query
  steps are replayed from the measured intervals once the window has closed —
  same numbers, same layout, a few seconds late. Only ``should_cancel()`` is
  polled inside, and that is a 4-per-second cached ``stat``.
"""

from __future__ import annotations

from collections.abc import Callable
from time import perf_counter
from typing import Any

from clousight_bench.core.errors import RunCancelled
from clousight_bench.core.progress import NULL_PROGRESS, ProgressReporter

_MS = 1000.0


class StepClock:
    """The millisecond origin every step of one run is measured against.

    Reads the same ``perf_counter`` the suites already time with, so a step can be
    built from the very marks a measured interval used — no second, disagreeing
    timing is ever taken.
    """

    __slots__ = ("_clock", "_t0")

    def __init__(self, clock: Callable[[], float] = perf_counter) -> None:
        self._clock = clock
        self._t0 = clock()

    def now(self) -> float:
        """The raw clock value, to be handed back to :meth:`ms` later."""
        return self._clock()

    def ms(self, at: float | None = None) -> float:
        """Milliseconds from the origin to ``at`` (default: now)."""
        return ((self._clock() if at is None else at) - self._t0) * _MS


def raise_if_cancelled(progress: ProgressReporter, where: str) -> None:
    """Abort the run when a cancel has been requested.

    Raises rather than returns: a partially executed workload must never be
    mistaken for a finished one.
    """
    if progress.should_cancel():
        raise RunCancelled(f"cancel requested during {where}")


class TpcProgress:
    """What the official TPC phase machine reports through.

    Carries the run's reporter, the suite id (so step names match the sealed
    trajectory's span names) and the shared :class:`StepClock`. The default is
    fully inert, so an unwired caller — the reference-capture scripts, the phase
    machine's own unit tests — behaves exactly as before.
    """

    __slots__ = ("clock", "reporter", "suite_id")

    def __init__(
        self,
        reporter: ProgressReporter = NULL_PROGRESS,
        *,
        suite_id: str = "tpc",
        clock: StepClock | None = None,
    ) -> None:
        self.reporter = reporter
        self.suite_id = suite_id
        self.clock = clock if clock is not None else StepClock()

    # -- naming ---------------------------------------------------------------

    def name(self, leaf: str) -> str:
        """``<suite_id>.<leaf>`` — the span name the sealed trajectory will use."""
        return f"{self.suite_id}.{leaf}"

    @property
    def root(self) -> str:
        """The run's root span name (``<suite_id>.official``)."""
        return self.name("official")

    # -- passthrough ----------------------------------------------------------

    def phase(
        self, label: str, total: int = 0, *, unit: str = "query", reports_progress: bool = True
    ) -> None:
        self.reporter.phase(label, total, unit=unit, reports_progress=reports_progress)

    def advance(self, n: int = 1) -> None:
        self.reporter.advance(n)

    def log(self, msg: str) -> None:
        self.reporter.log(msg)

    def check_cancel(self, where: str) -> None:
        raise_if_cancelled(self.reporter, where)

    # -- steps ----------------------------------------------------------------

    def interval(self, leaf: str, start: float, end: float, *, parent: str = "") -> None:
        """One finished step, timed from the clock marks the phase already took."""
        self.reporter.step(
            self.name(leaf),
            self.clock.ms(start),
            self.clock.ms(end),
            parent=self.name(parent) if parent else "",
        )

    def query(self, leaf: str, start: float, end: float, *, parent: str, interval_s: float) -> None:
        """A finished query: its step, its latency as a sample, one unit advanced.

        ``interval_s`` is the interval the phase machine measured — the sample is
        a copy of it for the UI's lagging distribution and feeds nothing scored.
        """
        self.interval(leaf, start, end, parent=parent)
        self.reporter.sample(f"{self.suite_id}.latency_ms", interval_s * _MS)
        self.reporter.advance()

    def replay_throughput(self, tp: dict[str, Any], *, start: float, phase: str = "") -> None:
        """Draw a finished Throughput test, from ITS OWN measured intervals.

        Called after the window closed, because ``elapsed_s`` is the metric and
        must not carry this module's I/O. The layout mirrors
        ``_tpc_official/trace.py`` exactly: every query stream starts at the
        window start with its queries back-to-back, the refresh stream runs its
        pairs sequentially alongside. ``phase`` is empty for TPC-H's single
        Throughput test and ``throughput1``/``throughput2`` for TPC-DS's two.
        """
        prefix = f"{phase}." if phase else ""
        block = phase or "throughput"
        start_ms = self.clock.ms(start)
        done = 0
        for stream in tp.get("query_streams") or []:
            sid = int(stream.get("stream_id") or 0)
            stream_leaf = f"{prefix}stream{sid}"
            cursor = start_ms
            for q in stream.get("queries") or []:
                dur = float(q.get("interval_s") or 0.0) * _MS
                self.reporter.step(
                    self.name(f"{prefix}s{sid}.q{q.get('query_nr')}"),
                    cursor,
                    cursor + dur,
                    parent=self.name(stream_leaf),
                )
                self.reporter.sample(f"{self.suite_id}.latency_ms", dur)
                cursor += dur
                done += 1
            self.reporter.step(self.name(stream_leaf), start_ms, cursor, parent=self.name(block))
        r_cursor = start_ms
        for pair in tp.get("refresh_stream") or []:
            dur = (float(pair.get("rf1_s") or 0.0) + float(pair.get("rf2_s") or 0.0)) * _MS
            self.reporter.step(
                self.name(f"refresh-pair{pair.get('pair')}"),
                r_cursor,
                r_cursor + dur,
                parent=self.name(block),
            )
            r_cursor += dur
        end_ms = start_ms + float(tp.get("elapsed_s") or 0.0) * _MS
        self.reporter.step(self.name(block), start_ms, end_ms, parent=self.root)
        if done:
            self.reporter.advance(done)

    def finish_root(self) -> None:
        """Close the run's root step, spanning the whole clock frame."""
        self.reporter.step(self.root, 0.0, self.clock.ms())


#: Shared inert reporter for a phase-machine call that was never wired up.
INERT_TPC_PROGRESS = TpcProgress()
