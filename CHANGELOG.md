# Changelog

All notable changes to Clousight Bench are recorded here.

## [Unreleased]

### Added

- **Watch a run while it happens.** A benchmark used to write nothing
  observable until it finished — right for an audit artifact, useless for a
  thirty-minute run. New progress plane at `results/.progress/<run_id>/`:
  `state.json` (an atomically replaced snapshot), `stream.jsonl` (an
  append-only event log, every event carrying a monotonic `seq`) and `cancel`
  (a zero-byte marker). It is deliberately *outside* the sealed record: nothing
  in it is covered by `record_digest`, nothing in it can move a verdict, and
  deleting the directory loses nothing but a live view. Best-effort (an IO
  failure disables the plane at debug level and the run carries on), bounded
  (`CLOUSIGHT_PROGRESS_MAX_LINES`, default 20000 / `CLOUSIGHT_PROGRESS_MAX_BYTES`,
  default 8 MiB — past which only `log` and `sample` events are dropped, never
  `stage` or `step`), and reaped on run start and viewer start so a machine
  that lost power never shows a phantom "running" row.
- **New viewer pages: Live and Results.** `#/live` lists what is in flight and
  forwards straight through when there is only one; `#/live/:run_id` shows one
  run off a single SSE connection — stage, phase progress with an ETA that is
  withheld until three units have completed, a live waterfall, browser-computed
  numbers, and the log tail. Results is now a domain board (`#/`) → a suite
  comparison (`#/suite/:domain/:suite_id`) → one run. `#/record/:id`,
  `#/record/:id/trace` and `#/runs` (the old flat table) still resolve.
- **Cancel a run from the browser.** `POST /api/progress/<run_id>/cancel` is the
  only mutating route this server has ever had, and creates a zero-byte marker
  and nothing else. The run raises `RunCancelled` — a `KeyboardInterrupt`
  subclass — so a cancel takes the path a Ctrl-C already took: teardown runs, an
  `interrupted` record with the stages completed so far is persisted, nothing
  provisioned is orphaned. Guarded by the existing Host check plus a required
  `X-Csbench-Progress: 1` header (unreachable from a cross-origin `<form>`), a
  refusal of any request body, and run_id token validation before anything can
  name a path.
- **New read-only API routes**: `/api/board`, `/api/suite/<domain>/<suite_id>`,
  `/api/progress`, `/api/progress/<run_id>` (`?since=<seq>`) and
  `/api/progress/<run_id>/stream` (SSE, resumable by `seq`, capped at 8
  concurrent streams and 6 hours each).
- **A de-jargon layer** (`web/src/lib/glossary.ts`) with two rules: the human
  label leads and the raw key stays visible under it in mono, and an
  unrecognised metric never gets a direction we did not verify — so it is never
  bolded as a "leader" in a comparison. Fingerprints, identity and environment —
  which the old viewer simply never rendered — are now shown in full behind an
  engineer-view toggle.
- **Viewer unit tests** (`cd web && npm test`, vitest), run by CI's
  `viewer-dist` job alongside the byte-identical dist check.
- **The execution trace is one tree**, `csbench.run` down to a single query. A
  benchmark's own spans were already on the run's trace — a suite gets the
  `trace_id` on its `DriverContext` — but its root span had no parent and lived
  in a separate file, so the viewer showed the lifecycle *or* the detail, never
  both: an official TPC run rendered 76 query spans and no stages, a reference
  run rendered the stages and an empty `EXECUTE`. `emit_run_trace` now re-emits
  the benchmark's trajectory as children of the stage that produced it. The
  benchmark's own artifact is left byte-identical — it is the SUT's unmodified
  account, and its sha256 is pinned in the record — so the run trace becomes the
  merged view while the artifact stays the primary source.
- **`run.stage_spans`** records each timed stage's real `[start_ms, end_ms]`
  from the run's start. Additive and optional. Durations alone cannot say *when*
  a stage ran, so the trace had to lay the stages end-to-end and invent a
  timeline — on a real official run the benchmark's spans started 5.117s after
  the reconstructed `EXECUTE` window began, which would have drawn children
  outside their parent. It also makes a gap between two stages visible: the
  first run with real marks showed five seconds before `PREFLIGHT` that nothing
  previously reported.

### Changed

- **Plugin API 3.0 -> 3.1, additive.** `DriverContext.progress` carries a
  `ProgressReporter` (`phase` / `advance` / `step` / `sample` / `log` /
  `should_cancel`) and defaults to an inert `NULL_PROGRESS`. **A suite written
  against 3.0 is unaffected** — it never saw the field, every call is a no-op
  when nothing is watching, and `requires_plugin_api = ">=3.0,<4.0"` still
  resolves. Nothing was removed or re-signed.
- **The bundled suites report progress and poll for cancellation** — TPC-H /
  TPC-DS (reference and official modes), TPC-C, YCSB, SWE-bench, MMLU, GSM8K,
  HumanEval. Reporting stays out of measured windows: the TPC Throughput test's
  `elapsed_s` *is* `Throughput@Size`, so nothing is written inside that window
  and its per-stream steps are replayed from the intervals it already measured
  once the window closes. A phase that knows its size but cannot tick through it
  declares `reports_progress=False`, and the viewer draws an indeterminate bar
  with a reason instead of a 0% bar that reads as a hang.
- **The record page leads with the conclusion.** Eleven lifecycle stages
  collapse into one health line that expands on demand, instead of eleven cards
  ending in `PUBLISH: skipped`. The board replaced the flat record list as the
  landing page; "178 runs, newest first" answers no question anyone arrives
  with.
- **Cache policy is now explicit**: `no-store` for the document and every API
  response, `public, max-age=31536000, immutable` for the content-hashed
  `/assets/*`. Without the first half, upgrading `csbench` and reloading served
  a cached `index.html` naming an asset hash the new wheel does not contain — a
  white page with nothing in the console to explain it.

### Fixed

