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

    metadata = payload.get("metadata", {})

    if not isinstance(metadata, dict):
        metadata = {}

    return {
        "action_id": payload.get("action_id"),
        "target_node_id": payload.get("target_node_id"),
        "target_node_name": payload.get("target_node_name"),
        "hypothesis_id": payload.get("hypothesis_id"),
        "topic_id": payload.get("topic_id"),
        "evidence_tags": metadata.get("evidence_tags", []) if isinstance(metadata.get("evidence_tags", []), list) else [],
        "question_type_hint": metadata.get("question_type_hint", ""),
    }


def _extract_best_answer_score(search_report: dict, best_answer_id: str, best_answer_name: str) -> dict:
    scores = search_report.get("final_answer_scores", [])

    if not isinstance(scores, list) or len(scores) == 0:
        return {}

    for score in scores:
        if not isinstance(score, dict):
            continue

        if len(best_answer_id) > 0 and str(score.get("answer_id") or "") == best_answer_id:
            return score

    for score in scores:
        if not isinstance(score, dict):
            continue

        if len(best_answer_name) > 0 and str(score.get("answer_name") or "") == best_answer_name:
            return score

    first_score = scores[0]
    return first_score if isinstance(first_score, dict) else {}


def _extract_turn_summary(output: dict, previous_question_node_id: str | None = None) -> dict:
    search_report = output.get("search_report") or {}
    repair_context = search_report.get("repair_context") or {}
    selected_action = _compact_action(search_report.get("selected_action"))
    root_best_action = _compact_action(search_report.get("root_best_action"))
    repair_selected_action = _compact_action(search_report.get("repair_selected_action"))
    pending_action = _compact_action(output.get("pending_action"))
    final_report = output.get("final_report") or {}
    pending_action_audit = output.get("pending_action_audit") or {}

    if not isinstance(pending_action_audit, dict):
        pending_action_audit = {}

    best_final_answer = final_report.get("best_final_answer") or {}
    best_answer_id = str(search_report.get("best_answer_id") or best_final_answer.get("answer_id") or "")
    best_answer_name = str(search_report.get("best_answer_name") or best_final_answer.get("answer_name") or "")
    best_answer_score = _extract_best_answer_score(search_report, best_answer_id, best_answer_name)
    best_answer_metadata = best_answer_score.get("metadata") if isinstance(best_answer_score, dict) else {}

    if not isinstance(best_answer_metadata, dict):
        best_answer_metadata = {}

    verifier_mode = str(best_answer_metadata.get("verifier_mode") or "")
    verifier_called = bool(
        best_answer_metadata.get(
            "verifier_called",
            verifier_mode == "llm_verifier" and "verifier_should_accept" in best_answer_metadata,
        )
    )
    verifier_reject_reason_source = str(best_answer_metadata.get("verifier_reject_reason_source") or "")
    verifier_schema_valid = best_answer_metadata.get("verifier_schema_valid")
    verifier_accept_reason = str(best_answer_metadata.get("verifier_accept_reason") or "")
    verifier_accept_reason_source = str(best_answer_metadata.get("verifier_accept_reason_source") or "")
    verifier_accept_schema_valid = best_answer_metadata.get("verifier_accept_schema_valid")
    verifier_alternative_candidates = best_answer_metadata.get("verifier_alternative_candidates", [])
    guarded_negative_or_doubtful_key_evidence = best_answer_metadata.get(
        "guarded_negative_or_doubtful_key_evidence",
        [],
    )
    verifier_metadata_complete = (
        verifier_mode == "llm_verifier"
        and verifier_called
        and isinstance(best_answer_metadata.get("verifier_should_accept"), bool)
        and isinstance(verifier_schema_valid, bool)
        and len(verifier_reject_reason_source) > 0
        and len(verifier_accept_reason) > 0
        and isinstance(verifier_accept_schema_valid, bool)
        and len(verifier_accept_reason_source) > 0
    )

    current_question_node_id = None
    if pending_action is not None:
        current_question_node_id = pending_action.get("target_node_id")

    return {
        "turn_index": output.get("turn_index"),
        "reject_reason": repair_context.get("reject_reason", ""),
        "recommended_next_evidence": repair_context.get("recommended_next_evidence", []),
        "alternative_candidates": repair_context.get("alternative_candidates", []),
        "repair_mode": repair_context.get("repair_mode", ""),
        "verifier_schema_valid": repair_context.get("verifier_schema_valid"),
        "verifier_reject_reason_source": repair_context.get("verifier_reject_reason_source", ""),
        "rerooted": bool(repair_context.get("rerooted", False)),
        "reroot_reason": repair_context.get("reroot_reason", ""),
        "previous_selected_action": repair_context.get("previous_selected_action"),
        "new_selected_action": repair_context.get("new_selected_action"),
        "selected_action": selected_action,
        "root_best_action": root_best_action,
        "repair_selected_action": repair_selected_action,
        "pending_action": pending_action,
        "route_after_pending_action_stage": (output.get("route_after_pending_action") or {}).get("stage"),
        "best_answer_id": best_answer_id,
        "best_answer_name": best_answer_name,
        "best_answer_verifier_mode": verifier_mode,
        "best_answer_verifier_called": verifier_called,
        "best_answer_verifier_should_accept": best_answer_metadata.get("verifier_should_accept"),
        "best_answer_verifier_reject_reason": best_answer_metadata.get("verifier_reject_reason", ""),
        "best_answer_verifier_accept_reason": verifier_accept_reason,
        "best_answer_verifier_accept_reason_source": verifier_accept_reason_source,
        "best_answer_verifier_accept_schema_valid": verifier_accept_schema_valid,
        "best_answer_verifier_alternative_candidates": (
            verifier_alternative_candidates
            if isinstance(verifier_alternative_candidates, list)
            else []
        ),
        "best_answer_verifier_score": best_answer_score.get("agent_evaluation") if isinstance(best_answer_score, dict) else None,
        "best_answer_verifier_schema_valid": verifier_schema_valid,
        "best_answer_verifier_reject_reason_source": verifier_reject_reason_source,
        "best_answer_verifier_metadata_complete": verifier_metadata_complete,
        "best_answer_acceptance_profile": best_answer_metadata.get("acceptance_profile", ""),
        "best_answer_guarded_acceptance_applied": bool(best_answer_metadata.get("guarded_acceptance_applied", False)),
        "best_answer_guarded_acceptance_block_reason": best_answer_metadata.get("guarded_acceptance_block_reason", ""),
        "best_answer_guarded_high_risk_respiratory_answer": bool(
            best_answer_metadata.get("guarded_high_risk_respiratory_answer", False)
        ),
        "best_answer_guarded_pcp_answer": bool(best_answer_metadata.get("guarded_pcp_answer", False)),
        "best_answer_guarded_has_confirmed_key_evidence": bool(
            best_answer_metadata.get("guarded_has_confirmed_key_evidence", False)
        ),
        "best_answer_guarded_confirmed_key_evidence": best_answer_metadata.get("guarded_confirmed_key_evidence", []),
        "best_answer_guarded_confirmed_key_evidence_families": (
            best_answer_metadata.get("guarded_confirmed_key_evidence_families", [])
            if isinstance(best_answer_metadata.get("guarded_confirmed_key_evidence_families", []), list)
            else []
        ),
        "best_answer_guarded_provisional_key_evidence": (
            best_answer_metadata.get("guarded_provisional_key_evidence", [])
            if isinstance(best_answer_metadata.get("guarded_provisional_key_evidence", []), list)
            else []
        ),
        "best_answer_guarded_provisional_key_evidence_families": (
            best_answer_metadata.get("guarded_provisional_key_evidence_families", [])
            if isinstance(best_answer_metadata.get("guarded_provisional_key_evidence_families", []), list)
            else []
        ),
        "best_answer_guarded_combined_key_evidence_families": (
            best_answer_metadata.get("guarded_combined_key_evidence_families", [])
            if isinstance(best_answer_metadata.get("guarded_combined_key_evidence_families", []), list)
            else []
        ),
        "best_answer_guarded_missing_evidence_families": (
            best_answer_metadata.get("guarded_missing_evidence_families", [])
            if isinstance(best_answer_metadata.get("guarded_missing_evidence_families", []), list)
            else []
        ),
        "best_answer_guarded_pcp_combo_satisfied": bool(
            best_answer_metadata.get("guarded_pcp_combo_satisfied", False)
        ),
        "best_answer_guarded_confirmed_pcp_combo_satisfied": bool(
            best_answer_metadata.get("guarded_confirmed_pcp_combo_satisfied", False)
        ),
        "best_answer_guarded_pcp_combo_uses_provisional": bool(
            best_answer_metadata.get("guarded_pcp_combo_uses_provisional", False)
        ),
        "best_answer_guarded_pcp_combo_variant": best_answer_metadata.get("guarded_pcp_combo_variant", ""),
        "best_answer_guarded_pcp_combo_missing_family_options": (
            best_answer_metadata.get("guarded_pcp_combo_missing_family_options", [])
            if isinstance(best_answer_metadata.get("guarded_pcp_combo_missing_family_options", []), list)
            else []
        ),
        "best_answer_guarded_has_negative_or_doubtful_key_evidence": bool(
            best_answer_metadata.get("guarded_has_negative_or_doubtful_key_evidence", False)
        ),
        "best_answer_guarded_negative_or_doubtful_key_evidence": (
            guarded_negative_or_doubtful_key_evidence
            if isinstance(guarded_negative_or_doubtful_key_evidence, list)
            else []
        ),
        "best_answer_guarded_hard_negative_key_evidence": (
            best_answer_metadata.get("guarded_hard_negative_key_evidence", [])
            if isinstance(best_answer_metadata.get("guarded_hard_negative_key_evidence", []), list)
            else []
        ),
        "best_answer_guarded_soft_negative_or_doubtful_key_evidence": (
            best_answer_metadata.get("guarded_soft_negative_or_doubtful_key_evidence", [])
            if isinstance(best_answer_metadata.get("guarded_soft_negative_or_doubtful_key_evidence", []), list)
            else []
        ),
        "best_answer_guarded_soft_negative_requires_stability": bool(
            best_answer_metadata.get("guarded_soft_negative_requires_stability", False)
        ),
        "best_answer_guarded_recent_hypothesis_switch": bool(
            best_answer_metadata.get("guarded_recent_hypothesis_switch", False)
        ),
        "best_answer_guarded_answer_changed_after_first_accept": bool(
            best_answer_metadata.get("guarded_answer_changed_after_first_accept", False)
        ),
        "best_answer_guarded_nonempty_alternative_candidates": bool(
            best_answer_metadata.get("guarded_nonempty_alternative_candidates", False)
        ),
        "best_answer_guarded_has_strong_unresolved_alternative": bool(
            best_answer_metadata.get("guarded_has_strong_unresolved_alternative", False)
        ),
        "best_answer_guarded_strong_alternative_candidates": (
            best_answer_metadata.get("guarded_strong_alternative_candidates", [])
            if isinstance(best_answer_metadata.get("guarded_strong_alternative_candidates", []), list)
            else []
        ),
        "best_answer_guarded_weak_or_ruled_down_alternative_candidates": (
            best_answer_metadata.get("guarded_weak_or_ruled_down_alternative_candidates", [])
            if isinstance(best_answer_metadata.get("guarded_weak_or_ruled_down_alternative_candidates", []), list)
            else []
        ),
        "best_answer_guarded_recent_key_evidence_states": (
            best_answer_metadata.get("guarded_recent_key_evidence_states", [])
            if isinstance(best_answer_metadata.get("guarded_recent_key_evidence_states", []), list)
            else []
        ),
        "best_answer_guarded_gate_audit": (
            best_answer_metadata.get("guarded_gate_audit", {})
            if isinstance(best_answer_metadata.get("guarded_gate_audit", {}), dict)
            else {}
        ),
        "stop_reason": final_report.get("stop_reason"),
        "pending_action_audit": pending_action_audit,
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
    acceptance_category_counts = {
        "correct_accepted": 0,
        "correct_rejected": 0,
        "wrong_rejected": 0,
        "wrong_accepted": 0,
    }
    repeated_question_turns = 0
    rerooted_turns = 0
    repair_override_turns = 0
    repair_hypothesis_switch_turns = 0
    repair_turns = 0
    first_correct_turns: dict[str, int | None] = {}
    first_verifier_accept_turns: dict[str, int | None] = {}
    correct_but_rejected_spans: dict[str, int] = {}
    verifier_called_count = 0
    accepted_with_verifier_metadata_count = 0
    accepted_without_verifier_metadata_count = 0
    accepted_on_turn1_count = 0
    wrong_accept_on_turn1_count = 0
    accept_reason_counts: dict[str, int] = {}
    wrong_accept_reason_counts: dict[str, int] = {}
    first_verifier_accept_turns_for_final_answer: dict[str, int | None] = {}
    final_answer_changed_after_first_accept_count = 0
    accepted_after_negative_key_evidence_count = 0
    accepted_after_recent_hypothesis_switch_count = 0
    accepted_with_nonempty_alternative_candidates_count = 0
    guarded_block_reason_counts: dict[str, int] = {}
    verifier_positive_but_gate_rejected_count = 0
    accept_candidate_without_confirmed_combo_count = 0
    guarded_gate_audit_records: list[dict] = []
    guarded_negative_evidence_node_counts: dict[str, int] = {}
    guarded_negative_evidence_family_counts: dict[str, int] = {}
    guarded_negative_evidence_tier_counts: dict[str, int] = {}
    guarded_negative_evidence_scope_counts: dict[str, int] = {}
    strong_alternative_block_count = 0
    weak_alternative_allowed_count = 0
    combo_satisfied_but_alternative_blocked_count = 0
    pending_action_audit_records: list[dict] = []
    pending_action_audit_record_count = 0
    pending_action_confirmed_family_candidate_count = 0
    pending_action_provisional_family_candidate_count = 0
    provisional_family_used_count = 0
    provisional_combo_satisfied_count = 0
    accepted_with_provisional_combo_count = 0
    missing_family_first_selected_count = 0
    missing_family_repair_turn_count = 0
    combo_anchor_selected_before_turn3_count = 0
    family_recorded_after_question_count = 0
    family_recorded_after_question_attempt_count = 0

    for row in focused_rows:
        stop_reason = str(row.get("final_stop_reason") or "")
        stop_reason_counts[stop_reason] = stop_reason_counts.get(stop_reason, 0) + 1
        acceptance_category = _acceptance_category(row)
        acceptance_category_counts[acceptance_category] = acceptance_category_counts.get(acceptance_category, 0) + 1
        timeline = _compute_acceptance_timeline(row)
        case_id = str(row.get("case_id") or f"case_{len(first_correct_turns) + 1}")
        first_correct_turns[case_id] = timeline["first_correct_best_answer_turn"]
        first_verifier_accept_turns[case_id] = timeline["first_verifier_accept_turn"]
        first_verifier_accept_turns_for_final_answer[case_id] = timeline[
            "first_verifier_accept_turn_for_final_answer"
        ]
        correct_but_rejected_spans[case_id] = int(timeline["correct_but_rejected_span"])

        if bool(timeline["final_answer_changed_after_first_accept"]):
            final_answer_changed_after_first_accept_count += 1

        if _is_final_answer_accepted(row):
            final_turn = _find_final_turn_summary(row)

            if _accepted_row_has_complete_verifier_metadata(row):
                accepted_with_verifier_metadata_count += 1
            else:
                accepted_without_verifier_metadata_count += 1

            if timeline["first_verifier_accept_turn"] == 1:
                accepted_on_turn1_count += 1

                if not _is_correct_best_answer(row):
                    wrong_accept_on_turn1_count += 1

            if final_turn is not None:
                if not _is_correct_best_answer(row):
                    wrong_accept_reason = str(final_turn.get("best_answer_verifier_accept_reason") or "missing")
                    wrong_accept_reason_counts[wrong_accept_reason] = wrong_accept_reason_counts.get(
                        wrong_accept_reason,
                        0,
                    ) + 1

                if bool(final_turn.get("best_answer_guarded_has_negative_or_doubtful_key_evidence", False)):
                    accepted_after_negative_key_evidence_count += 1

                if bool(final_turn.get("best_answer_guarded_recent_hypothesis_switch", False)):
                    accepted_after_recent_hypothesis_switch_count += 1

                verifier_alternatives = final_turn.get("best_answer_verifier_alternative_candidates", [])
                has_verifier_alternatives = isinstance(verifier_alternatives, list) and len(verifier_alternatives) > 0

                if bool(final_turn.get("best_answer_guarded_nonempty_alternative_candidates", False)) or has_verifier_alternatives:
                    accepted_with_nonempty_alternative_candidates_count += 1

                if bool(final_turn.get("best_answer_guarded_pcp_combo_uses_provisional", False)):
                    accepted_with_provisional_combo_count += 1

        family_repair_metrics = _compute_missing_family_repair_metrics(row)
        missing_family_first_selected_count += int(family_repair_metrics["missing_family_first_selected_count"])
        missing_family_repair_turn_count += int(family_repair_metrics["missing_family_repair_turn_count"])
        combo_anchor_selected_before_turn3_count += int(
            family_repair_metrics["combo_anchor_selected_before_turn3_count"]
        )
        family_recorded_after_question_count += int(family_repair_metrics["family_recorded_after_question_count"])
        family_recorded_after_question_attempt_count += int(
            family_repair_metrics["family_recorded_after_question_attempt_count"]
        )

        for turn_summary in row.get("turn_summaries", []):
            guarded_block_reason = str(turn_summary.get("best_answer_guarded_acceptance_block_reason") or "")
            strong_alternatives = turn_summary.get("best_answer_guarded_strong_alternative_candidates", [])
            weak_alternatives = turn_summary.get("best_answer_guarded_weak_or_ruled_down_alternative_candidates", [])
            has_strong_alternatives = isinstance(strong_alternatives, list) and len(strong_alternatives) > 0
            has_weak_alternatives = isinstance(weak_alternatives, list) and len(weak_alternatives) > 0

            if len(guarded_block_reason) > 0:
                guarded_block_reason_counts[guarded_block_reason] = (
                    guarded_block_reason_counts.get(guarded_block_reason, 0) + 1
                )

            if guarded_block_reason == "strong_unresolved_alternative_candidates":
                strong_alternative_block_count += 1

                if bool(turn_summary.get("best_answer_guarded_pcp_combo_satisfied", False)):
                    combo_satisfied_but_alternative_blocked_count += 1

            if (
                turn_summary.get("best_answer_verifier_should_accept") is True
                and has_weak_alternatives
                and not has_strong_alternatives
                and guarded_block_reason != "strong_unresolved_alternative_candidates"
            ):
                weak_alternative_allowed_count += 1

            evidence_audit = turn_summary.get("pending_action_audit", {})

            if isinstance(evidence_audit, dict) and len(evidence_audit) > 0:
                pending_action_audit_record_count += 1
                pending_action_audit_records.append(
                    {
                        "case_id": row.get("case_id"),
                        **evidence_audit,
                    }
                )

                if bool(evidence_audit.get("confirmed_family_candidate", False)):
                    pending_action_confirmed_family_candidate_count += 1

                if bool(evidence_audit.get("provisional_family_candidate", False)):
                    pending_action_provisional_family_candidate_count += 1

            if len(_string_set(turn_summary.get("best_answer_guarded_provisional_key_evidence_families", []))) > 0:
                provisional_family_used_count += 1

            if bool(turn_summary.get("best_answer_guarded_pcp_combo_uses_provisional", False)):
                provisional_combo_satisfied_count += 1

            if _turn_has_verifier_call(turn_summary):
                verifier_called_count += 1

                if turn_summary.get("best_answer_verifier_should_accept") is True:
                    accept_reason = str(turn_summary.get("best_answer_verifier_accept_reason") or "missing")
                    accept_reason_counts[accept_reason] = accept_reason_counts.get(accept_reason, 0) + 1

                    if len(guarded_block_reason) > 0:
                        verifier_positive_but_gate_rejected_count += 1
                        audit_record = _build_guarded_gate_audit_record(row, turn_summary, guarded_block_reason)
                        guarded_gate_audit_records.append(audit_record)
                        _accumulate_guarded_negative_evidence_counts(
                            audit_record,
                            node_counts=guarded_negative_evidence_node_counts,
                            family_counts=guarded_negative_evidence_family_counts,
                            tier_counts=guarded_negative_evidence_tier_counts,
                            scope_counts=guarded_negative_evidence_scope_counts,
                        )

                    if _accept_candidate_without_confirmed_combo(turn_summary):
                        accept_candidate_without_confirmed_combo_count += 1

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
        "accepted_correct_count": acceptance_category_counts.get("correct_accepted", 0),
        "accepted_wrong_count": acceptance_category_counts.get("wrong_accepted", 0),
        "correct_best_answer_but_rejected_count": acceptance_category_counts.get("correct_rejected", 0),
        "wrong_best_answer_rejected_count": acceptance_category_counts.get("wrong_rejected", 0),
        "acceptance_category_counts": acceptance_category_counts,
        "first_correct_best_answer_turns": first_correct_turns,
        "first_verifier_accept_turns": first_verifier_accept_turns,
        "correct_but_rejected_spans": correct_but_rejected_spans,
        "avg_first_correct_best_answer_turn": _average_optional_ints(first_correct_turns.values()),
        "avg_first_verifier_accept_turn": _average_optional_ints(first_verifier_accept_turns.values()),
        "avg_correct_but_rejected_span": _average_ints(correct_but_rejected_spans.values()),
        "verifier_called_count": verifier_called_count,
        "accepted_with_verifier_metadata_count": accepted_with_verifier_metadata_count,
        "accepted_without_verifier_metadata_count": accepted_without_verifier_metadata_count,
        "accepted_on_turn1_count": accepted_on_turn1_count,
        "wrong_accept_on_turn1_count": wrong_accept_on_turn1_count,
        "accept_reason_counts": accept_reason_counts,
        "wrong_accept_reason_counts": wrong_accept_reason_counts,
        "median_first_verifier_accept_turn": _median_optional_ints(first_verifier_accept_turns.values()),
        "first_verifier_accept_turn_for_final_answer": first_verifier_accept_turns_for_final_answer,
        "median_first_verifier_accept_turn_for_final_answer": _median_optional_ints(
            first_verifier_accept_turns_for_final_answer.values()
        ),
        "final_answer_changed_after_first_accept_count": final_answer_changed_after_first_accept_count,
        "accepted_after_negative_key_evidence_count": accepted_after_negative_key_evidence_count,
        "accepted_after_recent_hypothesis_switch_count": accepted_after_recent_hypothesis_switch_count,
        "accepted_with_nonempty_alternative_candidates_count": accepted_with_nonempty_alternative_candidates_count,
        "guarded_block_reason_counts": guarded_block_reason_counts,
        "verifier_positive_but_gate_rejected_count": verifier_positive_but_gate_rejected_count,
        "accept_candidate_without_confirmed_combo_count": accept_candidate_without_confirmed_combo_count,
        "guarded_gate_audit_records": guarded_gate_audit_records,
        "guarded_negative_evidence_node_counts": guarded_negative_evidence_node_counts,
        "guarded_negative_evidence_family_counts": guarded_negative_evidence_family_counts,
        "guarded_negative_evidence_tier_counts": guarded_negative_evidence_tier_counts,
        "guarded_negative_evidence_scope_counts": guarded_negative_evidence_scope_counts,
        "strong_alternative_block_count": strong_alternative_block_count,
        "weak_alternative_allowed_count": weak_alternative_allowed_count,
        "combo_satisfied_but_alternative_blocked_count": combo_satisfied_but_alternative_blocked_count,
        "pending_action_audit_record_count": pending_action_audit_record_count,
        "pending_action_confirmed_family_candidate_count": pending_action_confirmed_family_candidate_count,
        "pending_action_provisional_family_candidate_count": pending_action_provisional_family_candidate_count,
        "provisional_family_used_count": provisional_family_used_count,
        "provisional_combo_satisfied_count": provisional_combo_satisfied_count,
        "accepted_with_provisional_combo_count": accepted_with_provisional_combo_count,
        "pending_action_audit_records": pending_action_audit_records,
        "missing_family_first_selected_count": missing_family_first_selected_count,
        "missing_family_repair_turn_count": missing_family_repair_turn_count,
        "combo_anchor_selected_before_turn3_count": combo_anchor_selected_before_turn3_count,
        "family_recorded_after_question_count": family_recorded_after_question_count,
        "family_recorded_after_question_attempt_count": family_recorded_after_question_attempt_count,
    }


