"""Benchmark asset resolution: bundled / remote(+checksum,cache) / private(license)."""

import hashlib

import pytest

from clousight_bench.core.assets import (
    AssetError,
    AssetSpec,
    NeedLicense,
    load_asset_specs,
    resolve_asset,
)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# --- spec parsing / validation ----------------------------------------------


def test_spec_requires_name_and_source():
    with pytest.raises(AssetError):
        AssetSpec.from_dict({"name": "x"})


def test_remote_requires_uri_and_license():
    with pytest.raises(AssetError):
        AssetSpec.from_dict({"name": "x", "source": "remote", "uri": "http://a"})  # no license
    with pytest.raises(AssetError):
        AssetSpec.from_dict({"name": "x", "source": "remote", "license": "MIT"})  # no uri


def test_identity_carries_no_contents():
    spec = AssetSpec("keys", "private", uri="ref", sha256="abc", version="2")
    ident = spec.identity()
    assert ident == {"name": "keys", "version": "2", "source": "private", "sha256": "abc"}


def test_load_asset_specs_from_manifest():
    manifest = {
        "assets": [
            {"name": "a", "source": "bundled", "uri": "data/a.bin"},
            {"name": "b", "source": "remote", "uri": "http://x/b", "license": "CC-BY", "sha256": "aa"},
        ]
    }
    specs = load_asset_specs(manifest)
    assert [s.name for s in specs] == ["a", "b"]
    assert specs[1].sha256 == "aa"


# --- bundled ----------------------------------------------------------------


def test_bundled_resolves_and_verifies(tmp_path):
    blob = b"hello-dataset"
    (tmp_path / "data").mkdir()
    f = tmp_path / "data" / "a.bin"
    f.write_bytes(blob)
    spec = AssetSpec("a", "bundled", uri="data/a.bin", sha256=_sha(blob))
    assert resolve_asset(spec, base_dir=tmp_path) == f.resolve()


def test_bundled_sha_mismatch_raises(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"actual")
    spec = AssetSpec("a", "bundled", uri="a.bin", sha256=_sha(b"expected-different"))
    with pytest.raises(AssetError, match="sha256 mismatch"):
        resolve_asset(spec, base_dir=tmp_path)


# --- remote (https URI; urlopen monkeypatched so no network) ----------------


def _serve(monkeypatch, payload: dict[str, bytes]):
    """Make assets.request.urlopen serve bytes for known https URIs, no network."""
    import io

    from clousight_bench.core import assets as assets_mod

    def _fake_urlopen(url, timeout=60):
        if url not in payload:
            raise AssetError(f"unexpected url {url!r}")
        return io.BytesIO(payload[url])

    monkeypatch.setattr(assets_mod.request, "urlopen", _fake_urlopen)


def test_remote_downloads_verifies_and_caches(tmp_path, monkeypatch):
    blob = b"remote-payload-123"
    uri = "https://datasets.example.com/src.bin"
    _serve(monkeypatch, {uri: blob})
    cache = tmp_path / "cache"
    spec = AssetSpec("r", "remote", uri=uri, sha256=_sha(blob), license="CC-BY", version="1")

    p1 = resolve_asset(spec, cache_dir=cache)
    assert p1.read_bytes() == blob
    assert p1.parent == cache

    # second call: checksum cache hit -> same path, even if the source is gone
    _serve(monkeypatch, {})  # source now unavailable
    p2 = resolve_asset(spec, cache_dir=cache)
    assert p2 == p1


def test_remote_sha_mismatch_raises(tmp_path, monkeypatch):
    uri = "https://datasets.example.com/src.bin"
    _serve(monkeypatch, {uri: b"data"})
    spec = AssetSpec("r", "remote", uri=uri, sha256=_sha(b"other"), license="CC-BY")
    with pytest.raises(AssetError, match="sha256 mismatch"):
        resolve_asset(spec, cache_dir=tmp_path / "c")


# --- private ----------------------------------------------------------------


def test_private_without_resolver_raises_need_license(tmp_path):
    spec = AssetSpec("keys", "private", uri="held-out/T4.1", sha256="aa")
    with pytest.raises(NeedLicense, match="licensed private asset"):
        resolve_asset(spec, cache_dir=tmp_path)


def test_private_with_injected_resolver(tmp_path):
    target = tmp_path / "resolved.bin"
    target.write_bytes(b"secret-keys")

    class _Resolver:
        name = "fake"

        def resolve(self, spec, cache_dir=None):
            return target

    spec = AssetSpec("keys", "private", uri="held-out/T4.1")
    assert resolve_asset(spec, private_resolver=_Resolver()) == target
