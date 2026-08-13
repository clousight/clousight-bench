# In-region probe carrier (data-plane)

Latency- and load-class measurements must not be taken from the operator's
laptop: the WAN path and local proxy contaminate them (inflated TTFT, phantom
transport errors). Clousight Bench splits **control plane** (the `csbench`
process that orchestrates, scores, and persists) from **data plane** (the load
generator + measurement client that actually drives the platform under test).
The data plane runs **in the target region** on a *probe carrier*. The control
plane never sits in the measurement path.

## Control ↔ data plane over OSS (no inbound to the carrier)

The two planes talk **only through object storage** — the carrier has no public
inbound surface and the control plane never opens a connection to it:

```
control plane  ──write job.json──▶  OSS  ◀──poll──  probe carrier   carrier ──▶ platform-under-test
control plane  ◀──read result────  OSS  ◀──write──  probe carrier   carrier ──▶ OSS (telemetry)
```

Modules (all in the core package, under `probe/`): `oss_channel` (the wire
protocol — job / result / progress / heartbeat / stop keys), `agent_loop` (the
carrier-side poller), `oss_dispatch_client` (`OssProbeClient`, the control-side
client — a drop-in for the legacy HTTP `RemoteProbeClient`). The measured numbers
come from carrier→platform calls, in-region; the OSS hops carry only control
messages and bulk telemetry.

## Carrier = ECS (not a container)

The carrier needs an isolated, in-region compute that any open-source user can
stand up in their own account without a private artifact. On Aliyun that rules
out every **container** option and points to a plain **ECS instance**:

| Carrier | Needs a container image? | Open-source friendly | Isolated from the platform-under-test |
|---|---|---|---|
| ECI / ACS / ACK | **Yes** — image in a registry | ✗ a private/custom image can't be shipped in an OSS tool | ✓ |
| FC (function, OSS zip) | No | ✓ | ✗ shares the FC platform with FC-backed AgentRun |
| **ECS (VM, stock OS image)** | **No** — boots a stock OS image | ✓ **stock OS + your public package** | ✓ dedicated VM |

ACS/ACK are container services (ACS runs *on* ECI) — they inherit the image
requirement. A container image is either a private artifact (un-shippable for an
open-source tool) or requires a paid ACR Enterprise subscription to auto-mirror a
public base — friction either way. **ECS boots a stock, Aliyun-hosted OS image
(Aliyun Linux / Ubuntu) — free, public, no registry, no custom image** — so it
sidesteps the container-image problem entirely while keeping a dedicated,
isolated VM for clean load generation.

Cost: a small ECS is pay-as-you-go per second (~¥0.05–0.1/hr); it is created at
campaign start and destroyed at the end, like any other per-run resource.

## The code is the published package (`pip install`)

`clousight-bench` is already a pip package, so the carrier installs the probe the
ordinary way — **no image, no private artifact, no code upload of a second
copy**:

```
ECS boots a stock Aliyun OS image (python3 + pip present)
  user-data (cloud-init):
    pip install -i <aliyun-pypi-mirror> "clousight-bench[probe]==<control-plane version>"
    cb-probe            # console entry point -> agent_loop, reads CB_PROBE_* env
```

- **Reproducible**: the control plane passes its own version, so the carrier runs
  the *same* probe code.
- **Open source**: it installs the public package everyone else installs.
- **Change the code → just release (or, in dev, re-upload the wheel)** — nothing
  to rebuild on the carrier.

## China-region constraints (why every source is Aliyun-internal)

Verified live on cn-hangzhou: **docker hub, github, and pypi.org are all
throttled/blocked from the region**. The design fetches nothing from those:

| What the carrier needs | Source | Egress |
|---|---|---|
| OS image | stock Aliyun image (boot) | none |
| the probe package | Aliyun PyPI mirror (`mirrors.cloud.aliyuncs.com/pypi`) once published; a wheel on **OSS** in dev | VPC-internal |
| deps (`requests`, `oss2`) | same Aliyun PyPI mirror | VPC-internal |
| control channel + telemetry | **OSS internal endpoint** | VPC-internal |
| **the AgentRun endpoint** | its public endpoint (no VPC-internal variant) | **NAT** |

Only the AgentRun hop needs egress, so a **NAT gateway** is provisioned — gated
by a separate `enable_nat` flag and torn down after the run, because it is the
only hourly-billing piece (see the terraform module). Everything else is
VPC-internal.

## Reproducible + automatable, per-run

