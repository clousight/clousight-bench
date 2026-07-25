# Phase 1A Release Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore a truthful, installable `0.2.0` developer-preview baseline: wheels contain runnable reference workloads, CI tests installed artifacts, skeleton adapters are visibly non-runnable, and CLI input failures return stable user-facing errors.

**Architecture:** Keep the current `DomainPack → Task → ProviderAdapter` and result model unchanged in Phase 1A. Add only the metadata and resource-location seams required by the approved Phase 1 design; ResultRecord 0.2, execute/score separation, fingerprints, run plans, comparability and protocol hardening belong to separate Phase 1B–1D plans.

**Tech Stack:** Python 3.10–3.13, stdlib `importlib.resources`, Hatchling, PyYAML, pytest, ruff, GitHub Actions, uv for local development.

## Global Constraints

- Core package version becomes `0.2.0`; release classifier remains `Development Status :: 3 - Alpha`.
- Do not implement a real cloud adapter, SaaS service, licensing, uploader, enterprise adapter or new Pro capability.
- Core remains lightweight: no new runtime dependency beyond `pyyaml>=6.0`.
- Every code change follows TDD: failing focused test, minimal implementation, focused test, relevant suite.
- All Core commits use DCO sign-off: `git commit -s`.
- Before any GitHub push/PR, `gh auth status` must show `clousight-dev`; no push is possible until that account and a remote exist.
- Preserve current ResultRecord `schema_version="1.0"` and `PLUGIN_API_VERSION="1.0"` in this plan; Phase 1B/1D will replace those contracts.
- `skeleton` adapters are discoverable but never executable.
- Wheel smoke must run outside the repository working tree.
- Pro changes are compatibility-only: dependency pins and packaging of the already-existing sampler workload.

## Plan Boundary

This is the first of four implementation plans:

1. **Phase 1A — this plan:** release baseline, packaging, adapter status, CLI errors, CI.
2. **Phase 1B:** ResultRecord 0.2, lifecycle, execute/score, fingerprints, migration.
3. **Phase 1C:** run-plan, repeats/statistics, comparability and reports.
4. **Phase 1D:** plugin API range, schemas, protocol/security, conformance kit and release governance.

Do not pull Phase 1B–1D work into this plan.

## File Map

### Core files created

- `src/clousight_bench/core/errors.py` — stable user-input exception hierarchy.
- `src/clousight_bench/core/resources.py` — resolve packaged reference workloads.
- `src/clousight_bench/resources/__init__.py` — package-data root.
- `src/clousight_bench/resources/workloads/__init__.py` — packaged workload collection.
- `tests/test_cli_surface.py` — list metadata and friendly run errors.
- `tests/test_packaged_resources.py` — resource discovery and default J1.1 regression.
- `CHANGELOG.md` — 0.2.0 developer-preview change log.
- `SECURITY.md` — current local execution and asset trust boundary.

### Core files modified

- `pyproject.toml` — package version and wheel resources.
- `uv.lock` — locked local package version.
- `src/clousight_bench/__init__.py` — runner/package version.
- `src/clousight_bench/core/plugin.py` — adapter status/provider metadata.
- `src/clousight_bench/core/registry.py` — unknown-domain error joins user-input hierarchy.
- `src/clousight_bench/core/orchestrator.py` — typed task/platform/skeleton errors.
- `src/clousight_bench/cli.py` — verbose discovery and exit-code-2 handling.
- `src/clousight_bench/domains/agent_runtime/adapters/local_sim.py` — `reference` status.
- `src/clousight_bench/domains/agent_runtime/adapters/cn_clouds.py` — `skeleton` statuses/providers.
- `src/clousight_bench/domains/bigdata_emr/adapters/local_process.py` — `reference` status.
- `src/clousight_bench/domains/bigdata_emr/adapters/aws_emr.py` — `skeleton` status/provider.
- `src/clousight_bench/domains/bigdata_emr/tasks/j1_1_wordcount.py` — packaged resource lookup.
- `tests/test_schema.py` — package version assertion.
- `tests/test_plugin_registry.py` — status metadata assertions.
- `tests/test_bigdata_workload.py` — resource helper use.
- `tests/test_gsm8k_e2e.py` — resource helper use.
- `.github/workflows/ci.yml` — Python 3.11, all local dimensions and wheel-install smoke.
- `README.md` — 0.2 preview and truthful adapter status.
- `CONTRIBUTING.md` — installed-wheel check and skeleton contract.

### Core files moved

- `workloads/wordcount-py/**` → `src/clousight_bench/resources/workloads/wordcount-py/**`
- `workloads/gsm8k-stats/**` → `src/clousight_bench/resources/workloads/gsm8k-stats/**`
- `workloads/ycsb-wrapper/**` → `src/clousight_bench/resources/workloads/ycsb-wrapper/**`

Keep `workloads/README.md` as a short compatibility pointer to the packaged location.

### Pro compatibility files modified

- `packages/cb-pricing/pyproject.toml`
- `packages/cb-samplers/pyproject.toml`
- `packages/cb-dataservice/pyproject.toml`
- `packages/cb-adapters-enterprise/pyproject.toml`
- `packages/cb-samplers/tests/test_sampler.py`

