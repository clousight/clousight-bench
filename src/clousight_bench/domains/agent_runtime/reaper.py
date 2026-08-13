"""AliyunResourceReaper: reap ECS probe carriers + AgentRun runtimes for csbench sweep.

The list/delete cloud calls are behind injectable seams so the reaper is
unit-tested account-free; the real path injects ECS/AgentRun SDK-backed
functions. Each seam yields dicts: {"kind","id","run_id","created_ts","tags"}.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from clousight_bench.core.plugin import ResourceReaper
from clousight_bench.core.resource_tags import TAG_MANAGED, TAG_RUN_ID

# Probe carrier ECS instances are named cb-probe-<run-id-suffix> by EcsProbeCarrier;
# this prefix is how the reaper recognises a managed carrier (instances are not
# tag-stamped, same as the AgentRun name-prefix convention).
_PROBE_NAME_PREFIX = "cb-probe"


class AliyunResourceReaper(ResourceReaper):
    """Reap ECS probe carriers + AgentRun runtimes for the clousight-bench project.

    The list/delete cloud calls are behind injectable seams so the reaper is
    unit-tested account-free. Each seam yields dicts:
    {"kind","id","run_id","created_ts","tags"}."""

    provider = "aliyun"

    def __init__(
        self,
        list_fns: list[Callable[[], list[dict[str, Any]]]] | None = None,
        delete_fn: Callable[[str, str], None] | None = None,  # (kind, id) -> None
        now: Callable[[], float] = time.time,
        *,
        region: str = "cn-hangzhou",
        ecs_client: Any | None = None,
        agentrun_client: Any | None = None,
    ) -> None:
        self._region = region
        self._ecs_client = ecs_client
        self._agentrun_client = agentrun_client
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

    def _ecs(self) -> Any:
        if self._ecs_client is None:
            from alibabacloud_credentials.client import Client as CredClient
            from alibabacloud_ecs20140526.client import Client as EcsClient
            from alibabacloud_tea_openapi import models as open_api_models

            cfg = open_api_models.Config(credential=CredClient())
            cfg.endpoint = f"ecs.{self._region}.aliyuncs.com"
            self._ecs_client = EcsClient(cfg)
        return self._ecs_client

    def _agentrun(self) -> Any:
        if self._agentrun_client is None:
            from alibabacloud_agentrun20250910.client import Client as AgentRunClient
            from alibabacloud_credentials.client import Client as CredClient
            from alibabacloud_tea_openapi import models as open_api_models

            self._agentrun_client = AgentRunClient(open_api_models.Config(credential=CredClient()))
        return self._agentrun_client

    def _default_list_fns(self) -> list[Callable[[], list[dict[str, Any]]]]:
        return [self._list_ecs, self._list_agentrun]

    @staticmethod
    def _ecs_models() -> Any:
        """Return alibabacloud_ecs20140526.models, or a None-proxy for test path."""
        try:
            from alibabacloud_ecs20140526 import models as m

            return m
        except ImportError:

            class _NoneProxy:
                def __getattr__(self, _: str) -> None:
                    return None

            return _NoneProxy()

    @staticmethod
    def _ar_models() -> Any:
        """Return alibabacloud_agentrun20250910.models, or a None-proxy for test path."""
        try:
            from alibabacloud_agentrun20250910 import models as m

            return m
        except ImportError:

            class _NoneProxy:
                def __getattr__(self, _: str) -> None:
                    return None

            return _NoneProxy()

    @staticmethod
    def _make(model_cls: Any, **kwargs: Any) -> Any:
        """Construct a model (real class) or SimpleNamespace fallback (test path)."""
        import types as _types

        if model_cls is None:
            return _types.SimpleNamespace(**kwargs)
        return model_cls(**kwargs)

    def _list_ecs(self) -> list[dict[str, Any]]:
        import datetime as _dt

        m = self._ecs_models()
        resp = self._ecs().describe_instances(self._make(m.DescribeInstancesRequest, region_id=self._region))
        instances = getattr(getattr(resp.body, "instances", None), "instance", None) or []
        out: list[dict[str, Any]] = []
        for inst in instances:
            name = str(getattr(inst, "instance_name", "") or "")
            if not name.startswith(_PROBE_NAME_PREFIX):
                continue  # name-prefix managed detection (instances aren't tag-stamped)
            created = getattr(inst, "creation_time", "") or ""
            try:
                ts = _dt.datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
            except ValueError:
                ts = 0.0
            out.append(
                {
                    "kind": "ecs",
                    "id": str(getattr(inst, "instance_id", "")),
                    "created_ts": ts,
                    "tags": {TAG_MANAGED: "true", TAG_RUN_ID: name},
                }
            )
        return out

    def _list_agentrun(self) -> list[dict[str, Any]]:
        m = self._ar_models()
        resp = self._agentrun().list_agent_runtimes(self._make(m.ListAgentRuntimesRequest))
        data = getattr(getattr(resp, "body", None), "data", None)
        items = getattr(data, "items", None) or []
        out: list[dict[str, Any]] = []
        for it in items:
            name = str(getattr(it, "agent_runtime_name", "") or "")
            if not name.startswith("clousight-bench"):
                continue  # name-prefix managed detection (runtimes aren't tag-stamped)
            out.append(
                {
                    "kind": "agentrun",
                    "id": str(getattr(it, "agent_runtime_id", "")),
                    "created_ts": 0.0,
                    "tags": {TAG_MANAGED: "true", TAG_RUN_ID: name},
                }
            )
        return out

    def _default_delete(self, kind: str, resource_id: str) -> None:
        if kind == "ecs":
            m = self._ecs_models()
            self._ecs().delete_instances(
                self._make(
                    m.DeleteInstancesRequest,
                    region_id=self._region,
                    instance_id=[resource_id],
                    force=True,
                )
            )
        elif kind == "agentrun":
            self._agentrun().delete_agent_runtime(resource_id)
        else:
            raise ValueError(f"unknown reap kind: {kind!r}")
