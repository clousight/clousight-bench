"""Conformance checks for a domain plugin.

Pure functions the CLI (``csbench conformance``) and CI reuse. Each check returns
a ``CheckResult``; the caller decides how to render / exit. The built-in domains
must all pass, which makes this the core's own contract regression.

The checks are static (they never provision a cloud): they load the plugin,
verify its declared plugin-API range, and look for name conflicts. Emitted-record
conformance itself is guaranteed at a different layer -- the PERSIST gate
validates every record against the 0.4 schema -- so a successful ``csbench run``
already proves records conform.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from clousight_bench import PLUGIN_API_VERSION
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

    if platform is not None:
        results.append(_check_platform(pack, platform))

    return results


def check_evaluator(
    evaluator: object,
    suite_id: str,
    measurements: dict[str, Any],
    *,
    known_suite_ids: Sequence[str] = (),
) -> list[CheckResult]:
    """Conformance checks for a suite evaluator.

    Verifies the namespace/official invariant over a provided measurement dict:

    - Official evaluators (``official=True``) must emit ONLY keys namespaced
      ``"<suite_id>."`` and all measurements must have ``official=True``.
    - Custom evaluators (``official=False``) must emit ONLY keys namespaced
      ``"<evaluator_id>."`` and all measurements must have ``official=False``.

    A third check ``evaluator:no-suite-squatting`` is always appended.  For
    custom evaluators (``official=False``) it verifies that no emitted key
    starts with ``"<sid>."`` for any ``sid`` in ``known_suite_ids``.  For
    official evaluators it is a no-op passing check (output shape is stable).

    Args:
        evaluator: An ``Evaluator`` instance.
        suite_id: The canonical suite id (e.g. ``"swe-bench"``).
        measurements: The ``dict[str, Measurement]`` returned by
            ``evaluator.evaluate()``.
        known_suite_ids: All registered suite ids; used to detect namespace
            squatting by unofficial evaluators.
    """
    results: list[CheckResult] = []
    is_official: bool = bool(getattr(evaluator, "official", False))
    evaluator_id: str = str(getattr(evaluator, "evaluator_id", ""))

    expected_prefix = f"{suite_id}." if is_official else f"{evaluator_id}."
    expected_official = is_official

    bad_ns: list[str] = []
    bad_flag: list[str] = []

    for key, m in measurements.items():
        if not key.startswith(expected_prefix):
            bad_ns.append(key)
        m_official = bool(getattr(m, "official", False))
        if m_official != expected_official:
            bad_flag.append(key)

    ns_ok = len(bad_ns) == 0
    flag_ok = len(bad_flag) == 0

    ns_detail = (
        ""
        if ns_ok
        else (f"keys outside expected namespace {expected_prefix!r}: " + ", ".join(repr(k) for k in bad_ns))
    )
    flag_detail = (
        ""
        if flag_ok
        else (
            f"measurements with wrong official flag (expected {expected_official}): "
            + ", ".join(repr(k) for k in bad_flag)
        )
    )

    results.append(CheckResult("evaluator:namespace", ns_ok, ns_detail))
    results.append(CheckResult("evaluator:official-flag", flag_ok, flag_detail))

    # Anti-squatting check: always emit a CheckResult so output shape is stable.
    if is_official:
        results.append(
            CheckResult("evaluator:no-suite-squatting", True, "official evaluator; squatting check n/a")
        )
    else:
        squatted: list[str] = []
        for sid in known_suite_ids:
            prefix = f"{sid}."
            for key in measurements:
                if key.startswith(prefix):
                    squatted.append(key)
        sq_ok = len(squatted) == 0
        sq_detail = (
            ""
            if sq_ok
            else ("custom evaluator squats known suite namespace(s): " + ", ".join(repr(k) for k in squatted))
        )
        results.append(CheckResult("evaluator:no-suite-squatting", sq_ok, sq_detail))

    return results


def _check_platform(pack, platform: str) -> CheckResult:
    adapters = pack.adapters()
    if platform not in adapters:
        return CheckResult(
            f"platform:{platform}", False, f"platform {platform!r} is not one of {sorted(adapters)}"
        )
    return CheckResult(f"platform:{platform}", True, "adapter is declared")
