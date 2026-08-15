"""Ephemeral agent-artifact lifecycle: build the zip from package data, upload
under a unique key, and delete on teardown -- all without an account (the OSS
bucket client is injected).
"""

import io
import zipfile

import pytest

from clousight_bench.domains.agent_runtime import artifact
from clousight_bench.domains.agent_runtime.adapters.cn_clouds import AliyunAgentRunAdapter
from clousight_bench.domains.agent_runtime.aliyun import AliyunAgentRunTransport
from clousight_bench.domains.agent_runtime.artifact import OssArtifactStore, build_agent_zip_bytes


class _FakeBucket:
    """Records object puts/deletes so the lifecycle is assertable offline."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def put_object(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    def delete_object(self, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)


def test_build_agent_zip_is_a_valid_zip_with_the_agent():
    # langchain-free path (no network/pip); the zip roots the agent + shared
    # protocol contract flat, importable as siblings in FC/ECI.
    data = build_agent_zip_bytes(with_langchain=False)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        assert "agent.py" in names and "protocol.py" in names
        assert b"handle_invoke" in zf.read("agent.py")


def test_upload_puts_unique_key_and_delete_removes_it():
    fake = _FakeBucket()
    store = OssArtifactStore("my-bucket", "cn-hangzhou", bucket_factory=lambda: fake)

    ref1 = store.upload()
    ref2 = store.upload()
    assert ref1.startswith("oss://my-bucket/clousight-bench/")
    assert ref1 != ref2  # unique key per upload
    assert len(fake.objects) == 2

    store.delete(ref1)
    assert len(fake.objects) == 1
    assert ref1.rsplit("/", 1)[-1].removesuffix(".zip") in fake.deleted[0]


def test_upload_namespaces_key_under_run_id():
    # With a run_id the object is filed under it, so billing/audit can attribute
    # the artifact to the run.
    fake = _FakeBucket()
    store = OssArtifactStore(
        "my-bucket",
        "cn-hangzhou",
        run_id="run-20260730-000000-abcdef",
        bucket_factory=lambda: fake,
    )
    ref = store.upload()
    assert "/clousight-bench/run-20260730-000000-abcdef/" in ref
    assert next(iter(fake.objects)).startswith("clousight-bench/run-20260730-000000-abcdef/")


def test_delete_ignores_a_foreign_reference():
    fake = _FakeBucket()
    store = OssArtifactStore("my-bucket", "cn-hangzhou", bucket_factory=lambda: fake)
    store.delete("oss://other-bucket/whatever.zip")
    assert fake.deleted == []


def test_oss_auth_bridges_the_default_chain_incl_sts_token():
    pytest.importorskip("oss2")  # _ChainCredentialsProvider subclasses an oss2 base
    # OSS auth uses the same alibabacloud_credentials default chain as AgentRun,
    # so AK, CLI profile, OIDC, instance role AND STS temporary credentials all
    # work -- the security_token is carried through (that is what makes STS /
    # 本机临时 credentials work for uploads, not just static AccessKeys).
    from clousight_bench.domains.agent_runtime.artifact import _ChainCredentialsProvider

    class _FakeCred:
        access_key_id = "ak"
        access_key_secret = "sk"
        security_token = "sts-token"

    class _FakeClient:
        def get_credential(self):
            return _FakeCred()

    creds = _ChainCredentialsProvider(_FakeClient()).get_credentials()
    assert (creds.access_key_id, creds.access_key_secret) == ("ak", "sk")
    assert creds.security_token == "sts-token"


def test_missing_oss_sdk_gives_clear_install_hint(monkeypatch):
    # Force the OSS SDK import to fail (it may be installed via the `aliyun`
    # extra) so this deterministically exercises the missing-SDK path.
    import sys

    monkeypatch.setitem(sys.modules, "oss2", None)
    store = OssArtifactStore("my-bucket", "cn-hangzhou")
    with pytest.raises(RuntimeError, match="pip install oss2"):
        store.upload()


def test_provision_builds_and_uploads_when_bucket_set_and_teardown_deletes(monkeypatch):
    # The transport owns the artifact: with a bucket configured and no explicit
    # artifact_ref, provision uploads the built agent and teardown deletes it.
    fake = _FakeBucket()

    def _fake_store(bucket, region, *, endpoint=None, run_id=None):
        return OssArtifactStore(bucket, region, endpoint=endpoint, run_id=run_id, bucket_factory=lambda: fake)

    monkeypatch.setattr(artifact, "OssArtifactStore", _fake_store)

    t = AliyunAgentRunTransport(AliyunAgentRunAdapter({"region": "cn-hangzhou", "oss_bucket": "my-bucket"}))
    ref = t._ensure_artifact({})
    assert ref and ref.startswith("oss://my-bucket/")
    assert len(fake.objects) == 1

    residual = t._cleanup_artifact()
    assert residual == []
    assert len(fake.objects) == 0


def test_build_vendor_dir_falls_back_to_pip_when_uv_missing(tmp_path, monkeypatch):
    """A missing `uv` (e.g. a CI runner without it) must fall back to pip, not
    raise FileNotFoundError. Regression: subprocess.run raises before the
    returncode check, so the loop has to catch it and try the next installer."""
    import subprocess
    import sys

    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        if cmd[0] == "uv":
            raise FileNotFoundError(2, "No such file or directory", "uv")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    artifact._build_vendor_dir(tmp_path / "vendor")
    # tried uv, then fell back to THIS interpreter's pip (never a bare `pip`,
    # which isn't on PATH on the in-region controller).
    assert seen[0][0] == "uv"
    assert seen[1][:3] == [sys.executable, "-m", "pip"]
    assert len(seen) == 2
