#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

CONDA_ENV="${CONDA_ENV:-GraduationDesign}"
CASE_IDS="${CASE_IDS:-pcp_typical_001,pcp_vague_001,concealing_risk_001}"
MAX_TURNS="${MAX_TURNS:-5}"
RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)}"
OUTPUT_ROOT="${OUTPUT_ROOT:-${PROJECT_ROOT}/test_outputs/simulator_replay/focused_ablation_baseline_${RUN_ID}}"
HEARTBEAT_SECONDS="${HEARTBEAT_SECONDS:-30}"

export OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}"
export OPENAI_MODEL="${OPENAI_MODEL:-qwen3-max}"
export NEO4J_PASSWORD="${NEO4J_PASSWORD:-admin123456}"

if [[ -z "${DASHSCOPE_API_KEY:-}" ]]; then
  read -rsp "DASHSCOPE_API_KEY: " DASHSCOPE_API_KEY
  echo
  export DASHSCOPE_API_KEY
fi

mkdir -p "${OUTPUT_ROOT}"
LOG_FILE="${OUTPUT_ROOT}/run.log"
STATUS_FILE="${OUTPUT_ROOT}/status.json"
COMMAND_FILE="${OUTPUT_ROOT}/command.txt"
LATEST_FILE="${PROJECT_ROOT}/test_outputs/simulator_replay/latest_baseline_ablation_output.txt"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
START_SECONDS="${SECONDS}"

write_status() {
  local status="$1"
  local message="$2"
  local exit_code="${3:-null}"
  local updated_at
  updated_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  cat > "${STATUS_FILE}" <<JSON
{
  "status": "${status}",
  "message": "${message}",
  "started_at": "${STARTED_AT}",
  "updated_at": "${updated_at}",
  "output_root": "${OUTPUT_ROOT}",
  "log_file": "${LOG_FILE}",
  "metrics_file": "${OUTPUT_ROOT}/baseline/focused_metrics.json",
  "summary_file": "${OUTPUT_ROOT}/baseline/focused_repair_summary.jsonl",
  "exit_code": ${exit_code}
}
JSON
}

heartbeat() {
  while true; do
    local elapsed
    elapsed=$((SECONDS - START_SECONDS))
    echo "[baseline-ablation] heartbeat elapsed=${elapsed}s status=running output_root=${OUTPUT_ROOT}" | tee -a "${LOG_FILE}"
    write_status "running" "baseline ablation is still running" "null"
    sleep "${HEARTBEAT_SECONDS}"
  done
}

cleanup_heartbeat() {
  if [[ -n "${HEARTBEAT_PID:-}" ]]; then
    kill "${HEARTBEAT_PID}" >/dev/null 2>&1 || true
    wait "${HEARTBEAT_PID}" >/dev/null 2>&1 || true
  fi
}

trap cleanup_heartbeat EXIT

COMMAND=(
  conda run --no-capture-output -n "${CONDA_ENV}"
  python -u scripts/run_focused_ablation.py
  --case-ids "${CASE_IDS}"
  --variants baseline
  --output-root "${OUTPUT_ROOT}"
  --max-turns "${MAX_TURNS}"
)

{
  echo "[baseline-ablation] started_at=${STARTED_AT}"
  echo "[baseline-ablation] project_root=${PROJECT_ROOT}"
  echo "[baseline-ablation] output_root=${OUTPUT_ROOT}"
  echo "[baseline-ablation] case_ids=${CASE_IDS}"
  echo "[baseline-ablation] max_turns=${MAX_TURNS}"
  echo "[baseline-ablation] openai_model=${OPENAI_MODEL}"
  echo "[baseline-ablation] neo4j_uri=${NEO4J_URI:-bolt://localhost:7687}"
} | tee "${LOG_FILE}"

printf '%s\n' "${OUTPUT_ROOT}" > "${LATEST_FILE}"
printf '%q ' "${COMMAND[@]}" > "${COMMAND_FILE}"
printf '\n' >> "${COMMAND_FILE}"
write_status "starting" "checking Neo4j connectivity" "null"

echo "[baseline-ablation] checking Neo4j connectivity..." | tee -a "${LOG_FILE}"
set +e
conda run --no-capture-output -n "${CONDA_ENV}" python - <<'PY' 2>&1 | tee -a "${LOG_FILE}"
from brain.neo4j_client import Neo4jClient

client = Neo4jClient.from_env()
try:
    rows = client.run_query("RETURN 1 AS ok")
    print(f"[baseline-ablation] neo4j_check={rows}")
finally:
    client.close()
PY
neo4j_status=${PIPESTATUS[0]}
set -e

if [[ "${neo4j_status}" -ne 0 ]]; then
  write_status "failed" "Neo4j connectivity check failed" "${neo4j_status}"
  echo "[baseline-ablation] failed: Neo4j connectivity check failed" | tee -a "${LOG_FILE}"
  exit "${neo4j_status}"
fi

write_status "running" "baseline ablation is running" "null"
heartbeat &
HEARTBEAT_PID=$!

set +e
"${COMMAND[@]}" 2>&1 | tee -a "${LOG_FILE}"
run_status=${PIPESTATUS[0]}
set -e
cleanup_heartbeat
unset HEARTBEAT_PID

if [[ "${run_status}" -eq 0 ]]; then
  write_status "succeeded" "baseline ablation completed" "0"
  echo "[baseline-ablation] succeeded output_root=${OUTPUT_ROOT}" | tee -a "${LOG_FILE}"
  echo "[baseline-ablation] metrics=${OUTPUT_ROOT}/baseline/focused_metrics.json" | tee -a "${LOG_FILE}"
  echo "[baseline-ablation] summary=${OUTPUT_ROOT}/baseline/focused_repair_summary.jsonl" | tee -a "${LOG_FILE}"
  exit 0
fi

write_status "failed" "baseline ablation command failed" "${run_status}"
echo "[baseline-ablation] failed exit_code=${run_status} output_root=${OUTPUT_ROOT}" | tee -a "${LOG_FILE}"
exit "${run_status}"
