"""csbench init / doctor: convenient, secret-free onboarding."""

import yaml

from clousight_bench.cli import main


def test_init_scaffolds_config_env_and_gitignore(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = main(["init", "aws", "--out", "."])
    assert rc == 0

    cfg = tmp_path / "agent-runtime-aws.local.yaml"
    assert cfg.exists()
    data = yaml.safe_load(cfg.read_text())
    assert data["target"]["provider"] == "aws"
    # no secret material anywhere in the generated config
    assert "SECRET" not in cfg.read_text().upper().replace("AWS_SECRET_ACCESS_KEY", "")

    env = tmp_path / ".env.example"
    assert env.exists()
    assert "AWS_ACCESS_KEY_ID=" in env.read_text()

    gi = (tmp_path / ".gitignore").read_text()
    assert "*.local.yaml" in gi and ".env" in gi


def test_init_unknown_provider_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = main(["init", "nope", "--out", "."])
    assert rc == 2  # a bad invocation, like every other user-input error
    assert "unknown provider" in capsys.readouterr().err


def test_doctor_flags_missing_credentials(tmp_path, monkeypatch, capsys):
    for var in ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.aws here
    cfg = tmp_path / "x.yaml"
    cfg.write_text("target:\n  provider: aws\n  region: us-east-1\n")
    rc = main(["doctor", "--config", str(cfg)])
    out = capsys.readouterr().out
    assert "provider — aws" in out
    assert rc == 1  # missing creds -> non-zero
    assert "credentials" in out.lower()


def test_doctor_passes_with_profile(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "x.yaml"
    # a non-localhost mock -> unreachable-from-here is a warning, not a blocker
    cfg.write_text(
        "target:\n  provider: aws\n  region: us-east-1\n  profile: default\n"
        "  mock_base_url: https://mock.example.com\n"
    )
    rc = main(["doctor", "--config", str(cfg)])
    out = capsys.readouterr().out
    assert "credentials — via profile" in out
    assert rc == 0


def test_doctor_rejects_localhost_mock(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "x.yaml"
    cfg.write_text("target:\n  provider: aws\n  profile: default\n  mock_base_url: http://127.0.0.1:8770\n")
    rc = main(["doctor", "--config", str(cfg)])
    out = capsys.readouterr().out
    assert "localhost" in out
    assert rc == 1
