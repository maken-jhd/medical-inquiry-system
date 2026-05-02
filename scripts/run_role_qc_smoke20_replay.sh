#!/usr/bin/env bash
set -euo pipefail

CASES_FILE="${CASES_FILE:-test_outputs/simulator_cases/graph_cases_20260502_role_qc/smoke20/cases.jsonl}"
OUTPUT_ROOT="${OUTPUT_ROOT:-test_outputs/simulator_replay/graph_cases_20260502_role_qc_smoke20}"
MAX_TURNS="${MAX_TURNS:-8}"
CASE_CONCURRENCY="${CASE_CONCURRENCY:-4}"
LIMIT="${LIMIT:-20}"

conda run --no-capture-output -n GraduationDesign python scripts/run_batch_replay.py \
  --cases-file "${CASES_FILE}" \
  --output-root "${OUTPUT_ROOT}" \
  --max-turns "${MAX_TURNS}" \
  --case-concurrency "${CASE_CONCURRENCY}" \
  --limit "${LIMIT}"
