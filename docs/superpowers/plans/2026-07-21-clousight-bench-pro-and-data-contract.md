# Clousight Bench 数据契约 + clousight-bench-pro 骨架 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给开源核心加「面向 SaaS 富采样」的数据契约（schema/协议/store/enricher 扩展点），并新建私有多模块仓 `clousight-bench-pro`（cb-pricing 接真 + cb-samplers/cb-dataservice 可运行骨架）。

**Architecture:** 抽象切在编排层不变；本轮新增三个开源扩展面——(1) `ResultRecord` 三通道字段 + JSONL `sample`/`artifact` 事件，(2) `ResultStore`（Parquet 列存，DuckDB 查询，optional extra），(3) `ResultEnricher` entry-point 扩展点。商业实现（定价/采样/rollup）全部落在独立私有仓，经 entry point 注入，core 零反向依赖。

**Tech Stack:** Python ≥3.10、hatchling（core）、uv workspace（pro）、pytest、ruff、duckdb + pyarrow（optional `[store]` extra）、pyyaml。

## Global Constraints

- Python 版本下限：`requires-python = ">=3.10"`（core），pro 各包同。
- core 主依赖只允许 `pyyaml>=6.0`；duckdb/pyarrow 必须隔离在 optional extra `[store]`（`duckdb>=1.0`、`pyarrow>=16`）。
- 插件兼容契约：core 暴露 `PLUGIN_API_VERSION = "1.0"`；pro 各包 `dependencies` pin `clousight-bench>=1.0,<2.0`。
- open-core 纪律：core 仓不得出现任何 pro 包名 / import / 引用；pro 仓 LICENSE 为专有（All Rights Reserved），非 Apache。
- 落盘向后兼容：record.json 路径保持 `results_dir/<domain>/<platform>/<task_id>-<run_id>.json`，现有 `test_result_file_persisted` 必须继续通过。
- 协议向后兼容：既有 `metric`/`log`/`result` JSONL 事件行为不变。
- ruff 配置：line-length 110，`select = ["E","F","I","W","UP"]`——所有新文件须过 `ruff check`。
- CLI 名：`csbench`（core）；pro 各包各自 CLI（如 `cb-dataservice`）。
- 提交纪律：每个 Task 末尾 commit；两仓均**仅本地 git，无 remote**，不 push。
- 证据分档取值：`{"A","B","C","D"}`，不得新增。

---

### Task 1: 版本锚点 + ResultRecord 三通道字段 + 容忍 from_dict

**Files:**
- Modify: `src/clousight_bench/__init__.py`
- Modify: `src/clousight_bench/core/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `clousight_bench.PLUGIN_API_VERSION: str = "1.0"`
- Produces: `ResultRecord` 新增字段 `schema_version: str = "1.0"`, `series: dict[str, Any] = {}`, `artifacts: list[dict] = []`
- Produces: `ResultRecord.from_dict(data)` 忽略未知键（前向兼容）

- [ ] **Step 1: 写失败测试**

在 `tests/test_schema.py` 末尾追加：

```python
def test_result_record_has_data_contract_defaults():
    from clousight_bench.core.schema import ResultRecord
    rec = ResultRecord(
        domain="d", task_id="t", platform="p", run_id="r",
        started_at=utc_now(), finished_at=utc_now(),
        config_hash="sha256:x", evidence_layer="C", metrics={},
    )
    assert rec.schema_version == "1.0"
    assert rec.series == {}
    assert rec.artifacts == []


def test_from_dict_tolerates_unknown_keys():
    from clousight_bench.core.schema import ResultRecord
    payload = {
        "domain": "d", "task_id": "t", "platform": "p", "run_id": "r",
        "started_at": utc_now(), "finished_at": utc_now(),
        "config_hash": "sha256:x", "evidence_layer": "C", "metrics": {},
        "future_field_from_newer_schema": 123,
    }
    rec = ResultRecord.from_dict(payload)
    assert rec.domain == "d"
    assert rec.schema_version == "1.0"


def test_plugin_api_version_exposed():
    import clousight_bench
    assert clousight_bench.PLUGIN_API_VERSION == "1.0"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_schema.py -k "data_contract or unknown_keys or plugin_api" -v`
Expected: FAIL（`AttributeError: schema_version` / `TypeError: unexpected keyword` / `AttributeError: PLUGIN_API_VERSION`）

- [ ] **Step 3: 实现 —— `__init__.py`**

把 `src/clousight_bench/__init__.py` 改为：

```python
"""Clousight Bench: reproducible, evidence-graded benchmarking for cloud products."""

RUNNER_VERSION = "0.1.0"

# Plugin compatibility contract (SemVer). Commercial/3rd-party plugins pin this.
# Bump the MAJOR when a plugin-facing contract (schema fields, entry-point
# groups, enricher/store signatures) changes incompatibly.
PLUGIN_API_VERSION = "1.0"

__version__ = RUNNER_VERSION
```

- [ ] **Step 4: 实现 —— schema 字段**

在 `src/clousight_bench/core/schema.py` 的 `ResultRecord` 中，`notes: str = ""` 之后、`error` 之前插入三字段：

```python
    schema_version: str = "1.0"
    series: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
```

将 `from_dict` 改为容忍未知键：

```python
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResultRecord:
        import dataclasses
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})
```

- [ ] **Step 5: 运行确认通过**

Run: `pytest tests/test_schema.py -v`
Expected: PASS（含既有 `test_result_record_roundtrip`）

- [ ] **Step 6: lint + commit**

```bash
ruff check src/clousight_bench/__init__.py src/clousight_bench/core/schema.py
git add src/clousight_bench/__init__.py src/clousight_bench/core/schema.py tests/test_schema.py
git commit -m "feat(core): PLUGIN_API_VERSION + ResultRecord series/artifacts/schema_version 数据契约"
```

---

### Task 2: workload 协议扩展 —— sample / artifact 事件

**Files:**
- Modify: `src/clousight_bench/core/workload.py`
- Test: `tests/test_workload_protocol.py` (create)

**Interfaces:**
- Consumes: 无（独立）
- Produces: `WorkloadResult` 新增 `series: dict[str, list]`（`{name: [[t, value], ...]}`）与 `artifacts: list[dict]`（每项 `{"kind","path","media","sha256"}`）
- Produces: 引擎解析 `{"type":"sample","series","t","value"}` 与 `{"type":"artifact","kind","path","media"}`，artifact 的 `sha256` 由引擎按 `workload_dir/path` 计算

- [ ] **Step 1: 写失败测试**

新建 `tests/test_workload_protocol.py`：

```python
"""WorkloadEngine protocol: sample + artifact events parse into WorkloadResult."""
import hashlib
import os
import stat
from pathlib import Path

from clousight_bench.core.workload import WorkloadEngine


