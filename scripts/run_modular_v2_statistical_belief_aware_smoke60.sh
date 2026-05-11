#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BRAIN_CONFIG_PATH_VALUE="${BRAIN_CONFIG_PATH_VALUE:-${PROJECT_ROOT}/configs/brain_benchmark_modular_v2_statistical_belief_aware.yaml}"
CASES_FILE="${CASES_FILE:-${PROJECT_ROOT}/test_outputs/simulator_cases/graph_cases_20260502_role_qc/smoke60/cases.jsonl}"
OUTPUT_ROOT="${OUTPUT_ROOT:-${PROJECT_ROOT}/test_outputs/simulator_replay/benchmark_modular_v2_statistical_belief_aware_smoke60}"
MAX_TURNS="${MAX_TURNS:-8}"
CASE_CONCURRENCY="${CASE_CONCURRENCY:-6}"
LIMIT="${LIMIT:-0}"
API_ERROR_RETRIES="${API_ERROR_RETRIES:-1}"
NO_RESUME="${NO_RESUME:-0}"

ARGS=(
  --cases-file "${CASES_FILE}"
  --output-root "${OUTPUT_ROOT}"
  --max-turns "${MAX_TURNS}"
  --case-concurrency "${CASE_CONCURRENCY}"
  --api-error-retries "${API_ERROR_RETRIES}"
)

if [[ "${LIMIT}" != "0" ]]; then
  ARGS+=(--limit "${LIMIT}")
fi

if [[ "${NO_RESUME}" == "1" ]]; then
  ARGS+=(--no-resume)
fi

if [[ $# -gt 0 ]]; then
  ARGS+=("$@")
fi

BRAIN_CONFIG_PATH="${BRAIN_CONFIG_PATH_VALUE}" \
conda run --no-capture-output -n GraduationDesign python "${PROJECT_ROOT}/scripts/run_batch_replay.py" "${ARGS[@]}"
