"""The pinned 5xx-retry policy on the agent's stub/reliability path (handle_invoke).

The reliability tasks (T1.3/T1.10/T1.12) drive the agent through handle_invoke
(no arms_config), and observe the agent's retries via the mock's per-correlation
call counter. So handle_invoke MUST honor the same pinned contract as lc_agent:
  max_retries=2  → 3 total attempts on persistent 5xx
  backoff_ms=200 → skipped via monkeypatch here
  retry_on=5xx   → 4xx and connection failures (599) do NOT retry

Regression: the retry contract was only wired into the traced lc_agent path,
so the stub path issued a single call and reliability probes reported a constant
recovered=false / observed_attempts=1 regardless of platform behavior.
"""

from urllib.error import HTTPError, URLError

import pytest

from clousight_bench.domains.agent_runtime.agent_bundle import agent


class _FakeResp:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *a: object) -> bool:
        return False

    def read(self) -> bytes:
        return b"{}"


def _urlopen_returning(statuses: list[object], counter: dict[str, int]):
    """Fake urlopen that yields one outcome per call from `statuses`.

    An int 2xx returns a response; an int >=400 raises HTTPError with that code;
    a URLError instance simulates a connection failure (no .code → 599).
    """
    seq = iter(statuses)

    def _open(req, timeout=None):  # noqa: ANN001, ANN202
        counter["n"] += 1
        outcome = next(seq)
        if isinstance(outcome, URLError):
            raise outcome
        status = int(outcome)
        if 200 <= status < 300:
            return _FakeResp(status)
        raise HTTPError(req.full_url, status, "err", {}, None)

    return _open


def _invoke(monkeypatch, statuses: list[object]) -> tuple[dict, int]:
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    counter = {"n": 0}
    monkeypatch.setattr(agent.urlrequest, "urlopen", _urlopen_returning(statuses, counter))
    result = agent.handle_invoke(
        {
            "tool": {"target": "prices", "method": "GET", "params": {"provider": "aliyun"}},
            "mock_base_url": "http://mock",
            "mock_token": "t",
            "_correlation_id": "corr-1",
        }
    )
    return result, counter["n"]


def test_policy_matches_lc_agent_contract():
    # Single source of truth: the stub-path policy must equal the traced-path one.
    assert agent.AGENT_RETRY_POLICY == {"max_retries": 2, "backoff_ms": 200, "retry_on": "5xx"}


def test_retries_twice_on_persistent_5xx_then_gives_up(monkeypatch):
    result, calls = _invoke(monkeypatch, [500, 500, 500])
    assert calls == 3  # 1 initial + 2 retries
    assert result["ok"] is False and result["status"] == 500


def test_retries_then_succeeds(monkeypatch):
    result, calls = _invoke(monkeypatch, [500, 200])
    assert calls == 2  # stopped as soon as it recovered
    assert result["ok"] is True and result["status"] == 200


def test_no_retry_on_success(monkeypatch):
    result, calls = _invoke(monkeypatch, [200])
    assert calls == 1 and result["ok"] is True


def test_no_retry_on_4xx(monkeypatch):
    result, calls = _invoke(monkeypatch, [404])
    assert calls == 1 and result["ok"] is False and result["status"] == 404


def test_no_retry_on_connection_failure(monkeypatch):
    result, calls = _invoke(monkeypatch, [URLError("refused")])
    assert calls == 1 and result["status"] == 599


def test_stub_and_traced_policies_do_not_drift():
    lc_agent = pytest.importorskip("clousight_bench.domains.agent_runtime.agent_bundle.lc_agent")
    assert agent.AGENT_RETRY_POLICY == lc_agent.AGENT_RETRY_POLICY
