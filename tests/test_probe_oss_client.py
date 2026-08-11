import pytest

from clousight_bench.domains.agent_runtime.probe.oss_client import InMemoryOssClient, Oss2Client, OssClient


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