def _make_workload(tmp_path: Path, script: str) -> Path:
    (tmp_path / "manifest.yaml").write_text(
        "name: proto-test\nversion: 0.0.1\nentrypoint: ./run.sh\n", encoding="utf-8"
    )
    run = tmp_path / "run.sh"
    run.write_text(script, encoding="utf-8")
    run.chmod(run.stat().st_mode | stat.S_IEXEC)
    return tmp_path


def test_sample_events_accumulate_into_series(tmp_path):
    script = (
        "#!/usr/bin/env bash\n"
        'echo \'{"type":"sample","series":"latency_ms","t":1,"value":10}\'\n'
        'echo \'{"type":"sample","series":"latency_ms","t":2,"value":20}\'\n'
        'echo \'{"type":"result","ok":true}\'\n'
    )
    wl = WorkloadEngine(_make_workload(tmp_path, script))
    res = wl.run()
    assert res.ok
    assert res.series["latency_ms"] == [[1, 10], [2, 20]]


def test_artifact_event_gets_sha256(tmp_path):
    (tmp_path / "trace.json").write_text('{"span":1}', encoding="utf-8")
    expected = "sha256:" + hashlib.sha256(b'{"span":1}').hexdigest()
    script = (
        "#!/usr/bin/env bash\n"
        'echo \'{"type":"artifact","kind":"otel_trace","path":"trace.json","media":"application/json"}\'\n'
        'echo \'{"type":"result","ok":true}\'\n'
    )
    wl = WorkloadEngine(_make_workload(tmp_path, script))
    res = wl.run()
    assert res.ok
    assert len(res.artifacts) == 1
    art = res.artifacts[0]
    assert art["kind"] == "otel_trace"
    assert art["path"] == "trace.json"
    assert art["media"] == "application/json"
    assert art["sha256"] == expected
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_workload_protocol.py -v`
Expected: FAIL（`AttributeError: 'WorkloadResult' object has no attribute 'series'`）

- [ ] **Step 3: 实现 —— WorkloadResult 字段**

在 `src/clousight_bench/core/workload.py` 的 `WorkloadResult` 中，`exit_code: int = 0` 之后追加：

```python
    series: dict[str, list] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
```

顶部加 `import hashlib`（放在 `import json` 附近）。

- [ ] **Step 4: 实现 —— run() 解析新事件**

在 `run()` 的事件循环里，`saw_result = False` / `result_ok = False` 附近新增本地累积容器，并在循环内追加分支。将现有循环初始化改为：

```python
        metrics: dict[str, Any] = {}
        logs: list[str] = []
        series: dict[str, list] = {}
        artifacts: list[dict[str, Any]] = []
        saw_result = False
        result_ok = False
```

在 `elif etype == "log":` 分支之后、`elif etype == "result":` 之前插入：

```python
            elif etype == "sample":
                name = str(event["series"])
                series.setdefault(name, []).append([event["t"], event["value"]])
            elif etype == "artifact":
                rel = str(event["path"])
                blob = (self.workload_dir / rel).read_bytes()
                artifacts.append({
                    "kind": str(event.get("kind", "artifact")),
                    "path": rel,
                    "media": str(event.get("media", "application/octet-stream")),
                    "sha256": "sha256:" + hashlib.sha256(blob).hexdigest(),
                })
```

把 return 改为：

```python
        return WorkloadResult(
            ok=ok, metrics=metrics, logs=logs, exit_code=proc.returncode,
            series=series, artifacts=artifacts,
        )
```

- [ ] **Step 5: 运行确认通过**

Run: `pytest tests/test_workload_protocol.py tests/test_bigdata_workload.py -v`
Expected: PASS（新协议不影响既有 workload 测试）

- [ ] **Step 6: lint + commit**

```bash
ruff check src/clousight_bench/core/workload.py tests/test_workload_protocol.py
git add src/clousight_bench/core/workload.py tests/test_workload_protocol.py
git commit -m "feat(core): workload 协议支持 sample/artifact 事件"
```

---

### Task 3: core/store.py（ResultStore + Parquet + DuckDB）+ orchestrator 接入 + [store] extra

**Files:**
- Create: `src/clousight_bench/core/store.py`
- Modify: `src/clousight_bench/core/orchestrator.py:100-106`（`_persist`）
- Modify: `pyproject.toml`
- Test: `tests/test_store.py` (create)

**Interfaces:**
- Consumes: `ResultRecord`（Task 1 的 series/artifacts 字段）
- Produces: `ResultStore(results_dir: Path)` with:
  - `persist(record: ResultRecord) -> Path`（写 record.json 到 `results_dir/<domain>/<platform>/<task_id>-<run_id>.json`；有 `[store]` 且有 series 时外置 parquet + 改写 record.series 为 `{"$parquet": <rel>}`）
  - `query_series(sql: str | None = None, glob: str = "**/series.parquet")`（返回 list[dict]；缺 extra 抛 `ImportError`）
- Produces: `STORE_AVAILABLE: bool`（duckdb+pyarrow 是否可用）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_store.py`：

```python
"""ResultStore: record.json 兼容布局 + 可选 Parquet series 外置。"""
import json
from pathlib import Path

import pytest

from clousight_bench.core.schema import ResultRecord, utc_now
from clousight_bench.core.store import STORE_AVAILABLE, ResultStore


def _rec(series=None) -> ResultRecord:
    return ResultRecord(
        domain="agent-runtime", task_id="T1.3", platform="local-sim", run_id="run-x",
        started_at=utc_now(), finished_at=utc_now(),
        config_hash="sha256:abc", evidence_layer="C", metrics={"p99_ms": 9},
        series=series or {},
    )


def test_persist_keeps_backward_compatible_record_path(tmp_path):
    store = ResultStore(tmp_path)
    path = store.persist(_rec())
    expected = tmp_path / "agent-runtime" / "local-sim" / "T1.3-run-x.json"
    assert path == expected
    assert expected.exists()
    data = json.loads(expected.read_text())
    assert data["metrics"]["p99_ms"] == 9


@pytest.mark.skipif(not STORE_AVAILABLE, reason="requires [store] extra")
def test_series_externalized_to_parquet_and_queryable(tmp_path):
    store = ResultStore(tmp_path)
    store.persist(_rec(series={"latency_ms": [[1, 10.0], [2, 20.0]]}))
    parquet = tmp_path / "agent-runtime" / "local-sim" / "run-x" / "series.parquet"
    assert parquet.exists()
    record_json = json.loads(
        (tmp_path / "agent-runtime" / "local-sim" / "T1.3-run-x.json").read_text()
    )
    assert record_json["series"] == {"$parquet": "agent-runtime/local-sim/run-x/series.parquet"}
    rows = store.query_series("SELECT series, count(*) AS n FROM series GROUP BY series")
    assert rows == [{"series": "latency_ms", "n": 2}]


def test_series_inline_when_store_unavailable(tmp_path, monkeypatch):
    import clousight_bench.core.store as store_mod
    monkeypatch.setattr(store_mod, "STORE_AVAILABLE", False)
    store = store_mod.ResultStore(tmp_path)
    store.persist(_rec(series={"latency_ms": [[1, 10.0]]}))
    record_json = json.loads(
        (tmp_path / "agent-runtime" / "local-sim" / "T1.3-run-x.json").read_text()
    )
    assert record_json["series"] == {"latency_ms": [[1, 10.0]]}
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_store.py -v`
Expected: FAIL（`ModuleNotFoundError: clousight_bench.core.store`）

