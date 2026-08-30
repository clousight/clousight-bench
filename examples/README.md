# Examples

Copy-paste runnable flows. None of these need a cloud account or an API key —
they run the real stage machine over each benchmark's bundled fixture in
`mode: mock`.

```bash
printf 'target:\n  mode: mock\n' > mock.yaml
```

## 1. Run a coding benchmark (agent-runtime)

```bash
csbench run --domain agent-runtime --benchmark swe-bench --platform local-sim --config mock.yaml
```

A full `resolve → prepare → run → score → persist` over SWE-bench Verified's
bundled fixture; emits a `status: completed`, schema-0.4 record. Swap
`--benchmark swe-bench-lite` or `swe-bench-multimodal` for the variants.

## 2. Run an LLM benchmark (llm domain)

```bash
csbench run --domain llm --benchmark mmlu       --platform llm-mock --config mock.yaml
csbench run --domain llm --benchmark gsm8k      --platform llm-mock --config mock.yaml
csbench run --domain llm --benchmark human-eval --platform llm-mock --config mock.yaml
```

Point at a real managed LLM by switching to `--platform llm-endpoint` and a config
with `target: {mode: runtime, endpoint, model, credentials_ref}` (see the
[MMLU](../docs/mmlu-suite.mdx) doc).

## 3. Run a data-systems benchmark

```bash
csbench run --domain data-warehouse   --benchmark tpc-ds --platform duckdb-local   --config mock.yaml
csbench run --domain key-value        --benchmark ycsb   --platform ycsb-local     --config mock.yaml
csbench run --domain transactional-db --benchmark tpc-c  --platform benchbase-local --config mock.yaml
```

`tpc-ds` / `tpc-h` also run for real against DuckDB (the `[tpcds]`/`[tpch]` extra)
with `target: {mode: runtime}`.

## 4. Browse results

```bash
csbench serve            # web viewer at http://127.0.0.1:8787 (EN | 中文)
csbench query 'SELECT * FROM measurements'   # DuckDB over the canonical records (needs [store])
```

## 5. Point a real platform at it

Config-connect to an already-running service (no provisioning): fill in a
`target.endpoint` / `credentials_ref` and switch `--platform` to the connect
adapter (`llm-endpoint`, `ycsb-endpoint`, `jdbc-endpoint`). For a cloud the
framework provisions, install the provider plugin and use `--allow-live`. See the
[SWE-bench live runbook](../docs/swe-bench-live-runbook.mdx).

To add your own benchmark, see [docs/adding-a-suite.mdx](../docs/adding-a-suite.mdx).
