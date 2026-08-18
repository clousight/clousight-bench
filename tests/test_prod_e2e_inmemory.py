"""In-memory end-to-end for the ecs prod profile (no cloud).

Wires submit → CampaignController → status/fetch → teardown over one
InMemoryOssClient with fake terraform/run_task/delete seams. This is the CI
guarantee that the whole loop closes: a campaign submitted, run, fetched, and
torn down with residuals cleared.
"""

from clousight_bench.core import prod_submit
from clousight_bench.core.controller import CampaignController, TaskOutcome
from clousight_bench.core.resource_ledger import LEDGER_FILE, ResourceLedger
from clousight_bench.domains.agent_runtime.probe.campaign_channel import CampaignChannel
from clousight_bench.domains.agent_runtime.probe.oss_client import InMemoryOssClient


def test_full_prod_flow_in_memory(tmp_path):
    oss = InMemoryOssClient()
    plan = tmp_path / "plan.yaml"
    plan.write_text("tasks:\n  - task: T1.13\n  - task: T2.1\n", encoding="utf-8")
    config = tmp_path / "cfg.yaml"
    config.write_text('params: {}\ntarget: {"provider": "aliyun"}\n', encoding="utf-8")

    # 1) submit — writes launch + (fake) terraform apply
    tf_calls = []
    cid = prod_submit.submit(
        plan,
        config,
        channel_factory=lambda c: CampaignChannel(oss, c),
        terraform=lambda argv: tf_calls.append(argv[0]) or 0,
        watchdog_timeout_s=600.0,
        gen_id=lambda: "camp-e2e",
    )
    assert tf_calls == ["apply"]

    ch = CampaignChannel(oss, cid, now=lambda: 5.0)

    # simulate the controller having provisioned a runtime (ledger synced to OSS)
    led_dir = tmp_path / "ledger"
    led = ResourceLedger(led_dir)
    led.record_created(cid, "aliyun", "rt-1", "runtime")

    # 2) controller runs the campaign (fake run_task; ledger snapshot each task)
    def run_task(task_id, spec):
        parquet = b"PARQ" if task_id == "T1.13" else None
        return TaskOutcome(
            task_id=task_id, ok=True, result_json=b'{"id":"%s"}' % task_id.encode(), series_parquet=parquet
        )

    CampaignController(
        ch,
        run_task,
        now=lambda: 5.0,
        ledger_bytes=lambda: (led_dir / LEDGER_FILE).read_bytes(),
    ).run()

    # 3) status — done, all completed
    st = prod_submit.status(ch, now=lambda: 6.0)
    assert st["done"] == "DONE"
    assert st["counts"] == {"completed": 2}

    # 4) fetch — both results land, T1.13 with its parquet sidecar
    written = prod_submit.fetch(ch, tmp_path / "out")
    names = sorted(p.name for p in written)
    assert names == ["T1.13.json", "T1.13.series.parquet", "T2.1.json"]

    # 5) teardown — reaps the ledger-listed runtime and destroys
    deleted = []
    out = prod_submit.teardown(
        ch,
        terraform=lambda argv: 0,
        delete_runtime=lambda rid: deleted.append(rid),
    )
    assert deleted == ["rt-1"]
    assert out["destroyed"] is True and out["residual_deleted"] == ["rt-1"]
