"""CampaignProbeOrchestrator: the per-campaign probe lifecycle shared by every cloud.

A campaign probe provisions an in-region carrier (ECS/EC2), mirrors its
object-store telemetry prefix back to the local results dir, and reaps it. The
start/sync/stop lifecycle and the control-channel wiring (a ``BlobChannel`` over
the cloud blob client) are identical across clouds; only the blob client, the
carrier config and the dev-wheel code-spec resolution differ. Cloud subclasses
(``_AliyunCampaignProbe`` over ECS+OSS, ``_AwsCampaignProbe`` over EC2+S3)
implement those three hooks; everything else lives here once.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from clousight_bench.core.plugin import CampaignProbeHook

if TYPE_CHECKING:
    from clousight_bench.core.blobstore import BlobStore
    from clousight_bench.domains.agent_runtime.probe.blob_channel import BlobChannel


def _truthy(v: object) -> bool:
    """Interpret a target flag that may arrive as a bool or a YAML string."""
    return v is True or str(v).strip().lower() in ("1", "true", "yes", "on")


def _published_code_spec() -> str:
    """The published-package ``code_spec`` pinned to the control plane's own version.

    Pinning avoids control-plane<->probe version skew: the probe installs the SAME
    version that is driving the campaign (protocol/token/prefix contract). Falls
    back to the bare name if the version can't be read (e.g. odd install).
    """
    try:
        from importlib.metadata import version

        return f"clousight-bench[probe]=={version('clousight-bench')}"
    except Exception:  # noqa: BLE001 - metadata absent → bare name still installs
        return "clousight-bench[probe]"


class CampaignProbeOrchestrator(CampaignProbeHook):
    """Shared per-campaign probe lifecycle: carrier + object-store sync (probe-sink §7).

    Constructor factories are injectable so tests run account-free. Subclasses
    supply the cloud blob client (``_default_store``), the carrier
    (``_default_carrier``) and the dev-wheel code-spec resolution
    (``_resolve_code_spec``); the start/sync/stop lifecycle is inherited.
    """

    def __init__(self, carrier_factory: Any = None, store_factory: Any = None) -> None:
        self._carrier_factory = carrier_factory or self._default_carrier
        self._store_factory = store_factory or self._default_store
        self._carrier: Any = None
        self._store: BlobStore | None = None
        self._channel: BlobChannel | None = None  # built during start_campaign_probe
        self._prefix = ""
        self._bucket = ""

    def start_campaign_probe(self, target: dict) -> dict[str, Any]:
        """Provision the probe.

        Returns ``{probe_control_prefix, probe_blob_prefix, probe_token,
        probe_in_vpc}`` for target stamping — no ``probe_url`` key (object-store-
        mediated transport, no HTTP surface required).
        """
        from clousight_bench.domains.agent_runtime.probe.blob_channel import BlobChannel

        run_id = str(target.get("run_id") or "")
        campaign_id = run_id or "adhoc"
        self._bucket = str(target.get("blob_bucket") or "")
        self._prefix = f"clousight-bench/telemetry/{campaign_id}/"
        self._store = self._store_factory(target)
        # Build the control channel — readiness is polled via the object store, not HTTP.
        channel = BlobChannel(self._store, campaign_id)
        self._channel = channel
        # Clear any residue from a prior run on this (possibly reused) campaign
        # prefix — a stale `stop` sentinel would make the fresh probe exit at once.
        channel.reset()
        self._carrier = self._carrier_factory(target, self._prefix, campaign_id, self._bucket)
        # Inject the readiness check so provision() polls the object store (not IAM/RAM).
        self._carrier.ready_check = channel.is_ready
        self._carrier.provision()  # raises CarrierError on failure
        return {
            "probe_control_prefix": campaign_id,
            "probe_blob_prefix": self._prefix,
            "probe_token": getattr(self._carrier, "token", "") or "",
            "probe_in_vpc": True,
        }

    def sync_probe_artifacts(self, results_dir: Any) -> None:
        """Mirror the probe's object-store prefix into results_dir (channel ②)."""
        if self._store is None:
            return
        from clousight_bench.domains.agent_runtime.probe.blob_sync import sync_prefix

        sync_prefix(self._store, self._prefix, results_dir)

    def stop_campaign_probe(self) -> None:
        """Reap the probe. Idempotent + best-effort (called from a finally).

        Sends the object-store stop sentinel BEFORE tearing down the carrier so
        the in-region loop gets a chance to drain gracefully.
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

    # ---- cloud-specific hooks (subclasses override) --------------------------

    @staticmethod
    def _default_store(target: dict) -> Any:
        raise NotImplementedError

    @staticmethod
    def _default_carrier(target: dict, prefix: str, campaign_id: str = "", bucket: str = "") -> Any:
        raise NotImplementedError

    @staticmethod
    def _resolve_code_spec(target: dict, bucket: str, region: str, campaign_id: str) -> tuple[str, list[str]]:
        raise NotImplementedError
