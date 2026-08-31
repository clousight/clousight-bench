"""Prod-campaign commands: submit / status / logs / fetch / teardown."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

import argparse
from pathlib import Path

from clousight_bench.cli._common import _check_target

_PROD_TF_DIR = "infra/terraform/aliyun-iam"


def _prod_target(config_path: str | None) -> dict:

    import yaml as _yaml

    doc = _yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) if config_path else {}
    return _check_target(dict((doc or {}).get("target") or {}))


def _prod_oss(target: dict):
    from clousight_bench.domains.agent_runtime.probe.oss_client import Oss2Client

    return Oss2Client(str(target.get("blob_bucket") or ""), str(target.get("region") or "cn-hangzhou"))


def _prod_channel(target: dict, campaign_id: str):
    from clousight_bench.core.campaign.channel import CampaignChannel

    return CampaignChannel(_prod_oss(target), campaign_id)


def _terraform_runner():
    import subprocess

    return lambda argv: subprocess.call(["terraform", *argv], cwd=_PROD_TF_DIR)


def _prod_runtime_deleter(target: dict):
    def _del(runtime_id: str) -> None:
        from clousight_bench.domains.agent_runtime.adapters.cn_clouds import AliyunAgentRunAdapter

        AliyunAgentRunAdapter(target).transport().deprovision(runtime_id)

    return _del


def _controller_extra_deps(needs_swebench: bool) -> list[str]:
    """Controller pip deps: the full orchestrator needs probe + aliyun SDKs + store.

    ``needs_swebench=True`` (any ``suite:`` task in the plan) additionally pulls
    the ``[swebench]`` harness extra so the driver host can actually evaluate —
    without it the suite fails at run() with "swebench extra not installed".
    """
    from clousight_bench.domains.agent_runtime.dev_wheel import deps_for_extras

    extras = ["probe", "aliyun", "store"] + (["swebench"] if needs_swebench else [])
    fallback = ["requests>=2.28", "oss2>=2.18", "duckdb>=1.0", "pyarrow>=16"]
    if needs_swebench:
        fallback.append("swebench>=3.0")
    return deps_for_extras(extras) or fallback


def _prod_wheel_builder(target: dict):
    """Build+upload the private dev wheel; return (campaign_id, needs_swebench) -> (url, deps)."""

    def _build(campaign_id: str, needs_swebench: bool = False) -> tuple[str, list[str]]:
        from clousight_bench.domains.agent_runtime.dev_wheel import upload_dev_wheel
        from clousight_bench.domains.agent_runtime.probe.oss_client import Oss2Client

        bucket = str(target.get("blob_bucket") or "")
        region = str(target.get("region") or "cn-hangzhou")
        upload = Oss2Client(bucket, region)  # public endpoint for the PUT
        sign = Oss2Client(bucket, region, internal=True)  # internal endpoint for the presign
        url = upload_dev_wheel(upload, sign, campaign_id, expires=7200)
        return url, _controller_extra_deps(needs_swebench)

    return _build


def _cmd_submit(args: argparse.Namespace) -> int:
    from clousight_bench.core.campaign import prod_submit
    from clousight_bench.core.campaign.channel import CampaignChannel

    target = _prod_target(args.config)
    oss = _prod_oss(target)
    cid = prod_submit.submit(
        args.plan_file,
        args.config,
        lambda c: CampaignChannel(oss, c),
        _terraform_runner(),
        watchdog_timeout_s=args.watchdog_timeout,
        wheel_builder=_prod_wheel_builder(target),
    )
    print(cid)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    import json as _json

    from clousight_bench.core.campaign import prod_submit

    st = prod_submit.status(_prod_channel(_prod_target(args.config), args.campaign_id))
    print(_json.dumps(st, ensure_ascii=False))
    return 0


def _cmd_logs(args: argparse.Namespace) -> int:
    from clousight_bench.core.campaign import prod_submit

    for line in prod_submit.logs(_prod_channel(_prod_target(args.config), args.campaign_id)):
        print(line)
    return 0


def _cmd_fetch(args: argparse.Namespace) -> int:
    from clousight_bench.core.campaign import prod_submit

    paths = prod_submit.fetch(_prod_channel(_prod_target(args.config), args.campaign_id), args.dest)
    print(f"fetched {len(paths)} file(s) to {args.dest}")
    return 0


def _cmd_teardown(args: argparse.Namespace) -> int:
    from clousight_bench.core.campaign import prod_submit
    from clousight_bench.core.credentials import infer_provider

    target = _prod_target(args.config)
    out = prod_submit.teardown(
        _prod_channel(target, args.campaign_id),
        _terraform_runner(),
        _prod_runtime_deleter(target),
        # resolves the provider's controller terraform surface (ControllerTfSpec)
        provider=infer_provider(target),
    )
    print(out)
    return 0
