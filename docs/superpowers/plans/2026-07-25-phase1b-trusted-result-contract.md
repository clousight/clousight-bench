# Phase 1B Trusted Result Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Clousight Bench result auditable: ResultRecord schema `0.2`, Tasks split into a side-effecting `execute()` and a pure `score()`, a lifecycle where a failure never leaks a resource nor loses an observation, deterministic benchmark/environment/implementation fingerprints, isolated extension failures, atomic persistence with an emergency fallback, and a deterministic non-destructive migrator for old `1.0` results.

**Architecture:** Value types first (canonical JSON, redaction, observations, record, fingerprints), then the Task contract with a temporary in-base bridge so the still-`1.0` orchestrator keeps working while all six built-in Tasks migrate one group at a time, then a single cutover task that rewrites the orchestrator, store and report onto `0.2` and deletes the bridge, then extension isolation, migration and cross-repository verification.

**Tech Stack:** Python 3.10–3.13, stdlib only for the new core modules (`json`, `hashlib`, `dataclasses`, `os`, `tempfile`, `platform`, `getpass`, `socket`), PyYAML for existing config, pytest, ruff, `uv`, GitHub Actions.

## Global Constraints

- Core package version stays `0.2.0` with classifier `Development Status :: 3 - Alpha`. `0.2.0` has never been published, so its `CHANGELOG.md` section is amended rather than superseded. **Do not bump the version** — Pro's `uv sync --frozen` locks against `clousight-bench 0.2.0` and a bump would break the Pro `core-compat` check.
- `ResultRecord.schema_version` is exactly the string `"0.2"`.
- `status` is exactly one of `"completed"`, `"failed"`, `"invalid"`, `"unsupported"`.
- `ok`, top-level `metrics`, top-level `evidence_layer` and `config_hash` are **removed** from new records. They survive only inside `extensions.legacy` on migrated records.
- Every digest is a full SHA-256 rendered as `"sha256:"` plus 64 lowercase hex characters. No truncation.
- Canonical JSON is UTF-8, object keys sorted, separators `(",", ":")`, `NaN`/`Infinity` rejected.
- Credentials, tokens, usernames, hostnames and raw environment variables must never reach a record or a fingerprint.
- `score()` is pure: no cloud credentials, no resource creation, no mutation of the observations it is given.
- All six built-in Tasks migrate in this plan. The bridge added in Task 6 is deleted in Task 12; no dual interface survives the plan.
- Migration never writes in place and never fabricates a fingerprint: unknown fingerprint fields are the literal string `"unknown"`.
- Every code change follows TDD: failing focused test, run it and see it fail, minimal implementation, run it and see it pass, run the affected suite, commit.
- All Core commits use DCO sign-off: `git commit -s`.
- Plain `gh` is authenticated as `legend91325` on this machine and must not be
  used for Clousight GitHub operations. Every GitHub command uses
  `/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh`, which
  injects the isolated `clousight-dev` config; do not mutate the global account.
- Core (`clousight/clousight-bench`) is public; Pro (`clousight/clousight-bench-pro`) is private. Never push Pro contents to Core.

## Plan Boundary

This plan implements Phase 1B only.

**Explicitly not in this plan:**

- Phase 1C — run plans, warmup, repeats, statistics, comparability reports.
- Phase 1D — plugin API version ranges, full JSON Schema, third-party conformance kit, workload supply-chain sandboxing, entry-point conflict governance.
- Real Aliyun/Huawei/Volcengine/AWS adapters.
- Hosted private assets, signing, upload, team reports or licensing.

Phase 1B ships the **boundary** for publishing (`ResultPublisher` plus explicit injection) but ships no hosted publisher.

## Prerequisites

`docs/superpowers/plans/2026-07-25-phase1a-delivery-gates.md` must be complete: Core `main` and Pro `main` both carry the Phase 1A work and both are protected. Create the working branch from the current Core `main`:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench
git checkout main
git pull --ff-only origin main
git worktree add /Users/bowang/IdeaProjects/clousight-bench/.worktrees/phase1b-trusted-result-contract -b feat/phase1b-trusted-result-contract main
cd /Users/bowang/IdeaProjects/clousight-bench/.worktrees/phase1b-trusted-result-contract
uv sync --all-extras
uv run pytest -q
```

Expected: the new worktree exists on branch `feat/phase1b-trusted-result-contract` and the inherited suite is green before any change.

Every Core path below is relative to `/Users/bowang/IdeaProjects/clousight-bench/.worktrees/phase1b-trusted-result-contract`.

## File Map

### Core modules created

| File | Responsibility |
|---|---|
| `src/clousight_bench/core/canonical.py` | canonical JSON encoding and SHA-256 digests |
| `src/clousight_bench/core/redaction.py` | secret scrubbing and machine-identity leak detection |
| `src/clousight_bench/core/observation.py` | `Measurement`, `Finding`, `ObservationBundle`, `TaskResult`, `collect()` |
| `src/clousight_bench/core/record.py` | ResultRecord `0.2` and its sub-structures |
| `src/clousight_bench/core/fingerprints.py` | the three fingerprints and the record digest |
| `src/clousight_bench/core/persistence.py` | atomic write and emergency-temp-dir write primitives |
| `src/clousight_bench/core/validation.py` | the VALIDATE stage |
| `src/clousight_bench/core/publish.py` | `ResultPublisher` boundary and append-only receipts |
| `src/clousight_bench/core/migrate.py` | deterministic schema `1.0` → `0.2` migration |

### Core modules modified

| File | Change |
|---|---|
| `src/clousight_bench/__init__.py` | export `RESULT_SCHEMA_VERSION` |
| `src/clousight_bench/core/plugin.py` | `execute`/`score` Task contract; `TaskOutput` deleted at cutover |
| `src/clousight_bench/core/schema.py` | keeps `RunSpec`/`utc_now`/`new_run_id`, re-exports `ResultRecord`, drops `config_hash` |
| `src/clousight_bench/core/orchestrator.py` | full lifecycle state machine |
| `src/clousight_bench/core/store.py` | persist ResultRecord `0.2` atomically with emergency fallback |
| `src/clousight_bench/core/report.py` | read `0.2` records, render measurements and findings |
| `src/clousight_bench/cli.py` | status-based exit codes, `--debug`, `migrate-results` |
| `src/clousight_bench/domains/agent_runtime/tasks/t1_2_state_persistence.py` | `execute`/`score` |
| `src/clousight_bench/domains/agent_runtime/tasks/t1_3_fault_recovery.py` | `execute`/`score` |
| `src/clousight_bench/domains/agent_runtime/tasks/t2_1_tool_registration.py` | `execute`/`score` |
| `src/clousight_bench/domains/agent_runtime/tasks/t4_1_trace_completeness.py` | `execute`/`score` |
| `src/clousight_bench/domains/agent_runtime/tasks/t4_2_otel_export.py` | `execute`/`score` |
| `src/clousight_bench/domains/bigdata_emr/tasks/j1_1_wordcount.py` | `execute`/`score`, `workload_identity` |
| `.github/workflows/ci.yml` | migration smoke and schema assertion |
| `README.md`, `CONTRIBUTING.md`, `docs/architecture.md`, `CHANGELOG.md` | the `0.2` contract |

### Core tests created

`tests/test_canonical.py`, `tests/test_redaction.py`, `tests/test_observation.py`, `tests/test_record.py`, `tests/test_fingerprints.py`, `tests/test_task_contract.py`, `tests/test_task_contract_agent_runtime.py`, `tests/test_task_contract_bigdata.py`, `tests/test_persistence.py`, `tests/test_validation.py`, `tests/test_lifecycle.py`, `tests/test_report.py`, `tests/test_extension_isolation.py`, `tests/test_migrate.py`

### Core tests modified

`tests/test_schema.py`, `tests/test_store.py`, `tests/test_enricher.py`, `tests/test_preflight.py`, `tests/test_agent_runtime_local.py`, `tests/test_agent_runtime_dimensions.py`, `tests/test_orchestrator_series_bridge.py`, `tests/test_bigdata_workload.py`

### Pro files modified (Task 15, repository `clousight-bench-pro`)

`packages/cb-pricing/src/cb_pricing/enricher.py`, `packages/cb-pricing/tests/test_pricing_enricher.py`

---

### Task 1: Canonical JSON and SHA-256 Digests

**Files:**
- Create: `src/clousight_bench/core/canonical.py`
- Test: `tests/test_canonical.py`

**Interfaces:**
- Produces: `CanonicalJSONError(ValueError)`.
- Produces: `canonical_json(value: Any) -> str` — UTF-8 text, object keys sorted, separators `(",", ":")`, `NaN`/`Infinity` rejected, `-0.0` normalised to `0.0`, tuples encoded as arrays, non-string object keys and unsupported types rejected.
- Produces: `digest(value: Any) -> str` — `"sha256:"` plus 64 lowercase hex characters of the SHA-256 of `canonical_json(value)` encoded UTF-8.
- Consumed by: Tasks 3, 4, 5, 10, 12, 14.

- [ ] **Step 1: Write the failing test**

Create `tests/test_canonical.py`:

```python
"""Canonical JSON: the single encoding every fingerprint and digest agrees on."""
import pytest

from clousight_bench.core.canonical import CanonicalJSONError, canonical_json, digest


def test_key_order_does_not_change_the_encoding():
    a = {"x": 1, "y": [1, 2], "z": {"b": 2, "a": 1}}
    b = {"z": {"a": 1, "b": 2}, "y": [1, 2], "x": 1}
    assert canonical_json(a) == canonical_json(b)
    assert canonical_json(a) == '{"x":1,"y":[1,2],"z":{"a":1,"b":2}}'


def test_no_insignificant_whitespace_and_unicode_is_not_escaped():
    assert canonical_json({"名字": "指北"}) == '{"名字":"指北"}'


def test_nan_and_infinity_are_rejected():
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(CanonicalJSONError):
            canonical_json({"v": bad})


def test_negative_zero_is_normalised():
    assert canonical_json({"v": -0.0}) == canonical_json({"v": 0.0})


def test_tuples_encode_as_arrays():
    assert canonical_json({"v": (1, 2)}) == canonical_json({"v": [1, 2]})


def test_non_string_keys_and_unsupported_types_are_rejected():
    with pytest.raises(CanonicalJSONError):
        canonical_json({1: "a"})
    with pytest.raises(CanonicalJSONError):
        canonical_json({"v": {1, 2}})


def test_digest_is_full_sha256_and_content_sensitive():
    value = digest({"x": 1})
    assert value.startswith("sha256:")
    assert len(value) == len("sha256:") + 64
    assert all(c in "0123456789abcdef" for c in value.removeprefix("sha256:"))
    assert digest({"x": 1}) != digest({"x": 2})
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_canonical.py -v`

Expected: collection FAIL with `ModuleNotFoundError: No module named 'clousight_bench.core.canonical'`.

- [ ] **Step 3: Write the implementation**

Create `src/clousight_bench/core/canonical.py`:

```python
"""Canonical JSON encoding and SHA-256 digests.

Every fingerprint and every record digest is computed from the same encoding,
so two runs that mean the same thing hash identically on any machine and in
any Python version: UTF-8, object keys sorted, no insignificant whitespace,
NaN/Infinity rejected, and deterministic scalar encoding.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any


class CanonicalJSONError(ValueError):
    """A value cannot be encoded as canonical JSON."""


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise CanonicalJSONError(f"non-finite float is not canonical: {value!r}")
        return 0.0 if value == 0.0 else value
    raise CanonicalJSONError(
        f"unsupported type for canonical JSON: {type(value).__name__}"
    )


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalJSONError(
                    f"object keys must be strings, got {type(key).__name__}: {key!r}"
                )
            out[key] = _canonicalize(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    return _scalar(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        _canonicalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value: Any) -> str:
    blob = canonical_json(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()
```

`bool` is listed before `int` matters only for readability here — both encode as
themselves, and `json` already renders `True` as `true`.

- [ ] **Step 4: Run the test and verify it passes**

Run: `uv run pytest tests/test_canonical.py -v`

Expected: 7 passed.

- [ ] **Step 5: Run ruff and the full suite**

Run:

```bash
uv run ruff check src tests
uv run pytest -q
```

Expected: ruff passes; the whole suite still passes (nothing consumes the new module yet).

- [ ] **Step 6: Commit**

```bash
git add src/clousight_bench/core/canonical.py tests/test_canonical.py
git commit -s -m "feat: add canonical JSON encoding and sha256 digests"
```

---

### Task 2: Redaction and Machine-Identity Leak Detection

**Files:**
- Create: `src/clousight_bench/core/redaction.py`
- Modify: `src/clousight_bench/core/plugin.py:177-190` (delete the private `_SECRET_HINTS`/`_redact` and call the shared module)
- Test: `tests/test_redaction.py`

**Interfaces:**
- Produces: `SECRET_HINTS: tuple[str, ...]`, `REDACTED: str = "<redacted>"`.
- Produces: `SensitiveDataError(RuntimeError)`.
- Produces: `redact(value: Any) -> Any` — recursive; any mapping key containing a secret hint (case-insensitive) has its value replaced by `REDACTED`.
- Produces: `identity_values() -> tuple[str, ...]` — the current username, hostname and FQDN, deduplicated, only entries of length 3 or more.
- Produces: `find_identity_leaks(payload: Any, identities: tuple[str, ...] | None = None) -> list[str]` — JSON-ish paths whose string value exactly equals one of the identity values.
- Consumed by: Tasks 5, 12 (`ResultStore`), 13.

- [ ] **Step 1: Write the failing test**

Create `tests/test_redaction.py`:

```python
"""Secrets and machine identity must never reach a record or a fingerprint."""
from clousight_bench.core.redaction import (
    REDACTED,
    find_identity_leaks,
    identity_values,
    redact,
)


def test_secret_named_keys_are_redacted_at_every_depth():
    payload = {
        "region": "cn-hangzhou",
        "access_key_id": "AKID",
        "nested": {"api_token": "t", "Password": "p", "plain": 1},
        "items": [{"client_secret": "s"}],
    }
    clean = redact(payload)
    assert clean["region"] == "cn-hangzhou"
    assert clean["access_key_id"] == REDACTED
    assert clean["nested"]["api_token"] == REDACTED
    assert clean["nested"]["Password"] == REDACTED
    assert clean["nested"]["plain"] == 1
    assert clean["items"][0]["client_secret"] == REDACTED


def test_redact_does_not_mutate_the_input():
    payload = {"token": "t"}
    redact(payload)
    assert payload == {"token": "t"}


def test_identity_values_are_non_empty_strings():
    values = identity_values()
    assert all(isinstance(v, str) and len(v) >= 3 for v in values)
    assert len(set(values)) == len(values)


def test_find_identity_leaks_reports_paths_for_exact_matches():
    leaks = find_identity_leaks(
        {"a": {"host": "build-box"}, "b": ["build-box", "other"]},
        identities=("build-box",),
    )
    assert leaks == ["$.a.host", "$.b[0]"]


def test_find_identity_leaks_ignores_substrings_and_clean_payloads():
    assert find_identity_leaks({"a": "build-box-2"}, identities=("build-box",)) == []
    assert find_identity_leaks({"a": 1, "b": None}, identities=("build-box",)) == []
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_redaction.py -v`

Expected: collection FAIL with `ModuleNotFoundError: No module named 'clousight_bench.core.redaction'`.

- [ ] **Step 3: Write the implementation**

Create `src/clousight_bench/core/redaction.py`:

```python
"""Keep credentials and machine identity out of records and fingerprints.

Two different jobs live here. ``redact`` scrubs values whose *key* looks like a
secret, and runs before anything is hashed or written. ``find_identity_leaks``
is the last line of defence: right before a record is persisted it looks for a
string that is exactly this machine's username, hostname or FQDN, because those
identify the operator rather than the benchmark.
"""
from __future__ import annotations

import getpass
import socket
from typing import Any

SECRET_HINTS: tuple[str, ...] = (
    "key",
    "secret",
    "token",
    "password",
    "passwd",
    "credential",
    "authorization",
)
REDACTED = "<redacted>"


class SensitiveDataError(RuntimeError):
    """A payload about to be persisted still carries identifying data."""


def redact(value: Any) -> Any:
    """Return a copy with secret-looking mapping values replaced."""
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if any(hint in name.lower() for hint in SECRET_HINTS):
                clean[name] = REDACTED
            else:
                clean[name] = redact(item)
        return clean
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


def identity_values() -> tuple[str, ...]:
    """This machine's operator-identifying strings, best effort."""
    found: list[str] = []
    for probe in (getpass.getuser, socket.gethostname, socket.getfqdn):
        try:
            value = probe()
        except Exception:  # noqa: BLE001 - identity probing must never break a run
            continue
        if isinstance(value, str) and len(value) >= 3:
            found.append(value)
    return tuple(dict.fromkeys(found))


def find_identity_leaks(
    payload: Any, identities: tuple[str, ...] | None = None
) -> list[str]:
    """Paths whose string value is exactly one of ``identities``."""
    known = identity_values() if identities is None else identities
    if not known:
        return []
    hits: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                walk(item, f"{path}.{key}")
        elif isinstance(node, (list, tuple)):
            for index, item in enumerate(node):
                walk(item, f"{path}[{index}]")
        elif isinstance(node, str) and node in known:
            hits.append(path)

    walk(payload, "$")
    return hits
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `uv run pytest tests/test_redaction.py -v`

Expected: 5 passed.

- [ ] **Step 5: Delete the duplicate redaction inside `plugin.py`**

In `src/clousight_bench/core/plugin.py`, delete the trailing `_SECRET_HINTS`
constant and the `_redact` function entirely, add the import near the other core
imports:

```python
from clousight_bench.core.redaction import redact
```

and change `ProviderAdapter.describe` to use it:

```python
    def describe(self) -> dict[str, Any]:
        """Non-secret target description, folded into the implementation fingerprint."""
        return {"adapter": self.name, "target": redact(self.target)}
```

- [ ] **Step 6: Run the adapter and plugin tests**

Run:

```bash
uv run pytest tests/test_redaction.py tests/test_plugin_registry.py tests/test_agent_runtime_local.py -v
uv run ruff check src tests
uv run pytest -q
```

Expected: everything passes; `describe()` behaves identically because `redact`
reproduces the deleted function's rules.

- [ ] **Step 7: Commit**

```bash
git add src/clousight_bench/core/redaction.py src/clousight_bench/core/plugin.py tests/test_redaction.py
git commit -s -m "feat: share redaction and detect machine-identity leaks"
```

---

### Task 3: Observation and Scored-Result Value Types

**Files:**
- Create: `src/clousight_bench/core/observation.py`
- Test: `tests/test_observation.py`

**Interfaces:**
- Produces: `EVIDENCE_LAYERS: tuple[str, ...] = ("A", "B", "C", "D")` and `SEVERITIES: tuple[str, ...] = ("info", "warning", "critical")`.
- Produces: `ObservationError(ValueError)`.
- Produces: `Measurement(value: Any, unit: str, evidence: str, aggregation: str = "", sample_count: int | None = None, notes: str = "")` with `to_dict() -> dict[str, Any]` that always emits `value`/`unit`/`evidence` and omits empty optionals.
- Produces: `Finding(code: str, severity: str, summary: str, evidence: str, details: dict[str, Any] = {})` with `to_dict()`.
- Produces: `ObservationBundle(observations: dict[str, Any] = {}, series: dict[str, list] = {}, artifacts: list[dict[str, Any]] = [])` with `to_dict()`.
- Produces: `TaskResult(measurements: dict[str, Measurement] = {}, findings: list[Finding] = [], notes: str = "", task_revision: str = "", scorer_revision: str = "", unsupported: bool = False)`.
- Produces: `TaskExecutionError(message: str, *, observations: ObservationBundle, code: str = "task_execute_failed", retryable: bool = False)` — an execute failure that carries every observation captured before the failure.
- Produces: `collect(bundle: ObservationBundle) -> ObservationBundle` — the COLLECT stage; validates and returns the same bundle.
- Consumed by: Tasks 6, 7, 8, 9, 12, 13, 14, 15.

- [ ] **Step 1: Write the failing test**

Create `tests/test_observation.py`:

```python
"""Observations stay raw; scoring conclusions live only in TaskResult."""
import pytest

from clousight_bench.core.canonical import CanonicalJSONError
from clousight_bench.core.observation import (
    Finding,
    Measurement,
    ObservationBundle,
    ObservationError,
    TaskExecutionError,
    TaskResult,
    collect,
)


def test_measurement_requires_value_unit_and_evidence():
    m = Measurement(value=12.5, unit="ms", evidence="C")
    assert m.to_dict() == {"value": 12.5, "unit": "ms", "evidence": "C"}


def test_measurement_emits_optional_fields_only_when_set():
    m = Measurement(value=1, unit="ms", evidence="C", aggregation="p99",
                    sample_count=100, notes="warm")
    assert m.to_dict() == {
        "value": 1, "unit": "ms", "evidence": "C",
        "aggregation": "p99", "sample_count": 100, "notes": "warm",
    }


def test_measurement_rejects_an_unknown_evidence_layer():
    with pytest.raises(ObservationError, match="evidence"):
        Measurement(value=1, unit="", evidence="Z")


def test_finding_requires_a_stable_code_and_known_severity():
    f = Finding(code="agent_runtime.state_ephemeral", severity="warning",
                summary="state lost on resume", evidence="C", details={"n": 1})
    assert f.to_dict()["code"] == "agent_runtime.state_ephemeral"
    with pytest.raises(ObservationError, match="code"):
        Finding(code="", severity="warning", summary="s", evidence="C")
    with pytest.raises(ObservationError, match="severity"):
        Finding(code="c", severity="fatal", summary="s", evidence="C")


def test_collect_accepts_a_well_formed_bundle_and_returns_it():
    bundle = ObservationBundle(
        observations={"attempts": [{"ok": True}]},
        series={"latency_ms": [[1, 10.0]]},
        artifacts=[{"kind": "trace", "path": "t.json", "media": "application/json",
                    "sha256": "sha256:ab"}],
    )
    assert collect(bundle) is bundle


def test_collect_rejects_a_non_bundle():
    with pytest.raises(ObservationError, match="ObservationBundle"):
        collect({"observations": {}})


def test_collect_rejects_non_finite_numbers():
    with pytest.raises(CanonicalJSONError):
        collect(ObservationBundle(observations={"v": float("nan")}))


def test_collect_rejects_malformed_series_points():
    with pytest.raises(ObservationError, match="latency_ms"):
        collect(ObservationBundle(series={"latency_ms": [[1, 2, 3]]}))


def test_collect_rejects_artifacts_without_a_pointer_or_digest():
    with pytest.raises(ObservationError, match="sha256"):
        collect(ObservationBundle(artifacts=[{"kind": "t", "path": "p", "media": "m"}]))
    with pytest.raises(ObservationError, match="pointer"):
        collect(ObservationBundle(
            artifacts=[{"kind": "t", "media": "m", "sha256": "sha256:ab"}]))


def test_task_result_defaults_are_empty_and_supported():
    result = TaskResult()
    assert result.measurements == {}
    assert result.findings == []
    assert result.unsupported is False


def test_task_execution_error_carries_partial_observations():
    bundle = ObservationBundle(observations={"attempts": [{"ok": False}]})
    error = TaskExecutionError(
        "tool failed", observations=bundle, code="tool_failed", retryable=True,
    )
    assert error.observations is bundle
    assert error.code == "tool_failed"
    assert error.retryable is True
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_observation.py -v`

Expected: collection FAIL with `ModuleNotFoundError: No module named 'clousight_bench.core.observation'`.

- [ ] **Step 3: Write the implementation**

Create `src/clousight_bench/core/observation.py`:

```python
"""The two halves of a Task: raw observations and the scored result.

``ObservationBundle`` is what ``Task.execute`` produces: raw, replayable
evidence with no conclusion in it. ``TaskResult`` is what ``Task.score``
derives from a bundle, and only ``score`` is allowed to draw conclusions. The
split is what makes a historical observation re-scorable when a scorer is
fixed, and it is why ``score`` never touches a cloud.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from clousight_bench.core.canonical import canonical_json

EVIDENCE_LAYERS: tuple[str, ...] = ("A", "B", "C", "D")
SEVERITIES: tuple[str, ...] = ("info", "warning", "critical")


class ObservationError(ValueError):
    """An observation bundle or scored result violates the Task contract."""


@dataclass
class Measurement:
    """One scored number or label, with the evidence that backs it."""

    value: Any
    unit: str
    evidence: str
    aggregation: str = ""
    sample_count: int | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if self.evidence not in EVIDENCE_LAYERS:
            raise ObservationError(
                f"evidence must be one of {EVIDENCE_LAYERS}, got {self.evidence!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "value": self.value,
            "unit": self.unit,
            "evidence": self.evidence,
        }
        if self.aggregation:
            out["aggregation"] = self.aggregation
        if self.sample_count is not None:
            out["sample_count"] = self.sample_count
        if self.notes:
            out["notes"] = self.notes
        return out


@dataclass
class Finding:
    """A stable, machine-readable statement about what the run showed."""

    code: str
    severity: str
    summary: str
    evidence: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.code:
            raise ObservationError("finding code must be a stable, non-empty string")
        if self.severity not in SEVERITIES:
            raise ObservationError(
                f"severity must be one of {SEVERITIES}, got {self.severity!r}"
            )
        if self.evidence not in EVIDENCE_LAYERS:
            raise ObservationError(
                f"evidence must be one of {EVIDENCE_LAYERS}, got {self.evidence!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "summary": self.summary,
            "evidence": self.evidence,
            "details": self.details,
        }


@dataclass
class ObservationBundle:
    """Raw, replayable evidence. Never a conclusion."""

    observations: dict[str, Any] = field(default_factory=dict)
    series: dict[str, list] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "observations": self.observations,
            "series": self.series,
            "artifacts": self.artifacts,
        }


@dataclass
class TaskResult:
    """What ``Task.score`` derives from an ObservationBundle."""

    measurements: dict[str, Measurement] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    notes: str = ""
    task_revision: str = ""
    scorer_revision: str = ""
    unsupported: bool = False


class TaskExecutionError(RuntimeError):
    """EXECUTE failed after producing observations that must remain auditable."""

    def __init__(
        self, message: str, *, observations: ObservationBundle,
        code: str = "task_execute_failed", retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.observations = observations
        self.code = code
        self.retryable = retryable


def collect(bundle: ObservationBundle) -> ObservationBundle:
    """COLLECT: prove the raw bundle is well formed and canonically encodable."""
    if not isinstance(bundle, ObservationBundle):
        raise ObservationError(
            f"execute() must return an ObservationBundle, got {type(bundle).__name__}"
        )
    canonical_json(bundle.to_dict())  # raises CanonicalJSONError on NaN / bad types
    for name, points in bundle.series.items():
        for point in points:
            if not (isinstance(point, (list, tuple)) and len(point) == 2):
                raise ObservationError(
                    f"series {name!r} points must be [t, value] pairs, got {point!r}"
                )
    for artifact in bundle.artifacts:
        missing = {"kind", "media", "sha256"} - set(artifact)
        if missing:
            raise ObservationError(
                f"artifact missing key(s) {sorted(missing)}: {artifact!r}"
            )
        if "path" not in artifact and "uri" not in artifact:
            raise ObservationError(
                f"artifact needs a path or uri pointer: {artifact!r}"
            )
    return bundle
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `uv run pytest tests/test_observation.py -v`

Expected: 11 passed.

- [ ] **Step 5: Run ruff and the full suite**

Run:

```bash
uv run ruff check src tests
uv run pytest -q
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/clousight_bench/core/observation.py tests/test_observation.py
git commit -s -m "feat: add observation bundle and scored result types"
```

---

### Task 4: ResultRecord 0.2

**Files:**
- Create: `src/clousight_bench/core/record.py`
- Test: `tests/test_record.py`

**Interfaces:**
- Produces: `SCHEMA_VERSION = "0.2"`, `STATUSES = ("completed", "failed", "invalid", "unsupported")`, `STAGES`, `STAGE_STATES = ("ok", "failed", "skipped")`, `MODES = ("local", "cloud", "unknown")`.
  `"unknown"` exists only so migrated 1.0 records can decline to guess; live runs
  always emit `"local"` or `"cloud"`.
- Produces: `RecordError(ValueError)`.
- Produces: `StageError(stage: str, code: str, type: str, message: str, retryable: bool = False)` with `to_dict()`.
- Produces: `RunInfo(run_id: str, started_at: str, finished_at: str, stages: dict[str, str] = {})` with `to_dict()`/`from_dict()`.
- Produces: `Identity(domain, task_id, task_revision, scorer_revision, adapter, adapter_status, core_version, workload="", workload_version="", plugin_versions={})` with `to_dict()`/`from_dict()`.
- Produces: `Environment(region, mode, python_version, os_name, facts={})` with `to_dict()`/`from_dict()`.
- Produces: `Fingerprints(benchmark, environment, implementation, record_digest="")` with `to_dict()`/`from_dict()`.
- Produces: `ResultRecord(run, identity, environment, fingerprints, status, measurements={}, findings=[], observations={}, series={}, artifacts=[], extensions={}, errors=[], schema_version="0.2")` with `to_dict()`, `to_json()` and `from_dict()`.
- Note: `schema.py` is **not** touched in this task, so the legacy `ResultRecord` and the whole existing suite keep working. The swap happens in Task 12.
- Consumed by: Tasks 5, 12, 13, 14, 15.

- [ ] **Step 1: Write the failing test**

Create `tests/test_record.py`:

```python
"""ResultRecord 0.2: the shape every reader and every plugin agrees on."""
import pytest

