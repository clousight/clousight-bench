#!/usr/bin/env python3
"""Synthetic sampler workload: emit `count` latency samples then a result line.

Reference for the `sample` protocol event. Bundled reference workloads are
invoked as standalone executables under a bare `python3`, so this is stdlib-only
and emits the events directly. A Python workload that runs inside an environment
with clousight-bench installed can instead reuse
`clousight_bench.core.sampling.HighFreqSampler`, which owns the same protocol.
"""
import json
import random
import sys
import time


def emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def main() -> int:
    count = 20
    if "--params" in sys.argv:
        with open(sys.argv[sys.argv.index("--params") + 1], encoding="utf-8") as fh:
            params = json.load(fh)
        count = int(params.get("count", 20))
    for _ in range(count):
        emit({"type": "sample", "series": "latency_ms", "t": time.time(),
              "value": random.uniform(50, 150)})
    emit({"type": "result", "ok": True})
    return 0


if __name__ == "__main__":
    sys.exit(main())