def _acceptance_category(row: dict) -> str:
    is_correct = _is_correct_best_answer(row)
    is_accepted = _is_final_answer_accepted(row)

    if is_correct and is_accepted:
        return "correct_accepted"

    if is_correct:
        return "correct_rejected"

    if is_accepted:
        return "wrong_accepted"

    return "wrong_rejected"


def _is_final_answer_accepted(row: dict) -> bool:
    return str(row.get("final_stop_reason") or "") == "final_answer_accepted"


def _turn_has_verifier_call(turn_summary: dict) -> bool:
    if not isinstance(turn_summary, dict):
        return False

    return bool(turn_summary.get("best_answer_verifier_called", False)) or (
        str(turn_summary.get("best_answer_verifier_mode") or "") == "llm_verifier"
        and turn_summary.get("best_answer_verifier_should_accept") is not None
    )


def _accept_candidate_without_confirmed_combo(turn_summary: dict) -> bool:
    if not isinstance(turn_summary, dict):
        return False

    if turn_summary.get("best_answer_verifier_should_accept") is not True:
        return False

    is_high_risk = bool(turn_summary.get("best_answer_guarded_high_risk_respiratory_answer", False))
    is_pcp = bool(turn_summary.get("best_answer_guarded_pcp_answer", False))
    has_confirmed_key = bool(turn_summary.get("best_answer_guarded_has_confirmed_key_evidence", False))
    pcp_combo_satisfied = bool(turn_summary.get("best_answer_guarded_pcp_combo_satisfied", False))

    if is_pcp and not pcp_combo_satisfied:
        return True

    return is_high_risk and not has_confirmed_key


