# Contributing to Clousight Bench

Thanks for helping build an independent, reproducible cloud benchmark.

## Developer Certificate of Origin (DCO)

We use the [DCO](https://developercertificate.org/) instead of a CLA. Sign off
every commit:

```bash
git commit -s -m "your message"
```

This appends `Signed-off-by: Your Name <you@example.com>`, certifying you have
the right to submit the code under the project's Apache-2.0 license.

## How changes land

`main` accepts no direct pushes. Open a pull request; it merges once these
checks pass:

| Check | What it runs |
|---|---|
| `test (3.10)` … `test (3.13)` | `ruff check src tests`, `pytest -q`, and the no-cloud local smoke |
| `wheel-smoke` | builds a wheel, installs it into a clean virtualenv, and runs `csbench` **outside** the checkout |
| `analyze` | CodeQL static analysis (see [Static analysis](#static-analysis)) |
| `viewer-dist` | rebuilds `web/` and asserts the committed viewer bundle is byte-identical |
| `review` | dependency review — blocks a PR that introduces a high-severity advisory |

No approving review is required, but the branch must be up to date with `main`
before merging. Force pushes to `main` and deleting `main` are blocked for
everyone, administrators included.

## How to extend

The abstraction cuts at the lifecycle (`provision → setup → execute → collect →
teardown → score → report`); everything product-specific is a plugin.

| You want to add... | Do this |
|---|---|
| A **platform** | one `ProviderAdapter` subclass + one `configs/*.example.yaml`. Surface the platform's own retry/session/trace behavior; never reimplement task or scoring logic. |
| A **suite** | one `BenchmarkSuite` + one `Evaluator` plugin (entry points `clousight_bench.benchmark_suites` / `.evaluators`); the simplest template is `src/clousight_bench/suites/gsm8k/` (see docs/adding-a-suite.mdx). The suite drives its own upstream harness unmodified; the evaluator is a pure function over `RawArtifacts`. |
| A **dimension** | one `Task` subclass with `config()` (the controlled inputs), `execute()` (raw observation only), `score()` (a pure function of the bundle), `task_revision` / `scorer_revision`, and optionally `environment_facts()` and `workload_identity()`. |
| A **product category** | one `DomainPack` registered via the `clousight_bench.domains` entry point. |
| A **load generator** | one `src/clousight_bench/resources/workloads/<name>/` dir: `manifest.yaml` + an executable speaking the JSONL protocol. Resolve it with `clousight_bench.core.resources.reference_workload_path(name)` — never build the path by concatenating it onto the repository root, since that breaks under a wheel install. Wrap a mature tool (YCSB / TPC-DS / sysbench) rather than reinventing it. |

## Reproducibility rules (non-negotiable)

- `execute()` may talk to the cloud and must return raw, replayable evidence
  only. Never put a verdict in an `ObservationBundle`.
- `score()` is a pure function of the bundle: no credentials, no network, no
  resource creation, no mutation of the bundle. A stored observation must be
  re-scorable years later.
- Bump `scorer_revision` when scoring changes and `task_revision` when the
  observation procedure changes. Both feed `fingerprints.benchmark`, which is
  what keeps a published number attributable.
- Put everything that determines a number into `Task.config()`.
- Never put a secret, hostname, username or raw environment variable into a
  `RunSpec`, an observation, an environment fact or a finding. Reference
  credentials by env-var name; `ResultStore` refuses to persist a record that
  contains this machine's identity.
- Every `Measurement` needs a `value`, a `unit`, a `reproducibility_class`
  (`deterministic` / `environmental` / `judge-based`) and an `official` flag; every
  `Finding` needs a stable `code` and a `severity`.
- Report per-dimension; never emit a blended cross-dimension score.
- An adapter's `teardown()` must be idempotent: the lifecycle calls it whenever
  `setup()` was entered, including when `setup()` itself failed half-way.

Before opening a PR that touches packaging, build the wheel and run the smoke
outside the checkout. Editable installs are not sufficient evidence because
repository-relative resource bugs do not reproduce there.

New adapters must declare one of `reference`, `experimental`, `wired`, or
`skeleton`. A skeleton must never be presented as runnable.

## Before you push

```bash
pip install -e ".[dev]"
pre-commit install          # optional: run the CI lint/type/format gate on every commit
ruff check src tests
pytest -q
csbench run --domain agent-runtime --benchmark swe-bench --platform local-sim \
    --config <yaml with 'target: {mode: mock}'>   # local smoke (mock suite run)
```

The repo ships a `.pre-commit-config.yaml` whose lint/format/type hooks call the
same `ruff` (pinned in `[dev]`) and `mypy` that CI runs, so a green
`pre-commit run --all-files` is the same gate as the `test` job.

CI runs lint · test · local baseline smoke on Python 3.10 / 3.11 / 3.12 / 3.13,
plus an installed-wheel smoke in an isolated Python 3.12 environment.

## Static analysis

Two tools, one job each, deliberately non-overlapping:

- **ruff** (`[tool.ruff.lint]` in `pyproject.toml`) is the *fast* gate. Beyond
  style it selects a security set — `S`/`B`/`G`/`LOG`/`SIM105`/`SIM115`/`ISC` —
  so the defect classes CodeQL reports days later on `main` fail at
  `git commit` instead. The `select` list carries a comment per rule, and a
  second list records the rules we deliberately do *not* select and why; extend
  either rather than sprinkling `# noqa`. Note `flake8-bandit.check-typed-exception
  = true`: without it `S110`/`S112` only fire on a broad `except Exception`, and
  every silent handler CodeQL still reported after the first triage caught a
  *typed* exception — the gate was blind to exactly the surviving cases.
- **CodeQL** (`.github/workflows/codeql.yml`) is the *deep* gate: taint
  tracking ruff cannot do. Its scope lives in
  `.github/codeql/codeql-config.yml`, which runs `security-and-quality` minus a
  short, individually-justified list of style queries whose every hit here is
  correct-by-design. **The open-alert count is expected to be zero.** If a new
  alert is a genuine false positive, add it to that config with a reason in the
  same PR — do not dismiss it in the GitHub UI, where the reasoning is invisible
  to the next reader.

### Never log an untrusted value directly

Anything that crossed a network or user boundary — an HTTP path, a header, a
filename, a run_id from a URL — goes through
`clousight_bench.core.logsafe.sanitize_for_log` before it reaches a `logger.*`
call. A bare newline in such a value forges a whole log entry (CWE-117); the
helper escapes control characters and caps the length. This is the fix CodeQL's
`py/log-injection` asks for, and the reason `G`/`LOG` are selected in ruff:
keeping log arguments as `%s` arguments (never f-strings) is what lets the
sanitizer sit in one place.

### Viewer dependency bumps

`src/clousight_bench/resources/viewer/dist` is committed and ships in the wheel,
so `viewer-dist` fails on **every** `web/` dependency PR: Dependabot updates
`package.json` / `package-lock.json` but cannot rebuild the bundle. Finish the
PR by hand:

```bash
cd web && npm ci && npm run build     # writes ../src/clousight_bench/resources/viewer/dist
git add src/clousight_bench/resources/viewer/dist
```

Deliberately **not** automated with a CI job that commits the rebuild: that job
would have to run `npm ci` — arbitrary `postinstall` scripts from the
just-bumped, not-yet-reviewed packages — while holding a write token. Rebuilding
a supply-chain-sensitive artifact is the last place to hand out write access.

A bump that crosses a major version also needs a render check before it lands,
not just a green build: `csbench serve --results <dir>`, then confirm the record
list, a record detail, the trace waterfall (the only ECharts consumer) and the
locale/theme toggles, with the browser console clean.

### Live tests

Tests that hit a real cloud endpoint (they need credentials) are marked
`@pytest.mark.live` and **skipped by default** — `pytest` runs with
`-m 'not live'`, so the suite stays account-free, fast and non-flaky. To run them
against your own account:

```bash
pytest -m live
```

## Triage & labels

Issues and PRs are triaged with a small label set: type (`bug`, `enhancement`,
`docs`, `refactor`, `test`, `question`), area (`area: core` / `adapter` /
`domain` / `report` / `cost` / `ci`), and meta (`good first issue`,
`help wanted`, `needs-repro`, `blocked`, `breaking`). A maintainer syncs them
with `scripts/setup-labels.sh` (uses the `gh` CLI). New to the project? Filter for
[`good first issue`](https://github.com/clousight/clousight-bench/labels/good%20first%20issue).
