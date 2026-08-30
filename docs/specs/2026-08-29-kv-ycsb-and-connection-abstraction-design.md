# Key-value domain (YCSB) + the SUT-connection abstraction — design

**Status:** design (2026-08-29). First increment of the "接入端更抽象" program:
generalize the system-under-test connection so a suite runs against a local
reference, an already-running service (config-connect), or a cloud-provisioned
engine — established here with a NEW `key-value` domain wrapping the recognized
**YCSB** benchmark (Apache-2.0). SQL/OLAP cross-engine connect and
cloud-provisioned backends are deferred (they need cross-dialect query sets and
a cloud account respectively).

## Why KV/YCSB is the right place to establish the abstraction

The "config-connect to an existing service" pattern is awkward for the SQL/OLAP
suites (TPC-DS/H): their query text is DuckDB-dialect, so pointing at Postgres/
Trino/cloud-DWH needs per-engine query rewrites (what ClickBench maintains by
hand) — a heavy, cloud-phase effort. For KV it is natural: operations are
get/put/scan, and **YCSB's own binding mechanism already IS the connection
abstraction** — `binding=basic` (in-memory, no real DB), `binding=redis` +
host/port, or a cloud-KV binding. We surface YCSB's binding + endpoint through
the framework's existing `Target` config. This matches the repo's own stated
intent (see `resources/workloads/ycsb-wrapper/manifest.yaml`: "Borrowing a mature
load generator instead of reimplementing it is the intended pattern for
database / KV / messaging domains").

## The abstraction (pattern, not new core machinery)

The pattern already exists for agent-runtime (`RuntimeTransport` seam +
`Target.handle` + mock/real + local/cloud adapters). We apply the SAME shape to
the `key-value` domain — no new core contracts, just a new domain whose adapters
resolve a YCSB connection from `Target`:

- `Target.mock` → offline mock path (bundled fixture; no YCSB, no backend).
- `Target` (real) → the adapter maps config to a YCSB invocation: which
  **binding** (basic / redis / …) and which **endpoint** (host/port/credentials
  from `Target.endpoint` / `Target.credentials_ref` / target props).

Adapters on the new domain:
- `ycsb-local` — `status="reference"`, `binding=basic` (YCSB's in-memory no-op
  DB). Proves the pipeline end-to-end with no external datastore. (Real path
  still needs the YCSB tool; offline is the mock path.)
- `ycsb-endpoint` — `status="experimental"`, **config-connect to an already-
  running service**: `binding` (default `redis`) + endpoint from `Target`. This
  is the "配置接入即可" deliverable.
- Cloud-provisioned KV (Aliyun/AWS managed KV) — LATER, same seam, gated on a
  cloud account.

## Suite: `ycsb` (`suites/ycsb/`)

Wraps the real upstream YCSB tool (load phase + run phase), like SWE-bench wraps
the Docker harness. The real path needs YCSB on `PATH` or `YCSB_HOME` (Java ≥ 11)
— documented + gated; the offline/CI path is `mock_artifacts()`.

- `suite_id = "ycsb"`; `suite_version` pins the YCSB distribution version the
  reference fixture came from.
- `resolve(cfg)`: pick `workload` (workloada..f, default workloada),
  `recordcount`, `operationcount` (offline; no tool). DatasetHandle version/digest
  folds workload + counts.
- `prepare(target)`: mock → empty EnvHandle. Real → resolve YCSB binary +
  binding + endpoint; (load phase may run here or in run()).
- `run(target)`: mock → `mock_artifacts`. Real → invoke YCSB load then run,
  capture stdout into `ycsb_output.txt`; RawArtifacts manifest = `{"ycsb_output":
  ...}` (+ a `summary.json` with workload/binding/counts/ycsb_version).
- `teardown`: best-effort.
- `mock_artifacts(cfg)`: copy a bundled real-format YCSB output fixture
  (`fixtures/mock/ycsb_output.txt` + `summary.json`). Offline, no tool.

## Evaluator: `official-ycsb-evaluator`

Pure function parsing `ycsb_output.txt` (the standard `[OVERALL]/[READ]/[UPDATE]`
lines) → namespaced `ycsb.*` Measurements, all `official=True` (provenance flag,
per the conformance contract), all `reproducibility_class="environmental"` (YCSB
is a throughput/latency benchmark):
- `ycsb.throughput_ops` (ops/sec), `ycsb.overall_runtime_ms`,
  `ycsb.read_p99_us`, `ycsb.update_p99_us` (whichever the output contains; a
  metric absent from the output is omitted — fail-safe).
- **No correctness dimension** — YCSB measures performance, not answer
  correctness; we honestly emit only performance (unlike the SQL suites'
  `queries_passed`). `supports(suite_id, product)` → `suite_id == "ycsb"`.

## Packaging / deps
- Entry points: `benchmark_suites: ycsb`, `evaluators: official-ycsb-evaluator`,
  `domains: key-value`.
- No pip extra (YCSB is a JVM tool, not a Python package). The real path fails
  loud with an actionable hint ("install YCSB; set YCSB_HOME or put ycsb on
  PATH") when the tool is absent; `mock_artifacts` needs nothing.

## CI
Add `conformance --domain key-value --platform ycsb-local`,
`conformance --suite ycsb`, and an offline `csbench run --domain key-value
--benchmark ycsb --platform ycsb-local --config <mock>` to the existing smoke
lane (schema-0.3 assertion already scoped). No Java in CI → only the mock path
runs (like SWE-bench's Docker-gated real path).

## Testing
- Suite: mock_artifacts valid; resolve digest stable/sensitive; run() delegates
  to mock on `target.mock`.
- Evaluator: parse a real-format YCSB output fixture → correct throughput/latency
  values; absent metric omitted; malformed output → empty/omit, never raises.
- e2e: mock run through the orchestrator → completed, `ycsb.*` measurements,
  provenance.suite_id == "ycsb".
- The real-YCSB path is structured + the parser unit-tested against a captured
  output fixture; a live run is gated/skipped without the YCSB tool.

## Honest scope (slice 1)
- Real numbers need the real YCSB tool + a real backend; offline is the mock
  path. The `ycsb-local` reference exercises YCSB's own machinery (binding=basic),
  not a real datastore.
- Config-connect (`ycsb-endpoint`) targets an existing KV service (Redis-protocol
  first). Cloud-provisioned managed-KV adapters are deferred (cloud account).
- This establishes the connection-abstraction pattern for C domains; OLAP
  cross-engine connect + cloud provisioning remain the big-data-scale phase.
