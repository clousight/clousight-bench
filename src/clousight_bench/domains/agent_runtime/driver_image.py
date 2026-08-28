"""Region-agnostic docker-image strategy for the SWE-bench driver host.

The driver host runs the upstream SWE-bench harness, which pulls docker images
(and clones repos from GitHub). Whether Docker Hub / GitHub are reachable depends
entirely on the region the operator happened to choose — which the tool does NOT
know in advance and must not hardcode.

Rather than make the operator paste a per-account Aliyun image-accelerator address
(that ``<id>.mirror.aliyuncs.com`` value has no OpenAPI — the ACR API has zero
``accelerator`` actions; only ``get_instance_endpoint`` is programmatic), the
driver *detects its own situation at boot* and picks a strategy:

* Docker Hub reachable (overseas / unblocked region) → direct pull, no mirror.
* Docker Hub blocked but the account's ACR endpoint is discoverable → route
  through the account's own ACR (``registry-vpc.<region>.aliyuncs.com``, found via
  the ``cr`` OpenAPI with the instance RAM role — no per-account address to type).
* Neither reachable → refuse to run and report exactly what to do, instead of
  silently producing a resolved=0 garbage result.

``decide_image_strategy`` and ``merge_registry_mirrors`` are pure and fully
tested; the live probes / metadata / ACR discovery are thin, seam-injected and
marked ``# pragma: no cover``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

_DOCKERHUB_PROBE = "https://registry-1.docker.io/v2/"
_GITHUB_PROBE = "https://github.com/"
_METADATA_REGION = "http://100.100.100.200/latest/meta-data/region-id"


@dataclass
class ImageStrategy:
    """The driver's decided docker-image sourcing plan for this boot."""

    mode: str  # "direct" | "acr-mirror" | "manual" | "blocked"
    registry_mirrors: list[str] = field(default_factory=list)
    region: str = ""
    ok: bool = True  # False → the driver must refuse to run
    notes: str = ""


def decide_image_strategy(
    region: str,
    *,
    dockerhub_ok: bool,
    github_ok: bool,
    acr_endpoint: str | None = None,
    override_mirror: str | None = None,
) -> ImageStrategy:
    """Pick the image strategy from what the driver can actually reach.

    ``override_mirror`` (a power-user escape hatch) always wins. Otherwise a
    reachable Docker Hub means no mirror at all; a blocked Docker Hub falls back
    to the account's ACR endpoint when one was discovered; and a fully blocked
    driver returns ``ok=False`` so the caller refuses to run.
    """
    if override_mirror:
        return ImageStrategy(
            mode="manual",
            registry_mirrors=[override_mirror],
            region=region,
            notes=f"operator-provided registry mirror {override_mirror!r}",
        )

    if dockerhub_ok:
        note = "Docker Hub reachable — pulling directly, no mirror needed."
        if not github_ok:
            note += " WARNING: GitHub unreachable — repo clones inside the harness may fail."
        return ImageStrategy(mode="direct", region=region, notes=note)

    if acr_endpoint:
        note = (
            f"Docker Hub blocked from region {region!r}; routing through the account's "
            f"ACR endpoint {acr_endpoint!r}. Base images must be pre-staged into ACR."
        )
        if not github_ok:
            note += " GitHub is ALSO blocked here — repo sources must be mirrored too."
        return ImageStrategy(
            mode="acr-mirror",
            registry_mirrors=[f"https://{acr_endpoint}"],
            region=region,
            notes=note,
        )

    return ImageStrategy(
        mode="blocked",
        region=region,
        ok=False,
        notes=(
            f"Docker Hub is unreachable from region {region!r} and no ACR endpoint was "
            "discovered, so the SWE-bench harness cannot pull images. Run the driver in a "
            "region with direct Docker Hub egress (e.g. an overseas region), or pre-stage "
            "the base images into your ACR — see the live runbook."
        ),
    )