def _build_guarded_gate_audit_record(row: dict, turn_summary: dict, guarded_block_reason: str) -> dict:
    audit_payload = turn_summary.get("best_answer_guarded_gate_audit", {})

    if not isinstance(audit_payload, dict):
        audit_payload = {}

    return {
        "case_id": row.get("case_id"),
        "turn_index": turn_summary.get("turn_index"),
        "block_reason": audit_payload.get("block_reason", guarded_block_reason),
        "current_answer_name": audit_payload.get(
            "current_answer_name",
            turn_summary.get("best_answer_name"),
        ),
        "confirmed_evidence_families": audit_payload.get(
            "confirmed_evidence_families",
            turn_summary.get("best_answer_guarded_confirmed_key_evidence_families", []),
        ),
        "provisional_evidence_families": audit_payload.get(
            "provisional_evidence_families",
            turn_summary.get("best_answer_guarded_provisional_key_evidence_families", []),
        ),
        "combined_evidence_families": audit_payload.get(
            "combined_evidence_families",
            turn_summary.get("best_answer_guarded_combined_key_evidence_families", []),
        ),
        "missing_families": audit_payload.get(
            "missing_families",
            turn_summary.get("best_answer_guarded_missing_evidence_families", []),
        ),
        "pcp_combo_uses_provisional": audit_payload.get(
            "pcp_combo_uses_provisional",
            turn_summary.get("best_answer_guarded_pcp_combo_uses_provisional", False),
        ),
        "alternative_candidates": audit_payload.get(
            "alternative_candidates",
            turn_summary.get("best_answer_verifier_alternative_candidates", []),
        ),
        "strong_alternative_candidates": audit_payload.get(
            "strong_alternative_candidates",
            turn_summary.get("best_answer_guarded_strong_alternative_candidates", []),
        ),
        "weak_or_ruled_down_alternative_candidates": audit_payload.get(
            "weak_or_ruled_down_alternative_candidates",
            turn_summary.get("best_answer_guarded_weak_or_ruled_down_alternative_candidates", []),
        ),
        "recent_key_evidence_states": audit_payload.get(
            "recent_key_evidence_states",
            turn_summary.get("best_answer_guarded_recent_key_evidence_states", []),
        ),
        "hard_negative_key_evidence": audit_payload.get(
            "hard_negative_key_evidence",
            turn_summary.get("best_answer_guarded_hard_negative_key_evidence", []),
        ),
        "soft_negative_or_doubtful_key_evidence": audit_payload.get(
            "soft_negative_or_doubtful_key_evidence",
            turn_summary.get("best_answer_guarded_soft_negative_or_doubtful_key_evidence", []),
        ),
        "soft_negative_requires_stability": audit_payload.get(
            "soft_negative_requires_stability",
            turn_summary.get("best_answer_guarded_soft_negative_requires_stability", False),
        ),
        "pcp_combo_missing_family_options": audit_payload.get(
            "pcp_combo_missing_family_options",
            turn_summary.get("best_answer_guarded_pcp_combo_missing_family_options", []),
        ),
        "pcp_combo_variant": audit_payload.get(
            "pcp_combo_variant",
            turn_summary.get("best_answer_guarded_pcp_combo_variant", ""),
        ),
    }


