# Support

Thanks for using Clousight Bench. Here is where to go:

- **Usage questions & ideas** —
  [GitHub Discussions](https://github.com/clousight/clousight-bench/discussions).
  Search first, then open a **Q&A** (how do I…?) or **Ideas** (what if…?) thread.
- **Bugs & feature requests** — open an
  [issue](https://github.com/clousight/clousight-bench/issues/new/choose) using the
  templates. For a bug, include your OS, Python version, the exact `csbench`
  command, and what you expected vs. what happened.
- **Security vulnerabilities** — do **not** open a public issue. Report privately
  via [GitHub Security Advisories](https://github.com/clousight/clousight-bench/security/advisories/new)
  (see [SECURITY.md](SECURITY.md)).
- **Documentation** — the [README](README.md) (start with the reproducibility
  contract) and the `docs/` tree: architecture, plugins, querying, reporting.

Before filing a bug, please reproduce on the latest `main` with the no-cloud path:

```bash
csbench run --domain agent-runtime --task T1.3 --platform local-sim
```

This is a Developer-Preview project maintained on a best-effort basis; see
[GOVERNANCE.md](GOVERNANCE.md) for how decisions are made and
[CONTRIBUTING.md](CONTRIBUTING.md) for how to help.
