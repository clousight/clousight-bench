"""P1-6: every capability token maps to concrete minimal actions on each cn cloud.

The required permissions for a run are the adapter's mapping of the task's
tokens. If a cloud is missing a token's mapping, a run that needs it can only
warn "unmapped" instead of naming (and, once wired, verifying) the exact minimal
actions -- so preflight cannot fail fast on a missing permission. This guards
that invariant: adding a token or a cloud can never silently drop a mapping.
"""
import pytest

from clousight_bench.domains.agent_runtime import permissions as perm
from clousight_bench.domains.agent_runtime.adapters.cn_clouds import (
    AliyunAgentRunAdapter,
    HuaweiAgentArtsAdapter,
    VolcengineAgentKitAdapter,
)

_ADAPTERS = [AliyunAgentRunAdapter, HuaweiAgentArtsAdapter, VolcengineAgentKitAdapter]


@pytest.mark.parametrize("adapter_cls", _ADAPTERS)
def test_every_token_is_mapped(adapter_cls):
    missing = [t for t in perm.TOKENS if t not in adapter_cls.PERMISSION_MAP]
    assert missing == [], f"{adapter_cls.name} has no mapping for {missing}"


@pytest.mark.parametrize("adapter_cls", _ADAPTERS)
def test_provision_tokens_map_to_concrete_actions(adapter_cls):
    # T0.1 / T0.2 (deploy / teardown) run on every cloud, so provision/deprovision
    # must resolve to real create/delete actions -- not be silently absent.
    for token in (perm.PROVISION, perm.DEPROVISION):
        actions = adapter_cls.PERMISSION_MAP.get(token)
        assert actions, f"{adapter_cls.name} maps {token} to nothing"


def test_aliyun_session_create_is_invoke_runtime():
    # AgentRun has no CreateSession API; a session is a header on InvokeRuntime.
    assert AliyunAgentRunAdapter.PERMISSION_MAP[perm.SESSION_CREATE] == [
        "agentrun:InvokeRuntime"]
