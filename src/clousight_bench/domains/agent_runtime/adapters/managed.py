"""Managed agent-runtime adapter: one body, four clouds, two transports.

Every managed agent platform (Aliyun AgentRun, Huawei AgentArts, Volcengine
AgentKit -- and the provider-less local reference) shares the same lifecycle:
resolve identity, resolve endpoint, pick a transport, run the task's plan. Only
the *metadata* differs per cloud: provider name, service (for endpoint
templating), and the minimal-permission map. So the four collapse onto this base
and declare only that metadata; the behaviour lives here.

``target['mode']`` picks the transport (default ``real`` for a cloud, always
``mock`` for the provider-less reference):

- ``mock`` -> ``MockRuntimeTransport``: a simulated runtime, runnable end-to-end
  with NO account. The full identity/endpoint/permission plumbing still runs,
  so a cloud's config surface can be dry-run before anything is wired.
- ``real`` -> ``NotWiredCloudTransport``: the honest seam. Runnable only once the
  provider's SDK calls are filled in; until then it is a skeleton and the
  orchestrator refuses it up front (see ``is_runnable_instance``).
"""
from __future__ import annotations

from typing import Any

from clousight_bench.core.clients import ClientFactory
from clousight_bench.core.endpoints import Endpoint, resolve_endpoint
from clousight_bench.domains.agent_runtime.adapters.base import (
    AgentRuntimeAdapter,
    InvocationTrace,
    ToolCall,
)
from clousight_bench.domains.agent_runtime.adapters.transport import (
    MockRuntimeTransport,
    NotWiredCloudTransport,
    RuntimeTransport,
)


