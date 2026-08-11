"""Local simulated runtime adapter (the provider-less reference).

Proves the harness end-to-end WITHOUT any cloud account: a runtime with a
configurable recovery policy (and configurable state / registration / trace
behaviour) so tasks can verify they correctly distinguish support from absence
before any real adapter exists.

It is just ``ManagedAgentRuntimeAdapter`` with no provider, pinned to the shared
``MockRuntimeTransport`` -- the exact same simulated runtime a cloud adapter uses
in ``mode: mock``. The cloud adapters (aliyun / huawei / volcengine) implement
the same base against live platforms; they must NOT re-implement task or scoring
logic.

Target keys: recovery{mode,max_retries,backoff_ms}, state_persistence,
tool_registration, trace{completeness,otel_export}, mock_port.
"""

from __future__ import annotations

from clousight_bench.domains.agent_runtime.adapters.managed import ManagedAgentRuntimeAdapter


class LocalSimAdapter(ManagedAgentRuntimeAdapter):
    name = "local-sim"
    status = "reference"
    provider = None
    target_example: dict = {
        "startup": {"cold_ms": 200, "warm_ms": 10},
        "recovery": {"mode": "auto-retry"},
        "limits": {"cpu_seconds": 30},
    }
