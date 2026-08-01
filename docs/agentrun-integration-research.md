# Aliyun AgentRun — integration research

Source notes behind `AliyunAgentRunAdapter` (`domains/agent_runtime/adapters/
cn_clouds.py`). This is what a wired transport must implement; it does NOT touch
tasks or scoring — the runtime's own session / retry / trace behaviour is what
gets measured, so it is surfaced as observed, never re-implemented.

API surface below reflects AgentRun API version **2025-09-10**. Re-verify
against the current RAM authorization doc before wiring; treat any drift here as
a bug in this file.

## Planes and endpoints

AgentRun splits control and data planes onto two hosts, both region-templated:

| Plane | Service (endpoint template) | Host | Used for |
|---|---|---|---|
| Control | `agentrun` | `agentrun.<region>.aliyuncs.com` | Create/Delete AgentRuntime + Endpoint (T0.1 / T0.2) |
| Data | `agentrun-data` | `agentrun-data.<region>.aliyuncs.com` | InvokeRuntime, Memory (sessions, tool calls, T1.x/T4.x) |

`ManagedAgentRuntimeAdapter.endpoint()` resolves the control host,
`data_endpoint()` the data host (falls back to control when a cloud does not
split planes; AgentRun does split). A dedicated/private region overrides both via
`target['endpoint']` / `target['data_endpoint']`.

## Sessions are a header, not a resource

There is **no `CreateSession` API**. A session is the `X-AgentRun-Session-ID`
header carried on `InvokeRuntime`: the first invoke with a fresh id starts the
session, subsequent invokes with the same id continue it. So:

- `create_session()` mints an id locally (no API call); the runtime materialises
  it on first `InvokeRuntime`.
- `destroy_session()` has no direct API — a session expires; a wired transport
  should stop referencing the id (and rely on Memory delete if state was stored).
- This is why `SESSION_CREATE` maps to `agentrun:InvokeRuntime`, not a session
  action.

## Capability token → minimal RAM action (map in `PERMISSION_MAP`)

| Token | RAM action(s) | Notes |
|---|---|---|
| `session:create` | `agentrun:InvokeRuntime` | session = header on invoke |
| `session:state` | `agentrun:CreateMemory`, `agentrun:RetrieveMemory`, `agentrun:UpdateMemory` | Memory API is the durable store |
| `tool:invoke` | `agentrun:InvokeRuntime` | |
| `tool:register` | `agentrun:ActivateTemplateMCP` | MCP template activation path |
| `trace:read` | *(none)* | traces go to ARMS, not read back via an AgentRun API |
| `trace:export` | *(none)* | export is ARMS/OTel backend, out of scope for the RAM gate |
| `provision:create` | `agentrun:CreateAgentRuntime`, `agentrun:CreateAgentRuntimeEndpoint` | T0.1 deploy |
| `provision:delete` | `agentrun:DeleteAgentRuntime`, `agentrun:DeleteAgentRuntimeEndpoint` | T0.2 teardown |

`trace:read` / `trace:export` map to an empty list *on purpose*: the capability
is delivered through ARMS, so there is no AgentRun RAM action to require. The
empty entry is a real mapping (the token is covered), not a gap — see the
`test_cn_cloud_permissions.py` completeness guard.

## Preflight permission verification (`_probe_permissions`)

Open-core returns `None` (unverified → WARNING that lists the minimal actions).
A wired adapter overrides `_probe_permissions(actions)` to fail fast BEFORE
provisioning:

1. `sts:GetCallerIdentity` — resolve the calling principal (also a cheap
   credential/connectivity check).
2. RAM `SimulatePrincipalPolicy` (or `ram:SimulatePolicy`) for the principal over
   `actions` on the run's resource scope — return `(ok, missing)` where `missing`
   is the subset denied.
3. Missing actions → CRITICAL preflight failure (no provisioning, `invalid`
   record). This is what turns a "billed halfway then denied" run into an early,
   free failure — the whole point of the preflight gate.

## Wiring checklist (what a live transport must honour)

- Build the SDK client through `ClientFactory` / `register_builder("aliyun", …)`;
  read timeouts + retry from `ClientContext.policy` (`ClientPolicy`), and bound
  each call by `ClientContext.deadline_s` (`policy.bounded_read_timeout(remaining)`).
  Do not invent a per-adapter timeout.
- Tag every created resource (runtime, endpoint) with `adapter.resource_tags()`
  so a crashed run's orphans are reap-able by `csbench sweep --provider aliyun`.
- A live run is gated: it only executes with `--allow-live` / `CSBENCH_ALLOW_LIVE`
  (the cost safety-belt); honour `target['live_limits']` for load/soak dimensions.
- The pinned tool universe must be reachable from the cloud runtime — expose
  `mock_tools` on a tunnel and set `CSBENCH_MOCK_TOKEN` so it is not an open
  public surface; the cloud agent presents `X-Clousight-Token`.
