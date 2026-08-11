"""Real-platform adapter: AWS Bedrock AgentCore.

Like the CN-cloud declarations in ``cn_clouds.py``, this is a thin statement on
top of ``ManagedAgentRuntimeAdapter``: it names only what is AWS-specific -- the
provider, the control/data-plane services used to template the endpoints, the
platform docs, and the minimal IAM action map per benchmark. The shared body
owns credential resolution, endpoint resolution, the mock<->real transport
switch, preflight, and the runtime-op delegation.

AgentCore splits its planes: the control plane (create/delete a runtime and its
endpoint) is ``bedrock-agentcore-control.<region>.amazonaws.com`` and the data
plane (invoke, memory) is ``bedrock-agentcore.<region>.amazonaws.com`` -- hence
the separate ``data_endpoint_service``. Both share the ``bedrock-agentcore:``
IAM prefix.

Status stays ``skeleton``: the *real* transport is not wired to a live account,
so ``csbench run`` refuses AWS in real mode up front. ``mode: mock`` runs it
end-to-end via the shared simulated runtime, so the whole harness (identity +
endpoint + permission plumbing) is exercisable without an account. Wiring the
real path means implementing ``NotWiredCloudTransport``'s ops against the AWS
SDK (or installing a commercial pack that registers a wired runtime provider);
it must NOT touch tasks/ or scoring -- the runtime's own behaviour is measured
as observed, never re-implemented.
"""

from __future__ import annotations

from clousight_bench.domains.agent_runtime import permissions as perm
from clousight_bench.domains.agent_runtime.adapters.managed import ManagedAgentRuntimeAdapter


class AwsAgentCoreAdapter(ManagedAgentRuntimeAdapter):
    """AWS Bedrock AgentCore Runtime. Sessions map to AgentCore runtime sessions
    (an ``X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`` on InvokeAgentRuntime);
    state maps to AgentCore Memory events."""

    name = "aws-agentcore"
    status = "skeleton"
    provider = "aws"
    endpoint_service = "bedrock-agentcore-control"  # control plane: create/delete runtime
    data_endpoint_service = "bedrock-agentcore"  # data plane: invoke / memory
    DOCS = "https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html"
    # Abstract capability token -> real AgentCore IAM action(s). Both planes share
    # the ``bedrock-agentcore:`` prefix.
    PERMISSION_MAP = {
        # Sessions are implicit: a session is a runtime-session-id header on
        # InvokeAgentRuntime (there is no CreateSession API) -> maps to invoke.
        perm.SESSION_CREATE: ["bedrock-agentcore:InvokeAgentRuntime"],
        perm.SESSION_STATE: [
            "bedrock-agentcore:CreateEvent",
            "bedrock-agentcore:ListEvents",
            "bedrock-agentcore:RetrieveMemoryRecords",
        ],
        perm.TOOL_INVOKE: ["bedrock-agentcore:InvokeAgentRuntime"],
        # Tools register through an AgentCore Gateway target.
        perm.TOOL_REGISTER: [
            "bedrock-agentcore:CreateGateway",
            "bedrock-agentcore:CreateGatewayTarget",
        ],
        # Traces export to CloudWatch / X-Ray via OTel, not read back through an
        # AgentCore API -> no agentcore action (reading via the backend is out of
        # scope for now).
        perm.TRACE_READ: [],
        perm.TRACE_EXPORT: [],
        perm.PROVISION: [
            "bedrock-agentcore:CreateAgentRuntime",
            "bedrock-agentcore:CreateAgentRuntimeEndpoint",
        ],
        perm.DEPROVISION: [
            "bedrock-agentcore:DeleteAgentRuntime",
            "bedrock-agentcore:DeleteAgentRuntimeEndpoint",
        ],
    }
