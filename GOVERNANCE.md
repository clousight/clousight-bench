# Governance

Clousight Bench is an open-source project under the Apache-2.0 license. This
document describes how the project is run today. It is deliberately lightweight
for the project's current size (Developer Preview) and will grow as the
community does.

## Roles

- **Contributors** — anyone who opens an issue or pull request. Contributions
  are accepted under the [DCO](https://developercertificate.org/); sign off
  every commit with `git commit -s`.
- **Maintainers** — listed in [MAINTAINERS.md](MAINTAINERS.md). Maintainers
  review pull requests, cut releases, and are the code owners in
  [.github/CODEOWNERS](.github/CODEOWNERS).

## How changes land

`main` is protected and accepts no direct pushes. Every change lands through a
pull request that passes the full CI matrix (lint, type-check, tests, the
no-cloud smoke, and the installed-wheel smoke). Force pushes and branch deletion
are blocked for everyone, administrators included. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the contributor-facing details.

## Decision making

Most decisions are made by lazy consensus in pull requests and issues: a
proposal that draws no sustained objection within a reasonable time is accepted.
When consensus is not reached, the maintainers decide. Decisions that change the
result schema, the plugin API, or the scoring of a shipped dimension require a
version bump and a `CHANGELOG.md` entry — published numbers must stay
attributable.

## Non-negotiable principles

These are the invariants that define the project; changes that violate them will
not be merged:

- Every result carries `config_hash` + `runner_version` + `evidence_layer`.
- Results are reported **per dimension** — never a blended cross-dimension score.
- Secrets are never stored in a `RunSpec`, config, or result file; they are
  referenced by environment-variable name and resolved through the cloud's own
  default credential chain.
- A `skeleton` adapter is never presented as runnable.

## Commercial plugins

Commercial plugins are developed in a separate private repository and are not
required to run anything in this one. The open-source core never depends on any
commercial plugin; plugins depend on the core only through the published plugin
API and data contract.
