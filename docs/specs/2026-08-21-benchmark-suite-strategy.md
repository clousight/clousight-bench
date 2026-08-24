# Benchmark-suite strategy — tool positioning & architecture direction

Date: 2026-08-21
Status: discussion checkpoint (not yet an implementation spec)

> This is a **direction document**, not a finished implementation spec. It
> captures the decisions we have converged on so the reasoning is not lost, and
> lists the open questions we are still discussing. Two implementation specs are
> expected to descend from it (see "Decomposition").

## The problem this answers

The Aliyun AgentRun benchmark we ran end-to-end is a **self-designed**
methodology. A benchmark whose tasks and scoring were invented by the same party
that sells the evaluation has **near-zero credibility** (公信力) with the
community — no matter how rigorous the harness is. "Reproducible" is a promise;
"has been independently reproduced against a recognized standard" is credibility.
Those are different things, and today only the first exists.

## The reframe: borrow credibility, don't manufacture it

Credibility for a benchmark is never built by designing a *better* methodology
in-house. It is **borrowed** from methodologies the world already trusts. So the
tool's identity flips:

- **Not**: "a cloud-product benchmark methodology Clousight designed." (Who
  trusts that?)
- **But**: "a **reproducible, one-click harness** that runs the industry's
  **recognized / open benchmark suites** (TPC-DS, SWE-bench, τ-bench, YCSB, …)
  against any cloud product, with full OTel trajectory capture, A/B experiments,
  and evidence-graded reports."

The methodology's authority belongs to the suite. **The tool's value is the
harness**: the reproducibility contract, evidence layers (C/B/A/D), adapters,
OTel tracing, A/B, comparability, reports — the things this repo already has. The
suite is *content*; the harness is the *runtime that runs it*.

This also avoids the governance fight over "who defines the standard": we do not
define any standard. We run other people's standards, unmodified.

## The growth model: a coverage matrix

Growth = filling cells of a `suites × cloud-products` matrix.

```
                TPC-DS   TPC-H   YCSB   SWE-bench  τ-bench  BFCL   SPEC
EMR/MaxCompute    ✓        ✓
Redshift/BQ       ✓        ✓
Cloud KV/NoSQL                     ✓
Aliyun AgentRun                             ✓         ✓       ✓
Bedrock AgentCore                           ✓         ✓       ✓
VM / FaaS                                                            ✓
```

- **Each recognized suite added (a row header) unlocks a whole category of cloud
  products** it can measure.
- **Each adapter added (a column) lets every applicable suite hit that product.**
- The model compounds: the 10th suite meeting the 10th adapter lights up its cell
  with no new work. Suites are the content library; adapters are the interfaces;
  the harness is what multiplies them.

**Dead prerequisite:** "include as many suites as possible" only turns if adding
each suite is *cheap*. Otherwise we drown hand-porting suites one by one. So the
project's real moat, starting now, is the **benchmark-suite integration
contract** — a cheap, uniform way to plug any recognized suite in. The suite
itself must never be rewritten.

## Core principle: wrap-and-delegate, run unmodified (LOCKED)

Two ways to integrate an external suite. We commit to the first:

1. **Wrap-and-delegate (chosen).** clousight-bench does **not** reimplement the
   eval logic. It drives the suite's **own upstream harness** (SWE-bench → its
   official docker harness; TPC-DS → `dsdgen` data-gen + the 99 SQL), and only
   owns: one-click orchestration, OTel trajectory capture, normalizing the
   suite's native output into evidence-graded records, and **pinning +
   fingerprinting the exact upstream version / commit**.
   - Why: adding a suite stays cheap, and **running it unmodified is the source
     of credibility** — "we ran SWE-bench@`<commit>`, unchanged."
   - Cost accepted: we inherit each suite's heavy environment (SWE-bench = one
     docker per issue; TPC data-gen is large), heavier/dirtier deps.
2. **Reimplement as native tasks (rejected).** Uniform and controllable, but
   expensive per suite, and the moment we change it, it is no longer SWE-bench —
   the borrowed credibility drops to zero. Self-defeating.

The harness's job is orchestration, observation, normalization, comparability —
**not** re-implementing the eval.

### The credibility chain (provenance)

For borrowed credibility to hold, every `ResultRecord` must attest the suite's
identity so the number is traceable end-to-end, e.g.:

> `SWE-bench@<commit>`, run **unmodified** by `clousight-bench@<ver>`, on
> `aliyun-agentrun` / `cn-hangzhou`, dataset digest `<sha>`.

This extends the existing `fingerprints.benchmark` ("what was measured") with the
upstream suite id + version + dataset digest + an unmodified flag. It rides on
the existing evidence layers so honest labels are structural, not prose. (Note:
TPC is a trademark with an audit process — we run "TPC-DS **derived**" workloads
and must never claim an "official audited TPC result." The provenance labeling
makes that honesty automatic.)

