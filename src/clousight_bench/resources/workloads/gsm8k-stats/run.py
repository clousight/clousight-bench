#!/usr/bin/env python3
"""GSM8K corpus-stats workload (WorkloadEngine JSONL protocol, stdlib only).

The engine resolves the manifest's remote asset (downloads + checksum-verifies +
caches GSM8K test.jsonl) and passes its local path in params["assets"]["gsm8k-test"].
This workload just reads that path -- it never fetches anything itself -- and emits
corpus statistics as metric events, proving the asset pipeline end to end.
"""

from __future__ import annotations

import json
import sys
import time


def _params() -> dict:
    if "--params" in sys.argv:
        with open(sys.argv[sys.argv.index("--params") + 1], encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def main() -> int:
    params = _params()
    assets = params.get("assets", {})
    path = assets.get("gsm8k-test")
    if not path:
        print(json.dumps({"type": "log", "message": "no gsm8k-test asset resolved"}))
        print(json.dumps({"type": "result", "ok": False}))
        return 1

    limit = int(params.get("limit", 0) or 0)
    start = time.perf_counter()

    n = 0
    q_chars = 0
    steps = 0
    with_final = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            n += 1
            q_chars += len(row.get("question", ""))
            answer = row.get("answer", "")
            # GSM8K answers are multi-step; the final line is "#### <number>".
            steps += max(0, answer.count("\n"))
            if "####" in answer:
                with_final += 1
            if limit and n >= limit:
                break

    duration_ms = round((time.perf_counter() - start) * 1000, 2)

    def emit(name: str, value: object) -> None:
        print(json.dumps({"type": "metric", "name": name, "value": value}))

    emit("num_problems", n)
    emit("avg_question_chars", round(q_chars / n, 2) if n else 0)
    emit("avg_reasoning_steps", round(steps / n, 2) if n else 0)
    emit("final_answer_rate", round(with_final / n, 4) if n else 0)
    emit("duration_ms", duration_ms)
    print(json.dumps({"type": "log", "message": f"read {n} problems from {path}"}))
    print(json.dumps({"type": "result", "ok": n > 0}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
