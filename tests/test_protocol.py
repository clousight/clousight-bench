import json

from clousight_bench.domains.agent_runtime import protocol as p


def test_invoke_request_round_trip():
    tool = {"target": "prices", "method": "GET", "params": {"provider": "aliyun"}, "body": {}}
    body = p.encode_invoke(tool, "https://mock.example.com")
    got = p.decode_request(body)
    assert got == {
        "tool": tool,
        "mock_base_url": "https://mock.example.com",
        "mock_token": "",
        "_correlation_id": "",
    }


def test_result_round_trip():
    result = {"ok": True, "status": 200, "tool_target": "prices"}
    resp = p.encode_result(result)
    assert p.decode_result(resp) == result


def test_decode_request_tolerates_missing():
    assert p.decode_request({"messages": []}) == {
        "tool": {},
        "mock_base_url": "",
        "mock_token": "",
        "_correlation_id": "",
    }


def test_encode_invoke_round_trips_correlation_id():
    tool = {"target": "prices", "method": "GET"}
    body = p.encode_invoke(tool, "https://mock.example.com", correlation_id="corr-abc")
    got = p.decode_request(body)
    assert got["_correlation_id"] == "corr-abc"


_INSTANCE = {
    "instance_id": "astropy__astropy-12907",
    "repo": "astropy/astropy",
    "base_commit": "d16bfe0",
    "problem_statement": "Modeling's separability matrix is wrong for nested CompoundModels",
    "hints_text": "look at _separable.py",
    "patch": "diff --git a/astropy/modeling/separable.py b/astropy/modeling/separable.py\n--- a\n+++ b\n",
}

_ZERO_USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _swe_payload(body):
    return json.loads(body["messages"][0]["content"])["swe"]


def test_encode_swe_invoke_oracle_carries_gold_patch():
    body = p.encode_swe_invoke(_INSTANCE, agent_mode="oracle")
    assert body["model"] == p.MODEL
    swe = _swe_payload(body)
    assert swe == {
        "instance_id": "astropy__astropy-12907",
        "problem_statement": _INSTANCE["problem_statement"],
        "hints": "look at _separable.py",
        "agent_mode": "oracle",
        "llm_model": "qwen-plus",
        "gold_patch": _INSTANCE["patch"],
    }


def test_encode_swe_invoke_llm_mode_never_leaks_gold_patch():
    body = p.encode_swe_invoke(_INSTANCE, agent_mode="llm", llm_model="qwen-max")
    swe = _swe_payload(body)
    assert "gold_patch" not in swe
    assert swe["agent_mode"] == "llm"
    assert swe["llm_model"] == "qwen-max"


def test_encode_swe_invoke_missing_hints_defaults_empty():
    inst = {k: v for k, v in _INSTANCE.items() if k != "hints_text"}
    swe = _swe_payload(p.encode_swe_invoke(inst, agent_mode="oracle"))
    assert swe["hints"] == ""


def test_decode_swe_result_round_trip():
    spans = [{"name": "swe-oracle", "kind": "CHAIN"}]
    usage = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    resp = p.encode_result({"model_patch": "diff --git x", "_spans": spans, "usage": usage})
    assert p.decode_swe_result(resp) == {"model_patch": "diff --git x", "spans": spans, "usage": usage}


def test_decode_swe_result_tolerates_missing_keys():
    assert p.decode_swe_result({}) == {"model_patch": "", "spans": [], "usage": _ZERO_USAGE}
    resp = p.encode_result({"model_patch": "d"})
    assert p.decode_swe_result(resp) == {"model_patch": "d", "spans": [], "usage": _ZERO_USAGE}
