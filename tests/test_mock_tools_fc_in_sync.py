"""The standalone mock-tools FC vendors a byte-identical copy of the in-process
mock_tools module (it must run without clousight-bench installed). Guard against
drift: a fix to one (e.g. the auth path) must be mirrored to the other.
"""
from pathlib import Path


def test_fc_mock_tools_matches_the_package_module():
    root = Path(__file__).resolve().parents[1]
    pkg = root / "src/clousight_bench/domains/agent_runtime/mock_tools.py"
    fc = root / "infra/mock-tools-fc/mock_tools.py"
    assert fc.exists(), "the FC copy is packed by terraform archive_file; it must exist"
    assert pkg.read_text(encoding="utf-8") == fc.read_text(encoding="utf-8"), (
        "infra/mock-tools-fc/mock_tools.py has drifted from the package module — "
        "re-sync them (they are intentionally identical)."
    )
