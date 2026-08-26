"""Mock-mode e2e for the COMMITTED SWE smoke plan (``configs/swe-bench-smoke.plan.yaml``).

Every test loads the actual committed file — never a lookalike literal — so the
file that operators will `csbench submit` is the file proven here:

1. shape: one ``suite:swe-bench`` task entry, oracle mode, the 3 real Verified
   instance ids, a docker-capable ``driver:`` section, and a ``cost_budget``;
2. submit: ``prod_submit.submit()`` (the exact function behind ``csbench
   submit``) accepts it with in-memory OSS + a fake terraform, and forwards the
   driver keys as ``controller_*`` tf vars;
3. execute: ``orchestrator.execute()`` with EXACTLY the parsed ``{task_id,
   params}`` entry against a mock target completes with swe-bench measurements
   and the HONEST mock-agent provenance scaffold (mock artifacts must never
   claim the slice-2 real-SUT scaffold).

No network, no docker, no cloud — safe for the default gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

import clousight_bench.core.orchestrator as orch
from clousight_bench.core import prod_submit
from clousight_bench.core.schema import RunSpec
from clousight_bench.domains.agent_runtime.probe.campaign_channel import CampaignChannel
from clousight_bench.domains.agent_runtime.probe.oss_client import InMemoryOssClient

REPO_ROOT = Path(__file__).resolve().parent.parent
PLAN_PATH = REPO_ROOT / "configs" / "swe-bench-smoke.plan.yaml"

# The 3 real SWE-bench Verified ids bundled in
# src/clousight_bench/suites/swe_bench/fixtures/instances_subset.json.
EXPECTED_INSTANCE_IDS = [
    "django__django-11099",
    "sympy__sympy-20590",
    "pytest-dev__pytest-7205",
]


def _plan_doc() -> dict[str, Any]:
    return dict(yaml.safe_load(PLAN_PATH.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# 1. Committed file shape
# ---------------------------------------------------------------------------


def test_committed_plan_shape():
    """The committed smoke plan carries exactly the documented submit shape."""
    doc = _plan_doc()
    tasks = doc["tasks"]
    assert len(tasks) == 1, f"smoke plan must stay a single task, got {len(tasks)}"
    entry = tasks[0]
    assert entry["task_id"] == "suite:swe-bench"
    params = entry["params"]
    assert params["agent_kind"] == "oracle"  # zero-LLM-cost smoke; llm is the paid variant
    assert params["instance_ids"] == EXPECTED_INSTANCE_IDS
    assert doc["cost_budget"] == 10.0
    driver = doc["driver"]
    assert driver["install_docker"] is True
    assert driver["hf_endpoint"] == "https://hf-mirror.com"  # cn-region HF mirror is REQUIRED
    assert "docker_registry_mirror" in driver  # docker hub is blocked in cn regions
    assert int(driver["system_disk_size"]) >= 100  # 3 instance images + base ~ tens of GiB


def test_committed_plan_has_no_secrets():
    """The committed plan must hold placeholders only — no key material shapes."""
    text = PLAN_PATH.read_text(encoding="utf-8")
    for needle in ("aliyuncs.com/secret", "ACCESS_KEY_SECRET="):
        assert needle not in text, f"suspicious credential material {needle!r} in committed plan"
    import re

    for pattern in (r"LTAI[0-9A-Za-z]{12,}", r"\b[a-z0-9-]*-[0-9a-f]{8}\b.*oss", r"@[0-9a-f]{10,}\b.*arms"):
        assert not re.search(pattern, text), f"credential-shaped match {pattern!r} in committed plan"


# ---------------------------------------------------------------------------
# 2. `csbench submit` accepts the committed file (in-memory OSS, fake terraform)
# ---------------------------------------------------------------------------


def test_submit_accepts_committed_plan(tmp_path):
    """prod_submit.submit() parses tasks/driver/cost_budget from the real file,
    and the suite task pulls the [swebench] harness extra onto the driver host."""
    from clousight_bench.cli import _controller_extra_deps

    config = tmp_path / "cfg.yaml"
    config.write_text(
        'params: {}\ntarget: {"provider": "aliyun", "region": "cn-hangzhou", "mode": "real"}\n',
        encoding="utf-8",
    )
    oss = InMemoryOssClient()
    tf_calls: list[list[str]] = []
    cid = prod_submit.submit(
        PLAN_PATH,
        str(config),
        channel_factory=lambda c: CampaignChannel(oss, c),
        terraform=lambda argv: tf_calls.append(argv) or 0,
        watchdog_timeout_s=5400.0,
        # The REAL extras computation behind _prod_wheel_builder — only the OSS
        # wheel upload is stubbed, so the submit path proves the deps it carries.
        wheel_builder=lambda c, needs_swebench: (
            "https://p/w.whl",
            _controller_extra_deps(needs_swebench),
        ),
        gen_id=lambda: "camp-swe-smoke",
    )
    assert cid == "camp-swe-smoke"
    spec = CampaignChannel(oss, cid).read_launch()
    assert spec.cost_budget == 10.0
    assert [t["task_id"] for t in spec.tasks] == ["suite:swe-bench"]
    assert spec.tasks[0]["params"]["agent_kind"] == "oracle"
    assert spec.tasks[0]["params"]["instance_ids"] == EXPECTED_INSTANCE_IDS
    # driver: forwarded as controller_* terraform vars (unknown keys would raise)
    argv = tf_calls[0]
    assert "controller_install_docker=true" in argv
    assert "controller_hf_endpoint=https://hf-mirror.com" in argv
    assert any(a.startswith("controller_docker_registry_mirror=") for a in argv)
    assert any(a.startswith("controller_system_disk_size=") for a in argv)
    # image builds need ≥4c8g — the plan pins the driver instance type (M1)
    assert "controller_instance_type=ecs.c6.xlarge" in argv
    # B1: the suite task makes the controller cloud-init install the harness —
    # the swebench requirement must ride in the controller_extra_deps tf var.
    (deps_var,) = [a for a in argv if a.startswith("controller_extra_deps=")]
    assert "swebench" in deps_var, f"swebench dep missing from driver deps: {deps_var}"


def test_llm_key_on_driver_never_lands_in_launch_spec_or_record(tmp_path, monkeypatch):
    """A DASHSCOPE_API_KEY in the driver's env goes ONLY to the provision API —
    it must never appear in the LaunchSpec on OSS, the persisted record, or any
    staged artifact file."""
    sentinel = "sk-DASHSCOPE-SENTINEL-must-never-persist"
    monkeypatch.setenv("DASHSCOPE_API_KEY", sentinel)
    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)

    # submit path: every byte written to the campaign channel is key-free
    config = tmp_path / "cfg.yaml"
    config.write_text('params: {}\ntarget: {"mode": "real"}\n', encoding="utf-8")
    oss = InMemoryOssClient()
    prod_submit.submit(
        PLAN_PATH,
        str(config),
        channel_factory=lambda c: CampaignChannel(oss, c),
        terraform=lambda argv: 0,
        watchdog_timeout_s=5400.0,
        gen_id=lambda: "camp-secret",
    )
    for key, blob in oss._store.items():
        assert sentinel.encode() not in blob, f"llm key leaked into OSS object {key}"

    # execute path: neither the record json nor any staged artifact carries it
    entry = _plan_doc()["tasks"][0]
    spec = RunSpec(
        domain="agent-runtime",
        task_id=entry["task_id"],
        platform="local-sim",
        target={"mode": "mock"},
        params=dict(entry.get("params") or {}),
    )
    record = orch.execute(spec, results_dir=tmp_path, enrich=False, preflight=False)
    assert record.status == "completed"
    import json as _json

    assert sentinel not in _json.dumps(record.to_dict(), ensure_ascii=False, default=str)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert sentinel.encode() not in path.read_bytes(), f"llm key leaked into {path}"


# ---------------------------------------------------------------------------
# 3. The plan's task entry executes end-to-end in mock mode
# ---------------------------------------------------------------------------


def test_mock_e2e_executes_committed_task_entry(tmp_path, monkeypatch):
    """orchestrator.execute() runs the EXACT parsed entry against target.mode=mock."""
    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
    entry = _plan_doc()["tasks"][0]
    spec = RunSpec(
        domain="agent-runtime",
        task_id=entry["task_id"],
        platform="local-sim",
        target={"mode": "mock"},
        params=dict(entry.get("params") or {}),
    )
    record = orch.execute(spec, results_dir=tmp_path, enrich=False, preflight=False)
    assert record.status == "completed", f"expected completed, got {record.status}: {record.errors}"
    assert "swe-bench.resolved" in record.measurements, (
        f"swe-bench.resolved missing: {list(record.measurements)}"
    )
    assert record.provenance.suite_id == "swe-bench"
    # target.mode=mock ran mock artifacts — the scaffold must say so honestly,
    # even though the plan names agent_kind=oracle (only a real run earns @slice2).
    assert record.provenance.scaffold == "mock-agent@slice1"
