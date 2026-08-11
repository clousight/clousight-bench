# tests/test_eci_bootstrap_fetch.py
import subprocess

from clousight_bench.domains.agent_runtime.eci_carrier import (
    EciCarrierConfig,
    EciProbeCarrier,
    _split_oss_uri,
)


class _NoSdk:
    def create_container_group(self, req): return "eci-x"
    def describe_container_group(self, i): return {"status": "Running", "public_ip": "1.1.1.1"}
    def delete_container_group(self, i): return None


def test_split_oss_uri():
    assert _split_oss_uri("oss://mybucket/campaign-1/cb-probe.zip") == (
        "mybucket", "campaign-1/cb-probe.zip")
    assert _split_oss_uri("") == ("", "")


def test_bootstrap_fetches_from_oss_not_urlretrieve():
    cfg = EciCarrierConfig(
        oss_code_uri="oss://b/clousight-bench/run-x/cb-probe.zip",
        region="cn-hangzhou", port=9000, run_id="run-x")
    carrier = EciProbeCarrier(sdk=_NoSdk(), config=cfg,
                              health_check=lambda u: True,
                              sleep=lambda s: None, now=lambda: 0.0)
    req = carrier._build_create_request()
    c = req["container"][0]
    boot = c["command"][-1]
    # the placeholder urlretrieve on an oss:// URL is GONE
    assert "urlretrieve" not in boot
    # a real oss2 download is present
    assert "oss2" in boot
    assert "get_object_to_file" in boot or "get_object" in boot
    # bucket + key + region delivered via env, parsed from oss_code_uri
    env_kv = {e["key"]: e["value"] for e in c["environment_var"]}
    assert env_kv["CB_PROBE_CODE_BUCKET"] == "b"
    assert env_kv["CB_PROBE_CODE_KEY"] == "clousight-bench/run-x/cb-probe.zip"
    assert env_kv["CB_PROBE_REGION"] == "cn-hangzhou"
    # still launches the probe server after extracting
    assert "clousight_bench.domains.agent_runtime.probe.server" in boot


def test_bootstrap_shell_syntax_is_valid():
    """Regression guard: the assembled bootstrap command must parse cleanly under
    /bin/sh -n (parse-only, no execution, no cloud calls).  This catches the class
    of quoting bugs where a literal double-quote inside `python -c "..."` prematurely
    terminates the outer argument and the container exits on boot."""
    cfg = EciCarrierConfig(
        oss_code_uri="oss://b/clousight-bench/run-x/cb-probe.zip",
        region="cn-hangzhou", port=9000, run_id="run-x")
    carrier = EciProbeCarrier(sdk=_NoSdk(), config=cfg,
                              health_check=lambda u: True,
                              sleep=lambda s: None, now=lambda: 0.0)
    req = carrier._build_create_request()
    c = req["container"][0]
    boot = c["command"][-1]
    result = subprocess.run(["/bin/sh", "-n", "-c", boot], capture_output=True)
    assert result.returncode == 0, (
        f"Bootstrap command fails shell syntax check:\n{boot}\n"
        f"stderr: {result.stderr.decode()}"
    )
