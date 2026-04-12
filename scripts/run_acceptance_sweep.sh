#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

CONDA_ENV="${CONDA_ENV:-GraduationDesign}"
CASE_IDS="${CASE_IDS:-pcp_typical_001,pcp_vague_001,concealing_risk_001}"
MAX_TURNS_SWEEP="${MAX_TURNS_SWEEP:-3,5,7}"
CASE_CONCURRENCY="${CASE_CONCURRENCY:-5}"
ACCEPTANCE_PROFILES="${ACCEPTANCE_PROFILES:-baseline}"
STOP_PROFILES="${STOP_PROFILES:-baseline,relaxed_thresholds}"
RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)}"
OUTPUT_ROOT="${OUTPUT_ROOT:-${PROJECT_ROOT}/test_outputs/simulator_replay/acceptance_sweep_${RUN_ID}}"
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
RESULTS_FILE="${OUTPUT_ROOT}/sweep_results.jsonl"
CURRENT_COMBO_FILE="${OUTPUT_ROOT}/current_combo.txt"
LATEST_FILE="${PROJECT_ROOT}/test_outputs/simulator_replay/latest_acceptance_sweep_output.txt"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
START_SECONDS="${SECONDS}"
CURRENT_COMBO="initializing"

read_current_combo() {
  if [[ -s "${CURRENT_COMBO_FILE}" ]]; then
    tr -d '\n' < "${CURRENT_COMBO_FILE}"
  else
    printf '%s' "${CURRENT_COMBO}"
  fi
}

write_current_combo() {
  CURRENT_COMBO="$1"
  printf '%s\n' "${CURRENT_COMBO}" > "${CURRENT_COMBO_FILE}"
}

write_status() {
  local status="$1"
  local message="$2"
  local exit_code="${3:-null}"
  local combo
  local updated_at
  combo="$(read_current_combo)"
  updated_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  cat > "${STATUS_FILE}" <<JSON
{
  "status": "${status}",
  "message": "${message}",
  "current_combo": "${combo}",
  "started_at": "${STARTED_AT}",
  "updated_at": "${updated_at}",
  "output_root": "${OUTPUT_ROOT}",
  "log_file": "${LOG_FILE}",
  "results_file": "${RESULTS_FILE}",
  "current_combo_file": "${CURRENT_COMBO_FILE}",
  "case_concurrency": ${CASE_CONCURRENCY},
  "exit_code": ${exit_code}
}
JSON
}

heartbeat() {
  while true; do
    local combo
    local elapsed
    combo="$(read_current_combo)"
    elapsed=$((SECONDS - START_SECONDS))
    echo "[acceptance-sweep] heartbeat elapsed=${elapsed}s status=running current_combo=${combo}" | tee -a "${LOG_FILE}"
    write_status "running" "acceptance sweep is still running" "null"
    sleep "${HEARTBEAT_SECONDS}"
  done
}

cleanup_heartbeat() {
  if [[ -n "${HEARTBEAT_PID:-}" ]]; then
    kill "${HEARTBEAT_PID}" >/dev/null 2>&1 || true
    wait "${HEARTBEAT_PID}" >/dev/null 2>&1 || true
  fi
}

append_result() {
  local combo_dir="$1"
  local max_turns="$2"
  local acceptance_profile="$3"
  local stop_profile="$4"

  python - "$combo_dir" "$max_turns" "$acceptance_profile" "$stop_profile" <<'PY' >> "${RESULTS_FILE}"
import json
import sys
from pathlib import Path

combo_dir = Path(sys.argv[1])
max_turns = int(sys.argv[2])
acceptance_profile = sys.argv[3]
stop_profile = sys.argv[4]
metrics_path = combo_dir / "baseline" / "focused_metrics.json"
metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
print(json.dumps(
    {
        "max_turns": max_turns,
        "acceptance_profile": acceptance_profile,
        "stop_profile": stop_profile,
        "output_root": str(combo_dir),
        "metrics": metrics,
    },
    ensure_ascii=False,
))
PY
}

trap cleanup_heartbeat EXIT
printf '%s\n' "${OUTPUT_ROOT}" > "${LATEST_FILE}"
: > "${RESULTS_FILE}"
write_current_combo "initializing"

{
  echo "[acceptance-sweep] started_at=${STARTED_AT}"
  echo "[acceptance-sweep] project_root=${PROJECT_ROOT}"
  echo "[acceptance-sweep] output_root=${OUTPUT_ROOT}"
  echo "[acceptance-sweep] case_ids=${CASE_IDS}"
  echo "[acceptance-sweep] max_turns_sweep=${MAX_TURNS_SWEEP}"
  echo "[acceptance-sweep] case_concurrency=${CASE_CONCURRENCY}"
  echo "[acceptance-sweep] acceptance_profiles=${ACCEPTANCE_PROFILES}"
  echo "[acceptance-sweep] stop_profiles=${STOP_PROFILES}"
  echo "[acceptance-sweep] openai_model=${OPENAI_MODEL}"
  echo "[acceptance-sweep] neo4j_uri=${NEO4J_URI:-bolt://localhost:7687}"
} | tee "${LOG_FILE}"

