from clousight_bench.domains.agent_runtime.adapters.cn_clouds import AliyunAgentRunAdapter
from clousight_bench.domains.agent_runtime.adapters.local_sim import LocalSimAdapter


def test_local_sim_is_simulated():
    assert LocalSimAdapter().execution_mode() == "simulated"


def test_cloud_mock_is_simulated_real_is_live():
    assert AliyunAgentRunAdapter({"mode": "mock"}).execution_mode() == "simulated"
    assert AliyunAgentRunAdapter({"mode": "real"}).execution_mode() == "live"
