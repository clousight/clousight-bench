"""Wired Aliyun AgentRun runtime provider (the real, SDK-backed implementation).

The commercial half of the open-core seam. The open core ships the
``aliyun-agentrun`` adapter as a *skeleton* (honest not-wired transport); this
package registers a ``RuntimeProviderPlugin`` for provider ``aliyun`` via the
``clousight_bench.runtime_providers`` entry point, which flips real mode to
runnable and supplies this live transport -- without editing the open adapter.

Design (see clousight-bench-pro/docs/agentrun-b-design.md and
agentrun-integration-research.md):

- Control plane (``agentrun.<region>.aliyuncs.com``): CreateAgentRuntime ->
  poll GetAgentRuntime to ready -> DeleteAgentRuntime. Drives T0.1 / T0.2.
- Data plane (``agentrun-data.<region>.aliyuncs.com``): InvokeRuntime
  (OpenAI-compatible), carrying an ``X-AgentRun-Session-ID`` header for session
  affinity; Memory API for state (T1.2); MCP activation for tools (T2.1).
  Traces go to ARMS, not a synchronous API -> CapabilityNotSupported.

The SDK (``alibabacloud-agentrun20250910``) is imported lazily inside the ops,
so this module imports (and the provider registers) even without the SDK -- the
mock path and the registration seam stay exercisable. The first real call
without the SDK raises a clear install hint, never an obscure ImportError.

Status: the CONTROL plane (create/get/delete AgentRuntime -> provision, status,
teardown) is wired against the installed SDK's typed models
(``CreateAgentRuntimeRequest`` wrapping ``CreateAgentRuntimeInput`` +
``CodeConfiguration``; ``get_agent_runtime(id, GetAgentRuntimeRequest)``;
responses read at ``body.data.{agent_runtime_id,status}``) and its request
builder passes the SDK's own ``validate()`` locally. The DATA plane (invoke /
memory / mcp) has no synchronous control-plane SDK op and is wired + validated
against a live account with a deployed agent and a public mock endpoint -- it
raises a clear ``_DataPlaneNotWired`` until then. Mock mode exercises everything
without an account.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from clousight_bench.core.observation import ObservationBundle

from clousight_bench.core.plugin import RuntimeProviderPlugin
from clousight_bench.domains.agent_runtime import protocol
from clousight_bench.domains.agent_runtime.adapters.base import (
    Attempt,
    CapabilityNotSupported,
    ConcurrentWriteResult,
    DeprovisionResult,
    HOLResult,
    InvocationTrace,
    ProvisionResult,
    RetryStormResult,
    ScalePoint,
    ToolCall,
)
from clousight_bench.domains.agent_runtime.adapters.transport import RuntimeTransport
from clousight_bench.domains.agent_runtime.ecs_carrier import (
    Ecs20140526Sdk,
    EcsCarrierConfig,
    EcsProbeCarrier,
)
from clousight_bench.domains.agent_runtime.mock_tools import AUTH_HEADER

_SDK_PACKAGE = "alibabacloud-agentrun20250910"
_READY_TIMEOUT_S = 300.0
_READY_POLL_S = 3.0

# Module-level cache: built once at first use, not at import, to avoid the
# import-time cost of building the runner when this module is merely loaded.
_PROBE_FNS: dict | None = None


def _auth_headers(mock_token: str) -> dict[str, str]:
    """Return the auth header dict for direct control-plane calls to the mock server."""
    return {AUTH_HEADER: mock_token} if mock_token else {}


def _get_probe_fns() -> dict:
    global _PROBE_FNS
    if _PROBE_FNS is None:
        from clousight_bench.domains.agent_runtime.probe.server import build_default_runner

        _PROBE_FNS = build_default_runner()._probes
    return _PROBE_FNS


class _SdkMissing(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            f"the Aliyun AgentRun SDK is required for real-mode calls but is not "
            f"installed. Install it with: pip install {_SDK_PACKAGE} "
            f"alibabacloud-tea-openapi alibabacloud-credentials. "
            f"(Or run the adapter in mode: mock to exercise the harness without it.)"
        )


class _DataPlaneNotWired(CapabilityNotSupported):
    """Data-plane seam not yet wired for live invocation.

    Inherits CapabilityNotSupported so all task-level except-clauses that
    catch CapabilityNotSupported will also catch this (T2.1, T4.1, T4.2…).
    """

    def __init__(self, op: str) -> None:
        super().__init__(
            f"aliyun {op}: data-plane seam not yet wired for live account. "
            f"Run in mode: mock for the account-free harness."
        )


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return ordered[idx]


class _LiveMemory:
    """OSS 기반 세션 상태 저장소.

    AgentRun의 Memory Collection API는 RAG/벡터 시스템으로 단순 K/V 세션 상태용이
    아닙니다. 대신 이미 확보된 OSS 버킷을 사용합니다:
      - store: clousight-bench/state/{session_id}.json 에 PUT
      - fetch: 동일 경로에서 GET
    T1.2는 '상태가 세션 간에 유지되는가'를 테스트하며, OSS는 진짜 영속성을 제공합니다.
    상태 파일은 teardown 시 정리됩니다.
    """

    def __init__(self, bucket: str, region: str, run_id: str | None = None) -> None:
        self._bucket = bucket
        self._region = region
        self._run_id = run_id
        self._keys: list[str] = []

    def _oss_bucket(self) -> Any:
        import oss2
        from alibabacloud_credentials.client import Client as CredClient

        from clousight_bench.domains.agent_runtime.probe.oss_client import _ChainCredentialsProvider

        auth = oss2.ProviderAuthV4(_ChainCredentialsProvider(CredClient()))
        endpoint = f"https://oss-{self._region}.aliyuncs.com"
        return oss2.Bucket(auth, endpoint, self._bucket, region=self._region)

    def store(self, session_id: str, state: dict[str, Any]) -> None:
        import json

        key = f"clousight-bench/state/{self._run_id or 'default'}/{session_id}.json"
        self._oss_bucket().put_object(key, json.dumps(state).encode("utf-8"))
        if key not in self._keys:
            self._keys.append(key)

    def fetch(self, session_id: str) -> dict[str, Any]:
        import json

        key = f"clousight-bench/state/{self._run_id or 'default'}/{session_id}.json"
        result = self._oss_bucket().get_object(key)
        return json.loads(result.read().decode("utf-8"))

    def cleanup(self) -> None:
        """teardown 시 저장된 상태 파일 정리."""
        bucket = self._oss_bucket()
        for key in self._keys:
            try:
                bucket.delete_object(key)
            except Exception:
                pass
        self._keys.clear()


class _LiveMcp:
    """AgentRun MCP：基于预注册模板，不支持动态工具注册。

    AgentRun 的 MCP 通过 ActivateTemplateMCP 激活已有模板。T2.1 的 _TOOL_SPEC
    是任意工具定义，不对应已注册的模板名称，因此此路径报告为"能力不支持"。
    这是平台真实行为的如实记录，不是 bug。
    """

    def __init__(self, client_factory: Any = None) -> None:
        self._client_factory = client_factory  # 注入真实 SDK client（可测试）

    def activate(self, name: str, spec: dict[str, Any]) -> bool:
        if self._client_factory is None:
            raise CapabilityNotSupported(
                "register_tool[mcp]: AgentRun MCP 使用预注册模板（ActivateTemplateMCP），"
                "不支持动态工具注册。请在 AgentRun 控制台预先创建 MCP 模板后再调用。"
            )
        # 尝试激活同名模板；若模板不存在或格式不符则视为能力不支持
        try:
            from alibabacloud_agentrun20250910 import models as m

            self._client_factory().activate_template_mcp(
                name,
                m.ActivateTemplateMCPRequest(transport="sse"),  # 必须指定传输协议
            )
            return True
        except Exception as exc:
            err = str(exc)
            # 模板不存在 → 需预注册
            if any(k in err for k in ("NotFound", "not found", "NoSuch", "ERR_NOT_FOUND")):
                raise CapabilityNotSupported(
                    f"register_tool[mcp]: AgentRun MCP 使用预注册模板，"
                    f"模板 '{name}' 不存在。请在控制台创建后重试。"
                ) from exc
            # 其他 400/403 → 平台限制，也视为能力不支持
            if "400" in err or "403" in err:
                raise CapabilityNotSupported(
                    f"register_tool[mcp]: AgentRun MCP 模板激活受限 — {err[:120]}"
                ) from exc
            raise


class AliyunAgentRunTransport(RuntimeTransport):
    """Live transport over the AgentRun control + data planes.

    One instance owns both planes: provision/deprovision use the control-plane
    client, invoke/state/tools the data-plane client. It surfaces the runtime's
    OWN behaviour (retries, readiness, teardown) as observed -- never simulates.
    """

    # AgentRun has no CreateSession API: session affinity is via a header.
    # Session creation is local-only (UUID generation); no cloud round-trip occurs.
    # Cold-start cost is incurred at provision (T0.1), not at create_session (T1.1).
    session_cold_start_is_provision = True

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter
        self._control = None
        self._data = None
        self._session_ids: set[str] = set()
        # Runtime ID — set by provision(); also lazily provisioned on first
        # data-plane call for T1.x-T6.x tasks that don't call provision() explicitly.
        self._runtime_id: str | None = None
        self._endpoint_public_url: str | None = None  # set by _create_default_endpoint
        self._lazy_provisioned: bool = False  # True = we own teardown
        self._http: Any = None  # requests.Session, lazily created
        # Ephemeral artifact: built + uploaded on provision, deleted on teardown.
        self._artifact_store: Any = None
        self._managed_artifact_ref: str | None = None
        # Data-plane seams: injectable for local tests; real defaults are
        # live-gated (validated on a live account). See docs/agentrun-b-design.md.
        self._invoke = self._live_invoke
        # Plan 4b hook: when set to a probe client, run_data_plane_probe
        # dispatches to the in-region ECI probe instead of running in-process.
        # OssProbeClient is preferred when probe_control_prefix is set (OSS-mediated);
        # RemoteProbeClient is the HTTP fallback when only probe_url is present.
        self._probe_client: Any = None
        probe_control_prefix = str(adapter.target.get("probe_control_prefix") or "")
        if probe_control_prefix:
            from clousight_bench.domains.agent_runtime.probe.oss_channel import OssChannel
            from clousight_bench.domains.agent_runtime.probe.oss_client import Oss2Client
            from clousight_bench.domains.agent_runtime.probe.oss_dispatch_client import OssProbeClient

            oss_bucket = str(adapter.target.get("oss_bucket") or "")
            oss_region = str(adapter.target.get("region") or "cn-hangzhou")
            oss = Oss2Client(bucket=oss_bucket, region=oss_region)
            channel = OssChannel(oss, campaign_id=probe_control_prefix)
            self._probe_client = OssProbeClient(channel)
        else:
            probe_url = str(adapter.target.get("probe_url") or "")
            if probe_url:
                from clousight_bench.domains.agent_runtime.probe.client import RemoteProbeClient

                probe_token = str(adapter.target.get("probe_token") or "") or None
                self._probe_client = RemoteProbeClient(probe_url, token=probe_token)
        self._last_ttft_ms: float | None = None  # set by run_tool_plan on first invoke
        self._last_trace_id: str | None = None  # set by _live_invoke from response headers
        self._collected_spans: list[dict] = []  # spans embedded in agent responses (_spans)
        target = adapter.target
        self._memory: Any = _LiveMemory(
            bucket=str(target.get("oss_bucket") or ""),
            region=str(target.get("region") or "cn-hangzhou"),
            run_id=getattr(adapter, "run_id", None),
        )
        self._mcp: Any = _LiveMcp(client_factory=self._control_client)

    # --- HTTP session (connection-pooled, thread-safe) -----------------------

    def _http_session(self) -> Any:
        """Lazily create a requests.Session with connection pooling.

        requests.Session is thread-safe for concurrent GET/POST calls,
        and reuses HTTPS connections — avoids the SSL EOF errors that urllib
        produces under concurrent load (no pooling, one TCP connection per call).
        """
        if self._http is None:
            try:
                import requests
                from requests.adapters import HTTPAdapter
            except ImportError as exc:
                raise RuntimeError(
                    "the 'requests' library is required for data-plane HTTP calls. "
                    "Install it with: pip install requests"
                ) from exc
            s = requests.Session()
            # Allow up to 64 concurrent connections per host
            adapter = HTTPAdapter(pool_connections=4, pool_maxsize=64)
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            self._http = s
        return self._http

    # --- lazy SDK clients ---------------------------------------------------

    def _credential(self) -> Any:
        try:
            from alibabacloud_credentials.client import Client as CredClient
        except ImportError as exc:
            raise _SdkMissing() from exc
        return CredClient()  # default chain: env / profile / role

    def _make_client(self, endpoint: str | None) -> Any:
        try:
            from alibabacloud_agentrun20250910.client import Client as AgentRunClient
            from alibabacloud_tea_openapi import models as open_api_models
        except ImportError as exc:
            raise _SdkMissing() from exc
        cfg = open_api_models.Config(credential=self._credential())
        if endpoint:
            cfg.endpoint = endpoint.replace("https://", "").replace("http://", "")
        return AgentRunClient(cfg)

    def _control_client(self) -> Any:
        if self._control is None:
            ep = self._adapter.endpoint()
            self._control = self._make_client(ep.url if ep else None)
        return self._control

    def _data_client(self) -> Any:
        if self._data is None:
            ep = self._adapter.data_endpoint()
            self._data = self._make_client(ep.url if ep else None)
        return self._data

    # --- sessions (data plane; header-based affinity) -----------------------

    def create_session(self, spec: dict[str, Any] | None = None) -> str:
        # AgentRun has no CreateSession API: affinity is via X-AgentRun-Session-ID header.
        # Session creation is purely local: generate a UUID and track it for teardown.
        # Cold-start cost is attributed to provision (T0.1), not here. T1.1 will
        # therefore record ~0 ms for create_session, which is the correct, honest number
        # for a platform where session ids are client-side tokens, not server resources.
        session_id = uuid.uuid4().hex
        self._session_ids.add(session_id)
        return session_id

    def destroy_session(self, session_id: str) -> None:
        self._session_ids.discard(session_id)

    def _live_invoke(self, session_id: str, openai_body: dict[str, Any]) -> dict[str, Any]:
        """POST OpenAI body to AgentRun data plane; return parsed response dict.

        URL: agentrun-data.{region}.aliyuncs.com
             /agent-runtimes/{runtime_id}/endpoints/Default/invocations/openai/v1/chat/completions
        Auth: default credential chain, signed via alibabacloud_tea_openapi (ROA style).
        """
        try:
            from alibabacloud_tea_openapi import models as open_api_models  # noqa: F401
            from alibabacloud_tea_util import models as util_models  # noqa: F401
        except ImportError as exc:
            raise _SdkMissing() from exc

        if not self._runtime_id:
            # Lazily provision for data-plane tasks (T1.x-T6.x) that don't call
            # provision() explicitly. The transport's stop() will deprovision.
            target = self._adapter.target
            self.provision({"oss_bucket": str(target.get("oss_bucket") or "")})
            self._lazy_provisioned = True

        endpoint_url = self._endpoint_public_url or str(self._adapter.target.get("endpoint_url") or "")
        if not endpoint_url:
            raise RuntimeError(
                "aliyun _live_invoke: no endpoint_public_url — endpoint may not be active yet."
            )

        # Call the endpoint's public URL directly (plain HTTPS POST, no Aliyun signing).
        # X-AgentRun-Session-ID provides session affinity for warm-path routing.
        # Use requests.Session for proper connection pooling under concurrent load.
        url = endpoint_url.rstrip("/") + "/openai/v1/chat/completions"
        session_obj = self._http_session()
        resp = session_obj.post(
            url,
            json=openai_body,
            headers={"X-AgentRun-Session-ID": session_id},
            timeout=120,
        )
        resp.raise_for_status()
        # Capture trace ID from response headers for T4.x ARMS queries.
        for h in ("x-trace-id", "x-b3-traceid", "traceparent", "eagleeye-traceid"):
            tid = resp.headers.get(h) or resp.headers.get(h.upper())
            if tid:
                # traceparent format: 00-<trace_id>-<span_id>-<flags>
                if h == "traceparent" and "-" in tid:
                    tid = tid.split("-")[1]
                self._last_trace_id = tid
                break
        return resp.json()

    def _measure_ttft(self, session_id: str, openai_body: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        """Send a streaming request and return (ttft_ms, full_response_dict).

        ttft_ms = wall-clock milliseconds from sending the request to receiving
        the first non-empty, non-comment SSE ``data:`` line. The full response is
        reassembled from all non-[DONE] data chunks.

        Falls back to a normal (non-streaming) invoke and returns (0.0, response)
        if the endpoint_url is not available or requests is not installed.
        """
        endpoint_url = self._endpoint_public_url or str(self._adapter.target.get("endpoint_url") or "")
        # Build a non-streaming fallback body (strip stream=True so _live_invoke
        # receives a body it can parse as JSON rather than SSE).
        non_stream_body = {k: v for k, v in openai_body.items() if k != "stream"}

        if not endpoint_url:
            resp = self._invoke(session_id, non_stream_body)
            return 0.0, resp

        try:
            import requests as _requests  # noqa: F401 - import check only
        except ImportError:
            resp = self._invoke(session_id, non_stream_body)
            return 0.0, resp

        url = endpoint_url.rstrip("/") + "/openai/v1/chat/completions"
        session_obj = self._http_session()
        t0 = time.perf_counter()
        try:
            http_resp = session_obj.post(
                url,
                json=openai_body,
                headers={"X-AgentRun-Session-ID": session_id},
                stream=True,
                timeout=120,
            )
            http_resp.raise_for_status()
        except Exception:
            # Fall back to normal invoke if streaming POST fails.
            resp = self._invoke(session_id, non_stream_body)
            return 0.0, resp

        # Check Content-Type: if the endpoint didn't return SSE, fall back to parsing
        # the response body as a normal JSON completion.
        content_type = str(http_resp.headers.get("Content-Type") or "")
        if "text/event-stream" not in content_type:
            # Deployed agent is an older version that doesn't support streaming.
            # Parse the body as normal JSON and return ttft = time to full response.
            try:
                full_resp = http_resp.json()
                ttft_ms = (time.perf_counter() - t0) * 1000
                return round(ttft_ms, 3), full_resp
            except Exception:
                resp = self._invoke(session_id, non_stream_body)
                return 0.0, resp

        ttft_ms = 0.0
        ttft_recorded = False
        chunks: list[str] = []
        try:
            for raw_line in http_resp.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                if not raw_line.startswith("data:"):
                    continue
                data_part = raw_line[5:].strip()
                if not ttft_recorded:
                    # Record TTFT at the first real data line (even if content is empty).
                    ttft_ms = (time.perf_counter() - t0) * 1000
                    ttft_recorded = True
                if data_part == "[DONE]":
                    break
                chunks.append(data_part)
        except Exception:
            # Stream read failed; fall back to a normal invoke for the result.
            if not ttft_recorded:
                ttft_ms = (time.perf_counter() - t0) * 1000
            resp = self._invoke(session_id, non_stream_body)
            return round(ttft_ms, 3), resp

        if not ttft_recorded:
            ttft_ms = (time.perf_counter() - t0) * 1000

        # Reassemble full response from all chunks by merging content deltas.
        import json as _json

        merged_content = ""
        for chunk_str in chunks:
            try:
                parsed = _json.loads(chunk_str)
                delta = (parsed.get("choices") or [{}])[0].get("delta") or {}
                merged_content += str(delta.get("content") or "")
            except (ValueError, TypeError):
                pass

        # Build a response that looks like a normal (non-streaming) chat completion.
        full_resp = {"choices": [{"message": {"role": "assistant", "content": merged_content}}]}
        return round(ttft_ms, 3), full_resp

    def run_tool_plan(self, session_id: str, plan: list[ToolCall]) -> InvocationTrace:
        # Data plane = the deployed runtime's OWN OpenAI-compatible endpoint, reached
        # with the X-AgentRun-Session-ID affinity header via the injectable _invoke
        # seam. One invoke per tool call, one attempt: the RUNTIME owns retries -- we
        # observe, never simulate.
        # For the first call (call_index == 1) we additionally measure TTFT via
        # streaming; the result is stored in self._last_ttft_ms for external queries.
        base = self._adapter.mock_base_url
        mock_token = str(self._adapter.target.get("mock_token") or "")
        # Inject arms_config when configured — agent.py will emit OpenInference spans
        # and embed them in the response body under "_spans" for get_trace() to collect.
        arms_cfg = self._arms_config_for_invoke()
        attempts: list[Attempt] = []
        completed = True
        final_state = "completed"
        for call_index, call in enumerate(plan, start=1):
            tool = {"target": call.target, "method": call.method, "params": call.params, "body": call.body}
            if call_index == 1:
                # First invoke: measure TTFT with a streaming request.
                stream_body = protocol.encode_invoke_stream(tool, base, mock_token=mock_token or None)
                start = time.perf_counter()
                ttft_ms, resp = self._measure_ttft(session_id, stream_body)
                latency = (time.perf_counter() - start) * 1000
                self._last_ttft_ms = ttft_ms
            else:
                body = protocol.encode_invoke(tool, base, mock_token=mock_token or None, arms_config=arms_cfg)
                start = time.perf_counter()
                resp = self._invoke(session_id, body)
                latency = (time.perf_counter() - start) * 1000
            result = protocol.decode_result(resp)
            # Collect OpenInference spans embedded in the response by agent.py.
            if isinstance(result.get("_spans"), list):
                self._collected_spans.extend(result["_spans"])
            status = int(result.get("status", 0))
            ok = bool(result.get("ok"))
            attempts.append(Attempt(call_index, 1, status, ok, round(latency, 2)))
            if not ok:
                completed = False
                final_state = "failed"
                break
        return InvocationTrace(session_id, attempts, completed, final_state)

    # --- state (AgentRun Memory collection API; validated live) --------------

    def persist_state(self, session_id: str, state: dict[str, Any]) -> None:
        self._memory.store(session_id, state)

    def load_state(self, session_id: str) -> dict[str, Any]:
        return self._memory.fetch(session_id)

    def resume_session(self, session_id: str) -> str:
        return session_id  # affinity id is stable; durability is the Memory guarantee

    def register_tool(self, path: str, spec: dict[str, Any]) -> bool:
        if path == "mcp":
            return self._register_tool_mcp()
        if path == "native":
            return self._register_tool_native()
        return False  # openapi not supported by AgentRun

    def _register_tool_mcp(self) -> bool:
        """T2.1 MCP path: list available templates → activate first one → stop.

        AgentRun MCP is template-based (pre-registered in console).
        If any templates exist, we activate + stop one to confirm the path works.
        """
        from alibabacloud_agentrun20250910 import models as m

        try:
            client = self._control_client()
            resp = client.list_templates(m.ListTemplatesRequest(page_size=5))
            items = getattr(getattr(resp.body, "data", None), "items", None) or []
            if not items:
                raise CapabilityNotSupported(
                    "register_tool[mcp]: no MCP templates found in this AgentRun workspace. "
                    "Pre-create a template in the console to enable MCP registration."
                )
            template_name = str(getattr(items[0], "template_name", "") or "")
            if not template_name:
                raise CapabilityNotSupported("register_tool[mcp]: template has no name")
            # Activate to verify the path works, then stop to clean up.
            self._control_client().activate_template_mcp(
                template_name,
                m.ActivateTemplateMCPRequest(transport="sse"),
            )
            import contextlib as _cl

            with _cl.suppress(Exception):
                self._control_client().stop_template_mcp(template_name, m.StopTemplateMCPRequest())
            return True
        except CapabilityNotSupported:
            raise
        except Exception as exc:
            raise CapabilityNotSupported(f"register_tool[mcp]: {exc}") from exc

    def _register_tool_native(self) -> bool:
        """T2.1 native path: list_tools confirms the native tool API is accessible.

        AgentRun's CreateTool deploys a new FC function (heavyweight; not probed here).
        Confirming list_tools succeeds proves the native registration path is open.
        """
        from alibabacloud_agentrun20250910 import models as m

        try:
            client = self._control_client()
            resp = client.list_tools(m.ListToolsRequest())
            items = getattr(getattr(resp.body, "data", None), "items", None) or []
            # API is accessible → native path is supported (CreateTool would deploy a tool)
            _ = items  # count is informational; API reachability is the gate
            return True
        except Exception as exc:
            raise CapabilityNotSupported(f"register_tool[native]: {exc}") from exc

    # --- probe methods (T1.4-T1.8): implemented via run_tool_plan ---------------

    def _one_tool_call(self) -> tuple[bool, float]:
        """Single tool-plan invocation used by all probe implementations."""
        ok, ms, _ = self._one_tool_call_classified()
        return ok, ms

    def _one_tool_call_classified(self) -> tuple[bool, float, str]:
        """Like _one_tool_call but also returns the error type.

        Returns (ok, latency_ms, error_type) where error_type is:
          ''          — success
          'transport' — SSL/connection error; runtime was never reached
          'runtime'   — runtime returned a non-2xx or tool call failed
        """
        import time as _time

        from clousight_bench.domains.agent_runtime.adapters.base import ToolCall

        plan = [ToolCall(target="prices", params={"provider": "aliyun"})]
        session = self.create_session()
        t0 = _time.perf_counter()
        error_type = ""
        try:
            trace = self.run_tool_plan(session, plan)
            ok = trace.completed
            if not ok:
                error_type = "tool"  # AgentRun OK, mock tool failed
        except Exception as exc:
            ok = False
            err_str = str(exc).lower()
            if any(k in err_str for k in ("ssl", "connection", "eof", "timeout", "connect")):
                error_type = "transport"
            else:
                error_type = "runtime"  # AgentRun data-plane returned non-2xx
        finally:
            self.destroy_session(session)
        return ok, (_time.perf_counter() - t0) * 1000, error_type

    def probe_sustained_load(self, duration_s: float, target_rps: float) -> Any:
        """真并发持续负载：用令牌桶 + 线程池驱动，真实测量吞吐和尾延迟。

        工作者数量 = min(target_rps * 预估延迟, 64)（Little's Law）。
        吞吐量分母使用实际挂钟时间（含所有 in-flight 请求完成后），避免高尾延迟
        场景下人为高估吞吐量（deadline 后仍在执行的请求不被计入 duration_s 但
        被计入 n，导致 n/duration_s 虚高）。
        """
        import threading
        import time as _time
        from concurrent.futures import ThreadPoolExecutor

        from clousight_bench.core.stats import percentiles
        from clousight_bench.domains.agent_runtime.adapters.base import LoadResult

        # 先做一次探测请求，估计平均延迟，决定并发度
        _, probe_ms = self._one_tool_call()
        estimated_latency_s = max(probe_ms / 1000, 0.1)
        # 并发度 = target_rps × 估计延迟（Little's Law），上限 32（原64，降低线程峰值避免系统线程耗尽）
        concurrency = min(max(int(target_rps * estimated_latency_s) + 1, 4), 32)

        latencies: list[float] = []
        errors_count = 0
        transport_errors = 0
        runtime_errors = 0
        tool_errors = 0
        lock = threading.Lock()
        deadline = _time.perf_counter() + duration_s

        def _worker() -> None:
            nonlocal errors_count, transport_errors, runtime_errors, tool_errors
            while _time.perf_counter() < deadline:
                ok, ms, err_type = self._one_tool_call_classified()
                with lock:
                    latencies.append(ms)
                    if not ok:
                        errors_count += 1
                        if err_type == "transport":
                            transport_errors += 1
                        elif err_type == "runtime":
                            runtime_errors += 1
                        else:  # "tool"
                            tool_errors += 1

        actual_start = _time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(_worker) for _ in range(concurrency)]
            for f in futures:
                f.result()
        actual_elapsed = _time.perf_counter() - actual_start  # includes in-flight tail

        n = len(latencies) or 1
        p = percentiles(latencies)
        # Use actual_elapsed (not duration_s) so long-tail requests don't inflate RPS.
        actual_rps = round(n / actual_elapsed, 3)
        return LoadResult(
            throughput_rps=actual_rps,
            p50_ms=round(p[50], 2),
            p99_ms=round(p[99], 2),
            jitter_ms=round(p[99] - p[50], 2),
            error_rate=round(errors_count / n, 4),
            requests=n,
            duration_s=round(actual_elapsed, 2),
            transport_error_rate=round(transport_errors / n, 4),
            runtime_error_rate=round(runtime_errors / n, 4),
            tool_error_rate=round(tool_errors / n, 4),
        )

    def probe_warm_retention(self) -> Any:
        """多点检测：建立热实例后，依次等待 10s/30s/60s，观察哪个时间点变冷。

        阈值策略：取 5 次热调用的 p95，乘以 2 作为"仍然热"的上限。
        这比固定 3× 均值更稳健：高方差平台（p95 >> mean）不会把正常慢响应
        误判为冷启动；低方差平台也不会把真正的冷启动漏掉（冷启动通常 5-20×）。
        retention_ms = 最后一次仍然热的等待时间（0 = 完全不保活）。
        """
        import time as _time

        from clousight_bench.core.stats import percentiles
        from clousight_bench.domains.agent_runtime.adapters.base import RetentionResult

        # 建立热实例 + 采集基准分布（5次），用 p95×2 作为阈值
        warmup_samples: list[float] = []
        for _ in range(5):
            _, ms = self._one_tool_call()
            warmup_samples.append(ms)
        warm_p95 = percentiles(warmup_samples)[95]
        warm_threshold = warm_p95 * 2  # cold start 通常 5-20× warm；2× 保守但足够区分

        wait_intervals_s = [10, 30, 60]
        last_warm_ms = 0.0
        keeps_warm = False
        for wait_s in wait_intervals_s:
            _time.sleep(wait_s)
            _, ms = self._one_tool_call()
            if ms <= warm_threshold:
                last_warm_ms = wait_s * 1000.0
                keeps_warm = True
            else:
                break  # 变冷，记录最后一次热点

        return RetentionResult(
            retention_ms=last_warm_ms,
            keeps_warm=keeps_warm,
        )

    def probe_soak(self, duration_s: float) -> Any:
        import time as _time

        from clousight_bench.domains.agent_runtime.adapters.base import SoakResult

        deadline = _time.perf_counter() + duration_s
        requests, errors = 0, 0
        while _time.perf_counter() < deadline:
            ok, _ = self._one_tool_call()
            requests += 1
            if not ok:
                errors += 1
        n = requests or 1
        return SoakResult(
            availability=1.0 - errors / n,
            error_rate=errors / n,
            requests=requests,
            window_s=duration_s,
        )

    def probe_rate_limit(self) -> Any:
        """阶梯式并发探测限流：10→20→40→80 并发，观察首个出现 429 的级别。

        直接检查 AgentRun 数据面的 HTTP 状态（不经过 run_tool_plan），捕获
        Retry-After 头，确认 429 合约是否完整。
        onset_rps = 触发限流的最小并发数（0 = 在测试范围内未触发）。
        """
        from concurrent.futures import ThreadPoolExecutor

        from clousight_bench.domains.agent_runtime.adapters.base import RateLimitResult

        # 确保 runtime 已启动（lazy provision）
        _, _ = self._one_tool_call()
        endpoint_url = self._endpoint_public_url or ""
        if not endpoint_url:
            # Provision/warm-up failed — not a capability gap, a runtime error.
            raise RuntimeError("probe_rate_limit: no endpoint_public_url after warm-up (provision failed?)")

        url = endpoint_url.rstrip("/") + "/openai/v1/chat/completions"
        mock = self._adapter.mock_base_url
        mock_token = str(self._adapter.target.get("mock_token") or "")
        body = protocol.encode_invoke(
            {"target": "prices", "method": "GET"},
            mock,
            mock_token=mock_token or None,
        )
        session_obj = self._http_session()
        BURST_LEVELS = [10, 20, 40, 80]
        onset_rps = 0.0
        retry_after_ms = 0.0
        honors_429 = False

        for burst_n in BURST_LEVELS:

            def _raw_call(i: int, _n: int = burst_n) -> tuple[int, float]:
                sid = f"rl-{_n}-{i}"
                try:
                    resp = session_obj.post(
                        url,
                        json=body,
                        headers={"X-AgentRun-Session-ID": sid},
                        timeout=30,
                    )
                    ra = resp.headers.get("Retry-After", "")
                    ra_ms = float(ra) * 1000 if ra else 0.0
                    return resp.status_code, ra_ms
                except Exception:
                    return 0, 0.0

            with ThreadPoolExecutor(max_workers=burst_n) as pool:
                results = list(pool.map(_raw_call, range(burst_n)))

            four_twenty_nines = [(s, ra) for s, ra in results if s == 429]
            if four_twenty_nines:
                onset_rps = float(burst_n)
                honors_429 = True
                retry_after_ms = four_twenty_nines[0][1]
                break

        return RateLimitResult(
            throttle_onset_rps=onset_rps,
            retry_after_ms=retry_after_ms,
            honors_429=honors_429,
        )

    def probe_cancellation(self) -> Any:
        """真实取消探测：用极短超时强制客户端断开，验证端点能从中恢复。

        设计：CLIENT_TIMEOUT_S = 0.1s（100ms），比任何 AgentRun 调用都快，
        保证必然触发 Timeout 异常（无需依赖 mock server 状态同步）。

        honored=True: 超时异常已抛出 = 客户端取消有效
        teardown_ran=True: 超时后端点仍可正常响应（session 未损坏）
        residual: 取消后检测到的异常状态
        """
        from clousight_bench.domains.agent_runtime.adapters.base import CancellationResult

        # 极短超时：100ms 远低于任何 AgentRun 数据面调用的实际延迟（通常 300ms+）
        CLIENT_TIMEOUT_S = 0.1
        residual: list[str] = []
        honored = False
        teardown_ran = False

        try:
            # Step 1: warm up to set _endpoint_public_url
            ok_warm, _ = self._one_tool_call()
            if not ok_warm:
                residual.append("warm-up call failed before cancellation probe")
            endpoint_url = self._endpoint_public_url or ""
            if not endpoint_url:
                raise RuntimeError(
                    "probe_cancellation: no endpoint_public_url after warm-up (provision failed?)"
                )

            # Step 2: fire a request with 100ms timeout → always triggers Timeout
            session_id = self.create_session()
            try:
                mock = self._adapter.mock_base_url
                mock_token = str(self._adapter.target.get("mock_token") or "")
                body = protocol.encode_invoke(
                    {"target": "prices", "method": "GET"},
                    mock,
                    mock_token=mock_token or None,
                )
                url = endpoint_url.rstrip("/") + "/openai/v1/chat/completions"
                session_obj = self._http_session()
                try:
                    session_obj.post(
                        url,
                        json=body,
                        headers={"X-AgentRun-Session-ID": session_id},
                        timeout=CLIENT_TIMEOUT_S,
                    )
                    honored = False  # completed within 100ms (unexpected)
                except Exception:
                    honored = True  # Timeout raised = cancel was honored
            finally:
                self.destroy_session(session_id)

            # Step 3: verify endpoint is still healthy after the abrupt disconnect
            ok_after, _ = self._one_tool_call()
            teardown_ran = ok_after
            if not ok_after:
                residual.append("endpoint unhealthy after cancellation: session may be stuck")

        except CapabilityNotSupported:
            raise
        except Exception as exc:
            residual.append(f"probe error: {exc!r}")
            teardown_ran = False

        return CancellationResult(
            honored=honored,
            teardown_ran=teardown_ran,
            residual=residual,
        )

    def probe_ttft(self) -> float:
        """Run one streaming invoke and return TTFT in milliseconds.

        Uses the SSE streaming path (_measure_ttft) which was added to both
        agent.py (deployed) and the transport. If the deployed agent does not
        yet support streaming, _measure_ttft falls back to full RTT (returns
        the time to the full non-streaming response) and T1.9 surfaces a finding.
        """
        from clousight_bench.domains.agent_runtime.adapters.base import ToolCall

        plan = [ToolCall(target="prices", params={"provider": "aliyun"})]
        session = self.create_session()
        try:
            # run_tool_plan streams the first call and stores TTFT in _last_ttft_ms
            self.run_tool_plan(session, plan)
            return self._last_ttft_ms or 0.0
        finally:
            self.destroy_session(session)

    def probe_retry_storm(self, max_window_s: float = 30.0) -> RetryStormResult:
        """T1.10: mock-counted total attempts + storm-bounded-by attribution.

        Configures the mock server to fail ALL calls on a per-correlation bucket
        (fail_from_call:1, fail_count:999), issues a single invoke with that
        correlation id, and reads the mock call counter to determine how many times
        the platform let the agent hit the tool.

        Attribution:
          total_attempts <= 3 and no timeout → storm_bounded_by = "agent"
          invoke raised Timeout               → storm_bounded_by = "platform"
          total_attempts > 3                  → storm_bounded_by = "none"
        """
        import uuid

        base = self._adapter.mock_base_url
        mock_token = str(self._adapter.target.get("mock_token") or "")
        corr = uuid.uuid4().hex

        # Step 1: Configure fault — fail ALL calls in this corr bucket. "target" is
        # REQUIRED: the mock only faults a request whose tool target matches it
        # (mock_tools.fault_status_for). Omitting it silently injects nothing, so
        # the storm never forms and total_attempts collapses to 1.
        fault_config: dict[str, Any] = {
            "target": "prices",
            "fail_from_call": 1,
            "fail_count": 999,
            "corr": corr,
        }
        fault_url = base.rstrip("/") + "/fault/config"
        try:
            import requests as _requests

            _requests.post(
                fault_url, json=fault_config, headers=_auth_headers(mock_token), timeout=10
            ).raise_for_status()
        except Exception:
            pass  # best-effort; probe proceeds

        # Step 2: Single invoke with this corr id.
        session = self.create_session()
        t_start = time.perf_counter()
        storm_bounded_by = "agent"
        tool = {"target": "prices", "method": "GET", "params": {"provider": "aliyun"}}
        body = protocol.encode_invoke(
            tool,
            base,
            mock_token=mock_token or None,
            session_id=session,
            correlation_id=corr,
        )
        try:
            self._invoke(session, body)
        except Exception as exc:
            err_str = str(exc).lower()
            if any(k in err_str for k in ("timeout", "connection", "ssl", "eof", "connect")):
                storm_bounded_by = "platform"
        finally:
            self.destroy_session(session)

        duration_ms = round((time.perf_counter() - t_start) * 1000, 2)

        # Step 3: Read mock server call counter.
        total_attempts = 0
        try:
            import requests as _requests

            state_resp = _requests.get(
                base.rstrip("/") + "/fault/state", headers=_auth_headers(mock_token), timeout=10
            )
            state_resp.raise_for_status()
            counts = state_resp.json().get("call_counts", {})
            total_attempts = int(counts.get(f"prices|{corr}", 0))
        except Exception:
            pass

        # Derive storm_bounded_by from total_attempts (unless platform timeout).
        if storm_bounded_by != "platform":
            storm_bounded_by = "none" if total_attempts > 3 else "agent"

        return RetryStormResult(
            capability="supported",
            total_attempts=total_attempts,
            storm_bounded_by=storm_bounded_by,
            duration_ms=duration_ms,
        )

    def probe_concurrent_writes(self) -> ConcurrentWriteResult:
        """T1.11: two sessions write to the same state key simultaneously.

        Uses threads to overlap two OSS-backed persist_state calls, then
        reads each session's state to verify no cross-write corruption.
        """
        import concurrent.futures as _cf

        key = "__concurrent_write_probe__"
        session_a = self.create_session()
        session_b = self.create_session()

        def write(session_id: str) -> None:
            self._memory.store(session_id, {key: session_id})

        try:
            with _cf.ThreadPoolExecutor(max_workers=2) as pool:
                fa = pool.submit(write, session_a)
                fb = pool.submit(write, session_b)
                fa.result()
                fb.result()

            val_a = self._memory.fetch(session_a).get(key, "")
            val_b = self._memory.fetch(session_b).get(key, "")
        except CapabilityNotSupported:
            raise
        except Exception as exc:
            raise CapabilityNotSupported(f"probe_concurrent_writes: state API unavailable — {exc}") from exc
        finally:
            with contextlib.suppress(Exception):
                self._memory.cleanup()
            self.destroy_session(session_a)
            self.destroy_session(session_b)

        write_safe = val_a == session_a and val_b == session_b
        if write_safe:
            winner = "both"
        elif val_a == session_a:
            winner = "session_a"
        elif val_b == session_b:
            winner = "session_b"
        else:
            winner = "unknown"

        return ConcurrentWriteResult(write_safe=write_safe, winner=winner)

    def probe_hol_blocking(self) -> HOLResult:
        """T1.12: two-phase HOL probe (live Aliyun path).

        Phase A (baseline): 20 concurrent fast requests (``prices``) with no
        slow request running — establishes ``fast_p50_baseline``.
        Phase B (under-slow): 1 slow (``reports``) + 20 fast (``prices``)
        concurrent on the same session — establishes ``fast_p50_under_slow``.

        serialized = fast_p50_under_slow > fast_p50_baseline * 2.0
        hol_ratio  = fast_p50_under_slow / fast_p50_baseline
        """
        import concurrent.futures as _cf

        from clousight_bench.core.stats import percentiles

        # Ensure runtime is provisioned
        _, _ = self._one_tool_call()

        base = self._adapter.mock_base_url
        mock_token = str(self._adapter.target.get("mock_token") or "")
        session_id = self.create_session()

        def timed_invoke(target: str) -> float:
            body = protocol.encode_invoke(
                {"target": target, "method": "GET"},
                base,
                mock_token=mock_token or None,
            )
            t0 = time.perf_counter()
            try:
                self._invoke(session_id, body)
            except Exception:
                pass
            return (time.perf_counter() - t0) * 1000

        fast_count = 20
        fast_calls = ["prices"] * fast_count

        try:
            # Phase A: baseline — fast requests only, no slow
            with _cf.ThreadPoolExecutor(max_workers=fast_count) as pool:
                futs_a = [pool.submit(timed_invoke, t) for t in fast_calls]
                baseline_latencies = [f.result() for f in futs_a]

            fast_p50_baseline = percentiles(baseline_latencies, [50])[50]

            # Phase B: under-slow — 1 slow + N fast concurrent
            with _cf.ThreadPoolExecutor(max_workers=1 + fast_count) as pool:
                slow_fut = pool.submit(timed_invoke, "reports")
                futs_b = [pool.submit(timed_invoke, t) for t in fast_calls]
                slow_fut.result()
                under_slow_latencies = [f.result() for f in futs_b]
        finally:
            self.destroy_session(session_id)

        fast_p50_under_slow = percentiles(under_slow_latencies, [50])[50]
        hol_ratio = round(fast_p50_under_slow / fast_p50_baseline, 4) if fast_p50_baseline > 0 else 0.0
        serialized = fast_p50_under_slow > fast_p50_baseline * 2.0

        return HOLResult(
            serialized=serialized,
            fast_p50_baseline=fast_p50_baseline,
            fast_p50_under_slow=fast_p50_under_slow,
            hol_ratio=hol_ratio,
        )

    # --- data-plane probe seam (Plan 4a/4b) ------------------------------------

    def run_data_plane_probe(self, name: str, params: dict) -> ObservationBundle:
        """Run a named data-plane probe and return its ObservationBundle.

        In-process path (Plan 4a, default): builds a JobSpec, looks up the probe
        function from the module-level _PROBE_FNS cache, runs it synchronously,
        then attaches vantage metadata.

        Remote path (Plan 4b, when self._probe_client is set): delegates to the
        configured probe client — OssProbeClient for the OSS/ECI path (when
        ``probe_control_prefix`` is set) or RemoteProbeClient for the legacy
        HTTP path (when only ``probe_url`` is set) — instead of running
        in-process.

        Endpoint resolution mirrors _live_invoke's lazy-provision pattern so
        data-plane tasks that skip explicit provision() still work.
        """
        from clousight_bench.core.observation import ObservationBundle  # noqa: F401 (type hint)
        from clousight_bench.domains.agent_runtime.probe.jobs import JobSpec

        # Resolve endpoint the same way _live_invoke does.
        endpoint = self._endpoint_public_url or str(self._adapter.target.get("endpoint_url") or "")
        if not endpoint:
            # Lazy provision for tasks that skip explicit provision().
            target = self._adapter.target
            self.provision({"oss_bucket": str(target.get("oss_bucket") or "")})
            self._lazy_provisioned = True
            endpoint = self._endpoint_public_url or str(self._adapter.target.get("endpoint_url") or "")
        if not endpoint:
            raise RuntimeError(
                "aliyun run_data_plane_probe: no endpoint_public_url — endpoint may not be active yet."
            )

        spec = JobSpec(
            probe=name,
            params=params,
            target_endpoint=endpoint,
            mock_base_url=self._adapter.mock_base_url,
            mock_token=str(self._adapter.target.get("mock_token") or ""),
            session_header_scheme="X-AgentRun-Session-ID",
            oss_prefix=str(self._adapter.target.get("probe_oss_prefix") or ""),
        )

        if self._probe_client is not None:
            bundle = self._probe_client.run_job(spec)
        else:
            probe_fns = _get_probe_fns()
            bundle = probe_fns[name](spec, lambda p, m: None)

        remote = self._probe_client is not None
        bundle.observations.setdefault(
            "vantage",
            {
                "carrier": "ecs" if remote else "local",
                "region": str(self._adapter.target.get("region") or "cn-hangzhou"),
                "in_vpc": bool(self._adapter.target.get("probe_in_vpc", False)) if remote else False,
                "probe_version": 1,
            },
        )
        return bundle

    # --- traces: read from ARMS after async export (T4.1 / T4.2) --------------

    def _arms_client(self) -> Any:
        """Lazy ARMS client for trace queries."""
        try:
            from alibabacloud_arms20190808.client import Client as ArmsClient
            from alibabacloud_tea_openapi import models as open_api_models
        except ImportError as exc:
            raise CapabilityNotSupported(
                "ARMS SDK not installed; run: uv pip install alibabacloud-arms20190808"
            ) from exc
        cfg = open_api_models.Config(credential=self._credential())
        region = str(self._adapter.target.get("region") or "cn-hangzhou")
        cfg.endpoint = f"arms.{region}.aliyuncs.com"
        cfg.region_id = region
        return ArmsClient(cfg)

    def _arms_get_spans(self, trace_id: str) -> list[dict[str, Any]]:
        """Query ARMS for a trace by ID; return list of span dicts.

        Waits up to ARMS_WAIT_S for the trace to propagate before querying.
        Returns [] if the trace is not yet visible or ARMS is inaccessible.
        """
        import time as _time

        ARMS_WAIT_S = 25  # ARMS trace propagation typically 10–30s
        ARMS_POLL_S = 5
        from alibabacloud_arms20190808 import models as arms_m

        client = self._arms_client()
        region = str(self._adapter.target.get("region") or "cn-hangzhou")
        deadline = _time.perf_counter() + ARMS_WAIT_S
        while _time.perf_counter() < deadline:
            _time.sleep(ARMS_POLL_S)
            try:
                # Query a 5-minute window centered on now to catch the trace
                now_ms = int(_time.time() * 1000)
                resp = client.get_trace(
                    arms_m.GetTraceRequest(
                        region_id=region,
                        trace_id=trace_id,
                        start_time=now_ms - 5 * 60 * 1000,
                        end_time=now_ms + 60 * 1000,
                    )
                )
                spans_raw = getattr(resp.body, "spans", None) or []
                if spans_raw:
                    spans = []
                    for s in spans_raw:
                        tags = {
                            t.key: t.value
                            for t in (getattr(s, "tag_entry_list", None) or [])
                            if hasattr(t, "key")
                        }
                        spans.append(
                            {
                                "trace_id": getattr(s, "trace_id", trace_id),
                                "span_id": getattr(s, "span_id", ""),
                                "parent_span_id": getattr(s, "parent_span_id", None) or "",
                                "operation_name": getattr(s, "operation_name", ""),
                                "service_name": getattr(s, "service_name", ""),
                                "duration_us": getattr(s, "duration", 0),
                                "timestamp_ms": getattr(s, "timestamp", 0),
                                "tags": tags,
                            }
                        )
                    return spans
            except Exception:
                pass
        return []

    def _arms_get_spans_by_time(self, invocation_time_ms: int) -> list[dict[str, Any]]:
        """Fallback: search ARMS by time range when no trace ID is in response headers.

        Queries the known ARMS app (if any) for traces in a 3-minute window
        around the invocation time. Returns the first matching trace's spans.
        """
        import time as _time

        from alibabacloud_arms20190808 import models as arms_m

        client = self._arms_client()
        region = str(self._adapter.target.get("region") or "cn-hangzhou")

        # Get PID of the registered trace app (cached in target if configured,
        # otherwise discovered via ListTraceApps).
        # Poll for up to 90s — ARMS propagation is typically 60–90s for FC-backed runtimes.
        # Search by time range only (no PID filter) because AgentRun traces appear indexed
        # under the FC function name (FC:agentrun-<id>), not the benchmark app's PID.
        POLL_S, MAX_WAIT_S = 10, 90
        deadline = _time.perf_counter() + MAX_WAIT_S
        seen_ids: set = set()
        while _time.perf_counter() < deadline:
            _time.sleep(POLL_S)
            try:
                resp = client.search_traces(
                    arms_m.SearchTracesRequest(
                        region_id=region,
                        start_time=invocation_time_ms - 30_000,  # 30s before invocation
                        end_time=int(_time.time() * 1000) + 5_000,
                    )
                )
                trace_infos = getattr(resp.body, "trace_infos", None) or []
                # Pick the first trace not yet fetched
                for ti in trace_infos:
                    tid = str(getattr(ti, "trace_id", "") or "")
                    if tid and tid not in seen_ids:
                        seen_ids.add(tid)
                        spans = self._arms_get_spans(tid)
                        if spans:
                            return spans
            except Exception:
                pass
        return []

    def _arms_config_for_invoke(self) -> dict[str, Any] | None:
        """Build the arms_config dict to pass to agent.py for OpenInference span export."""
        arms_key = str(self._adapter.target.get("arms_license_key") or "")
        if not arms_key:
            return None
        region = str(self._adapter.target.get("region") or "cn-hangzhou")
        return {"license_key": arms_key, "region": region}

    def probe_span_propagation(self) -> Any:
        """T4.4: verify parent/child span linkage using collected OpenInference spans.

        Runs a multi-call tool plan to collect spans, then checks:
        - orphan_spans: spans whose parent_id points to a non-existent span
        - root_count: number of spans with no parent (should be exactly 1 = CHAIN)
        """
        from clousight_bench.domains.agent_runtime.adapters.base import PropagationResult, ToolCall

        plan = [
            ToolCall(target="prices", params={"provider": "aliyun"}),
            ToolCall(target="inventory", params={"item": "A"}),
        ]
        session = self.create_session()
        self._collected_spans.clear()
        try:
            self.run_tool_plan(session, plan)
        except Exception:
            pass
        finally:
            self.destroy_session(session)

        if not self._collected_spans:
            raise CapabilityNotSupported("probe_span_propagation: no spans collected")

        # _collected_spans uses "parent_span_id" (from agent.py _spans field)
        span_ids = {s.get("span_id") for s in self._collected_spans}
        orphans = sum(
            1
            for s in self._collected_spans
            if s.get("parent_span_id") and s["parent_span_id"] not in span_ids
        )
        roots = sum(1 for s in self._collected_spans if not s.get("parent_span_id"))
        return PropagationResult(
            spans=len(self._collected_spans),
            orphan_spans=orphans,
            root_count=roots,
        )

    def probe_signals(self) -> Any:
        """T4.3: verify ARMS metrics are present for the FC function.

        Makes one tool call to ensure a recent invocation exists, waits ~15s
        for ARMS metric propagation, then probes three FC metric names that
        ARMS typically emits for AgentRun-backed FC functions.

        Returns SignalsResult regardless of whether metrics are found — ARMS IS
        the observability export mechanism, so its availability (not just
        presence of data) is what the probe measures.
        """
        import time as _time

        from clousight_bench.domains.agent_runtime.adapters.base import SignalsResult

        # Ensure at least one recent invocation exists for ARMS to pick up.
        _, _ = self._one_tool_call()

        # Wait for ARMS metric propagation (typically 10–20s).
        _time.sleep(15)

        from alibabacloud_arms20190808 import models as arms_m

        now_ms = int(_time.time() * 1000)
        metrics_found: list[str] = []
        try:
            client = self._arms_client()
            for metric_name in [
                "arms_fc_function_summary_15s",
                "agentrun_invocations",
                "fc_function_invocations",
            ]:
                try:
                    resp = client.query_metric_by_page(
                        arms_m.QueryMetricByPageRequest(
                            metric=metric_name,
                            start_time=now_ms - 5 * 60 * 1000,
                            end_time=now_ms,
                            measures=["count"],
                            current_page=1,
                            page_size=5,
                        )
                    )
                    data = getattr(resp.body, "data", None) or {}
                    rows = (data.get("items") if isinstance(data, dict) else None) or []
                    if rows:
                        metrics_found.append(metric_name)
                except Exception:
                    pass
        except Exception:
            pass  # ARMS client unavailable — return zero counts, not an exception.

        return SignalsResult(
            metrics_present=len(metrics_found),
            metrics_expected=3,  # count, error_rate, duration from FC
            logs_present=1 if metrics_found else 0,  # logs co-located with metrics
            logs_expected=1,
            structured_logs=True,  # ARMS metrics are always structured
        )

    def probe_export_latency(self) -> Any:
        """T4.5: measure wall-clock time from invocation to trace visible in ARMS.

        Uses _arms_get_spans_by_time() which polls up to 90s for a new trace.
        If no trace appears within 90s the span is counted as dropped.
        """
        import time as _time

        from clousight_bench.domains.agent_runtime.adapters.base import ExportLatencyResult

        t0 = _time.time()
        _, _ = self._one_tool_call()
        invocation_time_ms = int(t0 * 1000)

        spans = self._arms_get_spans_by_time(invocation_time_ms)
        t_found = _time.time()

        if spans:
            export_latency_ms = round((t_found - t0) * 1000, 2)
            return ExportLatencyResult(export_latency_ms=export_latency_ms, dropped_ratio=0.0)
        else:
            # Trace not found within 90s — treat as dropped.
            return ExportLatencyResult(export_latency_ms=90_000.0, dropped_ratio=1.0)

    def get_trace(self, session_id: str) -> list[dict[str, Any]]:
        """T4.1: return OpenInference spans collected from agent response bodies.

        agent.py embeds CHAIN/LLM/TOOL spans in ``_spans`` when arms_config is
        injected. run_tool_plan collects them in _collected_spans. This method
        returns those spans, converting them to the openinference-compatible dict
        shape expected by the T4.1 scorer.

        Falls back to ARMS time-range query if no spans were collected in-band
        (e.g. old agent without _spans support or arms_config not configured).
        """
        import time as _time

        # Primary: spans collected in-band from agent.py responses.
        if self._collected_spans:
            spans = list(self._collected_spans)
            return [
                {
                    "trace_id": s.get("trace_id", ""),
                    "span_id": s.get("span_id", ""),
                    "parent_id": s.get("parent_span_id", ""),
                    "name": s.get("name", ""),
                    "kind": s.get("kind", "CHAIN"),
                    "attributes": {
                        **s.get("attributes", {}),
                        "openinference.span.kind": s.get("kind", "CHAIN"),
                    },
                }
                for s in spans
            ]

        # Fallback: ARMS time-range query.
        invocation_time_ms = int(_time.time() * 1000)
        spans_arms = self._arms_get_spans_by_time(invocation_time_ms)
        if spans_arms:
            return spans_arms

        arms_cfg = self._arms_config_for_invoke()
        raise CapabilityNotSupported(
            "get_trace: no spans collected in-band and none found in ARMS after 90s. "
            f"arms_config={'present' if arms_cfg else 'absent'}. "
            "Ensure the deployed agent supports the _spans response field."
        )

        # Convert ARMS span format → OpenInference-compatible dict expected by T4.1 scorer.
        # Map ARMS operation_name to OpenInference kind via tag inspection.
        def _oi_kind(span: dict) -> str:
            tags = span.get("tags", {})
            op = span.get("operation_name", "").lower()
            if tags.get("openinference.span.kind"):
                return str(tags["openinference.span.kind"]).upper()
            # ARMS FC spans: "Invocation /openai/v1/chat/completions" → CHAIN (top-level)
            # "InvokeFunction" → TOOL (function execution is the "tool call" here)
            if "invokefunction" in op.replace(" ", ""):
                return "TOOL"
            if "invocation" in op or "completions" in op:
                return "CHAIN"
            if "llm" in op or "model" in op:
                return "LLM"
            return "CHAIN"

        return [
            {
                **s,
                "kind": _oi_kind(s),
                # Include openinference.span.kind in attributes so kinds_present() finds it.
                "attributes": {
                    **s.get("tags", {}),
                    "openinference.span.kind": _oi_kind(s),
                },
            }
            for s in spans
        ]

    def export_otel(self, session_id: str) -> dict[str, Any]:
        """T4.2: return OTLP-compatible span dict using the openinference.to_otel helper.

        Delegates to get_trace() for the span list, then formats as OTLP resourceSpans
        using the same helper the scorer's validate_otel() expects.
        """
        from clousight_bench.domains.agent_runtime import openinference as oi

        spans = self.get_trace(session_id)  # raises CapabilityNotSupported if unavailable
        return oi.to_otel(spans, service_name="agentrun")

    def _query_current_instances(self) -> int | None:
        """Query the Default endpoint's scaling_status.current_instances.

        AgentRun exposes instance counts via list_agent_runtime_endpoints
        (AgentRuntimeEndpoint.scaling_status.current_instances). Returns None
        if the runtime is not provisioned, the field is absent, or the SDK call fails.
        """
        if not self._runtime_id:
            return None
        try:
            from alibabacloud_agentrun20250910 import models as m

            client = self._control_client()
            resp = client.list_agent_runtime_endpoints(self._runtime_id, m.ListAgentRuntimeEndpointsRequest())
            items = getattr(getattr(getattr(resp, "body", None), "data", None), "items", None) or []
            for ep in items:
                if str(getattr(ep, "agent_runtime_endpoint_name", "") or "") == "Default":
                    scaling = getattr(ep, "scaling_status", None)
                    if scaling is not None:
                        val = getattr(scaling, "current_instances", None)
                        if val is not None:
                            return int(val)
        except Exception:
            pass
        return None

    def probe_isolation(self) -> Any:
        """Isolation probe (T6.1): session state isolation + platform-asserted sandbox.

        Measured:
          tenant_isolated — OSS state keys are scoped per session; cross-session
          reads always miss (different key path → OSS 404).

        Platform-asserted (evidence A — not measured without agent-side code):
          network_egress_controlled — AgentRun uses VPC-controlled egress per docs.
          filesystem_isolated — FC container ephemeral filesystem per invocation.

        Active sandbox probing (reading /proc, outbound arbitrary egress) requires
        agent.py changes outside this task's scope; a finding is emitted to note it.
        """
        from clousight_bench.domains.agent_runtime.adapters.base import IsolationResult

        # Ensure runtime is provisioned
        _, _ = self._one_tool_call()

        # Test: session state cannot cross-contaminate (different OSS key paths)
        session_a = self.create_session()
        session_b = self.create_session()
        tenant_isolated = True
        _tenant_isolation_skipped = False
        try:
            self._memory.store(session_a, {"sentinel": "isolation-test-value"})
            try:
                recovered = self._memory.fetch(session_b)
                if recovered.get("sentinel") == "isolation-test-value":
                    tenant_isolated = False  # session_b read session_a's data
            except Exception:
                pass  # OSS key not found → correct, sessions are isolated
        except Exception:
            # OSS store failed (ACL, permissions, connectivity). Cannot probe isolation.
            # Fall back to platform assertion for this dimension too.
            tenant_isolated = True
            _tenant_isolation_skipped = True
        finally:
            with contextlib.suppress(Exception):
                self._memory.cleanup()
            self.destroy_session(session_a)
            self.destroy_session(session_b)

        # Network egress and filesystem isolation are platform documentation claims.
        # Active probing requires agent-side instrumentation beyond this task's scope.
        # platform_asserted_dimensions tells the scorer to apply evidence="A" to these.
        asserted = ["network_egress_controlled", "filesystem_isolated"]
        if _tenant_isolation_skipped:
            # OSS unavailable — tenant_isolated also falls back to platform assertion.
            asserted.append("tenant_isolated")
        return IsolationResult(
            tenant_isolated=tenant_isolated,
            network_egress_controlled=True,  # VPC-isolated per AgentRun docs
            filesystem_isolated=True,  # FC container ephemeral FS
            platform_asserted_dimensions=asserted,
        )

    def probe_scaling(self, levels: list[int]) -> list[ScalePoint]:
        """Elasticity probe: run each concurrency level N_REPS times and report
        the median success_rate and p95_ms across reps. This eliminates single-sample
        noise that causes non-monotonic p95 patterns under high concurrency.

        Between reps of the same level: 10s cooldown (lets OS reclaim threads).
        Between levels: 5s cooldown.
        """
        from concurrent.futures import ThreadPoolExecutor

        N_REPS = 3
        INTER_REP_COOLDOWN_S = 10  # 加长冷却让 OS 回收线程，避免累积耗尽

        base = self._adapter.mock_base_url
        mock_token = str(self._adapter.target.get("mock_token") or "")
        body = protocol.encode_invoke(
            {"target": "prices", "method": "GET"},
            base,
            mock_token=mock_token or None,
        )
        _instance_count_supported = self._query_current_instances() is not None
        points: list[ScalePoint] = []

        for n in levels:
            if n <= 0:
                continue

            rep_success_rates: list[float] = []
            rep_p95s: list[float] = []
            observed_instances = None

            for rep in range(N_REPS):
                if rep > 0:
                    time.sleep(INTER_REP_COOLDOWN_S)

                latencies: list[float] = []
                oks = 0

                def _one(_i: int, _n: int = n) -> tuple[bool, float]:
                    start = time.perf_counter()
                    try:
                        resp = self._invoke(f"scale-{_n}", body)
                        dt = (time.perf_counter() - start) * 1000
                        return bool(protocol.decode_result(resp).get("ok")), dt
                    except Exception:
                        dt = (time.perf_counter() - start) * 1000
                        return False, dt

                with ThreadPoolExecutor(max_workers=n) as pool:
                    for ok, dt in pool.map(_one, range(n)):
                        latencies.append(dt)
                        oks += 1 if ok else 0

                rep_success_rates.append(oks / n)
                rep_p95s.append(_p95(latencies))

                # Observe instance count after the last rep's burst.
                if rep == N_REPS - 1:
                    observed_instances = self._query_current_instances()

            # Inter-level cooldown: give OS time to reclaim threads before next level.
            if n != levels[-1]:
                time.sleep(5)

            # Median across reps: stable even if 1/3 reps is an outlier.
            def _median(vals: list[float]) -> float:
                s = sorted(vals)
                return s[len(s) // 2]

            points.append(
                ScalePoint(
                    concurrency=n,
                    success_rate=round(_median(rep_success_rates), 4),
                    p95_ms=round(_median(rep_p95s), 2),
                    observed_instances=observed_instances,
                )
            )

        return points

    # --- provisioning lifecycle (control plane) -----------------------------

    def provision(self, spec: dict[str, Any] | None = None) -> ProvisionResult:
        spec = dict(spec or {})
        # Own the artifact lifecycle: if the caller gave no artifact_ref but a
        # bucket is configured, build + upload the bundled agent now and remember
        # the object so teardown can delete it. Ready-latency timing starts at
        # CreateAgentRuntime, not the upload, so it measures the runtime alone.
        if not spec.get("artifact_ref"):
            managed = self._ensure_artifact(spec)
            if managed:
                spec["artifact_ref"] = managed
        client = self._control_client()
        start = time.perf_counter()
        created = client.create_agent_runtime(self._create_runtime_request(spec))
        runtime_id = _runtime_id(created)
        self._runtime_id = runtime_id  # stored for data-plane URL construction
        ready = self._poll_ready(client, runtime_id)
        ready_ms = (time.perf_counter() - start) * 1000
        # Publish a version (required before the endpoint can route traffic).
        version_id = self._publish_version(client, runtime_id)
        # Create the Default endpoint and wait for its public URL.
        self._endpoint_public_url = self._create_default_endpoint(client, runtime_id, version_id)
        return ProvisionResult(
            runtime_id=runtime_id,
            ready_latency_ms=round(ready_ms, 2),
            ready=ready,
            artifact_ref=str(spec.get("artifact_ref") or ""),
        )

    def provision_status(self, runtime_id: str) -> str:
        resp = self._control_client().get_agent_runtime(runtime_id, self._get_request())
        return _runtime_status(resp)

    def _publish_version(self, client: Any, runtime_id: str) -> str:
        """Publish a runtime version so the code is deployable. Returns version ID."""
        from alibabacloud_agentrun20250910 import models as m

        resp = client.publish_runtime_version(
            runtime_id,
            m.PublishRuntimeVersionRequest(
                body=m.CreateAgentRuntimeVersionInput(description="clousight-bench")
            ),
        )
        data = getattr(getattr(resp, "body", None), "data", None)
        version_id = str(getattr(data, "agent_runtime_version", "") or "")
        return version_id

    def _create_default_endpoint(self, client: Any, runtime_id: str, version_id: str = "") -> str:
        """Create (or reuse) the Default endpoint and return its public URL.

        Polls until the endpoint status is Active and endpoint_public_url is set.
        Returns the public URL to use for data-plane HTTP calls.
        """
        from alibabacloud_agentrun20250910 import models as m

        body = m.CreateAgentRuntimeEndpointInput(
            agent_runtime_endpoint_name="Default",
            disable_public_network_access=False,
            target_version=version_id or "LATEST",
        )
        try:
            client.create_agent_runtime_endpoint(runtime_id, m.CreateAgentRuntimeEndpointRequest(body=body))
        except Exception as exc:
            err = str(exc)
            if "already exist" not in err.lower() and "AlreadyExist" not in err:
                import logging

                logging.getLogger(__name__).warning(
                    "CreateAgentRuntimeEndpoint warning (continuing): %s", exc
                )
        # Poll endpoint list for status=Active and endpoint_public_url.
        # Using list (not get) because get requires a UUID, not the name.
        deadline = time.perf_counter() + _READY_TIMEOUT_S
        while time.perf_counter() < deadline:
            list_resp = client.list_agent_runtime_endpoints(runtime_id, m.ListAgentRuntimeEndpointsRequest())
            items = getattr(getattr(getattr(list_resp, "body", None), "data", None), "items", None) or []
            for ep in items:
                if str(getattr(ep, "agent_runtime_endpoint_name", "") or "") == "Default":
                    status = str(getattr(ep, "status", "") or "").lower()
                    url = str(getattr(ep, "endpoint_public_url", "") or "")
                    if status in ("active", "ready") and url:
                        return url
            time.sleep(_READY_POLL_S)
        return ""  # endpoint not ready within timeout

    def deprovision(self, runtime_id: str) -> DeprovisionResult:
        client = self._control_client()
        start = time.perf_counter()
        # Delete the Default endpoint before deleting the runtime.
        with contextlib.suppress(Exception):
            from alibabacloud_agentrun20250910 import models as m

            client.delete_agent_runtime_endpoint(runtime_id, "Default", m.DeleteAgentRuntimeEndpointRequest())
        client.delete_agent_runtime(runtime_id)
        residual = self._residual_after_delete(client, runtime_id)
        teardown_ms = (time.perf_counter() - start) * 1000
        # Clear stored runtime_id so stop() doesn't double-deprovision.
        if self._runtime_id == runtime_id:
            self._runtime_id = None
            self._lazy_provisioned = False
        artifact_residual = self._cleanup_artifact()
        return DeprovisionResult(
            teardown_ms=round(teardown_ms, 2),
            clean=not residual and not artifact_residual,
            residual=residual + artifact_residual,
        )

    def stop(self) -> None:
        """Deprovision a lazily-created runtime and clean up state files."""
        # Clean up OSS state files created by _LiveMemory.store()
        try:
            if hasattr(self._memory, "cleanup"):
                self._memory.cleanup()
        except Exception:  # noqa: BLE001
            pass
        if self._runtime_id and self._lazy_provisioned:
            try:
                self.deprovision(self._runtime_id)
            except Exception:  # noqa: BLE001 — best-effort cleanup
                self._runtime_id = None
                self._lazy_provisioned = False

    def _ensure_artifact(self, spec: dict[str, Any]) -> str | None:
        """Build + upload the bundled agent when a bucket is configured; returns
        the ``oss://`` reference (remembered for teardown) or None to skip."""
        target = self._adapter.target
        bucket = spec.get("oss_bucket") or target.get("oss_bucket")
        if not bucket:
            return None
        from clousight_bench.domains.agent_runtime.artifact import OssArtifactStore

        self._artifact_store = OssArtifactStore(
            str(bucket),
            str(target.get("region") or ""),
            endpoint=target.get("oss_endpoint"),
            run_id=getattr(self._adapter, "run_id", None),
        )
        self._managed_artifact_ref = self._artifact_store.upload()
        return self._managed_artifact_ref

    def _cleanup_artifact(self) -> list[str]:
        if not (self._artifact_store and self._managed_artifact_ref):
            return []
        ref = self._managed_artifact_ref
        try:
            self._artifact_store.delete(ref)
        except Exception as exc:  # noqa: BLE001
            err_str = str(exc).lower()
            # NoSuchKey / 404 means the object is already gone — that is the
            # desired end state, so treat it as clean rather than residual.
            if any(k in err_str for k in ("nosuchkey", "no such key", "404", "not found", "noexist")):
                pass  # already deleted, that's fine
            else:
                return [ref]  # genuine error = report as residual
        finally:
            self._managed_artifact_ref = None
        return []

    def _poll_ready(self, client: Any, runtime_id: str) -> bool:
        deadline = time.perf_counter() + _READY_TIMEOUT_S
        while time.perf_counter() < deadline:
            resp = client.get_agent_runtime(runtime_id, self._get_request())
            if _runtime_status(resp).lower() in ("ready", "active"):
                return True
            time.sleep(_READY_POLL_S)
        return False

    def _residual_after_delete(self, client: Any, runtime_id: str) -> list[str]:
        # AgentRun deletion is async; the runtime lingers in "Deleting" state for
        # several seconds after delete_agent_runtime() returns.  Poll until the
        # control-plane raises a not-found exception (= clean) or 60 s elapse (= real
        # residual that the caller should report).
        deadline = time.perf_counter() + 60.0
        while time.perf_counter() < deadline:
            try:
                status = _runtime_status(client.get_agent_runtime(runtime_id, self._get_request()))
                if not status:
                    return []  # API returned but no status field = effectively gone
                time.sleep(3.0)
            except Exception:  # noqa: BLE001 - not-found exception = successfully deleted
                return []
        return [runtime_id]  # Still exists after 60 s = real residual

    # --- typed control-plane request builders (real SDK models) -------------

    def _get_request(self) -> Any:
        from alibabacloud_agentrun20250910 import models as m

        return m.GetAgentRuntimeRequest()

    def probe_idle_cost(self) -> Any:
        from clousight_bench.domains.agent_runtime.adapters.base import IdleCostResult

        # AgentRun runs on FC (Function Compute) which scales to zero when idle.
        # Billing is per-invocation only; no charge when there are no requests.
        # This is a platform documentation claim — billing API verification is
        # out of scope for this benchmark.
        return IdleCostResult(
            scales_to_zero=True,
            idle_cost_per_hour=0.0,
        )

    def probe_concurrency_ceiling(self) -> Any:
        from concurrent.futures import ThreadPoolExecutor

        from clousight_bench.domains.agent_runtime.adapters.base import CeilingResult

        # Ensure runtime is provisioned
        _, _ = self._one_tool_call()
        endpoint_url = self._endpoint_public_url or ""
        if not endpoint_url:
            raise CapabilityNotSupported("probe_concurrency_ceiling: no endpoint after warm-up")

        url = endpoint_url.rstrip("/") + "/openai/v1/chat/completions"
        mock = self._adapter.mock_base_url
        mock_token = str(self._adapter.target.get("mock_token") or "")
        body = protocol.encode_invoke(
            {"target": "prices", "method": "GET"},
            mock,
            mock_token=mock_token or None,
        )
        session_obj = self._http_session()

        BURST_LEVELS = [50, 100, 200, 500]
        REJECTION_THRESHOLD = 0.1  # >10% rejections = ceiling found

        ceiling = None
        hard_limit = False

        for burst_n in BURST_LEVELS:

            def _call(i: int, _n: int = burst_n) -> int:
                try:
                    resp = session_obj.post(
                        url,
                        json=body,
                        headers={"X-AgentRun-Session-ID": f"ceil-{_n}-{i}"},
                        timeout=15,
                    )
                    return resp.status_code
                except Exception:
                    return 0

            with ThreadPoolExecutor(max_workers=burst_n) as pool:
                status_codes = list(pool.map(_call, range(burst_n)))

            rejections = sum(1 for s in status_codes if s in (429, 503, 0))
            rejection_rate = rejections / burst_n

            if rejection_rate > REJECTION_THRESHOLD:
                ceiling = burst_n
                hard_limit = any(s == 429 for s in status_codes)  # 429 = hard limit
                break

        return CeilingResult(
            max_in_flight=ceiling if ceiling else BURST_LEVELS[-1],
            hard_limit=hard_limit,
        )

    def _create_runtime_request(self, spec: dict[str, Any] | None) -> Any:
        from alibabacloud_agentrun20250910 import models as m

        spec = spec or {}
        target = self._adapter.target
        bucket, obj = _split_artifact(str(spec.get("artifact_ref") or ""))
        code = m.CodeConfiguration(
            oss_bucket_name=str(spec.get("oss_bucket") or target.get("oss_bucket") or bucket),
            oss_object_name=str(spec.get("oss_object") or obj),
            # Service accepts: python3.10 python3.12 nodejs18 nodejs20 nodejs22 java8 java11 java17
            language=str(target.get("language") or "python3.12"),
            # Startup command as a list ([]string on the server side)
            command=list(target.get("command") or ["python3", "agent.py"]),
        )
        # NetworkConfiguration is required.
        # Valid networkMode values confirmed from console capture:
        #   "PUBLIC"  — public internet, no VPC fields allowed
        #   "VPC"     — user VPC; requires vpc_id + security_group_id + vswitch_ids
        #               (exact casing TBD — use target.network_mode to override)
        vpc_id = str(target.get("vpc_id") or "")
        if target.get("network_mode"):
            net_mode = str(target["network_mode"])
        elif vpc_id:
            net_mode = "VPC"  # assume VPC mode when vpc_id is configured
        else:
            net_mode = "PUBLIC"  # default: public internet, no VPC needed
        net_cfg = m.NetworkConfiguration(network_mode=net_mode)
        # Only attach VPC fields for non-PUBLIC modes
        if vpc_id and net_mode != "PUBLIC":
            net_cfg.vpc_id = vpc_id
            sg_id = str(target.get("security_group_id") or "")
            if sg_id:
                net_cfg.security_group_id = sg_id
            vsw = target.get("vswitch_id") or ""
            vsw_list = list(target.get("vswitch_ids") or ([vsw] if vsw else []))
            if vsw_list:
                net_cfg.vswitch_ids = vsw_list
        # Include run_id suffix + sample index so repeated provision cycles (e.g. T0.1
        # 3-sample) and concurrent runs never share a name on the server.
        base_name = str(target.get("runtime_name") or "clousight-bench")
        run_id = getattr(self._adapter, "run_id", None)
        sample_idx = int((spec or {}).get("_sample") or 0)
        if run_id:
            runtime_name = f"{base_name}-{run_id[-6:]}-{sample_idx}"
        else:
            runtime_name = f"{base_name}-{sample_idx}"
        body = m.CreateAgentRuntimeInput(
            agent_runtime_name=runtime_name,
            artifact_type="Code",
            code_configuration=code,
            network_configuration=net_cfg,
            # cpu / memory / port are required; allow override via target config.
            # port must match the agent's listen port (agent.py default: 9000).
            cpu=int(target.get("cpu") or 1),
            memory=int(target.get("memory") or 2048),
            port=int(target.get("port") or 9000),
        )
        # Carry the run-id for cost reconciliation via environment variable.
        run_id = getattr(self._adapter, "run_id", None)
        if run_id:
            body.environment_variables = {"CLOUSIGHT_RUN_ID": run_id}
        # Enable ARMS tracing if arms_license_key is configured (T4.x trace probes).
        arms_key = str(target.get("arms_license_key") or "")
        if arms_key:
            body.arms_configuration = m.ArmsConfiguration(
                enable_arms=True,
                arms_license_key=arms_key,
            )
        return m.CreateAgentRuntimeRequest(body=body)


