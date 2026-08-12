"""Region -> endpoint resolution (convenience layer).

Every provider derives its service endpoint from a region differently: Aliyun
and Huawei fold the region into the host (``svc.<region>.aliyuncs.com``), AWS
does too with its own suffix, and Volcengine keeps a single gateway host and
carries the region as a request parameter. Left to each adapter, that templating
gets copy-pasted four ways and drifts.

This module centralises it: an adapter declares only its *service* name and hands
over ``(provider, region)``; the resolver returns a fully-formed URL (or honours
an explicit ``target['endpoint']`` override for private-cloud / proprietary
regions). It never contacts the network -- it only shapes a URL.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# provider -> how it builds a host from (service, region). ``region_in_host``
# records whether the region is a host component (Aliyun/Huawei/AWS) or a
# request-time parameter against a fixed gateway (Volcengine).
_TEMPLATES: dict[str, dict[str, object]] = {
    "aws": {
        "host": lambda service, region: f"{service}.{region}.amazonaws.com",
        "region_in_host": True,
    },
    "aliyun": {
        "host": lambda service, region: f"{service}.{region}.aliyuncs.com",
        "region_in_host": True,
    },
    "huawei": {
        "host": lambda service, region: f"{service}.{region}.myhuaweicloud.com",
        "region_in_host": True,
    },
    "volcengine": {
        # single OpenAPI gateway; Region travels as a request parameter.
        "host": lambda service, region: "open.volcengineapi.com",
        "region_in_host": False,
    },
}


@dataclass
class Endpoint:
    """A resolved service endpoint (non-secret; safe to fold into config_hash)."""

    provider: str
    service: str
    region: str | None
    url: str
    source: str  # "override" | "template" | "unknown-provider" | "missing-region"
    remediation: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.url) and self.source in ("override", "template")


def _normalize(url: str) -> str:
    url = url.strip()
    if not url:
        return url
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url.rstrip("/")


def resolve_endpoint(
    provider: str | None,
    region: str | None,
    service: str,
    override: str | None = None,
) -> Endpoint:
    """Resolve a service endpoint URL. Order: explicit override -> region template.

    An explicit ``override`` (``target['endpoint']``) always wins -- it is how a
    private-cloud / dedicated region or a proprietary gateway is reached. Otherwise
    the provider's region template applies; a region-in-host provider with no
    region yields a non-ok Endpoint carrying remediation rather than a bad URL.
    """
    provider = provider or ""
    if override:
        return Endpoint(provider, service, region, _normalize(override), "override")

    tmpl = _TEMPLATES.get(provider)
    if tmpl is None:
        return Endpoint(
            provider,
            service,
            region,
            "",
            "unknown-provider",
            remediation=(f"no endpoint template for provider {provider!r}; set target.endpoint explicitly"),
        )

    if tmpl["region_in_host"] and not region:
        return Endpoint(
            provider,
            service,
            region,
            "",
            "missing-region",
            remediation=f"set target.region (or an explicit target.endpoint) for {provider}",
        )

    host_fn: Callable[[str, str | None], str] = tmpl["host"]  # type: ignore[assignment]
    return Endpoint(provider, service, region, _normalize(host_fn(service, region)), "template")
