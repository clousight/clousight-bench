# Benchmark agent artifact

The tiny agent deployed onto a managed runtime (Aliyun AgentRun / AWS Bedrock
AgentCore) as the payload under test. It adds no intelligence: on each invoke it
makes exactly the one tool call the harness requested, against the pinned mock
tool universe at `mock_base_url`. The **runtime** is the only variable.

## Invoke contract

```
request  = {"tool": {"target","method","params","body"}, "mock_base_url": "..."}
response = {"ok": bool, "status": int, "tool_target": str}
```

`handle_invoke(body) -> dict` in `agent.py` is the pure core; `agent.py` also
runs it behind a bare HTTP server for local self-test. Standard library only.

## Build & deploy (harness-owned)

This payload ships as **package data**. The provisioner owns its whole lifecycle
(`artifact.py`): on a real run it zips this directory, uploads it to OSS under a
unique key, hands the reference to `CreateAgentRuntime`, and **deletes the object
on teardown** — so a run needs only credentials + a bucket (`target.oss_bucket`),
not a hand-uploaded zip, and leaves nothing behind.

To build the zip yourself (manual upload / inspection):

```python
from clousight_bench.domains.agent_runtime.artifact import build_agent_zip
build_agent_zip("dist/clousight-bench-agent.zip")
```

Then pass `target.artifact_ref = "oss://bucket/key"` to skip the managed upload.

## Local self-test (no cloud)

`tests/test_agent_artifact.py` starts the pinned mock universe + this agent's
invoke core in process and drives an invoke end-to-end, proving the agent really
calls the tool universe and reports the tool's own status.

## Live deployment note

The platform code-package **entrypoint convention** (handler name / ASGI app vs
bare server) is confirmed on the first live run. If the platform requires a
specific entry declaration, wrap `handle_invoke` in that entrypoint and adjust
the packaged files in `artifact.py` (`_INCLUDE`) -- the core logic does not change.
