"""sanitize_for_log must make an attacker-controlled value un-forgeable in a log."""

from __future__ import annotations

import logging

import pytest

from clousight_bench.core.logsafe import DEFAULT_LIMIT, sanitize_for_log


@pytest.mark.parametrize(
    "hostile",
    [
        "run-1\nERROR:root:forged entry",
        "run-1\r\nERROR:root:forged entry",
        "run-1\rERROR:root:forged entry",
        "run-1 forged",  # LINE SEPARATOR: a record boundary for some parsers
    ],
)
def test_no_newline_survives(hostile: str) -> None:
    out = sanitize_for_log(hostile)
    assert "\n" not in out and "\r" not in out and " " not in out
    assert out.startswith("run-1")


def test_control_chars_are_escaped_not_dropped() -> None:
    # Escaped, so the value is still recognisable in the log; inert, so an ANSI
    # escape cannot repaint the operator's terminal.
    assert sanitize_for_log("\x1b[31mred\x1b[0m") == "\\x1b[31mred\\x1b[0m"
    assert sanitize_for_log("a\tb") == "a\\x09b"


def test_ordinary_values_pass_through_unchanged() -> None:
    assert sanitize_for_log("swe-bench-20260906-abc123") == "swe-bench-20260906-abc123"
    assert sanitize_for_log("目标/路径 ok") == "目标/路径 ok"


def test_length_is_capped_so_one_request_cannot_flood_the_log() -> None:
    out = sanitize_for_log("x" * 10_000)
    assert out == "x" * DEFAULT_LIMIT + "...(truncated)"
    assert sanitize_for_log("abcdef", limit=3) == "abc...(truncated)"


def test_non_str_values_are_coerced() -> None:
    assert sanitize_for_log(42) == "42"
    assert sanitize_for_log(ValueError("bad\nline")) == "bad\\nline"


def test_viewer_never_logs_a_forged_line(tmp_path, caplog) -> None:
    """End-to-end: a hostile run_id in the API path stays on one log line."""
    from clousight_bench.viewer.data import load_trajectory

    caplog.set_level(logging.DEBUG)
    load_trajectory(tmp_path, "run-1\nCRITICAL:root:forged")
    assert all("\n" not in rec.getMessage() for rec in caplog.records)