def _accumulate_guarded_negative_evidence_counts(
    audit_record: dict,
    *,
    node_counts: dict[str, int],
    family_counts: dict[str, int],
    tier_counts: dict[str, int],
    scope_counts: dict[str, int],
) -> None:
    evidence_items = []
    evidence_items.extend(audit_record.get("hard_negative_key_evidence", []) or [])
    evidence_items.extend(audit_record.get("soft_negative_or_doubtful_key_evidence", []) or [])

    for item in evidence_items:
        if not isinstance(item, dict):
            continue

        node_name = str(item.get("name") or item.get("node_id") or "unknown")
        node_counts[node_name] = node_counts.get(node_name, 0) + 1
        tier = str(item.get("negative_evidence_tier") or "unknown")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        scope = str(item.get("evidence_scope") or "unknown")
        scope_counts[scope] = scope_counts.get(scope, 0) + 1

        families = item.get("evidence_families", [])

        if not isinstance(families, list) or len(families) == 0:
            families = ["unknown"]

        for family in families:
            family_name = str(family)
            family_counts[family_name] = family_counts.get(family_name, 0) + 1


def _compute_missing_family_repair_metrics(row: dict) -> dict:
    turn_summaries = row.get("turn_summaries", [])

    if not isinstance(turn_summaries, list):
        turn_summaries = []

    missing_family_first_selected_count = 0
    missing_family_repair_turn_count = 0
    combo_anchor_selected_before_turn3_count = 0
    family_recorded_after_question_count = 0
    family_recorded_after_question_attempt_count = 0

    for index, turn_summary in enumerate(turn_summaries):
        if not isinstance(turn_summary, dict):
            continue

        action = _selected_next_action(turn_summary)
        action_families = _action_families(action)
        missing_families = _string_set(turn_summary.get("best_answer_guarded_missing_evidence_families", []))
        block_reason = str(turn_summary.get("best_answer_guarded_acceptance_block_reason") or "")

        if _is_combo_anchor_action(action) and _coerce_optional_int(turn_summary.get("turn_index")) in {1, 2, 3}:
            combo_anchor_selected_before_turn3_count += 1

        if block_reason in {"pcp_combo_insufficient", "missing_confirmed_key_evidence"} and len(missing_families) > 0:
            missing_family_repair_turn_count += 1

            matched_families = action_families & missing_families

            if len(matched_families) > 0:
                missing_family_first_selected_count += 1

                if index + 1 < len(turn_summaries):
                    family_recorded_after_question_attempt_count += 1
                    next_turn = turn_summaries[index + 1]
                    next_confirmed_families = _string_set(
                        next_turn.get("best_answer_guarded_confirmed_key_evidence_families", [])
                    ) if isinstance(next_turn, dict) else set()

                    if len(matched_families & next_confirmed_families) > 0:
                        family_recorded_after_question_count += 1

    return {
        "missing_family_first_selected_count": missing_family_first_selected_count,
        "missing_family_repair_turn_count": missing_family_repair_turn_count,
        "combo_anchor_selected_before_turn3_count": combo_anchor_selected_before_turn3_count,
        "family_recorded_after_question_count": family_recorded_after_question_count,
        "family_recorded_after_question_attempt_count": family_recorded_after_question_attempt_count,
    }


