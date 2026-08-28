from clousight_bench.core.cost_notice import is_live_platform, live_cost_notice


def test_local_sim_never_bills():
    assert is_live_platform("local-sim") is False
    assert live_cost_notice("local-sim", allow_live=True) is None
    assert live_cost_notice("local-sim", allow_live=False) is None


def test_no_notice_without_allow_live():
    assert live_cost_notice("aliyun-agentrun", allow_live=False) is None


def test_live_notice_mentions_local_sim_and_budget():
    msg = live_cost_notice("aliyun-agentrun", task_count=27, allow_live=True)
    assert msg is not None
    assert "local-sim" in msg
    assert "--cost-budget" in msg
    # a batched (multi-task) run does not get the single-task nudge
    assert "SINGLE-task" not in msg


def test_single_task_live_run_gets_batch_nudge():
    msg = live_cost_notice("aliyun-agentrun", task_count=1, allow_live=True)
    assert msg is not None and "SINGLE-task" in msg
    # the batch lever names the mechanism and the cold-start rationale
    assert "run-plan" in msg
    assert "cold start" in msg.lower()


def test_notice_is_vendor_neutral():
    # core prose must not name provider products or vendor-specific latencies
    for count in (1, 27):
        msg = live_cost_notice("aliyun-agentrun", task_count=count, allow_live=True)
        assert msg is not None
        for vendor_term in ("FC", "AgentRuntime", "AgentRun", "86s", "~1s"):
            assert vendor_term not in msg


def test_aws_is_live():
    assert is_live_platform("aws-agentcore") is True
