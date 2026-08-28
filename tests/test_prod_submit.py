"""Tests for prod submit + teardown local logic."""

import pytest

from clousight_bench.core import prod_submit
from clousight_bench.core.blobstore import InMemoryBlobStore
from clousight_bench.core.campaign_channel import CampaignChannel
from clousight_bench.core.errors import UserInputError
from clousight_bench.core.resource_ledger import LEDGER_FILE, ResourceLedger


def _write(p, text):
    p.write_text(text, encoding="utf-8")
    return str(p)


# ---- provider-resolved controller terraform surface (ControllerTfSpec) ------


def test_aliyun_controller_tf_spec_is_verbatim_legacy_constants():
    """The Aliyun provider's spec must equal the constants that used to live in
    core/prod_submit.py — same targets, same order, same driver-var mapping."""
    from clousight_bench.domains.agent_runtime.aliyun.provider import AliyunRuntimeProvider

    spec = AliyunRuntimeProvider().controller_tf_spec()
    assert spec is not None
    assert spec.tf_targets == (
        "alicloud_instance.controller",
        "alicloud_ram_role.controller",
        "alicloud_ram_policy.controller",
        "alicloud_ram_role_policy_attachment.controller",
        "alicloud_nat_gateway.bench",
        "alicloud_eip_address.nat",
        "alicloud_eip_association.nat",
        "alicloud_snat_entry.bench",
    )
    # items() asserts both the mapping AND the -var emission order
    assert list(spec.driver_tf_vars.items()) == [
        ("install_docker", "controller_install_docker"),
        ("system_disk_size", "controller_system_disk_size"),
        ("docker_registry_mirror", "controller_docker_registry_mirror"),
        ("hf_endpoint", "controller_hf_endpoint"),
        ("instance_type", "controller_instance_type"),
    ]


def test_aliyun_controller_reaper_spec_wires_three_callables_plus_self_id():
    """The Aliyun provider's reaper spec must carry the three live delete
    callables + a self-instance-id lookup (the SDK bodies themselves stay
    live-only / # pragma: no cover — assert only the wiring shape here)."""
    from clousight_bench.core.plugin import ControllerReaperSpec
    from clousight_bench.domains.agent_runtime.aliyun.provider import AliyunRuntimeProvider

    spec = AliyunRuntimeProvider().controller_reaper_spec("cn-hangzhou", lambda _m: None)
    assert isinstance(spec, ControllerReaperSpec)
    assert callable(spec.delete_runtime)
    assert callable(spec.delete_nat)
    assert callable(spec.delete_self)
    assert callable(spec.self_instance_id)


def test_default_provider_has_no_controller_reaper_spec():
    """A provider without a wired prod-controller reaper returns None, so the
    controller degrades to a no-op reap rather than guessing another cloud's SDK."""
    from clousight_bench.core.plugin import RuntimeProviderPlugin

    class _Bare(RuntimeProviderPlugin):
        provider = "bare"

        def build_transport(self, adapter):  # noqa: ANN001, ANN201
            return None

    assert _Bare().controller_reaper_spec("cn-hangzhou", lambda _m: None) is None


def test_submit_provider_without_controller_profile_fails_loudly(tmp_path):
    """aws IS an installed runtime provider but has no prod-controller profile →
    submit fails with the actionable message BEFORE any launch write or
    terraform call — never a silent fallback to the aliyun targets."""
    plan = _write(tmp_path / "plan.yaml", "tasks:\n  - task_id: T1.9\n")
    config = _write(tmp_path / "cfg.yaml", 'params: {}\ntarget: {"provider": "aws"}\n')
    oss = InMemoryBlobStore()
    tf_calls = []
    with pytest.raises(UserInputError, match=r"provider 'aws' has no prod-controller profile"):
        prod_submit.submit(
            plan,
            config,
            channel_factory=lambda c: CampaignChannel(oss, c),
            terraform=lambda argv: tf_calls.append(argv) or 0,
            watchdog_timeout_s=600.0,
            gen_id=lambda: "camp-x",
        )
    assert tf_calls == [] and not oss._store  # failed before any side effect


