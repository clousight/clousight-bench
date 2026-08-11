"""P1-4 / P1-5: one shared timeout + retry policy for real-cloud SDK calls.

Centralised so all four clouds inherit the same bound instead of the first wired
adapter inventing its own. The per-request read timeout -- not the SIGALRM stage
deadline, which is main-thread-only and misses threaded load probes -- is the
real defense against a hung live call, so it must always be finite and it must
be bounded by the run's remaining deadline.
"""

from clousight_bench.core.clients import ClientFactory, ClientPolicy
from clousight_bench.domains.agent_runtime.adapters.local_sim import LocalSimAdapter


def test_policy_has_finite_defaults():
    p = ClientPolicy.from_target({})
    assert p.connect_timeout_s > 0
    assert p.read_timeout_s > 0
    assert p.max_attempts >= 1


def test_policy_reads_overrides_from_target():
    p = ClientPolicy.from_target(
        {
            "timeouts": {"connect_s": 2, "read_s": 8},
            "retries": {"max_attempts": 5, "backoff_base_s": 0.5, "backoff_max_s": 10},
        }
    )
    assert p.connect_timeout_s == 2
    assert p.read_timeout_s == 8
    assert p.max_attempts == 5
    assert p.backoff_base_s == 0.5
    assert p.backoff_max_s == 10


def test_read_timeout_is_bounded_by_remaining_deadline():
    p = ClientPolicy.from_target({"timeouts": {"read_s": 30}})
    assert p.bounded_read_timeout(4.0) == 4.0  # deadline is tighter -> use it
    assert p.bounded_read_timeout(None) == 30  # no deadline -> full read timeout
    assert p.bounded_read_timeout(100.0) == 30  # deadline looser -> cap at read


def test_backoff_grows_and_caps():
    p = ClientPolicy.from_target({"retries": {"backoff_base_s": 1.0, "backoff_max_s": 3.0}})
    assert p.backoff_for(1) == 1.0
    assert p.backoff_for(2) == 2.0
    assert p.backoff_for(10) == 3.0  # capped


def test_client_context_carries_policy_and_deadline():
    f = ClientFactory(
        "aliyun",
        "cn-hangzhou",
        "https://x",
        {"timeouts": {"read_s": 7}},
        platform="aliyun-agentrun",
        deadline_s=12.0,
    )
    ctx = f.context()
    assert ctx.policy.read_timeout_s == 7
    assert ctx.deadline_s == 12.0


def test_managed_adapter_threads_its_run_deadline_into_the_client():
    a = LocalSimAdapter({"provider": "aliyun", "region": "cn-hangzhou"})
    a.deadline_s = 20.0
    ctx = a.client_factory().context()
    assert ctx.deadline_s == 20.0
