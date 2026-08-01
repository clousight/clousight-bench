"""P2-8: referenced integration docs must exist (no dangling doc references).

cn_clouds.py cites ``docs/agentrun-integration-research.md`` as the source of
its RAM action map. A comment that points at a missing file is a silent trap
before a wiring effort. This guards that every ``docs/....md`` a cn-cloud adapter
references actually exists.
"""
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CN_CLOUDS = _ROOT / "src/clousight_bench/domains/agent_runtime/adapters/cn_clouds.py"


def test_cn_cloud_adapter_doc_references_exist():
    text = _CN_CLOUDS.read_text(encoding="utf-8")
    refs = set(re.findall(r"docs/[\w./-]+\.md", text))
    assert refs, "expected at least one docs/*.md reference in cn_clouds.py"
    missing = [r for r in sorted(refs) if not (_ROOT / r).exists()]
    assert missing == [], f"cn_clouds.py references missing docs: {missing}"