def test_submit_unresolvable_provider_fails_loudly(tmp_path):
    """No target.provider and no plan platform → loud failure, not an implicit
    aliyun; same for a provider with no installed plugin at all."""
    plan = _write(tmp_path / "plan.yaml", "tasks:\n  - task_id: T1.9\n")
    for target_yaml in ("target: {}\n", 'target: {"provider": "gcp"}\n'):
        config = _write(tmp_path / "cfg.yaml", target_yaml)
        with pytest.raises(UserInputError, match="no prod-controller profile.*csbench submit"):
            prod_submit.submit(
                plan,
                config,
                channel_factory=lambda c: CampaignChannel(InMemoryBlobStore(), c),
                terraform=lambda argv: 0,
                watchdog_timeout_s=600.0,
                gen_id=lambda: "camp-x",
            )


def test_submit_infers_provider_from_plan_platform(tmp_path):
    """A plan carrying ``platform: aliyun-agentrun`` (the committed-plan shape)
    resolves provider aliyun even when the config target has no provider key."""
    plan = _write(
        tmp_path / "plan.yaml",
        "platform: aliyun-agentrun\ntasks:\n  - task_id: T1.9\n",
    )
    config = _write(tmp_path / "cfg.yaml", "params: {}\ntarget: {}\n")
    tf_calls = []
    prod_submit.submit(
        plan,
        config,
        channel_factory=lambda c: CampaignChannel(InMemoryBlobStore(), c),
        terraform=lambda argv: tf_calls.append(argv) or 0,
        watchdog_timeout_s=600.0,
        gen_id=lambda: "camp-i",
    )
    assert "alicloud_instance.controller" in tf_calls[0]


def test_teardown_provider_without_profile_fails_before_stop_signal(tmp_path):
    ch = CampaignChannel(InMemoryBlobStore(), "camp-1")
    with pytest.raises(UserInputError, match="no prod-controller profile"):
        prod_submit.teardown(
            ch,
            terraform=lambda argv: 0,
            delete_runtime=lambda rid: None,
            provider="aws",
        )
    assert ch.stop_requested() is False  # failed before signalling the controller


def test_submit_writes_launch_and_applies_terraform(tmp_path):
    plan = _write(
        tmp_path / "plan.yaml",
        "tasks:\n  - task_id: T1.9\n  - task_id: T1.13\n    params: {repeat: 2}\n",
    )
    config = _write(
        tmp_path / "cfg.yaml",
        'params: {"warmup": 1}\ntarget: {"provider": "aliyun", "region": "cn-hangzhou"}\n',
    )
    oss = InMemoryBlobStore()
    tf_calls = []
    cid = prod_submit.submit(
        plan,
        config,
        channel_factory=lambda c: CampaignChannel(oss, c),
        terraform=lambda argv: tf_calls.append(argv) or 0,
        watchdog_timeout_s=600.0,
        gen_id=lambda: "camp-x",
    )
    assert cid == "camp-x"
    spec = CampaignChannel(oss, "camp-x").read_launch()
    assert spec.tasks == [
        {"task_id": "T1.9", "params": {}},
        {"task_id": "T1.13", "params": {"repeat": 2}},
    ]
    assert spec.target["provider"] == "aliyun" and spec.params == {"warmup": 1}
    assert spec.watchdog_timeout_s == 600.0
    assert spec.cost_budget is None  # no budget in the plan
    assert tf_calls[0][0] == "apply"
    assert "enable_controller=true" in tf_calls[0] and "enable_nat=true" in tf_calls[0]
    # no driver section → no controller_* driver vars injected
    assert not any(a.startswith("controller_install_docker") for a in tf_calls[0])


def test_submit_driver_section_and_cost_budget(tmp_path):
    plan = _write(
        tmp_path / "plan.yaml",
        "cost_budget: 25.5\n"
        "driver:\n"
        "  install_docker: true\n"
        "  system_disk_size: 120\n"
        '  docker_registry_mirror: "https://m.example.com"\n'
        '  hf_endpoint: "https://hf-mirror.com"\n'
        '  instance_type: "ecs.c6.xlarge"\n'
        "tasks:\n"
        '  - task_id: "suite:swe-bench"\n'
        "    params: {subset: verified-50}\n",
    )
    config = _write(tmp_path / "cfg.yaml", 'params: {}\ntarget: {"provider": "aliyun"}\n')
    oss = InMemoryBlobStore()
    tf_calls = []
    prod_submit.submit(
        plan,
        config,
        channel_factory=lambda c: CampaignChannel(oss, c),
        terraform=lambda argv: tf_calls.append(argv) or 0,
        watchdog_timeout_s=600.0,
        gen_id=lambda: "camp-d",
    )
    argv = tf_calls[0]
    assert "controller_install_docker=true" in argv
    assert "controller_system_disk_size=120" in argv
    assert "controller_docker_registry_mirror=https://m.example.com" in argv
    assert "controller_hf_endpoint=https://hf-mirror.com" in argv
    assert "controller_instance_type=ecs.c6.xlarge" in argv
    spec = CampaignChannel(oss, "camp-d").read_launch()
    assert spec.cost_budget == 25.5
    assert spec.tasks == [{"task_id": "suite:swe-bench", "params": {"subset": "verified-50"}}]