write_status "starting" "checking Neo4j connectivity" "null"
echo "[acceptance-sweep] checking Neo4j connectivity..." | tee -a "${LOG_FILE}"
set +e
conda run --no-capture-output -n "${CONDA_ENV}" python - <<'PY' 2>&1 | tee -a "${LOG_FILE}"
from brain.neo4j_client import Neo4jClient

client = Neo4jClient.from_env()
try:
    rows = client.run_query("RETURN 1 AS ok")
    print(f"[acceptance-sweep] neo4j_check={rows}")
finally:
    client.close()
PY
neo4j_status=${PIPESTATUS[0]}
set -e

if [[ "${neo4j_status}" -ne 0 ]]; then
  write_status "failed" "Neo4j connectivity check failed" "${neo4j_status}"
  echo "[acceptance-sweep] failed: Neo4j connectivity check failed" | tee -a "${LOG_FILE}"
  exit "${neo4j_status}"
fi

IFS=',' read -r -a MAX_TURN_VALUES <<< "${MAX_TURNS_SWEEP}"
IFS=',' read -r -a ACCEPTANCE_PROFILE_VALUES <<< "${ACCEPTANCE_PROFILES}"
IFS=',' read -r -a STOP_PROFILE_VALUES <<< "${STOP_PROFILES}"

write_status "running" "acceptance sweep is running" "null"
heartbeat &
HEARTBEAT_PID=$!

for max_turns in "${MAX_TURN_VALUES[@]}"; do
  max_turns="$(echo "${max_turns}" | xargs)"

  for acceptance_profile in "${ACCEPTANCE_PROFILE_VALUES[@]}"; do
    acceptance_profile="$(echo "${acceptance_profile}" | xargs)"

    for stop_profile in "${STOP_PROFILE_VALUES[@]}"; do
      stop_profile="$(echo "${stop_profile}" | xargs)"
      combo="turns_${max_turns}__verifier_${acceptance_profile}__stop_${stop_profile}"
      combo_dir="${OUTPUT_ROOT}/${combo}"
      write_current_combo "${combo}"
      mkdir -p "${combo_dir}"
      write_status "running" "running ${combo}" "null"
      echo "[acceptance-sweep] start ${combo}" | tee -a "${LOG_FILE}"

      command=(
        conda run --no-capture-output -n "${CONDA_ENV}"
        python -u scripts/run_focused_ablation.py
        --case-ids "${CASE_IDS}"
        --variants baseline
        --output-root "${combo_dir}"
        --max-turns "${max_turns}"
        --case-concurrency "${CASE_CONCURRENCY}"
      )

      case "${stop_profile}" in
        baseline)
          ;;
        relaxed_thresholds)
          command+=(--min-answer-consistency 0.40 --min-agent-eval-score 0.65 --min-final-score 0.55)
          ;;
        no_verifier_gate)
          command+=(--allow-verifier-rejected-stop)
          ;;
        relaxed_no_verifier_gate)
          command+=(
            --min-answer-consistency 0.40
            --min-agent-eval-score 0.65
            --min-final-score 0.55
            --allow-verifier-rejected-stop
          )
          ;;
        *)
          write_status "failed" "unknown STOP_PROFILE=${stop_profile}" "2"
          echo "[acceptance-sweep] unknown STOP_PROFILE=${stop_profile}" | tee -a "${LOG_FILE}"
          exit 2
          ;;
      esac

      set +e
      TRAJECTORY_VERIFIER_ACCEPTANCE_PROFILE="${acceptance_profile}" "${command[@]}" 2>&1 | tee -a "${LOG_FILE}"
      run_status=${PIPESTATUS[0]}
      set -e

      if [[ "${run_status}" -ne 0 ]]; then
        write_status "failed" "combo ${combo} failed" "${run_status}"
        echo "[acceptance-sweep] failed combo=${combo} exit_code=${run_status}" | tee -a "${LOG_FILE}"
        exit "${run_status}"
      fi

      append_result "${combo_dir}" "${max_turns}" "${acceptance_profile}" "${stop_profile}"
      echo "[acceptance-sweep] done ${combo}" | tee -a "${LOG_FILE}"
    done
  done
done

cleanup_heartbeat
unset HEARTBEAT_PID
write_current_combo "completed"
write_status "succeeded" "acceptance sweep completed" "0"
echo "[acceptance-sweep] succeeded output_root=${OUTPUT_ROOT}" | tee -a "${LOG_FILE}"
echo "[acceptance-sweep] results=${RESULTS_FILE}" | tee -a "${LOG_FILE}"