### Pro files moved/created

- `packages/cb-samplers/workloads/synthetic-sampler/**` → `packages/cb-samplers/src/cb_samplers/workloads/synthetic-sampler/**`
- `packages/cb-samplers/src/cb_samplers/workloads/__init__.py`

---

### Task 1: Reset Package Version and Establish Preview Governance

**Files:**
- Modify: `pyproject.toml:7`
- Modify: `src/clousight_bench/__init__.py:1-10`
- Modify: `tests/test_schema.py:68-71`
- Create: `CHANGELOG.md`

**Interfaces:**
- Produces: `clousight_bench.__version__ == RUNNER_VERSION == "0.2.0"`.
- Preserves: `PLUGIN_API_VERSION == "1.0"` and `ResultRecord.schema_version == "1.0"` until Phase 1B/1D.

- [ ] **Step 1: Write the failing package-version test**

Add to `tests/test_schema.py`:

```python
def test_package_version_is_0_2_preview():
    import clousight_bench

    assert clousight_bench.__version__ == "0.2.0"
    assert clousight_bench.RUNNER_VERSION == "0.2.0"
    assert clousight_bench.PLUGIN_API_VERSION == "1.0"
```

- [ ] **Step 2: Run the focused test and verify the current version fails**

Run:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench
uv run pytest tests/test_schema.py::test_package_version_is_0_2_preview -v
```

Expected: FAIL showing `"1.0.0" != "0.2.0"`.

- [ ] **Step 3: Change package and runner versions**

In `pyproject.toml`:

```toml
[project]
name = "clousight-bench"
version = "0.2.0"
```

In `src/clousight_bench/__init__.py`:

```python
"""Clousight Bench: reproducible, evidence-graded benchmarking for cloud products."""

RUNNER_VERSION = "0.2.0"

# Temporary compatibility contract for the pre-ResultRecord-0.2 plugin surface.
# Phase 1D replaces this with API-range negotiation.
PLUGIN_API_VERSION = "1.0"

__version__ = RUNNER_VERSION
```

- [ ] **Step 4: Add the changelog entry**

Create `CHANGELOG.md`:

```markdown
# Changelog

All notable changes to Clousight Bench are recorded here.

## 0.2.0 — Unreleased

Developer-preview reset before the first public release.

### Changed

- Restored installable reference workloads and wheel smoke coverage.
- Made adapter implementation status explicit.
- Standardized user-facing CLI configuration errors.

### Compatibility

- Package version is pre-1.0.
- Result schema and plugin API are migrated in later Phase 1 plans.
```

- [ ] **Step 5: Refresh the Core lockfile and run version/schema tests**

Run:

```bash
uv lock
uv run pytest tests/test_schema.py -v
```

Expected: all tests PASS, including existing `PLUGIN_API_VERSION == "1.0"` and schema defaults.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/clousight_bench/__init__.py tests/test_schema.py CHANGELOG.md
git commit -s -m "chore: reset core to 0.2 developer preview"
```

---

### Task 2: Add Adapter Status Metadata and Block Skeleton Execution

**Files:**
- Create: `src/clousight_bench/core/errors.py`
- Modify: `src/clousight_bench/core/plugin.py:45-100`
- Modify: `src/clousight_bench/core/registry.py:17-40`
- Modify: `src/clousight_bench/core/orchestrator.py:40-50`
- Modify: `src/clousight_bench/domains/agent_runtime/adapters/local_sim.py:25-31`
- Modify: `src/clousight_bench/domains/agent_runtime/adapters/cn_clouds.py:39-118`
- Modify: `src/clousight_bench/domains/bigdata_emr/adapters/local_process.py:16-21`
- Modify: `src/clousight_bench/domains/bigdata_emr/adapters/aws_emr.py:34-38`
- Modify: `tests/test_plugin_registry.py`

**Interfaces:**
- Produces: `AdapterStatus = Literal["reference", "experimental", "wired", "skeleton"]`.
- Produces: `ProviderAdapter.status`, `ProviderAdapter.provider`, `ProviderAdapter.is_runnable()`.
- Produces: `UnknownDomainError`, `UnknownTaskError`, `UnknownPlatformError`, `AdapterNotRunnableError`.
- Consumed by: Task 3 CLI error mapping and verbose list.

- [ ] **Step 1: Write failing metadata and skeleton-guard tests**

Append to `tests/test_plugin_registry.py`:

```python
import pytest

from clousight_bench.core.errors import AdapterNotRunnableError
from clousight_bench.core.orchestrator import execute
from clousight_bench.core.schema import RunSpec


def test_adapter_status_distinguishes_reference_from_skeleton():
    agent = get_domain("agent-runtime").adapters()
    bigdata = get_domain("bigdata-emr").adapters()

    assert agent["local-sim"].status == "reference"
    assert agent["local-sim"].is_runnable()
    assert agent["aliyun-agentrun"].status == "skeleton"
    assert not agent["aliyun-agentrun"].is_runnable()
    assert bigdata["local-process"].status == "reference"
    assert bigdata["aws-emr"].status == "skeleton"


def test_orchestrator_rejects_skeleton_before_preflight(tmp_path):
    with pytest.raises(AdapterNotRunnableError, match="aliyun-agentrun.*skeleton"):
        execute(
            RunSpec("agent-runtime", "T1.3", "aliyun-agentrun"),
            results_dir=tmp_path,
            preflight=False,
        )
```