def _selected_next_action(turn_summary: dict) -> dict:
    for key in ("pending_action", "repair_selected_action", "selected_action"):
        action = turn_summary.get(key)

        if isinstance(action, dict):
            return action

    return {}


def _action_families(action: dict) -> set[str]:
    families = _string_set(action.get("evidence_tags", []))
    text = _normalize_answer_text(
        " ".join(
            [
                str(action.get("target_node_name") or ""),
                str(action.get("target_node_id") or ""),
                str(action.get("question_type_hint") or ""),
            ]
        )
    )
    family_rules = {
        "immune_status": ("hiv", "cd4", "t淋巴", "免疫", "艾滋"),
        "imaging": ("ct", "影像", "x线", "胸片", "磨玻璃", "双肺"),
        "oxygenation": ("低氧", "血氧", "pao2", "氧分压", "氧合", "呼吸衰竭"),
        "respiratory": ("发热", "干咳", "咳嗽", "呼吸困难", "气促"),
        "pathogen": ("βd葡聚糖", "bdg", "葡聚糖", "病原", "痰", "balf", "pcr", "核酸", "支气管肺泡"),
        "pcp_specific": ("肺孢子", "pcp", "pneumocystis", "βd葡聚糖", "bdg", "葡聚糖"),
    }

    for family, keywords in family_rules.items():
        if any(keyword in text for keyword in keywords):
            families.add(family)

    return {item for item in families if not item.startswith("type:")}


