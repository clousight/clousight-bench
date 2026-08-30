# Eval-core refactor — per-item substrate + composable Metrics + judge base

**Status:** design spec (2026-08-30). Scope: **R1 + R2 + R4** from the architecture
review (per-item evidence/score substrate → composable Metric plugin point →
judge base). R3 (unify Task/Suite) and R5 (lift cloud-ops out of the universal
lifecycle) and R6 (Golden dataset + cache) are sequenced after and sketched here
but not specified in full.

This is a **bold** refactor — the project has no external users pinned to the
current record schema, so we bump the schema and change the scoring contract
rather than bolting compatibility shims on. We keep the orchestration spine
(single `execute()` + `_record_and_finish`, failure taxonomy, provenance/
fingerprints, plugin/open-core seam, `reproducibility_class` taxonomy) unchanged.

## 1. Motivation (what's broken today)

The evaluation model is the load-bearing weakness. Today:
`Evaluator.evaluate(RawArtifacts) -> dict[str, Measurement]` — a batch
`artifact → scalar` reducer. Consequences:

- **No per-item substrate.** Item-level results live inside suite-private JSON
  (`answers.json`, `results.json`); the record model can only hold whole-run
  scalars (`Measurement`). No error analysis, category slicing, run-to-run
  item diff, or confidence intervals without each suite reinventing a format.
- **`judge-based` is a label with no machinery.** No judge invocation, rubric,
  judge-model provenance, self-consistency, or judge cost/latency accounting.
- **Metrics are not pluggable.** One `official-*` evaluator per suite; one
  evaluator per record; `evaluate()` returns the whole dict monolithically.
  Adding a metric (safety, calibration, a new judge rubric) means forking the
  evaluator. There is no `Metric` abstraction.
- **`score()` is synchronous + pure by contract** — an LLM judge needs network
  I/O + concurrency, structurally forbidden, so a judge would have to be
  smuggled into `execute()`, collapsing the observation/score split.

DeepEval solves the analogous problems with a `BaseMetric` contract
(`measure/score/threshold/success/reason/error`), per-metric error isolation, a
structured-judge-output helper with fallback, and a Golden↔executed-case split.
We adopt the **contract-and-plumbing** layer and reject the parts wired to its
single-LLM-call, per-test-case, stateless-and-cacheable worldview (see §7).

## 2. Non-goals / what stays unchanged

