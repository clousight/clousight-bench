# TPC-DS benchmark suite — design (slice 1)

**Status:** design locked (brainstormed + user-approved 2026-08-28).
**Goal:** add TPC-DS as clousight-bench's SECOND `BenchmarkSuite`, on a NEW
`data-warehouse` domain with a `duckdb-local` reference platform. This proves the
suite/evaluator contract generalizes beyond agent suites (SWE-bench) to a
completely different domain (OLAP/data) — the strongest validation of the
"benchmark-suite integration contract is the moat" thesis. Cloud/distributed
engines (EMR/Spark, cloud DWH) and "big-data scale" are a deliberately deferred
later phase; this slice is the offline, deterministic, CI-shippable foundation.

## Verified technical facts (empirically confirmed 2026-08-28, duckdb 1.5.4)

- The DuckDB `tpcds` extension is a **core autoloadable** extension. `INSTALL tpcds`
  fetches from the DuckDB extension repository on first use, then caches to
  `~/.duckdb/extensions/<ver>/<plat>/tpcds.duckdb_extension`; subsequent `LOAD tpcds`
  works offline. CI has network (it pip-installs), so `INSTALL tpcds` at prepare
  time is fine. The offline **mock** path uses a bundled fixture and never touches
  the extension.
- `CALL dsdgen(sf := <N>)` generates TPC-DS data at scale factor N (SF1 ≈ 5.6s,
  24 tables). `sf` is a `DOUBLE` named param.
- `SELECT query_nr, query FROM tpcds_queries()` returns the 99 query texts.
- `PRAGMA tpcds(<N>)` runs query N and returns its result rows. **All 99 run in
  ~1.3s at SF1 with zero failures**, and results are **deterministic** (stable
  normalized digest across repeated runs in a pinned duckdb version).
- `tpcds_answers()` is **NOT usable**: it takes no parameters, defaults to SF10,
  and errors ("Don't have TPC-DS answers for SF 10"). Correctness therefore uses a
  **pinned reference fixture** (below), not DuckDB's answer function.

## Architecture