## Domain model — suite / job / evaluator (three plugin concepts)

The refined model splits *running* from *evaluating* — the key improvement:

| Concept | Owns | ~ lifecycle stage |
|---|---|---|
| **评测集 suite** (`benchmark_suite`) | pinned dataset gen/fetch + drive the upstream harness **unmodified** → **raw artifacts** | `resolve()` + `delegate()` |
| **云产品 cloud product** | the target + its telemetry (OTel, billing) — exists as `adapter` + `runtime_provider` | — |
| **评测任务 job** | pick one suite × one cloud product, run a batch → raw artifacts on disk | one `campaign` / `RunSpec` |
| **评估器 evaluator** (`evaluator`) | read raw artifacts → metrics; **declares which suite-types × which cloud-products it supports** | `normalize()` / `score()` |
| **评估结果 result** | `ResultRecord` schema 0.2 | — |

**Why split running from evaluating** — separating `delegate()` (run unmodified)
from metric computation decouples the untouched upstream run from scoring, and
adds a coordinate to the provenance chain: **the evaluator identity**. A record
reads `SWE-bench@<commit>` × `aliyun-agentrun` × **`official-swe-evaluator`**.
Swap in a custom evaluator and it becomes `× my-custom-evaluator` — structurally
signalling "not the official metric." So user-extensibility does **not** dilute
credibility: the official built-in evaluator carries the borrowed credibility;
custom evaluators are honestly labeled. **This is the guardrail.**

**Why the evaluator declares *cloud products*, not just suites** — many metrics
are not in the suite's output; they live on the cloud-product side. SWE-bench's
`results.json` gives only `% resolved`; "cost per resolved issue" and "p95
latency" come from the cloud product's **billing + OTel trajectory**. The
evaluator is the **JOIN of `suite raw output × cloud-product telemetry`**, so it
declares compatibility on both axes.

**Built-in vs custom** — suites ship built-in for the mainstream (TPC-DS, TPC-H,
SWE-bench, …); evaluators ship built-in with each suite's **official** metrics.
Both are user-extensible via the same plugin contracts (custom suite, custom
evaluator).

**Naming collision to avoid:** the user's "评测任务" is a *job/campaign* (product
× suite), but `task` in the code (`T1.3`, …) means a *single dimension file*. Do
**not** reuse `task` for the job concept. UI layer: call it **run / experiment /
job**; underneath, reuse `campaign`. The `evaluator` concept generalizes the
existing `scorer`.

## Evidence grading retires; its one bit survives as a per-dimension property

"Evidence grading" as a **standalone C/B/A/D concept is retired** — it is jargon
nobody understands ("evidence layer B"?), and A (documentation) / D (marketing)
only ever existed for the *self-designed* methodology's vendor citations, which
this framework no longer produces.

