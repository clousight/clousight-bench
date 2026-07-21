#!/usr/bin/env bash
# Polyglot workload example: wrap YCSB (JVM) and translate its output into the
# OpenCloudBench JSONL metric protocol. The core never imports Java -- it only
# reads {"type":"metric"...} / {"type":"result"...} lines from our stdout.
#
# Contract: invoked as `./run.sh --params <json-file>`, emit JSONL on stdout.
set -euo pipefail

PARAMS_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --params) PARAMS_FILE="$2"; shift 2 ;;
    *) shift ;;
  esac
done

emit() { printf '%s\n' "$1"; }

read_param() { # key default
  python3 -c "import json,sys; d=json.load(open('$PARAMS_FILE')); print(d.get('$1', '$2'))" 2>/dev/null || echo "$2"
}

WORKLOAD="$(read_param workload workloada)"
RECORDCOUNT="$(read_param recordcount 10000)"
OPERATIONCOUNT="$(read_param operationcount 10000)"
BINDING="$(read_param binding basic)"

# Locate YCSB. If absent, fail cleanly through the protocol so the framework
# records an honest "workload unavailable" instead of crashing.
YCSB_BIN=""
if command -v ycsb >/dev/null 2>&1; then
  YCSB_BIN="ycsb"
elif [[ -n "${YCSB_HOME:-}" && -x "${YCSB_HOME}/bin/ycsb" ]]; then
  YCSB_BIN="${YCSB_HOME}/bin/ycsb"
fi

if [[ -z "$YCSB_BIN" ]]; then
  emit '{"type": "log", "message": "YCSB not found (set YCSB_HOME or put ycsb on PATH)"}'
  emit '{"type": "result", "ok": false}'
  exit 0
fi

if ! command -v java >/dev/null 2>&1; then
  emit '{"type": "log", "message": "java not found; YCSB needs a JVM"}'
  emit '{"type": "result", "ok": false}'
  exit 0
fi

OUT="$("$YCSB_BIN" run "$BINDING" \
  -P "workloads/${WORKLOAD}" \
  -p recordcount="$RECORDCOUNT" \
  -p operationcount="$OPERATIONCOUNT" 2>/dev/null || true)"

# Parse YCSB's "[OVERALL], RunTime(ms), 1234" style lines into JSONL metrics.
python3 - "$OUT" <<'PYEOF'
import re, sys
text = sys.argv[1]
def emit(name, value):
    import json
    print(json.dumps({"type": "metric", "name": name, "value": value}))

patterns = {
    "overall_runtime_ms": r"\[OVERALL\], RunTime\(ms\), ([\d.]+)",
    "overall_throughput_ops": r"\[OVERALL\], Throughput\(ops/sec\), ([\d.]+)",
    "read_p99_us": r"\[READ\], 99thPercentileLatency\(us\), ([\d.]+)",
    "update_p99_us": r"\[UPDATE\], 99thPercentileLatency\(us\), ([\d.]+)",
}
found_any = False
for name, pat in patterns.items():
    m = re.search(pat, text)
    if m:
        emit(name, float(m.group(1)))
        found_any = True
import json
print(json.dumps({"type": "result", "ok": found_any}))
PYEOF
