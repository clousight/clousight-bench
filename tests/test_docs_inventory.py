"""Guard the generated task/adapter inventory block in docs/architecture.md.

The block between the ``generated:task-inventory`` markers is rendered from the
domain registry (via ``scripts/gen_docs.py``). If a task is added, renamed, or an
adapter changes status, the doc goes stale unless regenerated. This test fails
loudly on that drift — pointing the author at ``python scripts/gen_docs.py`` —
so a hand-typed number like a stale "28 tasks" can never ship again.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from clousight_bench.core.inventory import inventory

REPO_ROOT = Path(__file__).resolve().parent.parent
GEN_DOCS = REPO_ROOT / "scripts" / "gen_docs.py"
ARCHITECTURE_DOC = REPO_ROOT / "docs" / "architecture.mdx"


def _load_gen_docs():
    spec = importlib.util.spec_from_file_location("gen_docs", GEN_DOCS)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_architecture_inventory_is_not_stale():
    """docs/architecture.mdx must match a fresh render of the registry.

    Equivalent to `python scripts/gen_docs.py --check`; if this fails, run
    `python scripts/gen_docs.py` and commit the result.
    """
    gen_docs = _load_gen_docs()
    current = ARCHITECTURE_DOC.read_text(encoding="utf-8")
    regenerated = gen_docs.build_doc(current)
    assert current == regenerated, (
        "docs/architecture.mdx task-inventory block is stale — run `python scripts/gen_docs.py` and commit."
    )


def test_render_is_idempotent():
    """Rendering twice yields identical output (deterministic ordering)."""
    gen_docs = _load_gen_docs()
    payload = inventory()
    assert gen_docs.render_inventory(payload) == gen_docs.render_inventory(payload)


def test_doc_task_count_matches_registry():
    """A human-readable belt-and-suspenders guard on the agent-runtime count."""
    payload = inventory()
    agent_runtime = next(d for d in payload["domains"] if d["domain"] == "agent-runtime")
    n = len(agent_runtime["tasks"])
    doc = ARCHITECTURE_DOC.read_text(encoding="utf-8")
    assert f"`agent-runtime` — {n} tasks" in doc