def _is_combo_anchor_action(action: dict) -> bool:
    action_families = _action_families(action)

    if len(action_families & {"immune_status", "pathogen", "pcp_specific"}) > 0:
        return True

    text = _normalize_answer_text(
        " ".join(
            [
                str(action.get("target_node_name") or ""),
                str(action.get("target_node_id") or ""),
            ]
        )
    )
    anchor_keywords = ("cd4", "βd葡聚糖", "bdg", "葡聚糖", "pcppcr", "肺孢子", "支气管肺泡", "bal", "balf")
    return any(keyword in text for keyword in anchor_keywords)


def _string_set(payload: object) -> set[str]:
    if not isinstance(payload, list):
        return set()

    return {str(item).strip() for item in payload if len(str(item).strip()) > 0}


def _accepted_row_has_complete_verifier_metadata(row: dict) -> bool:
    final_turn = _find_final_turn_summary(row)

    if final_turn is None:
        return False

    return bool(final_turn.get("best_answer_verifier_metadata_complete", False))


def _find_final_turn_summary(row: dict) -> dict | None:
    turn_summaries = row.get("turn_summaries", [])

    if not isinstance(turn_summaries, list) or len(turn_summaries) == 0:
        return None

    for turn_summary in reversed(turn_summaries):
        if not isinstance(turn_summary, dict):
            continue

        if str(turn_summary.get("stop_reason") or "") == "final_answer_accepted":
            return turn_summary

    last_turn = turn_summaries[-1]
    return last_turn if isinstance(last_turn, dict) else None


