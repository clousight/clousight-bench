# Coding / agent benchmarks — research + implementation plan

**Status:** research + design (2026-08-29, overnight). The user asked to add
SWE-bench (Verified done / Lite / Multimodal), SWE-Lancer, CoreBench, USACO,
SciCode "as much as possible". This doc is the OBJECTIVE, honest close-out for
that batch: which are done, which are safe to implement next (with human review),
and which are gated on data-access / licensing / a flagship refactor. It exists
because these are all heavy-harness coding benchmarks whose real paths (code
execution, per-task environments, large gated datasets) cannot be implemented
FAITHFULLY unattended overnight without risking either inaccurate/hollow suites
or the live-validated flagship SWE-bench. Per "长远考虑、客观处理", the honest
deliverable is this plan + the clean wins already merged, not fake stubs.

## What already shipped this cycle (for context)
Clean, faithful, merged: SWE-bench **Verified** (agent-runtime), TPC-DS/TPC-H
(data-warehouse), YCSB (key-value), TPC-C (transactional-db), **MMLU + GSM8K**
(llm domain), the pytest plugin + `--assert` threshold gate. 7 suites / 5 domains.

## License findings (verified 2026-08-29 — the ClickBench=NC lesson: check first)
| Benchmark | License | Vendorable? | Notes |
|---|---|---|---|
| SWE-bench Verified/Lite/Multimodal | **MIT** (princeton-nlp/SWE-bench) | Yes | datasets on HF, not gated |
| CoreBench | **MIT** (siegelz/core-bench) | Yes | but per-task research artifacts have their own varied licenses + are large |
| SciCode | **Apache-2.0** (scicode-bench/SciCode) | Yes | code + test cases |
| SWE-Lancer | **UNVERIFIED** (openai repo LICENSE not found at probed path) | **No — verify first** | real Upwork tasks; heavy per-task app environments; likely gated/complex |
| USACO | **UNVERIFIED** + contest-problem data (usaco.org copyright) | **No — data risk** | problem statements are contest IP; redistribution risky regardless of repo license |

## The architectural prerequisite (blocks Lite + Multimodal)
The shipped flagship `suites/swe_bench/` HARDCODES its identity: module-level
`_HF_REVISION`, `_FIXTURES_DIR`, and the evaluator's `swe-bench.` namespace +
`supports() == "swe-bench"`. SWE-bench Lite/Multimodal are the SAME harness with
a different dataset + instance set + namespace. Doing them cleanly (not by
copy-paste) needs a small **parametrization refactor**:
- `SweBenchSuite`: module consts → class attrs (`hf_revision`, `fixtures_dir`,
  keep `suite_id`), so a subclass sets them.
- `OfficialSweEvaluator`: a `suite_id` class attr driving the `<suite_id>.`
  namespace + `supports()` (default preserves `swe-bench` behavior — additive).
Then `swe-bench-lite` / `swe-bench-multimodal` are ~thin subclasses + real
instance fixtures + mock artifacts. **This refactor touches the live-validated
flagship and MUST be done with human review (not unattended).** Risk: low with
review + the full-suite gate + the golden-fingerprint test; unacceptable at 2am.

## Per-benchmark plan

### 1. SWE-bench Lite  — READY (needs the refactor above + human review)
300-instance curated subset; `princeton-nlp/SWE-bench_Lite` (MIT). After the
parametrization refactor: `SweBenchLiteSuite(suite_id="swe-bench-lite",
hf_revision=<Lite pin>)` + real Lite instance fixtures (bundle a small subset like
Verified did) + `OfficialSweEvaluator` emitting `swe-bench-lite.resolved`. agent-
runtime domain, existing local-sim/aliyun platforms. Effort: S–M. Faithful: yes.

### 2. SWE-bench Multimodal — READY-ish (refactor + multimodal fixtures)
JS/visual repos, image-augmented issues; `princeton-nlp/SWE-bench_Multimodal`
(MIT). Same refactor + the SUT/harness must carry images (the agent bundle +
sut_span already have MLLMImage plumbing on the agent-runtime side — reuse).
Effort: M. Faithful: yes, but verify the upstream MM harness invocation + image
handling before claiming it.

### 3. CoreBench — NEEDS DESIGN (MIT, but heavy + big data)
Computational-reproducibility agent benchmark: reproduce a paper's results from
its code+data. Real harness runs the paper's compute in a container; per-task
artifacts are large + have mixed licenses. Maps to agent-runtime (agent + a
reproduction harness) OR a new `research-repro` domain. Effort: L. Faithful
offline: hard (big per-task environments). Recommend: mock-first suite + gated
real harness, but design the artifact/scoring contract first.

### 4. SciCode — NEEDS DESIGN (Apache-2.0)
Scientific-code generation from research problems; scored by executing generated
code against reference test cases. Maps to the `llm` domain (code accuracy) or
agent-runtime. Needs a code-execution sandbox for the real path (security: see
ROADMAP workload-sandbox layers 3-5, not yet landed). Effort: M–L. Faithful
offline: mock path yes; real path needs the sandbox. Recommend after the sandbox
or with a clearly-gated exec path.

### 5. SWE-Lancer — BLOCKED (license unverified + very heavy)
OpenAI freelance-SWE benchmark (real Upwork tasks). License not confirmed at the
probed path — MUST verify before any vendoring. Real harness = full app
environments per task (very heavy). Recommend: verify license; if permissive,
mock-first suite + gated real harness; treat as a large standalone effort.

### 6. USACO — BLOCKED (data licensing)
Competitive-programming (USACO contest problems). The problem statements are
contest IP (usaco.org); redistribution is risky regardless of any harness repo
license. Recommend: do NOT vendor problem data; if pursued, fetch-at-runtime +
clearly-labeled + a licensing review — same posture as ClickBench (opt-in,
non-vendored, flagged). Harness = code exec + test cases (needs the sandbox).

## Recommended sequencing (for morning review to greenlight)
1. **Flagship parametrization refactor** (reviewed) → unlocks Lite + Multimodal.
2. **SWE-bench Lite** (faithful, S–M) then **Multimodal** (M).
3. **SciCode** + **CoreBench** — after a code-execution sandbox decision (they
   need safe exec / big environments); design the artifact+scoring contract.
4. **SWE-Lancer** — verify license; large standalone.
5. **USACO** — licensing review first (contest-problem data); likely opt-in
   fetch-at-runtime, non-vendored.

Also easy license-clean LLM-domain adds (not requested but cheap, MMLU-shaped):
HellaSwag / ARC / TruthfulQA / BBH (multiple-choice, MIT-ish) — no sandbox needed.
