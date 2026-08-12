import pytest

from clousight_bench.domains.agent_runtime.probe.oss_client import (
    EcsRamRoleOssClient,
    InMemoryOssClient,
    Oss2Client,
    OssClient,
)


def test_in_memory_put_get_roundtrip():
    c = InMemoryOssClient()
    c.put_object("p/a.jsonl", b"hello")
    assert c.get_object("p/a.jsonl") == b"hello"


def test_in_memory_list_prefix_is_sorted_and_scoped():
    c = InMemoryOssClient()
    for k in ("p/b", "p/a", "q/c"):
        c.put_object(k, b"x")
    assert c.list_prefix("p/") == ["p/a", "p/b"]
    assert c.list_prefix("q/") == ["q/c"]


def test_in_memory_delete_and_missing_get():
    c = InMemoryOssClient()
    c.put_object("k", b"v")
    c.delete_object("k")
    assert c.list_prefix("") == []
    with pytest.raises(KeyError):
        c.get_object("k")


def test_in_memory_is_an_ossclient():
    assert isinstance(InMemoryOssClient(), OssClient)


def test_oss2_client_constructs_without_sdk_or_network():
    # Constructing must not import oss2 or require credentials; the lazy import
    # happens only when a method is called.
    client = Oss2Client(bucket="b", region="cn-hangzhou")
    assert client is not None
    assert isinstance(client, OssClient)


# ---------------------------------------------------------------------------
# Oss2Client internal endpoint kwarg
# ---------------------------------------------------------------------------


def test_oss2_client_internal_true_uses_internal_endpoint():
    client = Oss2Client(bucket="b", region="cn-hangzhou", internal=True)
    assert client._endpoint == "https://oss-cn-hangzhou-internal.aliyuncs.com"


def test_oss2_client_internal_false_uses_public_endpoint():
    client = Oss2Client(bucket="b", region="cn-hangzhou", internal=False)
    assert client._endpoint == "https://oss-cn-hangzhou.aliyuncs.com"


def test_oss2_client_default_is_public_endpoint():
    client = Oss2Client(bucket="b", region="cn-hangzhou")
    assert client._endpoint == "https://oss-cn-hangzhou.aliyuncs.com"


def test_oss2_client_explicit_endpoint_wins_over_internal():
    client = Oss2Client(
        bucket="b", region="cn-hangzhou", endpoint="https://custom.example.com", internal=True
    )
    assert client._endpoint == "https://custom.example.com"


def test_oss2_client_explicit_endpoint_wins_over_public():
    client = Oss2Client(bucket="b", region="cn-hangzhou", endpoint="https://custom.example.com")
    assert client._endpoint == "https://custom.example.com"


# ---------------------------------------------------------------------------
# EcsRamRoleOssClient
# ---------------------------------------------------------------------------


def test_ecs_ram_role_oss_client_uses_internal_endpoint():
    client = EcsRamRoleOssClient(bucket="b", region="cn-hangzhou")
    assert client._endpoint == "https://oss-cn-hangzhou-internal.aliyuncs.com"


def test_ecs_ram_role_oss_client_is_an_ossclient():
    client = EcsRamRoleOssClient(bucket="b", region="cn-hangzhou")
    assert isinstance(client, OssClient)


def test_ecs_ram_role_oss_client_constructs_without_sdk_or_network():
    # Construction must not import oss2 or contact any network.
    client = EcsRamRoleOssClient(bucket="b", region="cn-hangzhou")
    assert client is not None
