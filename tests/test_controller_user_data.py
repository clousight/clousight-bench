"""Test the prod controller cloud-init user-data build."""

import base64

from clousight_bench.domains.agent_runtime.ecs_carrier import build_controller_user_data


def _script(**kw) -> str:
    return base64.b64decode(build_controller_user_data(**kw)).decode()


def test_controller_user_data_installs_store_and_runs_controller():
    script = _script(bucket="bench-bkt", region="cn-hangzhou", campaign_id="camp-1")
    # installs the [probe,store] extra so parquet sidecars can be written in-cloud
    assert "clousight-bench[probe,store]" in script
    # runs the controller entrypoint, not the probe loop
    assert "clousight_bench.core.controller_main" in script
    # exposes the CB_* env vars controller_main.build reads
    assert "export CB_CAMPAIGN_ID='camp-1'" in script
    assert "export CB_OSS_BUCKET='bench-bkt'" in script
    assert "export CB_REGION='cn-hangzhou'" in script


def test_controller_user_data_installs_extra_deps_first():
    script = _script(bucket="b", region="r", campaign_id="c", extra_deps=["duckdb>=1.0", "pyarrow>=16"])
    assert "'duckdb>=1.0'" in script and "'pyarrow>=16'" in script
    # extra deps come before the code_spec line
    assert script.index("duckdb>=1.0") < script.index("clousight-bench[probe,store]")


def test_controller_user_data_default_flags_byte_identical_to_pre_docker_shape():
    # The docker/HF knobs default OFF: the script must be byte-for-byte what it
    # was before those kwargs existed (no drift for existing campaigns).
    expected = (
        "\n".join(
            [
                "#!/bin/sh",
                "set -e",
                "export CB_CAMPAIGN_ID='c'",
                "export CB_OSS_BUCKET='b'",
                "export CB_REGION='r'",
                "export CB_RESULTS_DIR='/var/lib/cb/results'",
                "export CB_PLATFORM='aliyun-agentrun'",
                "yum install -y 'python3.11'",
                "python3.11 -m ensurepip --upgrade",
                "python3.11 -m pip install -i 'https://mirrors.cloud.aliyuncs.com/pypi/simple/'"
                " 'clousight-bench[probe,store]'",
                "exec python3.11 -m clousight_bench.core.controller_main",
            ]
        )
        + "\n"
    )
    assert _script(bucket="b", region="r", campaign_id="c") == expected


def test_controller_user_data_docker_mirror_and_hf_lines():
    script = _script(
        bucket="b",
        region="r",
        campaign_id="c",
        install_docker=True,
        docker_registry_mirror="https://m.example.com",
        hf_endpoint="https://hf-mirror.com",
    )
    assert "export HF_ENDPOINT='https://hf-mirror.com'" in script
    daemon = 'echo \'{"registry-mirrors": ["https://m.example.com"]}\' > /etc/docker/daemon.json'
    assert "mkdir -p /etc/docker" in script
    assert daemon in script
    install = "yum install -y docker || dnf install -y docker"
    assert install in script
    assert "systemctl enable --now docker" in script
    # daemon.json is written BEFORE docker is installed/started
    assert script.index(daemon) < script.index(install)
    assert script.index(install) < script.index("systemctl enable --now docker")
    # docker setup happens before the python/controller install
    assert script.index("systemctl enable --now docker") < script.index("yum install -y 'python3.11'")


def test_controller_user_data_install_docker_without_mirror():
    script = _script(bucket="b", region="r", campaign_id="c", install_docker=True)
    assert "yum install -y docker || dnf install -y docker" in script
    assert "daemon.json" not in script and "HF_ENDPOINT" not in script
