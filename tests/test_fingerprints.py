"""Fingerprints must be deterministic, meaning-sensitive and secret-free."""

from clousight_bench.core.fingerprints import (
    UNKNOWN,
    benchmark_fingerprint,
    environment_fingerprint,
    implementation_fingerprint,
    record_digest,
)
from clousight_bench.core.record import (
    Environment,
    Fingerprints,
    Identity,
    ResultRecord,
    RunInfo,
)
from clousight_bench.core.redaction import REDACTED


def _benchmark(**overrides):
    kwargs = dict(
        task_id="T1.3",
        task_revision="2",
        scorer_revision="2",
        workload="wordcount-py",
        workload_version="0.1.0",
        assets=[
            {
                "name": "corpus",
                "version": "1",
                "source": "bundled",
                "sha256": "ab",
            }
        ],
        params={"rows": 100, "seed": 42},
    )
    kwargs.update(overrides)
    return benchmark_fingerprint(**kwargs)


def _record() -> ResultRecord:
    return ResultRecord(
        run=RunInfo(
            run_id="run-1",
            started_at="2026-07-25T00:00:00Z",
            finished_at="2026-07-25T00:00:01Z",
        ),
        identity=Identity(
            domain="agent-runtime",
            task_id="T1.3",
            task_revision="2",
            scorer_revision="2",
            adapter="local-sim",
            adapter_status="reference",
            core_version="0.2.0",
        ),
        environment=Environment(
            region="",
            mode="local",
            python_version="3.12.0",
            os_name="Linux",
        ),
        fingerprints=Fingerprints(
            benchmark="sha256:a",
            environment="sha256:b",
            implementation="sha256:c",
        ),
        status="completed",
    )


def test_unknown_is_the_literal_migration_sentinel():
    assert UNKNOWN == "unknown"


def test_benchmark_fingerprint_is_stable_and_full_sha256():
    value = _benchmark()
    assert value == _benchmark()
    assert value.startswith("sha256:")
    assert len(value) == len("sha256:") + 64


def test_benchmark_fingerprint_changes_with_every_controlled_input():
    base = _benchmark()
    assert _benchmark(task_id="T2.1") != base
    assert _benchmark(task_revision="3") != base
    assert _benchmark(scorer_revision="3") != base
    assert _benchmark(workload="other") != base
    assert _benchmark(workload_version="0.2.0") != base
    assert _benchmark(params={"rows": 200, "seed": 42}) != base
    assert _benchmark(assets=[]) != base


def test_benchmark_fingerprint_ignores_asset_ordering():
    a = {"name": "a", "version": "1", "source": "bundled", "sha256": "1"}
    b = {"name": "b", "version": "1", "source": "remote", "sha256": "2"}
    assert _benchmark(assets=[a, b]) == _benchmark(assets=[b, a])


def test_benchmark_fingerprint_never_hashes_a_secret_param():
    with_secret = _benchmark(params={"rows": 100, "seed": 42, "api_token": "t1"})
    other_secret = _benchmark(params={"rows": 100, "seed": 42, "api_token": "t2"})
    assert with_secret == other_secret


def test_environment_fingerprint_covers_region_mode_and_facts_only():
    base = environment_fingerprint(
        region="cn-hangzhou", mode="cloud", facts={"runtime": "agentrun"}
    )
    assert base == environment_fingerprint(
        region="cn-hangzhou", mode="cloud", facts={"runtime": "agentrun"}
    )
    assert base != environment_fingerprint(
        region="cn-beijing", mode="cloud", facts={"runtime": "agentrun"}
    )
    assert base != environment_fingerprint(
        region="cn-hangzhou", mode="local", facts={"runtime": "agentrun"}
    )
    assert base != environment_fingerprint(
        region="cn-hangzhou", mode="cloud", facts={"runtime": "other"}
    )
    assert base != environment_fingerprint(
        region="cn-hangzhou", mode="cloud", facts={"runtime": "agentrun"},
        execution="live"
    )


