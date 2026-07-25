# Examples

Copy-paste runnable flows. None of these need a cloud account.

## 1. Agent runtime: fault recovery, two policies

```bash
# auto-retry runtime absorbs a transient tool fault -> completes
csbench run --domain agent-runtime --task T1.3 --platform local-sim

# fail-fast runtime surfaces the same fault -> aborts
csbench run --domain agent-runtime --task T1.3 --platform local-sim \
    --config configs/local-sim.fail-fast.yaml
```

The mock tool universe is pinned and the fault is deterministic (the 3rd call
fails), so both runs are replayable and their `config_hash` values are stable.

## 2. Big data: a batch job on a local "cluster"

```bash
csbench run --domain bigdata-emr --task J1.1 --platform local-process
```

Same lifecycle as the agent-runtime run, but the "system under test" is a
packaged subprocess workload reached over the cross-language JSONL protocol —
proof the abstraction carries a non-agent product. Resolve it safely from either
an editable or wheel install:

```python
from clousight_bench.core.resources import reference_workload_path
from clousight_bench.core.workload import WorkloadEngine

engine = WorkloadEngine(reference_workload_path("wordcount-py"))
```

## 3. Aggregate into a comparison report

```bash
csbench report            # writes results/comparison.md
```

## 4. Point a real platform at it

1. `cp configs/agent-runtime.aliyun.example.yaml my-aliyun.yaml` and fill it in.
2. Expose the mock server where the cloud runtime can reach it:
   `python -m clousight_bench.domains.agent_runtime.mock_tools --port 8770`
3. Implement the adapter, then:
   `csbench run --domain agent-runtime --task T1.3 --platform aliyun-agentrun --config my-aliyun.yaml`
