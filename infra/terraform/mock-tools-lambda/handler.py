"""
Clousight Bench — mock tool server (AWS Lambda).

Returns deterministic fixture JSON for any path the benchmark agent calls
(e.g. /prices, /weather, /search).  An optional X-Clousight-Token header
check provides a thin auth layer; the token is injected via CSBENCH_MOCK_TOKEN.
"""

import json
import os

_TOKEN = os.environ.get("CSBENCH_MOCK_TOKEN", "")

# Minimal per-path fixtures — extend as new task suites are added.
_FIXTURES: dict[str, object] = {
    "/prices": {"price": 1.23, "currency": "USD", "symbol": "MOCK"},
    "/weather": {"temperature_c": 22.0, "condition": "sunny", "location": "mock-city"},
    "/search": {"results": [{"title": "Mock Result", "url": "https://example.com"}]},
}
_DEFAULT_FIXTURE = {"status": "ok", "data": {"value": 42}}


def lambda_handler(event: dict, context: object) -> dict:
    # Token auth (application-layer; function URL has auth_type=NONE)
    if _TOKEN:
        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        if headers.get("x-clousight-token", "") != _TOKEN:
            return {
                "statusCode": 401,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "unauthorized"}),
            }

    path = event.get("rawPath") or event.get("path") or "/"
    payload = _FIXTURES.get(path, _DEFAULT_FIXTURE)

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }
