# OLTP domain (TPC-C via BenchBase) — design

**Status:** design (2026-08-29). Third data domain, following the established
"wrap the recognized upstream tool + local-reference / config-connect adapters"
pattern (see the YCSB / key-value increment). Adds transactional (OLTP) coverage:
OLAP (data-warehouse) + KV (key-value) + **OLTP (transactional-db)**.

## Choice + licensing
TPC-C is the classic OLTP benchmark. We run it via **BenchBase** (CMU-DB, the
successor to OLTPBench) — verified **Apache-2.0** (commercial-safe, unlike
sysbench/HammerDB which are GPL). BenchBase drives TPC-C against a JDBC database
and is the industry-standard permissive TPC-C harness.

## Architecture (mirrors YCSB)
New `transactional-db` domain (`tasks()` empty, suite-first). The SUT connection
is BenchBase's `dbtype` + JDBC endpoint, resolved from the run `Target`:
- `benchbase-local` — `status="reference"`, `dbtype=sqlite` (embedded, no server):
  a provider-less local reference. (Real path needs the BenchBase build; offline
  is the mock path.)
- `jdbc-endpoint` — `status="experimental"`, **config-connect to an already-
  running database**: `dbtype` (default `postgres`) + JDBC endpoint/credentials
  from `Target.endpoint` / `Target.credentials_ref`. The "配置接入已有服务" path.
- Cloud-managed RDBMS backends attach later on the same seam.

## Suite: `tpc-c` (`suites/tpc_c/`)
Wraps BenchBase (create + load + execute phases), like the ycsb suite wraps the
YCSB tool. Real path needs the BenchBase distribution (Java) via `$BENCHBASE_HOME`
or a `benchbase` launcher — documented + gated; offline/CI is `mock_artifacts()`.
- `suite_id = "tpc-c"`; `suite_version` pins the BenchBase version the fixture
  reflects.
- `resolve(cfg)`: `scalefactor` (warehouses, default 1), `terminals` (default 1),
  `time` seconds (default 60) — offline; no tool. Digest folds these + version.
- `prepare(target)`: mock → empty EnvHandle. Real → resolve the BenchBase
  launcher + dbtype + JDBC endpoint (from Target); render/point at a BenchBase
  config XML.
- `run(target)`: mock → `mock_artifacts`. Real → invoke BenchBase
  (`--create=true --load=true --execute=true -b tpcc -c <config>`), locate the
  produced `*.summary.json`, copy it into RawArtifacts (manifest key `summary`).
- `teardown`: best-effort (drop the sqlite temp file).
- `mock_artifacts(cfg)`: copy a bundled real-format BenchBase
  `summary.json` fixture. Offline, no tool.

## Evaluator: `official-tpcc-evaluator`
Pure function parsing BenchBase's `summary.json` → namespaced `tpc-c.*`
Measurements, all `official=True` (provenance flag) + `environmental` (OLTP is a
throughput/latency benchmark; no answer-correctness dimension):
- `tpc-c.throughput_req_per_sec` (from `"Throughput (requests/second)"`)
- `tpc-c.goodput_req_per_sec` (from `"Goodput (requests/second)"`)
- `tpc-c.p99_latency_us`, `tpc-c.median_latency_us`, `tpc-c.avg_latency_us`
  (from the `"Latency Distribution"` map's `"99th Percentile"/"Median"/"Average
  Latency (microseconds)"` keys)
- Any absent key omitted (fail-safe); missing/broken summary → `{}`, never raises.
- We do NOT claim the audited **tpmC** (new-order txns/min): that needs a full
  TPC-C audit. We report BenchBase's throughput/goodput/latency honestly.
- `supports(suite_id, product)` → `suite_id == "tpc-c"`.

## Packaging / deps
- Entry points: `benchmark_suites: tpc-c`, `evaluators: official-tpcc-evaluator`,
  `domains: transactional-db`. No pip extra (BenchBase is a JVM tool). Real path
  fails loud with an actionable hint when the tool is absent; `mock_artifacts`
  needs nothing.

## CI / testing / honesty
- CI: `conformance --domain transactional-db --platform benchbase-local`,
  `conformance --suite tpc-c`, and an offline `csbench run ... --platform
  benchbase-local --config <mock>` (no Java in CI → mock path only, like SWE-bench
  Docker-gated).
- Tests: domain/adapters, suite (mock + resolve + fail-loud without tool),
  evaluator (parse a real-format summary.json fixture; absent key omitted;
  broken → {}), mock e2e through the orchestrator.
- Honest scope: real numbers need the BenchBase build + a database; offline is the
  mock path. Config-connect (`jdbc-endpoint`) targets an existing DB. Cloud-managed
  RDBMS + audited tpmC are deferred.
