"""Local (laptop) thin-client logic for the ecs prod profile.

Pure functions behind the `submit` / `status` / `logs` / `fetch` / `teardown`
CLI commands. All cloud side effects (OSS, terraform, runtime delete) are
injected seams so these are testable with no cloud.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from clousight_bench.core.campaign_channel import CampaignChannel
from clousight_bench.core.campaign_spec import LaunchSpec
from clousight_bench.core.credentials import infer_provider
from clousight_bench.core.errors import UserInputError
from clousight_bench.core.plugin import ControllerTfSpec
from clousight_bench.core.registry import get_runtime_provider
from clousight_bench.core.resource_ledger import LEDGER_FILE, ResourceLedger, live_runtimes_from_ledger

# Controller heartbeat cadence; status flags "stale" past 2x this.
HEARTBEAT_INTERVAL_S = 15.0

Terraform = Callable[[list[str]], int]


def _controller_tf_spec(provider: str | None) -> ControllerTfSpec:
    """The prod-controller terraform surface for ``provider`` — loud when absent.

    Vendor knowledge (resource addresses, driver-var names) lives on the
    provider plugin, not in core: a submit against a provider without a wired
    prod-controller profile must fail HERE, before any launch bytes or
    terraform state exist — never fall back to another cloud's resources.
    """
    plugin = get_runtime_provider(provider)
    spec = plugin.controller_tf_spec() if plugin is not None else None
    if spec is None:
        raise UserInputError(
            f"provider {provider!r} has no prod-controller profile; only providers "
            "implementing controller_tf_spec() support `csbench submit` — set "
            "target.provider in the config (or platform: in the plan) to a provider "
            "with a wired prod-controller path"
        )
    return spec


def _tf_targets(spec: ControllerTfSpec) -> list[str]:
    out: list[str] = []
    for t in spec.tf_targets:
        out += ["-target", t]
    return out


def _require_task_id(entry: dict[str, Any]) -> str:
    """A submit-plan task entry must carry ``task_id`` — fail with context, not KeyError."""
    if "task_id" not in entry:
        hint = ""
        if "task" in entry:
            hint = " (found 'task' — that is the csbench run-plan shape, not the submit shape)"
        raise ValueError(f"submit plan task entries need 'task_id'{hint}: {entry!r}")
    return str(entry["task_id"])


def _load_plan(
    plan_path: str | Path,
) -> tuple[list[dict[str, Any]], dict[str, Any], float | None, str | None]:
    """Parse the plan yaml: task entries (``{task_id, params}`` mappings), the
    optional ``driver:`` section, the optional campaign ``cost_budget``, and the
    optional ``platform`` (used to infer the provider when the config target
    carries no explicit ``provider`` key)."""
    doc = yaml.safe_load(Path(plan_path).read_text(encoding="utf-8")) or {}
    tasks = [
        {"task_id": _require_task_id(t), "params": dict(t.get("params") or {})}
        for t in (doc.get("tasks") or [])
    ]
    driver = dict(doc.get("driver") or {})
    budget = doc.get("cost_budget")
    platform = doc.get("platform")
    return (
        tasks,
        driver,
        (float(budget) if budget is not None else None),
        (str(platform) if platform else None),
    )


def _driver_tf_args(driver: dict[str, Any], spec: ControllerTfSpec) -> list[str]:
    """``-var <driver_tf_var>=...`` flags for the driver keys present in the plan."""
    out: list[str] = []
    unknown = set(driver) - set(spec.driver_tf_vars)
    if unknown:
        raise ValueError(
            f"unknown driver key(s) {sorted(unknown)!r}; known: {sorted(spec.driver_tf_vars)}"
        )
    for key, var in spec.driver_tf_vars.items():
        if key not in driver:
            continue
        value = driver[key]
        text = str(value).lower() if isinstance(value, bool) else str(value)
        out += ["-var", f"{var}={text}"]
    return out


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
    wheel_builder: Callable[[str, bool], tuple[str, list[str]]] | None = None,
    gen_id: Callable[[], str] = lambda: "camp-" + uuid.uuid4().hex[:8],
) -> str:
    """Write the launch spec to OSS, then terraform-apply the controller + NAT.

    ``wheel_builder(campaign_id, needs_swebench) -> (presigned_wheel_url,
    extra_deps)`` builds + uploads the private clousight-bench dev wheel and
    returns the URL the controller's cloud-init installs (the package is not on
    the public mirror). ``needs_swebench`` is True when the plan contains any
    ``suite:`` task, so the driver host gets the ``[swebench]`` harness extra.

    Plan yaml: ``tasks`` entries are ``{task_id, params}`` mappings; a top-level
    ``cost_budget`` becomes ``LaunchSpec.cost_budget``; a top-level ``driver:``
    section (install_docker / system_disk_size / docker_registry_mirror /
    hf_endpoint / instance_type) is forwarded as ``-var controller_*`` flags so
    the controller instance can double as the suite driver host.

    The controller's terraform surface (targets + driver-var names) comes from
    the resolved provider's ``ControllerTfSpec`` — ``target.provider`` in the
    config, or inferred from the plan's ``platform:`` (e.g. ``aliyun-agentrun``
    → ``aliyun``). A provider without one fails loudly before any side effect.
    """
    campaign_id = gen_id()
    tasks, driver, cost_budget, platform = _load_plan(plan_path)
    target, params = _load_config(config_path)
    tf_spec = _controller_tf_spec(infer_provider(target, platform))
    has_suite_task = any(str(t["task_id"]).startswith("suite:") for t in tasks)
    # Silent-mock trap: a suite task on a non-real target runs canned fixtures on
    # full-price infra. Loud warning, not an error — mock submits are legitimate
    # for pipeline tests.
    if has_suite_task and str(target.get("mode", "")) != "real":
        print(
            f"warning: suite task(s) submitted with target.mode={target.get('mode', '')!r} — "
            "the suite will run MOCK artifacts; set target.mode: real for a live SUT run",
            file=sys.stderr,
        )
    spec = LaunchSpec(
        campaign_id=campaign_id,
        tasks=tasks,
        params=params,
        target=target,
        watchdog_timeout_s=watchdog_timeout_s,
        cost_budget=cost_budget,
    )
    channel_factory(campaign_id).write_launch(spec)
    tf_args = [
        "apply",
        "-auto-approve",
        *_tf_targets(tf_spec),
        "-var",
        "enable_controller=true",
        "-var",
        "enable_nat=true",
        "-var",
        f"campaign_id={campaign_id}",
        *_driver_tf_args(driver, tf_spec),
    ]
    if wheel_builder is not None:
        wheel_url, extra_deps = wheel_builder(campaign_id, has_suite_task)
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
    for name in channel.list_results():
        # name is <task_id>--<run_id> — unique per execution, so repeated tasks
        # in one campaign land as distinct local files instead of overwriting.
        j, p = channel.read_result(name)
        jpath = dest / f"{name}.json"
        jpath.write_bytes(j)
        written.append(jpath)
        if p is not None:
            ppath = dest / f"{name}.series.parquet"
            ppath.write_bytes(p)
            written.append(ppath)
    return written


def teardown(
    channel: CampaignChannel,
    terraform: Terraform,
    delete_runtime: Callable[[str], None],
    *,
    provider: str | None,
) -> dict[str, Any]:
    """Backstop cleanup: stop the controller, reap residual runtimes from the
    OSS-synced ledger (independent of a live controller), then terraform destroy.

    ``provider`` (the config's ``target.provider``) resolves the controller's
    terraform surface; an unsupported provider fails loudly before the stop
    signal or any destroy."""
    tf_spec = _controller_tf_spec(provider)
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
            *_tf_targets(tf_spec),
            "-var",
            "enable_controller=false",
            "-var",
            "enable_nat=false",
        ]
    )
    return {"destroyed": rc == 0, "residual_deleted": residual}