def test_submit_driver_partial_keys_only_emit_present_vars(tmp_path):
    plan = _write(
        tmp_path / "plan.yaml",
        "driver:\n  system_disk_size: 80\ntasks:\n  - task_id: T2.1\n",
    )
    config = _write(tmp_path / "cfg.yaml", 'params: {}\ntarget: {"provider": "aliyun"}\n')
    tf_calls = []
    prod_submit.submit(
        plan,
        config,
        channel_factory=lambda c: CampaignChannel(InMemoryBlobStore(), c),
        terraform=lambda argv: tf_calls.append(argv) or 0,
        watchdog_timeout_s=600.0,
        gen_id=lambda: "camp-p",
    )
    argv = tf_calls[0]
    assert "controller_system_disk_size=80" in argv
    assert not any(a.startswith("controller_install_docker") for a in argv)
    assert not any(a.startswith("controller_hf_endpoint") for a in argv)


def test_submit_with_wheel_builder_injects_wheel_vars(tmp_path):
    plan = _write(tmp_path / "plan.yaml", "tasks:\n  - task_id: T1.13\n")
    config = _write(
        tmp_path / "cfg.yaml",
        'params: {}\ntarget: {"provider": "aliyun", "blob_bucket": "b", "region": "cn-hangzhou"}\n',
    )
    oss = InMemoryBlobStore()
    tf_calls = []
    swebench_flags = []
    prod_submit.submit(
        plan,
        config,
        channel_factory=lambda c: CampaignChannel(oss, c),
        terraform=lambda argv: tf_calls.append(argv) or 0,
        watchdog_timeout_s=600.0,
        wheel_builder=lambda cid, needs_swebench: (
            swebench_flags.append(needs_swebench) or ("https://p/w.whl", ["requests>=2.28", "duckdb>=1.0"])
        ),
        gen_id=lambda: "camp-w",
    )
    argv = tf_calls[0]
    assert "controller_wheel_url=https://p/w.whl" in argv
    assert 'controller_extra_deps=["requests>=2.28", "duckdb>=1.0"]' in argv
    # no suite: task in the plan → the driver host does not need the harness extra
    assert swebench_flags == [False]


def test_submit_suite_plan_requests_swebench_deps(tmp_path):
    """A plan with any suite: task must ask the wheel builder for the swebench extra
    — otherwise the driver host cannot run the harness (B1 live blocker)."""
    plan = _write(
        tmp_path / "plan.yaml",
        'tasks:\n  - task_id: T1.9\n  - task_id: "suite:swe-bench"\n',
    )
    config = _write(tmp_path / "cfg.yaml", 'params: {}\ntarget: {"provider": "aliyun", "mode": "real"}\n')
    swebench_flags = []
    prod_submit.submit(
        plan,
        config,
        channel_factory=lambda c: CampaignChannel(InMemoryBlobStore(), c),
        terraform=lambda argv: 0,
        watchdog_timeout_s=600.0,
        wheel_builder=lambda cid, needs_swebench: (
            swebench_flags.append(needs_swebench) or ("https://p/w.whl", ["swebench>=3.0"])
        ),
        gen_id=lambda: "camp-s",
    )
    assert swebench_flags == [True]


