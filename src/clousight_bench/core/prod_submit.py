"""Local (laptop) thin-client logic for the ecs prod profile.

Pure functions behind the `submit` / `status` / `logs` / `fetch` / `teardown`
CLI commands. All cloud side effects (OSS, terraform, runtime delete) are
injected seams so these are testable with no cloud.
"""

from __future__ import annotations

import json
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from clousight_bench.core.campaign_spec import LaunchSpec
from clousight_bench.core.resource_ledger import LEDGER_FILE, ResourceLedger
from clousight_bench.domains.agent_runtime.controller_reaper import live_runtimes_from_ledger
from clousight_bench.domains.agent_runtime.probe.campaign_channel import CampaignChannel

# Controller heartbeat cadence; status flags "stale" past 2x this.
HEARTBEAT_INTERVAL_S = 15.0

Terraform = Callable[[list[str]], int]

# Only the prod-profile resources — never a bare apply/destroy (which would
# in-place touch the mock FC + everything else in the module). Aliyun-specific.
_CONTROLLER_TF_TARGETS = [
    "alicloud_instance.controller",
    "alicloud_ram_role.controller",
    "alicloud_ram_policy.controller",
    "alicloud_ram_role_policy_attachment.controller",
    "alicloud_nat_gateway.bench",
    "alicloud_eip_address.nat",
    "alicloud_eip_association.nat",
    "alicloud_snat_entry.bench",
]


def _tf_targets() -> list[str]:
    out: list[str] = []
    for t in _CONTROLLER_TF_TARGETS:
        out += ["-target", t]
    return out


def _load_tasks(plan_path: str | Path) -> list[str]:
    doc = yaml.safe_load(Path(plan_path).read_text(encoding="utf-8")) or {}
    return [str(t["task"]) for t in (doc.get("tasks") or [])]


def _load_config(config_path: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    doc = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    return dict(doc.get("target") or {}), dict(doc.get("params") or {})


def submit(
    plan_path: str | Path,
    config_path: str | Path,
    channel_factory: Callable[[str], CampaignChannel],
    terraform: Terraform,
    *,
    watchdog_timeout_s: float,
    wheel_builder: Callable[[str], tuple[str, list[str]]] | None = None,
    gen_id: Callable[[], str] = lambda: "camp-" + uuid.uuid4().hex[:8],
) -> str:
    """Write the launch spec to OSS, then terraform-apply the controller + NAT.

    ``wheel_builder(campaign_id) -> (presigned_wheel_url, extra_deps)`` builds +
    uploads the private clousight-bench dev wheel and returns the URL the
    controller's cloud-init installs (the package is not on the public mirror).
    """
    campaign_id = gen_id()
    tasks = _load_tasks(plan_path)
    target, params = _load_config(config_path)
    spec = LaunchSpec(
        campaign_id=campaign_id,
        tasks=tasks,
        params=params,
        target=target,
        watchdog_timeout_s=watchdog_timeout_s,
    )
    channel_factory(campaign_id).write_launch(spec)
    tf_args = [
        "apply",
        "-auto-approve",
        *_tf_targets(),
        "-var",
        "enable_controller=true",
        "-var",
        "enable_nat=true",
        "-var",
        f"campaign_id={campaign_id}",
    ]
    if wheel_builder is not None:
        wheel_url, extra_deps = wheel_builder(campaign_id)
        tf_args += [
            "-var",
            f"controller_wheel_url={wheel_url}",
            "-var",
            "controller_extra_deps=" + json.dumps(extra_deps),
        ]
    terraform(tf_args)
    return campaign_id


def status(channel: CampaignChannel, *, now: Callable[[], float] = time.time) -> dict[str, Any]:
    manifest = channel.read_manifest()
    hb = channel.read_heartbeat()
    hb_age = (now() - hb["ts"]) if hb else None
    return {
        "counts": manifest.counts() if manifest else {},
        "current_task": hb.get("current_task") if hb else None,
        "heartbeat_age_s": hb_age,
        "stale": (hb_age is not None and hb_age > 2 * HEARTBEAT_INTERVAL_S),
        "done": channel.is_done(),
    }


def logs(channel: CampaignChannel) -> list[str]:
    return channel.read_logs()


def fetch(channel: CampaignChannel, dest_dir: str | Path) -> list[Path]:
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for task_id in channel.list_results():
        j, p = channel.read_result(task_id)
        jpath = dest / f"{task_id}.json"
        jpath.write_bytes(j)
        written.append(jpath)
        if p is not None:
            ppath = dest / f"{task_id}.series.parquet"
            ppath.write_bytes(p)
            written.append(ppath)
    return written


def teardown(
    channel: CampaignChannel,
    terraform: Terraform,
    delete_runtime: Callable[[str], None],
) -> dict[str, Any]:
    """Backstop cleanup: stop the controller, reap residual runtimes from the
    OSS-synced ledger (independent of a live controller), then terraform destroy."""
    channel.signal_stop()
    residual: list[str] = []
    raw = channel.read_ledger()
    if raw:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / LEDGER_FILE).write_bytes(raw)
            for rid in live_runtimes_from_ledger(ResourceLedger(td)):
                try:
                    delete_runtime(rid)
                    residual.append(rid)
                except Exception:  # noqa: BLE001 — best-effort
                    pass
    rc = terraform(
        [
            "destroy",
            "-auto-approve",
            *_tf_targets(),
            "-var",
            "enable_controller=false",
            "-var",
            "enable_nat=false",
        ]
    )
    return {"destroyed": rc == 0, "residual_deleted": residual}
