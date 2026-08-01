"""P0-3: value-level scrub of cloud account identifiers in error text.

``redact`` scrubs by KEY name and ``scrub_identity_text`` scrubs the operator's
machine identity. Neither catches a cloud account id / ARN embedded as a VALUE
in an SDK exception message -- which lands verbatim in a stage error and, once a
record is published, leaks the operator's account. This closes that gap.
"""
from clousight_bench.core.record import StageError
from clousight_bench.core.redaction import scrub_cloud_identifiers


def test_aws_arn_is_scrubbed():
    out = scrub_cloud_identifiers(
        "AccessDenied: arn:aws:iam::123456789012:user/bob is not authorized")
    assert "123456789012" not in out
    assert "arn:aws:iam" not in out


def test_aliyun_ram_arn_is_scrubbed():
    out = scrub_cloud_identifiers(
        "not authorized: acs:ram::1234567890123456:role/AgentRunRole")
    assert "1234567890123456" not in out
    assert "acs:ram" not in out


def test_aliyun_resource_arn_is_scrubbed():
    out = scrub_cloud_identifiers(
        "denied on acs:agentrun:cn-hangzhou:1234567890123456:runtime/r-abc")
    assert "1234567890123456" not in out


def test_bare_aliyun_account_uid_is_scrubbed():
    out = scrub_cloud_identifiers("caller uid 1234567890123456 lacks agentrun:InvokeRuntime")
    assert "1234567890123456" not in out


def test_ordinary_numbers_are_preserved():
    out = scrub_cloud_identifiers("latency was 1234 ms; http status 429; retry after 5s")
    assert "1234" in out
    assert "429" in out


def test_stage_error_message_is_scrubbed_before_it_is_stored():
    # The orchestrator wraps every stage error through _scrubbed; a cloud id in
    # the message must be gone by the time it is a StageError to persist.
    from clousight_bench.core.orchestrator import _scrubbed

    err = _scrubbed(StageError(
        stage="EXECUTE", code="denied", type="ClientError",
        message="acs:ram::1234567890123456:role/X denied agentrun:InvokeRuntime",
        retryable=False))
    assert "1234567890123456" not in err.message
