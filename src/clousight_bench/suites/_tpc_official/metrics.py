"""Pure official TPC-H metric formulas — no engine, no I/O.

The three official numbers (TPC-H spec, clause 5.4), computed from timing windows:

    Power@Size      = (3600 / geomean(QI[1..22], RI_RF1, RI_RF2)) * SF
    Throughput@Size = (S * Q / Ts) * 3600 * SF
    QphH@Size       = sqrt(Power@Size * Throughput@Size)

where ``QI``/``RI`` are per-query / per-refresh intervals in seconds, ``S`` the
number of query streams, ``Q`` the query count (22), and ``Ts`` the throughput
elapsed wall-clock in seconds. All are ``environmental`` — they drift with
hardware. These functions are the reusable core shared by ``tpc-ds`` (QphDS).
"""

from __future__ import annotations

import math
from collections.abc import Iterable


def geomean(values: Iterable[float]) -> float:
    """Geometric mean over strictly-positive values.

    Non-positive entries are dropped (real ``perf_counter`` deltas are always
    ``> 0``; the guard keeps a degenerate/crafted artifact from ``log(0)`` /
    ``log(-x)``). Raises ``ValueError`` when nothing positive remains.
    """
    positive = [float(v) for v in values if float(v) > 0.0]
    if not positive:
        raise ValueError("geomean needs at least one strictly-positive value")
    log_mean = math.fsum(math.log(v) for v in positive) / len(positive)
    return math.exp(log_mean)


def power_at_size(
    query_intervals_s: Iterable[float],
    refresh_intervals_s: Iterable[float],
    *,
    scale_factor: float,
) -> float:
    """Power@Size from the single-stream Power test's 24 timing windows (seconds)."""
    intervals = [*query_intervals_s, *refresh_intervals_s]
    return (3600.0 / geomean(intervals)) * float(scale_factor)


def throughput_at_size(
    *,
    num_streams: int,
    num_queries: int,
    elapsed_s: float,
    scale_factor: float,
) -> float:
    """Throughput@Size from the multi-stream Throughput test's elapsed wall-clock."""
    if float(elapsed_s) <= 0.0:
        raise ValueError("throughput elapsed_s must be strictly positive")
    return (int(num_streams) * int(num_queries) / float(elapsed_s)) * 3600.0 * float(scale_factor)


def qphh_at_size(power: float, throughput: float) -> float:
    """QphH@Size — the geometric mean of Power@Size and Throughput@Size."""
    return math.sqrt(float(power) * float(throughput))
