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


def qphds_at_size(
    *,
    scale_factor: float,
    num_streams: int,
    num_queries: int,
    t_power_s: float,
    t_tt1_s: float,
    t_tt2_s: float,
    t_dm1_s: float,
    t_dm2_s: float,
    t_load_s: float,
) -> float:
    """QphDS@SF — the official TPC-DS composite (spec clause 7.6.3), floored.

    ``QphDS@SF = floor( SF * Q / (T_PT * T_TT * T_DM * T_LD) ** 0.25 )`` with all
    components in hours: ``Q = S * num_queries``, ``T_PT = T_Power * S``,
    ``T_TT = TT1 + TT2``, ``T_DM = DM1 + DM2``, ``T_LD = 0.01 * S * T_Load``.
    Every input is wall-clock seconds; each component must be strictly positive.
    """
    s = int(num_streams)
    hours = 3600.0
    t_pt = (float(t_power_s) / hours) * s
    t_tt = (float(t_tt1_s) + float(t_tt2_s)) / hours
    t_dm = (float(t_dm1_s) + float(t_dm2_s)) / hours
    t_ld = 0.01 * s * (float(t_load_s) / hours)
    for label, v in (("T_PT", t_pt), ("T_TT", t_tt), ("T_DM", t_dm), ("T_LD", t_ld)):
        if v <= 0.0:
            raise ValueError(f"QphDS component {label} must be strictly positive, got {v!r}")
    q = s * int(num_queries)
    return float(math.floor(float(scale_factor) * q / (t_pt * t_tt * t_dm * t_ld) ** 0.25))
