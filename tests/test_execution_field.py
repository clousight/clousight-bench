import pytest

from clousight_bench.core.fingerprints import environment_fingerprint
from clousight_bench.core.record import Environment, RecordError


def _env(**kw):
    base = dict(region="cn-hangzhou", mode="cloud", python_version="3.12.0", os_name="Linux")
    base.update(kw)
    return Environment(**base)


def test_execution_defaults_unknown_and_round_trips():
    env = _env()
    assert env.execution == "unknown"
    env2 = _env(execution="simulated")
    assert Environment.from_dict(env2.to_dict()).execution == "simulated"


def test_invalid_execution_rejected():
    with pytest.raises(RecordError):
        _env(execution="bogus")


def test_execution_changes_environment_fingerprint():
    sim = environment_fingerprint(region="r", mode="cloud", facts={}, execution="simulated")
    live = environment_fingerprint(region="r", mode="cloud", facts={}, execution="live")
    assert sim != live
    assert sim == environment_fingerprint(region="r", mode="cloud", facts={}, execution="simulated")


def test_fingerprint_default_execution_backwards_compatible():
    assert environment_fingerprint(region="r", mode="cloud", facts={}) == \
        environment_fingerprint(region="r", mode="cloud", facts={}, execution="unknown")
