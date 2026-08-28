"""Region-agnostic docker-image strategy for the SWE-bench driver host."""

from clousight_bench.domains.agent_runtime.driver_image import (
    decide_image_strategy,
    merge_registry_mirrors,
)


def test_direct_pull_when_dockerhub_reachable():
    s = decide_image_strategy("ap-southeast-1", dockerhub_ok=True, github_ok=True)
    assert s.mode == "direct"
    assert s.registry_mirrors == []
    assert s.ok is True


def test_direct_pull_warns_when_github_blocked():
    s = decide_image_strategy("cn-hangzhou", dockerhub_ok=True, github_ok=False)
    assert s.mode == "direct"
    assert s.ok is True
    assert "GitHub unreachable" in s.notes


def test_acr_mirror_when_dockerhub_blocked_and_acr_found():
    s = decide_image_strategy(
        "cn-hangzhou",
        dockerhub_ok=False,
        github_ok=True,
        acr_endpoint="registry-vpc.cn-hangzhou.aliyuncs.com",
    )
    assert s.mode == "acr-mirror"
    assert s.registry_mirrors == ["https://registry-vpc.cn-hangzhou.aliyuncs.com"]
    assert s.ok is True
    assert "pre-staged" in s.notes


def test_acr_mirror_flags_github_wall_too():
    s = decide_image_strategy(
        "cn-hangzhou",
        dockerhub_ok=False,
        github_ok=False,
        acr_endpoint="registry-vpc.cn-hangzhou.aliyuncs.com",
    )
    assert s.mode == "acr-mirror"
    assert "GitHub is ALSO blocked" in s.notes


def test_blocked_when_nothing_reachable_refuses_to_run():
    s = decide_image_strategy("cn-hangzhou", dockerhub_ok=False, github_ok=False)
    assert s.mode == "blocked"
    assert s.ok is False
    assert "overseas region" in s.notes


def test_override_mirror_always_wins():
    s = decide_image_strategy(
        "cn-hangzhou",
        dockerhub_ok=True,  # even with direct reachability, an explicit override wins
        github_ok=True,
        override_mirror="https://my.mirror.example",
    )
    assert s.mode == "manual"
    assert s.registry_mirrors == ["https://my.mirror.example"]


def test_merge_preserves_existing_daemon_keys():
    existing = {"log-driver": "json-file", "registry-mirrors": ["https://old"]}
    merged = merge_registry_mirrors(existing, ["https://new"])
    assert merged["log-driver"] == "json-file"  # untouched
    assert merged["registry-mirrors"] == ["https://new"]
    # empty mirrors (direct mode) removes the key but keeps the rest
    cleared = merge_registry_mirrors(existing, [])
    assert "registry-mirrors" not in cleared
    assert cleared["log-driver"] == "json-file"
