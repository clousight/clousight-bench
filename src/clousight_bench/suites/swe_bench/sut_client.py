"""SweSutClient: invoke the AgentRun-hosted agent per SWE-bench instance.

The client is the suite's ONLY seam onto the agent-runtime domain.  It wraps
the orchestrator's ProviderAdapter (``Target.handle``) and per instance:

1. creates a session on the adapter,
2. sends :func:`encode_swe_invoke` through the transport's PUBLIC
   ``invoke_openai(session_id, body)`` seam (formal contract on
   ``RuntimeTransport``; ONLY the shared ``MockRuntimeTransport`` opts in — by explicit isinstance
   check — to running the bundled agent in-process, which is the exact code
   deployed to AgentRun, so the whole path is testable offline; any other
   transport lacking the seam raises ``RuntimeError`` instead of silently
   faking a cloud run),
3. decodes the response with :func:`decode_swe_result` and maps the agent's
   OpenInference-style spans onto the sut-span **v3 (OTel-native)** schema.

Span mapping rules (see ``core/sut_span.py`` for the v3 schema):

- OpenInference kind ``LLM`` -> ``gen_ai.operation.name="chat"``; everything
  else (``CHAIN`` / ``TOOL`` / unknown) -> ``"execute_tool"`` +
  ``gen_ai.tool.name`` from the span name.
- ids: the agent's raw ids are kept when already W3C-hex-shaped, otherwise a
  deterministic hex id is derived (sha256 of the raw id) — the same raw id
  always maps to the same hex id, preserving the parent/child forest.
- trace id: the span's own, else the transport's last observed trace id
  (public ``last_trace_id``), else derived from the instance id.
- times: agent spans carry no timestamps, so all spans of one invoke share the
  invoke's wall-clock bounds, as integer nanoseconds (acceptable by design).
- attributes: passed through with any single string value truncated to 8 KiB so
  the 64 KiB total cap cannot be blown by one oversized value; an agent error
  lands in ``error.message``.

Every mapped span MUST pass ``validate_span`` — a failure raises ``ValueError``
immediately (loud, never a silently-dropped span).
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from typing import Any

from clousight_bench.core.sut_span import validate_span
from clousight_bench.domains.agent_runtime.adapters.transport import MockRuntimeTransport
from clousight_bench.domains.agent_runtime.protocol import decode_swe_result, encode_swe_invoke

#: Per-attribute string value cap: 8 KiB (the total attrs cap is 64 KiB).
_MAX_ATTR_VALUE_BYTES = 8192


def _hex_id(raw: str, length: int) -> str:
    """The raw id itself when already W3C-hex-shaped, else a deterministic
    sha256-derived hex id (same raw id -> same hex id, so the forest survives).

    No domain separator: a derived id could in principle collide with another
    span's genuine hex id — accepted (agent ids are not adversarial), noted for
    honesty. Hex-shaped ids are case-folded; non-hex raw ids hash case-sensitively.
    """
    candidate = raw.lower()
    if len(candidate) == length:
        try:
            int(candidate, 16)
        except ValueError:
            pass
        else:
            return candidate
    return hashlib.sha256(raw.encode()).hexdigest()[:length]


def _truncate_attr(value: Any) -> Any:
    """Truncate a single string attr value to 8 KiB (UTF-8); pass others through."""
    if isinstance(value, str) and len(value.encode("utf-8")) > _MAX_ATTR_VALUE_BYTES:
        return value.encode("utf-8")[:_MAX_ATTR_VALUE_BYTES].decode("utf-8", errors="ignore")
    return value


class SweSutClient:
    """Real-SUT client: one adapter, one optional provisioned runtime, N solves.

    Lifecycle is owned by the SUITE: ``prepare()`` calls :meth:`provision`,
    ``run()`` calls :meth:`solve` per instance, ``teardown()`` calls
    :meth:`close` (best-effort).
    """

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter
        self._runtime_id: str | None = None

    # --- lifecycle ------------------------------------------------------

    def provision(self, agent_mode: str | None = None) -> None:
        """Provision the SUT runtime via the adapter (tags/ledger stamped there).

        ``agent_mode == "llm"``: when ``DASHSCOPE_API_KEY`` is set in the DRIVER
        host's environment, it is forwarded as a provision-time runtime env var
        so the deployed agent can call the model.  The value travels ONLY inside
        the provision spec to the cloud CreateAgentRuntime API — it must never
        land in RawArtifacts, persisted records, or the LaunchSpec.
        """
        spec: dict[str, Any] = {}
        if agent_mode == "llm":
            key = os.environ.get("DASHSCOPE_API_KEY")
            if key:
                spec["environment_variables"] = {"DASHSCOPE_API_KEY": key}
        result = self._adapter.provision(spec)
        self._runtime_id = str(getattr(result, "runtime_id", "") or "") or None

    def close(self) -> None:
        """Best-effort deprovision of the runtime created by :meth:`provision`.

        Never raises; a second call is a no-op (no double deprovision).
        """
        runtime_id, self._runtime_id = self._runtime_id, None
        if runtime_id is None:
            return
        try:
            self._adapter.deprovision(runtime_id)
        except Exception:  # noqa: BLE001 - teardown is best-effort by contract
            return

    # --- invocation -----------------------------------------------------

    def _transport(self) -> Any:
        """The adapter's cached runtime transport (``transport()`` seam).

        Always the cached-transport seam — a fresh transport per call would
        lose ``last_trace_id`` set during the invoke.
        """
        return self._adapter.transport()

    def _invoke(self, transport: Any, session_id: str, openai_body: dict[str, Any]) -> dict[str, Any]:
        """Send the OpenAI body through *transport*'s public ``invoke_openai`` seam.

        In-process execution of the bundled agent is an EXPLICIT opt-in for the
        shared ``MockRuntimeTransport`` only (the same code deployed to
        AgentRun, so the full solve path stays exercisable with no cloud
        account).  A transport without a wired ``invoke_openai`` raises
        ``CapabilityNotSupported`` (the base default) instead of silently
        faking a cloud run.
        """
        if isinstance(transport, MockRuntimeTransport):
            from clousight_bench.domains.agent_runtime.agent_bundle import agent

            return agent.handle_chat_completion(openai_body)
        return dict(transport.invoke_openai(session_id, openai_body))

    def solve(self, instance: dict[str, Any], agent_mode: str) -> dict[str, Any]:
        """Solve one SWE-bench instance on the deployed agent.

        *instance* is the full dataset row (``_load_instance`` shape); its gold
        ``patch`` travels to the agent ONLY in oracle mode (protocol-enforced).

        Returns ``{"model_patch": str, "spans": list[sut-span-v2 dict],
        "usage_events": list[dict]}``.  ``usage_events`` carries at most one
        ``llm_tokens`` event, only when the agent reported ``total_tokens > 0``.
        Raises ``ValueError`` if any mapped span fails ``validate_span``.
        """
        iid = str(instance.get("instance_id") or "")
        body = encode_swe_invoke(instance, agent_mode=agent_mode)
        transport = self._transport()  # resolved ONCE; reused for span mapping
        session_id = self._adapter.create_session()
        t_start = time.time()
        try:
            response = self._invoke(transport, session_id, body)
            t_end = time.time()  # invoke bounds only — never includes destroy_session
        finally:
            try:
                self._adapter.destroy_session(session_id)
            except Exception:  # noqa: BLE001 - session cleanup is best-effort
                pass
        # A non-monotonic clock must never trip validate_span after paying cloud cost.
        t_end = max(t_end, t_start)

        decoded = decode_swe_result(response)
        fallback_trace_id = str(getattr(transport, "last_trace_id", "") or "")
        spans = [
            self._map_span(raw, iid, t_start, t_end, fallback_trace_id)
            for raw in decoded["spans"]
            if isinstance(raw, dict)
        ]
        usage_events: list[dict[str, Any]] = []
        total_tokens = int(decoded["usage"].get("total_tokens") or 0)
        if total_tokens > 0:
            usage_events.append(
                {"kind": "llm_tokens", "value": total_tokens, "instance_id": iid, "mode": agent_mode}
            )
        return {
            "model_patch": decoded["model_patch"],
            "spans": spans,
            "usage_events": usage_events,
        }

    # --- span mapping -----------------------------------------------------

    def _map_span(
        self,
        raw: dict[str, Any],
        instance_id: str,
        t_start: float,
        t_end: float,
        fallback_trace_id: str,
    ) -> dict[str, Any]:
        """Map one agent span dict onto the sut-span v2 schema; validate loudly.

        *fallback_trace_id* is the transport's public ``last_trace_id`` read once by
        ``solve()`` after the invoke (live response headers set it).
        """
        attributes = raw.get("attributes")
        attributes = dict(attributes) if isinstance(attributes, dict) else {}
        oi_kind = str(attributes.get("openinference.span.kind") or raw.get("kind") or "")
        raw_trace = str(raw.get("trace_id") or "") or fallback_trace_id or f"trace-{instance_id}"
        raw_span_id = str(raw.get("span_id") or "") or uuid.uuid4().hex[:16]
        raw_parent = str(raw.get("parent_span_id") or raw.get("parent_id") or "")
        name = str(raw.get("name") or "span")

        v3_attrs: dict[str, Any] = {k: _truncate_attr(v) for k, v in attributes.items()}
        if oi_kind.upper() == "LLM":
            v3_attrs.setdefault("gen_ai.operation.name", "chat")
        else:
            v3_attrs.setdefault("gen_ai.operation.name", "execute_tool")
            v3_attrs.setdefault("gen_ai.tool.name", name.rsplit(".", 1)[-1])
        raw_status = str(raw.get("status") or "ok").lower()
        if raw_status not in ("ok", "error", "unset"):
            raise ValueError(
                f"agent span {name!r} has out-of-vocab status {raw.get('status')!r} "
                "(expected ok|error|unset) — refusing to guess"
            )
        status = {"ok": "OK", "error": "ERROR", "unset": "UNSET"}[raw_status]
        if status == "ERROR" and raw.get("error"):
            v3_attrs["error.message"] = _truncate_attr(str(raw["error"]))

        span: dict[str, Any] = {
            "span_id": _hex_id(raw_span_id, 16),
            "trace_id": _hex_id(raw_trace, 32),
            "parent_span_id": _hex_id(raw_parent, 16) if raw_parent else "",
            "name": name,
            "start_unix_nano": int(t_start * 1_000_000_000),
            "end_unix_nano": int(t_end * 1_000_000_000),
            "status": status,
            "attributes": v3_attrs,
        }
        validate_span(span)  # raises ValueError — a bad span must never be silent
        return span