from clousight_bench.core.record import (
    Environment,
    Fingerprints,
    Identity,
    RecordError,
    ResultRecord,
    RunInfo,
    StageError,
)


def _record(**overrides):
    base = dict(
        run=RunInfo(run_id="run-1", started_at="2026-07-25T00:00:00Z",
                    finished_at="2026-07-25T00:00:01Z", stages={"EXECUTE": "ok"}),
        identity=Identity(domain="agent-runtime", task_id="T1.3", task_revision="2",
                          scorer_revision="2", adapter="local-sim",
                          adapter_status="reference", core_version="0.2.0"),
        environment=Environment(region="", mode="local", python_version="3.12.0",
                                os_name="Linux"),
        fingerprints=Fingerprints(benchmark="sha256:a", environment="sha256:b",
                                  implementation="sha256:c"),
        status="completed",
    )
    base.update(overrides)
    return ResultRecord(**base)


def test_schema_version_is_exactly_0_2():
    assert _record().to_dict()["schema_version"] == "0.2"


def test_top_level_keys_are_the_fixed_contract():
    assert set(_record().to_dict()) == {
        "schema_version", "run", "identity", "environment", "fingerprints",
        "measurements", "findings", "observations", "series", "artifacts",
        "extensions", "errors", "status",
    }


def test_legacy_fields_are_gone():
    payload = _record().to_dict()
    for gone in ("ok", "metrics", "evidence_layer", "config_hash", "raw", "notes"):
        assert gone not in payload


def test_status_must_be_one_of_the_four_values():
    with pytest.raises(RecordError, match="status"):
        _record(status="green")


def test_mode_must_be_a_known_value():
    with pytest.raises(RecordError, match="mode"):
        _record(environment=Environment(region="", mode="hybrid",
                                        python_version="3.12.0", os_name="Linux"))


def test_stage_error_carries_the_mandatory_fields():
    err = StageError(stage="EXECUTE", code="tool_plan_failed",
                     type="ConnectionError", message="boom", retryable=True)
    assert err.to_dict() == {
        "stage": "EXECUTE", "code": "tool_plan_failed",
        "type": "ConnectionError", "message": "boom", "retryable": True,
    }


def test_stage_error_rejects_an_unknown_stage():
    with pytest.raises(RecordError, match="stage"):
        StageError(stage="LAUNCH", code="c", type="T", message="m")


def test_round_trip_is_lossless():
    record = _record(
        measurements={"p99_ms": {"value": 9, "unit": "ms", "evidence": "C"}},
        findings=[{"code": "x.y", "severity": "warning", "summary": "s",
                   "evidence": "C", "details": {}}],
        observations={"attempts": [1, 2]},
        series={"latency_ms": [[1, 10.0]]},
        artifacts=[{"kind": "trace", "path": "t", "media": "m", "sha256": "sha256:a"}],
        extensions={"core": {"notes": "n"}},
        errors=[StageError(stage="TEARDOWN", code="teardown_failed",
                           type="OSError", message="m").to_dict()],
    )
    again = ResultRecord.from_dict(record.to_dict())
    assert again.to_dict() == record.to_dict()
    assert again.identity.task_revision == "2"
    assert again.run.stages == {"EXECUTE": "ok"}


def test_from_dict_rejects_a_legacy_record_with_a_migration_hint():
    with pytest.raises(RecordError, match="migrate-results"):
        ResultRecord.from_dict({"schema_version": "1.0", "domain": "d"})
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_record.py -v`

Expected: collection FAIL with `ModuleNotFoundError: No module named 'clousight_bench.core.record'`.

- [ ] **Step 3: Write the implementation**

Create `src/clousight_bench/core/record.py`:

```python
"""ResultRecord 0.2: one benchmark result, fully attributable.

Everything a reader needs to trust a number is a top-level field: which
benchmark ran (``identity`` + ``fingerprints.benchmark``), where it ran
(``environment`` + ``fingerprints.environment``), which code produced it
(``fingerprints.implementation``), what was measured (``measurements``), what
it means (``findings``), what was actually seen (``observations`` / ``series``
/ ``artifacts``), what went wrong (``errors``) and how it ended (``status``).

There is no ``ok`` flag: a run is ``completed``, ``failed``, ``invalid`` or
``unsupported``, and every one of those is a legitimate benchmark outcome.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "0.2"

STATUSES: tuple[str, ...] = ("completed", "failed", "invalid", "unsupported")
STAGES: tuple[str, ...] = (
    "RESOLVE",
    "VALIDATE",
    "PREFLIGHT",
    "SETUP",
    "EXECUTE",
    "COLLECT",
    "TEARDOWN",
    "SCORE",
    "ENRICH",
    "PERSIST",
    "PUBLISH",
)
STAGE_STATES: tuple[str, ...] = ("ok", "failed", "skipped")
MODES: tuple[str, ...] = ("local", "cloud", "unknown")


class RecordError(ValueError):
    """A record or one of its parts violates the 0.2 contract."""


@dataclass
class StageError:
    """One lifecycle-stage failure, attributable to the stage that produced it."""

    stage: str
    code: str
    type: str
    message: str
    retryable: bool = False

    def __post_init__(self) -> None:
        if self.stage not in STAGES:
            raise RecordError(f"stage must be one of {STAGES}, got {self.stage!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "code": self.code,
            "type": self.type,
            "message": self.message,
            "retryable": self.retryable,
        }


@dataclass
class RunInfo:
    run_id: str
    started_at: str
    finished_at: str
    stages: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, state in self.stages.items():
            if name not in STAGES:
                raise RecordError(f"unknown stage {name!r}")
            if state not in STAGE_STATES:
                raise RecordError(f"stage {name!r} state must be one of {STAGE_STATES}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "stages": dict(self.stages),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunInfo:
        return cls(
            run_id=str(data["run_id"]),
            started_at=str(data["started_at"]),
            finished_at=str(data["finished_at"]),
            stages=dict(data.get("stages", {})),
        )


@dataclass
class Identity:
    domain: str
    task_id: str
    task_revision: str
    scorer_revision: str
    adapter: str
    adapter_status: str
    core_version: str
    workload: str = ""
    workload_version: str = ""
    plugin_versions: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "task_id": self.task_id,
            "task_revision": self.task_revision,
            "scorer_revision": self.scorer_revision,
            "adapter": self.adapter,
            "adapter_status": self.adapter_status,
            "core_version": self.core_version,
            "workload": self.workload,
            "workload_version": self.workload_version,
            "plugin_versions": dict(self.plugin_versions),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Identity:
        return cls(
            domain=str(data["domain"]),
            task_id=str(data["task_id"]),
            task_revision=str(data["task_revision"]),
            scorer_revision=str(data["scorer_revision"]),
            adapter=str(data["adapter"]),
            adapter_status=str(data["adapter_status"]),
            core_version=str(data["core_version"]),
            workload=str(data.get("workload", "")),
            workload_version=str(data.get("workload_version", "")),
            plugin_versions=dict(data.get("plugin_versions", {})),
        )


@dataclass
class Environment:
    region: str
    mode: str
    python_version: str
    os_name: str
    facts: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise RecordError(f"mode must be one of {MODES}, got {self.mode!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "region": self.region,
            "mode": self.mode,
            "python_version": self.python_version,
            "os_name": self.os_name,
            "facts": dict(self.facts),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Environment:
        return cls(
            region=str(data["region"]),
            mode=str(data["mode"]),
            python_version=str(data["python_version"]),
            os_name=str(data["os_name"]),
            facts=dict(data.get("facts", {})),
        )


@dataclass
class Fingerprints:
    benchmark: str
    environment: str
    implementation: str
    record_digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark": self.benchmark,
            "environment": self.environment,
            "implementation": self.implementation,
            "record_digest": self.record_digest,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Fingerprints:
        return cls(
            benchmark=str(data["benchmark"]),
            environment=str(data["environment"]),
            implementation=str(data["implementation"]),
            record_digest=str(data.get("record_digest", "")),
        )


@dataclass
class ResultRecord:
    """One benchmark result in schema 0.2.

    ``observations`` holds the raw evidence a re-score would replay. When that
    evidence is too large to inline, a Task stores an artifact pointer instead
    — ``{"trace": {"$artifact": "trace.jsonl"}}`` — where the value names an
    entry in ``artifacts``. Either shape is valid; ``observations`` must never
    be dropped just because the payload is big.
    """

    run: RunInfo
    identity: Identity
    environment: Environment
    fingerprints: Fingerprints
    status: str
    measurements: dict[str, dict[str, Any]] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)
    observations: dict[str, Any] = field(default_factory=dict)
    series: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    extensions: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise RecordError(f"status must be one of {STATUSES}, got {self.status!r}")
        if self.schema_version != SCHEMA_VERSION:
            raise RecordError(
                f"schema_version must be {SCHEMA_VERSION!r}, got {self.schema_version!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run": self.run.to_dict(),
            "identity": self.identity.to_dict(),
            "environment": self.environment.to_dict(),
            "fingerprints": self.fingerprints.to_dict(),
            "measurements": dict(self.measurements),
            "findings": list(self.findings),
            "observations": dict(self.observations),
            "series": dict(self.series),
            "artifacts": list(self.artifacts),
            "extensions": dict(self.extensions),
            "errors": list(self.errors),
            "status": self.status,
        }

    def to_json(self) -> str:
        import json

        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResultRecord:
        version = str(data.get("schema_version", ""))
        if version != SCHEMA_VERSION:
            raise RecordError(
                f"unsupported schema_version {version!r}; run "
                f"`csbench migrate-results <dir> --output <dir>` to convert it to "
                f"{SCHEMA_VERSION!r}"
            )
        return cls(
            run=RunInfo.from_dict(data["run"]),
            identity=Identity.from_dict(data["identity"]),
            environment=Environment.from_dict(data["environment"]),
            fingerprints=Fingerprints.from_dict(data["fingerprints"]),
            status=str(data["status"]),
            measurements=dict(data.get("measurements", {})),
            findings=list(data.get("findings", [])),
            observations=dict(data.get("observations", {})),
            series=dict(data.get("series", {})),
            artifacts=list(data.get("artifacts", [])),
            extensions=dict(data.get("extensions", {})),
            errors=list(data.get("errors", [])),
        )
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `uv run pytest tests/test_record.py -v`

Expected: 9 passed.

- [ ] **Step 5: Run ruff and the full suite**

Run:

```bash
uv run ruff check src tests
uv run pytest -q
```

Expected: all green. `core/schema.py` is untouched, so the legacy record and the
current orchestrator still work.

- [ ] **Step 6: Commit**

```bash
git add src/clousight_bench/core/record.py tests/test_record.py
git commit -s -m "feat: add ResultRecord 0.2 alongside the legacy record"
```

---

### Task 5: Benchmark, Environment, Implementation Fingerprints and the Record Digest

**Files:**
- Create: `src/clousight_bench/core/fingerprints.py`
- Test: `tests/test_fingerprints.py`

**Interfaces:**
- Produces: `UNKNOWN = "unknown"`.
- Produces: `benchmark_fingerprint(*, task_id: str, task_revision: str, scorer_revision: str, workload: str, workload_version: str, assets: list[dict[str, str]], params: dict[str, Any]) -> str`.
- Produces: `environment_fingerprint(*, region: str, mode: str, facts: dict[str, Any]) -> str`.
- Produces: `implementation_fingerprint(*, core_version: str, domain: str, adapter: str, adapter_status: str, plugin_versions: dict[str, str]) -> str`.
- Produces: `record_digest(payload: dict[str, Any]) -> str` — digests the persisted payload with `fingerprints.record_digest` removed, so it never digests itself.
- All four return the `digest()` format from Task 1. All apply `redact()` from Task 2 to free-form maps.
- Consumed by: Tasks 12, 14.

- [ ] **Step 1: Write the failing test**

Create `tests/test_fingerprints.py`:

```python
"""Fingerprints must be deterministic, meaning-sensitive and secret-free."""
from clousight_bench.core.fingerprints import (
    benchmark_fingerprint,
    environment_fingerprint,
    implementation_fingerprint,
    record_digest,
)
from clousight_bench.core.redaction import REDACTED


def _benchmark(**overrides):
    kwargs = dict(
        task_id="T1.3", task_revision="2", scorer_revision="2",
        workload="wordcount-py", workload_version="0.1.0",
        assets=[{"name": "corpus", "version": "1", "source": "bundled", "sha256": "ab"}],
        params={"rows": 100, "seed": 42},
    )
    kwargs.update(overrides)
    return benchmark_fingerprint(**kwargs)


def test_benchmark_fingerprint_is_stable_and_full_sha256():
    value = _benchmark()
    assert value == _benchmark()
    assert value.startswith("sha256:")
    assert len(value) == len("sha256:") + 64


def test_benchmark_fingerprint_changes_with_every_controlled_input():
    base = _benchmark()
    assert _benchmark(task_revision="3") != base
    assert _benchmark(scorer_revision="3") != base
    assert _benchmark(workload_version="0.2.0") != base
    assert _benchmark(params={"rows": 200, "seed": 42}) != base
    assert _benchmark(assets=[]) != base


def test_benchmark_fingerprint_ignores_asset_ordering():
    a = {"name": "a", "version": "1", "source": "bundled", "sha256": "1"}
    b = {"name": "b", "version": "1", "source": "remote", "sha256": "2"}
    assert _benchmark(assets=[a, b]) == _benchmark(assets=[b, a])


def test_benchmark_fingerprint_never_hashes_a_secret_param():
    with_secret = _benchmark(params={"rows": 100, "seed": 42, "api_token": "t1"})
    other_secret = _benchmark(params={"rows": 100, "seed": 42, "api_token": "t2"})
    assert with_secret == other_secret  # both redact to <redacted>


def test_environment_fingerprint_covers_region_mode_and_facts_only():
    base = environment_fingerprint(region="cn-hangzhou", mode="cloud",
                                   facts={"runtime": "agentrun"})
    assert base == environment_fingerprint(region="cn-hangzhou", mode="cloud",
                                           facts={"runtime": "agentrun"})
    assert base != environment_fingerprint(region="cn-beijing", mode="cloud",
                                           facts={"runtime": "agentrun"})
    assert base != environment_fingerprint(region="cn-hangzhou", mode="local",
                                           facts={"runtime": "agentrun"})
    assert base != environment_fingerprint(region="cn-hangzhou", mode="cloud",
                                           facts={"runtime": "other"})


def test_implementation_fingerprint_covers_core_domain_adapter_and_plugins():
    base = implementation_fingerprint(core_version="0.2.0", domain="agent-runtime",
                                      adapter="local-sim", adapter_status="reference",
                                      plugin_versions={"clousight-bench": "0.2.0"})
    assert base != implementation_fingerprint(
        core_version="0.2.1", domain="agent-runtime", adapter="local-sim",
        adapter_status="reference", plugin_versions={"clousight-bench": "0.2.0"})
    assert base != implementation_fingerprint(
        core_version="0.2.0", domain="agent-runtime", adapter="local-sim",
        adapter_status="reference",
        plugin_versions={"clousight-bench": "0.2.0", "cb-pricing": "0.1.0"})


def test_record_digest_excludes_itself_and_is_stable():
    payload = {
        "schema_version": "0.2",
        "status": "completed",
        "fingerprints": {"benchmark": "sha256:a", "environment": "sha256:b",
                         "implementation": "sha256:c", "record_digest": ""},
    }
    first = record_digest(payload)
    payload["fingerprints"]["record_digest"] = first
    assert record_digest(payload) == first


def test_record_digest_does_not_mutate_the_payload():
    payload = {"fingerprints": {"benchmark": "sha256:a", "environment": "sha256:b",
                                "implementation": "sha256:c",
                                "record_digest": "sha256:old"}}
    record_digest(payload)
    assert payload["fingerprints"]["record_digest"] == "sha256:old"


def test_redaction_constant_is_the_one_used_by_fingerprints():
    assert REDACTED == "<redacted>"
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_fingerprints.py -v`

Expected: collection FAIL with `ModuleNotFoundError: No module named 'clousight_bench.core.fingerprints'`.

- [ ] **Step 3: Write the implementation**

Create `src/clousight_bench/core/fingerprints.py`:

```python
"""The three fingerprints that make a result attributable, plus its digest.

Split by what changes them, because that is what a reader needs to reason about:

- ``benchmark``       — what was measured. Changes when the task, the scorer,
                        the workload, an asset or a controlled parameter changes.
- ``environment``     — where it was measured. Changes with region, mode or the
                        environment facts the task itself declares.
- ``implementation``  — which code measured it. Changes with the core, the
                        domain pack, the adapter or any installed plugin.

``record_digest`` covers the persisted payload itself and is computed with its
own field removed, so it can live inside the payload it describes.
"""
from __future__ import annotations

import copy
from typing import Any

from clousight_bench.core.canonical import digest
from clousight_bench.core.redaction import redact

UNKNOWN = "unknown"


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
    return digest(
        {
            "task_id": task_id,
            "task_revision": task_revision,
            "scorer_revision": scorer_revision,
            "workload": workload,
            "workload_version": workload_version,
            "assets": sorted(
                (redact(a) for a in assets),
                key=lambda a: (str(a.get("name", "")), str(a.get("version", ""))),
            ),
            "params": redact(params),
        }
    )


def environment_fingerprint(
    *, region: str, mode: str, facts: dict[str, Any]
) -> str:
    return digest({"region": region, "mode": mode, "facts": redact(facts)})


def implementation_fingerprint(
    *,
    core_version: str,
    domain: str,
    adapter: str,
    adapter_status: str,
    plugin_versions: dict[str, str],
) -> str:
    return digest(
        {
            "core_version": core_version,
            "domain": domain,
            "adapter": adapter,
            "adapter_status": adapter_status,
            "plugin_versions": dict(sorted(plugin_versions.items())),
        }
    )


def record_digest(payload: dict[str, Any]) -> str:
    """Digest of the persisted payload, excluding the digest field itself."""
    body = copy.deepcopy(payload)
    fingerprints = body.get("fingerprints")
    if isinstance(fingerprints, dict):
        fingerprints.pop("record_digest", None)
    return digest(body)
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `uv run pytest tests/test_fingerprints.py -v`

Expected: 9 passed.

- [ ] **Step 5: Run ruff and the full suite**

Run:

```bash
uv run ruff check src tests
uv run pytest -q
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/clousight_bench/core/fingerprints.py tests/test_fingerprints.py
git commit -s -m "feat: add benchmark, environment and implementation fingerprints"
```

---

### Task 6: The `execute`/`score` Task Contract with a Temporary Bridge

**Files:**
- Modify: `src/clousight_bench/core/plugin.py:111-129` (the `Task` class)
- Test: `tests/test_task_contract.py`

**Interfaces:**
- Produces on `Task`: class attributes `task_revision: str = "0"` and `scorer_revision: str = "0"`.
- Produces on `Task`: `execute(self, adapter: ProviderAdapter, params: dict[str, Any]) -> ObservationBundle` and `score(self, observations: ObservationBundle) -> TaskResult`, both raising `NotImplementedError` by default so a Task that implements neither still fails loudly.
- Produces on `Task`: `environment_facts(self, adapter: ProviderAdapter, params: dict[str, Any]) -> dict[str, Any]`, default `{}` — the non-sensitive environment facts this benchmark depends on, folded into `environment_fingerprint`.
- Produces on `Task`: `workload_identity(self, params: dict[str, Any]) -> dict[str, Any]` returning exactly the keys `{"workload": str, "workload_version": str, "assets": list[dict[str, str]]}`, default `{"workload": "", "workload_version": "", "assets": []}`.
- Changes on `Task`: `run()` stops being abstract and becomes a **temporary bridge** that composes `execute` → `collect` → `score` into the legacy `TaskOutput`, so the still-`1.0` orchestrator drives migrated Tasks unchanged.
- **The bridge and `TaskOutput` are deleted in Task 12.** Nothing outside this plan may depend on them.
- Consumed by: Tasks 7, 8, 9, 12.

- [ ] **Step 1: Write the failing test**

Create `tests/test_task_contract.py`:

```python
"""Task.execute/score is the contract; run() is a bridge deleted at cutover."""
import pytest

from clousight_bench.core.observation import (
    Finding,
    Measurement,
    ObservationBundle,
    TaskResult,
)
from clousight_bench.core.plugin import ProviderAdapter, Task


class _Adapter(ProviderAdapter):
    name = "fake"


class _Good(Task):
    task_id = "TX"
    evidence_layer = "C"
    task_revision = "3"
    scorer_revision = "4"

    def config(self, params):
        return {"task_id": self.task_id}

    def execute(self, adapter, params):
        return ObservationBundle(
            observations={"hits": 2},
            series={"latency_ms": [[1, 10.0]]},
            artifacts=[{"kind": "trace", "path": "t", "media": "m",
                        "sha256": "sha256:a"}],
        )

    def score(self, observations):
        hits = observations.observations["hits"]
        return TaskResult(
            measurements={"hits": Measurement(value=hits, unit="count", evidence="C")},
            findings=[] if hits else [Finding(code="tx.no_hits", severity="critical",
                                              summary="nothing observed", evidence="C")],
            notes=f"hits={hits}",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )


class _Unimplemented(Task):
    task_id = "TY"

    def config(self, params):
        return {}


def test_default_revisions_are_zero():
    assert Task.task_revision == "0"
    assert Task.scorer_revision == "0"


def test_default_environment_facts_and_workload_identity_are_empty():
    task = _Good()
    assert task.environment_facts(_Adapter(), {}) == {}
    assert task.workload_identity({}) == {
        "workload": "", "workload_version": "", "assets": [],
    }


def test_execute_and_score_are_required_of_a_concrete_task():
    task = _Unimplemented()
    with pytest.raises(NotImplementedError, match="execute"):
        task.execute(_Adapter(), {})
    with pytest.raises(NotImplementedError, match="score"):
        task.score(ObservationBundle())


def test_bridge_run_composes_execute_collect_and_score():
    out = _Good().run(_Adapter(), {})
    assert out.metrics == {"hits": 2}
    assert out.evidence_layer == "C"
    assert out.ok is True
    assert out.raw == {"hits": 2}
    assert out.series == {"latency_ms": [[1, 10.0]]}
    assert out.artifacts[0]["kind"] == "trace"
    assert out.notes == "hits=2"


def test_bridge_run_reports_not_ok_on_a_critical_finding(monkeypatch):
    task = _Good()
    monkeypatch.setattr(
        task, "execute", lambda adapter, params: ObservationBundle(observations={"hits": 0})
    )
    assert task.run(_Adapter(), {}).ok is False
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_task_contract.py -v`

Expected: FAIL — `Task` has no `task_revision`, no `environment_facts`, no
`workload_identity`, and `_Unimplemented` cannot be instantiated because `run`
is still abstract.

- [ ] **Step 3: Replace the `Task` class in `plugin.py`**

In `src/clousight_bench/core/plugin.py`, add the import next to the other core
imports:

```python
from clousight_bench.core.observation import ObservationBundle, TaskResult, collect
```

and replace the whole `class Task(ABC):` block with:

```python
class Task(ABC):
    """One benchmark dimension, split into observation and scoring.

    ``execute`` may talk to the cloud; it returns only raw, replayable evidence.
    ``score`` is a pure function of that evidence: it must not read credentials,
    create resources or mutate the bundle it is given, which is exactly what
    makes a stored observation re-scorable after a scorer fix.
    """

    task_id: str = "abstract"
    title: str = ""
    evidence_layer: str = "C"
    # Bumped whenever the observation procedure or the scoring rules change, so
    # a published number stays attributable to the code that produced it.
    task_revision: str = "0"
    scorer_revision: str = "0"
    # Abstract capability tokens this benchmark exercises (cloud-independent).
    # The adapter maps these to each cloud's concrete minimal permissions and
    # verifies them at preflight. Empty = no special permissions declared.
    required_permissions: tuple[str, ...] = ()

    @abstractmethod
    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        """The controlled inputs that determine the result -> benchmark fingerprint."""

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        """Drive the system under test and return raw observations only."""
        raise NotImplementedError(f"{type(self).__name__} must implement execute()")

    def score(self, observations: ObservationBundle) -> TaskResult:
        """Turn observations into measurements and findings. Pure function."""
        raise NotImplementedError(f"{type(self).__name__} must implement score()")

    def environment_facts(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Non-sensitive environment facts this benchmark depends on.

        Folded into the environment fingerprint. Never return a credential,
        hostname, username or raw environment variable."""
        return {}

    def workload_identity(self, params: dict[str, Any]) -> dict[str, Any]:
        """Workload and asset identity folded into the benchmark fingerprint.

        Tasks that drive a WorkloadEngine override this; the default declares no
        workload. Keys are exactly ``workload``, ``workload_version`` and
        ``assets``."""
        return {"workload": "", "workload_version": "", "assets": []}

    def run(self, adapter: ProviderAdapter, params: dict[str, Any]) -> TaskOutput:
        """TEMPORARY Phase 1B bridge — deleted with TaskOutput at the cutover.

        Lets the still-1.0 orchestrator drive a migrated execute/score Task, so
        the built-in tasks can migrate one group at a time with the suite green
        the whole way."""
        bundle = collect(self.execute(adapter, params))
        result = self.score(bundle)
        return TaskOutput(
            metrics={name: m.value for name, m in result.measurements.items()},
            evidence_layer=self.evidence_layer,
            ok=not any(f.severity == "critical" for f in result.findings),
            raw=dict(bundle.observations),
            notes=result.notes,
            series=dict(bundle.series),
            artifacts=list(bundle.artifacts),
        )
