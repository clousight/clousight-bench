# TPC-DS benchmark suite — implementation plan (slice 1)

Design: `docs/specs/2026-08-28-tpcds-suite-design.md` (read it first).
Builds TPC-DS as the 2nd `BenchmarkSuite`, on a new `data-warehouse` domain with a
`duckdb-local` reference platform. Offline, deterministic, CI-shippable. Cloud
engines + big-data scale are a deferred later phase.

## Global Constraints

- **Quality gates (every task, before commit) — run with `--no-sync`:**
  `uv run --no-sync ruff check src tests`, `uv run --no-sync mypy src` (expect
  "Success ... N source files"; if a numpy `.pyi` "Type statement" error appears run
  `uv pip install "numpy<2.5" --quiet` once, or ignore if numpy absent),
  `uv run --no-sync pytest -q` (current baseline on this branch: **1227 passed,
  1 skipped**; zero failures = green; each task adds tests so the pass count grows).
  `tests/test_layering.py` must stay green.
- **After editing `pyproject.toml` entry points / extras:** `uv sync --all-extras
  --frozen` before running tests; do NOT commit `uv.lock` churn
  (`git checkout -- uv.lock`).
- **duckdb** is available via the `[store]` extra AND a new `[tpcds]` extra (Task 1).
  Import duckdb LAZILY inside functions (never at module top level in src outside the
  suite's own runtime paths) and fail loud with an install hint if absent — mirror how
  aliyun/aws SDKs are handled. The suite/adapter modules must import cleanly WITHOUT
  duckdb installed (so the plugin registry can import them; only the real run needs it).
- **Docs in the same change** where a task touches user-facing surface (README status,
  ROADMAP, a docs page). Run `uv run --no-sync python scripts/gen_docs.py` and commit
  any drift. Do NOT edit dated `docs/plans/` or `docs/specs/` files.
- **Commit hygiene:** stage explicit paths only (never `-A`/`.`); verify
  `git diff --cached --stat`; conventional commit messages per `git log --oneline -10`.
  Do NOT commit `docs/plans/2026-08-28-tpcds-suite.md` or the design doc inside a task
  (the controller owns those). Do NOT commit `uv.lock`.

## Verified DuckDB facts (do not re-derive — confirmed 2026-08-28, duckdb 1.5.4)

- `INSTALL tpcds; LOAD tpcds` — tpcds is a **core autoloadable** extension; INSTALL
  fetches on first use then caches to `~/.duckdb/extensions/<ver>/<plat>/`; LOAD is
  offline thereafter. CI has network.
- `CALL dsdgen(sf := 1)` generates SF1 (24 tables, ~5.6s).
- `SELECT query_nr, query FROM tpcds_queries()` → 99 rows (cols: `query_nr` int,
  `query` text).
- `PRAGMA tpcds(<N>)` runs query N, returns result rows. All 99 run in ~1.3s at SF1,
  zero failures, **deterministic** (stable normalized digest within a pinned version).
- `tpcds_answers()` is UNUSABLE (no params, defaults to SF10, errors). Correctness uses
  a committed reference-digest fixture instead (Task 2).

## Artifact schema contract (the suite↔evaluator interface — Tasks 2 & 3 both target this)

The suite's `run()` / `mock_artifacts()` write into `RawArtifacts.dir` and populate
`RawArtifacts.manifest` with these two named entries:

- `"queries"` → a JSON file (`queries.json`): a JSON array; each element is
  `{"query_nr": int, "latency_ms": float, "row_count": int, "result_digest": str}`.
  `result_digest` is `"sha256:<hex>"` of the normalized result (see Task 2 normalization).
- `"summary"` → a JSON file (`summary.json`):
  `{"scale_factor": float, "duckdb_version": str, "extension_version": str,
    "query_count": int, "query_ids": [int, ...]}`.

`manifest` entry shape mirrors swe-bench: `manifest["queries"] = {"path": "queries.json",
...}` so `raw.path("queries")` resolves. (Read `core/suite.py` RawArtifacts + how
swe_bench/suite.py builds the manifest, including its `_sha256_file` / row-count helpers,
and mirror that style.)

## Task 1: New `data-warehouse` domain + `duckdb-local` reference adapter + `[tpcds]` extra

**Goal:** the domain loads, `csbench list --verbose` shows `data-warehouse` with the
`duckdb-local` platform, and `csbench conformance --domain data-warehouse --platform
duckdb-local` passes — with zero suite/evaluator yet.

**Changes:**
1. New package `src/clousight_bench/domains/data_warehouse/` with `__init__.py`
   defining `DataWarehouseDomain(DomainPack)` (mirror `domains/agent_runtime/__init__.py`):
   `domain = "data-warehouse"`, a one-line `description`, `tasks()` → `{}` (suite-first),
   `adapters()` → `{DuckDbLocalAdapter.name: DuckDbLocalAdapter}`.
2. New `domains/data_warehouse/adapters/duckdb_local.py` — `DuckDbLocalAdapter(ProviderAdapter)`
   (read the `ProviderAdapter` ABC in `core/plugin.py:41-145`): `name = "duckdb-local"`,
   `status = "reference"`, `provider = None`, a `target_example` (e.g.
   `{"scale_factor": 1}`), `execution_mode()` → `"simulated"` (single-node local
   reference, so its numbers never pool with live cloud data), `setup`/`teardown` no-op.
   Override `preflight(task=None)` to return a `PreflightReport` that checks duckdb is
   importable and `INSTALL tpcds; LOAD tpcds` works (a `pf.PreflightReport` with a custom
   check; read `core/preflight.py` for how to build a check result — CRITICAL on failure
   with an actionable hint like "pip install clousight-bench[tpcds]"). Do NOT call the
   default credential/sdk checks (there are no cloud creds). Keep the module importable
   without duckdb (lazy import inside preflight).
3. `pyproject.toml`: add `domains: data-warehouse = "clousight_bench.domains.data_warehouse:DataWarehouseDomain"`
   under `[project.entry-points."clousight_bench.domains"]`; add a `tpcds = ["duckdb>=..."]`
   optional extra (match the duckdb version already pinned by `[store]`). Then
   `uv sync --all-extras --frozen`.
4. Tests: `tests/test_data_warehouse_domain.py` — the domain loads via the registry,
   declares `duckdb-local`, `tasks()` is empty; `duckdb-local` adapter is a reference
   adapter, is_runnable, execution_mode=="simulated"; preflight passes when duckdb+tpcds
   available (this test may need the `[tpcds]` extra — mark it to skip if duckdb absent,
   mirroring existing optional-dep test skips).

**Verify:** gates; `csbench list --verbose` shows data-warehouse/duckdb-local;
`csbench conformance --domain data-warehouse --platform duckdb-local` passes.

## Task 2: The `tpc-ds` suite + fixtures (mock + SF1 reference digests)

**Goal:** `TpcdsSuite` produces valid `RawArtifacts` (per the schema contract) via both
`mock_artifacts()` (offline, no duckdb) and the real `run()` (duckdb-local SF1), and the
committed SF1 reference-digest fixture is captured deterministically.

**Changes:**
1. New package `src/clousight_bench/suites/tpc_ds/` (mirror `suites/swe_bench/`):
   `__init__.py`, `suite.py`, `fixtures/`.
2. `suite.py` — `TpcdsSuite(BenchmarkSuite)` (read `core/suite.py` ABC + mirror
   `swe_bench/suite.py`):
   - `suite_id = "tpc-ds"`; `suite_version` = a pinned tag folding duckdb+extension
     version + reference version, e.g. `"duckdb-1.5.4/tpcds/sf1-ref-v1"`.
   - `resolve(cfg, assets)`: read `scale_factor` (default 1.0) and `query_ids` (default
     the full range 1..99) from `cfg["params"]` (inspect how swe_bench reads cfg/params);
     offline (no data gen). Build `DatasetHandle(version=<tag+sf>, digest=<sha256 of
     sf+sorted query_ids+reference-fixture digest>, payload={sf, query_ids})`.
   - `prepare(target, dataset, driver)`: if `target.mock` → return an empty EnvHandle
     (mock path never touches duckdb). Else lazily `import duckdb`, connect (temp db dir
     under a tempfile), `INSTALL tpcds; LOAD tpcds`, `CALL dsdgen(sf := SF)`; carry the
     db path/conn in `EnvHandle.payload`.
   - `run(target, env, driver)`: if `target.mock` → delegate to `mock_artifacts`. Else
     for each query_nr in the query set: time `PRAGMA tpcds(query_nr)`, capture
     latency_ms, row_count, and `result_digest` (normalization below). Write
     `queries.json` + `summary.json` into a fresh RawArtifacts dir; populate manifest.
   - `teardown(env)`: close conn / drop temp db (best-effort, never raise).
   - `mock_artifacts(cfg)`: copy `fixtures/mock/{queries.json,summary.json}` into a
     RawArtifacts dir and build the manifest (mirror swe_bench's fixture copy). This is
     the offline path — no duckdb.
   - **Normalization for `result_digest`** (must be cross-platform stable): fetch rows;
     for each row render a canonical string where every float/Decimal is rounded to 2
     decimal places (document the rule; use a deterministic format), None → a sentinel,
     other types → repr; sort the row-strings (order-independent); join with `\n`;
     `"sha256:" + sha256(...)`. Provide this as a module-level helper so the reference
     capture (below) uses the EXACT same function.
3. Fixtures:
   - `fixtures/mock/queries.json` + `summary.json`: a small, hand-verified sample (e.g. 3
     queries) matching the artifact schema, so evaluator + stage-machine tests run offline.
   - `fixtures/reference/sf1_digests.json`: the pinned per-query
     `{query_nr: {"result_digest": ..., "row_count": ...}}` for all 99 at SF1, CAPTURED by
     a committed helper script `scripts/capture_tpcds_reference.py` (or a `python -m`
     entry) that runs the real duckdb path with the suite's normalization and writes the
     file. Run it, commit the output. The capture script is the reproducible source of the
     fixture (document how to re-run it on a version bump).
4. `pyproject.toml`: add `benchmark_suites: tpc-ds = "...suites.tpc_ds.suite:TpcdsSuite"`.
   `uv sync --all-extras --frozen`.
5. Tests: `tests/test_tpcds_suite.py` — `mock_artifacts()` returns valid RawArtifacts
   (files exist, manifest resolves, schema shape correct); `resolve()` DatasetHandle
   version/digest stable + digest changes with sf/query_ids; a real SF1 run of a small
   query subset (mark `slow` and/or skip-if-no-duckdb) produces `queries.json` whose
   digests MATCH `fixtures/reference/sf1_digests.json` for those queries (this is the
   cross-platform stability check — if it fails in CI/linux, harden the normalization or
   fall back to row_count match, and re-capture).

**Verify:** gates; the real-run test's digests match the committed reference; mock path
needs no duckdb.

## Task 3: The `official-tpcds-evaluator`

**Goal:** a pure-function evaluator mapping the artifact schema → namespaced Measurements,
honestly labeled.

**Changes:**
1. `suites/tpc_ds/evaluator.py` — `OfficialTpcdsEvaluator(Evaluator)` (mirror
   `swe_bench/evaluator.py`): `evaluator_id = "official-tpcds-evaluator"`,
   `official = True` (the plugin is the official one for this suite; per-measurement
   `official` flags differ — see below). `supports(suite_id, product)` → `suite_id ==
   "tpc-ds"`.
   `evaluate(raw)` reads `queries.json` + `summary.json` and returns:
   - `tpcds.queries_passed` — ONLY when `summary.scale_factor == 1`: ratio of queries
     whose `result_digest` matches the bundled SF1 reference (import the reference from
     the suite package or re-read the fixture — read it from
     `suites/tpc_ds/fixtures/reference/sf1_digests.json`).
     `Measurement(value=ratio, unit="ratio", reproducibility_class="deterministic",
     official=False, notes="pinned-reference reproducibility vs duckdb <ver> tpcds; not an
     audited TPC answer")`. Omit when sf≠1.
   - `tpcds.geomean_latency_ms` — geometric mean of per-query `latency_ms`.
     `Measurement(..., unit="ms", reproducibility_class="environmental", official=False)`.
   - `tpcds.total_runtime_ms` — sum of `latency_ms`. `unit="ms"`,
     `reproducibility_class="environmental"`, `official=False`.
   - Malformed/missing artifact → omit the affected measurement (mirror swe-bench's
     fail-safe cost metric; never raise on a bad optional field).
   Read the `Measurement` dataclass in `core/observation.py` for exact fields/defaults.
2. `pyproject.toml`: add `evaluators: official-tpcds-evaluator =
   "...suites.tpc_ds.evaluator:OfficialTpcdsEvaluator"`. `uv sync --all-extras --frozen`.
3. Tests: `tests/test_tpcds_evaluator.py` — over the mock fixture and hand-built
   artifacts: queries_passed ratio correct at sf1; queries_passed OMITTED at sf≠1;
   geomean + total computed correctly (verify the geomean math on a known set);
   malformed queries.json → affected measurement omitted, others still returned;
   Measurements carry the exact labels (reproducibility_class, official) above.

**Verify:** gates; evaluator unit tests green; conformance not yet (Task 4 wires selection).

## Task 4: End-to-end wiring, conformance, CI, docs

**Goal:** `csbench run --domain data-warehouse --benchmark tpc-ds --platform
duckdb-local` works end-to-end (mock + real), `csbench conformance --suite tpc-ds`
passes, CI exercises it, and the user-facing docs/README/ROADMAP reflect the new suite.

**Changes:**
1. Confirm the orchestrator's suite resolution (`core/orchestrator.py` suite branch)
   selects `official-tpcds-evaluator` for `(tpc-ds, data-warehouse/duckdb-local)` via
   `supports()` + official preference — no code change expected if Tasks 1-3 registered
   correctly; if a gap exists (e.g. product string mismatch), fix it minimally and note it.
2. Tests:
   - `tests/test_tpcds_e2e.py` — a MOCK end-to-end (mirror
     `test_swe_submit_e2e.py::test_mock_e2e_executes_committed_task_entry`):
     `csbench run --domain data-warehouse --benchmark tpc-ds --platform duckdb-local`
     with `target: {mode: mock}` (via RunSpec/CLI path) → status completed, a schema-0.3
     record with `tpcds.*` measurements, provenance.suite_id == "tpc-ds".
   - A real duckdb-local integration test (mark `slow`/skip-if-no-duckdb): a small
     query subset at SF1 → status completed, `tpcds.queries_passed == 1.0`, latency
     measurements present & positive.
3. `csbench conformance --suite tpc-ds` passes (read
   `tests/test_suite_conformance.py` for the rules the suite+evaluator must satisfy;
   add a conformance test if the existing one is parametrized over suites).
4. CI (`.github/workflows/ci.yml`): the `test` job's install line already installs
   several extras — add `tpcds` (or rely on `[store]`'s duckdb; be explicit and add
   `tpcds`). Add to the "Conformance smoke" step: `csbench conformance --suite tpc-ds`.
   Add a data-warehouse smoke: a real `csbench run --domain data-warehouse --task
   suite:tpc-ds --platform duckdb-local --param query_ids=<small subset, e.g. 3,7,42>`
   at SF1 (full 99 is ~1.3s but dsdgen ~6s — a subset keeps CI snappy), into a results
   dir the existing schema-0.3 assertion step already scans (or add a scoped assertion).
   Also add `tpcds` to the `wheel-smoke` if it runs a suite (optional — keep wheel-smoke
   on swe-bench mock unless trivial). VERIFY every new CI command locally with
   `uv run --no-sync` and paste the output in the report.
5. Docs: update `README.md` Status (a new checked item: "[x] Second suite: TPC-DS on the
   data-warehouse domain (DuckDB local reference)"), `ROADMAP.md` (mark the TPC-DS matrix
   cell / note the new domain), and add a short `docs/tpcds-suite.mdx` page (what it is,
   how to run the duckdb-local mock + real, the SF1-only correctness + honest-perf +
   deferred-QphDS caveats). Run `gen_docs.py` and commit drift. Update the Mintlify nav
   (`docs.json`) if adding a new page.

**Verify:** gates; both e2e tests green; conformance --suite tpc-ds passes; every new CI
smoke command verified green locally (paste output).
