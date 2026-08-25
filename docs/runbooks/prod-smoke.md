# Prod-profile live smoke runbook (OWNER-RUN)

Verifies the ecs-resident controller end-to-end on real cloud. **Requires the
MAIN-account AK** (terraform apply) and bills a short NAT + one small ECS
controller — keep the campaign to 1–2 light tasks.

The in-memory end-to-end (`tests/test_prod_e2e_inmemory.py`) already proves the
orchestration logic offline; this runbook proves the live wiring
(terraform → controller boot → OSS round-trip → self-destruct).

## Prerequisites

- `configs/agent-runtime-aliyun.local.yaml` with `target.oss_bucket` + `target.region` (gitignored, has secrets).
- MAIN-account AK for terraform: `export ALICLOUD_ACCESS_KEY=... ALICLOUD_SECRET_KEY=... ALICLOUD_REGION=cn-hangzhou`.
- Sub-account AK (for the controller's SDK calls, injected via the instance role) is provisioned by `controller.tf`.
- A slim plan, e.g. `configs/prod-smoke.plan.yaml`:
  ```yaml
  version: "1"
  domain: agent-runtime
  platform: aliyun-agentrun
  tasks:
    - task: T1.13   # startup curve, ~27s
    - task: T2.1    # tool registration
  ```

## Happy path

```bash
# 1. Submit — writes launch to OSS + terraform apply (controller + NAT). MAIN AK.
csbench submit configs/prod-smoke.plan.yaml \
  --config configs/agent-runtime-aliyun.local.yaml \
  --watchdog-timeout 1800
# prints the campaign_id, e.g. camp-ab12cd34

# 2. Laptop may now go offline. Later, check progress (reads OSS):
csbench status  camp-ab12cd34 --config configs/agent-runtime-aliyun.local.yaml
csbench logs    camp-ab12cd34 --config configs/agent-runtime-aliyun.local.yaml

# 3. When status shows done=DONE, pull results (JSON + parquet sidecars):
csbench fetch   camp-ab12cd34 --config configs/agent-runtime-aliyun.local.yaml --dest results/prod-smoke
# Inspect results (csbench report was retired; use query or open the JSON directly):
csbench query   --results results/prod-smoke --format table

# 4. Confirm self-destruct: the controller reaps runtime+NAT+self on DONE.
#    Verify 0 residual (should print 0):
terraform -chdir=infra/terraform/aliyun-iam output -raw controller_instance_id   # null after self-destruct
```

**Pass criteria:** status reaches `done=DONE`; fetch returns each task's JSON (+
`T1.13.series.parquet`); no residual controller instance / NAT / AgentRuntime.

## Fault injection — teardown backstop

Proves the leak-prevention path (cleanup independent of a live controller).

```bash
# Submit as above, then while it is mid-run, kill the controller from the console
# (or `aliyun ecs StopInstance`/DeleteInstance) to simulate a crash before self-destruct.

# The laptop backstop reaps residuals from the OSS-synced ledger + terraform destroy:
csbench teardown camp-ab12cd34 --config configs/agent-runtime-aliyun.local.yaml

# Verify: 0 residual AgentRuntimes, NAT gone, controller gone.
```

**Pass criteria:** `teardown` prints `residual_deleted` covering any runtime the
ledger recorded, `destroyed=True`; a follow-up `list_agent_runtimes` shows 0.

## Cost note

Keep the campaign small. NAT + one ECS controller for a 1–2 task run is a few
minutes of billing (a few CNY). Always confirm self-destruct or run `teardown`
before leaving.
