"""Abstract permission capability tokens for the agent-runtime domain.

The minimal permissions a run needs are a (benchmark x cloud) matrix. We split
that cleanly:

- A **Task** declares WHICH abstract capabilities it exercises (these tokens),
  independent of any cloud -- e.g. tracing needs to read traces, tool activation needs to
  register tools.
- An **Adapter** (per cloud) maps each token to that cloud's concrete minimal
  IAM/RAM actions (`PERMISSION_MAP`) and verifies them.

So the required concrete permissions for a given run = the adapter's mapping of
the task's tokens. Add a dimension -> declare its tokens on the Task; add a
cloud -> declare its token->action map on the Adapter. Neither touches the other.
"""

from __future__ import annotations

SESSION_CREATE = "session:create"
SESSION_STATE = "session:state"
TOOL_INVOKE = "tool:invoke"
TOOL_REGISTER = "tool:register"
TRACE_READ = "trace:read"
TRACE_EXPORT = "trace:export"
PROVISION = "provision:create"
DEPROVISION = "provision:delete"

# Human-readable purpose of each token (surfaced by doctor / docs).
TOKENS: dict[str, str] = {
    SESSION_CREATE: "create and destroy a runtime session",
    SESSION_STATE: "persist and read back session state",
    TOOL_INVOKE: "invoke tools from within a session",
    TOOL_REGISTER: "register a tool (MCP / OpenAPI / native connector)",
    TRACE_READ: "read the runtime's own trace of an invocation",
    TRACE_EXPORT: "export a trace in OTLP form",
    PROVISION: "stand up (deploy) a runtime instance from an artifact",
    DEPROVISION: "tear down (delete) a runtime instance and its resources",
}
