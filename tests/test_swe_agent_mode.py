"""SWE mode of the deployed agent bundle: oracle echoes the gold patch, llm calls
DashScope (monkeypatched here — no live network) and extracts a unified diff.
Driven in-process through ``handle_chat_completion`` like the other agent tests."""

import json

import pytest

from clousight_bench.domains.agent_runtime import protocol as p
from clousight_bench.domains.agent_runtime.agent_bundle import agent

GOLD = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"

INSTANCE = {
    "instance_id": "django__django-11099",
    "repo": "django/django",
    "base_commit": "abc123",
    "problem_statement": "UsernameValidator allows trailing newline in usernames",
    "hints_text": "regex $ matches before a trailing newline",
    "patch": GOLD,
}

ZERO_USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._data = json.dumps(payload).encode("utf-8")
        self.status = 200

    def read(self) -> bytes:
        return self._data

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _dashscope_reply(content: str, usage: dict | None = None) -> dict:
    body: dict = {"choices": [{"message": {"role": "assistant", "content": content}}]}
    if usage is not None:
        body["usage"] = usage
    return body


def _invoke(body: dict) -> dict:
    return p.decode_swe_result(agent.handle_chat_completion(body))


# ---------------------------------------------------------------------------
# oracle mode
# ---------------------------------------------------------------------------


def test_oracle_mode_returns_gold_patch_verbatim_with_chain_span():
    body = p.encode_swe_invoke(INSTANCE, agent_mode="oracle")
    result = _invoke(body)
    assert result["model_patch"] == GOLD
    assert result["usage"] == ZERO_USAGE
    (span,) = result["spans"]
    assert span["name"] == "swe-oracle"
    assert span["kind"] == "CHAIN"
    assert span["status"] == "ok"
    assert span["attributes"]["openinference.span.kind"] == "CHAIN"
    assert span["attributes"]["swe.instance_id"] == "django__django-11099"
    assert span["trace_id"] and span["span_id"]


# ---------------------------------------------------------------------------
# llm mode (DashScope monkeypatched — never live)
# ---------------------------------------------------------------------------


def _patch_dashscope(monkeypatch: pytest.MonkeyPatch, reply: dict, calls: list) -> None:
    def fake_urlopen(req, timeout=0):
        calls.append(req)
        return _FakeResp(reply)

    monkeypatch.setattr(agent.urlrequest, "urlopen", fake_urlopen)