- Orchestration stages, failure taxonomy, TEARDOWN-as-finally, deadline signals.
- `provenance`, the three fingerprints, `record_digest`, PUBLISH/ENRICH.
- Plugin discovery + open-core seam; `reproducibility_class` taxonomy (this axis
  is *more* expressive than DeepEval's flat model — it drives cache-safety in R6).
- Config-connect SUT seam; `RunSpec`.
- The suite-owns-lifecycle model (provision → run upstream harness → evaluate →
  teardown). We do NOT flatten a suite into a bag of independent test cases.

## 3. R1 — Per-item evidence + score substrate

### 3.1 New core types (`core/observation.py`)

```python
@dataclass
class ItemScore:
    """One metric's score for one item — the atom that Measurements aggregate."""
    metric: str                     # metric id (namespaced at record time)
    value: float | bool | str       # score / label
    status: str = "ok"              # ok | fail | skip | error   (see §4)
    reason: str = ""                # judge rationale / diagnostic (esp. judge-based)
    error: str = ""                 # populated when status == "error"

@dataclass
class ItemResult:
    """First-class, portable, re-scorable per-example evidence."""
    item_id: str                    # stable id within the dataset (e.g. HumanEval/0)
    group: str = ""                 # slice key (subject/repo/category) for breakdowns
    input: Any = None               # what the SUT was given (may be a ref, not full text)
    output: Any = None              # SUT output (or a pointer for large blobs)
    reference: Any = None           # gold / expected (or a digest)
    scores: list[ItemScore] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)   # tokens/latency per item
    attrs: dict[str, Any] = field(default_factory=dict)   # suite-specific extras
```

`ItemResult.output`/`reference` may hold a **pointer** (`{"$artifact": "..."}`)
for large content, resolved against the staged artifacts dir — so the substrate
scales to swe-bench-size patches without bloating the record.

### 3.2 Aggregation (`core/aggregate.py`, new)

Pure functions turning `list[ItemResult]` → `dict[str, Measurement]`:

- `aggregate(items, metric, how) -> Measurement` where `how ∈ {mean, ratio,
  sum, geomean, p50, p95, ...}` (reuse `core/stats.py`).
- **Confidence intervals**: `mean`/`ratio` aggregations attach a bootstrap or
  Wilson CI into `Measurement.notes` + a structured `Measurement.ci = (lo, hi)`
  field (new optional field; default None keeps old records valid).
- **Partial credit** falls out naturally: `ItemScore.value` may be a float in
  [0,1]; `ratio`/`mean` handle it.
- **Slicing**: `aggregate_by_group(items, metric, how) -> dict[group,
  Measurement]` powers category breakdowns (MMLU per-subject, swe-bench per-repo)
  emitted as `<suite>.<metric>.by_group.<group>` measurements (opt-in per suite).

### 3.3 Record schema (`core/record.py`, bump **0.3 → 0.4**)

Add an optional top-level `items: list[ItemResult-as-dict]` to `ResultRecord`
(and `TaskResult`). Existing scalar `measurements` stay — they are now the
*aggregation* of `items` where items exist. `Measurement` gains optional
`ci: tuple[float,float] | None`. Bump `SCHEMA_VERSION`, `RUNNER_VERSION`,
`tests/test_schema.py`, and the CI schema-0.x assertions in one change (they have
drifted before — see the version-sync memory).

Item volume is bounded: a `--max-persisted-items N` cap (default e.g. 1000) with
`log()`-ed truncation, so a 500-instance run doesn't write a 50 MB record; the
full set stays in artifacts.

## 4. Four-state outcome (adopted from DeepEval)

Per-item and per-metric outcomes become 4-state instead of pass/fail:

| status | meaning |
|---|---|
| `ok` | metric ran, item passed its threshold (or metric is non-thresholded) |
| `fail` | metric ran, item under threshold — the SUT underperformed (a *result*) |
| `skip` | required input / SUT capability absent — not scored (not a failure) |
| `error` | the metric/judge itself crashed — a *bug*, isolated per-metric |

The suite runner isolates each metric per item: one metric erroring sets
`status="error"` + `error=...` and never aborts the run (DeepEval's
`ErrorConfig(ignore_errors, skip_on_missing_params)`). `skip` vs `error` vs
`fail` is the honesty gain — today a fenced completion or a judge crash both
silently became `passed=False`.

## 5. R2 — Composable Metric plugin point

### 5.1 New ABC (`core/metric.py`, new)

```python
class Metric(ABC):
    metric_id: str = "abstract"          # namespaced <suite>.<metric_id> at record time
    reproducibility_class: str = "deterministic"
    required_inputs: tuple[str, ...] = ()  # declarative — validated before run (§5.3)
    requires_plugin_api = ">=1.0,<2.0"

    def score_item(self, item: ItemResult, ctx: MetricContext) -> ItemScore: ...
    # optional async variant for judge/network metrics:
    async def a_score_item(self, item, ctx) -> ItemScore: ...
    def aggregate(self, scores: list[ItemScore]) -> dict[str, Measurement]: ...
```

- A **metric** scores one item and defines how its per-item scores aggregate.
- `Evaluator` becomes a thin **orchestration** of metrics: it maps a suite's
  `RawArtifacts` → `list[ItemResult]` (the suite-specific parsing), then the
  runner applies the bound metrics. Existing evaluators can keep emitting
  scalars directly during migration (they're a degenerate 1-metric orchestration).
- **Multiple metrics per run**: entry-point group `clousight_bench.metrics`; a
  suite/domain declares which metric ids apply. `official` metrics still obey the
  `<suite_id>.` namespace conformance rule; community metrics namespace under
  `<metric_id>.` (the existing no-suite-squatting check already models this).

### 5.2 Composition

Metrics do not call each other (DeepEval's lesson). Composition is via the
**runner** (run N metrics over the same items) and, where a scoring *tree* is
needed, an optional `DagMetric` (R4/§6.3) — deterministic arithmetic with judge
calls only at leaves.

### 5.3 Declarative required-inputs

Each metric declares `required_inputs`; at boot the runner validates that the
suite's `ItemResult`s (or the SUT config-connect) can supply them, failing loud
(matching the project's fail-loud ethos) instead of a mid-run `KeyError`.

### 5.4 Execution model (`core/metric_runner.py`, new)

- Config-object args (DeepEval pattern): `MetricRunConfig(concurrency,
  error_policy, cache)` instead of flat kwargs.
- Async fan-out for judge metrics: item-level + metric-level `asyncio.gather`
  under a concurrency semaphore + deadline; deterministic metrics run inline.
- This runs in a **new SCORE-adjacent stage** that MAY do network I/O (judges),
  distinct from the pure native `Task.score`. The observation/score split is
  preserved for deterministic metrics; judge metrics get an explicit
  network-allowed lane with its own cost/latency accounted as `judge.*` usage.

## 6. R4 — Judge base (makes `judge-based` real, reproducibly)

### 6.1 `JudgeModel` abstraction (`core/judge.py`, new)

Mirrors DeepEval's `DeepEvalBaseLLM` but reuses our llm-domain adapter:
`generate(prompt) / a_generate` + `generate_schema(prompt, schema)` +
`capabilities()` probe (`supports_json_mode`, `supports_logprobs`, ...). A judge
is config-connected exactly like a SUT (`endpoint/model/credentials_ref`), reusing
`_llm_shared.resolve_endpoint` + `validate_endpoint` (SSRF guard) + `chat_once`.

### 6.2 Structured-output helper (adopted, highest-leverage borrow)

`judge_emit(judge, prompt, schema, extract) -> parsed` — tries native
schema/JSON-mode; falls back to prompt-with-example + brace-slice + trailing-comma
repair (DeepEval's `trimAndLoadJson`); both converge via an `extract` callback.
This decouples "what shape I want" from "does this judge support JSON mode" — the
core portability problem for a multi-model judge-based class.

### 6.3 Reproducible judging (we REJECT logprob weighting)

Judge metrics score via **categorical verdicts** (`Literal[...]`) + fixed
arithmetic, optionally structured as a `DagMetric` (LLM makes only categorical
calls; the score math is graph-fixed). We do **not** adopt G-Eval's top-logprob
weighted-sum (non-deterministic, provider-dependent, non-reproducible). Judge
provenance (model id, prompt hash, temperature=0, N-samples, self-consistency
vote) is recorded in `Provenance` + each `ItemScore.reason`, so a judge-based
Measurement is auditable even though it is not bit-reproducible — that's exactly
what `reproducibility_class="judge-based"` is for.

### 6.4 First real judge metric (proof)

Ship one judge metric end-to-end to validate the base — candidate: a
`response-quality` rubric metric on the `llm` domain (an opt-in, clearly
judge-based companion to MMLU/GSM8K's objective accuracy), scored by a
config-connected judge with a categorical rubric + self-consistency vote. Offline
path uses a recorded-judgement fixture (no live judge in CI); the live judge path
is gated like other real paths.

## 7. Explicitly rejected DeepEval patterns (with reasons)

- **G-Eval logprob weighted-sum** — non-reproducible; use categorical + DAG.
- **Per-test-case as the atom / "a span is the test case"** — our suite owns
  provision/env/teardown/multi-cloud; the suite stays the lifecycle unit.
- **Caching environmental results** — only `deterministic`/`judge-based` classes
  are cacheable (R6); environmental must always re-execute (our class taxonomy
  makes this correct where DeepEval's flat cache would be wrong).
- **Judge layer as the only retry site** — we also need retry at provisioning +
  SUT-invocation seams.
- **Unconditionally swallowing pytest exit status** — keep a configurable
  regression gate so CI can go red on a score regression vs baseline.
- **Folding multimodal into a god-object test case** — keep typed per-domain shapes.

## 8. Migration & backward compatibility

- **Schema 0.3 → 0.4**, additive: `items` and `Measurement.ci` are optional;
  old records validate; readers tolerate absence. One change bumps
  `SCHEMA_VERSION` + `RUNNER_VERSION` + `test_schema.py` + CI assertions.
- **Evaluators migrate incrementally.** Phase A: the runner supports both a
  legacy `Evaluator.evaluate(RawArtifacts)->measurements` and the new
  `Evaluator.items(RawArtifacts)->list[ItemResult]` + bound metrics. Existing 10
  suites keep working; we migrate them suite-by-suite to emit `ItemResult`s
  (mmlu/gsm8k/human-eval first — they already have per-item JSON), each behind
  its own reviewed increment + the conformance/mock gates.
- **Conformance** gains checks: item ids unique; every `ItemScore.metric` is
  namespaced; `status` ∈ the 4-state enum; aggregated Measurements reconcile with
  their items (a Measurement claiming `ratio=0.8` over N items must match).
- No suite's public entry-point id changes; the `suite:<id>` run command is stable.

## 9. Phasing (each phase = one reviewed, merged, green increment)

1. **R1a** — `ItemResult`/`ItemScore`/`Measurement.ci` types + `core/aggregate.py`
   + schema 0.4 bump + `items` on the record; NO suite changes yet (types + tests).
2. **R1b** — migrate mmlu/gsm8k/human-eval evaluators to emit `ItemResult`s;
   Measurements become aggregations; add per-group breakdown for MMLU subjects +
   CI on accuracy/pass@1. Prove item-diff + slicing work.
3. **R2** — `Metric` ABC + `core/metric_runner.py` + `clousight_bench.metrics`
   entry point + config-object runner + 4-state isolation; refactor the migrated
   evaluators to be metric orchestrations; allow multiple metrics per run.
4. **R4** — `JudgeModel` + `judge_emit` + one real judge metric (response-quality)
   with offline fixture + gated live path.
5. **(later) R3** — unify `Task` and `Suite/Evaluator`; delete the `suite:` string
   prefix + `SuiteTask` hardcoded scaffold strings.
6. **(later) R5** — lift cost/reaper/live-gate into an optional provisioned-cloud
   mixin so offline/judge/red-team runs skip cloud machinery.
7. **(later) R6** — Golden dataset model + content-addressed cache (det/judge only).

## 10. Risks

- **Scope creep / half-migration.** Mitigation: phased, dual-path runner so main
  is always green; each suite migrates independently.
- **Record bloat.** Mitigation: `--max-persisted-items` cap + artifact pointers.
- **Judge non-determinism eroding trust.** Mitigation: categorical-only scoring,
  temperature=0, self-consistency vote, full judge provenance, `judge-based`
  class label, offline fixtures in CI (never a live judge in the gate).
- **Conformance churn.** Mitigation: add new checks additively; keep the
  namespace + official-flag rules that already work.

## 11. Testing strategy

- Unit: aggregate.py (mean/ratio/geomean/CI/partial-credit/by-group), 4-state
  isolation (a crashing metric → `error`, run continues), required-inputs
  validation, `judge_emit` native + fallback + repair paths.
- Suite: migrated mmlu/gsm8k/human-eval produce `ItemResult`s whose aggregation
  reconciles to the previous scalar Measurements (regression: numbers unchanged).
- Conformance: the new item/namespace/reconciliation checks for all suites.
- Judge metric: offline fixture path in CI; a mocked-`JudgeModel` unit test for
  the categorical + self-consistency logic; the live path gated.
- Full suite stays green at every phase boundary; schema-0.4 assertion in CI.
</content>
