"""Tests for clousight_bench.core.sut_span.

TDD coverage:
- validate_span accepts well-formed tool_call and llm_call spans
- validate_span rejects: bad kind, t_end < t_start, missing required key
- The mock agent's canned trajectory.jsonl is a list of valid spans
- Schema v2: trace_id/status required; bool rejected for t_start/t_end;
  parent_id empty-string rejected; oversized/non-serializable attrs rejected
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clousight_bench.core.sut_span import MAX_ATTRS_BYTES, SPAN_KINDS, SPAN_STATUSES, validate_span

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

_GOOD_LLM_CALL = {
    "span_id": "span-001",
    "trace_id": "trace-0001",
    "parent_id": None,
    "name": "agent.llm_call",
    "kind": "llm_call",
    "t_start": 1_000.0,
    "t_end": 1_010.5,
    "status": "ok",
    "attrs": {"model": "gpt-4o", "prompt_tokens": 512},
}

_GOOD_TOOL_CALL = {
    "span_id": "span-002",
    "trace_id": "trace-0001",
    "parent_id": "span-001",
    "name": "agent.apply_patch",
    "kind": "tool_call",
    "t_start": 1_010.5,
    "t_end": 1_010.8,
    "status": "ok",
    "attrs": {"tool": "bash", "exit_code": 0},
}

# ---------------------------------------------------------------------------
# SPAN_KINDS
# ---------------------------------------------------------------------------


def test_span_kinds_contains_required_values() -> None:
    assert "tool_call" in SPAN_KINDS
    assert "llm_call" in SPAN_KINDS


# ---------------------------------------------------------------------------
# validate_span — happy path
# ---------------------------------------------------------------------------


def test_validate_span_accepts_llm_call() -> None:
    validate_span(_GOOD_LLM_CALL)  # must not raise


def test_validate_span_accepts_tool_call() -> None:
    validate_span(_GOOD_TOOL_CALL)  # must not raise


def test_validate_span_accepts_root_span_with_none_parent_id() -> None:
    span = dict(_GOOD_LLM_CALL, parent_id=None)
    validate_span(span)


def test_validate_span_accepts_t_end_equal_t_start() -> None:
    span = dict(_GOOD_TOOL_CALL, t_start=5.0, t_end=5.0)
    validate_span(span)


def test_validate_span_allows_extra_keys() -> None:
    span = dict(_GOOD_LLM_CALL, custom_field="extra")
    validate_span(span)  # must not raise


# ---------------------------------------------------------------------------
# validate_span — rejection cases
# ---------------------------------------------------------------------------


def test_validate_span_rejects_bad_kind() -> None:
    span = dict(_GOOD_LLM_CALL, kind="unknown_kind")
    with pytest.raises(ValueError, match="kind"):
        validate_span(span)


def test_validate_span_rejects_t_end_before_t_start() -> None:
    span = dict(_GOOD_LLM_CALL, t_start=100.0, t_end=99.9)
    with pytest.raises(ValueError, match="t_end"):
        validate_span(span)


@pytest.mark.parametrize(
    "missing_key",
    ["span_id", "parent_id", "name", "kind", "t_start", "t_end", "attrs"],
)
def test_validate_span_rejects_missing_key(missing_key: str) -> None:
    span = dict(_GOOD_LLM_CALL)
    del span[missing_key]
    with pytest.raises(ValueError, match="missing"):
        validate_span(span)


def test_validate_span_rejects_non_dict() -> None:
    with pytest.raises(ValueError, match="dict"):
        validate_span(["not", "a", "dict"])  # type: ignore[arg-type]


def test_validate_span_rejects_non_dict_attrs() -> None:
    span = dict(_GOOD_LLM_CALL, attrs="not-a-dict")
    with pytest.raises(ValueError, match="attrs"):
        validate_span(span)


def test_validate_span_rejects_non_numeric_t_start() -> None:
    span = dict(_GOOD_LLM_CALL, t_start="yesterday")
    with pytest.raises(ValueError, match="t_start"):
        validate_span(span)


def test_validate_span_rejects_non_numeric_t_end() -> None:
    span = dict(_GOOD_LLM_CALL, t_end="now")
    with pytest.raises(ValueError, match="t_end"):
        validate_span(span)


# ---------------------------------------------------------------------------
# Mock agent trajectory fixture — all spans validate
# ---------------------------------------------------------------------------

_TRAJECTORY_FIXTURE = (
    Path(__file__).parent.parent
    / "src"
    / "clousight_bench"
    / "suites"
    / "swe_bench"
    / "fixtures"
    / "trajectory.jsonl"
)


def test_mock_trajectory_fixture_is_valid() -> None:
    """The canned trajectory.jsonl contains ≥2 spans that all pass validate_span."""
    lines = [ln for ln in _TRAJECTORY_FIXTURE.read_text().splitlines() if ln.strip()]
    assert len(lines) >= 2, "trajectory.jsonl must have ≥2 spans"
    spans = [json.loads(line) for line in lines]
    for span in spans:
        validate_span(span)


def test_mock_trajectory_has_llm_call_and_tool_call() -> None:
    """The canned trajectory includes at least one llm_call and one tool_call."""
    lines = [ln for ln in _TRAJECTORY_FIXTURE.read_text().splitlines() if ln.strip()]
    spans = [json.loads(line) for line in lines]
    kinds = {span["kind"] for span in spans}
    assert "llm_call" in kinds
    assert "tool_call" in kinds


# ---------------------------------------------------------------------------
# Schema v2 — new exports
# ---------------------------------------------------------------------------


def test_span_statuses_exported() -> None:
    """SPAN_STATUSES must be exported and include 'ok' and 'error'."""
    assert "ok" in SPAN_STATUSES
    assert "error" in SPAN_STATUSES


def test_max_attrs_bytes_exported() -> None:
    """MAX_ATTRS_BYTES must be exported as an int."""
    assert isinstance(MAX_ATTRS_BYTES, int)
    assert MAX_ATTRS_BYTES > 0


# ---------------------------------------------------------------------------
# Schema v2 — happy path
# ---------------------------------------------------------------------------


def test_validate_span_accepts_v2_span() -> None:
    """A fully-valid v2 span (with trace_id and status) must pass."""
    validate_span(_GOOD_LLM_CALL)  # must not raise


def test_validate_span_accepts_error_status_with_error_field() -> None:
    """status='error' with a string 'error' field is valid."""
    span = dict(_GOOD_LLM_CALL, status="error", error="something went wrong")
    validate_span(span)


def test_validate_span_accepts_error_status_without_error_field() -> None:
    """status='error' without an 'error' field is still valid (error field optional)."""
    span = dict(_GOOD_LLM_CALL, status="error")
    validate_span(span)


# ---------------------------------------------------------------------------
# Schema v2 — rejection cases
# ---------------------------------------------------------------------------


def test_validate_span_rejects_missing_trace_id() -> None:
    """A span without 'trace_id' must be rejected."""
    span = dict(_GOOD_LLM_CALL)
    del span["trace_id"]
    with pytest.raises(ValueError, match="missing"):
        validate_span(span)


def test_validate_span_rejects_empty_trace_id() -> None:
    """trace_id must be non-empty."""
    span = dict(_GOOD_LLM_CALL, trace_id="")
    with pytest.raises(ValueError, match="trace_id"):
        validate_span(span)


def test_validate_span_rejects_missing_status() -> None:
    """A span without 'status' must be rejected."""
    span = dict(_GOOD_LLM_CALL)
    del span["status"]
    with pytest.raises(ValueError, match="missing"):
        validate_span(span)


def test_validate_span_rejects_bad_status() -> None:
    """status='weird' must be rejected."""
    span = dict(_GOOD_LLM_CALL, status="weird")
    with pytest.raises(ValueError, match="status"):
        validate_span(span)


def test_validate_span_rejects_non_string_error_field() -> None:
    """If status='error' and 'error' key present, it must be a str."""
    span = dict(_GOOD_LLM_CALL, status="error", error=42)
    with pytest.raises(ValueError, match="error"):
        validate_span(span)


def test_validate_span_rejects_bool_t_start() -> None:
    """t_start=True must be rejected with a message mentioning bool."""
    span = dict(_GOOD_LLM_CALL, t_start=True)
    with pytest.raises(ValueError, match="bool"):
        validate_span(span)


def test_validate_span_rejects_bool_t_end() -> None:
    """t_end=False must be rejected with a message mentioning bool."""
    span = dict(_GOOD_LLM_CALL, t_end=False)
    with pytest.raises(ValueError, match="bool"):
        validate_span(span)


def test_validate_span_rejects_empty_parent_id() -> None:
    """parent_id='' must be rejected (empty string is not a valid non-None parent_id)."""
    span = dict(_GOOD_LLM_CALL, parent_id="")
    with pytest.raises(ValueError, match="parent_id"):
        validate_span(span)


def test_validate_span_rejects_oversized_attrs() -> None:
    """attrs that serialise to >MAX_ATTRS_BYTES must be rejected."""
    big_val = "x" * (MAX_ATTRS_BYTES + 1)
    span = dict(_GOOD_LLM_CALL, attrs={"k": big_val})
    with pytest.raises(ValueError, match="attrs"):
        validate_span(span)


def test_validate_span_rejects_non_serializable_attrs() -> None:
    """attrs containing a non-JSON-serializable value must raise ValueError."""
    span = dict(_GOOD_LLM_CALL, attrs={"x": object()})
    with pytest.raises(ValueError, match="attrs"):
        validate_span(span)
