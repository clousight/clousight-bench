import zipfile

from clousight_bench.domains.agent_runtime.agent_bundle.build import build_artifact


def test_build_zip_contains_sources(tmp_path):
    zpath = build_artifact(tmp_path)
    assert zpath.exists() and zpath.name == "agent.zip"
    with zipfile.ZipFile(zpath) as zf:
        names = set(zf.namelist())
    assert "agent.py" in names and "protocol.py" in names
