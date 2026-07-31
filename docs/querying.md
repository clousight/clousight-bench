# Querying results

Every run persists a `0.2` record under `results/<domain>/<platform>/`. The
analytics layer flattens those records at read time into four SQL-queryable
views, so measurements, findings, cost and time series from **any** cloud or
product are queried the same way — `domain` and `platform` are just columns.

`csbench query` (SQL) and any parquet/`series` read need the `[store]` extra:

```bash
pip install 'clousight-bench[store]'
```

`csbench export` to CSV/JSONL (for `records` / `measurements` / `findings`)
works without it.

## The four views

Only records whose `record_digest` verifies are included (tamper-evident); run
plan aggregates are skipped. All four share `run_id / domain / task_id /
platform`, so any join works.

**`records`** — one row per run:

```
run_id, domain, task_id, platform, task_revision, scorer_revision,
status, started_at, finished_at,
benchmark_fp, environment_fp, implementation_fp, record_digest,
region, mode, cost_usd
```

`cost_usd` comes from `extensions["pricing"].cost_usd` (NULL when the pricing
enricher did not cover the run).

**`measurements`** — one row per scored measurement:

```
run_id, domain, task_id, platform, benchmark_fp, environment_fp,
name, value_num, value_str, unit, evidence, aggregation, sample_count
```

Numeric values go to `value_num`; label/text values (e.g.
`recovery_mode = "auto-retry"`) go to `value_str`.

**`findings`** — one row per finding:

```
run_id, domain, task_id, platform, code, severity, summary, evidence
```

**`series`** — the high-frequency time series (from each run's
`series.parquet`):

```
run_id, domain, task_id, platform, benchmark_fingerprint, series, t, value, unit
```

## Examples

Compare a dimension across clouds:

```bash
csbench query "SELECT platform, avg(value_num) avg_cold_ms
               FROM measurements WHERE name='cold_start_ms'
               GROUP BY platform ORDER BY avg_cold_ms"
```

Cost rollup by product and platform (avoid `cost` as an alias — it is a DuckDB
keyword):

```bash
csbench query "SELECT domain, platform, count(*) runs, sum(cost_usd) total_cost
               FROM records GROUP BY 1, 2"
```

Join a scalar to its time series:

```bash
csbench query "SELECT r.platform, s.t, s.value
               FROM series s JOIN records r USING (run_id)
               WHERE s.series='warm_start_ms'"
```

Shortcut for a whole table, no SQL:

```bash
csbench query --table measurements --where "task_id='T1.1'" --format csv
```

## Exporting for external tools

Write a long table to parquet / csv / jsonl for a notebook or BI tool:

```bash
csbench export measurements --out ./measurements.parquet
csbench export records --out ./records.csv --format csv
```
</content>
