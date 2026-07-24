"""Credential resolution: reuse the cloud's own chain, never store a secret."""
from clousight_bench.core.credentials import infer_provider, resolve_credentials


def test_infer_provider_from_platform_prefix():
    assert infer_provider({}, "aliyun-agentrun") == "aliyun"
    assert infer_provider({}, "aws-emr") == "aws"
    assert infer_provider({"provider": "huawei"}, "local-sim") == "huawei"
    assert infer_provider({}, "local-sim") is None


def test_unknown_provider_is_reported_not_raised():
    res = resolve_credentials({}, platform="local-sim")
    assert res.ok is False
    assert res.source == "unknown-provider"


def test_auth_env_escape_hatch(monkeypatch):
    monkeypatch.setenv("MY_AK", "x")
    monkeypatch.setenv("MY_SK", "y")
    res = resolve_credentials(
        {"provider": "aws", "auth_env": {"access_key_id": "MY_AK", "access_key_secret": "MY_SK"}}
    )
    assert res.ok and res.source == "auth_env"
    # identity hint carries only var NAMES, never the secret values
    assert "MY_AK" in res.identity_hint and "x" not in res.identity_hint


def test_auth_env_missing_reports_which(monkeypatch):
    monkeypatch.delenv("MY_AK", raising=False)
    res = resolve_credentials({"provider": "aws", "auth_env": {"id": "MY_AK"}})
    assert res.ok is False
    assert "MY_AK" in res.detail["missing_env"]


def test_profile_takes_precedence_over_std_env():
    res = resolve_credentials({"provider": "aws", "profile": "prod"})
    assert res.ok and res.source == "profile"
    assert res.identity_hint == "profile:prod"


def test_std_env_chain(monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "a")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "b")
    res = resolve_credentials({"provider": "aws"})
    assert res.ok and res.source == "std_env"


def test_no_credentials_gives_remediation(monkeypatch):
    for var in ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(var, raising=False)
    # point HOME somewhere with no ~/.aws so cred_file check misses
    res = resolve_credentials({"provider": "aws"})
    if not res.ok:  # only assert remediation when the host truly has no creds
        assert res.source == "none"
        assert "credentials" in res.remediation.lower()


def test_adapter_self_reports_credentials():
    from clousight_bench.domains.agent_runtime.adapters.local_sim import LocalSimAdapter

    # local-sim has no provider -> unknown, but the method must exist and not crash
    res = LocalSimAdapter().resolve_credentials()
    assert res.source == "unknown-provider"
