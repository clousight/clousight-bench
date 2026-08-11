from clousight_bench.domains.agent_runtime import protocol as p


def test_invoke_request_round_trip():
    tool = {"target": "prices", "method": "GET", "params": {"provider": "aliyun"}, "body": {}}
    body = p.encode_invoke(tool, "https://mock.example.com")
    got = p.decode_request(body)
    assert got == {"tool": tool, "mock_base_url": "https://mock.example.com",
                   "mock_token": ""}


def test_result_round_trip():
    result = {"ok": True, "status": 200, "tool_target": "prices"}
    resp = p.encode_result(result)
    assert p.decode_result(resp) == result


def test_decode_request_tolerates_missing():
    assert p.decode_request({"messages": []}) == {"tool": {}, "mock_base_url": "",
                                                   "mock_token": ""}
