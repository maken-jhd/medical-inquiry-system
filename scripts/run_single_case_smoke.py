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
    parser.add_argument("--output-file", default="", help="可选输出 JSONL 文件。")
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="仅输出紧凑 turn 摘要，便于观察 repair 行为。",
    )
    return parser.parse_args()


def _pick_case(cases_file: Path, case_id: str):
    cases = load_cases_jsonl(cases_file)

    if len(case_id.strip()) == 0:
        return cases[0]

    for case in cases:
        if case.case_id == case_id:
            return case

    raise SystemExit(f"未在 {cases_file} 中找到 case_id={case_id}")


def _compact_action(payload: object) -> dict | None:
    if not isinstance(payload, dict):
        return None

    return {
        "action_id": payload.get("action_id"),
        "target_node_id": payload.get("target_node_id"),
        "target_node_name": payload.get("target_node_name"),
        "hypothesis_id": payload.get("hypothesis_id"),
        "topic_id": payload.get("topic_id"),
    }


def _extract_turn_summary(output: dict, previous_question_node_id: str | None = None) -> dict:
    search_report = output.get("search_report") or {}
    repair_context = search_report.get("repair_context") or {}
    selected_action = _compact_action(search_report.get("selected_action"))
    root_best_action = _compact_action(search_report.get("root_best_action"))
    repair_selected_action = _compact_action(search_report.get("repair_selected_action"))
    pending_action = _compact_action(output.get("pending_action"))
    final_report = output.get("final_report") or {}
    best_final_answer = final_report.get("best_final_answer") or {}

    current_question_node_id = None
    if pending_action is not None:
        current_question_node_id = pending_action.get("target_node_id")

    return {
        "turn_index": output.get("turn_index"),
        "reject_reason": repair_context.get("reject_reason", ""),
        "recommended_next_evidence": repair_context.get("recommended_next_evidence", []),
        "alternative_candidates": repair_context.get("alternative_candidates", []),
        "repair_mode": repair_context.get("repair_mode", ""),
        "rerooted": bool(repair_context.get("rerooted", False)),
        "reroot_reason": repair_context.get("reroot_reason", ""),
        "previous_selected_action": _compact_action(repair_context.get("previous_selected_action")),
        "new_selected_action": _compact_action(repair_context.get("new_selected_action")),
        "selected_action": selected_action,
        "root_best_action": root_best_action,
        "repair_selected_action": repair_selected_action,
        "pending_action": pending_action,
        "route_after_a4_stage": (output.get("route_after_a4") or {}).get("stage"),
        "best_answer_name": search_report.get("best_answer_name") or best_final_answer.get("answer_name"),
        "stop_reason": final_report.get("stop_reason"),
        "same_question_as_previous": (
            previous_question_node_id is not None
            and current_question_node_id is not None
            and current_question_node_id == previous_question_node_id
        ),
    }


def _emit_event(event: dict, output_path: Path | None) -> None:
    print(json.dumps(event, ensure_ascii=False))
    if output_path is None:
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def main() -> int:
    args = parse_args()
    case = _pick_case(Path(args.cases_file), args.case_id)
    brain = build_default_brain_from_env()
    patient = VirtualPatientAgent(use_llm=True)
    session_id = f"single-smoke::{case.case_id}"
    brain.start_session(session_id)
    output_path = Path(args.output_file) if len(args.output_file.strip()) > 0 else None

    opening = patient.open_case(case)
    current_output = brain.process_turn(session_id, opening.opening_text)
    previous_question_node_id: str | None = None
    turn_summary = _extract_turn_summary(current_output)
    previous_question_node_id = (turn_summary.get("pending_action") or {}).get("target_node_id")
    initial_event = {
        "step": "turn_0",
        "opening_text": opening.opening_text,
        "has_final_report": current_output.get("final_report") is not None,
        "next_question": current_output.get("next_question"),
        "turn_summary": turn_summary,
    }
    if not args.summary_only:
        initial_event["pending_action"] = current_output.get("pending_action")
        initial_event["search_report"] = current_output.get("search_report")
    _emit_event(initial_event, output_path)

    for turn_index in range(1, args.max_turns + 1):
        pending_action = current_output.get("pending_action") or {}
        question_text = str(current_output.get("next_question") or "")
        target_node_id = str(pending_action.get("target_node_id") or "")

        if len(question_text) == 0 or len(target_node_id) == 0:
            break

        reply = patient.answer_question(target_node_id, question_text, case)
        _emit_event(
            {
                "step": f"reply_{turn_index}",
                "question_node_id": target_node_id,
                "question_text": question_text,
                "answer_text": reply.answer_text,
                "revealed_slot_id": reply.revealed_slot_id,
            },
            output_path,
        )
        current_output = brain.process_turn(session_id, reply.answer_text)
        turn_summary = _extract_turn_summary(current_output, previous_question_node_id=previous_question_node_id)
        previous_question_node_id = (turn_summary.get("pending_action") or {}).get("target_node_id")
        turn_event = {
            "step": f"turn_{turn_index}",
            "has_final_report": current_output.get("final_report") is not None,
            "next_question": current_output.get("next_question"),
            "route_after_a4": current_output.get("route_after_a4"),
            "turn_summary": turn_summary,
        }
        if not args.summary_only:
            turn_event["pending_action"] = current_output.get("pending_action")
            turn_event["search_report"] = current_output.get("search_report")
        _emit_event(turn_event, output_path)

        if current_output.get("final_report") is not None:
            break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