But dimensions ("what you measure") and reproducibility ("how trustworthy is this
number") are **orthogonal axes** — a dimension name does not tell you whether its
number is reproducible. That one surviving bit — **can you re-run and get the same
number?** — is load-bearing for the "challenge our numbers" positioning, and it
survives as a **plain-language property attached to each dimension**, not a
separate grading system users must learn:

| Dimension reproducibility class | Example | Re-run behavior |
|---|---|---|
| **deterministic** | SWE-bench `% resolved` | identical (variance ⇒ flake red flag) |
| **environmental** | TPC-DS latency / throughput | drifts with network / hardware / region |
| **judge-based** | LLM-as-judge quality score | varies with judge model / version / prompt — *inherently subjective* |

**LLM-as-judge is precisely why this cannot be dropped.** A judge score is the
*least* reproducible number in the system — swap the judge model or prompt and it
changes. Presented next to a deterministic `% resolved` with no marker, a reader
reads a 0.2 judge-score gap as signal when it is noise, and anyone who re-runs to
"challenge our numbers" gets a different figure — breaking the reproducibility
promise. So every dimension **must** visibly declare its class.

- The class is a **property each dimension (official or custom) declares**, in
  plain words — no letter grades, no separate taxonomy to learn.
- The **evaluator** owns it (it is the only component that knows how a metric is
  produced) and surfaces it per-dimension in the report.
- ⚠️ This changes a **non-negotiable principle in `GOVERNANCE.md`** ("every result
  carries `evidence_layer`") — it needs a version bump + CHANGELOG entry. This is
  a *clearer expression* of the same intent (every number attributable as to its
  trustworthiness), not a drop.

### normalize/evaluate carries, the official evaluator never re-judges

An evaluator produces **dimensions**. A dimension is either an **official**
dimension (follows the suite's upstream verdict, e.g. SWE-bench `% resolved`) or a
**judge-based** dimension (e.g. LLM-as-judge quality), and each declares its
reproducibility class (above). For a built-in suite, the official evaluator does
**format conversion + class tagging only** on its official dimensions — it may
**not** re-score, re-weight, discount, or override the upstream's verdict. The
moment an official dimension injects its own judgment, the output is no longer the
suite's result — borrowed credibility drops to zero.

### Official is read-only; custom may add, never replace (DECIDED)

The guardrail has teeth: for a built-in suite, its **official evaluator is
read-only and non-overridable** — its official metrics (e.g. SWE-bench
`% resolved`) always follow the upstream verdict; nobody can replace them. A
**custom evaluator may only *add* metrics** (e.g. a difficulty-weighted score) —
it runs *alongside* the official one, is provenance-tagged non-official, and is
displayed side-by-side, **never impersonating the official metric**.

- Rejected: letting a custom evaluator wholesale *replace* the official one. More
  flexible, but then "the SWE-bench result" could be anyone's reprocessed number
  and the borrowed credibility is hollow — trivially abused to manufacture
  flattering figures.
- Consequence: users extend **breadth** (new metrics, new suites), never
  **authority** (the official verdict). That is what makes "built-in mainstream
  suites with official metrics" a promise with teeth.

## Architecture (four layers; two are new)

```
Layer 3  Local web service   csbench serve    ← NEW: lens + launcher
                                                (OTel trajectory viewer / A/B /
                                                 coverage matrix / one-click run)
Layer 2  Suite plugins       benchmark_suites  ← NEW: wrap-and-delegate contract
                                                (TPC-DS, SWE-bench reference +
                                                 user-custom)
Layer 1  Core harness / CLI                    ← exists: orchestrator, registry,
                                                 adapters, runtime_providers
Layer 0  Canonical files (source of truth)     ← exists: RunSpec / ResultRecord /
                                                 OTel spans / Parquet, on disk,
                                                 git-committable
```

**Layer 2 (the moat).** A suite implements roughly:
`resolve_dataset()` (fetch/generate at a pinned version → reuse asset resolver,
record digest), `prepare(target)` (stand up the suite's own docker/env),
`run(target)` (drive the upstream suite **unmodified**, streaming OTel spans +
progress), `score(raw)` (map the suite's native metrics to evidence-graded
Measurements/Findings + provenance). TPC-DS / SWE-bench ship as reference
implementations; user-custom suites implement the same contract.

**Layer 3 (the experience).** Reads Layer 0 for browsing; launches only through
Layer 1 (same orchestrator, progress via SSE/websocket).

## Client vs. local web service — DECIDED: local web service

- **No desktop client (Electron/Tauri).** Packaging tax; developers already live
  in terminal + browser.
- **`csbench serve` — an on-demand local web service** + browser UI. This is the
  industry norm for eval + observability tooling (Phoenix, Jaeger, MLflow UI,
  TensorBoard, Ray Dashboard, DuckDB UI). OTel trajectory viewing and result
  exploration are inherently visual and interactive; a static terminal report
  cannot carry large-trace exploration.
- **Evolution bonus:** this same web service is the future SaaS codebase —
  single-tenant local today, multi-tenant hosted later. Clean path onto the
  open-core commercialization plan.

**Locked principle — the web service is OPTIONAL and NON-AUTHORITATIVE.**
Everything it does, the CLI can do. It is a *lens + launcher* over the canonical
result files (schema 0.2, fingerprinted, digested) — never a new source of truth
or a required runtime. **CLI is the source; web is the lens.** If the UI ever
became the only way to run or see results, we would rebuild the very SaaS lock-in
we are deferring, and break the git-native reproducibility that *is* the
credibility.

## Isolation & resource reclamation — the real-cloud safety architecture

Benchmarking a real cloud product must not pollute the user's environment, and
must clean up after itself. Today's machinery (`live_guard`, `resource_ledger`,
`resource_reconcile`, `reaper_base`) is **tag-scoped**: resources live in the
user's own account, so the reaper may only ever delete resources carrying
`TAG_MANAGED` + `TAG_RUN_ID`. A missed tag is a leak that bills forever. Blast
radius = the whole account.

**Isolation is a tiered, optional spectrum**, recorded in
`fingerprints.environment`, and the reaper's aggressiveness is a function of the
tier:

| Tier | Boundary | What the reaper may do |
|---|---|---|
| T0 mock (default) | none | no resources; zero blast radius |
| T1 user account + tags (today) | none | **tag-scoped only**; a missed tag leaks |
| T2 dedicated resource group | RG | scoped sweep within the group |
| T3 dedicated sub-account under an org | account | **allowlist/nuke mode** — anything in the account/region not on a keep-list is reclaimable, catching even *untagged* leaks |

Key coupling: **reclamation thoroughness = f(isolation tier).** The sub-account
(T3) is not just "no pollution" — it is the only tier that lets the reaper
promise "this box is clean afterward, including what I forgot to tag." So
`reaper_base` grows a second mode: `tag-scoped` (shared account) vs
`boundary-nuke` (dedicated account), selected by tier.

### Batteries-included but optional ("we do the dirty work")

Principle: the user focuses on evaluation; the harness adapts all the isolation
plumbing. But every layer is BYO-able and degrades gracefully.

- **The one thing the user does out-of-band:** open the isolated sub-account /
  resource-directory member (needs org-master privilege, often no clean API — the
  harness must not do this).
- **Everything *inside* the boundary is the harness's dirty work (optional, B):**
  resource group, scoped IAM role, terraform state backend, tag policy, network
  scaffolding, and each suite's heavy env. If the user built some of it already,
  `init` detects and **adopts** it rather than recreating. If the user does
  nothing, it degrades to T1.

**This scaffolding is inherently per-cloud → a new plugin responsibility: an
`isolation_provisioner` per cloud**, registered by entry point, mirroring
`runtime_providers` / `resource_reapers`. Open core ships the Aliyun one first
(the live-validated path); other clouds wire the same way.

### How the harness enters the box: cross-account role assumption (DECIDED)

The harness enters the isolated account by **assuming a role** across the account
boundary (STS AssumeRole / Aliyun RAM role), never by holding that account's
static keys. The user's primary identity assumes a role that lives *inside* the
disposable box.

- **No long-lived secret in the box** — the harness works from short-lived
  temporary credentials, minimizing the leak surface.
- **The identity-proof gate is native** — the assumed role's owning account-id is
  provable, so preflight can assert "I am in the declared disposable box" before
  any provisioning.
- **Composes with batteries-included/BYO** — the `isolation_provisioner` creates
  the role + trust relationship (B / auto), or the user pre-creates the role and
  hands the harness only its ARN (BYO). Both share the one assume-role path.
- Rejected: per-account static AK/SK — simpler, but parks a long-lived key in the
  box that must be stored and rotated, widening the blast surface.

**Four trust properties that make auto-provisioning safe:**

1. Everything the harness creates is itself tagged + ledgered, so
   `csbench destroy` nukes exactly what `init` made and never touches
   pre-existing user resources.
2. Least privilege: the role the harness creates/uses is scoped to the RG /
   account boundary — a harness bug cannot reach outside the box.
3. **Identity-proof gate first** (extends `live_guard`): before provisioning
   anything, preflight asserts the resolved credentials' account-id / RD-member-id
   matches the declared isolation profile, and refuses otherwise. Nuke mode is
   only safe when we can *prove* we are in the disposable box.
4. Idempotent + reversible + auditable: terraform plan shown before apply;
   `init` is re-runnable; `destroy` fully reverses.

## Decomposition — three sub-projects

This is genuinely three independent subsystems; each deserves its own spec:

1. **Sub-project A — Isolation & reclamation infra.** The real-cloud safety
   architecture above: the tiered isolation spectrum, the per-cloud
   `isolation_provisioner`, the identity-proof gate, and the reaper's
   `boundary-nuke` mode. Foundational for any credible real-cloud suite run.
2. **Sub-project B — `benchmark_suite` integration contract + one reference
   pilot.** The moat; the ground the growth model stands on. **Without it the web
   service has nothing to show.** Recommended first pilot: **SWE-bench on the
   already-live-validated `aliyun-agentrun`** — fastest path to a credible "real
   cloud + recognized suite + unmodified" result.
3. **Sub-project C — `csbench serve` local web service.** Trajectory viewing /
   A/B / coverage matrix / one-click run — the experience layer.

**Build order:** A and B are the foundation (A makes real-cloud runs safe; B is
the content). C mirrors what A+B produce, so it comes last.

## Open questions (still discussing)

- Exact shape of the `benchmark_suite` contract interface (method set, lifecycle,
  how it composes with the existing orchestrator phases and adapters).
- How a suite's heavy environment (docker-per-issue, large data-gen) reconciles
  with the workload sandbox and cost/reaper safety belts.
- How suite-native metrics map onto evidence layers and the per-dimension report
  (SWE-bench = % resolved; TPC-DS = latency/throughput @ SF).
- Web service stack + how the OTel trajectory viewer consumes the exported spans.
- Whether A/B (product × product, config × config) is elevated to a first-class
  concept beyond the current repeat/warmup/comparability machinery.
