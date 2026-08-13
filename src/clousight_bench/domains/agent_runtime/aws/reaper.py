"""AwsResourceReaper: reap EC2 probe carriers + Bedrock AgentCore runtimes for csbench sweep.

The list/delete cloud calls are behind injectable seams so the reaper is
unit-tested account-free; the real path injects boto3-backed clients.
Each seam yields dicts: {"kind","id","created_ts","tags"}.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from clousight_bench.core.plugin import ResourceReaper
from clousight_bench.core.resource_tags import TAG_MANAGED, TAG_RUN_ID

# Probe carrier EC2 instances are named cb-probe-<run-id-suffix> by the EC2 carrier;
# this prefix is how the reaper recognises a managed carrier (instances are not
# tag-stamped on the EC2 side, same as the AgentCore name-prefix convention).
_PROBE_NAME_PREFIX = "cb-probe"


class AwsResourceReaper(ResourceReaper):
    """Reap EC2 probe carriers + Bedrock AgentCore runtimes for the clousight-bench project.

    The list/delete cloud calls are behind injectable seams so the reaper is
    unit-tested account-free. Each seam yields dicts:
    {"kind","id","created_ts","tags"}."""

    provider = "aws"

    def __init__(
        self,
        list_fns: list[Callable[[], list[dict[str, Any]]]] | None = None,
        delete_fn: Callable[[str, str], None] | None = None,  # (kind, id) -> None
        now: Callable[[], float] = time.time,
        *,
        region: str = "us-east-1",
        ec2_client: Any | None = None,
        agentcore_client: Any | None = None,
    ) -> None:
        self._region = region
        self._ec2_client = ec2_client
        self._agentcore_client = agentcore_client
        self._list_fns = list_fns if list_fns is not None else self._default_list_fns()
        self._delete_fn = delete_fn if delete_fn is not None else self._default_delete
        self._now = now

    def sweep(self, *, dry_run: bool, older_than_s: float | None = None) -> list[dict[str, Any]]:
        acted: list[dict[str, Any]] = []
        for list_fn in self._list_fns:
            for res in list_fn():
                if res.get("tags", {}).get(TAG_MANAGED) != "true":
                    continue
                if older_than_s is not None:
                    created_ts = float(res.get("created_ts") or 0.0)
                    # Fail safe: a resource whose creation time is unknown (0.0)
                    # must NOT be age-reaped — it may be in-flight. Only reap it
                    # in an untimed sweep (older_than_s is None), never by age.
                    if created_ts <= 0.0:
                        continue
                    if self._now() - created_ts < older_than_s:
                        continue
                if not dry_run:
                    self._delete_fn(res["kind"], res["id"])
                acted.append(
                    {
                        "kind": res["kind"],
                        "id": res["id"],
                        "run_id": res.get("tags", {}).get(TAG_RUN_ID, "?"),
                    }
                )
        return acted

    def _ec2(self) -> Any:
        if self._ec2_client is None:
            import boto3  # local import — not a hard dependency in open-core

            self._ec2_client = boto3.client("ec2", region_name=self._region)
        return self._ec2_client

    def _agentcore(self) -> Any:
        if self._agentcore_client is None:
            import boto3  # local import — not a hard dependency in open-core

            self._agentcore_client = boto3.client("bedrock-agentcore-control", region_name=self._region)
        return self._agentcore_client

    def _default_list_fns(self) -> list[Callable[[], list[dict[str, Any]]]]:
        return [self._list_ec2, self._list_agentcore]

    def _list_ec2(self) -> list[dict[str, Any]]:
        resp = self._ec2().describe_instances(
            Filters=[{"Name": "instance-state-name", "Values": ["running", "pending"]}]
        )
        out: list[dict[str, Any]] = []
        for reservation in resp.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                state = inst.get("State", {}).get("Name", "")
                if state in ("terminated", "shutting-down"):
                    continue  # defensive double-check
                # Name is stored as a tag with key "Name"
                name = ""
                for tag in inst.get("Tags", []):
                    if tag.get("Key") == "Name":
                        name = tag.get("Value", "")
                        break
                if not name.startswith(_PROBE_NAME_PREFIX):
                    continue  # name-prefix managed detection (instances aren't tag-stamped)
                launch_time = inst.get("LaunchTime")
                try:
                    ts = float(launch_time.timestamp()) if launch_time is not None else 0.0
                except (AttributeError, TypeError, ValueError):
                    ts = 0.0
                out.append(
                    {
                        "kind": "ec2",
                        "id": str(inst.get("InstanceId", "")),
                        "created_ts": ts,
                        "tags": {TAG_MANAGED: "true", TAG_RUN_ID: name},
                    }
                )
        return out

    def _list_agentcore(self) -> list[dict[str, Any]]:
        resp = self._agentcore().list_agent_runtimes()
        items = resp.get("agentRuntimes", [])
        out: list[dict[str, Any]] = []
        for it in items:
            name = str(it.get("agentRuntimeName", "") or "")
            if not name.startswith("clousight-bench"):
                continue  # name-prefix managed detection (runtimes aren't tag-stamped)
            out.append(
                {
                    "kind": "agentcore",
                    "id": str(it.get("agentRuntimeId", "")),
                    "created_ts": 0.0,  # API doesn't expose creation time
                    "tags": {TAG_MANAGED: "true", TAG_RUN_ID: name},
                }
            )
        return out

    def _default_delete(self, kind: str, resource_id: str) -> None:
        if kind == "ec2":
            self._ec2().terminate_instances(InstanceIds=[resource_id])
        elif kind == "agentcore":
            self._agentcore().delete_agent_runtime(agentRuntimeId=resource_id)
        else:
            raise ValueError(f"unknown reap kind: {kind!r}")