- [ ] **Step 2: Run focused tests and verify missing interfaces**

Run:

```bash
uv run pytest tests/test_plugin_registry.py -v
```

Expected: collection FAIL because `clousight_bench.core.errors` does not exist.

- [ ] **Step 3: Add the input-error hierarchy**

Create `src/clousight_bench/core/errors.py`:

```python
"""Stable user-input errors shared by the API and CLI."""


class UserInputError(RuntimeError):
    """A request cannot run because the requested benchmark surface is invalid."""


class UnknownDomainError(UserInputError):
    pass


class UnknownTaskError(UserInputError):
    pass


class UnknownPlatformError(UserInputError):
    pass


class AdapterNotRunnableError(UserInputError):
    pass
```

- [ ] **Step 4: Add adapter metadata to the base class**

In `src/clousight_bench/core/plugin.py`, import `Literal` and add:

```python
AdapterStatus = Literal["reference", "experimental", "wired", "skeleton"]


class ProviderAdapter(ABC):
    name: str = "abstract"
    status: AdapterStatus = "experimental"
    provider: str | None = None

    @classmethod
    def is_runnable(cls) -> bool:
        return cls.status != "skeleton"
```

Keep the existing constructor and hooks unchanged.

- [ ] **Step 5: Mark built-in adapters truthfully**

Add class attributes:

```python
class LocalSimAdapter(AgentRuntimeAdapter):
    name = "local-sim"
    status = "reference"
    provider = None
```

```python
class AliyunAgentRunAdapter(AgentRuntimeAdapter):
    name = "aliyun-agentrun"
    status = "skeleton"
    provider = "aliyun"
```

Use the same shape for Huawei (`provider = "huawei"`), Volcengine (`"volcengine"`), local-process (`status = "reference"`, `provider = None`) and AWS EMR (`status = "skeleton"`, `provider = "aws"`).

- [ ] **Step 6: Replace untyped lookup failures and guard skeletons**

Change `RegistryError` in `core/registry.py` to subclass `UserInputError`, and raise `UnknownDomainError` from `get_domain()`:

```python
from clousight_bench.core.errors import UnknownDomainError, UserInputError


class RegistryError(UserInputError):
    pass


def get_domain(name: str) -> DomainPack:
    domains = load_domains()
    if name not in domains:
        available = ", ".join(sorted(domains)) or "<none installed>"
        raise UnknownDomainError(
            f"domain {name!r} not found. Installed domains: {available}"
        )
    return domains[name]
```

In `core/orchestrator.py`, replace both `KeyError` branches and guard the adapter class:

```python
from clousight_bench.core.errors import (
    AdapterNotRunnableError,
    UnknownPlatformError,
    UnknownTaskError,
)

if spec.task_id not in task_classes:
    raise UnknownTaskError(
        f"task {spec.task_id!r} not in domain {spec.domain!r}: {sorted(task_classes)}"
    )

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

task = task_classes[spec.task_id]()
adapter = adapter_cls(spec.target)
```

- [ ] **Step 7: Run focused and orchestrator regression tests**

In `tests/test_preflight.py`, preserve the two preflight-gate tests by explicitly treating the selected skeleton as a wired test double:

```python
from clousight_bench.domains.agent_runtime.adapters.cn_clouds import (
    AliyunAgentRunAdapter,
)


def test_run_aborts_at_preflight_not_midrun(monkeypatch, tmp_path):
    monkeypatch.setattr(AliyunAgentRunAdapter, "status", "wired")
    # existing assertions remain


def test_skip_preflight_reaches_the_real_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(AliyunAgentRunAdapter, "status", "wired")
    # existing assertions remain
```

Run:

```bash
uv run pytest tests/test_plugin_registry.py tests/test_preflight.py tests/test_agent_runtime_local.py -v
```

Expected: all tests PASS. The dedicated plugin-registry test proves normal skeleton rejection; the two preflight tests opt into `wired` only to keep testing the preflight gate independently.

- [ ] **Step 8: Commit**

```bash
git add src/clousight_bench/core/errors.py \
  src/clousight_bench/core/plugin.py \
  src/clousight_bench/core/registry.py \
  src/clousight_bench/core/orchestrator.py \
  src/clousight_bench/domains/agent_runtime/adapters/local_sim.py \
  src/clousight_bench/domains/agent_runtime/adapters/cn_clouds.py \
  src/clousight_bench/domains/bigdata_emr/adapters/local_process.py \
  src/clousight_bench/domains/bigdata_emr/adapters/aws_emr.py \
  tests/test_plugin_registry.py tests/test_preflight.py
git commit -s -m "feat: expose adapter readiness status"
```

---

### Task 3: Make CLI Discovery and Input Errors Actionable

