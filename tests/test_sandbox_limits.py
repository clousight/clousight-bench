import pytest

from clousight_bench.core.sandbox import ResourceLimits, posix_rlimit_preexec


def test_defaults():
    lim = ResourceLimits()
    assert lim.cpu_s == 1800
    assert lim.mem_bytes == 2 << 30
    assert lim.fsize_bytes == 1 << 30
    assert lim.nofile == 1024


def test_from_target_overrides():
    lim = ResourceLimits.from_target({"limits": {"mem_mb": 512, "cpu_s": 60}})
    assert lim.mem_bytes == 512 * (1 << 20)
    assert lim.cpu_s == 60
    assert lim.fsize_bytes == 1 << 30  # untouched default


def test_from_target_disable_with_zero_or_none():
    lim = ResourceLimits.from_target({"limits": {"cpu_s": 0, "nofile": None}})
    assert lim.cpu_s is None
    assert lim.nofile is None


def test_from_target_absent():
    assert ResourceLimits.from_target({}) == ResourceLimits()


def test_preexec_none_on_non_posix(monkeypatch):
    monkeypatch.setattr("clousight_bench.core.sandbox.os.name", "nt")
    assert posix_rlimit_preexec(ResourceLimits()) is None


def test_preexec_callable_on_posix():
    import os

    if os.name != "posix":
        pytest.skip("posix only")
    assert callable(posix_rlimit_preexec(ResourceLimits()))
