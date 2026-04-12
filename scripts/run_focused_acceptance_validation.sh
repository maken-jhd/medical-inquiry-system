#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)}"
export CASES_FILE="${CASES_FILE:-${PROJECT_ROOT}/simulator/focused_acceptance_cases.jsonl}"
export CASE_IDS="${CASE_IDS:-}"
export MAX_TURNS="${MAX_TURNS:-5}"
export CASE_CONCURRENCY="${CASE_CONCURRENCY:-5}"
export ACCEPTANCE_PROFILES="${ACCEPTANCE_PROFILES:-baseline,slightly_lenient,guarded_lenient}"
export OUTPUT_ROOT="${OUTPUT_ROOT:-${PROJECT_ROOT}/test_outputs/simulator_replay/focused_acceptance_validation_${RUN_ID}}"
export LATEST_FILE="${LATEST_FILE:-${PROJECT_ROOT}/test_outputs/simulator_replay/latest_focused_acceptance_validation_output.txt}"

./scripts/run_verifier_acceptance_sweep.sh
