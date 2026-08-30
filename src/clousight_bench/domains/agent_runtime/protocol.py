"""Shared OpenAI wire contract between the transport and the deployed agent.

AgentRun invoke is OpenAI /chat/completions-shaped. The tool plan travels as a
JSON user message; the result returns as the assistant message content. This one
module is imported by BOTH sides so they can never drift. Stdlib only.
"""

from __future__ import annotations

import json
from typing import Any

MODEL = "clousight-bench"


def encode_invoke(
    tool: dict[str, Any],
    mock_base_url: str,
    mock_token: str | None = None,
    arms_config: dict[str, Any] | None = None,
    fail_after_n_calls: int = 0,
    session_id: str = "",
    correlation_id: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {"tool": tool, "mock_base_url": mock_base_url}
    if mock_token:
        payload["mock_token"] = mock_token
    if arms_config:
        payload["arms_config"] = arms_config
    if fail_after_n_calls:
        payload["fail_after_n_calls"] = fail_after_n_calls
    if session_id:
        payload["_session_id"] = session_id
    if correlation_id:
        payload["_correlation_id"] = correlation_id
    return {"model": MODEL, "messages": [{"role": "user", "content": json.dumps(payload)}]}


def decode_request(openai_body: dict[str, Any]) -> dict[str, Any]:
    messages = openai_body.get("messages") or []
    content = ""
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = str(msg.get("content") or "")
    try:
        payload = json.loads(content) if content else {}
    except (ValueError, TypeError):
        payload = {}
    result: dict[str, Any] = {
        "tool": payload.get("tool") or {},
        "mock_base_url": str(payload.get("mock_base_url") or ""),
        "mock_token": str(payload.get("mock_token") or ""),
        "_correlation_id": str(payload.get("_correlation_id") or ""),
    }
    if payload.get("arms_config"):
        result["arms_config"] = payload["arms_config"]
    if payload.get("fail_after_n_calls"):
        result["fail_after_n_calls"] = int(payload["fail_after_n_calls"])
    if payload.get("_session_id"):
        result["_session_id"] = str(payload["_session_id"])
    if payload.get("swe"):
        result["swe"] = payload["swe"]
    return result


def encode_result(result: dict[str, Any]) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": json.dumps(result)}}]}


def decode_result(openai_resp: dict[str, Any]) -> dict[str, Any]:
    choices = openai_resp.get("choices") or []
    if not choices:
        return {}
    content = str(choices[0].get("message", {}).get("content") or "")
    try:
        return json.loads(content) if content else {}
    except (ValueError, TypeError):
        return {}


def encode_swe_invoke(
    instance: dict[str, Any],
    *,
    agent_mode: str,
    llm_model: str = "qwen-plus",
) -> dict[str, Any]:
    """OpenAI chat body carrying a SWE-bench instance for the deployed agent.

    The user message content is JSON ``{"swe": {...}}``. ``gold_patch`` is
    included ONLY in oracle mode — llm mode must never leak the answer into
    the runtime.

    SWE-bench Multimodal instances carry an ``image_assets`` field (a JSON
    string of image URLs keyed by ``problem_statement``/``patch``/``test_patch``);
    when present it is forwarded verbatim so a multimodal agent can fetch the
    screenshots. Text-only splits (Verified/Lite) omit the field, so the payload
    is unchanged for them.
    """
    swe: dict[str, Any] = {
        "instance_id": str(instance.get("instance_id") or ""),
        "problem_statement": str(instance.get("problem_statement") or ""),
        "hints": str(instance.get("hints_text") or ""),
        "agent_mode": agent_mode,
        "llm_model": llm_model,
    }
    image_assets = instance.get("image_assets")
    if image_assets:
        swe["image_assets"] = image_assets
    if agent_mode == "oracle":
        swe["gold_patch"] = str(instance.get("patch") or "")
    return {"model": MODEL, "messages": [{"role": "user", "content": json.dumps({"swe": swe})}]}


def decode_swe_result(response_body: dict[str, Any]) -> dict[str, Any]:
    """Decode the agent's SWE response, tolerant of missing keys.

    Returns ``{"model_patch": str, "spans": list, "usage": {prompt/completion/
    total_tokens ints}}`` with ""/[]/zeros defaults.
    """
    result = decode_result(response_body)
    raw_usage = result.get("usage") or {}
    usage: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        try:
            usage[key] = int(raw_usage.get(key) or 0)
        except (TypeError, ValueError):
            usage[key] = 0
    spans = result.get("_spans")
    return {
        "model_patch": str(result.get("model_patch") or ""),
        "spans": list(spans) if isinstance(spans, list) else [],
        "usage": usage,
    }


def encode_invoke_stream(
    tool: dict[str, Any],
    mock_base_url: str,
    mock_token: str | None = None,
    arms_config: dict[str, Any] | None = None,
    fail_after_n_calls: int = 0,
    session_id: str = "",
    correlation_id: str = "",
) -> dict[str, Any]:
    """Like encode_invoke but sets stream=True so the agent responds with SSE chunks.

    Use this when measuring TTFT: the first SSE chunk arrives before the full
    response, enabling TTFT = time-to-first-non-empty-chunk measurement.
    """
    body = encode_invoke(
        tool,
        mock_base_url,
        mock_token=mock_token,
        arms_config=arms_config,
        fail_after_n_calls=fail_after_n_calls,
        session_id=session_id,
        correlation_id=correlation_id,
    )
    body["stream"] = True
    return body
