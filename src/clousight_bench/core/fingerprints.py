"""Deterministic fingerprints for attributable result records.

The benchmark, environment and implementation fingerprints separate what was
measured, where it ran and which code measured it.  ``record_digest`` covers
the persisted payload while excluding its own field.
"""

from __future__ import annotations

import copy
from typing import Any

from clousight_bench.core import redaction
from clousight_bench.core.canonical import canonical_json, digest

UNKNOWN = "unknown"


def _safe(value: Any) -> Any:
    """Redact secrets and exact machine identities before hashing."""
    clean = redaction.redact(value)
    identities = set(redaction.identity_values())

    def scrub(node: Any) -> Any:
        if isinstance(node, dict):
            return {str(key): scrub(item) for key, item in node.items()}
        if isinstance(node, (list, tuple)):
            return [scrub(item) for item in node]
        if isinstance(node, str) and node in identities:
            return redaction.REDACTED
        return node

    return scrub(clean)


def benchmark_fingerprint(
    *,
    task_id: str,
    task_revision: str,
    scorer_revision: str,
    workload: str,
    workload_version: str,
    assets: list[dict[str, str]],
    params: dict[str, Any],
) -> str:
    safe = _safe(
        {
            "task_id": task_id,
            "task_revision": task_revision,
            "scorer_revision": scorer_revision,
            "workload": workload,
            "workload_version": workload_version,
            "assets": assets,
            "params": params,
        }
    )
    safe["assets"] = sorted(safe["assets"], key=canonical_json)
    return digest(safe)


def environment_fingerprint(
    *, region: str, mode: str, facts: dict[str, Any]
) -> str:
    return digest(_safe({"region": region, "mode": mode, "facts": facts}))


def implementation_fingerprint(
    *,
    core_version: str,
    domain: str,
    adapter: str,
    adapter_status: str,
    plugin_versions: dict[str, str],
) -> str:
    return digest(
        _safe(
            {
                "core_version": core_version,
                "domain": domain,
                "adapter": adapter,
                "adapter_status": adapter_status,
                "plugin_versions": plugin_versions,
            }
        )
    )


def record_digest(payload: dict[str, Any]) -> str:
    """Digest a persisted payload without mutating it or hashing itself."""
    body = copy.deepcopy(payload)
    fingerprints = body.get("fingerprints")
    if isinstance(fingerprints, dict):
        fingerprints.pop("record_digest", None)
    return digest(_safe(body))