- **Most runs had a blank waterfall.** `/api/record/<id>/trajectory` only ever
  read the `kind=trajectory` artifact, which only official/agent runs emit. It
  now falls back to the run trace at `results/traces/<trace_id>.jsonl`, which
  *every* run writes at finalize, and the payload says in a `source` field which
  one it used. A declared-but-unreadable artifact is still a 404 rather than a
  silent downgrade: the record claims that file, and rendering a different one
  would be a lie about provenance.
- **A run that died without writing a record used to claim to be running
  forever.** A snapshot whose pid is gone is now reported as `abandoned`, and
  its directory is collected.
- **The waterfall's colours were not distinguishing anything.** The kind ->
  colour map named `db_query` and `stage`; the backend derives `query` and
  `phase`. A TPC-DS trace — 891 query spans inside 15 phase spans — painted
  every bar the same fallback blue. This predated the rebuild; adding a legend
  is what made it visible, because a legend with two identical swatches is
  obviously wrong in a way a uniformly blue chart is not. One vocabulary now,
  shared by the sealed trace and the live stream, asserted in both directions.
- **The progress plane leaked the results directory's absolute path.** The
  viewer publishes only its basename and says so in `/api/meta`; `record_path`
  handed the full path back through a different door. It is relative to the
  results root now.
- **`csbench verify` exited 1 on a healthy results directory.** It walked every
  `*.json` under the results tree and reported anything without a
  `record_digest` as a failure — so raw evaluator output under `artifacts/`, and
  the cost ledger, went red the moment any suite had run. The reserved-subtree
  list the viewer already maintained is now shared as
  `core.store.RESERVED_SUBTREES`, and dot-prefixed sidecars are skipped by
  `core.store.is_results_sidecar()`. Both are used by every walker over the
  results tree, so they cannot drift about what counts as a record.
- **A `run_id` of `".."` escaped the progress plane.** `[A-Za-z0-9._-]+` matches
  `".."` perfectly well, and `<results>/.progress/..` is `<results>`, so a
  cancel request could have created `<results>/cancel`. Readers now locate a
  progress directory by listing and matching a name, rather than joining the
  caller's string into a path at all.
- **The board drew a nameless, valueless card** for schema-0.1-era records,
  which carry no `identity` block and so summarise to empty strings. Those are
  skipped from the board and stay listed under `#/runs`, where an unplaceable
  record belongs.

## [0.6.0] — 2026-09-06

### Breaking (stage names: `COLLECT` -> `SEAL`, new `DESCRIBE`)

- **`COLLECT` is now `SEAL`.** The stage fetched nothing — EXECUTE already
  returned the bundle; the stage validates it and seals it before scoring
  (`core/observation.py::collect()` -> `seal()`). `COLLECT` stays in `STAGES`, so
  records written by 0.5.x still load, but a new run never writes it.
- **New `DESCRIBE` stage.** Assembling the run's identity / environment /
  fingerprints from plugin declarations is no longer filed under `VALIDATE`. Its
  second half runs *after* the preflight gate (facts need a live adapter), so the
  old attribution produced records that read `VALIDATE: failed, PREFLIGHT: ok`.
  `DESCRIBE` is recorded once, at the end, and a failure there is still `invalid`
  with nothing provisioned.

### Fixed

- **Viewer: stage durations were 1000x too long.** `run.stage_timings` is in
  milliseconds; the record detail card handed those numbers to the *seconds*
  formatter, so a 496 ms teardown rendered as "8.3m" on a run that took six
  seconds end to end. New `fmtDurMs()` formats them, and a source-discipline test
  keeps the two formatters from being confused again.
- **CodeQL triage** (closes #79): the real findings are fixed, the scan scope is
  narrowed to first-party code and the recurring classes are now gated by ruff
  rules, so they fail the lint job instead of a later scan. Includes log-injection
  hardening (`tests/test_logsafe.py`) across the probe/campaign/viewer paths.

### Security

- **echarts 5.6.0 -> 6.1.0** in the bundled viewer, clearing CVE-2026-45249 (XSS
  in the Lines-series tooltip). The vulnerability was never reachable here — the
  only chart is a `custom` series with its own HTML-escaping tooltip formatter —
  but the viewer `dist/` ships in the wheel, so the dependency is patched anyway.
  The waterfall was re-verified against the new major.
- **ruff 0.15.22 -> 0.16.5** (dev/lint only), plus the markdown formatting its new
  version applies to fenced code in the README.


**Breaking, read first:** `PLUGIN_API_VERSION` goes 1.0 → 2.0 → **3.0** in this
release — a plugin declaring a 1.x or 2.x range is refused with an upgrade
message. `Task` / `SuiteTask` / `DomainPack.tasks()` are gone (a benchmark is a
`BenchmarkSuite` + `Evaluator`, run as `suite:<id>`), and tracing is rebuilt on
the OpenTelemetry SDK. The record schema stays **0.4** — records written by 0.5.0
still load. Both breaking sections are detailed below.

### Changed (lifecycle: four phases over eleven stages)

The stage machine is unchanged; how it is explained and surfaced is not. The
eleven stages are now presented as **four phases** — PREPARE (`RESOLVE`/
`VALIDATE`/`PREFLIGHT`, nothing billed yet) → CONNECT (`SETUP`…`TEARDOWN`, the
only cloud window) → MEASURE (`EXECUTE`/`COLLECT`, observations only) → CONCLUDE
(`SCORE`/`ENRICH`/`PERSIST`/`PUBLISH`, pure and offline). The MEASURE|CONCLUDE
boundary is the "a cloud can never move the verdict" guarantee, enforced by
`task.score()`'s signature. Docs (EN+zh), the orchestrator docstring and the
viewer's stage card all group by phase; unknown stage names fall into an "other"
group rather than disappearing.

- **`run.stages` no longer carries `RESOLVE`.** A RESOLVE failure raises before
  any record exists, so `RESOLVE: ok` was a constant in every record. The map now
  holds only the stages this record's outcome depended on. `PUBLISH: skipped`
  **stays** — that one is a durable denial that a publisher touched the sealed
  record, not noise. `RESOLVE` remains a valid name so older records still load.
- **A blocked or failed run explains itself on stderr.** `csbench run` still
  prints the record as JSON on stdout (scripts are unaffected) and now adds a
  short human summary to stderr for any status other than `completed`: the failed
  stage with its error code and message, plus any critical/error finding's
  `summary` and `details.remediation` — the line the live gate and the cost budget
  write when they refuse to provision.
- **Doc drift fixed**: `status` also has `interrupted` (persisted on Ctrl-C, then
  the `KeyboardInterrupt` is re-raised, so it is never returned); `ENRICH` runs
  *before* `PERSIST`, not after (`core/finalize.py` said otherwise); stage spans
  cover the *timed* stages (`PREFLIGHT`…`SCORE`), not `RESOLVE`…`PERSIST`;
  `COLLECT` validates and seals the bundle — it fetches nothing from the cloud.

### Breaking (plugin API 3.0 — OTel-native tracing)

Tracing is rebuilt on the **OpenTelemetry SDK** (`opentelemetry-api`/`-sdk` become
core dependencies, pinned `>=1.30,<2`): a per-run `TracerProvider` with a `csbench`
`Resource` emits the root/stage spans; the `clousight_bench.span_exporters` entry
point now registers **SDK `SpanExporter`s** (the whole OTel exporter ecosystem plugs
in directly; the bundled local JSONL exporter is constructed per-run by core).
`PLUGIN_API_VERSION` 2.0 → 3.0; plugins declaring a 2.x (or 1.x) range are refused.
SUT trajectory spans gain **schema v3 (OTel-native)**: W3C hex ids, nanosecond
times, OTel status names, semconv attributes (`gen_ai.*` for LLM/tool activity,
`db.*` for queries, `csbench.*` for phases); legacy v2 spans (deployed agent-bundle
protocol) remain accepted and the viewer renders both. New extra `[otlp]` +
`CLOUSIGHT_OTLP_ENDPOINT` ship run traces to any OTLP collector.

- **TPC-H official mode emits its trace**: the measured Load/Power/Throughput
  intervals reconstruct into a v3 span tree (per-query `db.*` spans, concurrent
  stream lanes, refresh pairs) written as the run's `trajectory.jsonl` — the
  viewer waterfall now covers data suites, sharing the run's trace id (threaded
  via `DriverContext.trace_id`).
