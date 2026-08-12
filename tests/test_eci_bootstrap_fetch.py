# tests/test_eci_bootstrap_fetch.py
#
# The ECI probe used to bootstrap itself by pip-installing at boot and fetching a
# cb-probe.zip from OSS (verified by sha256) before running an HTTP server. That
# whole mechanism was replaced by a prebuilt ACR image whose ENTRYPOINT runs the
# OSS-poller loop directly (agent_loop.py) — so the create request no longer
# carries a shell bootstrap command, and readiness is polled via an OSS heartbeat,
# not an HTTP /health check. The bootstrap-command tests went with it; the oss-uri
# split helper is still used and keeps its test.

from clousight_bench.domains.agent_runtime.eci_carrier import _split_oss_uri


def test_split_oss_uri():
    assert _split_oss_uri("oss://mybucket/campaign-1/cb-probe.zip") == ("mybucket", "campaign-1/cb-probe.zip")
    assert _split_oss_uri("") == ("", "")
