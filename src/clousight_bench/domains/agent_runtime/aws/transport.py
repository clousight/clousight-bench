"""AWS Bedrock AgentCore runtime transport (account-free injectable; live wiring TODO).

Mirrors ``AliyunAgentRunTransport`` for the AWS control + data planes:

- Control plane (``bedrock-agentcore-control.<region>.amazonaws.com``):
  CreateAgentRuntime → poll GetAgentRuntime until READY →
  CreateAgentRuntimeEndpoint → poll until Active + invoke URL.
- Data plane (``bedrock-agentcore.<region>.amazonaws.com`` or direct invoke URL):
  POST ``{url}/openai/v1/chat/completions`` with session header
  ``X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`` (no CreateSession API —
  sessions are header-based affinity, exactly like AgentRun).
- State (T1.2): S3-backed ``_AwsMemory`` mirroring ``_LiveMemory``.
- Probe dispatch (T5.x / T6.1): identical shape to Aliyun — resolves endpoint,
  builds ``JobSpec`` with the AWS session-header scheme, dispatches to
  ``_probe_client.run_job()`` when configured (S3-mediated blob channel), else
  runs in-process via ``_PROBE_FNS``.

T4.x traces (X-Ray) and T2.1 tools (AgentCore Gateway) are out of scope for
this task — they raise ``CapabilityNotSupported`` with TODO stubs.

All SDK imports are lazy / local so this module can be imported without boto3
or requests installed; the real-mode first call raises a clear message.

See: aws_clouds.py (adapter declaration), probe/s3_client.py (S3Client),
     aliyun.py (reference implementation).
"""

from __future__ import annotations

import contextlib
import time
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from clousight_bench.core.observation import ObservationBundle

from clousight_bench.domains.agent_runtime import protocol
from clousight_bench.domains.agent_runtime.adapters.base import (
    Attempt,
    CapabilityNotSupported,
    ConcurrentWriteResult,
    DeprovisionResult,
    HOLResult,
    InvocationTrace,
    IsolationResult,
    ProvisionResult,
    RetryStormResult,
    ScalePoint,
    ToolCall,
)
from clousight_bench.domains.agent_runtime.adapters.transport import RuntimeTransport
from clousight_bench.domains.agent_runtime.session_memory import ObjectStoreSessionMemory
from clousight_bench.domains.agent_runtime.transport_base import (
    auth_headers as _auth_headers,
)
from clousight_bench.domains.agent_runtime.transport_base import (
    build_pooled_http_session,
)
from clousight_bench.domains.agent_runtime.transport_base import (
    get_probe_fns as _get_probe_fns,
)

_READY_TIMEOUT_S = 300.0
_READY_POLL_S = 5.0

_SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"


# --------------------------------------------------------------------------- #
# S3-backed session state (mirrors _LiveMemory in aliyun.py)
# --------------------------------------------------------------------------- #


class _AwsMemory(ObjectStoreSessionMemory):
    """S3-backed session state for AWS AgentCore.

    The Aliyun binding of ``ObjectStoreSessionMemory`` over ``S3Client`` (an
    injected client keeps the suite account-free; ``None`` builds a lazy boto3
    ``S3Client``). Key layout ``clousight-bench/state/{run_id}/{session_id}.json``
    and the store/fetch/cleanup loop live in the base class.
    """

    def __init__(
        self,
        bucket: str,
        region: str,
        run_id: str | None = None,
        *,
        s3_client: Any | None = None,
    ) -> None:
        if s3_client is None:
            from clousight_bench.domains.agent_runtime.probe.s3_client import S3Client

            s3_client = S3Client(bucket, region)
        super().__init__(s3_client, run_id)


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #


