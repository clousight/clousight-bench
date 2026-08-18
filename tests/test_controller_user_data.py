"""Test the prod controller cloud-init user-data build."""

import base64

from clousight_bench.domains.agent_runtime.ecs_carrier import build_controller_user_data


def test_controller_user_data_installs_store_and_runs_controller():
    ud = build_controller_user_data(bucket="bench-bkt", region="cn-hangzhou", campaign_id="camp-1")
    script = base64.b64decode(ud).decode()
    # installs the [probe,store] extra so parquet sidecars can be written in-cloud
    assert "clousight-bench[probe,store]" in script
    # runs the controller entrypoint, not the probe loop
    assert "clousight_bench.core.controller_main" in script
    # exposes the CB_* env vars controller_main.build reads
    assert "export CB_CAMPAIGN_ID='camp-1'" in script
    assert "export CB_OSS_BUCKET='bench-bkt'" in script
    assert "export CB_REGION='cn-hangzhou'" in script


def test_controller_user_data_installs_extra_deps_first():
    ud = build_controller_user_data(
        bucket="b", region="r", campaign_id="c", extra_deps=["duckdb>=1.0", "pyarrow>=16"]
    )
    script = base64.b64decode(ud).decode()
    assert "'duckdb>=1.0'" in script and "'pyarrow>=16'" in script
    # extra deps come before the code_spec line
    assert script.index("duckdb>=1.0") < script.index("clousight-bench[probe,store]")