def test_llm_mode_calls_dashscope_and_returns_diff_and_usage(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    usage = {"prompt_tokens": 120, "completion_tokens": 45, "total_tokens": 165}
    calls: list = []
    _patch_dashscope(monkeypatch, _dashscope_reply(GOLD, usage), calls)

    body = p.encode_swe_invoke(INSTANCE, agent_mode="llm", llm_model="qwen-max")
    result = _invoke(body)

    assert result["model_patch"] == GOLD.strip()
    assert result["usage"] == usage
    (span,) = result["spans"]
    assert span["name"] == "swe-llm"
    assert span["kind"] == "LLM"
    assert span["status"] == "ok"
    assert span["attributes"]["llm.model_name"] == "qwen-max"

    (req,) = calls
    assert req.full_url == agent.DASHSCOPE_URL
    assert req.get_header("Authorization") == "Bearer sk-test"
    sent = json.loads(req.data.decode("utf-8"))
    assert sent["model"] == "qwen-max"
    assert sent["messages"][0]["role"] == "system"
    assert "diff" in sent["messages"][0]["content"]
    assert INSTANCE["problem_statement"] in sent["messages"][1]["content"]
    assert INSTANCE["hints_text"] in sent["messages"][1]["content"]


def test_llm_mode_request_never_contains_gold_patch(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    calls: list = []
    _patch_dashscope(monkeypatch, _dashscope_reply("no diff here"), calls)
    _invoke(p.encode_swe_invoke(INSTANCE, agent_mode="llm"))
    (req,) = calls
    assert GOLD not in req.data.decode("utf-8")


def test_llm_mode_strips_code_fences(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    reply = f"```diff\n{GOLD}```"
    _patch_dashscope(monkeypatch, _dashscope_reply(reply), [])
    result = _invoke(p.encode_swe_invoke(INSTANCE, agent_mode="llm"))
    assert result["model_patch"] == GOLD.strip()


def test_llm_mode_missing_usage_defaults_to_zeros(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    _patch_dashscope(monkeypatch, _dashscope_reply(GOLD, usage=None), [])
    result = _invoke(p.encode_swe_invoke(INSTANCE, agent_mode="llm"))
    assert result["usage"] == ZERO_USAGE


def test_llm_mode_without_api_key_yields_error_span_not_exception(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    def boom(*a, **kw):  # any HTTP attempt without a key is a bug
        raise AssertionError("must not call DashScope without a key")

    monkeypatch.setattr(agent.urlrequest, "urlopen", boom)
    result = _invoke(p.encode_swe_invoke(INSTANCE, agent_mode="llm"))
    assert result["model_patch"] == ""
    assert result["usage"] == ZERO_USAGE
    (span,) = result["spans"]
    assert span["name"] == "swe-llm"
    assert span["status"] == "error"
    assert span["error"] == "DASHSCOPE_API_KEY not set"
    assert span["attributes"]["error"] == "DASHSCOPE_API_KEY not set"


def test_llm_mode_http_failure_yields_error_span_not_exception(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")

    def fail(req, timeout=0):
        raise OSError("connection timed out")

    monkeypatch.setattr(agent.urlrequest, "urlopen", fail)
    result = _invoke(p.encode_swe_invoke(INSTANCE, agent_mode="llm"))
    assert result["model_patch"] == ""
    assert result["usage"] == ZERO_USAGE
    (span,) = result["spans"]
    assert span["status"] == "error"
    assert "connection timed out" in span["error"]


# ---------------------------------------------------------------------------
# diff extraction unit tests
# ---------------------------------------------------------------------------


def test_extract_diff_plain_reply_passes_through():
    assert agent._extract_diff(f"  {GOLD}  ") == GOLD.strip()


def test_extract_diff_plain_fence():
    assert agent._extract_diff(f"```\n{GOLD}\n```") == GOLD.strip()


def test_extract_diff_fence_with_preamble_and_trailer():
    reply = f"Here is the fix:\n```diff\n{GOLD}\n```\nHope this helps!"
    assert agent._extract_diff(reply) == GOLD.strip()


def test_extract_diff_takes_from_first_diff_line_onward():
    reply = f"I analysed the bug.\n{GOLD}"
    assert agent._extract_diff(reply) == GOLD.strip()


def test_extract_diff_takes_from_first_minus_minus_minus_line():
    patch = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b"
    assert agent._extract_diff(f"Sure!\n{patch}") == patch


def test_extract_diff_no_markers_returns_empty():
    """Prose with no diff marker is NOT a patch — empty means "no patch produced"."""
    assert agent._extract_diff("  I cannot produce a patch.  ") == ""


def test_extract_diff_marker_wins_over_earlier_prose_fence():
    reply = (
        "The bug is here: ```python\nx = 1\n```\n"
        "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-x = 1\n+x = 2"
    )
    out = agent._extract_diff(reply)
    assert out.startswith("diff --git a/f.py")
    assert "+x = 2" in out


def test_extract_diff_fence_char_inside_diff_body_not_truncating():
    reply = (
        "```diff\ndiff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n"
        "@@ -1,2 +1,2 @@\n-```python\n+```py\n```"
    )
    out = agent._extract_diff(reply)
    # The +```py content line survives; only the trailing line-start fence is dropped.
    assert "+```py" in out
    assert not out.endswith("```") or out.endswith("+```py")


def test_handle_chat_completion_non_dict_swe_degrades(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    body = {"messages": [{"role": "user", "content": json.dumps({"swe": "malicious"})}]}
    resp = agent.handle_chat_completion(body)  # must not raise
    decoded = p.decode_swe_result(resp)
    assert decoded["model_patch"] == ""


# ---------------------------------------------------------------------------
# the existing tool-plan path is untouched
# ---------------------------------------------------------------------------


def test_tool_plan_payload_still_routes_to_stub_path(monkeypatch):
    seen: dict = {}

    def fake_handle_invoke(req):
        seen.update(req)
        return {"ok": True, "status": 200, "tool_target": req["tool"]["target"]}

    monkeypatch.setattr(agent, "handle_invoke", fake_handle_invoke)
    body = p.encode_invoke({"target": "prices", "method": "GET"}, "http://mock")
    result = p.decode_result(agent.handle_chat_completion(body))
    assert result["tool_target"] == "prices"
    assert "swe" not in seen