- **LLM endpoint suites propagate + record**: every `/chat/completions` call
  carries a W3C `traceparent` header (an OTel-instrumented SUT continues the
  run's trace in the operator's own APM) and lands a `gen_ai.*` span in the
  suite trajectory (MMLU/GSM8K/HumanEval). Docs: `tracing` (EN+zh).

### Breaking (plugin API 2.0 — eval-core consolidation)

Repositioned as a **cloud-product eval tool**: one benchmark rail, a slimmer
core, and the campaign layer quarantined as an optional package. `PLUGIN_API_VERSION`
is now `2.0`; plugins declaring `>=1.0,<2.0` are refused with an upgrade message.

- **`Task` and `SuiteTask` removed.** The `BenchmarkSuite` + `Evaluator` pair is
  the only benchmark contract (it already was in practice — zero native Tasks
  shipped since the suite-first pivot). The internal runner is `core/suite_runner.py::
  SuiteRunner` (not exported, not a plugin contract). `ObservationBundle`/`TaskResult`
  remain exported as the record's evidence containers; record **schema 0.4 unchanged**.
- **`DomainPack.tasks()` removed** — a domain declares adapters + vocabulary only.
  Bare (non-`suite:`) task_ids no longer resolve; `csbench list`/`doctor`/
  `conformance` drop their native-task surfaces, and the `list --json` inventory
  schema bumps to `list/2.0` (top-level `suites`, domains carry `platforms` only).
- **Campaign layer moved to `clousight_bench.ops`**: `core.runplan` → `ops.runplan`,
  `core.analytics` → `ops.analytics` (CLI commands unchanged);
  `iter_verified_records` moved to `core.store`. ops imports core, never the reverse.

### Changed

- **Positioning**: README + docs index (EN/zh) restate the project as **an eval tool
  for cloud computing** — five product categories, three legs (recognized-suite
  verdicts / cloud dimensions / provenance). Stale `bigdata-emr` mention removed from
  the wordcount workload manifest (the dead domain itself was never registered).

### Changed

- **langchain 1.x migration** (`[agent]` extra): `lc_agent.py` rewritten from the
  removed `AgentExecutor`/`create_tool_calling_agent` API to LangChain 1.x
  `create_agent` (langgraph-based loop). The pinned 5xx retry contract
  (max_retries=2, backoff 200ms, no retry on 4xx/599) is unchanged and covered
  by the same drift-guard tests; OpenInference still yields CHAIN/LLM/TOOL spans
  in one trace. Drops the vulnerable `langchain-core 0.3.x` line
  (GHSA-qh6h-p6c9-ff54) and removes its `allow-ghsas` exception from
  dependency review.

### Added

- **Reliability dimension (R5)**: (a) reliability evidence extracted from what
  the recognized tools already report — `ycsb.error_rate`/`ops_ok`/`ops_failed`
  from YCSB's own `Return=` counts, `tpc-c.goodput_ratio` from BenchBase's
  Goodput/Throughput; (b) a **driver-side disruption proxy**
  (`core/disruption.py`, pure-stdlib TCP relay — the fault-injection heritage
  generalized): `params.reliability {action: reset|stall, at_s, stall_ms?}` on
  `ycsb-endpoint` routes the measured phase through the relay and cuts/stalls it
  mid-run; the record carries the tool-reported damage, a
  `completed_under_disruption` gate (claimed only when the disruption actually
  fired) and a `disruption` trajectory span at the measured firing time.
  A plan that cannot be honored (non-redis binding / no endpoint) fails loudly
  instead of running a clean benchmark under a disruption-labeled dataset.
  Server-side fault injection against managed services is deliberately NOT done;
  a different disruption plan is a different benchmark (dataset digest).
