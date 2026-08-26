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

No approving review is required, but the branch must be up to date with `main`
before merging. Force pushes to `main` and deleting `main` are blocked for
everyone, administrators included.

## How to extend

The abstraction cuts at the lifecycle (`provision → setup → execute → collect →
teardown → score → report`); everything product-specific is a plugin.

| You want to add... | Do this |
|---|---|
| A **platform** | one `ProviderAdapter` subclass + one `configs/*.example.yaml`. Surface the platform's own retry/session/trace behavior; never reimplement task or scoring logic. |
| A **suite** | one `BenchmarkSuite` + one `Evaluator` plugin (entry points `clousight_bench.benchmark_suites` / `.evaluators`); the SWE-bench pilot in `suites/swe_bench/` is the template. The suite drives its own upstream harness unmodified; the evaluator is a pure function over `RawArtifacts`. |
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
csbench run --domain agent-runtime --task suite:swe-bench --platform local-sim \
    --config <yaml with 'target: {mode: mock}'>   # local smoke (mock suite run)
```

The repo ships a `.pre-commit-config.yaml` whose lint/format/type hooks call the
same `ruff` (pinned in `[dev]`) and `mypy` that CI runs, so a green
`pre-commit run --all-files` is the same gate as the `test` job.

CI runs lint · test · local baseline smoke on Python 3.10 / 3.11 / 3.12 / 3.13,
plus an installed-wheel smoke in an isolated Python 3.12 environment.

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