class ManagedAgentRuntimeAdapter(AgentRuntimeAdapter):
    """Shared body for managed agent-runtime platforms. Subclasses set metadata."""

    #: Service used to template the CONTROL-plane host (create/delete runtime),
    #: e.g. "agentrun" -> agentrun.<region>.aliyuncs.com.
    endpoint_service: str = ""
    #: Service used to template the DATA-plane host (invoke/session), when it
    #: differs from the control plane (e.g. "agentrun-data", or AWS's separate
    #: data-plane service). Empty -> data plane reuses ``endpoint_service``.
    data_endpoint_service: str = ""
    #: Platform docs surfaced in the not-wired error / remediation.
    DOCS: str = ""

    def __init__(self, target: dict[str, Any] | None = None) -> None:
        super().__init__(target)
        self._transport: RuntimeTransport | None = None

    # --- mode / endpoint / client ------------------------------------------

    @property
    def mode(self) -> str:
        """``mock`` | ``real``. A provider-less adapter (local reference) is
        always mock; a cloud defaults to real and opts into mock via target."""
        if self.provider is None:
            return "mock"
        return str(self.target.get("mode", "real")).lower()

    def endpoint(self) -> Endpoint | None:
        """Resolved CONTROL-plane endpoint (create/delete runtime), or None for a
        provider-less adapter. ``target['endpoint']`` overrides it."""
        if self.provider is None:
            return None
        return resolve_endpoint(
            self.provider,
            self.target.get("region"),
            self.endpoint_service,
            self.target.get("endpoint"),
        )

    def data_endpoint(self) -> Endpoint | None:
        """Resolved DATA-plane endpoint (invoke/session), or None for a
        provider-less adapter. Falls back to the control-plane service when a
        cloud does not split the planes; ``target['data_endpoint']`` overrides."""
        if self.provider is None:
            return None
        return resolve_endpoint(
            self.provider,
            self.target.get("region"),
            self.data_endpoint_service or self.endpoint_service,
            self.target.get("data_endpoint"),
        )

    def client_factory(self) -> ClientFactory:
        """The credential->client seam for real-mode calls."""
        ep = self.endpoint()
        return ClientFactory(
            self.provider,
            self.target.get("region"),
            ep.url if ep else None,
            self.target,
            platform=self.name,
        )

    def _build_transport(self) -> RuntimeTransport:
        if self.mode == "mock":
            return MockRuntimeTransport.from_target(self.target)
        # real mode: a wired runtime provider (commercial pack) supplies the live
        # SDK-backed transport; without one installed, fall back to the honest
        # not-wired seam that names exactly what is missing.
        from clousight_bench.core.registry import get_runtime_provider

        plugin = get_runtime_provider(self.provider)
        if plugin is not None:
            return plugin.build_transport(self)
        ep = self.endpoint()
        return NotWiredCloudTransport(
            self.name, self.provider, ep.url if ep else None,
            self.client_factory(), self.DOCS,
        )

    def _transport_(self) -> RuntimeTransport:
        if self._transport is None:
            self._transport = self._build_transport()
        return self._transport

    # --- lifecycle ----------------------------------------------------------

    def execution_mode(self) -> str:
        """Mock (incl. provider-less local-sim) is simulated; real is live."""
        return "simulated" if self.mode == "mock" else "live"

    def is_runnable_instance(self) -> bool:
        """Mock mode runs the shared simulator regardless of class status. Real
        mode is runnable if the class is already wired OR a commercial pack has
        registered a wired runtime provider for this cloud -- installing the
        pack, not editing the adapter, is what wires a skeleton cloud."""
        if self.mode == "mock":
            return True
        if type(self).is_runnable():
            return True
        from clousight_bench.core.registry import get_runtime_provider

        return get_runtime_provider(self.provider) is not None

    def setup(self) -> None:
        transport = self._transport_()
        transport.start()
        if transport.mock_base_url:  # expose the live mock URL to tasks
            self.mock_base_url = transport.mock_base_url

    def teardown(self) -> None:
        if self._transport is not None:
            self._transport.stop()
            self._transport = None

    def describe(self) -> dict[str, Any]:
        desc = super().describe()
        if self.provider is not None:
            desc["mode"] = self.mode
            ep = self.endpoint()
            if ep and ep.url:
                desc["endpoint"] = ep.url
                desc["endpoint_source"] = ep.source
            dep = self.data_endpoint()
            if dep and dep.url and dep.url != (ep.url if ep else None):
                desc["data_endpoint"] = dep.url
        return desc

    def preflight(self, task: Any | None = None) -> Any:
        """Mock mode is self-hosted -> no cloud prerequisites (like local-sim).
        Real mode keeps the full credentials + reachable-mock + permission gate."""
        if self.mode == "mock":
            from clousight_bench.core import preflight as pf

            return pf.PreflightReport().add(
                pf.Check("mode", ok=True, severity=pf.WARNING,
                         detail="mock: simulated runtime, no cloud prerequisites")
            )
        return super().preflight(task)

    # --- runtime ops: delegate to the selected transport --------------------

    def create_session(self, spec: dict[str, Any] | None = None) -> str:
        return self._transport_().create_session(spec)

    def run_tool_plan(self, session_id: str, plan: list[ToolCall]) -> InvocationTrace:
        return self._transport_().run_tool_plan(session_id, plan)

    def destroy_session(self, session_id: str) -> None:
        return self._transport_().destroy_session(session_id)

    def persist_state(self, session_id: str, state: dict[str, Any]) -> None:
        return self._transport_().persist_state(session_id, state)

    def load_state(self, session_id: str) -> dict[str, Any]:
        return self._transport_().load_state(session_id)

    def resume_session(self, session_id: str) -> str:
        return self._transport_().resume_session(session_id)

    def register_tool(self, path: str, spec: dict[str, Any]) -> bool:
        return self._transport_().register_tool(path, spec)

    def get_trace(self, session_id: str) -> list[dict[str, Any]]:
        return self._transport_().get_trace(session_id)

    def export_otel(self, session_id: str) -> dict[str, Any]:
        return self._transport_().export_otel(session_id)

    def probe_scaling(self, levels: list[int]) -> Any:
        return self._transport_().probe_scaling(levels)

    def probe_sustained_load(self, duration_s: float, target_rps: float) -> Any:
        return self._transport_().probe_sustained_load(duration_s, target_rps)

    def probe_warm_retention(self) -> Any:
        return self._transport_().probe_warm_retention()

    def probe_soak(self, duration_s: float) -> Any:
        return self._transport_().probe_soak(duration_s)

    def probe_rate_limit(self) -> Any:
        return self._transport_().probe_rate_limit()

    def probe_cancellation(self) -> Any:
        return self._transport_().probe_cancellation()

    def probe_signals(self) -> Any:
        return self._transport_().probe_signals()

    def probe_span_propagation(self) -> Any:
        return self._transport_().probe_span_propagation()

    def probe_export_latency(self) -> Any:
        return self._transport_().probe_export_latency()

    def provision(self, spec: dict[str, Any] | None = None) -> Any:
        return self._transport_().provision(spec)

    def provision_status(self, runtime_id: str) -> str:
        return self._transport_().provision_status(runtime_id)

    def deprovision(self, runtime_id: str) -> Any:
        return self._transport_().deprovision(runtime_id)
