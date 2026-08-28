"""Architecture guard: core/ must never import domains/suites/viewer at module level.

The one sanctioned exception is a LAZY (function-body) import inside a
composition-root entrypoint (controller_main's build_reaper) — module-level
edges from core to provider code recreate the package cycle this test exists
to prevent.
"""

import ast
from pathlib import Path

CORE = Path(__file__).parent.parent / "src" / "clousight_bench" / "core"
FORBIDDEN_PREFIXES = (
    "clousight_bench.domains",
    "clousight_bench.suites",
    "clousight_bench.viewer",
)


def _module_level_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in tree.body:  # module level only — function bodies are exempt
        if isinstance(node, ast.Import):
            found.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
        elif isinstance(node, ast.If):  # TYPE_CHECKING blocks are typing-only
            continue
    return [m for m in found if m.startswith(FORBIDDEN_PREFIXES)]


def test_core_has_no_module_level_domain_imports():
    violations = {}
    for py in sorted(CORE.glob("*.py")):
        bad = _module_level_imports(py)
        if bad:
            violations[py.name] = bad
    assert not violations, f"core→provider module-level imports (recreates the package cycle): {violations}"