### New domain: `data-warehouse`
A minimal `DomainPack` (mirrors `agent-runtime`'s post-pivot shape): `tasks()`
returns `{}` (suite-first — all runs are `suite:<id>` jobs); it declares the
`duckdb-local` platform via a reference adapter. Registered under the `domains`
entry-point group. Carries `DOCS` + `requires_plugin_api` like the existing pack.

### Reference platform/adapter: `duckdb-local`
A minimal reference `ProviderAdapter` mirroring `agent-runtime`'s `local_sim`:
- `preflight`: assert `duckdb` importable and the `tpcds` extension is
  loadable (`INSTALL tpcds; LOAD tpcds`), fail-loud with an actionable hint if not.
- `setup`/`teardown`: no-op (or manage a temp db dir); the suite owns the DuckDB
  work.
- The adapter is deliberately thin — it exists so the orchestrator's
  RESOLVE/PREFLIGHT lifecycle has a platform to bind to; the SUT work lives in the
  suite. `Target` for a `duckdb-local` run is `mode=runtime`(or the mode the suite
  contract expects), `mock=<bool>`; no region/endpoint/credentials.

### Suite: `tpc-ds` (`suites/tpc_ds/`)
Package layout mirrors `suites/swe_bench/`: `suite.py`, `evaluator.py`,
`__init__.py`, `fixtures/`.

`TpcdsSuite(BenchmarkSuite)`:
- `suite_id = "tpc-ds"`; `suite_version` pins the DuckDB + tpcds-extension version
  and the reference-fixture generation (e.g. `"duckdb-1.5.4/tpcds/sf1-ref-v1"`) so
  the benchmark fingerprint moves whenever the engine or reference changes.
- `resolve(cfg, assets) -> DatasetHandle`: read `scale_factor` (default **1**) and
  `query_ids` (default all 99; a subset via `--param`) from cfg; **offline/cheap**
  (no data gen here). `DatasetHandle.version` = the pinned tag + SF; `digest` folds
  SF + query set + reference-fixture digest into the benchmark fingerprint.
- `prepare(target, dataset, driver) -> EnvHandle`: open a DuckDB connection,
  `INSTALL tpcds; LOAD tpcds`, `CALL dsdgen(sf := SF)`. Carry the connection/db
  path in the (suite-private) `EnvHandle.payload`.
- `run(target, env, driver) -> RawArtifacts`: for each query in the query set, run
  `PRAGMA tpcds(nr)`, capturing per-query `latency_ms`, `row_count`, and a
  normalized `result_digest`. Write a `queries.json` (list of
  `{query_nr, latency_ms, row_count, result_digest}`) + a `summary.json`
  (`{scale_factor, duckdb_version, extension_version, query_count}`) into
  `RawArtifacts.dir`; populate `manifest`.
- `teardown(env)`: close the connection, drop the temp db (best-effort).
- `mock_artifacts(cfg) -> RawArtifacts`: copy a small committed fixture
  (`fixtures/mock/` — a handful of queries' `queries.json` + `summary.json`) so the
  whole stage machine + evaluator run offline with no DuckDB. Mirrors swe-bench's
  `mock_artifacts`.

**Result-digest normalization** (must be cross-platform stable — the reference is
captured once and must match in CI/linux and locally/macOS): round every numeric
(float/decimal) to a fixed precision (2 dp for decimals; a documented rule for
floats), render each row to a canonical string, **sort the rows** (order-
independent), then sha256. If cross-platform digest stability proves fragile, fall
back to a `(row_count, column_count)` match per query (bulletproof, weaker signal)
— the implementation must VERIFY the committed reference passes in CI (linux)
before the slice is accepted.

**Reference fixture** (`fixtures/reference/sf1_digests.json`): the pinned
per-query expected `result_digest` (and `row_count`) at SF1, captured once against
the pinned duckdb+extension version. Correctness = match against this. It is a
deterministic **reproducibility/regression** reference (DuckDB's tpcds is a
recognized implementation; we pin its SF1 output), NOT an externally-audited TPC
answer — labeled honestly (see evaluator). Only meaningful at SF1; at SF≠1
correctness is not asserted.

### Evaluator: `official-tpcds-evaluator` (`OfficialTpcdsEvaluator`)
- `evaluator_id = "official-tpcds-evaluator"`; `supports("tpc-ds", product)` →
  True (product-agnostic for now; the duckdb-local product).
- `evaluate(raw) -> dict[str, Measurement]`, a pure function reading only the
  artifacts. **Namespace + `official` follow the framework's conformance
  contract** (verified during implementation): the suite's *official* evaluator
  must emit ONLY keys namespaced `"<suite_id>."` (i.e. `tpc-ds.`) and every
  measurement must carry `official=True`. In this framework `official` is a
  *provenance* flag ("emitted by the recognized evaluator"), NOT an audit claim —
  determinism-vs-drift is carried by `reproducibility_class`, and the audited
  QphDS is simply not emitted. (This matches swe-bench, whose environmental
  `swe-bench.cost_per_resolved` is `official=True`.)
  - `tpc-ds.queries_passed`: ratio of queries whose `result_digest` matches the
    SF1 reference. `reproducibility_class="deterministic"`, `official=True`,
    `notes` naming the pinned engine/reference ("not an audited TPC answer").
    **Emitted only when SF==1** (reference is SF1-only); omitted otherwise.
  - `tpc-ds.geomean_latency_ms`: geometric mean of per-query latency.
    `reproducibility_class="environmental"`, `official=True`.
  - `tpc-ds.total_runtime_ms`: sum of per-query latency.
    `reproducibility_class="environmental"`, `official=True`.
  - (Any malformed artifact → omit the affected measurement, mirroring swe-bench's
    fail-safe cost metric.)
- The full audited **QphDS composite** (load + power single-stream + throughput
  multi-stream + audit rules) is explicitly **out of scope** — we never claim an
  official QphDS number without an audited multi-stream run.

## Packaging / entry points / deps
- New entry points: `benchmark_suites: tpc-ds = ...suites.tpc_ds.suite:TpcdsSuite`;
  `evaluators: official-tpcds-evaluator = ...suites.tpc_ds.evaluator:OfficialTpcdsEvaluator`;
  `domains: data-warehouse = ...domains.data_warehouse:DataWarehouseDomain`.
- `duckdb` is already an optional dep (the `[store]` extra). Add a `[tpcds]` extra
  (`duckdb`) so `pip install clousight-bench[tpcds]` pulls what the suite needs;
  the suite imports duckdb lazily and fails loud with an install hint if absent
  (mirroring how aliyun/aws SDKs are handled).

## CI
Add to the modernized `test` job (see the just-landed CI): a
`conformance --suite tpc-ds` line, and a real offline `duckdb-local` smoke —
`csbench run --domain data-warehouse --task suite:tpc-ds --platform duckdb-local`
with a small `--param query_ids=<subset>` at SF1 (full 99 is ~1.3s, so the full
run is also acceptable; use a subset if the ~6s dsdgen dominates). Assert the
emitted record is schema 0.3 (reuse the existing assertion; scope the glob past
`artifacts/`/`aggregates/`). The `[tpcds]`/`[store]` extra must be in the CI
install line so duckdb is present.

## Testing
- Unit: evaluator over the mock fixture (queries_passed / geomean / total from a
  known `queries.json`); the SF≠1 omission of `queries_passed`; malformed-artifact
  omission.
- Suite: `mock_artifacts` produces a valid `RawArtifacts` that passes the stage
  machine end-to-end (mirror `test_swe_submit_e2e.py`'s mock e2e).
- Integration (may be `slow`): a real `duckdb-local` SF1 run of a query subset,
  asserting `queries_passed == 1.0` against the committed reference and that
  latency measurements are present + positive.
- Conformance: the suite+evaluator pass `csbench conformance --suite tpc-ds`.

## Known constraints (carried forward, honest)
- Correctness verification is **SF1-only** and is a pinned-reference reproducibility
  check (official=False), not an audited TPC answer.
- No official QphDS composite metric in this slice.
- "Big-data scale" and distributed engines (EMR/Spark, cloud DWH) are a later
  phase; `duckdb-local` is a single-node reference.
- Reference-digest cross-platform stability is an implementation risk with a
  documented fallback (row-count match); must be validated in CI before acceptance.
