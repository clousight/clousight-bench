# Clousight Bench

**云计算指北 / [Clousight](https://clousight.com) 出品的云产品可复现测评框架.**

Clousight Bench measures the **platform runtime engineering** of managed cloud
products — session hosting, tool-failure recovery, trace completeness, cost
attribution — not model intelligence. The core orchestrates one lifecycle:

```
provision → setup → execute → collect → teardown → score → report
```

Everything product-specific is a plugin.

## The reproducibility contract

Every number is classified before you trust it, and every result record carries
`config_hash` + `runner_version` + `evidence_layer`:

| Evidence layer | Meaning | Reproducibility |
|---|---|---|
| **C** | Controlled-variable measurement | Precisely reproducible |
| **B** | Environment observation | Method reproducible; numbers vary by environment |
| **A** | Documentation reading | Vendor-stated, not measured |
| **D** | Marketing material | Never load-bearing |

Results are always reported **per dimension — never a blended score**.

## Quick start (no cloud account)

```bash
pip install clousight-bench
csbench list --verbose
csbench run --domain agent-runtime --task T1.3 --platform local-sim
csbench report
```

Optional time-series store (Parquet + DuckDB): `pip install clousight-bench[store]`

## Learn more

- [Architecture](architecture.md) — the lifecycle, plugin contracts, and evidence model.
- [API reference](reference.md) — the core data schema and plugin base classes.
- [Releasing](RELEASING.md) — how versions get published.
- [Contributing](https://github.com/clousight/clousight-bench/blob/main/CONTRIBUTING.md)
  and the [Roadmap](https://github.com/clousight/clousight-bench/blob/main/ROADMAP.md).