- [ ] **Step 3: 实现 —— store.py**

新建 `src/clousight_bench/core/store.py`：

```python
"""ResultStore: persistence layer for records + time-series + artifacts.

record.json keeps the historical layout so report.py and older readers keep
working. When the optional [store] extra (duckdb + pyarrow) is installed and a
record carries series, the series is externalized to a per-run Parquet long
table and the record's `series` field becomes a pointer. Without the extra the
series stays inline in record.json (lossless at small scale).

Long-table columns (the stable handshake for cb-dataservice / SaaS web):
    run_id | domain | task_id | platform | config_hash | series | t | value | unit
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clousight_bench.core.schema import ResultRecord

try:  # optional [store] extra
    import duckdb  # noqa: F401
    import pyarrow  # noqa: F401

    STORE_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on install extras
    STORE_AVAILABLE = False

_LONG_COLUMNS = [
    "run_id", "domain", "task_id", "platform", "config_hash", "series", "t", "value", "unit",
]


class ResultStore:
    def __init__(self, results_dir: Path) -> None:
        self.results_dir = Path(results_dir)

    def _record_path(self, rec: ResultRecord) -> Path:
        out_dir = self.results_dir / rec.domain / rec.platform
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / f"{rec.task_id}-{rec.run_id}.json"

    def _run_dir(self, rec: ResultRecord) -> Path:
        return self.results_dir / rec.domain / rec.platform / rec.run_id

    def persist(self, record: ResultRecord) -> Path:
        payload = record.to_dict()
        if STORE_AVAILABLE and record.series:
            rel = self._write_series_parquet(record)
            payload["series"] = {"$parquet": rel}
        path = self._record_path(record)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def _write_series_parquet(self, record: ResultRecord) -> str:
        import pyarrow as pa
        import pyarrow.parquet as pq

        rows: dict[str, list] = {c: [] for c in _LONG_COLUMNS}
        for series_name, points in record.series.items():
            for t, value in points:
                rows["run_id"].append(record.run_id)
                rows["domain"].append(record.domain)
                rows["task_id"].append(record.task_id)
                rows["platform"].append(record.platform)
                rows["config_hash"].append(record.config_hash)
                rows["series"].append(series_name)
                rows["t"].append(t)
                rows["value"].append(float(value))
                rows["unit"].append(record.metrics.get(f"{series_name}__unit", ""))
        run_dir = self._run_dir(record)
        run_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = run_dir / "series.parquet"
        pq.write_table(pa.table(rows), parquet_path)
        return str(parquet_path.relative_to(self.results_dir))

    def query_series(self, sql: str | None = None, glob: str = "**/series.parquet") -> list[dict[str, Any]]:
        if not STORE_AVAILABLE:
            raise ImportError(
                "query_series needs the [store] extra: pip install clousight-bench[store]"
            )
        import duckdb

        pattern = str(self.results_dir / glob)
        con = duckdb.connect()
        con.execute(f"CREATE VIEW series AS SELECT * FROM read_parquet('{pattern}')")
        query = sql or "SELECT * FROM series"
        cur = con.execute(query)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
```

- [ ] **Step 4: 接入 orchestrator**

在 `src/clousight_bench/core/orchestrator.py` 顶部 import 区加：

```python
from clousight_bench.core.store import ResultStore
```

把文件末尾的 `_persist` 函数体替换为委托 ResultStore（保留函数签名以兼容任何调用方）：

```python
def _persist(record: ResultRecord, results_dir: Path) -> Path:
    path = ResultStore(results_dir).persist(record)
    logger.info("result -> %s", path)
    return path
```

- [ ] **Step 5: pyproject 加 extra**

在 `pyproject.toml` 的 `[project.optional-dependencies]` 中，`dev = [...]` 之外新增：

```toml
store = ["duckdb>=1.0", "pyarrow>=16"]
```

- [ ] **Step 6: 安装 extra 并运行全部相关测试**

```bash
pip install -e ".[store,dev]"
pytest tests/test_store.py tests/test_agent_runtime_local.py -v
```
Expected: PASS（含 `test_result_file_persisted` 未回归、parquet 用例生效）

- [ ] **Step 7: lint + commit**

```bash
ruff check src/clousight_bench/core/store.py src/clousight_bench/core/orchestrator.py tests/test_store.py
git add src/clousight_bench/core/store.py src/clousight_bench/core/orchestrator.py pyproject.toml tests/test_store.py
git commit -m "feat(core): ResultStore（Parquet 列存 + DuckDB 查询，可选 [store] extra）"
```

---

### Task 4: ResultEnricher 扩展点 + registry 加载 + orchestrator 钩子 + CLI --no-enrich

**Files:**
- Modify: `src/clousight_bench/core/plugin.py`
- Modify: `src/clousight_bench/core/registry.py`
- Modify: `src/clousight_bench/core/orchestrator.py`（execute 内、persist 之前）
- Modify: `src/clousight_bench/cli.py`
- Test: `tests/test_enricher.py` (create)

**Interfaces:**
- Consumes: `ResultRecord`
- Produces: `class ResultEnricher(ABC)`：属性 `name: str`；方法 `enrich(self, record: ResultRecord) -> ResultRecord`
- Produces: `registry.load_enrichers() -> list[ResultEnricher]`（按 `name` 排序，entry point group `clousight_bench.enrichers`）
- Produces: `orchestrator.execute(spec, results_dir=None, enrich: bool = True)`；enrich 时按序调用所有 enricher，位于 `_persist` 之前

- [ ] **Step 1: 写失败测试**

新建 `tests/test_enricher.py`：