def _is_correct_best_answer(row: dict) -> bool:
    return _is_correct_answer_name(str(row.get("final_best_answer_name") or ""), row)


def _is_correct_answer_name(answer_name: str, row: dict) -> bool:
    answer_name = _normalize_answer_text(str(answer_name or ""))

    if len(answer_name) == 0:
        return False

    expected_answers = []
    true_phase = str(row.get("true_disease_phase") or "").strip()

    if len(true_phase) > 0:
        expected_answers.append(true_phase)

    true_conditions = row.get("true_conditions", [])

    if isinstance(true_conditions, list):
        expected_answers.extend(str(item).strip() for item in true_conditions if len(str(item).strip()) > 0)

    for expected in expected_answers:
        normalized_expected = _normalize_answer_text(expected)

        if len(normalized_expected) == 0:
            continue

        if answer_name == normalized_expected or answer_name in normalized_expected or normalized_expected in answer_name:
            return True

    return False


def _annotate_acceptance_timeline(row: dict) -> dict:
    row.update(_compute_acceptance_timeline(row))
    return row


def _compute_acceptance_timeline(row: dict) -> dict:
    turn_summaries = row.get("turn_summaries", [])

    if not isinstance(turn_summaries, list):
        turn_summaries = []

    first_correct_position: int | None = None
    first_verifier_accept_position: int | None = None
    first_verifier_accept_for_final_answer_position: int | None = None
    first_correct_turn: int | None = None
    first_verifier_accept_turn: int | None = None
    first_verifier_accept_turn_for_final_answer: int | None = None
    first_verifier_accept_answer_name = ""
    final_answer_name = str(row.get("final_best_answer_name") or "")

    for position, turn_summary in enumerate(turn_summaries):
        if not isinstance(turn_summary, dict):
            continue

        turn_index = _coerce_optional_int(turn_summary.get("turn_index"))

        if first_correct_position is None and _is_correct_answer_name(str(turn_summary.get("best_answer_name") or ""), row):
            first_correct_position = position
            first_correct_turn = turn_index

        verifier_accepts = turn_summary.get("best_answer_verifier_should_accept") is True or str(
            turn_summary.get("stop_reason") or ""
        ) == "final_answer_accepted"

        if first_verifier_accept_position is None and verifier_accepts:
            first_verifier_accept_position = position
            first_verifier_accept_turn = turn_index
            first_verifier_accept_answer_name = str(turn_summary.get("best_answer_name") or "")

        if (
            first_verifier_accept_for_final_answer_position is None
            and verifier_accepts
            and _same_answer_name(str(turn_summary.get("best_answer_name") or ""), final_answer_name)
        ):
            first_verifier_accept_for_final_answer_position = position
            first_verifier_accept_turn_for_final_answer = turn_index

    if first_correct_position is None and _is_correct_best_answer(row):
        first_correct_position = max(len(turn_summaries) - 1, 0) if len(turn_summaries) > 0 else None
        first_correct_turn = (
            _coerce_optional_int(turn_summaries[first_correct_position].get("turn_index"))
            if first_correct_position is not None
            else None
        )

    correct_but_rejected_span = _compute_correct_but_rejected_span(
        row,
        turn_summaries,
        first_correct_position=first_correct_position,
        first_verifier_accept_position=first_verifier_accept_position,
    )
    return {
        "first_correct_best_answer_turn": first_correct_turn,
        "first_verifier_accept_turn": first_verifier_accept_turn,
        "first_verifier_accept_turn_for_final_answer": first_verifier_accept_turn_for_final_answer,
        "final_answer_changed_after_first_accept": (
            len(first_verifier_accept_answer_name) > 0
            and len(final_answer_name) > 0
            and not _same_answer_name(first_verifier_accept_answer_name, final_answer_name)
        ),
        "correct_but_rejected_span": correct_but_rejected_span,
    }


