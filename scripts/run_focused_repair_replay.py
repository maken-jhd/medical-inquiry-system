"""运行 focused replay，并导出 repair 行为可观测摘要。"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain.service import build_default_brain_from_env
from simulator.generate_cases import build_seed_cases, load_cases_jsonl, write_cases_jsonl
from simulator.patient_agent import VirtualPatientAgent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 focused repair replay，并导出逐轮行为摘要。")
    parser.add_argument(
        "--cases-file",
        default="",
        help="可选病例 JSONL 文件；不提供时使用内置 seed cases。",
    )
    parser.add_argument(
        "--case-ids",
        default="",
        help="逗号分隔的病例 id 列表。",
    )
    parser.add_argument(
        "--output-root",
        default=str(PROJECT_ROOT / "test_outputs" / "focused_repair_replay"),
        help="focused replay 输出目录。",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=3,
        help="每个病例最大追问轮次。",
    )
    parser.add_argument(
        "--disable-verifier-reshuffle",
        action="store_true",
        help="关闭 verifier-driven hypothesis reshuffle。",
    )
    parser.add_argument(
        "--disable-best-repair-action",
        action="store_true",
        help="关闭 best repair action，仅保留 root action 过滤。",
    )
    parser.add_argument(
        "--disable-reroot",
        action="store_true",
        help="关闭 reroot，保留旧树复用。",
    )
    return parser.parse_args()


def _load_cases(args: argparse.Namespace):
    if len(args.cases_file.strip()) > 0:
        cases = load_cases_jsonl(Path(args.cases_file))
    else:
        cases = build_seed_cases()

    requested_ids = [item.strip() for item in args.case_ids.split(",") if len(item.strip()) > 0]

    if len(requested_ids) == 0:
        return cases

    requested_set = set(requested_ids)
    return [case for case in cases if case.case_id in requested_set]


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
        "previous_selected_action": repair_context.get("previous_selected_action"),
        "new_selected_action": repair_context.get("new_selected_action"),
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


def _build_config_overrides(args: argparse.Namespace) -> dict:
    return {
        "repair": {
            "enable_verifier_hypothesis_reshuffle": not args.disable_verifier_reshuffle,
            "enable_best_repair_action": not args.disable_best_repair_action,
            "enable_tree_reroot": not args.disable_reroot,
        }
    }


def _action_node_id(action: dict | None) -> str:
    if not isinstance(action, dict):
        return ""

    return str(action.get("target_node_id") or "")


def _action_hypothesis_id(action: dict | None) -> str:
    if not isinstance(action, dict):
        return ""

    return str(action.get("hypothesis_id") or "")


def _build_ablation_flags(args: argparse.Namespace) -> dict:
    return {
        "disable_verifier_reshuffle": bool(args.disable_verifier_reshuffle),
        "disable_best_repair_action": bool(args.disable_best_repair_action),
        "disable_reroot": bool(args.disable_reroot),
    }


def _summarize_focused_rows(focused_rows: list[dict], ablation_flags: dict) -> dict:
    stop_reason_counts: dict[str, int] = {}
    repeated_question_turns = 0
    rerooted_turns = 0
    repair_override_turns = 0
    repair_hypothesis_switch_turns = 0
    repair_turns = 0

    for row in focused_rows:
        stop_reason = str(row.get("final_stop_reason") or "")
        stop_reason_counts[stop_reason] = stop_reason_counts.get(stop_reason, 0) + 1

        for turn_summary in row.get("turn_summaries", []):
            if bool(turn_summary.get("same_question_as_previous", False)):
                repeated_question_turns += 1

            if bool(turn_summary.get("rerooted", False)):
                rerooted_turns += 1

            if len(str(turn_summary.get("reject_reason") or "")) > 0:
                repair_turns += 1

            root_best_action = turn_summary.get("root_best_action")
            selected_action = turn_summary.get("selected_action")

            if _action_node_id(root_best_action) != _action_node_id(selected_action):
                if len(_action_node_id(root_best_action)) > 0 and len(_action_node_id(selected_action)) > 0:
                    repair_override_turns += 1

            if _action_hypothesis_id(root_best_action) != _action_hypothesis_id(selected_action):
                if len(_action_hypothesis_id(root_best_action)) > 0 and len(_action_hypothesis_id(selected_action)) > 0:
                    repair_hypothesis_switch_turns += 1

    return {
        "ablation_flags": deepcopy(ablation_flags),
        "case_count": len(focused_rows),
        "stop_reason_counts": stop_reason_counts,
        "max_turn_reached_cases": stop_reason_counts.get("max_turn_reached", 0),
        "repeated_question_turns": repeated_question_turns,
        "rerooted_turns": rerooted_turns,
        "repair_turns": repair_turns,
        "repair_override_turns": repair_override_turns,
        "repair_hypothesis_switch_turns": repair_hypothesis_switch_turns,
    }


def main() -> int:
    args = parse_args()
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    cases = _load_cases(args)

    if len(cases) == 0:
        raise SystemExit("没有匹配到任何 focused replay 病例。")

    if len(args.cases_file.strip()) == 0:
        write_cases_jsonl(cases, output_root / "seed_cases.jsonl")

    config_overrides = _build_config_overrides(args)
    ablation_flags = _build_ablation_flags(args)
    brain = build_default_brain_from_env(config_overrides=config_overrides)
    patient_agent = VirtualPatientAgent()
    focused_rows: list[dict] = []

    for case in cases:
        session_id = f"focused::{case.case_id}"
        brain.start_session(session_id)
        current_output = brain.process_turn(session_id, case.chief_complaint)
        turn_summaries: list[dict] = []
        previous_question_node_id: str | None = None
        initial_summary = _extract_turn_summary(current_output)
        turn_summaries.append(initial_summary)
        previous_question_node_id = (initial_summary.get("pending_action") or {}).get("target_node_id")

        if current_output.get("final_report") is None:
            for _ in range(args.max_turns):
                pending_action = current_output.get("pending_action") or {}
                question_text = str(current_output.get("next_question") or "")
                question_node_id = str(pending_action.get("target_node_id") or "")

                if len(question_text) == 0 or len(question_node_id) == 0:
                    break

                reply = patient_agent.answer_question(question_node_id, question_text, case)
                current_output = brain.process_turn(session_id, reply.answer_text)
                turn_summary = _extract_turn_summary(current_output, previous_question_node_id=previous_question_node_id)
                turn_summary["answer_text"] = reply.answer_text
                turn_summaries.append(turn_summary)
                previous_question_node_id = (turn_summary.get("pending_action") or {}).get("target_node_id")

                if current_output.get("final_report") is not None:
                    break

        final_report = current_output.get("final_report") or brain.finalize(session_id)
        focused_rows.append(
            {
                "ablation_flags": deepcopy(ablation_flags),
                "case_id": case.case_id,
                "case_title": case.title,
                "true_conditions": list(case.true_conditions),
                "true_disease_phase": case.true_disease_phase,
                "turn_summaries": turn_summaries,
                "final_best_answer_name": ((final_report.get("best_final_answer") or {}).get("answer_name")),
                "final_stop_reason": final_report.get("stop_reason"),
                "final_repair_context": final_report.get("repair_context"),
            }
        )

    summary_path = output_root / "focused_repair_summary.jsonl"
    with summary_path.open("w", encoding="utf-8") as handle:
        for row in focused_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    metrics = _summarize_focused_rows(focused_rows, ablation_flags)
    with (output_root / "focused_metrics.json").open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(metrics, ensure_ascii=False, indent=2))

    print(
        json.dumps(
            {
                "ablation_flags": ablation_flags,
                "metrics": metrics,
                "rows": focused_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