```python
"""ResultEnricher hook: orchestrator applies registered enrichers before persist."""
from clousight_bench.core.plugin import ResultEnricher
from clousight_bench.core.schema import ResultRecord, utc_now


def test_enricher_is_abstract_and_subclassable():
    class Add(ResultEnricher):
        name = "add"

        def enrich(self, record: ResultRecord) -> ResultRecord:
            record.metrics["added"] = 1
            return record

    rec = ResultRecord(
        domain="d", task_id="t", platform="p", run_id="r",
        started_at=utc_now(), finished_at=utc_now(),
        config_hash="sha256:x", evidence_layer="C", metrics={},
    )
    out = Add().enrich(rec)
    assert out.metrics["added"] == 1


def test_orchestrator_applies_enrichers(monkeypatch, tmp_path):
    import clousight_bench.core.orchestrator as orch

    class Tagger(ResultEnricher):
        name = "tagger"

        def enrich(self, record: ResultRecord) -> ResultRecord:
            record.metrics["enriched_by"] = "tagger"
            return record

    monkeypatch.setattr(orch, "load_enrichers", lambda: [Tagger()])
    from clousight_bench.core.schema import RunSpec

    rec = orch.execute(
        RunSpec("agent-runtime", "T1.3", "local-sim"), results_dir=tmp_path
    )
    assert rec.metrics["enriched_by"] == "tagger"


def test_orchestrator_skips_enrichers_when_disabled(monkeypatch, tmp_path):
    import clousight_bench.core.orchestrator as orch
    from clousight_bench.core.schema import RunSpec

    called = {"n": 0}

    def _boom():
        called["n"] += 1
        return []

    monkeypatch.setattr(orch, "load_enrichers", _boom)
    orch.execute(RunSpec("agent-runtime", "T1.3", "local-sim"), results_dir=tmp_path, enrich=False)
    assert called["n"] == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_enricher.py -v`
Expected: FAIL（`ImportError: cannot import name 'ResultEnricher'`）

- [ ] **Step 3: 实现 —— plugin.py 抽象基类**

在 `src/clousight_bench/core/plugin.py` 末尾（`_redact` 之前或之后均可，放在 `DomainPack` 之后）新增。先在文件顶部 import 处补 `from clousight_bench.core.schema import ResultRecord`——注意避免循环导入：schema 不 import plugin，故安全。加：

```python
class ResultEnricher(ABC):
    """Post-run enrichment hook: annotate a ResultRecord (e.g. cost estimate).

    Open-core ships NO enricher implementations; commercial plugins register
    theirs via the ``clousight_bench.enrichers`` entry point. Enrichers must be
    deterministic and side-effect-free beyond the returned record.
    """

    name: str = "abstract"

    @abstractmethod
    def enrich(self, record: ResultRecord) -> ResultRecord:
        """Return the record, possibly with extra metrics / raw annotations."""
```

- [ ] **Step 4: 实现 —— registry loader**

在 `src/clousight_bench/core/registry.py` 中，import 处补 `ResultEnricher`，并新增常量与函数：

```python
from clousight_bench.core.plugin import DomainPack, ResultEnricher

ENRICHER_ENTRY_POINT_GROUP = "clousight_bench.enrichers"


def load_enrichers() -> list[ResultEnricher]:
    """Instantiate every installed enricher, ordered by name for determinism."""
    enrichers: list[ResultEnricher] = []
    for ep in entry_points(group=ENRICHER_ENTRY_POINT_GROUP):
        cls = ep.load()
        inst = cls()
        if not isinstance(inst, ResultEnricher):
            raise RegistryError(f"entry point {ep.name!r} is not a ResultEnricher")
        enrichers.append(inst)
    return sorted(enrichers, key=lambda e: e.name)
```

- [ ] **Step 5: 实现 —— orchestrator 钩子**

在 `orchestrator.py` import 区加 `from clousight_bench.core.registry import get_domain, load_enrichers`（合并现有 import 行）。将 `execute` 签名改为：

```python
def execute(spec: RunSpec, results_dir: Path | None = None, enrich: bool = True) -> ResultRecord:
```

在 `# RECORD` 段构造完 `record` 之后、`_persist(record, results_dir)` 之前插入：

```python
    if enrich:
        for enricher in load_enrichers():
            record = enricher.enrich(record)
```

- [ ] **Step 6: 实现 —— CLI --no-enrich**

在 `cli.py` 的 `_cmd_run` 里，`record = execute(...)` 改为传入 `enrich=not args.no_enrich`：

```python
    record = execute(spec, results_dir=Path(args.results), enrich=not args.no_enrich)
```

在 run 子命令参数区（`run_p.add_argument("--results", ...)` 后）加：

```python
    run_p.add_argument("--no-enrich", action="store_true", help="skip result enrichers")
```

- [ ] **Step 7: 运行确认通过**

Run: `pytest tests/test_enricher.py tests/ -v`
Expected: PASS（全量测试不回归）

- [ ] **Step 8: lint + commit**

```bash
ruff check src/clousight_bench/core/plugin.py src/clousight_bench/core/registry.py src/clousight_bench/core/orchestrator.py src/clousight_bench/cli.py tests/test_enricher.py
git add src/clousight_bench/core/plugin.py src/clousight_bench/core/registry.py src/clousight_bench/core/orchestrator.py src/clousight_bench/cli.py tests/test_enricher.py
git commit -m "feat(core): ResultEnricher 扩展点 + entry-point 加载 + orchestrator 钩子 + --no-enrich"
```

---

### Task 5: core 文档更新（architecture.md + README）

**Files:**
- Modify: `docs/architecture.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 1-4 的最终形状（字段名、entry-point group、extra 名）
- Produces: 无代码接口

- [ ] **Step 1: 更新 architecture.md**

在 `docs/architecture.md` 增补一节「数据契约与扩展点」，逐字覆盖：
- 三通道：`metrics`（标量判分）/ `series`（时序，`{name: [[t,value]]}`）/ `artifacts`（`{kind,uri/path,media,sha256}` 指针）。
- 协议：JSONL 新增 `sample`/`artifact` 事件（示例见 spec §2.3）。
- 落盘：record.json 保持 `results/<domain>/<platform>/<task>-<run_id>.json`；装 `[store]` extra 后 series 外置为 per-run `series.parquet`（长表列 `run_id|domain|task_id|platform|config_hash|series|t|value|unit`）。
- 扩展点：`clousight_bench.enrichers` entry-point group + `ResultEnricher.enrich(record)->record`；核心不带实现。
- `PLUGIN_API_VERSION="1.0"` 为插件兼容契约。

- [ ] **Step 2: 更新 README 安装说明**

在 README 的安装/quick start 段加一行：

```markdown
可选时序存储（Parquet + DuckDB）：`pip install clousight-bench[store]`
```

- [ ] **Step 3: commit**

```bash
git add docs/architecture.md README.md
git commit -m "docs(core): 数据契约、store 布局与 enricher 扩展点"
```

---

### Task 6: clousight-bench-pro 仓骨架（uv workspace + LICENSE + git init）

**Files:**
- Create: `/Users/bowang/IdeaProjects/clousight-bench-pro/pyproject.toml`
- Create: `/Users/bowang/IdeaProjects/clousight-bench-pro/LICENSE`
- Create: `/Users/bowang/IdeaProjects/clousight-bench-pro/README.md`
- Create: `/Users/bowang/IdeaProjects/clousight-bench-pro/.gitignore`

**Interfaces:**
- Produces: uv workspace 根，`members = ["packages/*"]`
- Produces: 空 `packages/` 结构（后续 Task 填充）

- [ ] **Step 1: 建目录 + workspace 根 pyproject**

```bash
mkdir -p /Users/bowang/IdeaProjects/clousight-bench-pro/packages
```

`/Users/bowang/IdeaProjects/clousight-bench-pro/pyproject.toml`：

```toml
[tool.uv.workspace]
members = ["packages/*"]

