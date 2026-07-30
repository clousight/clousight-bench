import os

import pytest

from clousight_bench.core.sandbox import (
    SandboxViolation,
    resolve_within,
    validate_asset_uri,
)


def test_resolve_within_accepts_nested(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "f.txt").write_text("x", encoding="utf-8")
    got = resolve_within(tmp_path, "sub/f.txt")
    assert got == (tmp_path / "sub" / "f.txt").resolve()


@pytest.mark.parametrize("rel", ["../escape", "../../etc/passwd", "/etc/passwd"])
def test_resolve_within_rejects_escape(tmp_path, rel):
    with pytest.raises(SandboxViolation):
        resolve_within(tmp_path, rel)


def test_resolve_within_rejects_symlink_escape(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "link"
    os.symlink(outside, link)
    with pytest.raises(SandboxViolation):
        resolve_within(tmp_path, "link")


@pytest.mark.parametrize("uri", [
    "http://example.com/a", "file:///etc/passwd", "ftp://h/a",
    "https://localhost/a", "https://127.0.0.1/a", "https://169.254.169.254/latest",
])
def test_validate_asset_uri_rejects(uri):
    with pytest.raises(SandboxViolation):
        validate_asset_uri(uri)


def test_validate_asset_uri_accepts_public_https():
    validate_asset_uri("https://datasets.example.com/tpcds.tar.zst")


def test_validate_asset_uri_allow_hosts():
    validate_asset_uri("https://ok.example.com/a", allow_hosts=("ok.example.com",))
    with pytest.raises(SandboxViolation):
        validate_asset_uri("https://nope.example.com/a", allow_hosts=("ok.example.com",))
