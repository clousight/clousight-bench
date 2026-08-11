"""The vendor cache is keyed on _LC_DEPS, not just 'is the dir non-empty'.

No network: _build_vendor_dir is monkeypatched to a stub that writes one fake
file, so we can assert the rebuild-vs-reuse decision without a real pip install.
"""

import zipfile

from clousight_bench.domains.agent_runtime import artifact as A


def _fake_build(calls):
    def _build(vendor_path):
        vendor_path.mkdir(parents=True, exist_ok=True)
        (vendor_path / "fake_pkg.py").write_text("# vendored\n")
        calls.append(1)

    return _build


def test_vendor_cache_rebuilds_when_deps_change(tmp_path, monkeypatch):
    # Point the module's cache dir at a temp location via __file__ parent.
    pkg_dir = tmp_path / "agent_runtime"
    (pkg_dir / "agent_bundle").mkdir(parents=True)
    monkeypatch.setattr(A, "__file__", str(pkg_dir / "artifact.py"))

    calls = []
    monkeypatch.setattr(A, "_build_vendor_dir", _fake_build(calls))
    # Avoid packing real agent/protocol sources — only exercise the vendor path.
    monkeypatch.setattr(A, "_INCLUDE", ())
    monkeypatch.setattr(
        A.resources,
        "files",
        lambda pkg: type(
            "F",
            (),
            {"joinpath": lambda self, n: type("J", (), {"read_text": lambda self, **k: "# stub\n"})()},
        )(),
    )

    A.build_agent_zip_bytes(with_langchain=True)
    assert len(calls) == 1, "first build must install vendor deps"

    # Second call with unchanged deps -> cache reused, no rebuild.
    A.build_agent_zip_bytes(with_langchain=True)
    assert len(calls) == 1, "unchanged deps must reuse the cache"

    # Change _LC_DEPS -> fingerprint changes -> rebuild.
    monkeypatch.setattr(A, "_LC_DEPS", [*A._LC_DEPS, "some-new-dep==1.0"])
    data = A.build_agent_zip_bytes(with_langchain=True)
    assert len(calls) == 2, "changed deps must trigger a rebuild"
    # the stamp file must not leak into the packed zip
    names = set(zipfile.ZipFile(__import__("io").BytesIO(data)).namelist())
    assert not any(".deps-hash" in n for n in names)