[tool.uv.sources]
clousight-bench = { path = "../clousight-bench", editable = true }

[tool.ruff]
line-length = 110
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "I", "W", "UP"]

[tool.pytest.ini_options]
testpaths = ["packages"]
```

- [ ] **Step 2: LICENSE（专有）**

`/Users/bowang/IdeaProjects/clousight-bench-pro/LICENSE`：

```text
Copyright (c) 2026 Clousight (云计算指北). All Rights Reserved.

This repository contains PROPRIETARY and CONFIDENTIAL software. It is NOT
open source. No license is granted to use, copy, modify, or distribute any
part of this repository without a separate written commercial agreement with
the copyright holder.

The open-source framework it plugs into (clousight-bench) is licensed
separately under Apache-2.0; this repository is not.
```

- [ ] **Step 3: README + .gitignore**

`/Users/bowang/IdeaProjects/clousight-bench-pro/README.md`：

```markdown
# clousight-bench-pro （专有 · 商业插件）

> **Proprietary — All Rights Reserved.** 非开源。开源核心见 `clousight-bench`（Apache-2.0）。

云计算指北 · Clousight Bench 的商业插件多模块仓。各包通过开源核心的 entry point
与数据契约（`PLUGIN_API_VERSION`、Parquet 长表）单向依赖 core，core 不反向依赖本仓。

## 包

| 包 | 作用 | 状态 |
|----|------|------|
| `cb-pricing` | 资源用量 → 成本预估（`ResultEnricher`） | 接真 |
| `cb-samplers` | 高频采样，产 `sample`/`artifact` 事件 | 可运行骨架 |
| `cb-dataservice` | rollup 降采样 + 托管存储 | 可运行骨架 |
| `cb-adapters-enterprise` | 信创/私有云 adapter | 占位（NotWired） |

## 开发

```bash
uv sync            # 安装 workspace 全部包 + 本地 clousight-bench
uv run pytest
```
```

`/Users/bowang/IdeaProjects/clousight-bench-pro/.gitignore`：

```gitignore
__pycache__/
*.pyc
.venv/
dist/
build/
*.egg-info/
.uv/
uv.lock
.pytest_cache/
.ruff_cache/
runs/
results/
```

- [ ] **Step 4: git init + 首次提交（本地，无 remote）**

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro
git init
git add .
git commit -m "chore: clousight-bench-pro uv workspace 骨架（专有商业插件仓）"
```
Expected: 提交成功；`git remote -v` 为空（不 push）。

---

### Task 7: cb-pricing（ResultEnricher 接真）

**Files:**
- Create: `/Users/bowang/IdeaProjects/clousight-bench-pro/packages/cb-pricing/pyproject.toml`
- Create: `.../cb-pricing/src/cb_pricing/__init__.py`
- Create: `.../cb-pricing/src/cb_pricing/enricher.py`
- Create: `.../cb-pricing/src/cb_pricing/data/pricing.json`
- Test: `.../cb-pricing/tests/test_pricing_enricher.py`

**Interfaces:**
- Consumes: `clousight_bench.core.plugin.ResultEnricher`、`ResultRecord`
- Produces: `cb_pricing.enricher.PricingEnricher`（`name = "cb-pricing"`），entry point `clousight_bench.enrichers`
- Produces: `enrich(record)` 读 `record.metrics` 中资源用量键（`vcpu_hours` / `tokens_1k` / `gb_month`）× 单价，写 `record.metrics["cost_usd"]` 与 `record.raw["pricing_breakdown"]`

- [ ] **Step 1: 包 pyproject + 定价数据**

`packages/cb-pricing/pyproject.toml`：

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "cb-pricing"
version = "0.1.0"
description = "Clousight Bench Pro: proprietary pricing enricher (resource usage -> cost estimate)."
requires-python = ">=3.10"
dependencies = ["clousight-bench>=1.0,<2.0"]

[project.optional-dependencies]
dev = ["pytest>=8.0", "ruff>=0.4"]

[project.entry-points."clousight_bench.enrichers"]
cb-pricing = "cb_pricing.enricher:PricingEnricher"

[tool.hatch.build.targets.wheel]
packages = ["src/cb_pricing"]
```

`packages/cb-pricing/src/cb_pricing/data/pricing.json`（专有价目，与开源 mock 分离）：

```json
{
  "schema": "cb-pricing/1.0",
  "currency": "USD",
  "units": {
    "vcpu_hours": {"metric_key": "vcpu_hours"},
    "tokens_1k": {"metric_key": "tokens_1k"},
    "gb_month": {"metric_key": "gb_month"}
  },
  "prices": [
    {"provider": "aws", "service": "agent-runtime", "unit": "vcpu_hours", "region": "us-east-1", "price": 0.0895, "source": "aws public pricing 2026-07"},
    {"provider": "aliyun", "service": "agent-runtime", "unit": "vcpu_hours", "region": "cn-hangzhou", "price": 0.062, "source": "aliyun public pricing 2026-07"},
    {"provider": "azure", "service": "agent-runtime", "unit": "tokens_1k", "region": "eastus", "price": 0.002, "source": "azure public pricing 2026-07"}
  ]
}
```

- [ ] **Step 2: 写失败测试**

`packages/cb-pricing/tests/test_pricing_enricher.py`：

```python
from clousight_bench.core.schema import ResultRecord, utc_now
from cb_pricing.enricher import PricingEnricher


def _rec(platform, metrics):
    return ResultRecord(
        domain="agent-runtime", task_id="T1.3", platform=platform, run_id="r",
        started_at=utc_now(), finished_at=utc_now(),
        config_hash="sha256:x", evidence_layer="C", metrics=metrics,
    )


def test_cost_computed_from_vcpu_hours():
    rec = _rec("aws", {"vcpu_hours": 10, "service": "agent-runtime", "region": "us-east-1"})
    out = PricingEnricher().enrich(rec)
    assert out.metrics["cost_usd"] == round(10 * 0.0895, 6)
    breakdown = out.raw["pricing_breakdown"]
    assert breakdown[0]["unit"] == "vcpu_hours"
    assert breakdown[0]["qty"] == 10
    assert breakdown[0]["unit_price"] == 0.0895


def test_uncovered_usage_notes_but_does_not_crash():
    rec = _rec("unknown-cloud", {"vcpu_hours": 5, "service": "agent-runtime"})
    out = PricingEnricher().enrich(rec)
    assert out.metrics["cost_usd"] == 0.0
    assert "uncovered" in out.notes.lower()


def test_enricher_name():
    assert PricingEnricher().name == "cb-pricing"
