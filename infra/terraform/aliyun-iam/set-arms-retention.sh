#!/usr/bin/env bash
# Set clousight-bench ARMS trace-app retention (probe-sink spec §12.1 principle 4).
# ARMS retention is NOT terraform-manageable (no alicloud_arms_* retention resource
# in the aliyun/alicloud provider as of v1.220); this script sets it via the ARMS
# OpenAPI. Idempotent: safe to re-run. Requires the MAIN account creds in env
# (ALICLOUD_ACCESS_KEY / ALICLOUD_SECRET_KEY) and the `aliyun` CLI.
#
# PHASE-B-VERIFY: the exact ARMS OpenAPI action/param names used below
#   (SearchTraceAppByName, UpdateTraceAppConfig, tracesDataRetention)
# must be confirmed against the live ARMS console / API explorer before running.
# If they differ, this is a one-line edit; the retention value (15d) is final.
set -euo pipefail

RETENTION_DAYS="${1:-15}"
REGION="${ALICLOUD_REGION:-cn-hangzhou}"
APP_NAME="${ARMS_TRACE_APP_NAME:-clousight-bench}"

echo "Setting ARMS trace retention for app '${APP_NAME}' to ${RETENTION_DAYS} days in ${REGION}..."

# Resolve the trace app PID by name, then update its retention config.
# PHASE-B-VERIFY: confirm SearchTraceAppByName is the correct ARMS OpenAPI action.
PID="$(aliyun arms SearchTraceAppByName \
  --region "${REGION}" \
  --TraceAppName "${APP_NAME}" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["TraceApps"][0]["Pid"])')"

# PHASE-B-VERIFY: confirm UpdateTraceAppConfig + key "tracesDataRetention" are correct.
aliyun arms UpdateTraceAppConfig \
  --region "${REGION}" \
  --Pid "${PID}" \
  --Settings "[{\"key\":\"tracesDataRetention\",\"value\":\"${RETENTION_DAYS}\"}]"

echo "Done. Retention set to ${RETENTION_DAYS} days for PID ${PID}."