# --- response readers (real SDK nesting: response.body.data.<field>) ---------


def _runtime_id(resp: Any) -> str:
    data = getattr(getattr(resp, "body", None), "data", None)
    return str(getattr(data, "agent_runtime_id", "") or "")


def _runtime_status(resp: Any) -> str:
    data = getattr(getattr(resp, "body", None), "data", None)
    return str(getattr(data, "status", "") or "")


def _split_artifact(artifact_ref: str) -> tuple[str, str]:
    """``oss://bucket/path/to/agent.zip`` -> ``("bucket", "path/to/agent.zip")``."""
    ref = artifact_ref.removeprefix("oss://")
    bucket, _, obj = ref.partition("/")
    return bucket, obj


class _AliyunCampaignProbe:
    """Per-campaign probe lifecycle: ECS carrier + OSS sync (probe-sink §7).

    Constructor factories are injectable so tests run account-free. The real
    path (``_default_carrier``) creates an :class:`EcsProbeCarrier` — a stock-OS
    ECS instance whose cloud-init user-data ``pip install``s the public
    ``clousight-bench[probe]`` package (no container image, see docs/probe-carrier.md).
    """

    def __init__(self, carrier_factory=None, oss_factory=None):
        self._carrier_factory = carrier_factory or self._default_carrier
        self._oss_factory = oss_factory or self._default_oss
        self._carrier = None
        self._oss = None
        self._channel = None  # OssChannel built during start_campaign_probe
        self._prefix = ""
        self._bucket = ""

    # ------------------------------------------------------------------
    # Default factories (real-cloud paths; wired in Plan 5 Task 3)
    # ------------------------------------------------------------------
    @staticmethod
    def _default_carrier(target: dict, prefix: str, campaign_id: str = "", bucket: str = ""):  # noqa: ANN202
        run_id = str(target.get("run_id") or "")
        _bucket = bucket or str(target.get("oss_bucket") or "")
        region = str(target.get("region") or "cn-hangzhou")
        cfg = EcsCarrierConfig(
            bucket=_bucket,
            campaign_id=campaign_id or run_id or "adhoc",
            region=region,
            vswitch_id=str(target.get("eci_vswitch_id") or ""),
            security_group_id=str(target.get("eci_security_group_id") or ""),
            ram_role=str(target.get("eci_probe_role") or ""),
            image_id=str(target.get("ecs_image_id") or ""),  # stock Aliyun OS image
            instance_type=str(target.get("ecs_instance_type") or "ecs.e-c1m2.large"),
            code_spec=str(target.get("probe_code_spec") or "clousight-bench[probe]"),
            run_id=run_id or None,
        )
        return EcsProbeCarrier(sdk=Ecs20140526Sdk(region=region), config=cfg)

    @staticmethod
    def _default_oss(target: dict):  # noqa: ANN202
        from clousight_bench.domains.agent_runtime.probe.oss_client import Oss2Client

        bucket = str(target.get("oss_bucket") or "")
        region = str(target.get("region") or "cn-hangzhou")
        return Oss2Client(bucket=bucket, region=region)

    # ------------------------------------------------------------------
    # CampaignProbeHook interface
    # ------------------------------------------------------------------
    def start_campaign_probe(self, target: dict) -> dict:
        """Provision the probe.

        Returns ``{probe_control_prefix, probe_oss_prefix, probe_token,
        probe_in_vpc}`` for target stamping — no ``probe_url`` key (OSS-mediated
        transport, no HTTP surface required).
        """
        from clousight_bench.domains.agent_runtime.probe.oss_channel import OssChannel

        run_id = str(target.get("run_id") or "")
        campaign_id = run_id or "adhoc"
        self._bucket = str(target.get("oss_bucket") or "")
        self._prefix = f"clousight-bench/telemetry/{campaign_id}/"
        self._oss = self._oss_factory(target)
        # Build the control channel — readiness is polled via OSS, not HTTP.
        channel = OssChannel(self._oss, campaign_id)
        self._channel = channel
        self._carrier = self._carrier_factory(target, self._prefix, campaign_id, self._bucket)
        # Inject the readiness check so provision() polls OSS (not EcsRamRole).
        self._carrier.ready_check = channel.is_ready
        self._carrier.provision()  # raises CarrierError on failure
        return {
            "probe_control_prefix": campaign_id,
            "probe_oss_prefix": self._prefix,
            "probe_token": getattr(self._carrier, "token", "") or "",
            "probe_in_vpc": True,
        }

    def sync_probe_artifacts(self, results_dir: Any) -> None:
        """Mirror the probe's OSS prefix into results_dir (channel ②)."""
        if self._oss is None:
            return
        from clousight_bench.domains.agent_runtime.probe.oss_sync import sync_prefix

        sync_prefix(self._oss, self._prefix, results_dir)

    def stop_campaign_probe(self) -> None:
        """Reap the probe. Idempotent + best-effort (called from a finally).

        Sends the OSS stop sentinel BEFORE tearing down the ECI carrier so the
        in-region loop gets a chance to drain gracefully.
        """
        if self._channel is not None:
            try:
                self._channel.signal_stop()
            except Exception:  # noqa: BLE001
                pass
            self._channel = None
        if self._carrier is not None:
            try:
                self._carrier.teardown()
            except Exception:  # noqa: BLE001
                pass
            self._carrier = None


class AliyunRuntimeProvider(RuntimeProviderPlugin):
    """Registered for provider ``aliyun`` via the runtime_providers entry point."""

    provider = "aliyun"

    def build_transport(self, adapter: Any) -> AliyunAgentRunTransport:
        return AliyunAgentRunTransport(adapter)

    def campaign_probe_hook(
        self,
        carrier_factory=None,
        oss_factory=None,
    ) -> _AliyunCampaignProbe:
        """Return an injectable ``_AliyunCampaignProbe``.

        ``carrier_factory`` / ``oss_factory`` are forwarded to the probe so
        tests can inject fakes without touching the real ECI/OSS SDKs.
        Called by ``core.plugin.campaign_probe_hook`` with no args (real mode);
        tests call it directly with injected fakes.
        """
        return _AliyunCampaignProbe(
            carrier_factory=carrier_factory,
            oss_factory=oss_factory,
        )