```

- [ ] **Step 3: 运行确认失败**

Run: `cd /Users/bowang/IdeaProjects/clousight-bench-pro && uv run pytest packages/cb-pricing -v`
Expected: FAIL（`ModuleNotFoundError: cb_pricing`）

- [ ] **Step 4: 实现 enricher**

`packages/cb-pricing/src/cb_pricing/__init__.py`：

```python
"""cb-pricing: proprietary pricing enricher for Clousight Bench."""
```

`packages/cb-pricing/src/cb_pricing/enricher.py`：

```python
"""PricingEnricher: turn resource-usage metrics into a cost estimate.

Proprietary. Reads the pinned pricing dataset (data/pricing.json) and multiplies
declared usage metrics by unit prices. Never invents numbers: usage it cannot
price is reported in notes and excluded from cost_usd.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clousight_bench.core.plugin import ResultEnricher
from clousight_bench.core.schema import ResultRecord

_DATA = Path(__file__).parent / "data" / "pricing.json"


class PricingEnricher(ResultEnricher):
    name = "cb-pricing"

    def __init__(self) -> None:
        self._prices: list[dict[str, Any]] = json.loads(_DATA.read_text(encoding="utf-8"))["prices"]

    def _lookup(self, provider: str, service: str, unit: str, region: str | None) -> dict | None:
        matches = [
            p for p in self._prices
            if p["provider"] == provider and p["service"] == service and p["unit"] == unit
            and (region is None or p["region"] == region)
        ]
        return matches[0] if matches else None

    def enrich(self, record: ResultRecord) -> ResultRecord:
        provider = record.platform.split("-")[0]
        service = str(record.metrics.get("service", record.domain))
        region = record.metrics.get("region")
        breakdown: list[dict[str, Any]] = []
        uncovered: list[str] = []
        total = 0.0
        for unit in ("vcpu_hours", "tokens_1k", "gb_month"):
            qty = record.metrics.get(unit)
            if qty is None:
                continue
            price = self._lookup(provider, service, unit, region)
            if price is None:
                uncovered.append(unit)
                continue
            subtotal = round(qty * price["price"], 6)
            total += subtotal
            breakdown.append({
                "unit": unit, "qty": qty, "unit_price": price["price"],
                "subtotal": subtotal, "region": price["region"], "price_source": price["source"],
            })
        record.metrics["cost_usd"] = round(total, 6)
        record.raw["pricing_breakdown"] = breakdown
        if uncovered:
            note = f"pricing: uncovered units for {provider}/{service}: {', '.join(uncovered)}"
            record.notes = (record.notes + " | " + note).strip(" |")
        return record
```

- [ ] **Step 5: 运行确认通过**

Run: `cd /Users/bowang/IdeaProjects/clousight-bench-pro && uv sync && uv run pytest packages/cb-pricing -v`
Expected: PASS

- [ ] **Step 6: 验证 enricher 被 core 自动发现（集成）**

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro
uv run python -c "from clousight_bench.core.registry import load_enrichers; print([e.name for e in load_enrichers()])"
```
Expected: 输出包含 `'cb-pricing'`

- [ ] **Step 7: lint + commit**

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro
uv run ruff check packages/cb-pricing
git add packages/cb-pricing
git commit -m "feat(cb-pricing): 资源用量→成本预估 enricher（专有定价数据）"
```

---

### Task 8: cb-samplers（高频采样可运行骨架）

**Files:**
- Create: `.../cb-samplers/pyproject.toml`
- Create: `.../cb-samplers/src/cb_samplers/__init__.py`
- Create: `.../cb-samplers/src/cb_samplers/sampler.py`
- Create: `.../cb-samplers/workloads/synthetic-sampler/manifest.yaml`
- Create: `.../cb-samplers/workloads/synthetic-sampler/run.py`
- Test: `.../cb-samplers/tests/test_sampler.py`

**Interfaces:**
- Produces: `cb_samplers.sampler.HighFreqSampler(series_name, interval_s)` with `.collect(callback, count) -> None`（每次调用 callback 取值并 `print` 一行 `{"type":"sample",...}`）
- Produces: 示例 workload 目录，遵守 core WorkloadEngine 协议（stdout JSONL，末行 `result`）

- [ ] **Step 1: 包 pyproject**

`packages/cb-samplers/pyproject.toml`：

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "cb-samplers"
version = "0.1.0"
description = "Clousight Bench Pro: high-frequency samplers emitting sample/artifact events."
requires-python = ">=3.10"
dependencies = ["clousight-bench>=1.0,<2.0"]

[project.optional-dependencies]
dev = ["pytest>=8.0", "ruff>=0.4"]

[tool.hatch.build.targets.wheel]
packages = ["src/cb_samplers"]
```

- [ ] **Step 2: 写失败测试**

`packages/cb-samplers/tests/test_sampler.py`：

```python
import json
from cb_samplers.sampler import HighFreqSampler


def test_sampler_emits_sample_lines(capsys):
    values = iter([10.0, 11.0, 12.0])
    sampler = HighFreqSampler(series_name="latency_ms", interval_s=0)
    sampler.collect(lambda: next(values), count=3)
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    events = [json.loads(l) for l in lines]
    assert all(e["type"] == "sample" and e["series"] == "latency_ms" for e in events)
    assert [e["value"] for e in events] == [10.0, 11.0, 12.0]
    assert all("t" in e for e in events)
```

- [ ] **Step 3: 运行确认失败**

Run: `cd /Users/bowang/IdeaProjects/clousight-bench-pro && uv run pytest packages/cb-samplers -v`
Expected: FAIL（`ModuleNotFoundError: cb_samplers`）

- [ ] **Step 4: 实现 sampler + 示例 workload**

`packages/cb-samplers/src/cb_samplers/__init__.py`：

```python
"""cb-samplers: high-frequency sampling helpers for Clousight Bench workloads."""
```

`packages/cb-samplers/src/cb_samplers/sampler.py`：

```python
"""HighFreqSampler: emit `sample` protocol events at a fixed interval.