```

`run()` keeps its old signature and semantics for tasks that still override it,
so nothing in the current suite changes behaviour.

- [ ] **Step 4: Run the test and verify it passes**

Run: `uv run pytest tests/test_task_contract.py -v`

Expected: 5 passed.

- [ ] **Step 5: Run ruff and the full suite**

Run:

```bash
uv run ruff check src tests
uv run pytest -q
```

Expected: all green — every built-in Task still overrides `run()`, so their
behaviour is untouched.

- [ ] **Step 6: Commit**

```bash
git add src/clousight_bench/core/plugin.py tests/test_task_contract.py
git commit -s -m "feat: add the execute/score task contract with a temporary bridge"
```

---

### Task 7: Migrate T1.2 and T1.3 to `execute`/`score`

**Files:**
- Modify: `src/clousight_bench/domains/agent_runtime/tasks/t1_2_state_persistence.py`
- Modify: `src/clousight_bench/domains/agent_runtime/tasks/t1_3_fault_recovery.py`
- Create: `tests/test_task_contract_agent_runtime.py`

**Interfaces:**
- Consumes: `Task.execute`/`score`/`environment_facts`, `ObservationBundle`, `Measurement`, `Finding`, `TaskResult`, `collect` from Tasks 3 and 6.
- Produces: `StatePersistenceTask` with `task_revision = "2"`, `scorer_revision = "2"`, measurement keys `state_capability`, `state_persisted`, `persistence_mode`, and finding codes `agent_runtime.state_api_absent` / `agent_runtime.state_ephemeral`.
- Produces: `FaultRecoveryTask` with `task_revision = "2"`, `scorer_revision = "2"`, measurement keys `recovery_mode`, `final_state`, `budgeted_success`, `time_to_recovery_ms`, `total_attempts`, `fault_hits`, `retried`, and finding codes `agent_runtime.fault_not_observed` / `agent_runtime.recovery_fail_fast`.
- Measurement keys are byte-identical to the old metric keys, so the Task 6 bridge keeps `tests/test_agent_runtime_local.py` and `tests/test_agent_runtime_dimensions.py` green with no edits.

- [ ] **Step 1: Write the failing test**

Create `tests/test_task_contract_agent_runtime.py`:

```python
"""Agent-runtime tasks: execute observes, score concludes, score stays pure."""
from clousight_bench.core.observation import ObservationBundle, collect
from clousight_bench.domains.agent_runtime.adapters.local_sim import LocalSimAdapter
from clousight_bench.domains.agent_runtime.tasks.t1_2_state_persistence import (
    StatePersistenceTask,
)
from clousight_bench.domains.agent_runtime.tasks.t1_3_fault_recovery import (
    FaultRecoveryTask,
)


def _run(task, adapter):
    adapter.setup()
    try:
        return collect(task.execute(adapter, {}))
    finally:
        adapter.teardown()


def test_t1_2_execute_returns_raw_observations_without_a_verdict():
    bundle = _run(StatePersistenceTask(), LocalSimAdapter({"state_persistence": "durable"}))
    assert isinstance(bundle, ObservationBundle)
    assert bundle.observations["capability"] == "supported"
    assert bundle.observations["recovered"] == bundle.observations["probe"]
    assert "state_persisted" not in bundle.observations
    assert "persistence_mode" not in bundle.observations


def test_t1_2_score_is_pure_and_repeatable():
    task = StatePersistenceTask()
    bundle = _run(task, LocalSimAdapter({"state_persistence": "ephemeral"}))
    before = bundle.to_dict()
    first = task.score(bundle)
    second = task.score(bundle)
    assert bundle.to_dict() == before
    assert first.measurements["persistence_mode"].value == "ephemeral"
    assert second.measurements["persistence_mode"].value == "ephemeral"
    assert [f.code for f in first.findings] == ["agent_runtime.state_ephemeral"]
    assert first.task_revision == "2" and first.scorer_revision == "2"


def test_t1_2_unsupported_capability_is_a_finding_not_a_crash():
    class _NoState(LocalSimAdapter):
        def persist_state(self, session_id, state):
            from clousight_bench.domains.agent_runtime.adapters.base import (
                CapabilityNotSupported,
            )

            raise CapabilityNotSupported("persist_state")

    task = StatePersistenceTask()
    result = task.score(_run(task, _NoState()))
    assert result.unsupported is True
    assert result.measurements["state_capability"].value == "unsupported"
    assert [f.code for f in result.findings] == ["agent_runtime.state_api_absent"]


def test_t1_3_execute_records_every_attempt():
    task = FaultRecoveryTask()
    bundle = _run(task, LocalSimAdapter({"recovery": {"mode": "auto-retry"}}))
    assert bundle.observations["plan_calls"] == 5
    assert len(bundle.observations["attempts"]) >= 5
    assert set(bundle.observations["attempts"][0]) == {
        "call_index", "attempt", "status", "ok", "latency_ms",
    }
    assert "recovery_mode" not in bundle.observations


def test_t1_3_score_classifies_auto_retry():
    task = FaultRecoveryTask()
    result = task.score(_run(task, LocalSimAdapter({"recovery": {"mode": "auto-retry"}})))
    assert result.measurements["recovery_mode"].value == "auto-retry"
    assert result.measurements["budgeted_success"].value is True
    assert result.measurements["time_to_recovery_ms"].unit == "ms"
    assert result.findings == []


def test_t1_3_score_classifies_fail_fast_as_a_warning_finding():
    task = FaultRecoveryTask()
    result = task.score(_run(task, LocalSimAdapter({"recovery": {"mode": "fail-fast"}})))
    assert result.measurements["recovery_mode"].value == "fail-fast"
    assert result.measurements["final_state"].value == "aborted"
    assert [(f.code, f.severity) for f in result.findings] == [
        ("agent_runtime.recovery_fail_fast", "warning")
    ]


def test_t1_3_score_flags_a_missing_fault_as_critical():
    task = FaultRecoveryTask()
    bundle = ObservationBundle(observations={
        "fault": {}, "plan_calls": 5, "completed": True, "final_state": "completed",
        "attempts": [{"call_index": 1, "attempt": 1, "status": 200, "ok": True,
                      "latency_ms": 1.0}],
    })
    result = task.score(bundle)
    assert result.measurements["recovery_mode"].value == "no-fault-observed"
    assert [(f.code, f.severity) for f in result.findings] == [
        ("agent_runtime.fault_not_observed", "critical")
    ]


def test_environment_facts_are_declared_and_non_sensitive():
    adapter = LocalSimAdapter({"recovery": {"mode": "auto-retry"}})
    assert StatePersistenceTask().environment_facts(adapter, {}) == {
        "state_persistence_policy": "durable"
    }
    assert FaultRecoveryTask().environment_facts(adapter, {}) == {
        "recovery_policy": "auto-retry", "max_retries": 3
    }
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_task_contract_agent_runtime.py -v`

Expected: FAIL with `NotImplementedError: StatePersistenceTask must implement execute()`.

- [ ] **Step 3: Rewrite T1.2**

Replace the body of `src/clousight_bench/domains/agent_runtime/tasks/t1_2_state_persistence.py`
below the module docstring with:

```python
from __future__ import annotations

from typing import Any

from clousight_bench.core.observation import (
    Finding,
    Measurement,
    ObservationBundle,
    TaskResult,
)
from clousight_bench.core.plugin import ProviderAdapter, Task
from clousight_bench.domains.agent_runtime import permissions as perm
from clousight_bench.domains.agent_runtime.adapters.base import (
    AgentRuntimeAdapter,
    CapabilityNotSupported,
)

_PROBE_STATE = {"cursor": 42, "scratch": "benchmark-marker"}


