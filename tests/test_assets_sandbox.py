import pytest

from clousight_bench.core.assets import AssetSpec, resolve_asset
from clousight_bench.core.sandbox import SandboxViolation


def test_bundled_path_traversal_rejected(tmp_path):
    spec = AssetSpec(name="evil", source="bundled", uri="../../etc/hostname")
    with pytest.raises(SandboxViolation):
        resolve_asset(spec, base_dir=tmp_path)


def test_remote_non_https_rejected():
    spec = AssetSpec(name="d", source="remote", uri="http://example.com/x.bin",
                     sha256="", license="CC0")
    with pytest.raises(SandboxViolation):
        resolve_asset(spec)


def test_remote_ssrf_rejected():
    spec = AssetSpec(name="d", source="remote", uri="https://169.254.169.254/latest",
                     sha256="", license="CC0")
    with pytest.raises(SandboxViolation):
        resolve_asset(spec)


def test_bundled_legit_relative_still_works(tmp_path):
    (tmp_path / "data.txt").write_text("hi", encoding="utf-8")
    spec = AssetSpec(name="d", source="bundled", uri="data.txt")
    got = resolve_asset(spec, base_dir=tmp_path)
    assert got == (tmp_path / "data.txt").resolve()
