# tests/test_eci20180808_sdk.py
import json
import types

from clousight_bench.domains.agent_runtime.eci_carrier import Eci20180808Sdk


class _FakeEciClient:
    """Stands in for alibabacloud_eci20180808.Client; records requests."""
    def __init__(self):
        self.create_req = None
        self.describe_req = None
        self.delete_req = None

    def create_container_group(self, req):
        self.create_req = req
        return types.SimpleNamespace(
            body=types.SimpleNamespace(container_group_id="eci-abc"))

    def describe_container_groups(self, req):
        self.describe_req = req
        cg = types.SimpleNamespace(status="Running", internet_ip="1.2.3.4")
        return types.SimpleNamespace(
            body=types.SimpleNamespace(container_groups=[cg]))

    def delete_container_group(self, req):
        self.delete_req = req
        return types.SimpleNamespace(body=types.SimpleNamespace(request_id="r1"))


def _carrier_req():
    # the exact dict shape EciProbeCarrier._build_create_request emits
    return {
        "region_id": "cn-hangzhou",
        "container_group_name": "cb-probe-run-xy",
        "cpu": 2.0, "memory": 4.0,
        "v_switch_id": "vsw-1", "security_group_id": "sg-1",
        "ram_role_name": "clousight-bench-eci-probe",
        "restart_policy": "Never",
        "tags": [{"key": "clousight-bench:managed", "value": "true"},
                 {"key": "clousight-bench:run-id", "value": "run-xy"}],
        "container": [{
            "name": "cb-probe",
            "image": "registry.cn-hangzhou.aliyuncs.com/library/python:3.12",
            "port": [{"port": 9000, "protocol": "TCP"}],
            "command": ["/bin/sh", "-c", "echo boot"],
            "environment_var": [{"key": "PORT", "value": "9000"}],
        }],
    }


def test_create_maps_dict_onto_request_and_returns_id():
    fake = _FakeEciClient()
    sdk = Eci20180808Sdk(region="cn-hangzhou", client=fake)
    iid = sdk.create_container_group(_carrier_req())
    assert iid == "eci-abc"
    r = fake.create_req
    # scalar fields mapped onto the ECI request model
    assert r.region_id == "cn-hangzhou"
    assert r.container_group_name == "cb-probe-run-xy"
    assert float(r.cpu) == 2.0 and float(r.memory) == 4.0
    assert r.v_switch_id == "vsw-1" and r.security_group_id == "sg-1"
    assert r.ram_role_name == "clousight-bench-eci-probe"
    assert r.restart_policy == "Never"
    # tags mapped to request tag objects
    tag_kv = {t.key: t.value for t in r.tag}
    assert tag_kv["clousight-bench:managed"] == "true"
    assert tag_kv["clousight-bench:run-id"] == "run-xy"
    # container mapped: image, port, command, env
    c = r.container[0]
    assert "python:3.12" in c.image
    assert c.port[0].port == 9000
    assert c.command == ["/bin/sh", "-c", "echo boot"]
    env_kv = {e.key: e.value for e in c.environment_var}
    assert env_kv["PORT"] == "9000"


def test_describe_uses_ids_filter_and_normalizes_status_and_ip():
    fake = _FakeEciClient()
    sdk = Eci20180808Sdk(region="cn-hangzhou", client=fake)
    desc = sdk.describe_container_group("eci-abc")
    # describe_container_groups filtered by the instance id (json array string)
    assert json.loads(fake.describe_req.container_group_ids) == ["eci-abc"]
    # normalized to the {"status","public_ip"} contract the carrier expects
    assert desc == {"status": "Running", "public_ip": "1.2.3.4"}


def test_describe_missing_group_returns_pending_empty():
    class _Empty(_FakeEciClient):
        def describe_container_groups(self, req):
            return types.SimpleNamespace(
                body=types.SimpleNamespace(container_groups=[]))
    sdk = Eci20180808Sdk(region="cn-hangzhou", client=_Empty())
    assert sdk.describe_container_group("eci-x") == {"status": "", "public_ip": ""}


def test_delete_maps_id_onto_request():
    fake = _FakeEciClient()
    sdk = Eci20180808Sdk(region="cn-hangzhou", client=fake)
    sdk.delete_container_group("eci-abc")
    assert fake.delete_req.container_group_id == "eci-abc"
