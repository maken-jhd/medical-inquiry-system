#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

CONDA_ENV="${CONDA_ENV:-GraduationDesign}"
CASE_IDS="${CASE_IDS:-pcp_typical_001,pcp_vague_001,concealing_risk_001}"
CASES_FILE="${CASES_FILE:-}"
CASE_LIST="${CASE_LIST:-}"
MAX_TURNS="${MAX_TURNS:-5}"
CASE_CONCURRENCY="${CASE_CONCURRENCY:-5}"
ACCEPTANCE_PROFILES="${ACCEPTANCE_PROFILES:-baseline,slightly_lenient,guarded_lenient}"
RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)}"
OUTPUT_ROOT="${OUTPUT_ROOT:-${PROJECT_ROOT}/test_outputs/simulator_replay/verifier_acceptance_sweep_${RUN_ID}}"
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
CURRENT_PROFILE_FILE="${OUTPUT_ROOT}/current_profile.txt"
PROFILE_SUMMARY_FILE="${OUTPUT_ROOT}/profile_summary.tsv"
LATEST_FILE="${LATEST_FILE:-${PROJECT_ROOT}/test_outputs/simulator_replay/latest_verifier_acceptance_sweep_output.txt}"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
START_SECONDS="${SECONDS}"
CURRENT_PROFILE="initializing"

read_current_profile() {
  if [[ -s "${CURRENT_PROFILE_FILE}" ]]; then
    tr -d '\n' < "${CURRENT_PROFILE_FILE}"
  else
    printf '%s' "${CURRENT_PROFILE}"
  fi
}

write_current_profile() {
  CURRENT_PROFILE="$1"
  printf '%s\n' "${CURRENT_PROFILE}" > "${CURRENT_PROFILE_FILE}"
}

write_status() {
  local status="$1"
  local message="$2"
  local exit_code="${3:-null}"
  local profile
  local updated_at
  profile="$(read_current_profile)"
  updated_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  cat > "${STATUS_FILE}" <<JSON
{
  "status": "${status}",
  "message": "${message}",
  "current_profile": "${profile}",
  "started_at": "${STARTED_AT}",
  "updated_at": "${updated_at}",
  "output_root": "${OUTPUT_ROOT}",
  "log_file": "${LOG_FILE}",
  "results_file": "${RESULTS_FILE}",
  "profile_summary_file": "${PROFILE_SUMMARY_FILE}",
  "current_profile_file": "${CURRENT_PROFILE_FILE}",
  "case_concurrency": ${CASE_CONCURRENCY},
  "exit_code": ${exit_code}
}
JSON
}

heartbeat() {
  while true; do
    local elapsed
    local profile
    elapsed=$((SECONDS - START_SECONDS))
    profile="$(read_current_profile)"
    echo "[verifier-acceptance-sweep] heartbeat elapsed=${elapsed}s status=running current_profile=${profile}" | tee -a "${LOG_FILE}"
    write_status "running" "verifier acceptance sweep is still running" "null"
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
  local profile_dir="$1"
  local acceptance_profile="$2"

  python - "$profile_dir" "$MAX_TURNS" "$acceptance_profile" <<'PY' >> "${RESULTS_FILE}"
import json
import sys
from pathlib import Path

profile_dir = Path(sys.argv[1])
max_turns = int(sys.argv[2])
acceptance_profile = sys.argv[3]
metrics_path = profile_dir / "baseline" / "focused_metrics.json"
metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
print(json.dumps(
    {
        "max_turns": max_turns,
        "acceptance_profile": acceptance_profile,
        "stop_profile": "baseline",
        "output_root": str(profile_dir),
        "metrics": metrics,
    },
    ensure_ascii=False,
))
PY
}