def merge_registry_mirrors(existing: dict, mirrors: list[str]) -> dict:
    """Return ``existing`` docker daemon config with ``registry-mirrors`` set.

    Preserves every other key the host's ``/etc/docker/daemon.json`` already had;
    an empty ``mirrors`` removes the key (direct-pull mode writes no mirror).
    """
    out = dict(existing)
    if mirrors:
        out["registry-mirrors"] = list(mirrors)
    else:
        out.pop("registry-mirrors", None)
    return out


# --------------------------------------------------------------------------
# Live seams (exercised only on a real driver host).
# --------------------------------------------------------------------------


def _probe(url: str, timeout: float = 5.0) -> bool:  # pragma: no cover - live network
    """True if *url* answers at all within *timeout* (any HTTP status counts —
    Docker Hub's ``/v2/`` returns 401, which still proves reachability)."""
    import urllib.request

    req = urllib.request.Request(url, method="HEAD")
    try:
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True  # a status (401/403/404) means the host is reachable
    except Exception:
        return False


def _metadata_region() -> str:  # pragma: no cover - live metadata
    import urllib.request

    try:
        with urllib.request.urlopen(_METADATA_REGION, timeout=5.0) as resp:
            return resp.read().decode().strip()
    except Exception:
        return ""


def _discover_acr_endpoint(region: str) -> str | None:  # pragma: no cover - live SDK
    """Best-effort ACR VPC endpoint for *region* via the ``cr`` OpenAPI.

    Uses the instance RAM role (default credential chain). Returns None on any
    failure — a missing ACR just means we fall through to the blocked branch.
    """
    try:
        from alibabacloud_cr20181201 import models as cr_models
        from alibabacloud_cr20181201.client import Client
        from alibabacloud_credentials.client import Client as Cred
        from alibabacloud_tea_openapi import models as om

        client = Client(om.Config(credential=Cred(), endpoint=f"cr.{region}.aliyuncs.com"))
        instances = client.list_instance(cr_models.ListInstanceRequest()).body.instances or []
        if not instances:
            return None
        iid = instances[0].instance_id
        ep = client.get_instance_endpoint(
            cr_models.GetInstanceEndpointRequest(instance_id=iid, endpoint_type="internet")
        ).body
        domains = getattr(ep, "domains", None) or []
        return str(domains[0].domain) if domains else None
    except Exception:
        return None


def main() -> int:  # pragma: no cover - live driver bootstrap
    """Cloud-init entrypoint: detect, write ``/etc/docker/daemon.json``, restart docker.

    Env: ``CB_REGION`` (fallback to metadata), ``CB_DOCKER_MIRROR`` (optional
    operator override). Exit code 3 (and no docker start) when the region is
    blocked, so the campaign fails loudly instead of producing empty results.
    """
    import os
    import subprocess
    from pathlib import Path

    region = os.environ.get("CB_REGION") or _metadata_region()
    override = os.environ.get("CB_DOCKER_MIRROR") or None
    dockerhub_ok = _probe(_DOCKERHUB_PROBE)
    github_ok = _probe(_GITHUB_PROBE)
    acr = None if (dockerhub_ok or override) else _discover_acr_endpoint(region)

    strategy = decide_image_strategy(
        region,
        dockerhub_ok=dockerhub_ok,
        github_ok=github_ok,
        acr_endpoint=acr,
        override_mirror=override,
    )
    print(f"[driver-image] mode={strategy.mode} region={region} :: {strategy.notes}")
    if not strategy.ok:
        return 3

    daemon = Path("/etc/docker/daemon.json")
    existing: dict = {}
    if daemon.exists():
        try:
            existing = json.loads(daemon.read_text())
        except Exception:
            existing = {}
    daemon.parent.mkdir(parents=True, exist_ok=True)
    daemon.write_text(json.dumps(merge_registry_mirrors(existing, strategy.registry_mirrors)))
    subprocess.run(["systemctl", "restart", "docker"], check=False)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