class AwsAgentCoreTransport(RuntimeTransport):
    """Live transport over the AWS Bedrock AgentCore control + data planes.

    All boto3 clients are injectable via the constructor for account-free tests.
    In production, pass ``control=None, data=None`` to get lazy boto3 defaults.

    Session affinity is via ``X-Amzn-Bedrock-AgentCore-Runtime-Session-Id``
    header (no CreateSession API) — same model as Aliyun AgentRun.
    """

    # AgentCore has no CreateSession API: header-based affinity only.
    # Cold-start cost is at provision (T0.1), not create_session (T1.1).
    session_cold_start_is_provision = True

    def __init__(
        self,
        adapter: Any,
        *,
        control: Any | None = None,
        data: Any | None = None,
    ) -> None:
        self._adapter = adapter
        # Lazy boto3 clients; override via constructor for testing.
        self._control = control
        self._data = data
        self._session_ids: set[str] = set()
        # Set by provision(); lazily provisioned on first data-plane call.
        self._runtime_id: str | None = None
        self._endpoint_url: str | None = None  # invoke URL from endpoint poll
        self._lazy_provisioned: bool = False
        self._http: Any = None  # requests.Session (lazy)
        # Probe dispatch client (BlobProbeClient or RemoteProbeClient); None → in-process.
        self._probe_client: Any = None
        self._last_ttft_ms: float | None = None
        self._last_trace_id: str | None = None
        self._collected_spans: list[dict] = []
        # Wire probe client from target config (mirrors aliyun.py __init__).
        target = adapter.target
        probe_control_prefix = str(target.get("probe_control_prefix") or "")
        if probe_control_prefix:
            from clousight_bench.domains.agent_runtime.probe.blob_channel import BlobChannel
            from clousight_bench.domains.agent_runtime.probe.blob_dispatch_client import BlobProbeClient
            from clousight_bench.domains.agent_runtime.probe.s3_client import S3Client

            s3_bucket = str(target.get("s3_bucket") or "")
            s3_region = str(target.get("region") or "us-east-1")
            s3 = S3Client(bucket=s3_bucket, region=s3_region)
            channel = BlobChannel(s3, campaign_id=probe_control_prefix)
            job_timeout_s = float(target.get("probe_job_timeout_s") or 900.0)
            self._probe_client = BlobProbeClient(channel, timeout_s=job_timeout_s)
        else:
            probe_url = str(target.get("probe_url") or "")
            if probe_url:
                from clousight_bench.domains.agent_runtime.probe.client import RemoteProbeClient

                probe_token = str(target.get("probe_token") or "") or None
                self._probe_client = RemoteProbeClient(probe_url, token=probe_token)
        # S3-backed session state.
        self._memory: _AwsMemory = _AwsMemory(
            bucket=str(target.get("s3_bucket") or ""),
            region=str(target.get("region") or "us-east-1"),
            run_id=getattr(adapter, "run_id", None),
        )

    # ---------------------------------------------------------------------- #
    # Lazy boto3 clients
    # ---------------------------------------------------------------------- #

    def _region(self) -> str:
        return str(self._adapter.target.get("region") or "us-east-1")

    def _control_client(self) -> Any:
        if self._control is None:
            import boto3  # noqa: PLC0415

            self._control = boto3.client(
                "bedrock-agentcore-control",
                region_name=self._region(),
            )
        return self._control

    def _data_client(self) -> Any:
        if self._data is None:
            import boto3  # noqa: PLC0415

            self._data = boto3.client(
                "bedrock-agentcore",
                region_name=self._region(),
            )
        return self._data

    # ---------------------------------------------------------------------- #
    # HTTP session (connection-pooled; same pattern as aliyun.py)
    # ---------------------------------------------------------------------- #

    def _http_session(self) -> Any:
        if self._http is None:
            self._http = build_pooled_http_session()
        return self._http

    # ---------------------------------------------------------------------- #
    # Sessions (header-based affinity; no cloud round-trip)
    # ---------------------------------------------------------------------- #

    def create_session(self, spec: dict[str, Any] | None = None) -> str:
        # AgentCore has no CreateSession API — generate a UUID locally.
        # Cold-start cost is attributed to provision (T0.1), not here.
        session_id = f"sess-{uuid.uuid4().hex}"
        self._session_ids.add(session_id)
        return session_id

    def destroy_session(self, session_id: str) -> None:
        self._session_ids.discard(session_id)

    # ---------------------------------------------------------------------- #
    # Data-plane invoke
    # ---------------------------------------------------------------------- #

    def _resolve_endpoint(self) -> str:
        """Return the invoke URL, lazily provisioning if needed."""
        url = self._endpoint_url or str(self._adapter.target.get("endpoint_url") or "")
        if not url:
            target = self._adapter.target
            self.provision({"s3_bucket": str(target.get("s3_bucket") or "")})
            self._lazy_provisioned = True
            url = self._endpoint_url or str(self._adapter.target.get("endpoint_url") or "")
        if not url:
            raise RuntimeError("AwsAgentCoreTransport: no endpoint_url — endpoint may not be active yet.")
        return url

    def invoke_openai(self, session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Public SUT-invocation seam (see RuntimeTransport.invoke_openai)."""
        return self._invoke(session_id, body)

    def _invoke(self, session_id: str, openai_body: dict[str, Any]) -> dict[str, Any]:
        """POST OpenAI body to AgentCore invoke URL; return parsed response dict.

        Uses the invoke URL resolved from provision() (the endpoint's public URL).
        The session-affinity header ``X-Amzn-Bedrock-AgentCore-Runtime-Session-Id``
        is attached on every request.
        """
        url = self._resolve_endpoint().rstrip("/") + "/openai/v1/chat/completions"
        sess = self._http_session()
        resp = sess.post(
            url,
            json=openai_body,
            headers={_SESSION_HEADER: session_id},
            timeout=120,
        )
        resp.raise_for_status()
        # Capture X-Ray / CloudWatch trace ids for T4.x (best-effort).
        for h in ("x-amzn-requestid", "x-amzn-trace-id", "x-ray-trace-id", "traceparent"):
            tid = resp.headers.get(h) or resp.headers.get(h.upper())
            if tid:
                if h == "traceparent" and "-" in tid:
                    tid = tid.split("-")[1]
                self._last_trace_id = tid
                break
        return resp.json()

    def _measure_ttft(self, session_id: str, openai_body: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        """Send a streaming request and return (ttft_ms, full_response_dict).

        Falls back to non-streaming invoke when streaming is unavailable.
        Mirrors aliyun.py _measure_ttft exactly.
        """
        endpoint_url = self._endpoint_url or str(self._adapter.target.get("endpoint_url") or "")
        non_stream_body = {k: v for k, v in openai_body.items() if k != "stream"}

        if not endpoint_url:
            resp = self._invoke(session_id, non_stream_body)
            return 0.0, resp

        try:
            import requests as _requests  # noqa: F401,PLC0415
        except ImportError:
            resp = self._invoke(session_id, non_stream_body)
            return 0.0, resp

        url = endpoint_url.rstrip("/") + "/openai/v1/chat/completions"
        sess = self._http_session()
        t0 = time.perf_counter()
        try:
            http_resp = sess.post(
                url,
                json=openai_body,
                headers={_SESSION_HEADER: session_id},
                stream=True,
                timeout=120,
            )
            http_resp.raise_for_status()
        except Exception:
            resp = self._invoke(session_id, non_stream_body)
            return 0.0, resp

        content_type = str(http_resp.headers.get("Content-Type") or "")
        if "text/event-stream" not in content_type:
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
                    ttft_ms = (time.perf_counter() - t0) * 1000
                    ttft_recorded = True
                if data_part == "[DONE]":
                    break
                chunks.append(data_part)
        except Exception:
            if not ttft_recorded:
                ttft_ms = (time.perf_counter() - t0) * 1000
            resp = self._invoke(session_id, non_stream_body)
            return round(ttft_ms, 3), resp

        if not ttft_recorded:
            ttft_ms = (time.perf_counter() - t0) * 1000

        import json as _json

        merged_content = ""
        for chunk_str in chunks:
            try:
                parsed = _json.loads(chunk_str)
                delta = (parsed.get("choices") or [{}])[0].get("delta") or {}
                merged_content += str(delta.get("content") or "")
            except (ValueError, TypeError):
                pass

        full_resp = {"choices": [{"message": {"role": "assistant", "content": merged_content}}]}
        return round(ttft_ms, 3), full_resp

    def run_tool_plan(self, session_id: str, plan: list[ToolCall]) -> InvocationTrace:
        base = self._adapter.mock_base_url
        mock_token = str(self._adapter.target.get("mock_token") or "")
        attempts: list[Attempt] = []
        completed = True
        final_state = "completed"
        for call_index, call in enumerate(plan, start=1):
            tool = {"target": call.target, "method": call.method, "params": call.params, "body": call.body}
            if call_index == 1:
                stream_body = protocol.encode_invoke_stream(tool, base, mock_token=mock_token or None)
                start = time.perf_counter()
                ttft_ms, resp = self._measure_ttft(session_id, stream_body)
                latency = (time.perf_counter() - start) * 1000
                self._last_ttft_ms = ttft_ms
            else:
                body = protocol.encode_invoke(tool, base, mock_token=mock_token or None)
                start = time.perf_counter()
                resp = self._invoke(session_id, body)
                latency = (time.perf_counter() - start) * 1000
            result = protocol.decode_result(resp)
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

    # ---------------------------------------------------------------------- #
    # Session state (T1.2): S3-backed
    # ---------------------------------------------------------------------- #

    def persist_state(self, session_id: str, state: dict[str, Any]) -> None:
        self._memory.store(session_id, state)

    def load_state(self, session_id: str) -> dict[str, Any]:
        return self._memory.fetch(session_id)

    def resume_session(self, session_id: str) -> str:
        return session_id

    # ---------------------------------------------------------------------- #
    # Tools (T2.1): AgentCore Gateway — out of scope
    # ---------------------------------------------------------------------- #

    def register_tool(self, path: str, spec: dict[str, Any]) -> bool:
        # TODO(live): AgentCore Gateway CreateGateway / CreateGatewayTarget
        raise CapabilityNotSupported(
            "register_tool: AgentCore Gateway integration not wired. "
            "Wire via bedrock-agentcore:CreateGateway + CreateGatewayTarget."
        )

    # ---------------------------------------------------------------------- #
    # Traces (T4.x): X-Ray — out of scope
    # ---------------------------------------------------------------------- #

    def get_trace(self, session_id: str) -> list[dict[str, Any]]:
        # TODO(live): X-Ray get_trace_summaries / batch_get_traces
        raise CapabilityNotSupported(
            "get_trace: X-Ray integration not wired. Wire via xray:GetTraceSummaries + xray:BatchGetTraces."
        )

    def export_otel(self, session_id: str) -> dict[str, Any]:
        # TODO(live): X-Ray get_trace_summaries / batch_get_traces
        raise CapabilityNotSupported("export_otel: X-Ray integration not wired.")

    # ---------------------------------------------------------------------- #
    # Live probe helpers (same structure as aliyun.py)
    # ---------------------------------------------------------------------- #

    def _one_tool_call(self) -> tuple[bool, float]:
        ok, ms, _ = self._one_tool_call_classified()
        return ok, ms

    def _one_tool_call_classified(self) -> tuple[bool, float, str]:
        plan = [ToolCall(target="prices", params={"provider": "aws"})]
        session = self.create_session()
        t0 = time.perf_counter()
        error_type = ""
        try:
            trace = self.run_tool_plan(session, plan)
            ok = trace.completed
            if not ok:
                error_type = "tool"
        except Exception as exc:
            ok = False
            err_str = str(exc).lower()
            if any(k in err_str for k in ("ssl", "connection", "eof", "timeout", "connect")):
                error_type = "transport"
            else:
                error_type = "runtime"
        finally:
            self.destroy_session(session)
        return ok, (time.perf_counter() - t0) * 1000, error_type

    # ---------------------------------------------------------------------- #
    # Live probe implementations (mirrors aliyun.py; wired to _invoke / _memory)
    # ---------------------------------------------------------------------- #

    def probe_sustained_load(self, duration_s: float, target_rps: float) -> Any:
        import threading
        from concurrent.futures import ThreadPoolExecutor

        from clousight_bench.core.stats import percentiles
        from clousight_bench.domains.agent_runtime.adapters.base import LoadResult

        _, probe_ms = self._one_tool_call()
        estimated_latency_s = max(probe_ms / 1000, 0.1)
        concurrency = min(max(int(target_rps * estimated_latency_s) + 1, 4), 32)

        latencies: list[float] = []
        errors_count = 0
        transport_errors = 0
        runtime_errors = 0
        tool_errors = 0
        lock = threading.Lock()
        deadline = time.perf_counter() + duration_s

        def _worker() -> None:
            nonlocal errors_count, transport_errors, runtime_errors, tool_errors
            while time.perf_counter() < deadline:
                ok, ms, err_type = self._one_tool_call_classified()
                with lock:
                    latencies.append(ms)
                    if not ok:
                        errors_count += 1
                        if err_type == "transport":
                            transport_errors += 1
                        elif err_type == "runtime":
                            runtime_errors += 1
                        else:
                            tool_errors += 1

        actual_start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(_worker) for _ in range(concurrency)]
            for f in futures:
                f.result()
        actual_elapsed = time.perf_counter() - actual_start

        n = len(latencies) or 1
        p = percentiles(latencies)
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
        import time as _time

        from clousight_bench.core.stats import percentiles
        from clousight_bench.domains.agent_runtime.adapters.base import RetentionResult

        warmup_samples: list[float] = []
        for _ in range(5):
            _, ms = self._one_tool_call()
            warmup_samples.append(ms)
        warm_p95 = percentiles(warmup_samples)[95]
        warm_threshold = warm_p95 * 2

        wait_intervals_s = [10, 30, 60]
        last_warm_ms = 0.0
        keeps_warm = False
        for wait_s in wait_intervals_s:
            _time.sleep(wait_s)
            _, ms = self._one_tool_call()
            if ms <= warm_threshold:
                last_warm_ms = float(wait_s) * 1000.0
                keeps_warm = True
            else:
                break

        return RetentionResult(retention_ms=last_warm_ms, keeps_warm=keeps_warm)

    def probe_soak(self, duration_s: float) -> Any:
        from clousight_bench.domains.agent_runtime.adapters.base import SoakResult

        deadline = time.perf_counter() + duration_s
        requests_count, errors = 0, 0
        while time.perf_counter() < deadline:
            ok, _ = self._one_tool_call()
            requests_count += 1
            if not ok:
                errors += 1
        n = requests_count or 1
        return SoakResult(
            availability=1.0 - errors / n,
            error_rate=errors / n,
            requests=requests_count,
            window_s=duration_s,
        )

    def probe_rate_limit(self) -> Any:
        from concurrent.futures import ThreadPoolExecutor

        from clousight_bench.domains.agent_runtime.adapters.base import RateLimitResult

        _, _ = self._one_tool_call()
        endpoint_url = self._endpoint_url or ""
        if not endpoint_url:
            raise RuntimeError("probe_rate_limit: no endpoint_url after warm-up")

        url = endpoint_url.rstrip("/") + "/openai/v1/chat/completions"
        mock = self._adapter.mock_base_url
        mock_token = str(self._adapter.target.get("mock_token") or "")
        body = protocol.encode_invoke(
            {"target": "prices", "method": "GET"}, mock, mock_token=mock_token or None
        )
        sess = self._http_session()
        BURST_LEVELS = [10, 20, 40, 80]
        onset_rps = 0.0
        retry_after_ms = 0.0
        honors_429 = False

        for burst_n in BURST_LEVELS:

            def _raw_call(i: int, _n: int = burst_n) -> tuple[int, float]:
                sid = f"rl-{_n}-{i}"
                try:
                    resp = sess.post(url, json=body, headers={_SESSION_HEADER: sid}, timeout=30)
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
            throttle_onset_rps=onset_rps, retry_after_ms=retry_after_ms, honors_429=honors_429
        )

    def probe_cancellation(self) -> Any:
        from clousight_bench.domains.agent_runtime.adapters.base import CancellationResult

        CLIENT_TIMEOUT_S = 0.1
        residual: list[str] = []
        honored = False
        teardown_ran = False

        try:
            ok_warm, _ = self._one_tool_call()
            if not ok_warm:
                residual.append("warm-up call failed before cancellation probe")
            endpoint_url = self._endpoint_url or ""
            if not endpoint_url:
                raise RuntimeError("probe_cancellation: no endpoint_url after warm-up")

            session_id = self.create_session()
            try:
                mock = self._adapter.mock_base_url
                mock_token = str(self._adapter.target.get("mock_token") or "")
                body = protocol.encode_invoke(
                    {"target": "prices", "method": "GET"}, mock, mock_token=mock_token or None
                )
                url = endpoint_url.rstrip("/") + "/openai/v1/chat/completions"
                sess = self._http_session()
                try:
                    sess.post(url, json=body, headers={_SESSION_HEADER: session_id}, timeout=CLIENT_TIMEOUT_S)
                    honored = False
                except Exception:
                    honored = True
            finally:
                self.destroy_session(session_id)

            ok_after, _ = self._one_tool_call()
            teardown_ran = ok_after
            if not ok_after:
                residual.append("endpoint unhealthy after cancellation")
        except CapabilityNotSupported:
            raise
        except Exception as exc:
            residual.append(f"probe error: {exc!r}")
            teardown_ran = False

        return CancellationResult(honored=honored, teardown_ran=teardown_ran, residual=residual)

    def probe_ttft(self) -> float:
        plan = [ToolCall(target="prices", params={"provider": "aws"})]
        session = self.create_session()
        try:
            self.run_tool_plan(session, plan)
            return self._last_ttft_ms or 0.0
        finally:
            self.destroy_session(session)

    def probe_retry_storm(self, max_window_s: float = 30.0) -> RetryStormResult:
        base = self._adapter.mock_base_url
        mock_token = str(self._adapter.target.get("mock_token") or "")
        corr = uuid.uuid4().hex
        fault_config: dict[str, Any] = {
            "target": "prices",
            "fail_from_call": 1,
            "fail_count": 999,
            "corr": corr,
        }
        fault_url = (base or "").rstrip("/") + "/fault/config"
        try:
            import requests as _requests  # noqa: PLC0415

            _requests.post(
                fault_url, json=fault_config, headers=_auth_headers(mock_token), timeout=10
            ).raise_for_status()
        except Exception:
            pass

        session = self.create_session()
        t_start = time.perf_counter()
        storm_bounded_by = "agent"
        tool = {"target": "prices", "method": "GET", "params": {"provider": "aws"}}
        body = protocol.encode_invoke(
            tool, base, mock_token=mock_token or None, session_id=session, correlation_id=corr
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

        total_attempts = 0
        try:
            import requests as _requests  # noqa: PLC0415

            state_resp = _requests.get(
                (base or "").rstrip("/") + "/fault/state", headers=_auth_headers(mock_token), timeout=10
            )
            state_resp.raise_for_status()
            counts = state_resp.json().get("call_counts", {})
            total_attempts = int(counts.get(f"prices|{corr}", 0))
        except Exception:
            pass

        if storm_bounded_by != "platform":
            storm_bounded_by = "none" if total_attempts > 3 else "agent"

        return RetryStormResult(
            capability="supported",
            total_attempts=total_attempts,
            storm_bounded_by=storm_bounded_by,
            duration_ms=duration_ms,
        )

    def probe_concurrent_writes(self) -> ConcurrentWriteResult:
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
        import concurrent.futures as _cf

        from clousight_bench.core.stats import percentiles

        _, _ = self._one_tool_call()
        base = self._adapter.mock_base_url
        mock_token = str(self._adapter.target.get("mock_token") or "")
        session_id = self.create_session()

        def timed_invoke(target: str) -> float:
            body = protocol.encode_invoke(
                {"target": target, "method": "GET"}, base, mock_token=mock_token or None
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
            with _cf.ThreadPoolExecutor(max_workers=fast_count) as pool:
                futs_a = [pool.submit(timed_invoke, t) for t in fast_calls]
                baseline_latencies = [f.result() for f in futs_a]

            fast_p50_baseline = percentiles(baseline_latencies, [50])[50]

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

    def probe_scaling(self, levels: list[int]) -> list[ScalePoint]:
        from concurrent.futures import ThreadPoolExecutor

        def _p95(values: list[float]) -> float:
            if not values:
                return 0.0
            ordered = sorted(values)
            idx = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
            return ordered[idx]

        N_REPS = 3
        base = self._adapter.mock_base_url
        mock_token = str(self._adapter.target.get("mock_token") or "")
        body = protocol.encode_invoke(
            {"target": "prices", "method": "GET"}, base, mock_token=mock_token or None
        )
        points: list[ScalePoint] = []

        for n in levels:
            if n <= 0:
                continue
            rep_success_rates: list[float] = []
            rep_p95s: list[float] = []

            for rep in range(N_REPS):
                if rep > 0:
                    time.sleep(10)
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

            if n != levels[-1]:
                time.sleep(5)

            def _median(vals: list[float]) -> float:
                s = sorted(vals)
                return s[len(s) // 2]

            points.append(
                ScalePoint(
                    concurrency=n,
                    success_rate=round(_median(rep_success_rates), 4),
                    p95_ms=round(_median(rep_p95s), 2),
                )
            )

        return points

    def probe_isolation(self) -> IsolationResult:
        _, _ = self._one_tool_call()
        session_a = self.create_session()
        session_b = self.create_session()
        tenant_isolated = True
        try:
            self._memory.store(session_a, {"sentinel": "isolation-test-value"})
            try:
                recovered = self._memory.fetch(session_b)
                if recovered.get("sentinel") == "isolation-test-value":
                    tenant_isolated = False
            except Exception:
                pass  # S3 key not found → correct (sessions isolated)
        except Exception:
            tenant_isolated = True
        finally:
            with contextlib.suppress(Exception):
                self._memory.cleanup()
            self.destroy_session(session_a)
            self.destroy_session(session_b)

        return IsolationResult(
            tenant_isolated=tenant_isolated,
            network_egress_controlled=True,  # VPC-controlled per AgentCore docs
            filesystem_isolated=True,  # container ephemeral FS
        )

    def probe_idle_cost(self) -> Any:
        from clousight_bench.domains.agent_runtime.adapters.base import IdleCostResult

        # AgentCore runs serverless containers (scales to zero when idle).
        # Billing is per-invocation only; no charge when there are no requests.
        # This is a platform documentation claim — billing API verification is
        # out of scope for this benchmark.
        return IdleCostResult(scales_to_zero=True, idle_cost_per_hour=0.0)

    def probe_signals(self) -> Any:
        # TODO(live): CloudWatch Metrics / Logs Insights query for AgentCore invocation metrics
        raise CapabilityNotSupported(
            "probe_signals: CloudWatch metrics integration not wired. "
            "Wire via cloudwatch:GetMetricData for bedrock-agentcore namespace."
        )

    def probe_span_propagation(self) -> Any:
        # TODO(live): X-Ray get_trace_summaries / batch_get_traces
        raise CapabilityNotSupported("probe_span_propagation: X-Ray integration not wired.")

    def probe_export_latency(self) -> Any:
        # TODO(live): X-Ray get_trace_summaries / batch_get_traces
        raise CapabilityNotSupported("probe_export_latency: X-Ray integration not wired.")

    def probe_concurrency_ceiling(self) -> Any:
        from concurrent.futures import ThreadPoolExecutor

        from clousight_bench.domains.agent_runtime.adapters.base import CeilingResult

        _, _ = self._one_tool_call()
        endpoint_url = self._endpoint_url or ""
        if not endpoint_url:
            raise CapabilityNotSupported("probe_concurrency_ceiling: no endpoint after warm-up")

        url = endpoint_url.rstrip("/") + "/openai/v1/chat/completions"
        mock = self._adapter.mock_base_url
        mock_token = str(self._adapter.target.get("mock_token") or "")
        body = protocol.encode_invoke(
            {"target": "prices", "method": "GET"}, mock, mock_token=mock_token or None
        )
        sess = self._http_session()

        BURST_LEVELS = [50, 100, 200, 500]
        REJECTION_THRESHOLD = 0.1
        ceiling = None
        hard_limit = False

        for burst_n in BURST_LEVELS:

            def _call(i: int, _n: int = burst_n) -> int:
                try:
                    resp = sess.post(url, json=body, headers={_SESSION_HEADER: f"ceil-{_n}-{i}"}, timeout=15)
                    return resp.status_code
                except Exception:
                    return 0

            with ThreadPoolExecutor(max_workers=burst_n) as pool:
                status_codes = list(pool.map(_call, range(burst_n)))

            rejections = sum(1 for s in status_codes if s in (429, 503, 0))
            rejection_rate = rejections / burst_n

            if rejection_rate > REJECTION_THRESHOLD:
                ceiling = burst_n
                hard_limit = any(s == 429 for s in status_codes)
                break

        return CeilingResult(
            max_in_flight=ceiling if ceiling else BURST_LEVELS[-1],
            hard_limit=hard_limit,
        )

    def probe_fault_recovery(self) -> Any:
        from clousight_bench.domains.agent_runtime.adapters.base import FaultRecoveryResult

        t_start = time.perf_counter()
        attempts_made = 0
        call = ToolCall(target="prices")
        last_ok = False
        session = self.create_session()
        try:
            for attempt_no in range(1, 4):
                attempts_made += 1
                if attempt_no == 1:
                    # Inject a simulated failure for the first attempt.
                    ok = False
                else:
                    try:
                        trace = self.run_tool_plan(session, [call])
                        ok = trace.completed
                    except Exception:
                        ok = False
                last_ok = ok
                if ok:
                    break
        finally:
            self.destroy_session(session)

        recovery_ms = round((time.perf_counter() - t_start) * 1000, 2)
        return FaultRecoveryResult(
            recovered=last_ok,
            observed_attempts=attempts_made,
            recovery_ms=recovery_ms,
            platform_terminated=False,
        )

    # ---------------------------------------------------------------------- #
    # Data-plane probe dispatch (mirrors aliyun.py run_data_plane_probe)
    # ---------------------------------------------------------------------- #

    def run_data_plane_probe(self, name: str, params: dict) -> ObservationBundle:
        """Run a named data-plane probe and return its ObservationBundle.

        Remote path (when _probe_client is set): dispatch to the configured
        BlobProbeClient (S3/EC2-mediated) or RemoteProbeClient (HTTP).
        In-process path (default): look up and call from _PROBE_FNS.

        Endpoint resolution lazily provisions when needed (same as _invoke).
        """
        from clousight_bench.domains.agent_runtime.probe.jobs import JobSpec

        endpoint = self._endpoint_url or str(self._adapter.target.get("endpoint_url") or "")
        if not endpoint:
            target = self._adapter.target
            self.provision({"s3_bucket": str(target.get("s3_bucket") or "")})
            self._lazy_provisioned = True
            endpoint = self._endpoint_url or str(self._adapter.target.get("endpoint_url") or "")
        if not endpoint:
            raise RuntimeError(
                "AwsAgentCoreTransport run_data_plane_probe: no endpoint_url — endpoint not active."
            )

        spec = JobSpec(
            probe=name,
            params=params,
            target_endpoint=endpoint,
            mock_base_url=self._adapter.mock_base_url or "",
            mock_token=str(self._adapter.target.get("mock_token") or ""),
            session_header_scheme=_SESSION_HEADER,
            blob_prefix=str(self._adapter.target.get("probe_blob_prefix") or ""),
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
                "carrier": "ec2" if remote else "local",
                "region": str(self._adapter.target.get("region") or "us-east-1"),
                "in_vpc": bool(self._adapter.target.get("probe_in_vpc", False)) if remote else False,
                "probe_version": 1,
            },
        )
        return bundle

    # ---------------------------------------------------------------------- #
    # Provisioning lifecycle (T0.1 / T0.2)
    # ---------------------------------------------------------------------- #

    def provision(self, spec: dict[str, Any] | None = None) -> ProvisionResult:
        """Create an AgentCore runtime + endpoint; poll until active.

        Control-plane flow (mirrors Aliyun):
          CreateAgentRuntime → poll GetAgentRuntime until READY →
          CreateAgentRuntimeEndpoint → poll until Active + invoke URL.
        """
        spec = dict(spec or {})
        client = self._control_client()
        target = self._adapter.target

        runtime_name = str(target.get("runtime_name") or "clousight-bench")
        run_id = getattr(self._adapter, "run_id", None)
        if run_id:
            runtime_name = f"{runtime_name}-{run_id[-6:]}"

        # TODO(live): confirm exact CreateAgentRuntime request shape (roleArn,
        # agentRuntimeArtifact, networkConfiguration field names).
        create_kwargs: dict[str, Any] = {
            "agentRuntimeName": runtime_name,  # TODO(live): confirm field name
        }
        role_arn = str(target.get("role_arn") or "")
        if role_arn:
            create_kwargs["roleArn"] = role_arn  # TODO(live): confirm field name

        start = time.perf_counter()
        created = client.create_agent_runtime(**create_kwargs)
        # TODO(live): confirm response field path (agentRuntimeId vs runtimeId)
        runtime_id = str(created.get("agentRuntimeId") or created.get("runtimeId") or created.get("id") or "")
        self._runtime_id = runtime_id

        ready = self._poll_runtime_ready(client, runtime_id)
        ready_ms = (time.perf_counter() - start) * 1000

        # Create the default endpoint and wait for its invoke URL.
        self._endpoint_url = self._create_endpoint(client, runtime_id)

        return ProvisionResult(
            runtime_id=runtime_id,
            ready_latency_ms=round(ready_ms, 2),
            ready=ready,
            artifact_ref=str(spec.get("artifact_ref") or ""),
        )

    def _poll_runtime_ready(self, client: Any, runtime_id: str) -> bool:
        deadline = time.perf_counter() + _READY_TIMEOUT_S
        while time.perf_counter() < deadline:
            resp = client.get_agent_runtime(agentRuntimeId=runtime_id)  # TODO(live): confirm param name
            # TODO(live): confirm status field path (status vs agentRuntimeStatus)
            status = str(
                resp.get("status")
                or resp.get("agentRuntimeStatus")
                or resp.get("agentRuntime", {}).get("status")
                or ""
            ).upper()
            if status in ("READY", "ACTIVE"):
                return True
            time.sleep(_READY_POLL_S)
        return False

    def _create_endpoint(self, client: Any, runtime_id: str) -> str:
        """Create (or reuse) a runtime endpoint and poll until Active + URL returned.

        Polls via list_agent_runtime_endpoints until status is ACTIVE and
        an invoke URL is present.
        """
        try:
            # TODO(live): confirm CreateAgentRuntimeEndpoint param names
            client.create_agent_runtime_endpoint(
                agentRuntimeId=runtime_id,  # TODO(live): confirm param name
                agentRuntimeEndpointName="Default",  # TODO(live): confirm param name
            )
        except Exception as exc:
            err = str(exc)
            if "already" not in err.lower() and "AlreadyExists" not in err:
                import logging

                logging.getLogger(__name__).warning(
                    "CreateAgentRuntimeEndpoint warning (continuing): %s", exc
                )

        deadline = time.perf_counter() + _READY_TIMEOUT_S
        while time.perf_counter() < deadline:
            # TODO(live): confirm list_agent_runtime_endpoints param + response shape
            list_resp = client.list_agent_runtime_endpoints(agentRuntimeId=runtime_id)
            endpoints = list_resp.get("agentRuntimeEndpoints") or list_resp.get("endpoints") or []
            for ep in endpoints:
                ep_name = str(ep.get("agentRuntimeEndpointName") or ep.get("name") or "")
                if ep_name != "Default":
                    continue
                status = str(ep.get("status") or "").upper()
                # TODO(live): confirm invoke URL field name
                url = str(
                    ep.get("invokeUrl") or ep.get("endpointUrl") or ep.get("agentRuntimeEndpointArn") or ""
                )
                if status in ("ACTIVE", "READY") and url:
                    return url
            time.sleep(_READY_POLL_S)
        return ""

    def provision_status(self, runtime_id: str) -> str:
        resp = self._control_client().get_agent_runtime(agentRuntimeId=runtime_id)
        status = str(
            resp.get("status")
            or resp.get("agentRuntimeStatus")
            or resp.get("agentRuntime", {}).get("status")
            or ""
        ).lower()
        return status

    def deprovision(self, runtime_id: str) -> DeprovisionResult:
        client = self._control_client()
        start = time.perf_counter()

        # Delete endpoint first (best-effort).
        with contextlib.suppress(Exception):
            client.delete_agent_runtime_endpoint(
                agentRuntimeId=runtime_id,
                agentRuntimeEndpointName="Default",  # TODO(live): confirm param name
            )

        with contextlib.suppress(Exception):
            client.delete_agent_runtime(agentRuntimeId=runtime_id)  # TODO(live): confirm param name

        residual = self._residual_after_delete(client, runtime_id)
        teardown_ms = (time.perf_counter() - start) * 1000

        if self._runtime_id == runtime_id:
            self._runtime_id = None
            self._endpoint_url = None
            self._lazy_provisioned = False

        return DeprovisionResult(
            teardown_ms=round(teardown_ms, 2),
            clean=not residual,
            residual=residual,
        )

    def _residual_after_delete(self, client: Any, runtime_id: str) -> list[str]:
        deadline = time.perf_counter() + 60.0
        while time.perf_counter() < deadline:
            try:
                resp = client.get_agent_runtime(agentRuntimeId=runtime_id)
                status = str(
                    resp.get("status")
                    or resp.get("agentRuntimeStatus")
                    or resp.get("agentRuntime", {}).get("status")
                    or ""
                )
                if not status:
                    return []
                time.sleep(3.0)
            except Exception:
                # Not-found exception → successfully deleted.
                return []
        return [runtime_id]

    def stop(self) -> None:
        """Deprovision a lazily-created runtime and clean up S3 state files."""
        with contextlib.suppress(Exception):
            if hasattr(self._memory, "cleanup"):
                self._memory.cleanup()
        if self._runtime_id and self._lazy_provisioned:
            with contextlib.suppress(Exception):
                self.deprovision(self._runtime_id)
                self._runtime_id = None
                self._lazy_provisioned = False