Proprietary. Wraps a value-producing callback and prints one JSONL sample event
per tick to stdout, consumed by the core WorkloadEngine and externalized to
series.parquet. Real cloud sampling (GPU util, token-level cost, cold-start
decomposition) plugs in as the callback; this class only owns the protocol.
"""
from __future__ import annotations

import json
import time
from typing import Callable


class HighFreqSampler:
    def __init__(self, series_name: str, interval_s: float = 0.01) -> None:
        self.series_name = series_name
        self.interval_s = interval_s

    def collect(self, callback: Callable[[], float], count: int) -> None:
        for _ in range(count):
            value = float(callback())
            event = {"type": "sample", "series": self.series_name, "t": time.time(), "value": value}
            print(json.dumps(event), flush=True)
            if self.interval_s:
                time.sleep(self.interval_s)
```

`packages/cb-samplers/workloads/synthetic-sampler/manifest.yaml`：

```yaml
name: synthetic-sampler
version: 0.1.0
entrypoint: ./run.py
params:
  count: {type: integer, default: 20}
metrics: []
```

`packages/cb-samplers/workloads/synthetic-sampler/run.py`（可执行，`chmod +x`）：

```python
#!/usr/bin/env python3
"""Synthetic sampler workload: emits `count` latency samples then a result line."""
import json
import random
import sys

from cb_samplers.sampler import HighFreqSampler


def main() -> int:
    count = 20
    if "--params" in sys.argv:
        params = json.loads(open(sys.argv[sys.argv.index("--params") + 1]).read())
        count = int(params.get("count", 20))
    HighFreqSampler("latency_ms", interval_s=0).collect(lambda: random.uniform(50, 150), count)
    print(json.dumps({"type": "result", "ok": True}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: 运行确认通过**

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro
chmod +x packages/cb-samplers/workloads/synthetic-sampler/run.py
uv sync && uv run pytest packages/cb-samplers -v
```
Expected: PASS

- [ ] **Step 6: 集成验证（workload → series）**

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro
uv run python -c "
from pathlib import Path
from clousight_bench.core.workload import WorkloadEngine
wl = WorkloadEngine(Path('packages/cb-samplers/workloads/synthetic-sampler'))
res = wl.run(params={'count': 5})
print('ok', res.ok, 'points', len(res.series.get('latency_ms', [])))
"
```
Expected: `ok True points 5`

- [ ] **Step 7: lint + commit**

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro
uv run ruff check packages/cb-samplers
git add packages/cb-samplers
git commit -m "feat(cb-samplers): HighFreqSampler + 合成采样 workload（sample 协议骨架）"
```

---

### Task 9: cb-dataservice（rollup 可运行骨架 + 上传 stub）

**Files:**
- Create: `.../cb-dataservice/pyproject.toml`
- Create: `.../cb-dataservice/src/cb_dataservice/__init__.py`
- Create: `.../cb-dataservice/src/cb_dataservice/rollup.py`
- Create: `.../cb-dataservice/src/cb_dataservice/upload.py`
- Create: `.../cb-dataservice/src/cb_dataservice/cli.py`
- Test: `.../cb-dataservice/tests/test_rollup.py`

**Interfaces:**
- Consumes: core `[store]` extra（duckdb+pyarrow）、`series.parquet` 长表
- Produces: `cb_dataservice.rollup.rollup(run_dir: Path, bucket_s: int = 1) -> Path`（读 `series.parquet` → 写同目录 `series_rollup.parquet`，列 `series|bucket|avg|p99|max|n`，返回路径）
- Produces: `cb_dataservice.upload.ObjectStoreUploader`（接口 + `upload()` 抛 NotImplementedError，占位）
- Produces: CLI `cb-dataservice rollup <run_dir>`

- [ ] **Step 1: 包 pyproject（依赖 core[store]）**

`packages/cb-dataservice/pyproject.toml`：

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "cb-dataservice"
version = "0.1.0"
description = "Clousight Bench Pro: rollup/downsampling + managed storage service."
requires-python = ">=3.10"
dependencies = ["clousight-bench[store]>=1.0,<2.0"]

[project.optional-dependencies]
dev = ["pytest>=8.0", "ruff>=0.4"]

[project.scripts]
cb-dataservice = "cb_dataservice.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["src/cb_dataservice"]
```

- [ ] **Step 2: 写失败测试**

`packages/cb-dataservice/tests/test_rollup.py`：

```python
import pyarrow as pa
import pyarrow.parquet as pq

from cb_dataservice.rollup import rollup


def _write_series(run_dir):
    run_dir.mkdir(parents=True, exist_ok=True)
    rows = {
        "run_id": ["r"] * 6, "domain": ["d"] * 6, "task_id": ["t"] * 6,
        "platform": ["p"] * 6, "config_hash": ["h"] * 6,
        "series": ["latency_ms"] * 6,
        "t": [0.1, 0.2, 0.9, 1.1, 1.2, 1.9],
        "value": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
        "unit": [""] * 6,
    }
    pq.write_table(pa.table(rows), run_dir / "series.parquet")


def test_rollup_buckets_reduce_rows(tmp_path):
    run_dir = tmp_path / "run-x"
    _write_series(run_dir)
    out = rollup(run_dir, bucket_s=1)
    assert out.exists()
    table = pq.read_table(out).to_pydict()
    # 6 raw points spanning t in [0,1] -> 2 one-second buckets
    assert len(table["bucket"]) == 2
    assert set(table["series"]) == {"latency_ms"}
    assert "avg" in table and "p99" in table and "max" in table
```

- [ ] **Step 3: 运行确认失败**

Run: `cd /Users/bowang/IdeaProjects/clousight-bench-pro && uv run pytest packages/cb-dataservice -v`
Expected: FAIL（`ModuleNotFoundError: cb_dataservice`）

- [ ] **Step 4: 实现 rollup + upload stub + cli**

`packages/cb-dataservice/src/cb_dataservice/__init__.py`：

```python
"""cb-dataservice: rollup/downsampling + managed storage for Clousight Bench Pro."""
```

`packages/cb-dataservice/src/cb_dataservice/rollup.py`：

```python
"""rollup: downsample a run's series.parquet into time-bucketed aggregates.

Proprietary. Reads the long-table series.parquet produced by the open-core
ResultStore and writes series_rollup.parquet (avg/p99/max per series per bucket)
so the platform renders a chart without scanning every raw sample.
"""
from __future__ import annotations

from pathlib import Path

import duckdb


def rollup(run_dir: Path, bucket_s: int = 1) -> Path:
    run_dir = Path(run_dir)
    src = run_dir / "series.parquet"
    if not src.exists():
        raise FileNotFoundError(f"no series.parquet in {run_dir}")
    out = run_dir / "series_rollup.parquet"
    con = duckdb.connect()
    con.execute(
        """
        COPY (
            SELECT series,
                   CAST(floor(t / ?) AS BIGINT) AS bucket,
                   avg(value) AS avg,
                   quantile_cont(value, 0.99) AS p99,
                   max(value) AS max,
                   count(*) AS n
            FROM read_parquet(?)
            GROUP BY series, bucket
            ORDER BY series, bucket
        ) TO ? (FORMAT PARQUET)
        """,
        [bucket_s, str(src), str(out)],
    )
    return out
```

`packages/cb-dataservice/src/cb_dataservice/upload.py`：

```python
"""Managed object-storage upload (R2/S3) — interface placeholder.