- **Trace completion pack (D)**: (a) SWE-bench agent spans now map onto schema
  v3 (OpenInference wire → `gen_ai.*` attributes, deterministic hex ids — the
  same raw id always maps to the same hex id so the span forest survives);
  (b) TPC-C and YCSB emit **measured** phase spans around their Java tool
  invocations (real wall-clock, not reconstruction); (c) `csbench trace import`
  converts external OTel spans (OTLP/JSON or flat JSONL) into a validated v3
  trajectory; (d) with `CLOUSIGHT_OTLP_ENDPOINT`, a finished run also ships its
  numeric measurements as OTel **gauges** and its errors/findings as OTel
  **logs** — one-shot, trace-correlated, fail-safe.
- **TPC-H correctness pack**: (a) the pinned references for SF 1/0.1/0.01 are now
  **verified cell-by-cell against DuckDB's official `tpch_answers()`** at capture
  time (all 22 queries; the capture script fails loud on any mismatch) and
  `queries_passed` notes say "verified against the official answer set";
  (b) correctness is **SF-keyed** — any SF with a captured
  `reference/sf<sf>_digests.json` scores, others make no claim
  (`capture_tpch_reference.py --sf`); (fingerprint note: non-SF1 reference-mode runs now fold the SF-matched
  reference — or `sha256:none` — instead of the SF1 file, so their dataset
  digests change once); (c) `params.query_order_file` lets the
  operator supply the full official Appendix A permutation table (sha folded
  into the dataset digest) — we bundle streams 0–2 and never fabricate the rest.
- **Honesty fix — TPC-H official mode drops its correctness claim**: the Power
  test's queries run after RF1 refreshed the data, so comparing them against
  pristine-data references mislabels correct behavior as failure (latent until
  the multi-SF references exposed it on real runs). Correctness rides the
  `reference` mode; the official composites are unaffected.
- **TPC-C tpmC-style estimate**: `tpc-c.tpmc_estimate` = goodput × 60 × the
  configured NewOrder mix weight (45%), honestly labeled — BenchBase reports
  aggregate throughput only, so this is an estimate from the configured mix,
  never a measured NewOrder rate; audited tpmC remains unclaimed.