**Files:**
- Modify: `src/clousight_bench/cli.py:23-250`
- Create: `tests/test_cli_surface.py`

**Interfaces:**
- Consumes: `ProviderAdapter.status/provider/is_runnable()` and `UserInputError` from Task 2.
- Produces: `csbench list --verbose`.
- Produces: stable exit code `2` for bad domain/task/platform, skeleton adapter, missing config and invalid YAML.

- [ ] **Step 1: Write failing CLI surface tests**

Create `tests/test_cli_surface.py`:

```python
from clousight_bench.cli import main


def test_list_verbose_shows_task_and_adapter_status(capsys):
    rc = main(["list", "--verbose"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "T1.3" in out
    assert "Tool-failure recovery" in out
    assert "local-sim" in out and "reference" in out
    assert "aliyun-agentrun" in out and "skeleton" in out


def test_run_unknown_task_returns_usage_error_without_traceback(capsys):
    rc = main(
        ["run", "--domain", "agent-runtime", "--task", "NOPE", "--platform", "local-sim"]
    )
    captured = capsys.readouterr()

    assert rc == 2
    assert "NOPE" in captured.err
    assert "csbench list" in captured.err
    assert "Traceback" not in captured.err


def test_run_skeleton_returns_usage_error(capsys):
    rc = main(
        [
            "run",
            "--domain",
            "agent-runtime",
            "--task",
            "T1.3",
            "--platform",
            "aliyun-agentrun",
            "--skip-preflight",
        ]
    )
    captured = capsys.readouterr()

    assert rc == 2
    assert "skeleton" in captured.err


def test_run_missing_config_returns_usage_error(capsys):
    rc = main(
        [
            "run",
            "--domain",
            "agent-runtime",
            "--task",
            "T1.3",
            "--platform",
            "local-sim",
            "--config",
            "does-not-exist.yaml",
        ]
    )
    captured = capsys.readouterr()

    assert rc == 2
    assert "does-not-exist.yaml" in captured.err
```

- [ ] **Step 2: Run tests and verify parser/interface failures**

Run:

```bash
uv run pytest tests/test_cli_surface.py -v
```

Expected: FAIL because `list` rejects `--verbose` and `_cmd_run` propagates exceptions.

- [ ] **Step 3: Add verbose list output**

Change `_cmd_list` to:

```python
def _cmd_list(args: argparse.Namespace) -> int:
    domains = load_domains()
    if not domains:
        print("no domain packs installed")
        return 1
    for name, pack in sorted(domains.items()):
        print(f"domain: {name}")
        if pack.description:
            print(f"  {pack.description}")
        if not args.verbose:
            print(f"  tasks     : {', '.join(sorted(pack.tasks()))}")
            print(f"  platforms : {', '.join(sorted(pack.adapters()))}")
            continue
        print("  tasks:")
        for task_id, task_cls in sorted(pack.tasks().items()):
            print(
                f"    {task_id:<8} {task_cls.title} "
                f"[evidence={task_cls.evidence_layer}]"
            )
        print("  platforms:")
        for platform, adapter_cls in sorted(pack.adapters().items()):
            provider = adapter_cls.provider or "local"
            print(
                f"    {platform:<24} status={adapter_cls.status} "
                f"provider={provider}"
            )
    return 0
```

Register the flag:

```python
list_p = sub.add_parser("list", help="list installed domains, tasks and platforms")
list_p.add_argument("--verbose", action="store_true", help="show task and adapter metadata")
```

- [ ] **Step 4: Add one config loader with precise errors**

Add:

```python
def _load_config(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    config_path = Path(path)
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise UserInputError(f"config not found: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise UserInputError(f"invalid YAML in {config_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise UserInputError(f"config root must be a mapping: {config_path}")
    return data
```

Use it in `_cmd_run` and `_cmd_doctor` instead of direct reads.

- [ ] **Step 5: Centralize command dispatch and usage-error handling**

Import `UserInputError`, move the existing command `if` chain into `_dispatch(args)`, and wrap it:

```python
def _dispatch(args: argparse.Namespace) -> int:
    handlers = {
        "list": _cmd_list,
        "run": _cmd_run,
        "report": _cmd_report,
        "init": _cmd_init,
        "doctor": _cmd_doctor,
    }
    return handlers[args.command](args)


def main(argv: list[str] | None = None) -> int:
    # existing parser construction remains here
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except UserInputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("hint: run `csbench list --verbose` to inspect valid choices", file=sys.stderr)
        return 2
```

Do not catch unexpected exceptions; internal bugs must still fail loudly in Phase 1A.

- [ ] **Step 6: Run focused CLI tests**

Run:

```bash
uv run pytest tests/test_cli_surface.py tests/test_cli_init_doctor.py -v
```

Expected: all tests PASS.

- [ ] **Step 7: Run local CLI smoke**

Run:

```bash
uv run csbench list --verbose
uv run csbench run --domain agent-runtime --task T1.3 --platform local-sim \
  --results /tmp/csbench-phase1a-cli
```

Expected: list labels local/skeleton adapters; run exits `0` and prints a successful JSON record.

