<!-- Thanks for contributing to Clousight Bench! -->

## What & why

<!-- What does this change do, and why? Link any related issue: Closes #123 -->

## Type of change

- [ ] Bug fix
- [ ] New platform (one `ProviderAdapter` + one `configs/*.example.yaml`)
- [ ] New dimension (one `Task` with `config()`, scoring, declared `evidence_layer`)
- [ ] New domain pack / workload
- [ ] Docs / packaging / CI
- [ ] Other:

## Checklist

- [ ] Commits are signed off (`git commit -s`) per the [DCO](https://developercertificate.org/)
- [ ] `ruff check src tests` passes
- [ ] `mypy src` passes
- [ ] `pytest -q` passes
- [ ] Ran the local no-cloud smoke (`csbench run --domain agent-runtime --task T1.3 --platform local-sim`)
- [ ] If packaging changed: built the wheel and ran the smoke **outside** the checkout
- [ ] New adapters declare a status (`reference` / `experimental` / `wired` / `skeleton`); no skeleton is presented as runnable
- [ ] Every result still carries `config_hash` + `runner_version` + `evidence_layer`; no blended scores; no secrets in configs/results
- [ ] Changing task/scoring for a **shipped** dimension: bumped the version and added a `CHANGELOG.md` entry
