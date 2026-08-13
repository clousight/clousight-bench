# tests/test_dev_wheel.py
"""Tests for the dev-wheel fallback (build → upload → presign).

Account-free: no wheel is built and no bucket is touched — the builder and OSS
clients are injected/faked. One test exercises the real _repo_root() lookup.
"""

from __future__ import annotations

import pytest

from clousight_bench.domains.agent_runtime import dev_wheel


class _FakeUpload:
    def __init__(self) -> None:
        self.puts: dict[str, bytes] = {}

    def put_object(self, key: str, data: bytes) -> None:
        self.puts[key] = data

    def get_object(self, key: str) -> bytes:
        return self.puts[key]

    def list_prefix(self, prefix: str) -> list[str]:
        return [k for k in self.puts if k.startswith(prefix)]

    def delete_object(self, key: str) -> None:
        self.puts.pop(key, None)


class _FakeSigner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str]] = []

    def sign_url(self, key: str, expires: int = 3600, method: str = "GET") -> str:
        self.calls.append((key, expires, method))
        return f"https://oss-internal.example/{key}?sig=abc&Expires={expires}"


_WHEEL = ("clousight_bench-0.2.0-py3-none-any.whl", b"WHEELBYTES")


def test_upload_dev_wheel_puts_under_prefix_and_presigns():
    up, sign = _FakeUpload(), _FakeSigner()
    url = dev_wheel.upload_dev_wheel(up, sign, "run-xy", wheel=_WHEEL)
    key = "clousight-bench/dev-wheels/run-xy/clousight_bench-0.2.0-py3-none-any.whl"
    assert up.puts[key] == b"WHEELBYTES"
    assert key in url and url.startswith("https://oss-internal.example/")
    assert sign.calls == [(key, 3600, "GET")]


def test_upload_dev_wheel_blank_campaign_falls_back_to_adhoc():
    up, sign = _FakeUpload(), _FakeSigner()
    dev_wheel.upload_dev_wheel(up, sign, "", wheel=_WHEEL)
    assert any("/dev-wheels/adhoc/" in k for k in up.puts)


def test_upload_dev_wheel_custom_expires_passed_through():
    up, sign = _FakeUpload(), _FakeSigner()
    dev_wheel.upload_dev_wheel(up, sign, "c", wheel=_WHEEL, expires=60)
    assert sign.calls[0][1] == 60


def test_upload_dev_wheel_builds_when_no_wheel_given(monkeypatch):
    up, sign = _FakeUpload(), _FakeSigner()
    monkeypatch.setattr(
        dev_wheel,
        "build_probe_wheel_bytes",
        lambda: ("clousight_bench-9.9-py3-none-any.whl", b"BUILT"),
    )
    dev_wheel.upload_dev_wheel(up, sign, "c")
    key = "clousight-bench/dev-wheels/c/clousight_bench-9.9-py3-none-any.whl"
    assert up.puts[key] == b"BUILT"


def test_probe_extra_deps_includes_requests_and_oss2():
    joined = " ".join(dev_wheel.probe_extra_deps())
    assert "requests" in joined and "oss2" in joined


def test_repo_root_finds_pyproject():
    root = dev_wheel._repo_root()
    assert (root / "pyproject.toml").exists()


def test_oss2_sign_url_delegates_to_bucket(monkeypatch):
    from clousight_bench.domains.agent_runtime.probe.oss_client import Oss2Client

    client = Oss2Client(bucket="b", region="cn-hangzhou", internal=True)
    calls: list[tuple] = []

    class _Bucket:
        def sign_url(self, method, key, expires, slash_safe):  # noqa: ANN001
            calls.append((method, key, expires, slash_safe))
            return "https://signed.example/x"

    monkeypatch.setattr(client, "_bucket_handle", lambda: _Bucket())
    url = client.sign_url("k/x.whl", expires=120)
    assert url == "https://signed.example/x"
    assert calls == [("GET", "k/x.whl", 120, True)]


def test_repo_root_raises_when_no_pyproject(monkeypatch):
    # A path whose parents contain no pyproject.toml (root of the fs tree).
    import pathlib

    from clousight_bench.domains.agent_runtime.ecs_carrier import CarrierError

    fake = pathlib.Path("/nonexistent-xyz/pkg/mod.py")
    monkeypatch.setattr(dev_wheel, "__file__", str(fake))
    with pytest.raises(CarrierError, match="source tree"):
        dev_wheel._repo_root()


def test_ecs_metadata_provider_reads_instance_role_creds(monkeypatch):
    """The probe reads its instance RAM role from the ECS metadata service using
    only requests (no alibabacloud_credentials, which isn't in the probe extra)."""
    pytest.importorskip("oss2")  # get_credentials() builds an oss2 Credentials
    requests = pytest.importorskip("requests")  # probe extra dep, absent in bare install

    from clousight_bench.domains.agent_runtime.probe import oss_client

    calls: list[str] = []

    class _Resp:
        def __init__(self, text="", data=None):
            self._t, self._d = text, data

        @property
        def text(self):
            return self._t

        def json(self):
            return self._d

    def fake_get(url, timeout=None):
        calls.append(url)
        if url.endswith("security-credentials/"):
            return _Resp(text="my-role\n")  # auto-discover the role name
        return _Resp(data={"AccessKeyId": "AK", "AccessKeySecret": "SK", "SecurityToken": "TOK"})

    monkeypatch.setattr(requests, "get", fake_get)
    cred = oss_client._EcsMetadataCredentialsProvider().get_credentials()
    assert cred.get_access_key_id() == "AK"
    assert cred.get_access_key_secret() == "SK"
    assert cred.get_security_token() == "TOK"
    assert calls[0].endswith("security-credentials/")  # discovery call first
    assert calls[1].endswith("security-credentials/my-role")