The hosted service uploads rollups + artifacts to object storage and indexes
record metadata in Postgres. Wiring to real buckets is deferred; this defines
the interface so callers can depend on it now.
"""
from __future__ import annotations

from pathlib import Path


class ObjectStoreUploader:
    def __init__(self, bucket: str, prefix: str = "") -> None:
        self.bucket = bucket
        self.prefix = prefix

    def upload(self, path: Path) -> str:
        raise NotImplementedError(
            "managed upload is not wired yet; configure R2/S3 credentials in a future release"
        )
```

`packages/cb-dataservice/src/cb_dataservice/cli.py`：

```python
"""cb-dataservice CLI: cb-dataservice rollup <run_dir> [--bucket-s N]."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cb_dataservice.rollup import rollup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cb-dataservice")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("rollup", help="downsample a run's series.parquet")
    r.add_argument("run_dir")
    r.add_argument("--bucket-s", type=int, default=1)
    args = parser.parse_args(argv)
    if args.command == "rollup":
        out = rollup(Path(args.run_dir), bucket_s=args.bucket_s)
        print(out)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: 运行确认通过**

Run: `cd /Users/bowang/IdeaProjects/clousight-bench-pro && uv sync && uv run pytest packages/cb-dataservice -v`
Expected: PASS

- [ ] **Step 6: lint + commit**

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro
uv run ruff check packages/cb-dataservice
git add packages/cb-dataservice
git commit -m "feat(cb-dataservice): series rollup 降采样 + 上传接口占位 + CLI"
```

---

### Task 10: cb-adapters-enterprise 占位 + 收尾（决策文档勾选 + 全量回归）

**Files:**
- Create: `.../cb-adapters-enterprise/pyproject.toml`
- Create: `.../cb-adapters-enterprise/src/cb_adapters_enterprise/__init__.py`
- Create: `.../cb-adapters-enterprise/src/cb_adapters_enterprise/placeholder.py`
- Modify: `/Users/bowang/IdeaProjects/cloudNew/docs/clousight-bench-open-core-strategy.md`（§10 勾选）

**Interfaces:**
- Produces: `cb_adapters_enterprise.placeholder.NotWiredEnterpriseAdapter`（`__init__` 抛 `NotImplementedError`，说明需私有云凭证）

- [ ] **Step 1: 占位包**

`packages/cb-adapters-enterprise/pyproject.toml`：

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "cb-adapters-enterprise"
version = "0.0.1"
description = "Clousight Bench Pro: enterprise/private-cloud adapters (placeholder)."
requires-python = ">=3.10"
dependencies = ["clousight-bench>=1.0,<2.0"]

[tool.hatch.build.targets.wheel]
packages = ["src/cb_adapters_enterprise"]
```

`packages/cb-adapters-enterprise/src/cb_adapters_enterprise/__init__.py`：

```python
"""cb-adapters-enterprise: private-cloud / 信创 adapters (placeholder)."""
```

`packages/cb-adapters-enterprise/src/cb_adapters_enterprise/placeholder.py`：

```python
"""Placeholder: enterprise adapters need private-cloud credentials to implement.

Deferred to a credentials-gated iteration (see decision record §9 items B/C/I).
"""
from __future__ import annotations


class NotWiredEnterpriseAdapter:
    def __init__(self, *_args, **_kwargs) -> None:
        raise NotImplementedError(
            "enterprise adapters require private-cloud/信创 access; not wired this iteration"
        )
```

- [ ] **Step 2: pro 全量回归**

Run: `cd /Users/bowang/IdeaProjects/clousight-bench-pro && uv sync && uv run pytest -v && uv run ruff check packages`
Expected: 全 PASS + ruff clean

- [ ] **Step 3: core 全量回归**

Run: `cd /Users/bowang/IdeaProjects/clousight-bench && pytest -v && ruff check src tests`
Expected: 全 PASS + ruff clean

- [ ] **Step 4: 勾选决策文档 §10**

在 `cloudNew/docs/clousight-bench-open-core-strategy.md` §10：把
`- [ ] core 落地 §4/§5 ...` 与 `- [ ] 何时新建 clousight-bench-pro 私有仓 ...`
改为 `- [x]`，并在「已拍板（本轮）」行后追加一句：`2026-07-21 实现：core 数据契约 + ResultStore + ResultEnricher；clousight-bench-pro（cb-pricing 接真、cb-samplers/cb-dataservice 骨架、cb-adapters-enterprise 占位）。`

- [ ] **Step 5: commit（两仓）**

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro
git add packages/cb-adapters-enterprise
git commit -m "feat(cb-adapters-enterprise): 占位包（需私有云凭证，本轮 NotWired）"

cd /Users/bowang/IdeaProjects/cloudNew
git add docs/clousight-bench-open-core-strategy.md
git commit -m "docs(bench): 勾选 §10 —— core 数据契约 + pro 仓落地"
```

---

## Self-Review

**1. Spec coverage：**
- spec §2.1 版本锚点 → Task 1 ✅
- spec §2.2 schema 字段 + 容忍 from_dict → Task 1 ✅
- spec §2.3 workload sample/artifact → Task 2 ✅
- spec §2.4 store.py + orchestrator 委托 + 兼容布局 → Task 3 ✅
- spec §2.5 ResultEnricher 扩展点 + registry + orchestrator + CLI → Task 4 ✅
- spec §2.6 pyproject [store] extra → Task 3 Step 5 ✅
- spec §2.7 core 测试（schema/workload/store/enricher）→ Task 1/2/3/4 ✅
- spec §2.8 core 文档 → Task 5 ✅
- spec §3 pro uv workspace + LICENSE + 目录 → Task 6 ✅
- spec §4 cb-pricing 接真 + 专有 pricing.json + breakdown → Task 7 ✅
- spec §5.1 cb-samplers 可运行骨架 → Task 8 ✅
- spec §5.2 cb-dataservice rollup + 上传 stub + CLI → Task 9 ✅
- spec §3.1 cb-adapters-enterprise 占位 → Task 10 ✅
- spec §6.2 DoD（含集成验证）→ Task 7 Step 6、Task 8 Step 6、Task 10 Step 2/3 ✅
- spec §6.3 决策文档勾选 → Task 10 Step 4 ✅

**2. Placeholder scan：** 无 TBD/TODO/“类似 TaskN”；每个 code step 均给完整代码与命令。占位包 `NotWiredEnterpriseAdapter` 是**产品设计上的刻意占位**（spec §3.1 明确本轮不实现），非计划缺口。

**3. Type consistency：**
- `ResultEnricher.enrich(record)->record`、`name` 属性：Task 4 定义，Task 7 `PricingEnricher` 一致使用 ✅
- `HighFreqSampler(series_name, interval_s).collect(callback, count)`：Task 8 定义与测试/ workload 一致 ✅
- `rollup(run_dir, bucket_s=1)->Path`、输出列 `series|bucket|avg|p99|max|n`：Task 9 定义、测试、CLI 一致 ✅
- `ResultStore(results_dir).persist(record)->Path` / `query_series(...)`：Task 3 定义、orchestrator 与测试一致 ✅
- entry-point group 名 `clousight_bench.enrichers`：Task 4 registry 与 Task 7 pyproject 一致 ✅
- Parquet 长表列在 Task 3（写）与 Task 9（读）一致 ✅

无遗留问题。