def _compute_correct_but_rejected_span(
    row: dict,
    turn_summaries: list,
    first_correct_position: int | None,
    first_verifier_accept_position: int | None,
) -> int:
    if first_correct_position is None:
        return 0

    if first_verifier_accept_position is not None and first_verifier_accept_position >= first_correct_position:
        return max(first_verifier_accept_position - first_correct_position, 0)

    span = 0

    for turn_summary in turn_summaries[first_correct_position:]:
        if not isinstance(turn_summary, dict):
            continue

        if not _is_correct_answer_name(str(turn_summary.get("best_answer_name") or ""), row):
            continue

        if turn_summary.get("best_answer_verifier_should_accept") is True:
            break

        if str(turn_summary.get("stop_reason") or "") == "final_answer_accepted":
            break

        span += 1

    return span


def _coerce_optional_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def _average_optional_ints(values) -> float | None:
    present_values = [int(value) for value in values if value is not None]

    if len(present_values) == 0:
        return None

    return sum(present_values) / len(present_values)


def _average_ints(values) -> float:
    values = [int(value) for value in values]

    if len(values) == 0:
        return 0.0

    return sum(values) / len(values)


def _median_optional_ints(values) -> float | None:
    present_values = sorted(int(value) for value in values if value is not None)

    if len(present_values) == 0:
        return None

    midpoint = len(present_values) // 2

    if len(present_values) % 2 == 1:
        return float(present_values[midpoint])

    return (present_values[midpoint - 1] + present_values[midpoint]) / 2


def _normalize_answer_text(text: str) -> str:
    return (
        text.strip()
        .lower()
        .replace(" ", "")
        .replace("（", "(")
        .replace("）", ")")
        .replace("，", ",")
        .replace("。", "")
        .replace("、", "")
        .replace("-", "")
        .replace("_", "")
        .replace("/", "")
    )


def _same_answer_name(left: str, right: str) -> bool:
    normalized_left = _normalize_answer_text(left)
    normalized_right = _normalize_answer_text(right)

    if len(normalized_left) == 0 or len(normalized_right) == 0:
        return False

    return (
        normalized_left == normalized_right
        or normalized_left in normalized_right
        or normalized_right in normalized_left
    )


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
    patient_agent = VirtualPatientAgent(use_llm=True)
    focused_rows: list[dict] = []

    for case in cases:
        session_id = f"focused::{case.case_id}"
        brain.start_session(session_id)
        opening = patient_agent.open_case(case)
        current_output = brain.process_turn(session_id, opening.opening_text)
        turn_summaries: list[dict] = []
        previous_question_node_id: str | None = None
        initial_summary = _extract_turn_summary(current_output)
        initial_summary["opening_text"] = opening.opening_text
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
        row = {
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
        row["is_best_answer_correct"] = _is_correct_best_answer(row)
        row["acceptance_category"] = _acceptance_category(row)
        _annotate_acceptance_timeline(row)
        focused_rows.append(row)

    summary_path = output_root / "focused_repair_summary.jsonl"
    with summary_path.open("w", encoding="utf-8") as handle:
        for row in focused_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    metrics = _summarize_focused_rows(focused_rows, ablation_flags)
    with (output_root / "focused_metrics.json").open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(metrics, ensure_ascii=False, indent=2))
    with (output_root / "guarded_gate_audit.jsonl").open("w", encoding="utf-8") as handle:
        for record in metrics.get("guarded_gate_audit_records", []):
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    with (output_root / "pending_action_audit.jsonl").open("w", encoding="utf-8") as handle:
        for record in metrics.get("pending_action_audit_records", []):
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

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