The carrier is not terraform-managed shared infra — it is an **ephemeral,
per-campaign resource** created and destroyed by the runtime (SDK/API), the same
way AgentRuntime instances and the `agent.zip` artifact are. Terraform owns only
the persistent base (VPC/subnet/SG, RAM identities, the `enable_nat` toggle).

## Generalizing across clouds

The pattern is vendor-neutral where it matters and vendor-specific only in the
launch layer:

- **Vendor-neutral**: the OSS-mediated control channel, the probe logic, and
  `pip install clousight-bench[probe]`.
- **Per-vendor**: the carrier launch (Aliyun ECS user-data; AWS EC2/Fargate
  user-data; GCP GCE startup-script) and the object-store + mirror endpoints.

Adding a cloud means writing that cloud's carrier launcher pointing at the same
package + object-store channel — not a new image pipeline.

## Live bring-up runbook (Aliyun ECS carrier)

A one-time-per-run live checklist. Two identities are in play: the **main
account AK** runs `terraform apply` (it creates RAM/NAT); the **benchmark RAM
user's static AK** runs the campaign. Use static AK for the campaign — a dev-wheel
presigned URL signed with a temporary/STS credential is capped by that token's
expiry and can fail mid-boot.

**0. Find a stock OS image id** (no private image needed):

```bash
aliyun ecs DescribeImages --RegionId cn-hangzhou --OSType linux \
  --ImageOwnerAlias system --Architecture x86_64 \
  | jq -r '.Images.Image[].ImageId' | grep '^aliyun_3' | head
```

**1. Apply terraform** (probe RAM + NAT + image), with the main account AK:

```bash
cd infra/terraform/aliyun-iam
terraform apply \
  -var enable_probe=true \
  -var enable_nat=true \
  -var ecs_image_id=aliyun_3_x64_20G_alibase_XXXX.vhd
```

Expect: NAT gateway + EIP + SNAT, the probe RAM role, and the benchmark user's
`ecs:RunInstances/DescribeInstances/DeleteInstances` ops policy. NAT/EIP are the
only hourly-billed resources — tear them down when done (step 8).

**2. Export the run config** and enable the dev-wheel fallback (until the running
version is published to the Aliyun PyPI mirror):

```bash
terraform output -raw csbench_config > /tmp/agent-runtime-aliyun.local.yaml
# then add one line under `target:` in that file →   probe_dev_wheel: true
```

The config already carries `oss_bucket`, `region`, `eci_vswitch_id`,
`eci_security_group_id`, `eci_probe_role`, `ecs_image_id`, `ecs_instance_type`.

**3. Credentials** (benchmark RAM user, static AK):

```bash
export ALIBABA_CLOUD_ACCESS_KEY_ID=<ram-user-ak>
export ALIBABA_CLOUD_ACCESS_KEY_SECRET=<secret>
```

**4. Preflight** (optional but cheap):

```bash
csbench doctor --config /tmp/agent-runtime-aliyun.local.yaml --provider aliyun
```

**5. Single-task live verify first** (small cost) — a plan with one task (e.g. the
TTFT probe), `--probe ecs`:

```bash
csbench run-plan single-task-plan.yaml \
  --config /tmp/agent-runtime-aliyun.local.yaml \
  --probe ecs --allow-live --cost-budget 5
```

**6. Watch progress** in another terminal:

```bash
csbench progress --watch          # most-recent campaign; --campaign <id> to pin
```

**7. Verify** the result: `observations.vantage.carrier == "ecs"` and
`in_vpc == true`, no `CarrierError`, and the probe's OSS telemetry synced into the
results dir.

**8. Reap + tear down:**

```bash
csbench sweep --provider aliyun                    # dry-run: list orphans
csbench sweep --provider aliyun --confirm          # delete stray ECS/AgentRun
terraform apply -var enable_probe=true -var enable_nat=false \
  -var ecs_image_id=aliyun_3_x64_20G_alibase_XXXX.vhd   # stop NAT/EIP billing
```

**Troubleshooting (live-only unknowns to confirm):**

- `Forbidden.RamRoleNotExist` / PassRole 403 on first launch → usually RAM
  propagation (2–5 min); the role must trust `ecs.aliyuncs.com`.
- Instance stuck / pip can't fetch the wheel → the instance has **no public IP**;
  egress is via NAT only. Confirm the presigned URL uses the VPC-internal OSS host
  and the NAT reaches both the PyPI mirror and the AgentRun endpoint.
- `DescribeInstances` field shapes (`status`, `creation_time`, `instance_id`) —
  confirm against the live response the carrier/reaper assume.
