# Workloads

Load generators live one directory per workload. They are deliberately **not**
part of the Python core: the framework talks to them across a process
boundary so a workload can be written in any language — including
closed-source commercial plugins distributed as binaries.

Runnable reference workloads are packaged under
`src/clousight_bench/resources/workloads/` so editable and wheel installs use
the same files. Resolve them with
`clousight_bench.core.resources.reference_workload_path(name)` instead of
constructing repository-relative paths (that resolver is installed-safe;
building a path by hand is not).

## Protocol

```
<workload-dir>/
├── manifest.yaml     # name, version, entrypoint, params, declared metrics
└── <entrypoint>      # any executable
```

1. The engine invokes `<entrypoint> --params <json-file>`.
2. The workload writes JSONL events to stdout:
   - `{"type": "metric", "name": "<str>", "value": <number>}`
   - `{"type": "log", "message": "<str>"}` (optional, folded into result notes)
   - `{"type": "sample", "series": "<str>", "t": <number>, "value": <number>}` (optional; accumulates into `ResultRecord.series`)
   - `{"type": "artifact", "kind": "<str>", "path": "<str>", "media": "<str>"}` (optional; the engine hashes the file, workload doesn't compute its own sha256)
   - `{"type": "result", "ok": <bool>}` (final line, **required**)
3. Exit code 0 **and** a `result` line with `ok: true` = success.

The workload's `name` + `version` + declared metrics are folded into each
result's `config_hash`, so a number always says which load produced it.

## Shipped examples

| Workload | Language | Purpose |
|---|---|---|
| `wordcount-py` | Python | Reference implementation; default load for `bigdata-emr` J1.1. Runs with no external deps. |
| `gsm8k-stats` | Python | Remote-asset-tier example: downloads the real GSM8K test set, verifies its sha256, caches it, and computes corpus statistics. Stdlib-only, no cloud account. |
| `ycsb-wrapper` | Bash → JVM | Polyglot example: wraps [YCSB](https://github.com/brianfrankcooper/YCSB) and translates its output into the protocol. Proves the core needs no Java. |

## Borrow, don't reinvent

For real database / big-data / messaging benchmarks, wrap the mature tool the
industry already trusts rather than reimplementing load generation:

| Domain | Borrow |
|---|---|
| Database / KV | YCSB, sysbench |
| Big data (SQL) | TPC-DS via spark-sql-perf, TPC-H |
| Big data (sort/shuffle) | terasort, HiBench |
| Messaging | OpenMessaging Benchmark |
| Compute / VM | fio, stress-ng, sysbench |
