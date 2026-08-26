"""MockAgent for SWE-bench Verified suite.

Produces deterministic patches without running any model or Docker container.
Used by mock_artifacts() and the mock run() path.
"""

from __future__ import annotations

import json
from pathlib import Path

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_instances() -> dict[str, dict]:
    """Return a mapping instance_id -> instance dict from the bundled fixture."""
    data: list[dict] = json.loads((_FIXTURES_DIR / "instances_subset.json").read_text())
    return {inst["instance_id"]: inst for inst in data}


class MockAgent:
    """Patch generator that never calls a real model.

    ``patch_for(instance_id, kind)`` returns:
    - ``"gold"``  — the gold patch from the fixtures (non-empty string).
    - ``"empty"`` — an empty string (simulates a no-op / failed agent).
    """

    def __init__(self) -> None:
        self._instances: dict[str, dict] | None = None

    def _get_instances(self) -> dict[str, dict]:
        if self._instances is None:
            self._instances = _load_instances()
        return self._instances

    def patch_for(self, instance_id: str, kind: str) -> str:
        """Return a patch string for *instance_id* according to *kind*.

        Parameters
        ----------
        instance_id:
            A SWE-bench instance id (e.g. ``"django__django-11099"``).
        kind:
            ``"gold"`` returns the gold patch from fixtures.
            ``"empty"`` always returns ``""``.
        """
        if kind == "empty":
            return ""
        if kind == "gold":
            instances = self._get_instances()
            if instance_id in instances:
                return instances[instance_id]["patch"]
            # Unknown instance: return a minimal non-empty stub patch so
            # the manifest is still valid.
            return f"--- a/{instance_id}\n+++ b/{instance_id}\n@@ -0,0 +1 @@\n+# mock patch\n"
        raise ValueError(f"MockAgent: unknown kind {kind!r}; expected 'gold' or 'empty'")
