"""AWS RuntimeProviderPlugin — registered for provider ``aws``.

Mirrors ``aliyun.AliyunRuntimeProvider``. Kept in a separate module so the
transport import (which may have optional deps) is always lazy and never pulled
in at import time of the campaign_probe module.
"""

from __future__ import annotations

from typing import Any

from clousight_bench.core.plugin import RuntimeProviderPlugin
from clousight_bench.domains.agent_runtime.aws.campaign_probe import _AwsCampaignProbe


class AwsRuntimeProvider(RuntimeProviderPlugin):
    """Registered for provider ``aws`` via the runtime_providers entry point."""

    provider = "aws"

    def build_transport(self, adapter: Any) -> Any:
        # Lazy import: transport file may not exist yet or may have optional deps.
        from clousight_bench.domains.agent_runtime.aws.transport import AwsAgentCoreTransport  # noqa: PLC0415

        return AwsAgentCoreTransport(adapter)

    def campaign_probe_hook(
        self,
        carrier_factory=None,
        store_factory=None,
    ) -> _AwsCampaignProbe:
        """Return an injectable ``_AwsCampaignProbe``.

        ``carrier_factory`` / ``store_factory`` are forwarded to the probe so
        tests can inject fakes without touching the real EC2/S3 SDKs.
        Called by ``core.plugin.campaign_probe_hook`` with no args (real mode);
        tests call it directly with injected fakes.
        """
        return _AwsCampaignProbe(
            carrier_factory=carrier_factory,
            store_factory=store_factory,
        )