- [ ] **Step 8: Commit**

```bash
git add src/clousight_bench/cli.py tests/test_cli_surface.py
git commit -s -m "feat: make CLI discovery and errors actionable"
```

---

### Task 4: Package and Resolve Reference Workloads

**Files:**
- Create: `src/clousight_bench/core/resources.py`
- Create: `src/clousight_bench/resources/__init__.py`
- Create: `src/clousight_bench/resources/workloads/__init__.py`
- Move: `workloads/wordcount-py/**`
- Move: `workloads/gsm8k-stats/**`
- Move: `workloads/ycsb-wrapper/**`
- Modify: `workloads/README.md`
- Modify: `src/clousight_bench/domains/bigdata_emr/tasks/j1_1_wordcount.py:28-37`
- Modify: `tests/test_bigdata_workload.py`
- Modify: `tests/test_gsm8k_e2e.py`
- Create: `tests/test_packaged_resources.py`

**Interfaces:**
- Produces: `reference_workload_path(name: str) -> Path`.
- Consumed by: J1.1 and tests; later Phase 1D manifest discovery.
- Preserves: absolute workload paths for caller-supplied workloads.

- [ ] **Step 1: Write failing resource tests before moving files**

Create `tests/test_packaged_resources.py`:

```python
from clousight_bench.core.resources import reference_workload_path
from clousight_bench.core.workload import WorkloadEngine


def test_reference_workloads_are_package_resources():
    for name in ("wordcount-py", "gsm8k-stats", "ycsb-wrapper"):
        path = reference_workload_path(name)
        assert (path / "manifest.yaml").is_file()


def test_packaged_wordcount_executes():
    engine = WorkloadEngine(reference_workload_path("wordcount-py"))
    result = engine.run({"rows": 100, "seed": 7})
    assert result.ok
    assert result.metrics["rows_processed"] == 100
```

- [ ] **Step 2: Run and verify the resource module is missing**

Run:

```bash
uv run pytest tests/test_packaged_resources.py -v
```

Expected: collection FAIL because `clousight_bench.core.resources` does not exist.

- [ ] **Step 3: Move workload directories into package data**

Run:

```bash
mkdir -p src/clousight_bench/resources/workloads
touch src/clousight_bench/resources/__init__.py
touch src/clousight_bench/resources/workloads/__init__.py
git mv workloads/wordcount-py src/clousight_bench/resources/workloads/
git mv workloads/gsm8k-stats src/clousight_bench/resources/workloads/
git mv workloads/ycsb-wrapper src/clousight_bench/resources/workloads/
```

Replace `workloads/README.md` with:

```markdown
# Reference workloads

Runnable reference workloads are packaged under
`src/clousight_bench/resources/workloads/` so editable and wheel installs use
the same files. Use `clousight_bench.core.resources.reference_workload_path()`
instead of constructing repository-relative paths.
```

- [ ] **Step 4: Add the resource resolver**

Create `src/clousight_bench/core/resources.py`:

```python
"""Installed-safe access to bundled reference workloads."""
from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from clousight_bench.core.errors import UserInputError


def reference_workload_path(name: str) -> Path:
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise UserInputError(f"invalid reference workload name: {name!r}")
    resource = files("clousight_bench.resources.workloads").joinpath(name)
    path = Path(str(resource))
    if not (path / "manifest.yaml").is_file():
        raise UserInputError(f"reference workload not found: {name!r}")
    return path
```

- [ ] **Step 5: Make J1.1 use package resources**

Replace `_workload_dir` in `j1_1_wordcount.py`:

```python
from clousight_bench.core.resources import reference_workload_path


def _workload_dir(self, params: dict[str, Any]) -> Path:
    workload = str(params.get("workload", DEFAULT_WORKLOAD))
    path = Path(workload)
    if path.is_absolute():
        return path
    return reference_workload_path(workload)
```

- [ ] **Step 6: Update existing workload tests to use the resolver**

In `tests/test_bigdata_workload.py`, remove `REPO_ROOT` and construct both engines with:

```python
from clousight_bench.core.resources import reference_workload_path

engine = WorkloadEngine(reference_workload_path("wordcount-py"))
```

In `tests/test_gsm8k_e2e.py`, replace `_WL` with:

```python
from clousight_bench.core.resources import reference_workload_path

_WL = reference_workload_path("gsm8k-stats")
```

- [ ] **Step 7: Run workload and local bigdata tests**

Run:

```bash
uv run pytest \
  tests/test_packaged_resources.py \
  tests/test_bigdata_workload.py \
  tests/test_gsm8k_e2e.py -v
```

Expected: package-resource and bigdata tests PASS; network GSM8K test remains skipped unless explicitly enabled.

- [ ] **Step 8: Verify Hatch wheel includes resources**

Run:

```bash
rm -rf /tmp/csbench-phase1a-dist
uv build --out-dir /tmp/csbench-phase1a-dist
python3 - <<'PY'
from pathlib import Path
from zipfile import ZipFile

wheel = next(Path("/tmp/csbench-phase1a-dist").glob("*.whl"))
with ZipFile(wheel) as archive:
    names = set(archive.namelist())
required = {
    "clousight_bench/resources/workloads/wordcount-py/manifest.yaml",
    "clousight_bench/resources/workloads/wordcount-py/run.py",
    "clousight_bench/resources/workloads/gsm8k-stats/manifest.yaml",
    "clousight_bench/resources/workloads/ycsb-wrapper/manifest.yaml",
}
missing = required - names
assert not missing, f"wheel missing: {sorted(missing)}"
print(f"verified {len(required)} packaged workload files")
PY
```

Expected: `verified 4 packaged workload files`.

- [ ] **Step 9: Commit**

```bash
git add -A workloads src/clousight_bench/resources
git add src/clousight_bench/core/resources.py \
  src/clousight_bench/domains/bigdata_emr/tasks/j1_1_wordcount.py \
  workloads/README.md tests/test_packaged_resources.py \
  tests/test_bigdata_workload.py tests/test_gsm8k_e2e.py
git commit -s -m "fix: package reference workloads in wheel"
```

---

### Task 5: Add Installed-Wheel Smoke and Repair Core CI

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `CONTRIBUTING.md`

**Interfaces:**
- Consumes: packaged `wordcount-py` from Task 4.
- Produces: CI proof that a clean wheel install runs `list`, all five local-sim dimensions and J1.1 outside the repository.

- [ ] **Step 1: Reproduce the current missing-config smoke failure**

Run:

```bash
test -f configs/bigdata-emr.local.yaml
```

Expected: exit `1`; the file referenced by current CI does not exist.

- [ ] **Step 2: Expand the source-test matrix and remove the nonexistent config**

Update the matrix:

```yaml
strategy:
  matrix:
    python-version: ["3.10", "3.11", "3.12", "3.13"]
```

Replace the smoke block with:

```yaml
- name: Local baseline smoke (no cloud account)
  run: |
    csbench list --verbose
    for task in T1.2 T1.3 T2.1 T4.1 T4.2; do
      csbench run --domain agent-runtime --task "$task" --platform local-sim
    done
    csbench run --domain agent-runtime --task T1.3 --platform local-sim \
      --config configs/local-sim.fail-fast.yaml
    csbench run --domain bigdata-emr --task J1.1 --platform local-process
    csbench report
```

- [ ] **Step 3: Add a separate installed-wheel job**

Append:

```yaml
  wheel-smoke:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Build wheel
        run: |
          python -m pip install --upgrade pip
          python -m pip wheel . --wheel-dir dist --no-deps
      - name: Install wheel into isolated environment
        run: |
          python -m venv /tmp/csbench-wheel
          /tmp/csbench-wheel/bin/pip install dist/*.whl
      - name: Smoke installed wheel outside checkout
        working-directory: /tmp
        run: |
          /tmp/csbench-wheel/bin/csbench list --verbose
          /tmp/csbench-wheel/bin/csbench run \
            --domain agent-runtime --task T1.3 --platform local-sim \
            --results /tmp/csbench-wheel-results
          /tmp/csbench-wheel/bin/csbench run \
            --domain bigdata-emr --task J1.1 --platform local-process \
            --results /tmp/csbench-wheel-results
          /tmp/csbench-wheel/bin/csbench report \
            --results /tmp/csbench-wheel-results
```

- [ ] **Step 4: Run the equivalent wheel smoke locally**

Run:

```bash
rm -rf /tmp/csbench-wheel-env /tmp/csbench-wheel-results /tmp/csbench-wheel-dist
uv build --out-dir /tmp/csbench-wheel-dist
uv venv /tmp/csbench-wheel-env --python 3.12
/tmp/csbench-wheel-env/bin/pip install /tmp/csbench-wheel-dist/*.whl
cd /tmp
/tmp/csbench-wheel-env/bin/csbench list --verbose
/tmp/csbench-wheel-env/bin/csbench run \
  --domain bigdata-emr --task J1.1 --platform local-process \
  --results /tmp/csbench-wheel-results
```

Expected: J1.1 exits `0` outside the repository and writes a successful result.

- [ ] **Step 5: Make README capability claims truthful**

Add this block below the README introduction, update the J1.1 example to omit `--config`, and remove conflicting readiness claims:

```markdown
> **0.2.0 Developer Preview.** The local reference baselines are runnable.
> Real-cloud adapters are visible for contributors but are not wired yet.

Run `csbench list --verbose` to inspect task metadata and adapter readiness.

| Adapter | Status | Runnable |
|---|---|---|
| `local-sim` | reference | yes |
| `local-process` | reference | yes |
| `aliyun-agentrun` | skeleton | no |
| `huawei-agentarts` | skeleton | no |
| `volcengine-agentkit` | skeleton | no |
| `aws-emr` | skeleton | no |

Adapter status is part of the public contract:
`reference` and `wired` can run; `experimental` can run with preview caveats;
`skeleton` is discoverable for contributors but is rejected before preflight.
```

- [ ] **Step 6: Update contributor verification**

In `CONTRIBUTING.md`, add:

```markdown
Before opening a PR that touches packaging, build the wheel and run the smoke
outside the checkout. Editable installs are not sufficient evidence because
repository-relative resource bugs do not reproduce there.

New adapters must declare one of `reference`, `experimental`, `wired`, or
`skeleton`. A skeleton must never be presented as runnable.
```

- [ ] **Step 7: Run full Core checks**

Run:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench
uv run ruff check src tests
uv run pytest -q
```

Expected: ruff passes; current suite plus new tests pass, with only the opt-in network test skipped.

- [ ] **Step 8: Commit**

```bash
git add .github/workflows/ci.yml README.md CONTRIBUTING.md
git commit -s -m "ci: verify source and installed-wheel baselines"
```

---

### Task 6: Repair Existing Pro Sampler Packaging and Core Version Pins

**Repository:** `/Users/bowang/IdeaProjects/clousight-bench-pro`

**Files:**
- Move: `packages/cb-samplers/workloads/synthetic-sampler/**`
- Create: `packages/cb-samplers/src/cb_samplers/workloads/__init__.py`
- Modify: `packages/cb-samplers/tests/test_sampler.py`
- Modify: `packages/cb-pricing/pyproject.toml`
- Modify: `packages/cb-samplers/pyproject.toml`
- Modify: `packages/cb-dataservice/pyproject.toml`
- Modify: `packages/cb-adapters-enterprise/pyproject.toml`

**Interfaces:**
- Consumes: Core package version `0.2.0`.
- Produces: existing sampler workload included in `cb-samplers` wheel.
- Constraint: no new Pro runtime behavior or commercial service.

- [ ] **Step 1: Add a failing sampler-resource test**

Append to `packages/cb-samplers/tests/test_sampler.py`:

```python
from importlib.resources import files


def test_synthetic_workload_is_packaged():
    root = files("cb_samplers").joinpath("workloads", "synthetic-sampler")
    assert root.joinpath("manifest.yaml").is_file()
    assert root.joinpath("run.py").is_file()
```

- [ ] **Step 2: Run the focused test**

Run:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro
uv run pytest packages/cb-samplers/tests/test_sampler.py -v
```

Expected: FAIL because `cb_samplers/workloads/synthetic-sampler` is absent from the package.

- [ ] **Step 3: Move the existing sampler workload into the package**

Run:

```bash
mkdir -p packages/cb-samplers/src/cb_samplers/workloads
touch packages/cb-samplers/src/cb_samplers/workloads/__init__.py
git mv packages/cb-samplers/workloads/synthetic-sampler \
  packages/cb-samplers/src/cb_samplers/workloads/
```

Hatchling already packages files under `src/cb_samplers`; do not add a second copy.

- [ ] **Step 4: Align Pro dependency pins with the pre-1.0 Core**

In all four Pro package `pyproject.toml` files, replace:

```toml
dependencies = ["clousight-bench>=1.0,<2.0"]
```

with:

```toml
dependencies = ["clousight-bench>=0.2,<0.3"]
```

For `cb-dataservice`, preserve the extra:

```toml
dependencies = ["clousight-bench[store]>=0.2,<0.3"]
```

- [ ] **Step 5: Refresh the ignored local lock and run Pro tests**

Run:

```bash
uv lock
uv run pytest -q
uv run ruff check packages
```

Expected: 14 existing tests plus the new packaging test PASS; ruff passes. `uv.lock` is currently ignored, so this step verifies the local workspace but does not add the lock in Phase 1A.

- [ ] **Step 6: Verify the sampler wheel contents**

Run:

```bash
rm -rf /tmp/cb-samplers-phase1a
uv build --package cb-samplers --out-dir /tmp/cb-samplers-phase1a
python3 - <<'PY'
from pathlib import Path
from zipfile import ZipFile

wheel = next(Path("/tmp/cb-samplers-phase1a").glob("*.whl"))
with ZipFile(wheel) as archive:
    names = set(archive.namelist())
required = {
    "cb_samplers/workloads/synthetic-sampler/manifest.yaml",
    "cb_samplers/workloads/synthetic-sampler/run.py",
}
missing = required - names
assert not missing, f"wheel missing: {sorted(missing)}"
print("sampler workload packaged")
PY
```

Expected: `sampler workload packaged`.

- [ ] **Step 7: Commit in the Pro repository**

```bash
git add packages/cb-samplers/src/cb_samplers/workloads \
  packages/cb-samplers/tests/test_sampler.py \
  packages/cb-pricing/pyproject.toml \
  packages/cb-samplers/pyproject.toml \
  packages/cb-dataservice/pyproject.toml \
  packages/cb-adapters-enterprise/pyproject.toml
git commit -s -m "fix: align Pro packages with core 0.2"
```

Do not push: the required `clousight-dev` account and repository remote must be configured first.

---

### Task 7: Add the Current Security Boundary and Final Phase 1A Verification

**Repository:** `/Users/bowang/IdeaProjects/clousight-bench`

**Files:**
- Create: `SECURITY.md`
- Modify: `docs/architecture.md`

**Interfaces:**
- Documents: current local-execution trust boundary without claiming Phase 1D sandboxing is complete.
- Produces: final Core and Pro verification evidence for Phase 1A.

- [ ] **Step 1: Create the minimum honest security policy**

Create `SECURITY.md`:

```markdown
# Security

## Reporting

This repository remains private during the 0.2 developer-preview phase.
Report suspected vulnerabilities through the private repository's maintainer
channel; do not copy vulnerability details into public issues or chat rooms.

## Current trust boundary

Clousight Bench runs workload executables and reads asset URLs declared by
manifests. In 0.2.0 these inputs are trusted local configuration:

- run only workloads and manifests you have reviewed;
- remote assets can make outbound requests;
- cloud credentials remain in provider SDK/default credential chains and must
  not be embedded in RunSpec or result files;
- `skeleton` adapters are not runnable.

Workload sandboxing, protocol limits and stricter path/URI validation are part
of the approved Phase 1D hardening work. Until then, do not run untrusted
third-party workload packages.
```

- [ ] **Step 2: Align architecture documentation with implemented Phase 1A facts**

Add this section to `docs/architecture.md` after the current-domain summary, and remove any conflicting statement that a skeleton is runnable:

```markdown
## 0.2 Developer Preview readiness

- `reference` and `wired` adapters can execute.
- `experimental` adapters can execute with preview caveats.
- `skeleton` adapters are discoverable but rejected before preflight.
- Current runnable references are `local-sim` and `local-process`; no real-cloud
  adapter is wired.
- Bundled workloads live in `clousight_bench.resources.workloads` and are
  resolved with `core.resources.reference_workload_path()`, so wheel and
  editable installs use the same files.

Phase 1A retains ResultRecord schema `1.0` and plugin API `1.0`. Their `0.2`
replacement is designed but is not implemented until Phase 1B/1D.
```

- [ ] **Step 3: Run Core final verification**

Run:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench
uv run ruff check src tests
uv run pytest -q
rm -rf /tmp/csbench-phase1a-final
uv build --out-dir /tmp/csbench-phase1a-final
```

Expected: ruff passes; tests pass with only the opt-in network test skipped; sdist and wheel build successfully.

- [ ] **Step 4: Run installed-wheel final smoke**

Run:

```bash
rm -rf /tmp/csbench-phase1a-final-venv /tmp/csbench-phase1a-final-results
uv venv /tmp/csbench-phase1a-final-venv --python 3.12
/tmp/csbench-phase1a-final-venv/bin/pip install /tmp/csbench-phase1a-final/*.whl
cd /tmp
/tmp/csbench-phase1a-final-venv/bin/csbench list --verbose
for task in T1.2 T1.3 T2.1 T4.1 T4.2; do
  /tmp/csbench-phase1a-final-venv/bin/csbench run \
    --domain agent-runtime --task "$task" --platform local-sim \
    --results /tmp/csbench-phase1a-final-results
done
/tmp/csbench-phase1a-final-venv/bin/csbench run \
  --domain bigdata-emr --task J1.1 --platform local-process \
  --results /tmp/csbench-phase1a-final-results
/tmp/csbench-phase1a-final-venv/bin/csbench report \
  --results /tmp/csbench-phase1a-final-results
```

Expected: all six runs exit `0`; comparison report is created.

- [ ] **Step 5: Run Pro compatibility verification**

Run:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro
uv run pytest -q
uv run ruff check packages
```

Expected: all Pro tests pass and ruff reports no errors.

- [ ] **Step 6: Check both repository worktrees**

Run:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench
git status --short
cd /Users/bowang/IdeaProjects/clousight-bench-pro
git status --short
```

Expected: only the intended Phase 1A documentation changes remain before the final Core commit; Pro is clean after Task 6.

- [ ] **Step 7: Commit Core documentation**

```bash
cd /Users/bowang/IdeaProjects/clousight-bench
git add SECURITY.md docs/architecture.md
git commit -s -m "docs: define 0.2 security and readiness boundaries"
```

- [ ] **Step 8: Record completion without pushing**

Run:

```bash
git log --oneline -8
git status --short
```

Expected: Phase 1A commits are visible and the Core worktree is clean. Do not push until `gh auth status` shows `clousight-dev` and the correct remote exists.

## Phase 1A Definition of Done

- `clousight_bench.__version__ == "0.2.0"`.
- Core ResultRecord and plugin API remain explicitly on their old contracts pending Phase 1B/1D.
- `csbench list --verbose` distinguishes reference, experimental, wired and skeleton adapters.
- Skeleton adapters are rejected before preflight/setup.
- Bad domain/task/platform/config requests return exit code 2 without a traceback.
- Core wheel contains and runs wordcount, GSM8K and YCSB reference workloads.
- J1.1 works from a wheel installed outside the checkout.
- CI includes Python 3.11 and runs all five local-sim dimensions.
- CI has a separate installed-wheel smoke.
- The current nonexistent `bigdata-emr.local.yaml` reference is removed.
- Existing Pro sampler workload is present in its wheel; no new Pro capability is added.
- Core and Pro tests and ruff checks pass.
- README, architecture and security statements match actual implementation.