- **Doctor connectivity + tooling probes**: adapter preflight (shared by
  `csbench doctor` and the run's PREFLIGHT gate) now proves the configured
  endpoint is reachable — TCP for JDBC endpoints, a Redis-protocol `PING` for
  `ycsb-endpoint` (no password ever sent; `-NOAUTH` counts as alive), an
  SSRF-guarded reachability probe for `llm-endpoint` — and checks the upstream
  tools' Java requirement (BenchBase ≥ 17, YCSB ≥ 11). Misconfiguration
  surfaces as a CRITICAL check with a remediation hint instead of a mid-run
  stack trace; mock runs skip the probes.
- **TPC-DS official mode (`QphDS@SF`)**: the `tpc-ds` suite gains `params.mode:
  official` — the official sequence (Load → Power → Throughput 1 → Data
  Maintenance 1 → Throughput 2 → Data Maintenance 2 → ACID gate) reusing the
  engine-agnostic `_tpc_official` phase machine, plus the new
  `official-tpcds-qphds-evaluator` computing the floored official composite
  `QphDS@SF = ⌊SF·Q/⁴√(T_PT·T_TT·T_DM·T_LD)⌋` (+ component times, SF1
  correctness, ACID atomicity/isolation). Honest substitutions, all folded into
  unaudited provenance: clousight-generated data maintenance (insert+delete
  round-trip on `store_sales`; DuckDB ships no LF_* functions), always-`generated`
  deterministic query ordering (no bundled permutation table), consistency/
  durability `n/a`. The run emits its OTel trace (waterfall covers both
  throughput tests and DM windows).
- **Price/performance composites (cloud dimension)**: the pricing feed gains
  `system_prices` — `{perf_metric, price, basis, provider?, region?, source}` —
  and the `pricing` enricher emits `extensions.pricing.price_performance[]`
  (`price_per_unit_perf = price / perf value`) for any matching headline
  measurement (e.g. `tpc-h.qphh_at_size` → price/QphH). The bundled seed ships no
  system prices (never invented); entries are annotated unaudited; enrichers stay
  additive (a price can never change a verdict).
- **Cloud connect runbooks (R2)**: step-by-step guides for evaluating managed
  cloud services over the existing config-connect seams — a managed
  Redis-compatible KV service via `ycsb-endpoint` and a managed RDBMS
  (RDS/PolarDB/Aurora) via `jdbc-endpoint` — including in-region driver guidance
  and the price/performance feed tie-in. Committed example profiles under
  `examples/cloud-connect/` (aliyun-redis, aws-elasticache, aliyun-rds-postgres,
  aws-aurora-postgres). Docs: `cloud-connect-kv` / `cloud-connect-rdbms` (EN+zh).
- **TPC-H official mode (`QphH@Size`)**: the `tpc-h` suite gains `params.mode:
  official` — the full official pipeline (Load → Power incl. RF1/RF2 → multi-stream
  Throughput → ACID) via the new engine-agnostic `suites/_tpc_official` phase
  machine (`metrics`/`streams`/`refresh`/`acid`/`phases`), emitting `official.json`.
  The new `official-tpch-qphh-evaluator` computes the official `Power@Size`,
  `Throughput@Size` and `QphH@Size` composites (plus load time, SF1 correctness, and
  A/C/I ACID pass/fail; Durability is `n/a` on embedded DuckDB). Numbers reproduce
  the official formulas but are **unaudited** (no membership/audit/priced FDR; the
  RF1/RF2 refresh set is clousight-generated). The default `mode: reference`
  single-stream path and `official-tpch-evaluator` are unchanged. Select the
  composite via `params.evaluator: official-tpch-qphh-evaluator`. Docs: `tpch-suite`.
- **TPC-H scale dimension**: `params.streams` (default = the official minimum for the
  scale factor) drives the Throughput stream count, and `params.query_order` selects the
  stream permutations — `official` (the Appendix A table, comparable, ships streams 0–2 and
  fails loudly beyond) or `generated` (a deterministic, reproducible clousight ordering that
  scales to any `S`, e.g. SF ≥ 10). The chosen `ordering_source` is recorded in
  `official.json` and folded into the run's dataset digest.

## [0.5.0] — 2026-08-30

An eval-core refactor (per-item scoring, composable metrics, LLM-as-judge) plus
more coding benchmarks. **Record schema 0.3 → 0.4** (additive). The one public
way to add a benchmark is a `BenchmarkSuite` + `Evaluator` (+ `Metric` / judge),
run with `csbench run --benchmark <id>`.

### Added

- **Per-item scoring substrate** (schema 0.4): records carry `items`
  (`ItemResult`/`ItemScore`, 4-state `ok`/`fail`/`skip`/`error`), and scalar
  `measurements` are their aggregation (`core/aggregate.py` — mean/ratio/geomean/
  percentile + Wilson/normal confidence intervals + per-`group` breakdowns, e.g.
  `mmlu.accuracy.by_group.<subject>`). `Measurement.ci` added. Item volume capped
  by `CSBENCH_MAX_PERSISTED_ITEMS` (truncation recorded, never silent).
- **Composable metrics**: new `clousight_bench.metrics` entry-point group + a
  `Metric` plugin point; multiple metrics per run, per-metric 4-state isolation;
  opt a metric into a run with `params.extra_metrics`. Reference metric:
  `answered-rate` (bound to mmlu/gsm8k).
- **LLM-as-judge**: `core/judge.py` (`JudgeModel` + `judge_emit` with native
  JSON-schema-or-repair structured output) + the `clousight_bench.judges`
  entry-point group (`JudgeProvider`; OSS `openai-compatible` provider, SSRF-
  guarded). Reference judge metric `response-quality` (categorical rubric + self-
  consistency; reproducible — no logprob weighting). Config-connect + run a judge
  via `params.judge`; a content-addressed `CachingJudge` reuses verdicts across
  re-runs. Judge-based scoring only — never environmental.
- **Coding benchmarks**: `swe-bench-lite` + `swe-bench-multimodal` (thin variants
  of the flagship via a parametrized suite seam) and `human-eval` (openai/
  openai_humaneval, MIT) with a sandboxed code-execution substrate reusing
  `core/sandbox`.
- **`--benchmark <id>`** as the standard run flag; `provisions_resources()`
  explicit adapter capability making connect-only (config-connect, no
  provisioning) a first-class path.

### Changed

- Record schema **0.3 → 0.4** (additive: `items`, `Measurement.ci`);
  `result-record-0.4.schema.json`.
- Provenance `scaffold` sourced from the suite (`BenchmarkSuite.scaffold`), not
  hardcoded in core — fixes non-agent suites being mis-tagged `mock-agent@slice1`
  (now ""). SWE-bench scaffold values unchanged (fingerprint stable).
- Native `Task` / `DomainPack.tasks()` demoted to an internal execution contract;
  the public benchmark contract is `BenchmarkSuite` + `Evaluator`.
- Shared llm-suite scaffolding consolidated into `suites/_llm_shared.py`; a single
  `enrichers.pricing.tokens_1k_price()` (honours the `CLOUSIGHT_PRICING_DATA`
  override the old per-evaluator copies ignored).

### Fixed

- SWE-bench Multimodal now forwards `image_assets` to the agent (was dropped).
- HumanEval no longer under-counts pass@1 on ```-fenced completions.
- Code-execution security: secret-stripped subprocess env, SSRF guard on
  endpoints (incl. integer-encoded IPs + the Alibaba metadata IP), no-redirect
  Bearer, process-group kill on timeout, explicit `allow_code_execution` opt-in.

## [0.4.0] — 2026-08-29

Data-systems benchmark coverage + a vendor-neutral, config-connect SUT layer.
Rolls up the previously-unreleased slices below (region-agnostic driver,
real-cloud SWE-bench, viewer, suite contract) plus the work in this cycle.

### Added

- **Data-systems benchmark domains + suites** (the suite/evaluator contract
  generalized well beyond agent suites):
  - `data-warehouse` domain on a `duckdb-local` reference platform with **TPC-DS**
    (`suite:tpc-ds`) and **TPC-H** (`suite:tpc-h`) via DuckDB's `tpcds`/`tpch`
    extensions. Offline mock + real DuckDB SF1; correctness vs a pinned SF1
    reference digest (captured by `scripts/capture_tpc*_reference.py`), honest
    per-query latency. Audited QphDS/QphH deliberately not claimed.
  - `key-value` domain with **YCSB** (`suite:ycsb`) wrapping the upstream YCSB
    tool; throughput + tail-latency.
  - `transactional-db` domain with **TPC-C** (`suite:tpc-c`) wrapping BenchBase
    (Apache-2.0); throughput/goodput/latency. Audited tpmC not claimed.
  - All perf measurements are `reproducibility_class="environmental"`,
    `official=True` (a provenance flag, not an audit claim); the data suites emit
    no fabricated correctness dimension.
- **SUT-connection abstraction (config-connect)**: a suite runs against a local
  reference OR an already-running service selected purely by config. `SuiteTask`
  now threads `endpoint`/`credentials_ref` from the run `Target` to the suite, so
  adapters like `ycsb-endpoint` (binding+host:port) and `jdbc-endpoint`
  (dbtype+JDBC endpoint) connect to an existing datastore. Cloud-provisioned
  backends attach later on the same seam.
- **Suite/evaluator plugin loading is version-gated** (`_check_api_version` now
  applied to `load_benchmark_suites`/`load_evaluators`, like every sibling loader).
- Docs: per-suite pages (`tpcds-suite`, `ycsb-suite`, `tpcc-suite`, EN + 中文).

### Changed

- **Core is vendor-neutral** (multi-cloud debt cleanup, 3 rounds): the blob-store
  ABC `OssClient`→`BlobStore` (+ probe modules `oss_*`→`blob_*`, classes `Oss*`→
  `Blob*`); the prod-controller Terraform surface moved out of `core/` behind a
  `RuntimeProviderPlugin.controller_tf_spec()` hook; the reaper's Aliyun SDK moved
  behind a `controller_reaper_spec()` hook — **`core/` now has zero `alibabacloud`
  imports**; the live-run cost notice de-vendored. `aliyun` carrier/reaper moved
  into the `aliyun/` subpackage for layout parity with `aws/`.
- **BREAKING — OSS-named keys renamed** to be vendor-neutral: the `target:` config
  key `oss_bucket`→`blob_bucket` (the legacy key now **fails loud** with a rename
  hint) and the internal `probe_oss_prefix`→`probe_blob_prefix`. The cross-process
  `JobSpec.oss_prefix` wire field →`blob_prefix` with a dual-read migration shim.
- **CI modernized** to the suite-first / schema-0.3 world (the old lanes still ran
  deleted T-code/bigdata tasks, removed `report`/`migrate-results` commands, and
  asserted schema 0.2 — they would have failed on first push).

### Packaging

- Version 0.4.0. New optional extras `[tpcds]` / `[tpch]` (DuckDB) for the
  data-warehouse real path. New entry points: domains `data-warehouse` /
  `key-value` / `transactional-db`; suites `tpc-ds` / `tpc-h` / `ycsb` / `tpc-c`;
  evaluators `official-tpcds/tpch/ycsb/tpcc-evaluator`.

---

The slices below were previously listed as `[Unreleased]`; they ship in 0.4.0.

### Region-agnostic driver image strategy

### Changed

- **The SWE-bench driver host auto-detects its docker-image strategy at boot**
  (`domains/agent_runtime/driver_image.py`) instead of requiring a per-account
  registry-mirror address. It probes Docker Hub reachability from whatever region
  the operator chose and picks: direct pull (reachable), the account's own ACR
  endpoint discovered via the `cr` OpenAPI (Docker Hub blocked), or a loud abort
  (neither reachable — never a silent resolved=0). `driver.docker_registry_mirror`
  is now an optional operator override; the committed smoke plan configures no
  mirror address at all.

### Real-cloud SWE-bench on Aliyun (B slice 2)

### Added

- **Real SUT path** (`suites/swe_bench/sut_client.py`): the suite's non-mock run
  invokes an AgentRun-hosted agent per issue and captures REAL predictions,
  trajectory (sut_span v2, mapped from the agent's OpenInference spans) and token
  usage — `swe-bench.cost_per_resolved` is now computed from real usage.
- **SWE agent modes** (`agent_bundle/agent.py`, `protocol.py`): `oracle` (echoes
  the dataset gold patch — pipeline validation, provenance-labeled) and `llm`
  (DashScope OpenAI-compatible endpoint; key via `DASHSCOPE_API_KEY` on the
  driver, forwarded at provision time; degrades to an error span, never crashes).
- **Docker-capable ECS driver host**: terraform knobs (`controller_install_docker`,
  `controller_system_disk_size`, `controller_docker_registry_mirror`,
  `controller_hf_endpoint`) + suite-aware `LaunchSpec` (`{task_id, params}` task
  entries, `cost_budget`) so `csbench submit` can run `suite:swe-bench` in-region.
- **Real dataset pin**: SWE-bench Verified at the real HF commit `c104f840…`;
  bundled fixtures carry the REAL gold patches (deliberate benchmark-identity
  change — golden fingerprint pin updated).
- **Live smoke plan + bilingual runbook**: `configs/swe-bench-smoke.plan.yaml`,
  `docs/swe-bench-live-runbook.mdx` (+ zh) with the live-verification checklist
  and cn-region gotchas.

### Changed (pre-slice-2 hardening, merged separately)

- Suite contract hardening: first-class `suite:<id>` bridge in the orchestrator,
  prepare/run state chain, faithful upstream harness invocation (exact report
  path, schema-v2 keys, `--dataset_name` pinning), `Task.provenance()` protocol
  with the real dataset digest in the benchmark fingerprint (golden pin test),
  relative staged artifacts, span schema v2 enforcement, `csbench conformance
  --suite`, `Telemetry` removed from `Evaluator.evaluate`, scoped docker teardown.
### Local results viewer (sub-project C, slice 1)

### Added

- **`csbench serve`** (`viewer/`): zero-dependency local web viewer over a results
  directory — record list, record detail (measurements / provenance / stages /
  errors), and a Jaeger-style SUT trajectory waterfall rendered from the staged
  `trajectory.jsonl` (sut_span v2). Read-only, binds 127.0.0.1 by default; strict
  path containment on artifact reads; single embedded `index.html` (no build step,
  no external requests).

### Benchmark-suite / evaluator contract (slice 1)

### Added

- **`BenchmarkSuite` / `Evaluator` contract** (`core/suite.py`): ABCs for driving
  externally-defined suites (e.g. SWE-bench) unmodified and scoring them with
  namespaced `Measurement` dicts. `Evaluator.evaluate` returns
  `dict[str, Measurement]` (namespaced key → measurement).
- **`SuiteTask` + `Provenance`** (`core/suite_task.py`): threads provenance
  (suite_id, suite_version, evaluator_id, unmodified flag) through every
  `TaskResult`; folded into the benchmark fingerprint.
- **SWE-bench Verified pilot** (`suites/swe_bench/`): `SweBenchSuite` (registered
  entry point `swe-bench`) + `MockAgent` (gold/empty/random patch kinds) + bundled
  fixtures + `[swebench]` optional extra gating the real Docker path.
- **`OfficialSweEvaluator`**: emits `swe-bench.resolved` (ratio, deterministic,
  official=True) and `swe-bench.cost_per_resolved` (usd, environmental, official=True)
  under the `swe-bench.` namespace.
- **SUT span schema** (`TraceRecord`): records SUT-side OpenTelemetry spans as
  auditable evidence alongside the benchmark result.
- **`conformance.check_evaluator(evaluator, suite_id, measurements)`**: verifies
  the namespace/official invariant — official evaluators emit only `official=True`
  keys under `"<suite_id>."`, custom evaluators only `official=False` under
  `"<evaluator_id>."`. Returns `list[CheckResult]`; integrated into the conformance
  test suite.
- **Gated real-Docker smoke** (`tests/test_swe_bench_real_smoke.py`,
  `@pytest.mark.slow`): exercises `SweBenchSuite.run()` end-to-end with Docker;
  deselected from the default fast gate.

### Fixed

- `suite.py run()`: the two gate conditions (missing extra vs. wrong placement) now
  raise separate `RuntimeError`s naming the real cause.
- `suite.py run()`: the upstream harness report is located via a best-effort glob
  (`<run_id>*.json` → `*.json` → recurse) and normalised into the suite's canonical
  `results.json` shape (`{per_instance, resolved, total}`), instead of assuming a
  plain `results.json` filename that the harness does not produce.

## 0.3.0 — 2026-08-25

### Changed (breaking — result schema 0.2 → 0.3, no backward compatibility)

- Evidence A/B/C/D grading **removed** — every measurement now declares a
  `reproducibility_class` (`deterministic` / `environmental` / `judge-based`)
  and an `official` boolean instead. The old `evidence_layer` field is gone.
- `Provenance` sub-object added to the result: records which recognized suite
  produced it, at which pinned version (`suite_id`, `suite_version`), whether it
  ran unmodified (`unmodified`), which evaluator scored it (`evaluator_id`), plus
  optional `scaffold` and `division` fields. Provenance is folded into the
  benchmark fingerprint; a record with all-empty provenance produces the same
  fingerprint as before (empty provenance is omitted from the hashed input).
- `SCHEMA_VERSION` bumped `0.2` → `0.3`; the reference JSON Schema is
  `result-record-0.3.schema.json`; the old `result-record-0.2.schema.json` is
  removed.
- `csbench migrate-results` command **removed** — old 0.2 records are
  unsupported. There is no migration path; re-run benchmarks to produce 0.3
  records.

## 0.2.0 — 2026-08-17

Developer-preview reset; the first public release.

### Added

- Cost & cleanup closed loop (builds on the live gate): a **cumulative cost
  budget** — `--cost-budget` / `CSBENCH_COST_BUDGET` / `target.cost_budget` caps
  total realized spend across runs sharing a `--results` dir; a billable run that
  would cross it stops before provisioning (`cost.budget_exceeded`), realized
  cost (priced by the enricher, else `target.estimated_cost_usd`) accruing to
  `<results>/.cost_ledger.json`. **Resource tagging applied end-to-end**: the
  shared managed adapter now stamps `resource_tags()` on every resource it
  provisions (all four clouds inherit it) and books it in a per-run
  `ResourceLedger` (`<results>/.resource_ledger.jsonl`). **Post-run
  reconcile-by-tag**: after every run the orchestrator reverse-looks-up the
  run's residual (local ledger + a `ResourceReaper.verify(run_id)` cloud tag
  query when installed), destroys what it can, and reports — `teardown.reclaimed`
  (a leak the harness cleaned) or `teardown.residual` (critical, could not
  reclaim → `csbench sweep`). Closes the "did the run leave anything billing?"
  gap that self-reported teardown could not answer.
- Pre-access hardening (safety belt before the first real-cloud wiring): a
  **live-run cost gate** — a run whose numbers come from a real cloud
  (`execution_mode == "live"` with a real provider) refuses to provision unless
  the operator acknowledges cost via `--allow-live` / `CSBENCH_ALLOW_LIVE`,
  producing an `invalid` record with a `live.unconfirmed` finding before SETUP;
  simulated / provider-less runs are never gated, and an acknowledged live run
  records `extensions.core.live_run` (with any `target.live_limits`). **Run-id
  resource tagging** (`core.resource_tags`, `ProviderAdapter.resource_tags()`)
  so a crashed run's orphaned cloud resources are findable, plus a
  `ResourceReaper` plugin seam and `csbench sweep --provider <p> [--confirm]`
  that reconciles them (open-core ships no reaper and fails clearly). **Cloud
  account scrub** (`redaction.scrub_cloud_identifiers`): every stage-error
  message is stripped of embedded ARNs / account ids before it is stored, so a
  published record never leaks the operator's cloud account. A shared
  **`ClientPolicy`** (timeouts + retry/backoff on `ClientContext`, resolved from
  `target.timeouts` / `target.retries`) that all four clouds inherit, bounded by
  the run's remaining deadline (`bounded_read_timeout`) since the SIGALRM stage
  deadline cannot interrupt a threaded load probe. Optional
  **`X-Clousight-Token` auth** on the mock tool server (`--token` /
  `CSBENCH_MOCK_TOKEN`) for when it must be tunnel-exposed to a cloud runtime.
  Complete provision/deprovision RAM/IAM maps for Huawei & Volcengine, and the
  AgentRun integration research doc (`docs/agentrun-integration-research.md`)
  the Aliyun adapter cites.
- Phase 1D plugin & contract hardening (stability slice): plugins declare a
  `requires_plugin_api` range (`core.versioning`, zero-dependency) and the
  registry hard-rejects one that excludes this core (`IncompatiblePluginError`)
  or two plugins that claim the same domain / enricher / provider / exporter /
  resolver name or the same task_id / platform within a domain
  (`DuplicatePluginError`) — no more silent last-wins. Authoritative JSON
  Schemas for RunSpec / workload manifest / ResultRecord ship in the wheel and
  are validated with the optional `[validate]` extra (`jsonschema`), falling
  back to the hand-written checks when it is absent; a record that fails the
  0.2 schema is refused at PERSIST and emergency-dumped raw rather than lost.
  `csbench conformance --domain <d> [--platform]` checks an installed domain
  against the contract (both built-in domains pass in CI). Workload sandboxing
  and path/URI allow-listing remain out of scope for this slice.
- `agent-runtime`: `aliyun-agentrun` promoted `skeleton` → `experimental` — its
  in-tree runtime provider ran a full 27-task **live** campaign (`cn-hangzhou`:
  25 `completed` + 2 honestly `unsupported`), the first real-cloud numbers. The
  live path is validated but not yet promoted to `wired`.
- `agent-runtime`: three more dimensions on the 0.2 contract, for eight total on
  `local-sim` — T1.1 cold/warm start latency, T5.1 cost attribution (emits usage
  measurements the pricing enricher prices), T5.2 elasticity under concurrency.
  Adapters gain a `managed`/`transport`/`mode` split (`core.clients` /
  `core.endpoints` helpers) so the same code drives the local simulated runtime
  or, once wired, a real cloud; the report gains a platform × capability matrix.
- Open cost attribution: the usage vocabulary (`core.usage`) and a reference
  cost enricher (`clousight_bench.enrichers.pricing`, registered via the
  `clousight_bench.enrichers` entry point) now ship in the core, with a small
  bundled seed of public list prices. It only prices records that report usage
  and never overwrites a cost another enricher already computed; point
  `CLOUSIGHT_PRICING_DATA` at a fuller/fresher feed to override the data without
  forking the mechanism.
- Reproducible sampling: `core.sampling.HighFreqSampler` (the `sample`-event
  protocol helper) and a `synthetic-sampler` reference workload.
- `csbench rollup <run_dir> [--bucket-s N]` downsamples a run's `series.parquet`
  into `series_rollup.parquet` (avg/p99/max/count per bucket); needs `[store]`.
- Result store & analytics (optional `[store]` extra): high-frequency `series`
  are written as `series.parquet` sidecars, and `csbench query "<sql>"` /
  `csbench export <view> --out f.parquet` run DuckDB SQL over flattened
  `records` / `measurements` / `findings` / `series` views for cross-cloud
  analysis or notebook/BI export (see `docs/querying.md`). Cost is surfaced on a
  **list → discount → net** axis (`CLOUSIGHT_PRICING_DATA` / `CLOUSIGHT_PRICING_DISCOUNTS`).
- Reporting: alongside the Markdown report, a self-contained **HTML/ECharts
  renderer** (now the default) with bilingual labels, a per-dimension matrix,
  capability matrix and quadrant / time-series / stacked-bar panels, plus a cost
  column and red flags — no external assets, one openable file.
- `docs/dataset-tiers.md`: the open-seed vs. private-held-out dataset policy.
- Project is a typed package: ships a `py.typed` marker (PEP 561) so downstream
  consumers get type information; CI enforces `mypy` on the source.
- Community health files: `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1),
  `GOVERNANCE.md`, `MAINTAINERS.md`, `ROADMAP.md`, `NOTICE`, GitHub issue forms,
  a pull-request template, `.github/CODEOWNERS`, and Dependabot.
