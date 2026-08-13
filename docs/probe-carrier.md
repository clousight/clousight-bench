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
