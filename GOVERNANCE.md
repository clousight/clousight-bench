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

- Every result is attributable: it carries its benchmark, environment, and
  implementation fingerprints, and every measured dimension declares its
  **reproducibility class** (`deterministic` / `environmental` / `judge-based`).
- Suite provenance is honest: a result records which recognized suite produced
  it, at which pinned version, run unmodified, and which evaluator scored it.
  Clousight stewards the tool; it is not the judge of who wins — suite authority
  belongs to the upstream, and results are decentralized and self-verifiable.
- Results are reported **per dimension** — never a blended cross-dimension score.
- Secrets are never stored in a `RunSpec`, config, or result file; they are
  referenced by environment-variable name and resolved through the cloud's own
  default credential chain.
- A `skeleton` adapter is never presented as runnable.

## Commercial plugins

Commercial plugins are developed in a separate private repository and are not
required to run anything in this one. The reproducibility mechanisms — including
the real-cloud adapters, the probe carrier, the resource reaper and the seed
pricing enricher — live here in the open core. The commercial layer is *data and
service*, not withheld code: a fuller / fresher price feed, token-gated private
and held-out datasets, and managed SaaS. The open-source core never depends on
any commercial plugin; plugins depend on the core only through the published
plugin API and data contract.