- Tag-triggered PyPI release workflow using Trusted Publishing (OIDC); see
  `docs/RELEASING.md`. Test coverage is measured (`pytest --cov`) and a
  MkDocs + mkdocstrings site (`docs/`, built `--strict` in CI) is added.
- `[project.urls]` metadata and per-version Python classifiers for PyPI.

### Changed

- Restored installable reference workloads and wheel smoke coverage.
- Made adapter implementation status explicit.
- Standardized user-facing CLI configuration errors.
- A relative reference-workload name (e.g. `workload: wordcount-py`) now
  resolves against the packaged `clousight_bench.resources.workloads` tree via
  `core.resources.reference_workload_path()`, not a repository-relative path.
  If you point a task at your own workload, pass an **absolute path** —
  relative paths that used to reach `workloads/<name>/` in a checkout no
  longer do.
- Unknown domain/task/platform lookups and a rejected skeleton adapter now
  raise typed `UnknownDomainError` / `UnknownTaskError` / `UnknownPlatformError`
  / `AdapterNotRunnableError` (all `UserInputError` subclasses) instead of a
  bare `KeyError`, so callers of the Python API get a stable, catchable
  exception hierarchy — the same one the CLI maps to exit code 2.
- The repository is public and Apache-2.0 licensed; `main` is protected by a
  ruleset requiring a pull request and the full CI matrix, with force push and
  branch deletion blocked for everyone. Security reports go through GitHub
  Security Advisories, not public issues.

