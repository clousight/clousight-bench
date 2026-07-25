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

## How to extend

The abstraction cuts at the lifecycle (`provision → setup → execute → collect →
teardown → score → report`); everything product-specific is a plugin.

| You want to add... | Do this |
|---|---|
| A **platform** | one `ProviderAdapter` subclass + one `configs/*.example.yaml`. Surface the platform's own retry/session/trace behavior; never reimplement task or scoring logic. |
| A **dimension** | one `Task` subclass with its own `config()` (hashed inputs), `run()` + scoring, and a declared `evidence_layer`. |
| A **product category** | one `DomainPack` registered via the `clousight_bench.domains` entry point. |
| A **load generator** | one `src/clousight_bench/resources/workloads/<name>/` dir: `manifest.yaml` + an executable speaking the JSONL protocol. Resolve it with `clousight_bench.core.resources.reference_workload_path(name)` — never build the path by concatenating it onto the repository root, since that breaks under a wheel install. Wrap a mature tool (YCSB / TPC-DS / sysbench) rather than reinventing it. |

## Reproducibility rules (non-negotiable)

- Every result must carry `config_hash` + `runner_version` + `evidence_layer`.
- Put everything that determines a number into `Task.config()` so the hash is honest.
- Never put secrets in a `RunSpec`/config; reference them by env-var name.
- Report per-dimension; never emit a blended cross-dimension score.
- Changing task/scoring logic for a **shipped** dimension requires a version bump
  and a changelog entry — published numbers must stay attributable.

Before opening a PR that touches packaging, build the wheel and run the smoke
outside the checkout. Editable installs are not sufficient evidence because
repository-relative resource bugs do not reproduce there.

New adapters must declare one of `reference`, `experimental`, `wired`, or
`skeleton`. A skeleton must never be presented as runnable.

## Before you push

```bash
pip install -e ".[dev]"
ruff check src tests
pytest -q
csbench run --domain agent-runtime --task T1.3 --platform local-sim   # local smoke
```

CI runs lint · test · local baseline smoke on Python 3.10 / 3.11 / 3.12 / 3.13,
plus an installed-wheel smoke in an isolated Python 3.12 environment.