def test_submit_suite_plan_without_real_mode_warns_loudly(tmp_path, capsys):
    """suite: task + target.mode != real → LOUD stderr warning (mock submits stay
    legitimate for pipeline tests, so this is a warning, never an error)."""
    plan = _write(tmp_path / "plan.yaml", 'tasks:\n  - task_id: "suite:swe-bench"\n')
    config = _write(tmp_path / "cfg.yaml", 'params: {}\ntarget: {"provider": "aliyun", "mode": "mock"}\n')
    prod_submit.submit(
        plan,
        config,
        channel_factory=lambda c: CampaignChannel(InMemoryBlobStore(), c),
        terraform=lambda argv: 0,
        watchdog_timeout_s=600.0,
        gen_id=lambda: "camp-m",
    )
    err = capsys.readouterr().err
    assert "warning: suite task(s) submitted with target.mode='mock'" in err
    assert "MOCK artifacts" in err and "target.mode: real" in err


def test_submit_suite_plan_with_real_mode_does_not_warn(tmp_path, capsys):
    plan = _write(tmp_path / "plan.yaml", 'tasks:\n  - task_id: "suite:swe-bench"\n')
    config = _write(tmp_path / "cfg.yaml", 'params: {}\ntarget: {"provider": "aliyun", "mode": "real"}\n')
    prod_submit.submit(
        plan,
        config,
        channel_factory=lambda c: CampaignChannel(InMemoryBlobStore(), c),
        terraform=lambda argv: 0,
        watchdog_timeout_s=600.0,
        gen_id=lambda: "camp-r",
    )
    assert "warning" not in capsys.readouterr().err


def test_submit_non_suite_plan_never_warns_about_mode(tmp_path, capsys):
    plan = _write(tmp_path / "plan.yaml", "tasks:\n  - task_id: T1.9\n")
    config = _write(tmp_path / "cfg.yaml", 'params: {}\ntarget: {"provider": "aliyun"}\n')
    prod_submit.submit(
        plan,
        config,
        channel_factory=lambda c: CampaignChannel(InMemoryBlobStore(), c),
        terraform=lambda argv: 0,
        watchdog_timeout_s=600.0,
        gen_id=lambda: "camp-n",
    )
    assert "warning" not in capsys.readouterr().err


def test_teardown_stops_reaps_residual_and_destroys(tmp_path):
    oss = InMemoryBlobStore()
    ch = CampaignChannel(oss, "camp-1")
    # seed an OSS ledger snapshot listing a still-live runtime r9
    led = ResourceLedger(tmp_path)
    led.record_created("run-1", "aliyun", "r9", "runtime")
    ch.write_ledger((tmp_path / LEDGER_FILE).read_bytes())

    deleted = []
    tf_calls = []
    out = prod_submit.teardown(
        ch,
        terraform=lambda argv: tf_calls.append(argv) or 0,
        delete_runtime=lambda rid: deleted.append(rid),
        provider="aliyun",
    )
    assert ch.stop_requested() is True
    assert deleted == ["r9"]
    assert out == {"destroyed": True, "residual_deleted": ["r9"]}
    assert tf_calls[0][0] == "destroy" and "enable_nat=false" in tf_calls[0]


def test_submit_rejects_old_run_plan_task_shape(tmp_path):
    """A run-plan-shaped entry ('task:') fails with a pointed error, not KeyError."""
    import pytest

    plan = _write(tmp_path / "plan.yaml", "tasks:\n  - task: T1.9\n")
    config = _write(tmp_path / "cfg.yaml", "target: {}\n")
    oss = InMemoryBlobStore()
    with pytest.raises(ValueError, match="run-plan shape"):
        prod_submit.submit(
            plan,
            config,
            channel_factory=lambda c: CampaignChannel(oss, c),
            terraform=lambda argv: 0,
            watchdog_timeout_s=600.0,
            gen_id=lambda: "camp-x",
        )


def test_submit_rejects_unknown_driver_keys(tmp_path):
    """A typo'd driver key must fail at submit time, not as a silent no-op in-cloud."""
    import pytest

    plan = _write(
        tmp_path / "plan.yaml",
        "tasks:\n  - task_id: T1.9\ndriver: {install_dokcer: true}\n",
    )
    config = _write(tmp_path / "cfg.yaml", 'target: {"provider": "aliyun"}\n')
    oss = InMemoryBlobStore()
    with pytest.raises(ValueError, match="install_dokcer"):
        prod_submit.submit(
            plan,
            config,
            channel_factory=lambda c: CampaignChannel(oss, c),
            terraform=lambda argv: 0,
            watchdog_timeout_s=600.0,
            gen_id=lambda: "camp-x",
        )