### Fixed

- `csbench doctor --domain --platform` no longer hard-rejects a skeleton
  adapter before showing anything: it prints a clear "skeleton, not
  implemented" warning and still runs `adapter.preflight(task)`, so the
  credential/SDK/minimal-permission requirements a contributor needs before
  wiring the adapter are visible. `csbench run`'s hard skeleton gate (exit
  code 2, checked in the orchestrator before preflight) is unchanged.
- `--config` pointed at a directory, an unreadable file, or a non-UTF-8 file
  now fails with the same stable `UserInputError` / exit-code-2 usage error as
  a missing file or invalid YAML, instead of an unhandled
  `IsADirectoryError` / `PermissionError` / `UnicodeDecodeError` traceback.
- The `examples/README.md` J1.1 walkthrough no longer references
  `configs/bigdata-emr.local.yaml`, which was never created; the command runs
  with the packaged default workload and no `--config`.

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
- Run plans: `csbench run --repeat N --warmup W` executes the benchmark
  `warmup + repeat` times (each still its own auditable `0.2` record), discards
  the warmups, and writes a `run_plan_aggregate` under `results/aggregates/`.
- Statistical aggregation over repeats (`core/statistics.py`): numeric
  measurements get `n`, `mean`, `stdev`, `min`, `max`, `p50`, `p95` and `cv`;
  label measurements get their distribution, `mode` and `agreement`.
- Comparability-aware reporting: `csbench report` pools only records that share
  a `benchmark` **and** `environment` fingerprint, and flags a cell that mixes
  benchmarks (not comparable) or implementation fingerprints (comparable only
  with the caveat that the code changed).

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

### Compatibility

- Package version is pre-1.0.
- Result schema is now `0.2` (migrate `1.0` files with `csbench migrate-results`).
- Plugin API is `1.0`; Phase 1D (in this release) adds version-range negotiation
  and conflict detection around it without bumping the version.
