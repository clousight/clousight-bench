import pytest

from clousight_bench.core.versioning import VersioningError, parse_version, range_contains


def test_parse_version_dotted():
    assert parse_version("1.0") == (1, 0)
    assert parse_version("2.3.1") == (2, 3, 1)


@pytest.mark.parametrize("bad", ["", "1.x", "a", "1..0", "1.-1"])
def test_parse_version_rejects_garbage(bad):
    with pytest.raises(VersioningError):
        parse_version(bad)


@pytest.mark.parametrize("rng,ver,ok", [
    (">=1.0,<2.0", "1.0", True),
    (">=1.0,<2.0", "1.9", True),
    (">=1.0,<2.0", "2.0", False),     # upper bound exclusive
    (">=1.0,<2.0", "0.9", False),
    ("==1.0", "1.0", True),
    ("==1.0", "1.1", False),
    (">1.0", "1.0", False),
    ("<=1.0", "1.0", True),
])
def test_range_contains(rng, ver, ok):
    assert range_contains(rng, ver) is ok


def test_range_rejects_bad_operator():
    with pytest.raises(VersioningError):
        range_contains("~=1.0", "1.0")


def test_range_rejects_empty():
    with pytest.raises(VersioningError):
        range_contains("", "1.0")