class StatePersistenceTask(Task):
    task_id = "T1.2"
    title = "Session state persistence"
    evidence_layer = "C"
    task_revision = "2"
    scorer_revision = "2"
    required_permissions = (perm.SESSION_CREATE, perm.SESSION_STATE)

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id, "probe_state": _PROBE_STATE}

    def environment_facts(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> dict[str, Any]:
        return {"state_persistence_policy": str(adapter.target.get("state_persistence", "durable"))}

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T1.2 needs an AgentRuntimeAdapter")
        session = adapter.create_session()
        try:
            try:
                adapter.persist_state(session, _PROBE_STATE)
                resumed = adapter.resume_session(session)
                recovered = adapter.load_state(resumed)
            except CapabilityNotSupported as exc:
                return ObservationBundle(
                    observations={
                        "capability": "unsupported",
                        "probe": dict(_PROBE_STATE),
                        "reason": str(exc),
                    }
                )
        finally:
            adapter.destroy_session(session)
        return ObservationBundle(
            observations={
                "capability": "supported",
                "probe": dict(_PROBE_STATE),
                "recovered": recovered,
            }
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={
                    "state_capability": Measurement(
                        value="unsupported", unit="", evidence="C"
                    ),
                    "state_persisted": Measurement(value=False, unit="", evidence="C"),
                },
                findings=[
                    Finding(
                        code="agent_runtime.state_api_absent",
                        severity="info",
                        summary="runtime exposes no session state persistence API",
                        evidence="C",
                        details={"reason": str(raw.get("reason", ""))},
                    )
                ],
                notes="runtime exposes no state persistence",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        persisted = raw.get("recovered") == raw.get("probe")
        mode = "durable" if persisted else "ephemeral"
        findings = (
            []
            if persisted
            else [
                Finding(
                    code="agent_runtime.state_ephemeral",
                    severity="warning",
                    summary="session state did not survive an interruption and resume",
                    evidence="C",
                    details={"probe": raw.get("probe", {}), "recovered": raw.get("recovered")},
                )
            ]
        )
        return TaskResult(
            measurements={
                "state_capability": Measurement(value="supported", unit="", evidence="C"),
                "state_persisted": Measurement(value=persisted, unit="", evidence="C"),
                "persistence_mode": Measurement(value=mode, unit="", evidence="C"),
            },
            findings=findings,
            notes=f"state after resume -> {mode}",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
```

- [ ] **Step 4: Rewrite T1.3**

In `src/clousight_bench/domains/agent_runtime/tasks/t1_3_fault_recovery.py`,
change the imports to:

```python
from dataclasses import asdict

from clousight_bench.core.observation import (
    Finding,
    Measurement,
    ObservationBundle,
    TaskResult,
)
from clousight_bench.core.plugin import ProviderAdapter, Task
```

(delete the `TaskOutput` import), keep `PLAN`, `FAULT` and `_post` unchanged, and
replace the class body below `required_permissions` with:

```python
    task_revision = "2"
    scorer_revision = "2"

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "plan": [{"target": c.target, "method": c.method, "params": c.params} for c in PLAN],
            "fault": FAULT,
        }

    def environment_facts(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> dict[str, Any]:
        recovery = adapter.target.get("recovery", {})
        return {
            "recovery_policy": str(recovery.get("mode", "auto-retry")),
            "max_retries": int(recovery.get("max_retries", 3)),
        }

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T1.3 needs an AgentRuntimeAdapter")
        mock = adapter.mock_base_url.rstrip("/")

        # 1. reset + arm the deterministic fault
        _post(mock, "/reset", {})
        _post(mock, "/fault/config", FAULT)

        # 2. run the plan under the runtime's own recovery semantics
        session = adapter.create_session()
        try:
            trace = adapter.run_tool_plan(session, PLAN)
        finally:
            adapter.destroy_session(session)

        return ObservationBundle(
            observations={
                "fault": dict(FAULT),
                "plan_calls": len(PLAN),
                "completed": trace.completed,
                "final_state": trace.final_state,
                "attempts": [asdict(a) for a in trace.attempts],
            }
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        attempts = list(raw.get("attempts", []))
        failures = [a for a in attempts if not a["ok"]]
        retried = any(a["attempt"] > 1 for a in attempts)
        completed = bool(raw.get("completed"))
        final_state = str(raw.get("final_state", ""))

        if not failures:
            recovery_mode = "no-fault-observed"  # fault never triggered -> invalid run
        elif completed and retried:
            recovery_mode = "auto-retry"
        elif not completed and final_state == "aborted":
            recovery_mode = "fail-fast"
        else:
            recovery_mode = "manual-resume"

        # Latency spent on failed attempts before the run either recovered or gave up.
        ttr_ms = round(sum(a["latency_ms"] for a in failures), 2)

        findings: list[Finding] = []
        if recovery_mode == "no-fault-observed":
            findings.append(
                Finding(
                    code="agent_runtime.fault_not_observed",
                    severity="critical",
                    summary="the injected fault never fired, so this run measures nothing",
                    evidence="C",
                    details={"fault": raw.get("fault", {}), "attempts": len(attempts)},
                )
            )
        elif recovery_mode == "fail-fast":
            findings.append(
                Finding(
                    code="agent_runtime.recovery_fail_fast",
                    severity="warning",
                    summary="runtime aborted on the first tool fault instead of retrying",
                    evidence="C",
                    details={"final_state": final_state},
                )
            )

        return TaskResult(
            measurements={
                "recovery_mode": Measurement(value=recovery_mode, unit="", evidence="C"),
                "final_state": Measurement(value=final_state, unit="", evidence="C"),
                "budgeted_success": Measurement(value=completed, unit="", evidence="C"),
                "time_to_recovery_ms": Measurement(
                    value=ttr_ms, unit="ms", evidence="C",
                    aggregation="sum", sample_count=len(failures),
                ),
                "total_attempts": Measurement(value=len(attempts), unit="count", evidence="C"),
                "fault_hits": Measurement(value=len(failures), unit="count", evidence="C"),
                "retried": Measurement(value=retried, unit="", evidence="C"),
            },
            findings=findings,
            notes=f"fault on call #{FAULT['fail_on_calls']}; runtime recovery_mode={recovery_mode}",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
```

The old `recovered = trace.completed and bool(failures)` guard collapses into
`completed and retried` because the `not failures` branch above already returns
`no-fault-observed`, so `failures` is non-empty from that point on.

- [ ] **Step 5: Run the new and the existing tests**

Run:

```bash
uv run pytest tests/test_task_contract_agent_runtime.py \
  tests/test_agent_runtime_local.py tests/test_agent_runtime_dimensions.py -v
```

Expected: the 8 new tests pass, and the existing T1.2/T1.3 tests pass **without
edits** — the Task 6 bridge maps the identical measurement keys back into
`TaskOutput.metrics`.

- [ ] **Step 6: Run ruff and the full suite**

Run:

```bash
uv run ruff check src tests
uv run pytest -q
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/clousight_bench/domains/agent_runtime/tasks/t1_2_state_persistence.py \
  src/clousight_bench/domains/agent_runtime/tasks/t1_3_fault_recovery.py \
  tests/test_task_contract_agent_runtime.py
git commit -s -m "refactor: split T1.2 and T1.3 into execute and score"
```

---

### Task 8: Migrate T2.1, T4.1 and T4.2 to `execute`/`score`

**Files:**
- Modify: `src/clousight_bench/domains/agent_runtime/tasks/t2_1_tool_registration.py`
- Modify: `src/clousight_bench/domains/agent_runtime/tasks/t4_1_trace_completeness.py`
- Modify: `src/clousight_bench/domains/agent_runtime/tasks/t4_2_otel_export.py`
- Modify: `tests/test_task_contract_agent_runtime.py` (append)

**Interfaces:**
- Produces: `ToolRegistrationTask` with `task_revision = "2"`, `scorer_revision = "2"`, measurement keys `supported_paths`, `supported_count`, `mcp`, `openapi`, `native`, finding code `agent_runtime.no_tool_registration_path`.
- Produces: `TraceCompletenessTask` with `task_revision = "2"`, `scorer_revision = "2"`, measurement keys `trace_capability`, `span_completeness`, `spans_present`, `spans_expected`, `kinds_present`, `kinds_missing`, finding codes `agent_runtime.trace_api_absent` / `agent_runtime.trace_spans_missing`.
- Produces: `OtelExportTask` with `task_revision = "2"`, `scorer_revision = "2"`, measurement keys `otel_export_supported`, `otel_valid`, `span_count`, `problems`, finding codes `agent_runtime.otel_export_absent` / `agent_runtime.otel_payload_invalid`.
- Measurement keys stay byte-identical to the old metric keys.

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_task_contract_agent_runtime.py`:

```python
def test_t2_1_execute_records_each_registration_path_attempt():
    from clousight_bench.domains.agent_runtime.tasks.t2_1_tool_registration import (
        ToolRegistrationTask,
    )

    task = ToolRegistrationTask()
    bundle = _run(task, LocalSimAdapter({"tool_registration": ["mcp"]}))
    assert bundle.observations["support"] == {"mcp": True, "openapi": False, "native": False}
    assert "supported_count" not in bundle.observations


def test_t2_1_score_counts_paths_and_flags_a_runtime_with_none():
    from clousight_bench.domains.agent_runtime.tasks.t2_1_tool_registration import (
        ToolRegistrationTask,
    )

    task = ToolRegistrationTask()
    result = task.score(_run(task, LocalSimAdapter({"tool_registration": ["mcp"]})))
    assert result.measurements["supported_paths"].value == ["mcp"]
    assert result.measurements["supported_count"].value == 1
    assert result.measurements["openapi"].value is False
    assert result.findings == []
    assert result.unsupported is False

    none_result = task.score(_run(task, LocalSimAdapter({"tool_registration": []})))
    assert none_result.unsupported is True
    assert [f.code for f in none_result.findings] == [
        "agent_runtime.no_tool_registration_path"
    ]


def test_t4_1_score_flags_missing_span_kinds():
    from clousight_bench.domains.agent_runtime.tasks.t4_1_trace_completeness import (
        TraceCompletenessTask,
    )

    task = TraceCompletenessTask()
    full = task.score(_run(task, LocalSimAdapter()))
    assert full.measurements["span_completeness"].value == 1.0
    assert full.measurements["kinds_missing"].value == []
    assert full.findings == []

    partial = task.score(
        _run(task, LocalSimAdapter({"trace": {"completeness": "partial"}}))
    )
    assert partial.measurements["span_completeness"].value < 1.0
    assert "TOOL" in partial.measurements["kinds_missing"].value
    assert [(f.code, f.severity) for f in partial.findings] == [
        ("agent_runtime.trace_spans_missing", "warning")
    ]


def test_t4_1_absent_trace_api_is_unsupported():
    from clousight_bench.domains.agent_runtime.adapters.base import CapabilityNotSupported
    from clousight_bench.domains.agent_runtime.tasks.t4_1_trace_completeness import (
        TraceCompletenessTask,
    )

    class _NoTrace(LocalSimAdapter):
        def get_trace(self, session_id):
            raise CapabilityNotSupported("get_trace")

    task = TraceCompletenessTask()
    result = task.score(_run(task, _NoTrace()))
    assert result.unsupported is True
    assert result.measurements["trace_capability"].value == "unsupported"
    assert result.measurements["span_completeness"].value == 0.0
    assert [f.code for f in result.findings] == ["agent_runtime.trace_api_absent"]


def test_t4_2_score_validates_the_otel_payload():
    from clousight_bench.domains.agent_runtime.tasks.t4_2_otel_export import OtelExportTask

    task = OtelExportTask()
    ok = task.score(_run(task, LocalSimAdapter()))
    assert ok.measurements["otel_export_supported"].value is True
    assert ok.measurements["otel_valid"].value is True
    assert ok.measurements["span_count"].value >= 1
    assert ok.findings == []

    absent = task.score(_run(task, LocalSimAdapter({"trace": {"otel_export": False}})))
    assert absent.unsupported is True
    assert absent.measurements["otel_export_supported"].value is False
    assert absent.measurements["otel_valid"].value is False
    assert [f.code for f in absent.findings] == ["agent_runtime.otel_export_absent"]
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run: `uv run pytest tests/test_task_contract_agent_runtime.py -v -k "t2_1 or t4_1 or t4_2"`

Expected: FAIL with `NotImplementedError: ToolRegistrationTask must implement execute()`.

- [ ] **Step 3: Rewrite T2.1**

In `src/clousight_bench/domains/agent_runtime/tasks/t2_1_tool_registration.py`,
replace the `TaskOutput` import with:

```python
from clousight_bench.core.observation import (
    Finding,
    Measurement,
    ObservationBundle,
    TaskResult,
)
```

and replace the class body below `required_permissions` with:

```python
    task_revision = "2"
    scorer_revision = "2"

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id, "paths": list(_PATHS), "tool_spec": _TOOL_SPEC}

    def environment_facts(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> dict[str, Any]:
        return {"probed_paths": list(_PATHS)}

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T2.1 needs an AgentRuntimeAdapter")
        session = adapter.create_session()
        support: dict[str, bool] = {}
        try:
            for path in _PATHS:
                try:
                    support[path] = bool(adapter.register_tool(path, _TOOL_SPEC))
                except CapabilityNotSupported:
                    support[path] = False
        finally:
            adapter.destroy_session(session)
        return ObservationBundle(
            observations={"support": support, "tool_spec": dict(_TOOL_SPEC)}
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        support = dict(observations.observations.get("support", {}))
        supported = sorted(path for path, ok in support.items() if ok)
        findings: list[Finding] = []
        if not supported:
            findings.append(
                Finding(
                    code="agent_runtime.no_tool_registration_path",
                    severity="warning",
                    summary="runtime accepts none of the MCP, OpenAPI or native paths",
                    evidence="B",
                    details={"support": support},
                )
            )
        return TaskResult(
            measurements={
                "supported_paths": Measurement(value=supported, unit="", evidence="B"),
                "supported_count": Measurement(
                    value=len(supported), unit="count", evidence="B"
                ),
                "mcp": Measurement(value=bool(support.get("mcp")), unit="", evidence="B"),
                "openapi": Measurement(
                    value=bool(support.get("openapi")), unit="", evidence="B"
                ),
                "native": Measurement(
                    value=bool(support.get("native")), unit="", evidence="B"
                ),
            },
            findings=findings,
            notes=f"registration paths supported: {', '.join(supported) or 'none'}",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
            unsupported=not supported,
        )
```

Add `ProviderAdapter` to the existing `clousight_bench.core.plugin` import if it
is not already there.

- [ ] **Step 4: Rewrite T4.1**

In `src/clousight_bench/domains/agent_runtime/tasks/t4_1_trace_completeness.py`,
replace the `TaskOutput` import with the `observation` imports used above, and
replace the class body below `required_permissions` with:

```python
    task_revision = "2"
    scorer_revision = "2"

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "plan": [{"target": c.target, "params": c.params} for c in PLAN],
            "expected_kinds": list(openinference.SPAN_KINDS),
        }

    def environment_facts(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> dict[str, Any]:
        trace = adapter.target.get("trace", {})
        return {"trace_completeness_policy": str(trace.get("completeness", "full"))}

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T4.1 needs an AgentRuntimeAdapter")
        _post(adapter.mock_base_url.rstrip("/"), "/reset", {})
        session = adapter.create_session()
        try:
            adapter.run_tool_plan(session, PLAN)
            try:
                spans = adapter.get_trace(session)
            except CapabilityNotSupported as exc:
                return ObservationBundle(
                    observations={
                        "capability": "unsupported",
                        "tool_calls": len(PLAN),
                        "reason": str(exc),
                    }
                )
        finally:
            adapter.destroy_session(session)
        return ObservationBundle(
            observations={
                "capability": "supported",
                "tool_calls": len(PLAN),
                "spans": spans,
            }
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={
                    "trace_capability": Measurement(
                        value="unsupported", unit="", evidence="C"
                    ),
                    "span_completeness": Measurement(
                        value=0.0, unit="ratio", evidence="C"
                    ),
                },
                findings=[
                    Finding(
                        code="agent_runtime.trace_api_absent",
                        severity="info",
                        summary="runtime exposes no trace API",
                        evidence="C",
                        details={"reason": str(raw.get("reason", ""))},
                    )
                ],
                notes="runtime exposes no trace API",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        spans = list(raw.get("spans", []))
        tool_calls = int(raw.get("tool_calls", 0))
        completeness = openinference.span_completeness(spans, tool_calls)
        present = openinference.kinds_present(spans)
        missing = sorted(set(openinference.SPAN_KINDS) - present)
        findings: list[Finding] = []
        if completeness < 1.0:
            findings.append(
                Finding(
                    code="agent_runtime.trace_spans_missing",
                    severity="warning",
                    summary="runtime trace is missing spans the OpenInference shape requires",
                    evidence="C",
                    details={"kinds_missing": missing, "completeness": completeness},
                )
            )
        return TaskResult(
            measurements={
                "trace_capability": Measurement(value="supported", unit="", evidence="C"),
                "span_completeness": Measurement(
                    value=completeness, unit="ratio", evidence="C"
                ),
                "spans_present": Measurement(value=len(spans), unit="count", evidence="C"),
                "spans_expected": Measurement(
                    value=openinference.expected_span_count(tool_calls),
                    unit="count", evidence="C",
                ),
                "kinds_present": Measurement(
                    value=sorted(present), unit="", evidence="C"
                ),
                "kinds_missing": Measurement(value=missing, unit="", evidence="C"),
            },
            findings=findings,
            notes=f"span completeness {completeness:.0%}; missing kinds {missing or 'none'}",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
```

- [ ] **Step 5: Rewrite T4.2**

In `src/clousight_bench/domains/agent_runtime/tasks/t4_2_otel_export.py`, replace
the `TaskOutput` import with the `observation` imports and replace the class body
below `required_permissions` with:

```python
    task_revision = "2"
    scorer_revision = "2"

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "plan": [{"target": c.target, "params": c.params} for c in PLAN],
        }

    def environment_facts(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> dict[str, Any]:
        trace = adapter.target.get("trace", {})
        return {"otel_export_policy": bool(trace.get("otel_export", True))}

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T4.2 needs an AgentRuntimeAdapter")
        _post(adapter.mock_base_url.rstrip("/"), "/reset", {})
        session = adapter.create_session()
        try:
            adapter.run_tool_plan(session, PLAN)
            try:
                payload = adapter.export_otel(session)
            except CapabilityNotSupported as exc:
                return ObservationBundle(
                    observations={"capability": "unsupported", "reason": str(exc)}
                )
        finally:
            adapter.destroy_session(session)
        return ObservationBundle(
            observations={"capability": "supported", "otel": payload}
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={
                    "otel_export_supported": Measurement(
                        value=False, unit="", evidence="B"
                    ),
                    "otel_valid": Measurement(value=False, unit="", evidence="B"),
                },
                findings=[
                    Finding(
                        code="agent_runtime.otel_export_absent",
                        severity="info",
                        summary="runtime cannot export OTel",
                        evidence="B",
                        details={"reason": str(raw.get("reason", ""))},
                    )
                ],
                notes="runtime cannot export OTel",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        payload = raw.get("otel", {})
        valid, problems = openinference.validate_otel(payload)
        span_count = sum(
            len(ss.get("spans", []))
            for rs in payload.get("resourceSpans", [])
            for ss in rs.get("scopeSpans", [])
        )
        findings: list[Finding] = []
        if not valid:
            findings.append(
                Finding(
                    code="agent_runtime.otel_payload_invalid",
                    severity="warning",
                    summary="exported OTel payload does not match the minimal OTLP shape",
                    evidence="B",
                    details={"problems": problems},
                )
            )
        return TaskResult(
            measurements={
                "otel_export_supported": Measurement(value=True, unit="", evidence="B"),
                "otel_valid": Measurement(value=valid, unit="", evidence="B"),
                "span_count": Measurement(value=span_count, unit="count", evidence="B"),
                "problems": Measurement(value=problems, unit="", evidence="B"),
            },
            findings=findings,
            notes=f"OTel export valid={valid}; spans={span_count}; problems={problems or 'none'}",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
```

- [ ] **Step 6: Run the new and the existing tests**

Run:

```bash
uv run pytest tests/test_task_contract_agent_runtime.py \
  tests/test_agent_runtime_dimensions.py tests/test_preflight.py -v
```

Expected: everything passes; the existing dimension tests are still unedited
because the bridge preserves both the metric keys and the `ok` semantics.

- [ ] **Step 7: Run ruff and the full suite**

Run:

```bash
uv run ruff check src tests
uv run pytest -q
```

Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add src/clousight_bench/domains/agent_runtime/tasks/t2_1_tool_registration.py \
  src/clousight_bench/domains/agent_runtime/tasks/t4_1_trace_completeness.py \
  src/clousight_bench/domains/agent_runtime/tasks/t4_2_otel_export.py \
  tests/test_task_contract_agent_runtime.py
git commit -s -m "refactor: split T2.1, T4.1 and T4.2 into execute and score"
```

---

### Task 9: Migrate J1.1 and Declare Its Workload Identity

**Files:**
- Modify: `src/clousight_bench/domains/bigdata_emr/tasks/j1_1_wordcount.py`
- Create: `tests/test_task_contract_bigdata.py`

**Interfaces:**
- Produces: `WordcountSmokeTask` with `task_revision = "2"`, `scorer_revision = "2"`, every workload metric promoted to a `Measurement`, plus the `job_succeeded` measurement and the finding code `bigdata.job_failed`.
- Produces: `WordcountSmokeTask.workload_identity(params)` returning the packaged workload's `name`, `version` and asset identities from `WorkloadEngine.describe()`, so the benchmark fingerprint moves when the workload or one of its assets moves.

- [ ] **Step 1: Write the failing test**

Create `tests/test_task_contract_bigdata.py`:

```python
"""J1.1: workload metrics become measurements; a failed job becomes a finding."""
from clousight_bench.core.observation import ObservationBundle, collect
from clousight_bench.domains.bigdata_emr.adapters.local_process import LocalProcessAdapter
from clousight_bench.domains.bigdata_emr.tasks.j1_1_wordcount import WordcountSmokeTask


def test_execute_returns_raw_workload_output():
    task = WordcountSmokeTask()
    bundle = collect(task.execute(LocalProcessAdapter(), {"rows": 100, "seed": 7}))
    assert isinstance(bundle, ObservationBundle)
    assert bundle.observations["workload"] == "wordcount-py"
    assert bundle.observations["job_params"] == {"rows": 100, "seed": 7}
    assert bundle.observations["ok"] is True
    assert bundle.observations["raw_metrics"]["rows_processed"] == 100
    assert "job_succeeded" not in bundle.observations


def test_score_promotes_every_workload_metric_to_a_measurement():
    task = WordcountSmokeTask()
    bundle = collect(task.execute(LocalProcessAdapter(), {"rows": 100, "seed": 7}))
    result = task.score(bundle)
    assert result.measurements["rows_processed"].value == 100
    assert result.measurements["rows_processed"].evidence == "C"
    assert result.measurements["job_succeeded"].value is True
    assert result.findings == []
    assert result.task_revision == "2"


def test_score_reports_a_failed_job_as_a_critical_finding():
    result = WordcountSmokeTask().score(
        ObservationBundle(observations={
            "workload": "wordcount-py", "job_params": {}, "raw_metrics": {},
            "exit_code": 1, "ok": False, "logs": ["boom"],
        })
    )
    assert result.measurements["job_succeeded"].value is False
    assert [(f.code, f.severity) for f in result.findings] == [
        ("bigdata.job_failed", "critical")
    ]
    assert result.findings[0].details["exit_code"] == 1


def test_workload_identity_names_the_packaged_workload():
    identity = WordcountSmokeTask().workload_identity({})
    assert identity["workload"] == "wordcount-py"
    assert identity["workload_version"]
    assert isinstance(identity["assets"], list)


def test_environment_facts_declare_the_workload():
    facts = WordcountSmokeTask().environment_facts(LocalProcessAdapter(), {})
    assert facts == {"workload": "wordcount-py"}
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_task_contract_bigdata.py -v`

Expected: FAIL with `NotImplementedError: WordcountSmokeTask must implement execute()`.

- [ ] **Step 3: Rewrite J1.1**

In `src/clousight_bench/domains/bigdata_emr/tasks/j1_1_wordcount.py`, change the
imports to:

```python
from clousight_bench.core.observation import (
    Finding,
    Measurement,
    ObservationBundle,
    TaskResult,
)
from clousight_bench.core.plugin import ProviderAdapter, Task
from clousight_bench.core.resources import reference_workload_path
from clousight_bench.core.workload import WorkloadEngine
from clousight_bench.domains.bigdata_emr.adapters.base import BigDataClusterAdapter
```

keep `DEFAULT_WORKLOAD` and `_workload_dir` unchanged, and replace `config` and
`run` with:

```python
    task_revision = "2"
    scorer_revision = "2"

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "workload": params.get("workload", DEFAULT_WORKLOAD),
            "rows": params.get("rows", 100_000),
            "seed": params.get("seed", 42),
        }

    def workload_identity(self, params: dict[str, Any]) -> dict[str, Any]:
        described = WorkloadEngine(self._workload_dir(params)).describe()
        return {
            "workload": str(described["workload"]),
            "workload_version": str(described["workload_version"]),
            "assets": list(described["assets"]),
        }

    def environment_facts(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> dict[str, Any]:
        return {"workload": self._workload_dir(params).name}

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        if not isinstance(adapter, BigDataClusterAdapter):
            raise TypeError("J1.1 needs a BigDataClusterAdapter")
        workload_dir = self._workload_dir(params)
        job_params = {"rows": params.get("rows", 100_000), "seed": params.get("seed", 42)}
        result = adapter.submit(str(workload_dir), job_params)
        return ObservationBundle(
            observations={
                "workload": workload_dir.name,
                "job_params": job_params,
                "raw_metrics": dict(result.metrics),
                "exit_code": result.exit_code,
                "ok": result.ok,
                "logs": list(result.logs[-20:]),
            },
            series=dict(result.series),
            artifacts=list(result.artifacts),
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        measurements = {
            name: Measurement(value=value, unit="", evidence="C")
            for name, value in sorted(raw.get("raw_metrics", {}).items())
        }
        succeeded = bool(raw.get("ok"))
        measurements["job_succeeded"] = Measurement(
            value=succeeded, unit="", evidence="C"
        )
        findings: list[Finding] = []
        if not succeeded:
            findings.append(
                Finding(
                    code="bigdata.job_failed",
                    severity="critical",
                    summary="the batch job did not complete successfully",
                    evidence="C",
                    details={
                        "exit_code": raw.get("exit_code"),
                        "logs": raw.get("logs", []),
                    },
                )
            )
        return TaskResult(
            measurements=measurements,
            findings=findings,
            notes=f"wordcount smoke via workload {raw.get('workload', '')}; ok={succeeded}",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
```

- [ ] **Step 4: Run the new and the existing tests**

Run:

```bash
uv run pytest tests/test_task_contract_bigdata.py tests/test_bigdata_workload.py -v
```

Expected: the 5 new tests pass and the existing big-data tests pass unedited.

- [ ] **Step 5: Verify the end-to-end CLI run still works through the bridge**

Run:

```bash
uv run csbench run --domain bigdata-emr --task J1.1 --platform local-process \
  --results /tmp/csbench-phase1b-j11
echo "exit=$?"
```

Expected: `exit=0` and a legacy-shaped record printed — the bridge is still in
place until Task 12.

- [ ] **Step 6: Run ruff and the full suite**

Run:

```bash
uv run ruff check src tests
uv run pytest -q
```

Expected: all green. Every built-in Task now implements `execute`/`score`.

- [ ] **Step 7: Commit**

```bash
git add src/clousight_bench/domains/bigdata_emr/tasks/j1_1_wordcount.py \
  tests/test_task_contract_bigdata.py
git commit -s -m "refactor: split J1.1 into execute and score with workload identity"
```

---

### Task 10: Atomic and Emergency Persistence Primitives

**Files:**
- Create: `src/clousight_bench/core/persistence.py`
- Test: `tests/test_persistence.py`

**Interfaces:**
- Produces: `EMERGENCY_DIR_NAME = "clousight-bench-emergency"`.
- Produces: `atomic_write_text(path: Path, text: str) -> Path` — creates parent directories, writes to a sibling temp file, `flush()` + `os.fsync()`, then `os.replace()`. Removes the temp file on any failure. Returns the resolved final path.
- Produces: `emergency_write_text(name: str, text: str) -> Path` — writes into `tempfile.gettempdir()/clousight-bench-emergency/<name>` and returns the resolved absolute path.
- Consumed by: Task 12 (`ResultStore`) and Task 13 (publish receipts).

- [ ] **Step 1: Write the failing test**

Create `tests/test_persistence.py`:

```python
"""A half-written result file must never exist, and a failed write must land somewhere."""
import os
from pathlib import Path

import pytest

from clousight_bench.core.persistence import (
    EMERGENCY_DIR_NAME,
    atomic_write_text,
    emergency_write_text,
)


def test_atomic_write_creates_parents_and_writes_the_content(tmp_path):
    target = tmp_path / "a" / "b" / "record.json"
    written = atomic_write_text(target, '{"x":1}\n')
    assert written == target.resolve()
    assert target.read_text(encoding="utf-8") == '{"x":1}\n'


def test_atomic_write_replaces_an_existing_file(tmp_path):
    target = tmp_path / "record.json"
    atomic_write_text(target, "old")
    atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"


def test_atomic_write_leaves_no_temp_file_behind(tmp_path):
    target = tmp_path / "record.json"
    atomic_write_text(target, "content")
    assert [p.name for p in tmp_path.iterdir()] == ["record.json"]


def test_atomic_write_cleans_up_and_reraises_when_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "record.json"

    def _boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError, match="disk full"):
        atomic_write_text(target, "content")
    assert list(tmp_path.iterdir()) == []


def test_emergency_write_returns_an_absolute_path_under_the_temp_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    path = emergency_write_text("agent-runtime-T1.3-run-1.json", '{"x":1}')
    assert path.is_absolute()
    assert path.parent.name == EMERGENCY_DIR_NAME
    assert path.parent.parent == Path(tmp_path).resolve()
    assert path.read_text(encoding="utf-8") == '{"x":1}'
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_persistence.py -v`

Expected: collection FAIL with `ModuleNotFoundError: No module named 'clousight_bench.core.persistence'`.

- [ ] **Step 3: Write the implementation**

Create `src/clousight_bench/core/persistence.py`:

```python
"""Write a result so a reader never sees half of one.

A benchmark record is worthless if a crash can leave it truncated, and it is
worse than worthless if a full disk silently discards it. ``atomic_write_text``
gives readers all-or-nothing visibility; ``emergency_write_text`` is the last
resort when the results directory itself cannot be written.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

EMERGENCY_DIR_NAME = "clousight-bench-emergency"


def atomic_write_text(path: Path, text: str) -> Path:
    """Write ``text`` to ``path`` so readers see either the old or the new file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return path.resolve()


def emergency_write_text(name: str, text: str) -> Path:
    """Write ``text`` into the system temp directory and return its absolute path."""
    directory = Path(tempfile.gettempdir()).resolve() / EMERGENCY_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path.resolve()
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `uv run pytest tests/test_persistence.py -v`

Expected: 5 passed.

- [ ] **Step 5: Run ruff and the full suite**

Run:

```bash
uv run ruff check src tests
uv run pytest -q
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/clousight_bench/core/persistence.py tests/test_persistence.py
git commit -s -m "feat: add atomic and emergency write primitives"
```

---

### Task 11: The VALIDATE Stage

**Files:**
- Create: `src/clousight_bench/core/validation.py`
- Test: `tests/test_validation.py`

**Interfaces:**
- Produces: `InvalidRunSpecError(UserInputError)`.
- Produces: `validate_run_spec(spec: RunSpec, task: Task) -> None` — raises `InvalidRunSpecError` when `domain`/`task_id`/`platform` are empty or non-string, when `target` or `params` is not a mapping, when either fails canonical encoding after redaction, or when `task.config(spec.params)` raises or fails canonical encoding.
- A `UserInputError` from RESOLVE or VALIDATE escapes the orchestrator, so the CLI returns exit code 2 and no benchmark record is written.
- Consumed by: Task 12.

- [ ] **Step 1: Write the failing test**

Create `tests/test_validation.py`:

```python
"""VALIDATE rejects a malformed request before any resource is touched."""
import pytest

from clousight_bench.core.errors import UserInputError
from clousight_bench.core.schema import RunSpec
from clousight_bench.core.validation import InvalidRunSpecError, validate_run_spec
from clousight_bench.domains.agent_runtime.tasks.t1_3_fault_recovery import (
    FaultRecoveryTask,
)


def test_a_well_formed_spec_validates():
    validate_run_spec(RunSpec("agent-runtime", "T1.3", "local-sim"), FaultRecoveryTask())


def test_invalid_run_spec_error_is_a_user_input_error():
    assert issubclass(InvalidRunSpecError, UserInputError)


@pytest.mark.parametrize("field", ["domain", "task_id", "platform"])
def test_empty_identifiers_are_rejected(field):
    spec = RunSpec("agent-runtime", "T1.3", "local-sim")
    setattr(spec, field, "")
    with pytest.raises(InvalidRunSpecError, match=field):
        validate_run_spec(spec, FaultRecoveryTask())


def test_non_mapping_target_and_params_are_rejected():
    spec = RunSpec("agent-runtime", "T1.3", "local-sim")
    spec.target = ["not", "a", "mapping"]
    with pytest.raises(InvalidRunSpecError, match="target"):
        validate_run_spec(spec, FaultRecoveryTask())

    spec = RunSpec("agent-runtime", "T1.3", "local-sim")
    spec.params = "nope"
    with pytest.raises(InvalidRunSpecError, match="params"):
        validate_run_spec(spec, FaultRecoveryTask())


def test_non_finite_numbers_are_rejected_before_the_run():
    spec = RunSpec("agent-runtime", "T1.3", "local-sim", params={"budget": float("inf")})
    with pytest.raises(InvalidRunSpecError, match="params"):
        validate_run_spec(spec, FaultRecoveryTask())


def test_a_task_that_cannot_describe_its_config_is_a_user_error():
    class _BadConfig(FaultRecoveryTask):
        def config(self, params):
            raise KeyError("missing-required-param")

    with pytest.raises(InvalidRunSpecError, match="config"):
        validate_run_spec(RunSpec("agent-runtime", "T1.3", "local-sim"), _BadConfig())
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_validation.py -v`

Expected: collection FAIL with `ModuleNotFoundError: No module named 'clousight_bench.core.validation'`.

- [ ] **Step 3: Write the implementation**

Create `src/clousight_bench/core/validation.py`:

```python
"""VALIDATE: parse the request at the boundary, before anything is provisioned.

Anything raised here is a user input error, not a benchmark result: the CLI
turns it into exit code 2 and no record is written, because a request we could
not even parse never measured anything.
"""
from __future__ import annotations

from typing import Any

from clousight_bench.core.canonical import CanonicalJSONError, canonical_json
from clousight_bench.core.errors import UserInputError
from clousight_bench.core.plugin import Task
from clousight_bench.core.redaction import redact
from clousight_bench.core.schema import RunSpec


class InvalidRunSpecError(UserInputError):
    """A RunSpec, its target, its params or its task config cannot be used."""


def _require_encodable(label: str, value: Any) -> None:
    try:
        canonical_json(redact(value))
    except CanonicalJSONError as exc:
        raise InvalidRunSpecError(f"{label} is not canonically encodable: {exc}") from exc


def validate_run_spec(spec: RunSpec, task: Task) -> None:
    for field in ("domain", "task_id", "platform"):
        value = getattr(spec, field, None)
        if not isinstance(value, str) or not value.strip():
            raise InvalidRunSpecError(
                f"{field} must be a non-empty string, got {value!r}"
            )
    for field in ("target", "params"):
        value = getattr(spec, field)
        if not isinstance(value, dict):
            raise InvalidRunSpecError(
                f"{field} must be a mapping, got {type(value).__name__}"
            )
        _require_encodable(field, value)
    try:
        config = task.config(spec.params)
    except Exception as exc:  # noqa: BLE001 - a task rejecting params is a user error
        raise InvalidRunSpecError(
            f"task {spec.task_id!r} rejected these params in config(): "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(config, dict):
        raise InvalidRunSpecError(
            f"task {spec.task_id!r} config() must return a mapping, "
            f"got {type(config).__name__}"
        )
    _require_encodable("task config", config)
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `uv run pytest tests/test_validation.py -v`

Expected: 8 passed (the parametrized identifier test counts as 3).

- [ ] **Step 5: Run ruff and the full suite**

Run:

```bash
uv run ruff check src tests
uv run pytest -q
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/clousight_bench/core/validation.py tests/test_validation.py
git commit -s -m "feat: validate the run request before provisioning"
```

---

### Task 12: Cut the Lifecycle, Store, Report and CLI Over to ResultRecord 0.2

**This is the atomic cutover and cannot be split:** the moment the orchestrator emits a `0.2` record, the store, the report reader, the schema module and every orchestrator-level test must move with it.

**Files:**
- Modify: `src/clousight_bench/__init__.py`
- Modify: `src/clousight_bench/core/schema.py` (delete `ResultRecord`, `config_hash`, `EVIDENCE_LAYERS`; re-export `ResultRecord` from `record.py`)
- Modify: `src/clousight_bench/core/plugin.py` (delete `TaskOutput` and the bridge `run()`; make `execute`/`score` abstract)
- Modify: `src/clousight_bench/core/orchestrator.py` (full rewrite)
- Modify: `src/clousight_bench/core/store.py` (full rewrite of `persist` and the Parquet bridge)
- Modify: `src/clousight_bench/core/report.py` (full rewrite of the reader and renderer)
- Modify: `src/clousight_bench/cli.py` (status-based exit codes, `--debug`)
- Create: `tests/test_lifecycle.py`
- Create: `tests/test_report.py`
- Modify: `tests/test_schema.py`, `tests/test_store.py`, `tests/test_enricher.py`, `tests/test_preflight.py`, `tests/test_agent_runtime_local.py`, `tests/test_agent_runtime_dimensions.py`, `tests/test_orchestrator_series_bridge.py`

**Interfaces:**
- Produces: `clousight_bench.RESULT_SCHEMA_VERSION == "0.2"`.
- Produces: `orchestrator.execute(spec, results_dir=None, enrich=True, preflight=True, publisher=None, debug=False) -> ResultRecord` where `ResultRecord` is the `0.2` type.
- Produces: `ResultStore.persist(record: ResultRecord) -> Path` — atomic, digest-stamped, identity-leak-checked, with an emergency fallback that also records the `PERSIST` failure inside the record it writes.
- Produces: CLI exit codes `completed`/`unsupported` → `0`, `failed`/`invalid` → `1`, user input error → `2`.
- Produces: `csbench run --debug`, writing tracebacks to `<results>/debug/<run_id>.log` and never into the record.
- Removes: `TaskOutput`, `Task.run`, `schema.config_hash`, `schema.EVIDENCE_LAYERS`.

- [ ] **Step 1: Write the failing lifecycle test**

Create `tests/test_lifecycle.py`:

```python
"""The lifecycle must never leak a resource and never lose an observation."""
import json

import pytest

import clousight_bench.core.orchestrator as orch
from clousight_bench.core.observation import (
    Measurement,
    ObservationBundle,
    TaskExecutionError,
    TaskResult,
)
from clousight_bench.core.plugin import DomainPack, ProviderAdapter, Task
from clousight_bench.core.schema import RunSpec

CALLS: list[str] = []


class _Adapter(ProviderAdapter):
    name = "fake"
    status = "reference"
    provider = None
    setup_raises = False
    teardown_raises = False

    def setup(self) -> None:
        CALLS.append("setup")
        if type(self).setup_raises:
            raise RuntimeError("setup blew up after allocating half a cluster")

    def teardown(self) -> None:
        CALLS.append("teardown")
        if type(self).teardown_raises:
            raise OSError("teardown could not reach the control plane")


class _Task(Task):
    task_id = "TX"
    title = "fake"
    evidence_layer = "C"
    task_revision = "1"
    scorer_revision = "1"
    execute_raises = False
    score_raises = False

    def config(self, params):
        return {"task_id": self.task_id}

    def environment_facts(self, adapter, params):
        return {"fake": True}

    def execute(self, adapter, params):
        CALLS.append("execute")
        if type(self).execute_raises:
            raise ConnectionError("the runtime dropped the session")
        return ObservationBundle(
            observations={"hits": 3}, series={"latency_ms": [[1, 10.0]]}
        )

    def score(self, observations):
        CALLS.append("score")
        if type(self).score_raises:
            raise ZeroDivisionError("scorer bug")
        return TaskResult(
            measurements={
                "hits": Measurement(
                    value=observations.observations["hits"], unit="count", evidence="C"
                )
            },
            notes="ok",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )


class _Domain(DomainPack):
    domain = "fake-domain"

    def tasks(self):
        return {"TX": _Task}

    def adapters(self):
        return {"fake": _Adapter}


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    CALLS.clear()
    _Adapter.setup_raises = False
    _Adapter.teardown_raises = False
    _Task.execute_raises = False
    _Task.score_raises = False
    monkeypatch.setattr(orch, "get_domain", lambda name: _Domain())
    monkeypatch.setattr(orch, "load_enrichers", list)


def _run(tmp_path, **kwargs):
    return orch.execute(
        RunSpec("fake-domain", "TX", "fake"), results_dir=tmp_path, **kwargs
    )


def test_happy_path_produces_a_completed_0_2_record(tmp_path):
    record = _run(tmp_path)
    assert record.schema_version == "0.2"
    assert record.status == "completed"
    assert record.errors == []
    assert record.measurements["hits"] == {"value": 3, "unit": "count", "evidence": "C"}
    assert record.observations == {"hits": 3}
    assert record.identity.task_revision == "1"
    assert record.identity.adapter_status == "reference"
    assert record.environment.mode == "local"
    assert record.environment.facts == {"fake": True}
    assert record.fingerprints.benchmark.startswith("sha256:")
    assert record.fingerprints.record_digest.startswith("sha256:")
    assert record.run.stages["TEARDOWN"] == "ok"
    assert CALLS == ["setup", "execute", "teardown", "score"]


def test_partial_setup_failure_still_tears_down(tmp_path):
    _Adapter.setup_raises = True
    record = _run(tmp_path)
    assert CALLS == ["setup", "teardown"]
    assert record.status == "failed"
    assert record.run.stages["SETUP"] == "failed"
    assert record.run.stages["TEARDOWN"] == "ok"
    assert [e["stage"] for e in record.errors] == ["SETUP"]


def test_teardown_error_does_not_overwrite_the_execute_error(tmp_path):
    _Task.execute_raises = True
    _Adapter.teardown_raises = True
    record = _run(tmp_path)
    assert record.status == "failed"
    assert [e["stage"] for e in record.errors] == ["EXECUTE", "TEARDOWN"]
    assert record.errors[0]["type"] == "ConnectionError"
    assert record.errors[1]["type"] == "OSError"


def test_teardown_error_alone_keeps_the_run_completed(tmp_path):
    _Adapter.teardown_raises = True
    record = _run(tmp_path)
    assert record.status == "completed"
    assert [e["stage"] for e in record.errors] == ["TEARDOWN"]
    assert record.measurements["hits"]["value"] == 3


def test_score_failure_keeps_the_observations(tmp_path):
    _Task.score_raises = True
    record = _run(tmp_path)
    assert record.status == "failed"
    assert record.observations == {"hits": 3}
    assert record.series == {"latency_ms": [[1, 10.0]]}
    assert record.measurements == {}
    assert [e["stage"] for e in record.errors] == ["SCORE"]
    assert record.errors[0]["type"] == "ZeroDivisionError"


def test_execute_failure_keeps_partial_observations_carried_by_the_error(
    tmp_path, monkeypatch,
):
    partial = ObservationBundle(observations={"attempts": [{"ok": False}]})
    monkeypatch.setattr(
        _Task,
        "execute",
        lambda self, adapter, params: (_ for _ in ()).throw(
            TaskExecutionError(
                "tool failed", observations=partial,
                code="tool_failed", retryable=True,
            )
        ),
    )
    record = _run(tmp_path)
    assert record.status == "failed"
    assert record.observations == partial.observations
    assert record.errors[0]["stage"] == "EXECUTE"
    assert record.errors[0]["code"] == "tool_failed"
    assert record.errors[0]["retryable"] is True


def test_records_never_carry_a_traceback(tmp_path):
    _Task.execute_raises = True
    payload = json.dumps(_run(tmp_path).to_dict())
    assert "Traceback" not in payload
    assert "test_lifecycle.py" not in payload


def test_debug_writes_the_traceback_to_a_local_log_only(tmp_path):
    _Task.execute_raises = True
    record = _run(tmp_path, debug=True)
    log = tmp_path / "debug" / f"{record.run.run_id}.log"
    assert log.is_file()
    assert "Traceback" in log.read_text(encoding="utf-8")
    assert "Traceback" not in json.dumps(record.to_dict())


def test_unsupported_capability_becomes_an_unsupported_status(tmp_path, monkeypatch):
    monkeypatch.setattr(
        _Task, "score",
        lambda self, observations: TaskResult(unsupported=True, notes="no API"),
    )
    assert _run(tmp_path).status == "unsupported"


def test_resolve_and_validate_errors_write_no_record(tmp_path):
    from clousight_bench.core.errors import UnknownTaskError

    with pytest.raises(UnknownTaskError):
        orch.execute(RunSpec("fake-domain", "NOPE", "fake"), results_dir=tmp_path)
    assert list(tmp_path.rglob("*.json")) == []
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_lifecycle.py -v`

Expected: FAIL — `execute()` still returns the legacy record, so
`record.schema_version` does not exist.

- [ ] **Step 3: Export the schema version constant**

Replace `src/clousight_bench/__init__.py` with:

```python
"""Clousight Bench: reproducible, evidence-graded benchmarking for cloud products."""

RUNNER_VERSION = "0.2.0"

# The result contract readers negotiate on. Phase 1B replaced schema "1.0".
RESULT_SCHEMA_VERSION = "0.2"

# Temporary compatibility contract for the plugin surface.
# Phase 1D replaces this with API-range negotiation.
PLUGIN_API_VERSION = "1.0"

__version__ = RUNNER_VERSION
```

- [ ] **Step 4: Reduce `schema.py` to the request side and re-export the record**

Replace `src/clousight_bench/core/schema.py` with:

```python
"""The request side of a benchmark run, plus the public ResultRecord import path.

``RunSpec`` says what to run. ``ResultRecord`` (defined in ``core/record.py``)
says what happened; it is re-exported here so plugins keep one stable import
path across the 1.0 -> 0.2 schema change.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from clousight_bench.core.record import ResultRecord

__all__ = ["ResultRecord", "RunSpec", "new_run_id", "utc_now"]


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class RunSpec:
    """What to run: one task from one domain against one provider target.

    ``params`` are task-level overrides; ``target`` is the provider-specific
    config (endpoint / auth reference / region / cluster size ...). Everything
    here reaches the benchmark and environment fingerprints, so never put a raw
    secret in a RunSpec -- reference it by env var name instead.
    """

    domain: str
    task_id: str
    platform: str
    target: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def new_run_id() -> str:
    return f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
```

`config_hash` and `EVIDENCE_LAYERS` are gone: fingerprints replace the first and
`core/observation.py` owns the second.

- [ ] **Step 5: Delete `TaskOutput` and the bridge from `plugin.py`**

In `src/clousight_bench/core/plugin.py`:

- delete the whole `@dataclass class TaskOutput:` block;
- delete the `run()` method added in Task 6;
- add `@abstractmethod` above `execute` and `score`, and replace their bodies
  with docstrings only:

```python
    @abstractmethod
    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        """Drive the system under test and return raw observations only."""

    @abstractmethod
    def score(self, observations: ObservationBundle) -> TaskResult:
        """Turn observations into measurements and findings. Pure function."""
```

- change the module import to `from clousight_bench.core.observation import ObservationBundle, TaskResult` (`collect` is no longer needed here);
- update the module docstring's pipeline line to:

```
    resolve -> validate -> preflight -> setup -> execute -> collect
            -> score -> enrich -> persist -> publish
```

- [ ] **Step 6: Rewrite the orchestrator**

Replace `src/clousight_bench/core/orchestrator.py` with:

```python
"""Orchestrator: the auditable lifecycle every domain shares.

    RESOLVE -> VALIDATE -> PREFLIGHT -> SETUP -> EXECUTE -> COLLECT
            -> SCORE -> ENRICH -> PERSIST -> optional PUBLISH

TEARDOWN is deliberately not a step in that line. It is the mandatory
``finally`` boundary around SETUP -> COLLECT: once SETUP is entered, teardown
always runs, even when setup itself failed half-way, and a teardown failure is
recorded as its own stage error without overwriting the execute or collect
error that caused it.

RESOLVE and VALIDATE failures raise ``UserInputError`` and write no record: a
request we could not parse never measured anything. Every later failure is a
recorded outcome, because "the platform failed" is itself a benchmark finding.
"""
from __future__ import annotations

import logging
import platform as platform_mod
import traceback
from pathlib import Path
from typing import Any

from clousight_bench import RUNNER_VERSION
from clousight_bench.core.errors import (
    AdapterNotRunnableError,
    UnknownPlatformError,
    UnknownTaskError,
)
from clousight_bench.core.fingerprints import (
    benchmark_fingerprint,
    environment_fingerprint,
    implementation_fingerprint,
)
from clousight_bench.core.observation import (
    Finding,
    ObservationBundle,
    TaskExecutionError,
    TaskResult,
    collect,
)
from clousight_bench.core.plugin import DomainPack, ProviderAdapter, Task
from clousight_bench.core.record import (
    Environment,
    Fingerprints,
    Identity,
    ResultRecord,
    RunInfo,
    StageError,
)
from clousight_bench.core.redaction import redact
from clousight_bench.core.registry import get_domain, load_enrichers
from clousight_bench.core.schema import RunSpec, new_run_id, utc_now
from clousight_bench.core.store import ResultStore
from clousight_bench.core.validation import validate_run_spec

logger = logging.getLogger(__name__)

DEFAULT_RESULTS_DIR = Path("results")

# Stages whose failure means the benchmark itself did not produce a verdict.
_FATAL_STAGES = ("SETUP", "EXECUTE", "COLLECT", "SCORE")


def execute(
    spec: RunSpec,
    results_dir: Path | None = None,
    enrich: bool = True,
    preflight: bool = True,
    debug: bool = False,
) -> ResultRecord:
    """Run one RunSpec through the full lifecycle and persist the result."""
    results_dir = Path(results_dir or DEFAULT_RESULTS_DIR)
    run_id = new_run_id()
    started_at = utc_now()
    stages: dict[str, str] = {}
    errors: list[StageError] = []

    # RESOLVE -- raises UserInputError; no record is written.
    pack, task, adapter_cls = _resolve(spec)
    adapter = adapter_cls(spec.target)
    stages["RESOLVE"] = "ok"

    # VALIDATE -- raises UserInputError; no record is written.
    validate_run_spec(spec, task)
    stages["VALIDATE"] = "ok"

    logger.info("run %s: %s/%s on %s", run_id, spec.domain, spec.task_id, spec.platform)

    workload = task.workload_identity(spec.params)
    facts = task.environment_facts(adapter, spec.params)
    identity = Identity(
        domain=spec.domain,
        task_id=task.task_id,
        task_revision=task.task_revision,
        scorer_revision=task.scorer_revision,
        adapter=adapter_cls.name,
        adapter_status=adapter_cls.status,
        core_version=RUNNER_VERSION,
        workload=str(workload["workload"]),
        workload_version=str(workload["workload_version"]),
        plugin_versions=_plugin_versions(pack, adapter_cls),
    )
    environment = Environment(
        region=str(spec.target.get("region", "")),
        mode="cloud" if adapter_cls.provider else "local",
        python_version=platform_mod.python_version(),
        os_name=platform_mod.system(),
        facts=redact(facts),
    )
    fingerprints = Fingerprints(
        benchmark=benchmark_fingerprint(
            task_id=task.task_id,
            task_revision=task.task_revision,
            scorer_revision=task.scorer_revision,
            workload=identity.workload,
            workload_version=identity.workload_version,
            assets=list(workload["assets"]),
            params=task.config(spec.params),
        ),
        environment=environment_fingerprint(
            region=environment.region, mode=environment.mode, facts=environment.facts
        ),
        implementation=implementation_fingerprint(
            core_version=RUNNER_VERSION,
            domain=spec.domain,
            adapter=identity.adapter,
            adapter_status=identity.adapter_status,
            plugin_versions=identity.plugin_versions,
        ),
    )

    findings: list[Finding] = []
    bundle = ObservationBundle()
    result: TaskResult | None = None

    # PREFLIGHT -- a critical failure means the request could not be measured
    # here, so the record is `invalid` and nothing is ever provisioned.
    if preflight:
        report = adapter.preflight(task)
        if not report.ok:
            logger.error("run %s aborted at preflight:\n%s", run_id, report.format())
            stages["PREFLIGHT"] = "failed"
            errors.append(
                StageError(
                    stage="PREFLIGHT",
                    code="preflight_failed",
                    type="PreflightFailure",
                    message=report.summary(),
                    retryable=True,
                )
            )
            findings.append(
                Finding(
                    code="core.preflight_failed",
                    severity="critical",
                    summary=report.summary(),
                    evidence="B",
                    details={"checks": [c.line() for c in report.checks]},
                )
            )
            record = _build_record(
                run_id, started_at, stages, identity, environment, fingerprints,
                "invalid", None, findings, ObservationBundle(), errors,
            )
            return _finish(record, results_dir, enrich=False)
        stages["PREFLIGHT"] = "ok"
    else:
        stages["PREFLIGHT"] = "skipped"

    # SETUP -> EXECUTE -> COLLECT, with TEARDOWN as the mandatory finally boundary.
    entered_setup = False
    try:
        entered_setup = True
        adapter.setup()
        stages["SETUP"] = "ok"
        bundle = task.execute(adapter, spec.params)
        stages["EXECUTE"] = "ok"
        bundle = collect(bundle)
        stages["COLLECT"] = "ok"
    except TaskExecutionError as exc:
        bundle = exc.observations
        stages["EXECUTE"] = "failed"
        errors.append(
            StageError(
                stage="EXECUTE",
                code=exc.code,
                type=type(exc).__name__,
                message=str(exc),
                retryable=exc.retryable,
            )
        )
        _log_traceback(results_dir, run_id, debug, exc)
    except Exception as exc:  # noqa: BLE001 - every failure is a recorded outcome
        stage = _failed_stage(stages)
        stages[stage] = "failed"
        errors.append(_stage_error(stage, exc))
        _log_traceback(results_dir, run_id, debug, exc)
    finally:
        if entered_setup:
            try:
                adapter.teardown()
                stages["TEARDOWN"] = "ok"
            except Exception as exc:  # noqa: BLE001 - never mask the primary error
                stages["TEARDOWN"] = "failed"
                errors.append(_stage_error("TEARDOWN", exc))
                _log_traceback(results_dir, run_id, debug, exc)

    # SCORE -- pure; observations already collected survive a scorer failure.
    if stages.get("COLLECT") == "ok":
        try:
            result = task.score(bundle)
            stages["SCORE"] = "ok"
        except Exception as exc:  # noqa: BLE001
            stages["SCORE"] = "failed"
            errors.append(_stage_error("SCORE", exc))
            _log_traceback(results_dir, run_id, debug, exc)
    else:
        stages["SCORE"] = "skipped"

    record = _build_record(
        run_id, started_at, stages, identity, environment, fingerprints,
        _status_for(errors, result), result, findings, bundle, errors,
    )
    return _finish(record, results_dir, enrich=enrich)


def _resolve(spec: RunSpec) -> tuple[DomainPack, Task, type[ProviderAdapter]]:
    pack = get_domain(spec.domain)
    task_classes = pack.tasks()
    if spec.task_id not in task_classes:
        raise UnknownTaskError(
            f"task {spec.task_id!r} not in domain {spec.domain!r}: {sorted(task_classes)}"
        )
    adapter_classes = pack.adapters()
    if spec.platform not in adapter_classes:
        raise UnknownPlatformError(
            f"platform {spec.platform!r} not in domain {spec.domain!r}: "
            f"{sorted(adapter_classes)}"
        )
    adapter_cls = adapter_classes[spec.platform]
    if not adapter_cls.is_runnable():
        raise AdapterNotRunnableError(
            f"platform {spec.platform!r} is a skeleton and cannot run; "
            "choose a reference/wired adapter or implement this adapter first"
        )
    return pack, task_classes[spec.task_id](), adapter_cls


def _plugin_versions(
    pack: DomainPack, adapter_cls: type[ProviderAdapter]
) -> dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version

    from clousight_bench.core.fingerprints import UNKNOWN

    modules = {
        type(pack).__module__.split(".")[0],
        adapter_cls.__module__.split(".")[0],
    }
    versions: dict[str, str] = {}
    for module in sorted(modules):
        distribution = module.replace("_", "-")
        try:
            versions[distribution] = version(distribution)
        except PackageNotFoundError:
            versions[distribution] = UNKNOWN
    return versions


def _failed_stage(stages: dict[str, str]) -> str:
    for stage in ("SETUP", "EXECUTE", "COLLECT"):
        if stage not in stages:
            return stage
    return "COLLECT"


def _stage_error(stage: str, exc: BaseException) -> StageError:
    return StageError(
        stage=stage,
        code=f"{stage.lower()}_failed",
        type=type(exc).__name__,
        message=str(exc),
        retryable=isinstance(exc, (ConnectionError, TimeoutError, OSError)),
    )


def _status_for(errors: list[StageError], result: TaskResult | None) -> str:
    if any(e.stage == "PREFLIGHT" for e in errors):
        return "invalid"
    if any(e.stage in _FATAL_STAGES for e in errors):
        return "failed"
    if result is not None and result.unsupported:
        return "unsupported"
    return "completed"


def _build_record(
    run_id: str,
    started_at: str,
    stages: dict[str, str],
    identity: Identity,
    environment: Environment,
    fingerprints: Fingerprints,
    status: str,
    result: TaskResult | None,
    findings: list[Finding],
    bundle: ObservationBundle,
    errors: list[StageError],
) -> ResultRecord:
    all_findings = list(findings) + list(result.findings if result else [])
    extensions: dict[str, Any] = {}
    if result is not None and result.notes:
        # "core" is the reserved extension namespace; plugins use their own name.
        extensions["core"] = {"notes": result.notes}
    return ResultRecord(
        run=RunInfo(
            run_id=run_id,
            started_at=started_at,
            finished_at=utc_now(),
            stages=dict(stages),
        ),
        identity=identity,
        environment=environment,
        fingerprints=fingerprints,
        status=status,
        measurements={
            name: m.to_dict()
            for name, m in (result.measurements if result else {}).items()
        },
        findings=[f.to_dict() for f in all_findings],
        observations=dict(bundle.observations),
        series=dict(bundle.series),
        artifacts=list(bundle.artifacts),
        extensions=extensions,
        errors=[e.to_dict() for e in errors],
    )


def _finish(record: ResultRecord, results_dir: Path, enrich: bool) -> ResultRecord:
    if enrich:
        for enricher in load_enrichers():
            record = enricher.enrich(record)
        record.run.stages["ENRICH"] = "ok"
    else:
        record.run.stages["ENRICH"] = "skipped"
    record.run.finished_at = utc_now()
    path = ResultStore(results_dir).persist(record)
    logger.info("result -> %s", path)
    return record


def _log_traceback(
    results_dir: Path, run_id: str, debug: bool, exc: BaseException
) -> None:
    """Tracebacks belong in a local log, never in a shareable record."""
    logger.exception("run %s stage failure", run_id, exc_info=exc)
    if not debug:
        return
    log_dir = Path(results_dir) / "debug"
    log_dir.mkdir(parents=True, exist_ok=True)
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    with (log_dir / f"{run_id}.log").open("a", encoding="utf-8") as handle:
        handle.write(text)
```

The `publisher` argument is added in Task 13; leave it out for now.

- [ ] **Step 7: Rewrite the store**

Replace `src/clousight_bench/core/store.py` with:

```python
"""ResultStore: persist a ResultRecord 0.2 atomically, or say loudly where it went.

Record layout stays ``results/<domain>/<adapter>/<task_id>-<run_id>.json`` so
existing tooling keeps finding results. Writes are atomic, the payload carries
its own content digest, and an operator-identifying string is refused rather
than published. When the results directory cannot be written at all, the record
is dumped into the system temp directory and its absolute path is printed --
losing a completed measurement silently is the one failure mode this layer must
not have.

With the optional [store] extra (duckdb + pyarrow) a record's series is
externalized to a per-run Parquet long table and the record's ``series`` field
becomes a pointer. Long-table columns (the stable handshake for cb-dataservice
and the SaaS web):

    run_id | domain | task_id | platform | benchmark_fingerprint | series | t | value | unit
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from clousight_bench.core.fingerprints import record_digest
from clousight_bench.core.persistence import atomic_write_text, emergency_write_text
from clousight_bench.core.record import ResultRecord, StageError
from clousight_bench.core.redaction import SensitiveDataError, find_identity_leaks

try:  # optional [store] extra
    import duckdb  # noqa: F401
    import pyarrow  # noqa: F401

    STORE_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on install extras
    STORE_AVAILABLE = False

_LONG_COLUMNS = [
    "run_id", "domain", "task_id", "platform", "benchmark_fingerprint",
    "series", "t", "value", "unit",
]


class ResultStore:
    def __init__(self, results_dir: Path) -> None:
        self.results_dir = Path(results_dir)

    def _record_path(self, rec: ResultRecord) -> Path:
        out_dir = self.results_dir / rec.identity.domain / rec.identity.adapter
        return out_dir / f"{rec.identity.task_id}-{rec.run.run_id}.json"

    def _run_dir(self, rec: ResultRecord) -> Path:
        return self.results_dir / rec.identity.domain / rec.identity.adapter / rec.run.run_id

    def persist(self, record: ResultRecord) -> Path:
        series_pointer: dict[str, Any] | None = None
        if STORE_AVAILABLE and record.series and "$parquet" not in record.series:
            try:
                series_pointer = {"$parquet": self._write_series_parquet(record)}
            except OSError:
                # The sidecar is an optimisation. If it cannot be written we keep
                # the series inline rather than losing the observation.
                series_pointer = None

        record.run.stages["PERSIST"] = "ok"
        try:
            return atomic_write_text(
                self._record_path(record), self._render(record, series_pointer)
            )
        except OSError as exc:
            record.run.stages["PERSIST"] = "failed"
            record.errors.append(
                StageError(
                    stage="PERSIST",
                    code="persist_failed",
                    type=type(exc).__name__,
                    message=str(exc),
                    retryable=True,
                ).to_dict()
            )
            name = (
                f"{record.identity.domain}-{record.identity.task_id}"
                f"-{record.run.run_id}.json"
            )
            path = emergency_write_text(name, self._render(record, series_pointer))
            print(
                f"clousight-bench: could not write the results directory ({exc}); "
                f"emergency record written to {path}",
                file=sys.stderr,
            )
            return path

    def _render(
        self, record: ResultRecord, series_pointer: dict[str, Any] | None
    ) -> str:
        payload = record.to_dict()
        if series_pointer is not None:
            payload["series"] = series_pointer
        leaks = find_identity_leaks(payload)
        if leaks:
            raise SensitiveDataError(
                f"refusing to persist run {record.run.run_id}: operator-identifying "
                f"values at {leaks}"
            )
        digest = record_digest(payload)
        payload["fingerprints"]["record_digest"] = digest
        record.fingerprints.record_digest = digest
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def _write_series_parquet(self, record: ResultRecord) -> str:
        import pyarrow as pa
        import pyarrow.parquet as pq

        rows: dict[str, list] = {c: [] for c in _LONG_COLUMNS}
        for series_name, points in record.series.items():
            unit = str(record.measurements.get(series_name, {}).get("unit", ""))
            for t, value in points:
                rows["run_id"].append(record.run.run_id)
                rows["domain"].append(record.identity.domain)
                rows["task_id"].append(record.identity.task_id)
                rows["platform"].append(record.identity.adapter)
                rows["benchmark_fingerprint"].append(record.fingerprints.benchmark)
                rows["series"].append(series_name)
                rows["t"].append(t)
                rows["value"].append(float(value))
                rows["unit"].append(unit)
        run_dir = self._run_dir(record)
        run_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = run_dir / "series.parquet"
        pq.write_table(pa.table(rows), parquet_path)
        return str(parquet_path.relative_to(self.results_dir))

    def query_series(
        self, sql: str | None = None, glob: str = "**/series.parquet"
    ) -> list[dict[str, Any]]:
        if not STORE_AVAILABLE:
            raise ImportError(
                "query_series needs the [store] extra: pip install clousight-bench[store]"
            )
        import duckdb

        pattern = str(self.results_dir / glob)
        con = duckdb.connect()
        # Pass the (possibly glob) path via the relation API, not string
        # interpolation, so paths with quotes / special chars cannot break out
        # of the SQL (parameters aren't allowed inside CREATE VIEW read_parquet).
        con.read_parquet(pattern).create_view("series")
        cur = con.execute(sql or "SELECT * FROM series")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
```

- [ ] **Step 8: Rewrite the report reader and renderer**

Replace `src/clousight_bench/core/report.py` with:

```python
"""Comparison report generator.

Reads every ResultRecord 0.2 under a results directory and renders a markdown
report: one comparison matrix per (domain, task) across adapters, plus a
red-flag list built from the records' own findings and statuses. Deliberately
does NOT compute a blended cross-dimension score -- per-dimension reporting
only, because blended agent-benchmark rankings have near-zero agreement.

Every measurement carries its own evidence layer, so a reader never confuses a
controlled measurement (C) with a documentation reading (A) or an environment
observation (B).
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from clousight_bench.core.record import RecordError, ResultRecord

_SKIP_FILES = {"comparison.json", "migration-manifest.json", "publish-receipts.jsonl"}
_STATUS_MARK = {
    "completed": "✅",
    "unsupported": "➖",
    "failed": "❌",
    "invalid": "⚠️",
}


def _load_results(results_dir: Path) -> list[ResultRecord]:
    records: list[ResultRecord] = []
    for path in sorted(results_dir.rglob("*.json")):
        if path.name in _SKIP_FILES:
            continue
        try:
            records.append(ResultRecord.from_dict(json.loads(path.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, KeyError, TypeError, RecordError):
            continue
    return records


def _latest_per_cell(
    records: list[ResultRecord],
) -> dict[tuple[str, str, str], ResultRecord]:
    """Keep the most recent record per (domain, task, adapter)."""
    latest: dict[tuple[str, str, str], ResultRecord] = {}
    for rec in records:
        key = (rec.identity.domain, rec.identity.task_id, rec.identity.adapter)
        current = latest.get(key)
        if current is None or rec.run.started_at >= current.run.started_at:
            latest[key] = rec
    return latest


def _fmt_measurements(measurements: dict[str, dict[str, Any]]) -> str:
    if not measurements:
        return "—"
    parts = []
    for name, m in sorted(measurements.items()):
        unit = f" {m['unit']}" if m.get("unit") else ""
        parts.append(f"{name}={m['value']}{unit} [{m['evidence']}]")
    return "<br>".join(parts)


def _red_flags(records: dict[tuple[str, str, str], ResultRecord]) -> list[str]:
    flags: list[str] = []
    for (domain, task, adapter), rec in sorted(records.items()):
        if rec.status != "completed":
            reason = rec.errors[0]["message"] if rec.errors else "see result"
            flags.append(
                f"- `{domain}/{task}` on **{adapter}**: status `{rec.status}` — {reason}"
            )
        for finding in rec.findings:
            if finding.get("severity") in ("warning", "critical"):
                flags.append(
                    f"- `{domain}/{task}` on **{adapter}**: "
                    f"`{finding['code']}` ({finding['severity']}) — {finding['summary']}"
                )
    return flags


def generate_report(results_dir: Path, out_path: Path | None = None) -> str:
    results_dir = Path(results_dir)
    out_path = out_path or (results_dir / "comparison.md")

    records = _load_results(results_dir)
    if not records:
        report = (
            "# Clousight Bench comparison\n\nNo schema 0.2 results found under "
            f"`{results_dir}`.\n"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        return report

    latest = _latest_per_cell(records)
    by_task: dict[tuple[str, str], dict[str, ResultRecord]] = defaultdict(dict)
    for (domain, task, adapter), rec in latest.items():
        by_task[(domain, task)][adapter] = rec

    lines: list[str] = ["# Clousight Bench comparison", ""]
    lines.append(
        "Per-dimension results only — no blended score. Evidence layers: "
        "A=docs · B=observation · C=controlled measurement · D=marketing."
    )
    lines.append("")

    for (domain, task), adapters in sorted(by_task.items()):
        lines.append(f"## {domain} · {task}")
        lines.append("")
        lines.append(
            "| adapter | status | measurements | benchmark fingerprint | core |"
        )
        lines.append("|---|---|---|---|---|")
        for adapter, rec in sorted(adapters.items()):
            mark = _STATUS_MARK.get(rec.status, rec.status)
            short = rec.fingerprints.benchmark.removeprefix("sha256:")[:12]
            lines.append(
                f"| {adapter} | {mark} {rec.status} | "
                f"{_fmt_measurements(rec.measurements)} | `{short}` | "
                f"{rec.identity.core_version} |"
            )
        lines.append("")

    flags = _red_flags(latest)
    lines.append("## Red flags")
    lines.append("")
    lines.extend(flags if flags else ["- none"])
    lines.append("")

    report = "\n".join(lines)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    return report
```

- [ ] **Step 9: Update the CLI exit codes and add `--debug`**

In `src/clousight_bench/cli.py`, replace `_cmd_run` with:

```python
_EXIT_BY_STATUS = {"completed": 0, "unsupported": 0, "failed": 1, "invalid": 1}


def _cmd_run(args: argparse.Namespace) -> int:
    target: dict[str, Any] = {}
    params: dict[str, Any] = {}
    cfg = _load_config(args.config)
    if cfg:
        target = cfg.get("target", {})
        params = cfg.get("params", {})
    params.update(_parse_params(args.param))

    spec = RunSpec(
        domain=args.domain,
        task_id=args.task,
        platform=args.platform,
        target=target,
        params=params,
    )
    record = execute(
        spec,
        results_dir=Path(args.results),
        enrich=not args.no_enrich,
        preflight=not args.skip_preflight,
        debug=args.debug,
    )
    print(record.to_json())
    return _EXIT_BY_STATUS[record.status]
```

and register the flag next to the other `run` options:

```python
    run_p.add_argument("--debug", action="store_true",
                       help="write stage tracebacks to <results>/debug/<run_id>.log "
                            "(never into the record)")
```

Update the module docstring's `csbench run` line to mention `[--debug]`.

- [ ] **Step 10: Update the tests that assert the old record shape**

In `tests/test_schema.py`, delete `test_config_hash_is_deterministic_and_order_independent`,
`test_config_hash_changes_with_content`, `test_result_record_rejects_bad_evidence_layer`,
`test_result_record_roundtrip`, `test_result_record_has_data_contract_defaults` and
`test_from_dict_tolerates_unknown_keys` (their `0.2` equivalents live in
`tests/test_record.py`), and leave the file as:

```python
"""The request side of a run, plus the version contract."""
from clousight_bench.core.schema import RunSpec


def test_runspec_to_dict():
    spec = RunSpec(domain="d", task_id="t", platform="p", target={"k": "v"}, params={"n": 1})
    assert spec.to_dict()["target"] == {"k": "v"}


def test_plugin_api_version_exposed():
    import clousight_bench

    assert clousight_bench.PLUGIN_API_VERSION == "1.0"


def test_package_and_schema_versions():
    import clousight_bench

    assert clousight_bench.__version__ == "0.2.0"
    assert clousight_bench.RUNNER_VERSION == "0.2.0"
    assert clousight_bench.RESULT_SCHEMA_VERSION == "0.2"


def test_result_record_is_reexported_from_schema():
    from clousight_bench.core.record import ResultRecord as Defined
    from clousight_bench.core.schema import ResultRecord as Reexported

    assert Reexported is Defined
```

In `tests/test_store.py`, replace `_rec` and the assertions with the `0.2` shape
and add the emergency-path test:

```python
"""ResultStore: atomic 0.2 records, optional Parquet series, emergency fallback."""
import json

import pytest

from clousight_bench.core.persistence import EMERGENCY_DIR_NAME
from clousight_bench.core.record import (
    Environment,
    Fingerprints,
    Identity,
    ResultRecord,
    RunInfo,
)
from clousight_bench.core.redaction import SensitiveDataError
from clousight_bench.core.schema import utc_now
from clousight_bench.core.store import STORE_AVAILABLE, ResultStore


def _rec(series=None, measurements=None, facts=None) -> ResultRecord:
    return ResultRecord(
        run=RunInfo(run_id="run-x", started_at=utc_now(), finished_at=utc_now()),
        identity=Identity(domain="agent-runtime", task_id="T1.3", task_revision="2",
                          scorer_revision="2", adapter="local-sim",
                          adapter_status="reference", core_version="0.2.0"),
        environment=Environment(region="", mode="local", python_version="3.12.0",
                                os_name="Linux", facts=facts or {}),
        fingerprints=Fingerprints(benchmark="sha256:a", environment="sha256:b",
                                  implementation="sha256:c"),
        status="completed",
        measurements=measurements or {"p99_ms": {"value": 9, "unit": "ms", "evidence": "C"}},
        series=series or {},
    )


def test_persist_keeps_the_domain_adapter_task_run_layout(tmp_path):
    path = ResultStore(tmp_path).persist(_rec())
    expected = (tmp_path / "agent-runtime" / "local-sim" / "T1.3-run-x.json").resolve()
    assert path == expected
    data = json.loads(expected.read_text(encoding="utf-8"))
    assert data["schema_version"] == "0.2"
    assert data["measurements"]["p99_ms"]["value"] == 9
    assert data["run"]["stages"]["PERSIST"] == "ok"


def test_persist_stamps_a_record_digest(tmp_path):
    record = _rec()
    path = ResultStore(tmp_path).persist(record)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["fingerprints"]["record_digest"].startswith("sha256:")
    assert record.fingerprints.record_digest == data["fingerprints"]["record_digest"]


def test_persist_leaves_no_temp_file(tmp_path):
    ResultStore(tmp_path).persist(_rec())
    names = sorted(p.name for p in (tmp_path / "agent-runtime" / "local-sim").iterdir())
    assert names == ["T1.3-run-x.json"]


def test_persist_refuses_to_write_an_operator_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "clousight_bench.core.store.find_identity_leaks",
        lambda payload: ["$.environment.facts.host"],
    )
    with pytest.raises(SensitiveDataError, match="operator-identifying"):
        ResultStore(tmp_path).persist(_rec(facts={"host": "build-box"}))


def test_primary_write_failure_falls_back_to_an_emergency_file(tmp_path, monkeypatch, capsys):
    def _boom(path, text):
        raise OSError("read-only file system")

    monkeypatch.setattr("clousight_bench.core.store.atomic_write_text", _boom)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path / "tmp"))
    record = _rec()
    path = ResultStore(tmp_path / "results").persist(record)

    assert path.is_absolute()
    assert path.parent.name == EMERGENCY_DIR_NAME
    assert str(path) in capsys.readouterr().err
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["run"]["stages"]["PERSIST"] == "failed"
    assert data["errors"][-1]["stage"] == "PERSIST"
    assert data["errors"][-1]["type"] == "OSError"


@pytest.mark.skipif(not STORE_AVAILABLE, reason="requires [store] extra")
def test_series_externalized_to_parquet_and_queryable(tmp_path):
    store = ResultStore(tmp_path)
    store.persist(_rec(
        series={"latency_ms": [[1, 10.0], [2, 20.0]]},
        measurements={"latency_ms": {"value": 15, "unit": "ms", "evidence": "C"}},
    ))
    parquet = tmp_path / "agent-runtime" / "local-sim" / "run-x" / "series.parquet"
    assert parquet.exists()
    record_json = json.loads(
        (tmp_path / "agent-runtime" / "local-sim" / "T1.3-run-x.json").read_text()
    )
    assert record_json["series"] == {
        "$parquet": "agent-runtime/local-sim/run-x/series.parquet"
    }
    rows = store.query_series(
        "SELECT series, unit, count(*) AS n FROM series GROUP BY series, unit"
    )
    assert rows == [{"series": "latency_ms", "unit": "ms", "n": 2}]


@pytest.mark.skipif(not STORE_AVAILABLE, reason="requires [store] extra")
def test_an_unwritable_parquet_sidecar_keeps_the_series_inline(tmp_path, monkeypatch):
    import clousight_bench.core.store as store_mod

    def _boom(self, record):
        raise OSError("no space left on device")

    monkeypatch.setattr(store_mod.ResultStore, "_write_series_parquet", _boom)
    path = ResultStore(tmp_path).persist(_rec(series={"latency_ms": [[1, 10.0]]}))

    assert json.loads(path.read_text(encoding="utf-8"))["series"] == {
        "latency_ms": [[1, 10.0]]
    }


def test_series_inline_when_store_unavailable(tmp_path, monkeypatch):
    import clousight_bench.core.store as store_mod

    monkeypatch.setattr(store_mod, "STORE_AVAILABLE", False)
    store_mod.ResultStore(tmp_path).persist(_rec(series={"latency_ms": [[1, 10.0]]}))
    record_json = json.loads(
        (tmp_path / "agent-runtime" / "local-sim" / "T1.3-run-x.json").read_text()
    )
    assert record_json["series"] == {"latency_ms": [[1, 10.0]]}
```

In `tests/test_enricher.py`, replace the two record-shaped assertions with the
`extensions` namespace:

```python
"""ResultEnricher hook: orchestrator applies registered enrichers before persist."""
from clousight_bench.core.plugin import ResultEnricher
from clousight_bench.core.record import ResultRecord


def test_orchestrator_applies_enrichers(monkeypatch, tmp_path):
    import clousight_bench.core.orchestrator as orch
    from clousight_bench.core.schema import RunSpec

    class Tagger(ResultEnricher):
        name = "tagger"

        def enrich(self, record: ResultRecord) -> ResultRecord:
            record.extensions["tagger"] = {"applied": True}
            return record

    monkeypatch.setattr(orch, "load_enrichers", lambda: [Tagger()])
    rec = orch.execute(RunSpec("agent-runtime", "T1.3", "local-sim"), results_dir=tmp_path)
    assert rec.extensions["tagger"] == {"applied": True}
    assert rec.run.stages["ENRICH"] == "ok"


def test_orchestrator_skips_enrichers_when_disabled(monkeypatch, tmp_path):
    import clousight_bench.core.orchestrator as orch
    from clousight_bench.core.schema import RunSpec

    called = {"n": 0}

    def _boom():
        called["n"] += 1
        return []

    monkeypatch.setattr(orch, "load_enrichers", _boom)
    rec = orch.execute(
        RunSpec("agent-runtime", "T1.3", "local-sim"), results_dir=tmp_path, enrich=False
    )
    assert called["n"] == 0
    assert rec.run.stages["ENRICH"] == "skipped"
```

In `tests/test_preflight.py`, replace the three orchestrator-gate tests with:

```python
def test_run_aborts_at_preflight_not_midrun(monkeypatch, tmp_path):
    monkeypatch.setattr(AliyunAgentRunAdapter, "status", "wired")
    _clear_aws(monkeypatch, tmp_path)
    spec = RunSpec("agent-runtime", "T1.3", "aliyun-agentrun",
                   target={"region": "cn-hangzhou"})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.status == "invalid"
    assert rec.run.stages["PREFLIGHT"] == "failed"
    assert "SETUP" not in rec.run.stages
    assert [e["stage"] for e in rec.errors] == ["PREFLIGHT"]
    assert "credentials" in rec.errors[0]["message"]
    # proves we stopped at the gate, not inside run_tool_plan (which raises NotWired)
    assert "NotImplemented" not in rec.errors[0]["message"]
    assert [f["code"] for f in rec.findings] == ["core.preflight_failed"]


def test_skip_preflight_reaches_the_real_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(AliyunAgentRunAdapter, "status", "wired")
    _clear_aws(monkeypatch, tmp_path)
    spec = RunSpec("agent-runtime", "T1.3", "aliyun-agentrun",
                   target={"region": "cn-hangzhou"})
    rec = execute(spec, results_dir=tmp_path, preflight=False)
    # gate off -> we fail LATER, mid-run (mock unreachable / skeleton NotWired),
    # exactly the late error the preflight gate exists to prevent.
    assert rec.status == "failed"
    assert rec.run.stages["PREFLIGHT"] == "skipped"
    assert rec.run.stages["TEARDOWN"] == "ok"
    assert [e["stage"] for e in rec.errors] == ["EXECUTE"]


def test_local_sim_run_still_works_with_preflight(tmp_path):
    rec = execute(RunSpec("agent-runtime", "T1.3", "local-sim"), results_dir=tmp_path)
    assert rec.status == "completed"
    assert rec.run.stages["PREFLIGHT"] == "ok"
```

In `tests/test_agent_runtime_local.py`, replace the four tests with:

```python
def test_local_sim_auto_retry_recovers(tmp_path):
    spec = RunSpec(domain="agent-runtime", task_id="T1.3", platform="local-sim",
                   target={"recovery": {"mode": "auto-retry"}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.status == "completed"
    assert rec.measurements["recovery_mode"] == {
        "value": "auto-retry", "unit": "", "evidence": "C"}
    assert rec.measurements["final_state"]["value"] == "completed"
    assert rec.measurements["budgeted_success"]["value"] is True
    assert rec.fingerprints.benchmark.startswith("sha256:")


def test_local_sim_fail_fast_aborts(tmp_path):
    spec = RunSpec(domain="agent-runtime", task_id="T1.3", platform="local-sim",
                   target={"recovery": {"mode": "fail-fast"}})
    rec = execute(spec, results_dir=tmp_path)
    # the benchmark itself succeeded: it observed a fault and classified it
    assert rec.status == "completed"
    assert rec.measurements["recovery_mode"]["value"] == "fail-fast"
    assert rec.measurements["final_state"]["value"] == "aborted"
    assert rec.measurements["budgeted_success"]["value"] is False
    assert [f["code"] for f in rec.findings] == ["agent_runtime.recovery_fail_fast"]


def test_recovery_policy_changes_the_environment_fingerprint(tmp_path):
    retry = execute(RunSpec("agent-runtime", "T1.3", "local-sim",
                            target={"recovery": {"mode": "auto-retry"}}), results_dir=tmp_path)
    fail = execute(RunSpec("agent-runtime", "T1.3", "local-sim",
                           target={"recovery": {"mode": "fail-fast"}}), results_dir=tmp_path)
    assert retry.fingerprints.environment != fail.fingerprints.environment
    assert retry.fingerprints.benchmark == fail.fingerprints.benchmark


def test_result_file_persisted(tmp_path):
    execute(RunSpec("agent-runtime", "T1.3", "local-sim"), results_dir=tmp_path)
    files = list((tmp_path / "agent-runtime" / "local-sim").glob("T1.3-*.json"))
    assert files
```

In `tests/test_agent_runtime_dimensions.py`, replace every `rec.ok` /
`rec.metrics[...]` assertion with the `0.2` equivalents, for example:

```python
def test_t1_2_durable_state_persists(tmp_path):
    spec = RunSpec("agent-runtime", "T1.2", "local-sim",
                   target={"state_persistence": "durable"})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.status == "completed"
    assert rec.measurements["state_capability"]["value"] == "supported"
    assert rec.measurements["state_persisted"]["value"] is True
    assert rec.measurements["persistence_mode"]["value"] == "durable"
```

Apply the same mechanical change to the other seven tests in that file:
`rec.ok` becomes `rec.status == "completed"` (or `"unsupported"` for
`test_t4_2_otel_export_unsupported`, which returns `unsupported`), and
`rec.metrics["X"]` becomes `rec.measurements["X"]["value"]`.

Replace `tests/test_orchestrator_series_bridge.py` with:

```python
"""ObservationBundle series/artifacts must flow through the lifecycle into the record."""
import clousight_bench.core.orchestrator as orch
from clousight_bench.core.observation import Measurement, ObservationBundle, TaskResult
from clousight_bench.core.plugin import DomainPack, ProviderAdapter, Task
from clousight_bench.core.schema import RunSpec


class _Adapter(ProviderAdapter):
    name = "fake"
    status = "reference"


class _Task(Task):
    task_id = "TX"
    evidence_layer = "C"

    def config(self, params):
        return {}

    def execute(self, adapter, params):
        return ObservationBundle(
            series={"latency_ms": [[1, 10.0]]},
            artifacts=[{"kind": "trace", "path": "p", "media": "m", "sha256": "sha256:x"}],
        )

    def score(self, observations):
        return TaskResult(
            measurements={"p99_ms": Measurement(value=1, unit="ms", evidence="C")}
        )


class _Domain(DomainPack):
    domain = "fake-domain"

    def tasks(self):
        return {"TX": _Task}

    def adapters(self):
        return {"fake": _Adapter}


def test_series_and_artifacts_reach_record(monkeypatch, tmp_path):
    monkeypatch.setattr(orch, "get_domain", lambda name: _Domain())
    rec = orch.execute(
        RunSpec("fake-domain", "TX", "fake"), results_dir=tmp_path, enrich=False
    )
    assert rec.series == {"latency_ms": [[1, 10.0]]}
    assert rec.artifacts[0]["kind"] == "trace"
```

- [ ] **Step 11: Write the report test**

Create `tests/test_report.py`:

```python
"""The report renders 0.2 measurements and derives red flags from findings."""
from clousight_bench.core.orchestrator import execute
from clousight_bench.core.report import generate_report
from clousight_bench.core.schema import RunSpec


def test_empty_directory_says_so(tmp_path):
    report = generate_report(tmp_path)
    assert "No schema 0.2 results found" in report
    assert (tmp_path / "comparison.md").is_file()


def test_report_renders_measurements_with_evidence_and_status(tmp_path):
    execute(RunSpec("agent-runtime", "T1.3", "local-sim",
                    target={"recovery": {"mode": "auto-retry"}}), results_dir=tmp_path)
    report = generate_report(tmp_path)
    assert "## agent-runtime · T1.3" in report
    assert "recovery_mode=auto-retry [C]" in report
    assert "completed" in report
    assert "- none" in report


def test_warning_findings_become_red_flags(tmp_path):
    execute(RunSpec("agent-runtime", "T1.3", "local-sim",
                    target={"recovery": {"mode": "fail-fast"}}), results_dir=tmp_path)
    report = generate_report(tmp_path)
    assert "agent_runtime.recovery_fail_fast" in report
    assert "warning" in report


def test_unreadable_and_non_record_files_are_skipped(tmp_path):
    (tmp_path / "migration-manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "junk.json").write_text("not json", encoding="utf-8")
    assert "No schema 0.2 results found" in generate_report(tmp_path)
```

- [ ] **Step 12: Run the whole suite and fix what the cutover broke**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass. Any remaining failure is a reference to a deleted
symbol (`TaskOutput`, `config_hash`, `record.ok`, `record.metrics`); fix each by
using its `0.2` equivalent from the tables above.

- [ ] **Step 13: Verify the CLI end to end**

Run:

```bash
rm -rf /tmp/csbench-phase1b-cli
for task in T1.2 T1.3 T2.1 T4.1 T4.2; do
  uv run csbench run --domain agent-runtime --task "$task" --platform local-sim \
    --results /tmp/csbench-phase1b-cli
  echo "$task exit=$?"
done
uv run csbench run --domain bigdata-emr --task J1.1 --platform local-process \
  --results /tmp/csbench-phase1b-cli
uv run csbench report --results /tmp/csbench-phase1b-cli
uv run python -c "
import json, pathlib
paths = sorted(p for p in pathlib.Path('/tmp/csbench-phase1b-cli').rglob('*.json')
               if p.name != 'comparison.json')
assert paths, 'no records written'
for p in paths:
    d = json.loads(p.read_text())
    assert d['schema_version'] == '0.2', (p, d['schema_version'])
    assert d['status'] in {'completed', 'unsupported'}, (p, d['status'])
    assert d['fingerprints']['record_digest'].startswith('sha256:'), p
    for gone in ('ok', 'metrics', 'evidence_layer', 'config_hash'):
        assert gone not in d, (p, gone)
print(f'{len(paths)} records verified at schema 0.2')
"
```

Expected: every task exits `0`, the report renders, and the verifier prints
`6 records verified at schema 0.2`.

- [ ] **Step 14: Run ruff and commit**

```bash
uv run ruff check src tests
uv run pytest -q
git add -A src tests
git commit -s -m "feat!: emit ResultRecord 0.2 from an auditable lifecycle"
```

---

### Task 13: Extension Isolation and the Publisher Boundary

**Files:**
- Create: `src/clousight_bench/core/publish.py`
- Modify: `src/clousight_bench/core/orchestrator.py` (`_finish` and the `execute` signature)
- Modify: `src/clousight_bench/cli.py` (module docstring only, to mention receipts)
- Create: `tests/test_extension_isolation.py`

**Interfaces:**
- Produces: `ResultPublisher(ABC)` with `name: str = "abstract"` and `publish(self, record: ResultRecord) -> dict[str, Any]`.
- Produces: `RECEIPTS_FILE = "publish-receipts.jsonl"`.
- Produces: `append_receipt(results_dir: Path, receipt: dict[str, Any]) -> Path` — append-only JSONL, one compact line per attempt.
- Produces: `orchestrator.execute(..., publisher: ResultPublisher | None = None)`. Publishing is off unless a publisher is injected; Core ships none.
- Changes: `_finish` isolates each enricher — a failing enricher records `StageError(stage="ENRICH", ...)`, sets `run.stages["ENRICH"] = "failed"`, and never changes `status` or another enricher's output.
- Ordering: PERSIST happens before PUBLISH, so a publish failure can never damage the persisted record.

- [ ] **Step 1: Write the failing test**

Create `tests/test_extension_isolation.py`:

```python
"""A third-party extension must never be able to change the core verdict."""
import json

import pytest

import clousight_bench.core.orchestrator as orch
from clousight_bench.core.plugin import ResultEnricher
from clousight_bench.core.publish import RECEIPTS_FILE, ResultPublisher
from clousight_bench.core.record import ResultRecord
from clousight_bench.core.schema import RunSpec

_SPEC = RunSpec("agent-runtime", "T1.3", "local-sim",
                target={"recovery": {"mode": "auto-retry"}})


class _Boom(ResultEnricher):
    name = "boom"

    def enrich(self, record: ResultRecord) -> ResultRecord:
        raise RuntimeError("pricing dataset unreadable")


class _Good(ResultEnricher):
    name = "good"

    def enrich(self, record: ResultRecord) -> ResultRecord:
        record.extensions["good"] = {"applied": True}
        return record


def test_a_failing_enricher_does_not_change_the_core_status(monkeypatch, tmp_path):
    monkeypatch.setattr(orch, "load_enrichers", lambda: [_Boom()])
    rec = orch.execute(_SPEC, results_dir=tmp_path)
    assert rec.status == "completed"
    assert rec.measurements["recovery_mode"]["value"] == "auto-retry"
    assert rec.run.stages["ENRICH"] == "failed"
    enrich_errors = [e for e in rec.errors if e["stage"] == "ENRICH"]
    assert len(enrich_errors) == 1
    assert enrich_errors[0]["code"] == "enricher_failed"
    assert enrich_errors[0]["type"] == "RuntimeError"
    assert "boom" in enrich_errors[0]["message"]


def test_a_failing_enricher_does_not_block_the_others(monkeypatch, tmp_path):
    monkeypatch.setattr(orch, "load_enrichers", lambda: [_Boom(), _Good()])
    rec = orch.execute(_SPEC, results_dir=tmp_path)
    assert rec.extensions["good"] == {"applied": True}
    assert rec.run.stages["ENRICH"] == "failed"


def test_an_enricher_returning_the_wrong_type_is_rejected(monkeypatch, tmp_path):
    class _Wrong(ResultEnricher):
        name = "wrong"

        def enrich(self, record):
            return {"not": "a record"}

    monkeypatch.setattr(orch, "load_enrichers", lambda: [_Wrong()])
    rec = orch.execute(_SPEC, results_dir=tmp_path)
    assert isinstance(rec, ResultRecord)
    assert rec.status == "completed"
    assert [e["code"] for e in rec.errors if e["stage"] == "ENRICH"] == ["enricher_failed"]


def test_publish_is_off_unless_a_publisher_is_injected(monkeypatch, tmp_path):
    monkeypatch.setattr(orch, "load_enrichers", list)
    rec = orch.execute(_SPEC, results_dir=tmp_path)
    assert rec.run.stages["PUBLISH"] == "skipped"
    assert not (tmp_path / RECEIPTS_FILE).exists()


def test_a_failing_publisher_writes_a_receipt_and_leaves_the_record_alone(
    monkeypatch, tmp_path
):
    class _BadPublisher(ResultPublisher):
        name = "bad"

        def publish(self, record):
            raise ConnectionError("data service unreachable")

    monkeypatch.setattr(orch, "load_enrichers", list)
    rec = orch.execute(_SPEC, results_dir=tmp_path, publisher=_BadPublisher())

    assert rec.status == "completed"
    assert rec.run.stages["PUBLISH"] == "failed"
    receipts = [
        json.loads(line)
        for line in (tmp_path / RECEIPTS_FILE).read_text(encoding="utf-8").splitlines()
    ]
    assert receipts[-1]["ok"] is False
    assert receipts[-1]["publisher"] == "bad"
    assert receipts[-1]["run_id"] == rec.run.run_id
    assert receipts[-1]["type"] == "ConnectionError"

    persisted = json.loads(
        (tmp_path / "agent-runtime" / "local-sim"
         / f"T1.3-{rec.run.run_id}.json").read_text(encoding="utf-8")
    )
    assert persisted["status"] == "completed"
    assert [e["stage"] for e in persisted["errors"]] == []


def test_a_successful_publisher_writes_an_ok_receipt(monkeypatch, tmp_path):
    class _GoodPublisher(ResultPublisher):
        name = "good"

        def publish(self, record):
            return {"remote_id": "abc"}

    monkeypatch.setattr(orch, "load_enrichers", list)
    rec = orch.execute(_SPEC, results_dir=tmp_path, publisher=_GoodPublisher())
    assert rec.run.stages["PUBLISH"] == "ok"
    receipt = json.loads((tmp_path / RECEIPTS_FILE).read_text(encoding="utf-8").strip())
    assert receipt["ok"] is True
    assert receipt["detail"] == {"remote_id": "abc"}


def test_result_publisher_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        ResultPublisher()
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_extension_isolation.py -v`

Expected: collection FAIL with `ModuleNotFoundError: No module named 'clousight_bench.core.publish'`.

- [ ] **Step 3: Write the publisher boundary**

Create `src/clousight_bench/core/publish.py`:

```python
"""The publishing boundary: a place to send a result, and proof of the attempt.

Phase 1B ships the interface and nothing that implements it. A publisher is
injected explicitly -- it is deliberately not discovered through an entry point,
because entry-point discovery needs the API-range and conflict governance that
belongs to Phase 1D.

PUBLISH runs after PERSIST and can never rewrite the core record. Every attempt
appends one line to an append-only receipt file, so a failed upload is
recoverable evidence rather than a silent gap.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from clousight_bench.core.record import ResultRecord

RECEIPTS_FILE = "publish-receipts.jsonl"


class ResultPublisher(ABC):
    """Send a persisted record somewhere. Must not mutate the record."""

    name: str = "abstract"

    @abstractmethod
    def publish(self, record: ResultRecord) -> dict[str, Any]:
        """Publish and return a non-secret detail dict for the receipt."""


def append_receipt(results_dir: Path, receipt: dict[str, Any]) -> Path:
    """Append one compact JSON line describing a publish attempt."""
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / RECEIPTS_FILE
    line = json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return path
```

- [ ] **Step 4: Isolate enrichment and add publishing in the orchestrator**

In `src/clousight_bench/core/orchestrator.py`, add the imports:

```python
from clousight_bench.core.publish import ResultPublisher, append_receipt
```

add the parameter to `execute` (after `preflight`, before `debug`):

```python
    publisher: ResultPublisher | None = None,
```

pass it through at both `_finish` call sites:

```python
            return _finish(record, results_dir, enrich=False, publisher=publisher, debug=debug)
```

```python
    return _finish(record, results_dir, enrich=enrich, publisher=publisher, debug=debug)
```

and replace `_finish` with:

```python
def _finish(
    record: ResultRecord,
    results_dir: Path,
    enrich: bool,
    publisher: ResultPublisher | None,
    debug: bool,
) -> ResultRecord:
    if enrich:
        record = _enrich(record, results_dir, debug)
    else:
        record.run.stages["ENRICH"] = "skipped"

    record.run.finished_at = utc_now()
    path = ResultStore(results_dir).persist(record)
    logger.info("result -> %s", path)

    _publish(record, results_dir, publisher, debug)
    return record


def _enrich(record: ResultRecord, results_dir: Path, debug: bool) -> ResultRecord:
    """Run every installed enricher in isolation.

    An enricher is third-party code. It may add derived information and nothing
    else: if it raises, or hands back something that is not a ResultRecord, the
    failure is recorded as an ENRICH stage error and the core verdict is kept
    exactly as the scorer produced it."""
    state = "ok"
    for enricher in load_enrichers():
        try:
            enriched = enricher.enrich(record)
            if not isinstance(enriched, ResultRecord):
                raise TypeError(
                    f"enricher {enricher.name!r} returned "
                    f"{type(enriched).__name__}, not a ResultRecord"
                )
            record = enriched
        except Exception as exc:  # noqa: BLE001 - extensions must not break a run
            state = "failed"
            record.errors.append(
                StageError(
                    stage="ENRICH",
                    code="enricher_failed",
                    type=type(exc).__name__,
                    message=f"{enricher.name}: {exc}",
                    retryable=False,
                ).to_dict()
            )
            _log_traceback(results_dir, record.run.run_id, debug, exc)
    record.run.stages["ENRICH"] = state
    return record


def _publish(
    record: ResultRecord,
    results_dir: Path,
    publisher: ResultPublisher | None,
    debug: bool,
) -> None:
    """PUBLISH runs after PERSIST and never rewrites the core record."""
    if publisher is None:
        record.run.stages["PUBLISH"] = "skipped"
        return
    receipt: dict[str, Any] = {
        "run_id": record.run.run_id,
        "publisher": publisher.name,
        "at": utc_now(),
    }
    try:
        detail = publisher.publish(record)
        record.run.stages["PUBLISH"] = "ok"
        receipt.update({"ok": True, "detail": detail})
    except Exception as exc:  # noqa: BLE001 - a failed upload is not a failed benchmark
        record.run.stages["PUBLISH"] = "failed"
        receipt.update(
            {
                "ok": False,
                "code": "publish_failed",
                "type": type(exc).__name__,
                "message": str(exc),
            }
        )
        _log_traceback(results_dir, record.run.run_id, debug, exc)
    append_receipt(results_dir, receipt)
```

`record.run.stages["PUBLISH"]` is written after `persist()` on purpose: the
persisted file reflects the record as it was when it was durable, and the
receipt — not the record — is where a publish outcome lives.

- [ ] **Step 5: Note the receipt file in the CLI docstring**

In `src/clousight_bench/cli.py`, extend the module docstring's `report` line with:

```
    #   results/publish-receipts.jsonl records publish attempts (append-only)
```

- [ ] **Step 6: Run the tests**

Run:

```bash
uv run pytest tests/test_extension_isolation.py tests/test_enricher.py tests/test_lifecycle.py -v
```

Expected: 7 new tests pass; the enricher and lifecycle tests still pass. The
lifecycle test's `_run` helper calls `orch.execute` without a publisher, so
`PUBLISH` is `skipped` there.

- [ ] **Step 7: Run ruff and the full suite**

Run:

```bash
uv run ruff check src tests
uv run pytest -q
```

Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add src/clousight_bench/core/publish.py src/clousight_bench/core/orchestrator.py \
  src/clousight_bench/cli.py tests/test_extension_isolation.py
git commit -s -m "feat: isolate extension failures and add the publisher boundary"
```

---

### Task 14: Deterministic Migration and the `csbench migrate-results` Command

**Files:**
- Create: `src/clousight_bench/core/migrate.py`
- Modify: `src/clousight_bench/cli.py` (`_cmd_migrate`, dispatch table, parser)
- Create: `tests/test_migrate.py`

**Interfaces:**
- Produces: `LEGACY_SCHEMA_VERSION = "1.0"`, `MANIFEST_FILE = "migration-manifest.json"`.
- Produces: `MigrationError(UserInputError)`.
- Produces: `MigrationEntry(source: str, source_sha256: str, output: str | None, status: str, reason: str = "")` with `to_dict()`; `status` is one of `"migrated"`, `"skipped"`, `"failed"`.
- Produces: `MigrationManifest(entries: list[MigrationEntry])` with `migrated`, `skipped`, `failed` int properties and `to_dict()`.
- Produces: `migrate_record(legacy: dict[str, Any], *, source_path: str, source_sha256: str) -> dict[str, Any]`.
- Produces: `migrate_tree(source: Path, dest: Path, *, dry_run: bool = False) -> MigrationManifest`.
- Produces: CLI `csbench migrate-results SOURCE --output DEST [--dry-run]`, exit `0` when nothing failed, `1` when any file failed, `2` on a user input error.

Migration rules, exactly:

| Legacy | Becomes |
|---|---|
| `metrics[k]` | `measurements[k] = {"value": v, "unit": "", "evidence": <legacy evidence_layer>, "notes": "migrated from schema 1.0 metrics"}` |
| `evidence_layer` | the `evidence` of every migrated measurement and finding |
| `ok: true` | `status: "completed"` |
| `ok: false` with `metrics.preflight_ok == false` | `status: "invalid"`, one `StageError(stage="PREFLIGHT", code="legacy_preflight_failed", ...)` |
| `ok: false` otherwise | `status: "failed"`, one `StageError(stage="EXECUTE", code="legacy_error", ...)` |
| `error` | the `message` of that stage error (`"legacy run reported ok=false without an error message"` when absent) |
| `config_hash`, `notes`, `raw`, `ok`, `evidence_layer`, `runner_version` | `extensions.legacy` |
| `raw` | also `observations.legacy_raw` |
| `series`, `artifacts` | copied verbatim |
| every fingerprint | the literal `"unknown"` |
| `record_digest` | computed from the migrated payload |

- [ ] **Step 1: Write the failing test**

Create `tests/test_migrate.py`:

```python
"""Migration is non-destructive, deterministic, idempotent and lossless."""
import hashlib
import json

import pytest

from clousight_bench.cli import main
from clousight_bench.core.migrate import (
    MANIFEST_FILE,
    MigrationError,
    migrate_record,
    migrate_tree,
)

_LEGACY = {
    "domain": "agent-runtime",
    "task_id": "T1.3",
    "platform": "local-sim",
    "run_id": "run-20260101-000000-aaaaaa",
    "started_at": "2026-01-01T00:00:00Z",
    "finished_at": "2026-01-01T00:00:05Z",
    "config_hash": "sha256:0123456789abcdef",
    "evidence_layer": "C",
    "metrics": {"recovery_mode": "auto-retry", "time_to_recovery_ms": 12.5},
    "ok": True,
    "runner_version": "1.0.0",
    "raw": {"attempts": [{"ok": True}]},
    "notes": "fault on call #[3]",
    "schema_version": "1.0",
    "series": {"latency_ms": [[1, 10.0]]},
    "artifacts": [{"kind": "trace", "path": "t.json", "media": "application/json",
                   "sha256": "sha256:aa"}],
    "error": None,
}


def _write(directory, name, payload):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_migrate_record_produces_a_valid_0_2_payload():
    out = migrate_record(_LEGACY, source_path="a/b.json", source_sha256="ff")
    assert out["schema_version"] == "0.2"
    assert out["status"] == "completed"
    assert out["identity"]["domain"] == "agent-runtime"
    assert out["identity"]["adapter"] == "local-sim"
    assert out["identity"]["core_version"] == "1.0.0"
    assert out["run"]["run_id"] == "run-20260101-000000-aaaaaa"


def test_metrics_become_measurements_carrying_the_legacy_evidence_layer():
    out = migrate_record(_LEGACY, source_path="a", source_sha256="ff")
    assert out["measurements"]["recovery_mode"] == {
        "value": "auto-retry", "unit": "", "evidence": "C",
        "notes": "migrated from schema 1.0 metrics",
    }
    assert out["measurements"]["time_to_recovery_ms"]["value"] == 12.5


def test_unknown_fingerprints_are_the_literal_unknown_never_fabricated():
    fingerprints = migrate_record(_LEGACY, source_path="a", source_sha256="ff")["fingerprints"]
    assert fingerprints["benchmark"] == "unknown"
    assert fingerprints["environment"] == "unknown"
    assert fingerprints["implementation"] == "unknown"
    assert fingerprints["record_digest"].startswith("sha256:")


def test_unrecorded_environment_declines_to_guess_local_or_cloud():
    environment = migrate_record(_LEGACY, source_path="a", source_sha256="ff")["environment"]
    assert environment["mode"] == "unknown"
    assert environment["region"] == "unknown"


def test_legacy_only_fields_land_in_extensions_legacy():
    legacy = migrate_record(_LEGACY, source_path="a/b.json", source_sha256="ff")["extensions"]["legacy"]
    assert legacy["config_hash"] == "sha256:0123456789abcdef"
    assert legacy["evidence_layer"] == "C"
    assert legacy["ok"] is True
    assert legacy["notes"] == "fault on call #[3]"
    assert legacy["source_path"] == "a/b.json"
    assert legacy["source_sha256"] == "ff"


def test_raw_series_and_artifacts_are_never_lost():
    out = migrate_record(_LEGACY, source_path="a", source_sha256="ff")
    assert out["observations"]["legacy_raw"] == {"attempts": [{"ok": True}]}
    assert out["series"] == {"latency_ms": [[1, 10.0]]}
    assert out["artifacts"][0]["sha256"] == "sha256:aa"


def test_a_failed_legacy_run_becomes_failed_with_an_execute_error():
    out = migrate_record(
        {**_LEGACY, "ok": False, "error": "ConnectionError: dropped"},
        source_path="a", source_sha256="ff",
    )
    assert out["status"] == "failed"
    assert out["errors"] == [{
        "stage": "EXECUTE", "code": "legacy_error", "type": "LegacyError",
        "message": "ConnectionError: dropped", "retryable": False,
    }]


def test_a_legacy_preflight_abort_becomes_invalid():
    out = migrate_record(
        {**_LEGACY, "ok": False, "error": "preflight failed: credentials",
         "metrics": {"preflight_ok": False}},
        source_path="a", source_sha256="ff",
    )
    assert out["status"] == "invalid"
    assert out["errors"][0]["stage"] == "PREFLIGHT"
    assert out["errors"][0]["code"] == "legacy_preflight_failed"


def test_migrate_tree_refuses_to_write_in_place(tmp_path):
    _write(tmp_path, "agent-runtime/local-sim/T1.3-run-1.json", _LEGACY)
    with pytest.raises(MigrationError, match="in place"):
        migrate_tree(tmp_path, tmp_path)
    with pytest.raises(MigrationError, match="inside"):
        migrate_tree(tmp_path, tmp_path / "out")


def test_migrate_tree_preserves_the_relative_layout_and_writes_a_manifest(tmp_path):
    source = tmp_path / "old"
    dest = tmp_path / "new"
    legacy_path = _write(source, "agent-runtime/local-sim/T1.3-run-1.json", _LEGACY)
    manifest = migrate_tree(source, dest)

    migrated = dest / "agent-runtime" / "local-sim" / "T1.3-run-1.json"
    assert migrated.is_file()
    assert json.loads(migrated.read_text())["schema_version"] == "0.2"
    assert legacy_path.read_text() == json.dumps(_LEGACY)  # source untouched

    assert manifest.migrated == 1 and manifest.skipped == 0 and manifest.failed == 0
    written = json.loads((dest / MANIFEST_FILE).read_text(encoding="utf-8"))
    entry = written["entries"][0]
    assert entry["source"] == "agent-runtime/local-sim/T1.3-run-1.json"
    assert entry["source_sha256"] == _sha(legacy_path)
    assert entry["status"] == "migrated"


def test_migration_is_idempotent_byte_for_byte(tmp_path):
    source = tmp_path / "old"
    _write(source, "a/T1.3-run-1.json", _LEGACY)
    first = tmp_path / "n1"
    second = tmp_path / "n2"
    migrate_tree(source, first)
    migrate_tree(source, second)
    assert (first / "a/T1.3-run-1.json").read_bytes() == (
        second / "a/T1.3-run-1.json"
    ).read_bytes()


def test_already_migrated_and_unparseable_files_are_reported_not_crashed(tmp_path):
    source = tmp_path / "old"
    dest = tmp_path / "new"
    _write(source, "already.json", {"schema_version": "0.2"})
    (source / "broken.json").write_text("{not json", encoding="utf-8")
    manifest = migrate_tree(source, dest)
    statuses = {e.source: e.status for e in manifest.entries}
    assert statuses == {"already.json": "skipped", "broken.json": "failed"}
    assert manifest.migrated == 0 and manifest.skipped == 1 and manifest.failed == 1


def test_dry_run_writes_nothing(tmp_path):
    source = tmp_path / "old"
    dest = tmp_path / "new"
    _write(source, "a/T1.3-run-1.json", _LEGACY)
    manifest = migrate_tree(source, dest, dry_run=True)
    assert manifest.migrated == 1
    assert not dest.exists()


def test_cli_migrate_results_reports_and_exits_zero(tmp_path, capsys):
    source = tmp_path / "old"
    dest = tmp_path / "new"
    _write(source, "a/T1.3-run-1.json", _LEGACY)
    rc = main(["migrate-results", str(source), "--output", str(dest)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "migrated=1" in out
    assert str(dest / MANIFEST_FILE) in out


def test_cli_migrate_results_exits_one_when_a_file_fails(tmp_path):
    source = tmp_path / "old"
    (source).mkdir()
    (source / "broken.json").write_text("{not json", encoding="utf-8")
    assert main(["migrate-results", str(source), "--output", str(tmp_path / "new")]) == 1


def test_cli_migrate_results_rejects_an_in_place_target(tmp_path, capsys):
    (tmp_path / "r").mkdir()
    rc = main(["migrate-results", str(tmp_path / "r"), "--output", str(tmp_path / "r")])
    assert rc == 2
    assert "in place" in capsys.readouterr().err
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_migrate.py -v`

Expected: collection FAIL with `ModuleNotFoundError: No module named 'clousight_bench.core.migrate'`.

- [ ] **Step 3: Write the migrator**

Create `src/clousight_bench/core/migrate.py`:

```python
"""Deterministic, non-destructive migration from ResultRecord 1.0 to 0.2.

Three rules make old numbers safe to keep:

1. **Never in place.** The source tree is read-only for this tool, and every
   entry records the original path and its SHA-256, so a migrated record can
   always be traced back to the bytes it came from.
2. **Never invent.** A 1.0 record cannot know its benchmark, environment or
   implementation fingerprint, so those are the literal string ``unknown``
   rather than a plausible-looking hash.
3. **Never lose.** Metric values, series, artifact pointers, the raw blob, the
   old config hash and the old notes all survive -- as measurements,
   observations or ``extensions.legacy``.

Migrating the same input twice produces byte-identical output.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clousight_bench.core.errors import UserInputError
from clousight_bench.core.fingerprints import UNKNOWN, record_digest
from clousight_bench.core.record import SCHEMA_VERSION

LEGACY_SCHEMA_VERSION = "1.0"
MANIFEST_FILE = "migration-manifest.json"
_MEASUREMENT_NOTE = "migrated from schema 1.0 metrics"


class MigrationError(UserInputError):
    """The migration request itself cannot be honoured."""


@dataclass
class MigrationEntry:
    source: str
    source_sha256: str
    output: str | None
    status: str  # "migrated" | "skipped" | "failed"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_sha256": self.source_sha256,
            "output": self.output,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass
class MigrationManifest:
    entries: list[MigrationEntry] = field(default_factory=list)

    def _count(self, status: str) -> int:
        return sum(1 for e in self.entries if e.status == status)

    @property
    def migrated(self) -> int:
        return self._count("migrated")

    @property
    def skipped(self) -> int:
        return self._count("skipped")

    @property
    def failed(self) -> int:
        return self._count("failed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "migrated": self.migrated,
            "skipped": self.skipped,
            "failed": self.failed,
            "entries": [e.to_dict() for e in self.entries],
        }


def _status_and_errors(legacy: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    if legacy.get("ok", True):
        return "completed", []
    message = str(
        legacy.get("error")
        or "legacy run reported ok=false without an error message"
    )
    metrics = legacy.get("metrics") or {}
    if metrics.get("preflight_ok") is False:
        return "invalid", [
            {
                "stage": "PREFLIGHT",
                "code": "legacy_preflight_failed",
                "type": "LegacyError",
                "message": message,
                "retryable": False,
            }
        ]
    return "failed", [
        {
            "stage": "EXECUTE",
            "code": "legacy_error",
            "type": "LegacyError",
            "message": message,
            "retryable": False,
        }
    ]


def migrate_record(
    legacy: dict[str, Any], *, source_path: str, source_sha256: str
) -> dict[str, Any]:
    """Convert one parsed schema 1.0 record into a schema 0.2 payload."""
    version = str(legacy.get("schema_version", LEGACY_SCHEMA_VERSION))
    if version != LEGACY_SCHEMA_VERSION:
        raise MigrationError(
            f"{source_path}: expected schema_version {LEGACY_SCHEMA_VERSION!r}, "
            f"got {version!r}"
        )
    evidence = str(legacy.get("evidence_layer", "C"))
    status, errors = _status_and_errors(legacy)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run": {
            "run_id": str(legacy.get("run_id", "")),
            "started_at": str(legacy.get("started_at", "")),
            "finished_at": str(legacy.get("finished_at", "")),
            "stages": {},
        },
        "identity": {
            "domain": str(legacy.get("domain", "")),
            "task_id": str(legacy.get("task_id", "")),
            "task_revision": UNKNOWN,
            "scorer_revision": UNKNOWN,
            "adapter": str(legacy.get("platform", "")),
            "adapter_status": UNKNOWN,
            "core_version": str(legacy.get("runner_version", UNKNOWN)),
            "workload": "",
            "workload_version": "",
            "plugin_versions": {},
        },
        "environment": {
            "region": UNKNOWN,
            "mode": "unknown",
            "python_version": UNKNOWN,
            "os_name": UNKNOWN,
            "facts": {},
        },
        "fingerprints": {
            "benchmark": UNKNOWN,
            "environment": UNKNOWN,
            "implementation": UNKNOWN,
            "record_digest": "",
        },
        "measurements": {
            name: {
                "value": value,
                "unit": "",
                "evidence": evidence,
                "notes": _MEASUREMENT_NOTE,
            }
            for name, value in sorted((legacy.get("metrics") or {}).items())
        },
        "findings": [],
        "observations": {"legacy_raw": legacy.get("raw") or {}},
        "series": legacy.get("series") or {},
        "artifacts": legacy.get("artifacts") or [],
        "extensions": {
            "legacy": {
                "config_hash": legacy.get("config_hash", ""),
                "evidence_layer": evidence,
                "ok": legacy.get("ok", True),
                "notes": legacy.get("notes", ""),
                "runner_version": legacy.get("runner_version", ""),
                "source_path": source_path,
                "source_sha256": source_sha256,
            }
        },
        "errors": errors,
        "status": status,
    }
    payload["fingerprints"]["record_digest"] = record_digest(payload)
    return payload


def migrate_tree(
    source: Path, dest: Path, *, dry_run: bool = False
) -> MigrationManifest:
    """Migrate every JSON file under ``source`` into ``dest``, never in place."""
    source = Path(source).resolve()
    dest = Path(dest).resolve()
    if not source.is_dir():
        raise MigrationError(f"source is not a directory: {source}")
    if dest == source:
        raise MigrationError(
            f"refusing to migrate in place: choose an --output outside {source}"
        )
    if source in dest.parents:
        raise MigrationError(
            f"refusing to write inside the source tree: {dest} is under {source}"
        )

    manifest = MigrationManifest()
    for path in sorted(source.rglob("*.json")):
        relative = path.relative_to(source).as_posix()
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        try:
            legacy = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(legacy, dict):
                raise MigrationError("record root must be an object")
            if str(legacy.get("schema_version", "")) == SCHEMA_VERSION:
                manifest.entries.append(
                    MigrationEntry(relative, sha, None, "skipped",
                                   f"already schema {SCHEMA_VERSION}")
                )
                continue
            payload = migrate_record(legacy, source_path=relative, source_sha256=sha)
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the batch
            manifest.entries.append(
                MigrationEntry(relative, sha, None, "failed",
                               f"{type(exc).__name__}: {exc}")
            )
            continue
        if not dry_run:
            out_path = dest / relative
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        manifest.entries.append(MigrationEntry(relative, sha, relative, "migrated"))

    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / MANIFEST_FILE).write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    return manifest
```

- [ ] **Step 4: Add the CLI command**

In `src/clousight_bench/cli.py`, add the handler:

```python
def _cmd_migrate(args: argparse.Namespace) -> int:
    from clousight_bench.core.migrate import MANIFEST_FILE, migrate_tree

    dest = Path(args.output)
    manifest = migrate_tree(Path(args.source), dest, dry_run=args.dry_run)
    prefix = "dry-run: " if args.dry_run else ""
    print(
        f"{prefix}migrated={manifest.migrated} "
        f"skipped={manifest.skipped} failed={manifest.failed}"
    )
    for entry in manifest.entries:
        if entry.status != "migrated":
            print(f"  {entry.status}: {entry.source} — {entry.reason}")
    if not args.dry_run:
        print(f"manifest: {dest.resolve() / MANIFEST_FILE}")
    return 1 if manifest.failed else 0
```

register it in `_dispatch`:

```python
        "migrate-results": _cmd_migrate,
```

and add the parser next to the others:

```python
    mig_p = sub.add_parser(
        "migrate-results",
        help="convert schema 1.0 result files into schema 0.2 (never in place)",
    )
    mig_p.add_argument("source", help="directory containing schema 1.0 result JSON")
    mig_p.add_argument("--output", required=True,
                       help="destination directory; must be outside SOURCE")
    mig_p.add_argument("--dry-run", action="store_true",
                       help="report what would be migrated without writing anything")
```

Finally, add this line to the module docstring's command list:

```
    csbench migrate-results old-results/ --output new-results/ [--dry-run]
```

`migrate_tree` raises `MigrationError`, a `UserInputError`, so the existing
`main()` handler already turns an in-place request into exit code 2.

- [ ] **Step 5: Run the migration tests**

Run: `uv run pytest tests/test_migrate.py -v`

Expected: 16 passed.

- [ ] **Step 6: Migrate a real legacy fixture end to end**

Run:

```bash
rm -rf /tmp/csbench-legacy /tmp/csbench-migrated
mkdir -p /tmp/csbench-legacy/agent-runtime/local-sim
cat > /tmp/csbench-legacy/agent-runtime/local-sim/T1.3-run-legacy.json <<'JSON'
{
  "domain": "agent-runtime",
  "task_id": "T1.3",
  "platform": "local-sim",
  "run_id": "run-legacy",
  "started_at": "2026-01-01T00:00:00Z",
  "finished_at": "2026-01-01T00:00:05Z",
  "config_hash": "sha256:deadbeefdeadbeef",
  "evidence_layer": "C",
  "metrics": {"recovery_mode": "fail-fast", "total_attempts": 3},
  "ok": true,
  "runner_version": "1.0.0",
  "raw": {"attempts": []},
  "notes": "legacy",
  "schema_version": "1.0",
  "series": {},
  "artifacts": [],
  "error": null
}
JSON
uv run csbench migrate-results /tmp/csbench-legacy --output /tmp/csbench-migrated
uv run csbench report --results /tmp/csbench-migrated
uv run python -c "
import json, pathlib
p = pathlib.Path('/tmp/csbench-migrated/agent-runtime/local-sim/T1.3-run-legacy.json')
d = json.loads(p.read_text())
assert d['schema_version'] == '0.2'
assert d['fingerprints']['benchmark'] == 'unknown'
assert d['extensions']['legacy']['config_hash'] == 'sha256:deadbeefdeadbeef'
assert d['measurements']['total_attempts']['value'] == 3
print('legacy record migrated and readable by the report layer')
"
```

Expected: `migrated=1 skipped=0 failed=0`, the report renders the migrated cell,
and the verifier prints its confirmation line.

- [ ] **Step 7: Run ruff and the full suite**

Run:

```bash
uv run ruff check src tests
uv run pytest -q
```

Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add src/clousight_bench/core/migrate.py src/clousight_bench/cli.py tests/test_migrate.py
git commit -s -m "feat: migrate schema 1.0 results into 0.2 deterministically"
```

---

### Task 15: Verify the Pro Contract Against the Phase 1B Core Head

**Repository:** `/Users/bowang/IdeaProjects/clousight-bench-pro`

**Files:**
- Modify: `packages/cb-pricing/src/cb_pricing/enricher.py`
- Modify: `packages/cb-pricing/tests/test_pricing_enricher.py`

**Interfaces:**
- Consumes: the public Core `0.2` surface only — `clousight_bench.core.plugin.ResultEnricher`, `clousight_bench.core.schema.ResultRecord`, `clousight_bench.core.record.{Environment, Fingerprints, Identity, RunInfo}`. **No underscore-private Core module may be imported.**
- Produces: `PricingEnricher.enrich(record)` writing only `record.extensions["cb-pricing"] = {"cost_usd": float, "breakdown": list[dict], "uncovered": list[str]}` and returning the same record.
- Compatibility only: no new commercial capability, no new package, no new entry point.

- [ ] **Step 1: Point the Pro workspace at the Phase 1B Core branch**

The Pro worktree resolves `../clousight-bench` through the symlink
`/Users/bowang/IdeaProjects/clousight-bench-pro/.worktrees/clousight-bench`.
Repoint it at the Phase 1B worktree and create the Pro compatibility branch:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro
ln -sfn /Users/bowang/IdeaProjects/clousight-bench/.worktrees/phase1b-trusted-result-contract \
  .worktrees/clousight-bench
readlink .worktrees/clousight-bench
git fetch origin
git worktree add .worktrees/phase1b-contract-compat -b feat/phase1b-contract-compat origin/main
```

Expected: `readlink` prints the Phase 1B Core worktree path, and the new Pro
worktree exists on branch `feat/phase1b-contract-compat`.

Note that `.worktrees/phase1b-contract-compat/../clousight-bench` is the same
symlink you just repointed, so `uv` resolves the workspace against Phase 1B Core.

- [ ] **Step 2: Prove the current Pro enricher breaks against the 0.2 core**

Run:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro/.worktrees/phase1b-contract-compat
uv sync --all-packages --all-extras
uv run pytest packages/cb-pricing -q
```

Expected: FAIL — `test_pricing_enricher.py` constructs a legacy `ResultRecord`
with `domain=`/`metrics=`/`evidence_layer=` keyword arguments that no longer
exist, so collection or construction raises `TypeError`.

- [ ] **Step 3: Rewrite the Pro enricher test against the 0.2 record**

Replace `packages/cb-pricing/tests/test_pricing_enricher.py` with:

```python
from cb_pricing.enricher import PricingEnricher
from clousight_bench.core.record import (
    Environment,
    Fingerprints,
    Identity,
    ResultRecord,
    RunInfo,
)
from clousight_bench.core.schema import utc_now


def _rec(adapter, measurements, region=""):
    return ResultRecord(
        run=RunInfo(run_id="r", started_at=utc_now(), finished_at=utc_now()),
        identity=Identity(domain="agent-runtime", task_id="T1.3", task_revision="2",
                          scorer_revision="2", adapter=adapter,
                          adapter_status="reference", core_version="0.2.0"),
        environment=Environment(region=region, mode="cloud", python_version="3.12.0",
                                os_name="Linux"),
        fingerprints=Fingerprints(benchmark="sha256:a", environment="sha256:b",
                                  implementation="sha256:c"),
        status="completed",
        measurements=measurements,
    )


def _usage(unit, value, service="agent-runtime"):
    return {
        unit: {"value": value, "unit": unit, "evidence": "C"},
        "service": {"value": service, "unit": "", "evidence": "A"},
    }


def test_cost_computed_from_vcpu_hours():
    rec = _rec("aws", _usage("vcpu_hours", 10), region="us-east-1")
    pricing = PricingEnricher().enrich(rec).extensions["cb-pricing"]
    assert pricing["cost_usd"] == round(10 * 0.0895, 6)
    assert pricing["breakdown"][0]["unit"] == "vcpu_hours"
    assert pricing["breakdown"][0]["qty"] == 10
    assert pricing["breakdown"][0]["unit_price"] == 0.0895


def test_uncovered_usage_is_reported_but_does_not_crash():
    rec = _rec("unknown-cloud", _usage("vcpu_hours", 5))
    pricing = PricingEnricher().enrich(rec).extensions["cb-pricing"]
    assert pricing["cost_usd"] == 0.0
    assert pricing["uncovered"] == ["vcpu_hours"]


def test_non_numeric_qty_raises_clear_error():
    import pytest

    rec = _rec("aws", _usage("vcpu_hours", "ten"), region="us-east-1")
    with pytest.raises(TypeError, match="must be a number"):
        PricingEnricher().enrich(rec)


def test_bool_qty_rejected():
    import pytest

    rec = _rec("aws", _usage("vcpu_hours", True), region="us-east-1")
    with pytest.raises(TypeError, match="must be a number"):
        PricingEnricher().enrich(rec)


def test_enricher_only_writes_its_own_extension_namespace():
    rec = _rec("aws", _usage("vcpu_hours", 10), region="us-east-1")
    out = PricingEnricher().enrich(rec)
    assert out.status == "completed"
    assert set(out.extensions) == {"cb-pricing"}
    assert out.measurements == rec.measurements
    assert out.findings == []


def test_enricher_name():
    assert PricingEnricher().name == "cb-pricing"


def test_enricher_uses_only_public_core_modules():
    import inspect

    import cb_pricing.enricher as module

    source = inspect.getsource(module)
    assert "clousight_bench.core._" not in source
    assert "from clousight_bench.core.assets import _" not in source
```

- [ ] **Step 4: Run the test and confirm it fails against the current enricher**

Run:

```bash
uv run pytest packages/cb-pricing -q
```

Expected: FAIL — `PricingEnricher.enrich` still reads `record.platform` and
`record.metrics`, so it raises `AttributeError`.

- [ ] **Step 5: Rewrite the Pro enricher against the 0.2 record**

Replace `packages/cb-pricing/src/cb_pricing/enricher.py` with:

```python
"""PricingEnricher: turn resource-usage measurements into a cost estimate.

Proprietary. Reads the pinned pricing dataset (data/pricing.json) and multiplies
declared usage measurements by unit prices. Never invents numbers: usage it
cannot price is listed under ``uncovered`` and excluded from ``cost_usd``.

Enrichment is additive and namespaced: everything this plugin produces lives
under ``record.extensions["cb-pricing"]``. It never touches ``status``,
``measurements``, ``findings`` or ``errors``, because a commercial plugin must
not be able to change the open core's verdict.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clousight_bench.core.plugin import ResultEnricher
from clousight_bench.core.schema import ResultRecord

_DATA = Path(__file__).parent / "data" / "pricing.json"
_UNITS = ("vcpu_hours", "tokens_1k", "gb_month")


class PricingEnricher(ResultEnricher):
    name = "cb-pricing"

    def __init__(self) -> None:
        self._prices: list[dict[str, Any]] = json.loads(
            _DATA.read_text(encoding="utf-8")
        )["prices"]

    def _lookup(
        self, provider: str, service: str, unit: str, region: str | None
    ) -> dict | None:
        matches = [
            p
            for p in self._prices
            if p["provider"] == provider
            and p["service"] == service
            and p["unit"] == unit
            and (not region or p["region"] == region)
        ]
        return matches[0] if matches else None

    @staticmethod
    def _measurement_value(record: ResultRecord, name: str) -> Any:
        entry = record.measurements.get(name)
        return entry.get("value") if isinstance(entry, dict) else None

    def enrich(self, record: ResultRecord) -> ResultRecord:
        provider = record.identity.adapter.split("-")[0]
        service = str(
            self._measurement_value(record, "service") or record.identity.domain
        )
        region = record.environment.region
        breakdown: list[dict[str, Any]] = []
        uncovered: list[str] = []
        total = 0.0
        for unit in _UNITS:
            qty = self._measurement_value(record, unit)
            if qty is None:
                continue
            if isinstance(qty, bool) or not isinstance(qty, (int, float)):
                raise TypeError(
                    f"pricing: usage measurement {unit!r} must be a number, "
                    f"got {type(qty).__name__}: {qty!r}"
                )
            price = self._lookup(provider, service, unit, region)
            if price is None:
                uncovered.append(unit)
                continue
            subtotal = round(qty * price["price"], 6)
            total += subtotal
            breakdown.append(
                {
                    "unit": unit,
                    "qty": qty,
                    "unit_price": price["price"],
                    "subtotal": subtotal,
                    "region": price["region"],
                    "price_source": price["source"],
                }
            )
        record.extensions["cb-pricing"] = {
            "cost_usd": round(total, 6),
            "breakdown": breakdown,
            "uncovered": uncovered,
        }
        return record
```

- [ ] **Step 6: Run the full Pro suite against the Phase 1B Core**

Run:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro/.worktrees/phase1b-contract-compat
uv sync --all-packages --all-extras
uv run ruff check packages
uv run pytest -q
```

Expected: ruff passes and every Pro test passes, including the resolver, rollup
and sampler tests that were never coupled to the record shape.

- [ ] **Step 7: Prove the Pro enricher works inside a real Core run**

Run:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro/.worktrees/phase1b-contract-compat
rm -rf /tmp/csbench-pro-contract
uv run python - <<'PY'
from pathlib import Path

from clousight_bench.core.orchestrator import execute
from clousight_bench.core.schema import RunSpec

record = execute(
    RunSpec("agent-runtime", "T1.3", "local-sim",
            target={"recovery": {"mode": "auto-retry"}}),
    results_dir=Path("/tmp/csbench-pro-contract"),
)
assert record.schema_version == "0.2", record.schema_version
assert record.status == "completed", record.status
assert "cb-pricing" in record.extensions, sorted(record.extensions)
assert record.run.stages["ENRICH"] == "ok", record.run.stages
assert record.errors == [], record.errors
print("pro enricher ran inside the 0.2 lifecycle:", record.extensions["cb-pricing"])
PY
```

Expected: the installed `cb-pricing` entry point is discovered by the Core
registry, `ENRICH` is `ok`, and the printed extension carries `cost_usd`,
`breakdown` and `uncovered`.

- [ ] **Step 8: Commit in the Pro repository, without pushing**

```bash
git add packages/cb-pricing/src/cb_pricing/enricher.py \
  packages/cb-pricing/tests/test_pricing_enricher.py
git commit -s -m "fix: adapt the pricing enricher to ResultRecord 0.2"
```

Do not push or open the Pro pull request yet: it must target the merged Core
`main`, which only exists after Task 16's Core PR lands. Task 16 records the
push and merge order.

**Rollback:** `git revert --signoff "$(git rev-parse HEAD)"` in the Pro worktree immediately after Step 8, and restore the
Core symlink with

```bash
ln -sfn /Users/bowang/IdeaProjects/clousight-bench/.worktrees/phase1a-release-baseline \
  /Users/bowang/IdeaProjects/clousight-bench-pro/.worktrees/clousight-bench
```

---

### Task 16: Document the 0.2 Contract, Extend CI, and Land Both Repositories

**Repository:** `/Users/bowang/IdeaProjects/clousight-bench/.worktrees/phase1b-trusted-result-contract`, then `/Users/bowang/IdeaProjects/clousight-bench-pro/.worktrees/phase1b-contract-compat`

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `CONTRIBUTING.md`
- Modify: `docs/architecture.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: everything built in Tasks 1–15.
- Produces: CI proof that a fresh install emits schema `0.2` and can migrate a schema `1.0` tree, and documentation that matches the shipped contract.

- [ ] **Step 1: Prove CI does not check the schema yet**

Run:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench/.worktrees/phase1b-trusted-result-contract
rg -n "schema_version|migrate-results" .github/workflows/ci.yml ; echo "exit=$?"
```

Expected: no matches and `exit=1`.

- [ ] **Step 2: Add the schema and migration checks to CI**

In `.github/workflows/ci.yml`, append these two steps to the `test` job, after
`Local baseline smoke (no cloud account)`:

```yaml
      - name: Assert every emitted record is schema 0.2
        run: |
          python - <<'PY'
          import json, pathlib
          paths = [p for p in pathlib.Path("results").rglob("*.json")
                   if p.name not in {"comparison.json", "migration-manifest.json"}]
          assert paths, "the smoke wrote no records"
          for path in paths:
              data = json.loads(path.read_text(encoding="utf-8"))
              assert data["schema_version"] == "0.2", (path, data["schema_version"])
              assert data["status"] in {"completed", "unsupported"}, (path, data["status"])
              assert data["fingerprints"]["record_digest"].startswith("sha256:"), path
              for gone in ("ok", "metrics", "evidence_layer", "config_hash"):
                  assert gone not in data, (path, gone)
          print(f"{len(paths)} records verified at schema 0.2")
          PY

      - name: Migration round-trip from schema 1.0
        run: |
          rm -rf /tmp/legacy /tmp/migrated
          mkdir -p /tmp/legacy/agent-runtime/local-sim
          cat > /tmp/legacy/agent-runtime/local-sim/T1.3-run-legacy.json <<'JSON'
          {"domain":"agent-runtime","task_id":"T1.3","platform":"local-sim",
           "run_id":"run-legacy","started_at":"2026-01-01T00:00:00Z",
           "finished_at":"2026-01-01T00:00:05Z","config_hash":"sha256:dead",
           "evidence_layer":"C","metrics":{"recovery_mode":"fail-fast"},
           "ok":true,"runner_version":"1.0.0","raw":{},"notes":"legacy",
           "schema_version":"1.0","series":{},"artifacts":[],"error":null}
          JSON
          csbench migrate-results /tmp/legacy --output /tmp/migrated
          csbench migrate-results /tmp/legacy --output /tmp/migrated-again
          diff -r /tmp/migrated /tmp/migrated-again
          csbench report --results /tmp/migrated
          python - <<'PY'
          import json, pathlib
          data = json.loads(pathlib.Path(
              "/tmp/migrated/agent-runtime/local-sim/T1.3-run-legacy.json"
          ).read_text(encoding="utf-8"))
          assert data["schema_version"] == "0.2"
          assert data["fingerprints"]["benchmark"] == "unknown"
          assert data["extensions"]["legacy"]["config_hash"] == "sha256:dead"
          print("migration is deterministic and idempotent")
          PY
```

In the `wheel-smoke` job, extend the existing inline verification command by
adding this assertion to the `python -c` string, immediately after the
`cb.__version__` assertion:

```
assert cb.RESULT_SCHEMA_VERSION == '0.2', cb.RESULT_SCHEMA_VERSION;
```

- [ ] **Step 3: Document the contract in `README.md`**

Replace the paragraph beginning "Every result record carries `config_hash` +
`runner_version` + `evidence_layer`" with:

```markdown
Every result record is schema `0.2` and is attributable on three independent
axes, so you can tell whether two numbers are even comparable:

| Field | Answers |
|---|---|
| `fingerprints.benchmark` | *what* was measured — task, scorer, workload, assets, controlled params |
| `fingerprints.environment` | *where* — region, mode and the environment facts the task declares |
| `fingerprints.implementation` | *which code* — core, domain pack, adapter and installed plugins |
| `fingerprints.record_digest` | the content digest of the record itself |

Each measurement carries its own `value`, `unit` and `evidence` layer, and each
finding carries a stable `code`, a `severity` and its evidence. A run ends in
exactly one `status`: `completed`, `failed`, `invalid` or `unsupported` — there
is no boolean `ok`, because "the platform does not support this" and "the run
crashed" are different results. We publish **per-dimension results, never a
single blended score** — blended agent-benchmark rankings have near-zero
cross-benchmark agreement.

Results written by an older version use schema `1.0`. Convert them with:

```bash
csbench migrate-results old-results/ --output new-results/
```

The migrator never writes in place, never fabricates a fingerprint (unknown
ones are the literal string `unknown`), and produces byte-identical output when
run twice.
```

- [ ] **Step 4: Document the Task contract in `CONTRIBUTING.md`**

Replace the "A **dimension**" row of the extension table with:

```markdown
| A **dimension** | one `Task` subclass with `config()` (the controlled inputs), `execute()` (raw observation only), `score()` (a pure function of the bundle), `task_revision` / `scorer_revision`, and optionally `environment_facts()` and `workload_identity()`. |
```

and replace the "Reproducibility rules (non-negotiable)" list with:

```markdown
## Reproducibility rules (non-negotiable)

- `execute()` may talk to the cloud and must return raw, replayable evidence
  only. Never put a verdict in an `ObservationBundle`.
- `score()` is a pure function of the bundle: no credentials, no network, no
  resource creation, no mutation of the bundle. A stored observation must be
  re-scorable years later.
- Bump `scorer_revision` when scoring changes and `task_revision` when the
  observation procedure changes. Both feed `fingerprints.benchmark`, which is
  what keeps a published number attributable.
- Put everything that determines a number into `Task.config()`.
- Never put a secret, hostname, username or raw environment variable into a
  `RunSpec`, an observation, an environment fact or a finding. Reference
  credentials by env-var name; `ResultStore` refuses to persist a record that
  contains this machine's identity.
- Every `Measurement` needs a `value`, a `unit` and an `evidence` layer; every
  `Finding` needs a stable `code`, a `severity` and its evidence.
- Report per-dimension; never emit a blended cross-dimension score.
- An adapter's `teardown()` must be idempotent: the lifecycle calls it whenever
  `setup()` was entered, including when `setup()` itself failed half-way.
```

- [ ] **Step 5: Replace the lifecycle section of `docs/architecture.md`**

Replace the `## Lifecycle (shared by every domain)` section and the
`## 0.2 Developer Preview readiness` closing paragraph with:

```markdown
## Lifecycle (shared by every domain)

```
RESOLVE -> VALIDATE -> PREFLIGHT -> SETUP -> EXECUTE -> COLLECT
        -> SCORE -> ENRICH -> PERSIST -> optional PUBLISH
```

- **RESOLVE** — look up the DomainPack, Task and Adapter for a `RunSpec`.
- **VALIDATE** — parse and check the RunSpec, target, params and task config
  (`core/validation.py`). RESOLVE and VALIDATE failures are `UserInputError`s:
  CLI exit code 2, **no record written**.
- **PREFLIGHT** — `adapter.preflight(task)`: credentials, SDK, connectivity and
  the minimal permissions this benchmark needs on this cloud, **before**
  provisioning. A CRITICAL failure produces an `invalid` record and never
  enters SETUP. Bypass with `--skip-preflight`; run standalone with
  `csbench doctor`.
- **SETUP** — `adapter.setup()`: provision (Terraform) or connect (SDK/HTTP).
- **EXECUTE** — `task.execute(adapter, params)`: drive the workload, return an
  `ObservationBundle` of raw evidence only.
- **COLLECT** — `core/observation.py::collect()`: prove the bundle is well
  formed and canonically encodable.
- **SCORE** — `task.score(bundle)`: a pure function producing measurements and
  findings. A scorer failure keeps the observations and records a SCORE error.
- **ENRICH** — installed `ResultEnricher`s, each isolated: a failure becomes an
  ENRICH stage error and never changes `status`.
- **PERSIST** — `core/store.py`: temp file, flush, `fsync`, atomic rename, plus
  an emergency dump into the system temp directory (path printed) when the
  results directory cannot be written.
- **PUBLISH** — off unless a `ResultPublisher` is injected. Runs after PERSIST
  and writes an append-only receipt; it can never rewrite the core record.

**TEARDOWN is not a step in that line.** It is the mandatory `finally` boundary
around SETUP → COLLECT: once SETUP is entered, `adapter.teardown()` always runs,
including when `setup()` failed half-way, and a teardown failure is recorded as
its own stage error without overwriting the execute or collect error that
caused it.

## Result contract (schema 0.2)

Top-level fields are fixed: `schema_version`, `run`, `identity`, `environment`,
`fingerprints`, `measurements`, `findings`, `observations`, `series`,
`artifacts`, `extensions`, `errors`, `status`.

- `status` ∈ `completed` · `failed` · `invalid` · `unsupported`. There is no
  boolean `ok`, no top-level `metrics`, no top-level `evidence_layer` and no
  `config_hash`.
- Every measurement carries `value`, `unit` and `evidence`, optionally
  `aggregation`, `sample_count` and `notes`.
- Every finding carries a stable `code`, a `severity`, a `summary`, its
  `evidence` and `details`.
- Every stage error carries `stage`, `code`, `type`, `message` and `retryable`.
  Tracebacks are never stored in a record; `csbench run --debug` writes them to
  `<results>/debug/<run_id>.log`.
- All digests are full SHA-256 over the canonical JSON encoding
  (`core/canonical.py`): UTF-8, sorted keys, no insignificant whitespace,
  NaN/Infinity rejected.
- `extensions["core"]` is reserved for the core; a plugin writes under its own
  name (for example `extensions["cb-pricing"]`).

Old schema `1.0` files are converted with
`csbench migrate-results SOURCE --output DEST [--dry-run]`: never in place,
never fabricating a fingerprint (unknown ones are the literal `unknown`), and
byte-identical on a repeat run. Each entry in `migration-manifest.json` records
the original path and its SHA-256.

Phase 1B does **not** ship run plans, repeats, statistics or comparability
reports (Phase 1C), nor plugin API ranges, JSON Schema, a conformance kit or
workload sandboxing (Phase 1D).
```

- [ ] **Step 6: Record the change in `CHANGELOG.md`**

In the `## 0.2.0 — Unreleased` section, add these subsections above the existing
`### Compatibility`:

```markdown
### Added

- Schema `0.2` result contract with `identity`, `environment`, `fingerprints`,
  `measurements`, `findings`, `observations`, `errors` and a four-value
  `status`.
- Deterministic `benchmark`, `environment` and `implementation` fingerprints
  plus a `record_digest`, all full SHA-256 over a canonical JSON encoding.
- `Task.execute()` / `Task.score()`, so a stored observation can be re-scored
  without re-running the benchmark.
- Atomic result persistence with an emergency dump into the system temp
  directory when the results directory cannot be written.
- `csbench migrate-results SOURCE --output DEST [--dry-run]` and
  `csbench run --debug`.
- A minimal `ResultPublisher` boundary with append-only publish receipts. Core
  ships no publisher.

### Changed (breaking)

- `ResultRecord` moved from schema `1.0` to `0.2`. `ok`, top-level `metrics`,
  top-level `evidence_layer` and `config_hash` are gone; migrate old files with
  `csbench migrate-results`.
- `Task.run()` and `TaskOutput` are removed. Implement `execute()` and
  `score()`.
- `clousight_bench.core.schema.config_hash` and
  `clousight_bench.core.schema.EVIDENCE_LAYERS` are removed; fingerprints and
  `core.observation.EVIDENCE_LAYERS` replace them.
- `csbench run` exit codes: `0` for `completed` and `unsupported`, `1` for
  `failed` and `invalid`, `2` for a user input error. A failed run used to exit
  `2`.
- An enricher failure is now isolated: it records an ENRICH stage error and
  leaves `status` alone instead of aborting the run.
```

- [ ] **Step 7: Run the whole gate locally**

Run:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench/.worktrees/phase1b-trusted-result-contract
uv run ruff check src tests
uv run pytest -q
rm -rf /tmp/csbench-phase1b-final /tmp/csbench-phase1b-venv /tmp/csbench-phase1b-results
uv build --out-dir /tmp/csbench-phase1b-final
uv venv /tmp/csbench-phase1b-venv --python 3.12
uv pip install --python /tmp/csbench-phase1b-venv/bin/python \
  /tmp/csbench-phase1b-final/*.whl
cd /tmp
/tmp/csbench-phase1b-venv/bin/python -c "import clousight_bench as cb; assert cb.RESULT_SCHEMA_VERSION == '0.2'; print('wheel schema', cb.RESULT_SCHEMA_VERSION)"
for task in T1.2 T1.3 T2.1 T4.1 T4.2; do
  /tmp/csbench-phase1b-venv/bin/csbench run \
    --domain agent-runtime --task "$task" --platform local-sim \
    --results /tmp/csbench-phase1b-results
done
/tmp/csbench-phase1b-venv/bin/csbench run \
  --domain bigdata-emr --task J1.1 --platform local-process \
  --results /tmp/csbench-phase1b-results
/tmp/csbench-phase1b-venv/bin/csbench report --results /tmp/csbench-phase1b-results
cd /Users/bowang/IdeaProjects/clousight-bench/.worktrees/phase1b-trusted-result-contract
```

Expected: ruff and pytest pass, `wheel schema 0.2` prints, all six runs exit `0`
and the report renders.

- [ ] **Step 8: Commit the documentation and CI**

```bash
git add .github/workflows/ci.yml README.md CONTRIBUTING.md docs/architecture.md CHANGELOG.md
git commit -s -m "docs: describe the 0.2 result contract and lifecycle"
```

- [ ] **Step 9: Open the Core pull request**

Run:

```bash
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
"$GH" auth status
git push origin feat/phase1b-trusted-result-contract
cd /Users/bowang/IdeaProjects/clousight-bench-pro/.worktrees/phase1b-contract-compat
git push -u origin feat/phase1b-contract-compat
"$GH" workflow run ci.yml \
  --repo clousight/clousight-bench-pro \
  --ref feat/phase1b-contract-compat \
  -f core_ref=feat/phase1b-trusted-result-contract
PRO_RUN_ID="$("$GH" run list --repo clousight/clousight-bench-pro \
  --workflow ci.yml --branch feat/phase1b-contract-compat --event workflow_dispatch \
  --limit 1 --json databaseId --jq '.[0].databaseId')"
test -n "$PRO_RUN_ID"
"$GH" run watch --repo clousight/clousight-bench-pro "$PRO_RUN_ID" --exit-status
cd /Users/bowang/IdeaProjects/clousight-bench/.worktrees/phase1b-trusted-result-contract
"$GH" pr create \
  --repo clousight/clousight-bench \
  --base main \
  --head feat/phase1b-trusted-result-contract \
  --title "Phase 1B: trusted result contract (ResultRecord 0.2)" \
  --body "$(cat <<'BODY'
## What

- ResultRecord schema `0.2`: `identity`, `environment`, `fingerprints`,
  `measurements`, `findings`, `observations`, `errors`, four-value `status`.
  `ok`, top-level `metrics`, top-level `evidence_layer` and `config_hash` are gone.
- `Task.execute()` / `Task.score()` replace `Task.run()` and `TaskOutput`; all
  six built-in tasks migrated.
- Auditable lifecycle: TEARDOWN is a mandatory `finally` boundary around
  SETUP → COLLECT, a teardown error never overwrites the primary error, and a
  scorer failure keeps the observations.
- Deterministic full-SHA-256 fingerprints over a canonical JSON encoding, with
  credentials, usernames and hostnames excluded.
- Atomic persistence with an emergency temp-directory dump; enricher failures
  isolated; a minimal `ResultPublisher` boundary with append-only receipts.
- `csbench migrate-results SOURCE --output DEST [--dry-run]`: never in place,
  never fabricating a fingerprint, byte-identical on a repeat run.

## Out of scope

Run plans, repeats, statistics and comparability (Phase 1C); plugin API ranges,
JSON Schema, conformance kit and workload sandboxing (Phase 1D). No real cloud
adapter, no commercial service. Version stays `0.2.0` Alpha.

## Verification

- `ruff check src tests`, `pytest -q`
- Installed-wheel smoke outside the checkout, asserting `RESULT_SCHEMA_VERSION == "0.2"`
- CI asserts every emitted record is schema `0.2` and that migration is idempotent
- Pro `cb-pricing` verified against this branch on `feat/phase1b-contract-compat`
BODY
)"
"$GH" pr checks --repo clousight/clousight-bench feat/phase1b-trusted-result-contract --watch
```

Expected: the manually dispatched Pro `core-compat` run first passes against the
unmerged Core Phase 1B head, then all five Core required checks pass.

- [ ] **Step 10: Merge Core and confirm `main`**

Run:

```bash
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
"$GH" pr merge --repo clousight/clousight-bench feat/phase1b-trusted-result-contract \
  --merge --delete-branch
cd /Users/bowang/IdeaProjects/clousight-bench
git checkout main
git pull --ff-only origin main
git rev-parse HEAD
RUN_ID="$("$GH" run list --repo clousight/clousight-bench \
  --branch main --event push --limit 1 \
  --json databaseId --jq '.[0].databaseId')"
test -n "$RUN_ID"
"$GH" run watch --repo clousight/clousight-bench "$RUN_ID" --exit-status
```

Expected: `main` carries Phase 1B and its CI run is `success`. Note the merge SHA.

- [ ] **Step 11: Land the Pro compatibility branch against the merged Core**

Run:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro/.worktrees/phase1b-contract-compat
git fetch origin
git rebase origin/main
git push origin feat/phase1b-contract-compat
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
"$GH" pr create \
  --repo clousight/clousight-bench-pro \
  --base main \
  --head feat/phase1b-contract-compat \
  --title "Phase 1B: adapt the pricing enricher to ResultRecord 0.2" \
  --body "Compatibility only. PricingEnricher now reads measurements, identity and environment from the 0.2 record and writes exclusively into extensions[\"cb-pricing\"]. No new commercial capability, no new package, no new entry point."
"$GH" pr checks --repo clousight/clousight-bench-pro feat/phase1b-contract-compat --watch
"$GH" pr merge --repo clousight/clousight-bench-pro feat/phase1b-contract-compat \
  --merge --delete-branch
```

Expected: the `core-compat` check — which checks out the now-merged Core `main`
— passes, and the pull request merges.

- [ ] **Step 12: Restore the local Core symlink**

Run:

```bash
ln -sfn /Users/bowang/IdeaProjects/clousight-bench \
  /Users/bowang/IdeaProjects/clousight-bench-pro/.worktrees/clousight-bench
readlink /Users/bowang/IdeaProjects/clousight-bench-pro/.worktrees/clousight-bench
cd /Users/bowang/IdeaProjects/clousight-bench-pro
git checkout main
git pull --ff-only origin main
uv sync --all-packages --all-extras --frozen
uv run pytest -q
```

Expected: the symlink now points at the Core main checkout and the Pro suite
passes against merged Core `main`.

**Rollback:** revert each merge through its own pull request, Pro first, then
Core:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro
git checkout -b revert/phase1b-contract-compat main
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
PRO_MERGE_SHA="$("$GH" pr view --repo clousight/clousight-bench-pro \
  feat/phase1b-contract-compat --json mergeCommit --jq '.mergeCommit.oid')"
test -n "$PRO_MERGE_SHA"
git revert -m 1 "$PRO_MERGE_SHA" --signoff
git push origin revert/phase1b-contract-compat
"$GH" pr create --repo clousight/clousight-bench-pro --base main \
  --head revert/phase1b-contract-compat --title "Revert Phase 1B Pro compat" \
  --body "Reverts the Phase 1B Pro merge commit."

cd /Users/bowang/IdeaProjects/clousight-bench
git checkout -b revert/phase1b-trusted-result-contract main
CORE_MERGE_SHA="$("$GH" pr view --repo clousight/clousight-bench \
  feat/phase1b-trusted-result-contract --json mergeCommit --jq '.mergeCommit.oid')"
test -n "$CORE_MERGE_SHA"
git revert -m 1 "$CORE_MERGE_SHA" --signoff
git push origin revert/phase1b-trusted-result-contract
"$GH" pr create --repo clousight/clousight-bench --base main \
  --head revert/phase1b-trusted-result-contract --title "Revert Phase 1B" \
  --body "Reverts the Phase 1B Core merge commit."
```

Records already written in schema `0.2` stay readable after a revert only via
the reverted code, so keep the revert branch available if any `0.2` result must
be re-read.

---

## Phase 1B Definition of Done

- `clousight_bench.RESULT_SCHEMA_VERSION == "0.2"` and every new record's `schema_version` is `"0.2"`.
- No new record contains `ok`, top-level `metrics`, top-level `evidence_layer` or `config_hash`.
- `status` is always one of `completed`, `failed`, `invalid`, `unsupported`.
- `TaskOutput` and `Task.run` no longer exist; all six built-in tasks implement `execute()` and `score()`.
- `score()` is pure: re-scoring the same bundle twice yields the same result and leaves the bundle unchanged.
- Entering SETUP always calls `teardown()`, including on a partial setup failure; a teardown error is recorded as its own stage error and never overwrites the execute/collect error.
- A scorer failure keeps every observation and produces a `failed` record with a SCORE stage error.
- A failing enricher records an ENRICH stage error, does not block other enrichers and does not change `status`.
- A failing publisher writes an append-only receipt and leaves the persisted record untouched.
- Persistence is atomic; a primary-write failure produces an emergency JSON file whose absolute path is printed on stderr.
- Canonical JSON rejects NaN and Infinity, all four digests are full SHA-256, and no credential, username or hostname reaches a record or a fingerprint.
- `csbench migrate-results` never writes in place, marks unknown fingerprints `"unknown"`, writes a manifest with each source path and SHA-256, loses no metric value, series point or artifact pointer, and is byte-identical on a repeat run.
- Core CI asserts schema `0.2` on emitted records and idempotent migration; the wheel smoke asserts `RESULT_SCHEMA_VERSION`.
- Pro's `cb-pricing` runs inside the `0.2` lifecycle using only public Core modules and writes only into `extensions["cb-pricing"]`.
- Core version is still `0.2.0` Alpha; no Phase 1C or Phase 1D capability and no real cloud adapter has been added.