def test_implementation_fingerprint_covers_core_domain_adapter_and_plugins():
    base = implementation_fingerprint(
        core_version="0.2.0",
        domain="agent-runtime",
        adapter="local-sim",
        adapter_status="reference",
        plugin_versions={"clousight-bench": "0.2.0"},
    )
    assert base != implementation_fingerprint(
        core_version="0.2.1",
        domain="agent-runtime",
        adapter="local-sim",
        adapter_status="reference",
        plugin_versions={"clousight-bench": "0.2.0"},
    )
    assert base != implementation_fingerprint(
        core_version="0.2.0",
        domain="agent-runtime",
        adapter="local-sim",
        adapter_status="reference",
        plugin_versions={"clousight-bench": "0.2.0", "cb-pricing": "0.1.0"},
    )


def test_all_free_form_maps_exclude_secrets():
    assert environment_fingerprint(
        region="r", mode="cloud", facts={"api_token": "first"}
    ) == environment_fingerprint(
        region="r", mode="cloud", facts={"api_token": "second"}
    )
    assert implementation_fingerprint(
        core_version="1",
        domain="d",
        adapter="a",
        adapter_status="reference",
        plugin_versions={"credential_plugin": "first"},
    ) == implementation_fingerprint(
        core_version="1",
        domain="d",
        adapter="a",
        adapter_status="reference",
        plugin_versions={"credential_plugin": "second"},
    )
    assert record_digest({"api_token": "first"}) == record_digest(
        {"api_token": "second"}
    )


def test_free_form_maps_exclude_current_machine_identity(monkeypatch):
    import clousight_bench.core.redaction as redaction

    monkeypatch.setattr(redaction.getpass, "getuser", lambda: "machine-user")
    monkeypatch.setattr(redaction.socket, "gethostname", lambda: "machine-host")
    monkeypatch.setattr(redaction.socket, "getfqdn", lambda: "machine-fqdn")
    first = environment_fingerprint(
        region="r", mode="cloud", facts={"operator": "machine-user"}
    )

    monkeypatch.setattr(redaction.getpass, "getuser", lambda: "other-user")
    monkeypatch.setattr(redaction.socket, "gethostname", lambda: "other-host")
    monkeypatch.setattr(redaction.socket, "getfqdn", lambda: "other-fqdn")
    second = environment_fingerprint(
        region="r", mode="cloud", facts={"operator": "other-user"}
    )
    assert first == second


def test_fixed_fingerprint_fields_exclude_current_machine_identity(monkeypatch):
    import clousight_bench.core.redaction as redaction

    monkeypatch.setattr(redaction.getpass, "getuser", lambda: "machine-user")
    monkeypatch.setattr(redaction.socket, "gethostname", lambda: "machine-host")
    monkeypatch.setattr(redaction.socket, "getfqdn", lambda: "machine-fqdn")
    first = environment_fingerprint(region="machine-host", mode="cloud", facts={})

    monkeypatch.setattr(redaction.getpass, "getuser", lambda: "other-user")
    monkeypatch.setattr(redaction.socket, "gethostname", lambda: "other-host")
    monkeypatch.setattr(redaction.socket, "getfqdn", lambda: "other-fqdn")
    second = environment_fingerprint(region="other-host", mode="cloud", facts={})
    assert first == second


def test_record_digest_excludes_itself_and_is_stable():
    payload = _record().to_dict()
    first = record_digest(payload)
    payload["fingerprints"]["record_digest"] = first
    assert record_digest(payload) == first


def test_record_digest_does_not_mutate_the_payload():
    payload = _record().to_dict()
    payload["fingerprints"]["record_digest"] = "sha256:old"
    record_digest(payload)
    assert payload["fingerprints"]["record_digest"] == "sha256:old"


def test_redaction_constant_is_the_one_used_by_fingerprints():
    assert REDACTED == "<redacted>"