write_profile_summary() {
  python - "$RESULTS_FILE" "$PROFILE_SUMMARY_FILE" <<'PY'
import json
import sys
from pathlib import Path

results_path = Path(sys.argv[1])
summary_path = Path(sys.argv[2])
headers = [
    "acceptance_profile",
    "case_concurrency",
    "accepted_correct_count",
    "correct_best_answer_but_rejected_count",
    "accepted_wrong_count",
    "wrong_best_answer_rejected_count",
    "verifier_called_count",
    "accepted_with_verifier_metadata_count",
    "accepted_without_verifier_metadata_count",
    "accepted_on_turn1_count",
    "wrong_accept_on_turn1_count",
    "accept_reason_counts",
    "wrong_accept_reason_counts",
    "avg_first_correct_best_answer_turn",
    "avg_first_verifier_accept_turn",
    "median_first_verifier_accept_turn",
    "median_first_verifier_accept_turn_for_final_answer",
    "final_answer_changed_after_first_accept_count",
    "accepted_after_negative_key_evidence_count",
    "accepted_after_recent_hypothesis_switch_count",
    "accepted_with_nonempty_alternative_candidates_count",
    "guarded_block_reason_counts",
    "verifier_positive_but_gate_rejected_count",
    "accept_candidate_without_confirmed_combo_count",
    "guarded_gate_audit_record_count",
    "guarded_negative_evidence_node_counts",
    "guarded_negative_evidence_family_counts",
    "guarded_negative_evidence_tier_counts",
    "guarded_negative_evidence_scope_counts",
    "strong_alternative_block_count",
    "weak_alternative_allowed_count",
    "combo_satisfied_but_alternative_blocked_count",
    "pending_action_audit_record_count",
    "pending_action_confirmed_family_candidate_count",
    "pending_action_provisional_family_candidate_count",
    "provisional_family_used_count",
    "provisional_combo_satisfied_count",
    "accepted_with_provisional_combo_count",
    "missing_family_first_selected_count",
    "missing_family_repair_turn_count",
    "combo_anchor_selected_before_turn3_count",
    "family_recorded_after_question_count",
    "family_recorded_after_question_attempt_count",
    "avg_correct_but_rejected_span",
    "verifier_schema_valid_true",
    "verifier_schema_valid_false",
]
rows = []
if results_path.exists():
    for line in results_path.read_text(encoding="utf-8").splitlines():
        if len(line.strip()) == 0:
            continue
        item = json.loads(line)
        metrics = item["metrics"]
        schema_counts = metrics.get("verifier_schema_valid_counts", {})
        rows.append(
            [
                item["acceptance_profile"],
                metrics.get("case_concurrency", ""),
                metrics.get("accepted_correct_count", 0),
                metrics.get("correct_best_answer_but_rejected_count", 0),
                metrics.get("accepted_wrong_count", 0),
                metrics.get("wrong_best_answer_rejected_count", 0),
                metrics.get("verifier_called_count", 0),
                metrics.get("accepted_with_verifier_metadata_count", 0),
                metrics.get("accepted_without_verifier_metadata_count", 0),
                metrics.get("accepted_on_turn1_count", 0),
                metrics.get("wrong_accept_on_turn1_count", 0),
                json.dumps(metrics.get("accept_reason_counts", {}), ensure_ascii=False, sort_keys=True),
                json.dumps(metrics.get("wrong_accept_reason_counts", {}), ensure_ascii=False, sort_keys=True),
                metrics.get("avg_first_correct_best_answer_turn"),
                metrics.get("avg_first_verifier_accept_turn"),
                metrics.get("median_first_verifier_accept_turn"),
                metrics.get("median_first_verifier_accept_turn_for_final_answer"),
                metrics.get("final_answer_changed_after_first_accept_count", 0),
                metrics.get("accepted_after_negative_key_evidence_count", 0),
                metrics.get("accepted_after_recent_hypothesis_switch_count", 0),
                metrics.get("accepted_with_nonempty_alternative_candidates_count", 0),
                json.dumps(metrics.get("guarded_block_reason_counts", {}), ensure_ascii=False, sort_keys=True),
                metrics.get("verifier_positive_but_gate_rejected_count", 0),
                metrics.get("accept_candidate_without_confirmed_combo_count", 0),
                len(metrics.get("guarded_gate_audit_records", [])),
                json.dumps(metrics.get("guarded_negative_evidence_node_counts", {}), ensure_ascii=False, sort_keys=True),
                json.dumps(metrics.get("guarded_negative_evidence_family_counts", {}), ensure_ascii=False, sort_keys=True),
                json.dumps(metrics.get("guarded_negative_evidence_tier_counts", {}), ensure_ascii=False, sort_keys=True),
                json.dumps(metrics.get("guarded_negative_evidence_scope_counts", {}), ensure_ascii=False, sort_keys=True),
                metrics.get("strong_alternative_block_count", 0),
                metrics.get("weak_alternative_allowed_count", 0),
                metrics.get("combo_satisfied_but_alternative_blocked_count", 0),
                metrics.get("pending_action_audit_record_count", 0),
                metrics.get("pending_action_confirmed_family_candidate_count", 0),
                metrics.get("pending_action_provisional_family_candidate_count", 0),
                metrics.get("provisional_family_used_count", 0),
                metrics.get("provisional_combo_satisfied_count", 0),
                metrics.get("accepted_with_provisional_combo_count", 0),
                metrics.get("missing_family_first_selected_count", 0),
                metrics.get("missing_family_repair_turn_count", 0),
                metrics.get("combo_anchor_selected_before_turn3_count", 0),
                metrics.get("family_recorded_after_question_count", 0),
                metrics.get("family_recorded_after_question_attempt_count", 0),
                metrics.get("avg_correct_but_rejected_span"),
                schema_counts.get("true", 0),
                schema_counts.get("false", 0),
            ]
        )
summary_path.write_text(
    "\n".join(["\t".join(headers)] + ["\t".join("" if value is None else str(value) for value in row) for row in rows])
    + "\n",
    encoding="utf-8",
)
PY
}

trap cleanup_heartbeat EXIT
printf '%s\n' "${OUTPUT_ROOT}" > "${LATEST_FILE}"
: > "${RESULTS_FILE}"
write_current_profile "initializing"

