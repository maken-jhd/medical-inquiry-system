"""运行单病例 smoke，并逐轮打印问诊动作变化。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain.service import build_default_brain_from_env
from simulator.generate_cases import load_cases_jsonl
from simulator.patient_agent import VirtualPatientAgent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行单病例真实 smoke，并逐轮打印动作变化。")
    parser.add_argument("--cases-file", required=True, help="单病例或小规模病例 JSONL 文件。")
    parser.add_argument("--case-id", default="", help="可选的目标病例 id；不提供时取第一个。")
    parser.add_argument("--max-turns", type=int, default=3, help="最大追问轮次。")
    return parser.parse_args()


def _pick_case(cases_file: Path, case_id: str):
    cases = load_cases_jsonl(cases_file)

    if len(case_id.strip()) == 0:
        return cases[0]

    for case in cases:
        if case.case_id == case_id:
            return case

    raise SystemExit(f"未在 {cases_file} 中找到 case_id={case_id}")


def main() -> int:
    args = parse_args()
    case = _pick_case(Path(args.cases_file), args.case_id)
    brain = build_default_brain_from_env()
    patient = VirtualPatientAgent()
    session_id = f"single-smoke::{case.case_id}"
    brain.start_session(session_id)

    current_output = brain.process_turn(session_id, case.chief_complaint)
    print(
        json.dumps(
            {
                "step": "turn_0",
                "has_final_report": current_output.get("final_report") is not None,
                "pending_action": current_output.get("pending_action"),
                "next_question": current_output.get("next_question"),
                "search_report": current_output.get("search_report"),
            },
            ensure_ascii=False,
        )
    )

    for turn_index in range(1, args.max_turns + 1):
        pending_action = current_output.get("pending_action") or {}
        question_text = str(current_output.get("next_question") or "")
        target_node_id = str(pending_action.get("target_node_id") or "")

        if len(question_text) == 0 or len(target_node_id) == 0:
            break

        reply = patient.answer_question(target_node_id, question_text, case)
        print(
            json.dumps(
                {
                    "step": f"reply_{turn_index}",
                    "question_node_id": target_node_id,
                    "question_text": question_text,
                    "answer_text": reply.answer_text,
                    "revealed_slot_id": reply.revealed_slot_id,
                },
                ensure_ascii=False,
            )
        )
        current_output = brain.process_turn(session_id, reply.answer_text)
        print(
            json.dumps(
                {
                    "step": f"turn_{turn_index}",
                    "has_final_report": current_output.get("final_report") is not None,
                    "pending_action": current_output.get("pending_action"),
                    "next_question": current_output.get("next_question"),
                    "route_after_a4": current_output.get("route_after_a4"),
                    "search_report": current_output.get("search_report"),
                },
                ensure_ascii=False,
            )
        )

        if current_output.get("final_report") is not None:
            break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
