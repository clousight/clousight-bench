import zipfile

from clousight_bench.domains.agent_runtime.artifact import build_agent_zip


def test_build_zip_contains_sources(tmp_path):
    # Single builder: the langchain-free zip still carries the agent + protocol.
    zpath = build_agent_zip(tmp_path / "agent.zip", with_langchain=False)
    assert zpath.exists() and zpath.name == "agent.zip"
    with zipfile.ZipFile(zpath) as zf:
        names = set(zf.namelist())
    assert "agent.py" in names and "protocol.py" in names
