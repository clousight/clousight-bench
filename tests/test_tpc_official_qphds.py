"""The official TPC-DS QphDS@SF formula (pure)."""

from __future__ import annotations

import math

import pytest

from clousight_bench.suites._tpc_official.metrics import qphds_at_size

_HOUR = 3600.0


def test_all_components_one_hour_gives_q_per_hour():
    # T_Power=1h/S=4 -> T_PT=4h ; TT1+TT2=... choose values making the 4th-root product 1:
    # T_PT=1, T_TT=1, T_DM=1, T_LD=1 (hours):
    #   T_Power=1/4h, TT1+TT2=1h, DM1+DM2=1h, T_Load=1/(0.01*4)=25h
    val = qphds_at_size(
        scale_factor=1.0,
        num_streams=4,
        num_queries=99,
        t_power_s=_HOUR / 4,
        t_tt1_s=_HOUR / 2,
        t_tt2_s=_HOUR / 2,
        t_dm1_s=_HOUR / 2,
        t_dm2_s=_HOUR / 2,
        t_load_s=25 * _HOUR,
    )
    # Q = 4*99 = 396; geo-mean of components = 1 -> floor(396.0)
    assert val == 396.0


def test_scales_linearly_with_sf():
    kwargs = dict(
        num_streams=4,
        num_queries=99,
        t_power_s=_HOUR / 4,
        t_tt1_s=_HOUR / 2,
        t_tt2_s=_HOUR / 2,
        t_dm1_s=_HOUR / 2,
        t_dm2_s=_HOUR / 2,
        t_load_s=25 * _HOUR,
    )
    assert qphds_at_size(scale_factor=10.0, **kwargs) == 3960.0


def test_result_is_floored_like_the_spec():
    val = qphds_at_size(
        scale_factor=1.0,
        num_streams=4,
        num_queries=99,
        t_power_s=_HOUR / 4,
        t_tt1_s=_HOUR / 2,
        t_tt2_s=_HOUR / 2,
        t_dm1_s=_HOUR / 2,
        t_dm2_s=_HOUR / 2,
        t_load_s=26 * _HOUR,  # slightly larger load -> non-integer result
    )
    assert val == math.floor(val)


def test_zero_time_component_rejected():
    with pytest.raises(ValueError):
        qphds_at_size(
            scale_factor=1.0,
            num_streams=4,
            num_queries=99,
            t_power_s=0.0,
            t_tt1_s=1.0,
            t_tt2_s=1.0,
            t_dm1_s=1.0,
            t_dm2_s=1.0,
            t_load_s=1.0,
        )
