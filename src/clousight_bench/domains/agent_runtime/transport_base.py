"""Shared low-level helpers for the managed-runtime cloud transports.

The Aliyun (``AliyunAgentRunTransport``) and AWS (``AwsAgentCoreTransport``)
data-plane transports — and the in-region probe loop — share a few byte-identical
helpers: the mock-server auth header, the lazily-built in-process probe-fn
registry, and the connection-pooled ``requests.Session``. They live here once.

The cloud-specific transport bodies (invoke, TTFT, the probe suite, provision)
stay in each cloud module because they genuinely differ in session-header,
endpoint attribute and control-plane SDK; only these provider-agnostic helpers
are hoisted.
"""

from __future__ import annotations

from typing import Any

from clousight_bench.domains.agent_runtime.mock_tools import AUTH_HEADER

# Built once at first use (not at import) to avoid the cost of building the
# runner when a module merely imports this helper. Shared across clouds — the
# registry is deterministic, so one cache serves every transport.
_PROBE_FNS: dict | None = None


def auth_headers(mock_token: str) -> dict[str, str]:
    """Return the auth header dict for direct calls to the mock server."""
    return {AUTH_HEADER: mock_token} if mock_token else {}


def get_probe_fns() -> dict:
    """Lazily build + cache the in-process probe-fn registry."""
    global _PROBE_FNS
    if _PROBE_FNS is None:
        from clousight_bench.domains.agent_runtime.probe.server import build_default_runner

        _PROBE_FNS = build_default_runner()._probes
    return _PROBE_FNS


def build_pooled_http_session() -> Any:
    """Return a lazily-created connection-pooled ``requests.Session``.

    ``requests.Session`` is thread-safe for concurrent GET/POST calls and reuses
    HTTPS connections — avoiding the SSL EOF errors that urllib produces under
    concurrent load (no pooling, one TCP connection per call). Allows up to 64
    concurrent connections per host.
    """
    try:
        import requests
        from requests.adapters import HTTPAdapter
    except ImportError as exc:
        raise RuntimeError(
            "the 'requests' library is required for data-plane HTTP calls. "
            "Install it with: pip install requests"
        ) from exc
    s = requests.Session()
    adapter = HTTPAdapter(pool_connections=4, pool_maxsize=64)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s
