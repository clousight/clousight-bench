"""Conformance checks for a domain plugin.

Pure functions the CLI (``csbench conformance``) and CI reuse. Each check returns
a ``CheckResult``; the caller decides how to render / exit. The built-in domains
must all pass, which makes this the core's own contract regression.

The checks are static (they never provision a cloud): they load the plugin,
verify its declared plugin-API range, look for name conflicts, and validate each
task's declared contract. Emitted-record conformance itself is guaranteed at a
different layer -- the PERSIST gate validates every record against the 0.2
schema -- so a successful ``csbench run`` already proves records conform.
"""

from __future__ import annotations

from dataclasses import dataclass

from clousight_bench import PLUGIN_API_VERSION
from clousight_bench.core.canonical import canonical_json
from clousight_bench.core.registry import check_domain_conflicts, get_domain
from clousight_bench.core.versioning import VersioningError, range_contains


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


def run_conformance(domain: str, platform: str | None = None) -> list[CheckResult]:
    """Run every conformance check for ``domain``; return one result per check.

    ``get_domain`` raises ``UnknownDomainError`` (a ``UserInputError``) when the
    domain is not installed -- the CLI maps that to exit code 2.
    """
    results: list[CheckResult] = []

    pack = get_domain(domain)
    results.append(CheckResult("load", True, f"domain {domain!r} loaded"))

    rng = getattr(pack, "requires_plugin_api", ">=1.0,<2.0")
    try:
        ok = range_contains(rng, PLUGIN_API_VERSION)
        detail = f"requires {rng!r}, core provides {PLUGIN_API_VERSION!r}"
    except VersioningError as exc:
        ok, detail = False, f"unparseable requires_plugin_api {rng!r}: {exc}"
    results.append(CheckResult("api-version", ok, detail))

    try:
        check_domain_conflicts(pack)
        results.append(CheckResult("no-conflicts", True, ""))
    except Exception as exc:  # noqa: BLE001 - report, don't crash
        results.append(CheckResult("no-conflicts", False, str(exc)))

    for tid, task_cls in pack.tasks().items():
        results.extend(_check_task(tid, task_cls))

    if platform is not None:
        results.append(_check_platform(pack, platform))

    return results


def _check_task(tid: str, task_cls: type) -> list[CheckResult]:
    out: list[CheckResult] = []
    label = f"task:{tid}"
    try:
        task = task_cls()
    except Exception as exc:  # noqa: BLE001
        return [CheckResult(f"{label}:construct", False, f"cannot instantiate: {exc}")]

    meta_ok = bool(getattr(task, "task_id", "")) and bool(getattr(task, "title", ""))
    out.append(CheckResult(f"{label}:meta", meta_ok, "" if meta_ok else "task_id/title must be non-empty"))

    revs_ok = bool(getattr(task, "task_revision", "")) and bool(getattr(task, "scorer_revision", ""))
    out.append(CheckResult(f"{label}:revisions", revs_ok, "task_revision/scorer_revision must be set"))

    try:
        cfg = task.config({})
        if not isinstance(cfg, dict):
            out.append(
                CheckResult(
                    f"{label}:config", False, f"config({{}}) must return a dict, got {type(cfg).__name__}"
                )
            )
        else:
            canonical_json(cfg)
            out.append(CheckResult(f"{label}:config", True, ""))
    except Exception as exc:  # noqa: BLE001
        out.append(CheckResult(f"{label}:config", False, f"config({{}}) failed: {exc}"))

    return out


def _check_platform(pack, platform: str) -> CheckResult:
    adapters = pack.adapters()
    if platform not in adapters:
        return CheckResult(
            f"platform:{platform}", False, f"platform {platform!r} is not one of {sorted(adapters)}"
        )
    return CheckResult(f"platform:{platform}", True, "adapter is declared")
