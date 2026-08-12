#!/usr/bin/env python3
"""Reference WorkloadEngine plugin: deterministic wordcount smoke.

Demonstrates the cross-language workload protocol (any executable can implement
it; this one happens to be Python):

    invoked as:  ./run.py --params <json-file>
    emits:       JSONL metric/log/result events on stdout

    {"type": "metric", "name": "throughput_rows_per_s", "value": 1234.5}
    {"type": "log", "message": "..."}
    {"type": "result", "ok": true}     # final line, required
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time

WORDS = ["cloud", "agent", "runtime", "spark", "emr", "bench", "north", "data", "scout", "kimi"]


def emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--params", required=True, help="JSON file with {rows, seed}")
    args = parser.parse_args()

    with open(args.params, encoding="utf-8") as f:
        params = json.load(f)
    rows = int(params.get("rows", 100_000))
    seed = int(params.get("seed", 42))

    rng = random.Random(seed)
    start = time.perf_counter()
    counts: dict[str, int] = {}
    for _ in range(rows):
        n = rng.randint(1, 6)
        for _ in range(n):
            w = rng.choice(WORDS)
            counts[w] = counts.get(w, 0) + 1
    duration_ms = (time.perf_counter() - start) * 1000

    emit({"type": "log", "message": f"counted {sum(counts.values())} words over {rows} rows"})
    emit({"type": "metric", "name": "rows_processed", "value": rows})
    emit({"type": "metric", "name": "distinct_words", "value": len(counts)})
    emit({"type": "metric", "name": "duration_ms", "value": round(duration_ms, 2)})
    emit(
        {
            "type": "metric",
            "name": "throughput_rows_per_s",
            "value": round(rows / (duration_ms / 1000), 2) if duration_ms else 0,
        }
    )
    emit({"type": "result", "ok": True})
    return 0


if __name__ == "__main__":
    sys.exit(main())