{
  echo "[verifier-acceptance-sweep] started_at=${STARTED_AT}"
  echo "[verifier-acceptance-sweep] project_root=${PROJECT_ROOT}"
  echo "[verifier-acceptance-sweep] output_root=${OUTPUT_ROOT}"
  echo "[verifier-acceptance-sweep] case_ids=${CASE_IDS}"
  echo "[verifier-acceptance-sweep] cases_file=${CASES_FILE}"
  echo "[verifier-acceptance-sweep] case_list=${CASE_LIST}"
  echo "[verifier-acceptance-sweep] max_turns=${MAX_TURNS}"
  echo "[verifier-acceptance-sweep] case_concurrency=${CASE_CONCURRENCY}"
  echo "[verifier-acceptance-sweep] acceptance_profiles=${ACCEPTANCE_PROFILES}"
  echo "[verifier-acceptance-sweep] stop_profile=baseline"
  echo "[verifier-acceptance-sweep] openai_model=${OPENAI_MODEL}"
  echo "[verifier-acceptance-sweep] neo4j_uri=${NEO4J_URI:-bolt://localhost:7687}"
} | tee "${LOG_FILE}"

write_status "starting" "checking Neo4j connectivity" "null"
echo "[verifier-acceptance-sweep] checking Neo4j connectivity..." | tee -a "${LOG_FILE}"
set +e
conda run --no-capture-output -n "${CONDA_ENV}" python - <<'PY' 2>&1 | tee -a "${LOG_FILE}"
from brain.neo4j_client import Neo4jClient

client = Neo4jClient.from_env()
try:
    rows = client.run_query("RETURN 1 AS ok")
    print(f"[verifier-acceptance-sweep] neo4j_check={rows}")
finally:
    client.close()
PY
neo4j_status=${PIPESTATUS[0]}
set -e

if [[ "${neo4j_status}" -ne 0 ]]; then
  write_status "failed" "Neo4j connectivity check failed" "${neo4j_status}"
  echo "[verifier-acceptance-sweep] failed: Neo4j connectivity check failed" | tee -a "${LOG_FILE}"
  exit "${neo4j_status}"
fi

IFS=',' read -r -a ACCEPTANCE_PROFILE_VALUES <<< "${ACCEPTANCE_PROFILES}"

write_status "running" "verifier acceptance sweep is running" "null"
heartbeat &
HEARTBEAT_PID=$!

for acceptance_profile in "${ACCEPTANCE_PROFILE_VALUES[@]}"; do
  acceptance_profile="$(echo "${acceptance_profile}" | xargs)"
  profile_dir="${OUTPUT_ROOT}/turns_${MAX_TURNS}__verifier_${acceptance_profile}__stop_baseline"
  write_current_profile "${acceptance_profile}"
  mkdir -p "${profile_dir}"
  write_status "running" "running acceptance_profile=${acceptance_profile}" "null"
  echo "[verifier-acceptance-sweep] start acceptance_profile=${acceptance_profile}" | tee -a "${LOG_FILE}"

  set +e
  command=(
    conda run --no-capture-output -n "${CONDA_ENV}"
    python -u scripts/run_focused_ablation.py
    --variants baseline
    --output-root "${profile_dir}"
    --max-turns "${MAX_TURNS}"
    --case-concurrency "${CASE_CONCURRENCY}"
  )

  if [[ -n "${CASES_FILE}" ]]; then
    command+=(--cases-file "${CASES_FILE}")
  else
    command+=(--case-ids "${CASE_IDS}")
  fi

  if [[ -n "${CASE_LIST}" ]]; then
    command+=(--case-list "${CASE_LIST}")
  fi

  TRAJECTORY_VERIFIER_ACCEPTANCE_PROFILE="${acceptance_profile}" "${command[@]}" 2>&1 | tee -a "${LOG_FILE}"
  run_status=${PIPESTATUS[0]}
  set -e

  if [[ "${run_status}" -ne 0 ]]; then
    write_status "failed" "acceptance_profile=${acceptance_profile} failed" "${run_status}"
    echo "[verifier-acceptance-sweep] failed acceptance_profile=${acceptance_profile} exit_code=${run_status}" | tee -a "${LOG_FILE}"
    exit "${run_status}"
  fi

  append_result "${profile_dir}" "${acceptance_profile}"
  write_profile_summary
  echo "[verifier-acceptance-sweep] done acceptance_profile=${acceptance_profile}" | tee -a "${LOG_FILE}"
done

cleanup_heartbeat
unset HEARTBEAT_PID
write_current_profile "completed"
write_status "succeeded" "verifier acceptance sweep completed" "0"
write_profile_summary
echo "[verifier-acceptance-sweep] succeeded output_root=${OUTPUT_ROOT}" | tee -a "${LOG_FILE}"
echo "[verifier-acceptance-sweep] results=${RESULTS_FILE}" | tee -a "${LOG_FILE}"
echo "[verifier-acceptance-sweep] summary=${PROFILE_SUMMARY_FILE}" | tee -a "${LOG_FILE}"
