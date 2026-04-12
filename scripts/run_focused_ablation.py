"""运行 focused repair ablation，并统一导出可复现实验摘要。"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain.service import build_default_brain_from_env
from scripts.run_focused_repair_replay import (
    _acceptance_category,
    _annotate_acceptance_timeline,
    _extract_turn_summary,
    _is_correct_best_answer,
    _summarize_focused_rows,
    _turn_has_verifier_call,
)
from simulator.generate_cases import build_seed_cases, load_cases_jsonl, write_cases_jsonl
from simulator.patient_agent import VirtualPatientAgent


VARIANT_REPAIR_OVERRIDES = {
    "baseline": {
        "enable_verifier_hypothesis_reshuffle": True,
        "enable_best_repair_action": True,
        "enable_tree_reroot": True,
    },
    "no_best_repair_action": {
        "enable_verifier_hypothesis_reshuffle": True,
        "enable_best_repair_action": False,
        "enable_tree_reroot": True,
    },
    "no_reshuffle": {
        "enable_verifier_hypothesis_reshuffle": False,
        "enable_best_repair_action": True,
        "enable_tree_reroot": True,
    },
    "no_reroot": {
        "enable_verifier_hypothesis_reshuffle": True,
        "enable_best_repair_action": True,
        "enable_tree_reroot": False,
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 focused repair ablation 标准实验。")
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
        "--case-list",
        default="",
        help="可选病例 id 文件，每行一个 id；会与 --case-ids 合并。",
    )
    parser.add_argument(
        "--variants",
        default="baseline,no_best_repair_action,no_reshuffle,no_reroot",
        help="逗号分隔的实验变体。",
    )
    parser.add_argument(
        "--output-root",
        default=str(PROJECT_ROOT / "test_outputs" / "simulator_replay" / "focused_ablation"),
        help="ablation 输出根目录。",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=3,
        help="每个病例最大追问轮次。",
    )
    parser.add_argument(
        "--case-concurrency",
        type=int,
        default=1,
        help="同一 variant 内并行运行的病例数；每个病例使用独立 brain 实例。",
    )
    parser.add_argument(
        "--min-answer-consistency",
        type=float,
        default=None,
        help="可选 stop 阈值覆盖：min_answer_consistency。",
    )
    parser.add_argument(
        "--min-agent-eval-score",
        type=float,
        default=None,
        help="可选 stop 阈值覆盖：min_agent_eval_score。",
    )
    parser.add_argument(
        "--min-final-score",
        type=float,
        default=None,
        help="可选 stop 阈值覆盖：min_final_score。",
    )
    parser.add_argument(
        "--min-turn-index-before-final-answer",
        type=int,
        default=None,
        help="可选 stop 阈值覆盖：min_turn_index_before_final_answer。",
    )
    parser.add_argument(
        "--min-trajectory-count-before-accept",
        type=int,
        default=None,
        help="可选 stop 阈值覆盖：min_trajectory_count_before_accept。",
    )
    parser.add_argument(
        "--allow-verifier-rejected-stop",
        action="store_true",
        help="校准实验用：允许在 verifier_should_accept=false 时继续用数值阈值判断是否接受。",
    )
    return parser.parse_args()


def _load_cases(args: argparse.Namespace):
    if len(args.cases_file.strip()) > 0:
        cases = load_cases_jsonl(Path(args.cases_file))
    else:
        cases = build_seed_cases()

    requested_ids = _parse_requested_case_ids(args)

    if len(requested_ids) == 0:
        return cases

    requested_set = set(requested_ids)
    return [case for case in cases if case.case_id in requested_set]


def _parse_requested_case_ids(args: argparse.Namespace) -> list[str]:
    values = [item.strip() for item in args.case_ids.split(",") if len(item.strip()) > 0]

    if len(args.case_list.strip()) > 0:
        with Path(args.case_list).open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()

                if len(text) == 0 or text.startswith("#") or text in values:
                    continue

                values.append(text)

    return values


def _parse_variants(raw_variants: str) -> list[str]:
    variants = [item.strip() for item in raw_variants.split(",") if len(item.strip()) > 0]
    unknown = [item for item in variants if item not in VARIANT_REPAIR_OVERRIDES]

    if len(unknown) > 0:
        allowed = ", ".join(sorted(VARIANT_REPAIR_OVERRIDES))
        raise SystemExit(f"未知 ablation variant: {unknown}; 可选值: {allowed}")

    if len(variants) == 0:
        raise SystemExit("至少需要指定一个 ablation variant。")

    return variants


def _build_config_overrides(args: argparse.Namespace, variant: str) -> dict:
    stop_overrides: dict[str, object] = {}

    if args.min_answer_consistency is not None:
        stop_overrides["min_answer_consistency"] = args.min_answer_consistency

    if args.min_agent_eval_score is not None:
        stop_overrides["min_agent_eval_score"] = args.min_agent_eval_score

    if args.min_final_score is not None:
        stop_overrides["min_final_score"] = args.min_final_score

    if args.min_turn_index_before_final_answer is not None:
        stop_overrides["min_turn_index_before_final_answer"] = args.min_turn_index_before_final_answer

    if args.min_trajectory_count_before_accept is not None:
        stop_overrides["min_trajectory_count_before_accept"] = args.min_trajectory_count_before_accept

    if bool(args.allow_verifier_rejected_stop):
        stop_overrides["require_verifier_accept_flag"] = False

    overrides = {"repair": deepcopy(VARIANT_REPAIR_OVERRIDES[variant])}

    if len(stop_overrides) > 0:
        overrides["stop"] = stop_overrides

    return overrides


def _build_ablation_flags(variant: str) -> dict:
    overrides = VARIANT_REPAIR_OVERRIDES[variant]
    return {
        "variant": variant,
        "disable_verifier_reshuffle": not overrides["enable_verifier_hypothesis_reshuffle"],
        "disable_best_repair_action": not overrides["enable_best_repair_action"],
        "disable_reroot": not overrides["enable_tree_reroot"],
    }


def _build_acceptance_overrides_metadata(args: argparse.Namespace) -> dict:
    return {
        "min_answer_consistency": args.min_answer_consistency,
        "min_agent_eval_score": args.min_agent_eval_score,
        "min_final_score": args.min_final_score,
        "min_turn_index_before_final_answer": args.min_turn_index_before_final_answer,
        "min_trajectory_count_before_accept": args.min_trajectory_count_before_accept,
        "allow_verifier_rejected_stop": bool(args.allow_verifier_rejected_stop),
    }


def _run_variant(args: argparse.Namespace, variant: str, cases: list, output_root: Path) -> tuple[list[dict], dict]:
    variant_output_root = output_root / variant
    variant_output_root.mkdir(parents=True, exist_ok=True)
    ablation_flags = _build_ablation_flags(variant)
    focused_rows: list[dict] = []
    summary_path = variant_output_root / "focused_repair_summary.jsonl"
    case_concurrency = max(int(args.case_concurrency), 1)

    print(
        f"[ablation] variant={variant} start cases={len(cases)} case_concurrency={case_concurrency}",
        file=sys.stderr,
        flush=True,
    )

    if case_concurrency == 1:
        for case in cases:
            focused_rows.append(_run_single_case(args, variant, case, ablation_flags))
    else:
        indexed_rows: dict[int, dict] = {}

        with ThreadPoolExecutor(max_workers=min(case_concurrency, len(cases))) as executor:
            futures = {
                executor.submit(_run_single_case, args, variant, case, ablation_flags): index
                for index, case in enumerate(cases)
            }

            for future in as_completed(futures):
                index = futures[future]
                indexed_rows[index] = future.result()

        focused_rows = [indexed_rows[index] for index in sorted(indexed_rows)]

    with summary_path.open("w", encoding="utf-8") as summary_handle:
        for row in focused_rows:
            summary_handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    metrics = _augment_metrics(_summarize_focused_rows(focused_rows, ablation_flags), focused_rows)
    metrics["case_concurrency"] = case_concurrency
    _write_guarded_gate_audit(variant_output_root / "guarded_gate_audit.jsonl", metrics)
    _write_a4_evidence_audit(variant_output_root / "a4_evidence_audit.jsonl", metrics)
    with (variant_output_root / "focused_metrics.json").open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(metrics, ensure_ascii=False, indent=2))

    print(f"[ablation] variant={variant} done", file=sys.stderr, flush=True)
    return focused_rows, metrics


def _run_single_case(args: argparse.Namespace, variant: str, case, ablation_flags: dict) -> dict:
    print(f"[ablation] variant={variant} case={case.case_id} start", file=sys.stderr, flush=True)
    brain = build_default_brain_from_env(config_overrides=_build_config_overrides(args, variant))
    patient_agent = VirtualPatientAgent()

    try:
        session_id = f"focused_ablation::{variant}::{case.case_id}"
        brain.start_session(session_id)
        current_output = brain.process_turn(session_id, case.chief_complaint)
        turn_summaries: list[dict] = []
        previous_question_node_id: str | None = None
        previous_pending_action: dict | None = None
        initial_summary = _extract_turn_summary(current_output)
        initial_summary["semantic_repeat_as_previous"] = False
        turn_summaries.append(initial_summary)
        previous_question_node_id = (initial_summary.get("pending_action") or {}).get("target_node_id")
        previous_pending_action = initial_summary.get("pending_action")

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
                turn_summary["semantic_repeat_as_previous"] = _is_semantic_repeat(
                    previous_pending_action,
                    turn_summary.get("pending_action"),
                )
                turn_summaries.append(turn_summary)
                previous_question_node_id = (turn_summary.get("pending_action") or {}).get("target_node_id")
                previous_pending_action = turn_summary.get("pending_action")

                if current_output.get("final_report") is not None:
                    break

        final_report = current_output.get("final_report") or brain.finalize(session_id)
        row = {
            "ablation_flags": deepcopy(ablation_flags),
            "acceptance_overrides": _build_acceptance_overrides_metadata(args),
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
        print(
            f"[ablation] variant={variant} case={case.case_id} done stop_reason={row.get('final_stop_reason')}",
            file=sys.stderr,
            flush=True,
        )
        return row
    finally:
        _close_brain(brain)


def _close_brain(brain) -> None:
    retriever = getattr(getattr(brain, "deps", None), "retriever", None)
    neo4j_client = getattr(retriever, "client", None)

    if neo4j_client is None or not hasattr(neo4j_client, "close"):
        return

    try:
        neo4j_client.close()
    except Exception:
        pass


def _augment_metrics(metrics: dict, focused_rows: list[dict]) -> dict:
    semantic_repeat_turns = 0
    verifier_schema_counts = {"true": 0, "false": 0, "missing": 0}
    verifier_reject_reason_source_counts: dict[str, int] = {}

    for row in focused_rows:
        for turn_summary in row.get("turn_summaries", []):
            if bool(turn_summary.get("semantic_repeat_as_previous", False)):
                semantic_repeat_turns += 1

            if not _turn_has_verifier_call(turn_summary):
                continue

            schema_value = turn_summary.get("best_answer_verifier_schema_valid")

            if schema_value is True:
                verifier_schema_counts["true"] += 1
            elif schema_value is False:
                verifier_schema_counts["false"] += 1
            else:
                verifier_schema_counts["missing"] += 1

            source = str(turn_summary.get("best_answer_verifier_reject_reason_source") or "missing")
            verifier_reject_reason_source_counts[source] = verifier_reject_reason_source_counts.get(source, 0) + 1

    repair_turns = int(metrics.get("repair_turns", 0))
    repair_override_turns = int(metrics.get("repair_override_turns", 0))
    metrics["semantic_repeat_turns"] = semantic_repeat_turns
    metrics["root_vs_repair_diff_rate"] = repair_override_turns / repair_turns if repair_turns > 0 else 0.0
    metrics["hypothesis_switch_turns"] = int(metrics.get("repair_hypothesis_switch_turns", 0))
    metrics["repeated_turns"] = int(metrics.get("repeated_question_turns", 0))
    metrics["verifier_schema_valid_counts"] = verifier_schema_counts
    metrics["verifier_reject_reason_source_counts"] = verifier_reject_reason_source_counts

    if len(focused_rows) > 0:
        metrics["acceptance_overrides"] = dict(focused_rows[0].get("acceptance_overrides", {}))

    return metrics


def _is_semantic_repeat(previous_action: dict | None, current_action: dict | None) -> bool:
    if not isinstance(previous_action, dict) or not isinstance(current_action, dict):
        return False

    previous_node_id = str(previous_action.get("target_node_id") or "")
    current_node_id = str(current_action.get("target_node_id") or "")

    if len(previous_node_id) == 0 or len(current_node_id) == 0 or previous_node_id == current_node_id:
        return False

    previous_family = _infer_action_family(previous_action)
    current_family = _infer_action_family(current_action)
    return len(previous_family) > 0 and previous_family == current_family


def _infer_action_family(action: dict) -> str:
    text = _normalize_text(
        " ".join(
            [
                str(action.get("target_node_name") or ""),
                str(action.get("target_node_id") or ""),
                str(action.get("topic_id") or ""),
            ]
        )
    )
    family_rules = {
        "immune_status": ("hiv", "cd4", "免疫", "艾滋"),
        "imaging": ("ct", "影像", "x线", "胸片", "磨玻璃"),
        "oxygenation": ("低氧", "血氧", "pao2", "氧分压", "氧合"),
        "pathogen": ("βd葡聚糖", "bdg", "病原", "痰", "balf", "肺孢子", "核酸", "pcr"),
        "respiratory": ("发热", "干咳", "咳嗽", "呼吸困难", "气促"),
        "systemic": ("皮疹", "咽痛", "关节", "腹泻", "淋巴结"),
        "risk": ("高危", "性行为", "接触史", "暴露"),
    }
    families = [family for family, keywords in family_rules.items() if any(keyword in text for keyword in keywords)]

    if len(families) > 0:
        return "|".join(families)

    return text[:8]


def _normalize_text(text: str) -> str:
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


def _write_combined_outputs(output_root: Path, all_rows: list[dict], all_metrics: dict) -> None:
    with (output_root / "ablation_summary.jsonl").open("w", encoding="utf-8") as handle:
        for row in all_rows:
            handle.write(
                json.dumps(
                    {
                        "variant": row.get("ablation_flags", {}).get("variant"),
                        "case_id": row.get("case_id"),
                        "stop_reason": row.get("final_stop_reason"),
                        "best_answer_name": row.get("final_best_answer_name"),
                        "is_best_answer_correct": row.get("is_best_answer_correct"),
                        "acceptance_category": row.get("acceptance_category"),
                        "first_correct_best_answer_turn": row.get("first_correct_best_answer_turn"),
                        "first_verifier_accept_turn": row.get("first_verifier_accept_turn"),
                        "first_verifier_accept_turn_for_final_answer": row.get(
                            "first_verifier_accept_turn_for_final_answer"
                        ),
                        "final_answer_changed_after_first_accept": row.get(
                            "final_answer_changed_after_first_accept"
                        ),
                        "correct_but_rejected_span": row.get("correct_but_rejected_span"),
                        "accepted_with_verifier_metadata": _row_summary_has_accept_metadata(row),
                        "accepted_on_turn1": (
                            row.get("first_verifier_accept_turn") == 1
                            and row.get("final_stop_reason") == "final_answer_accepted"
                        ),
                        "repair_turns": sum(
                            1
                            for turn in row.get("turn_summaries", [])
                            if len(str(turn.get("reject_reason") or "")) > 0
                        ),
                        "semantic_repeat_turns": sum(
                            1
                            for turn in row.get("turn_summaries", [])
                            if bool(turn.get("semantic_repeat_as_previous", False))
                        ),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    with (output_root / "ablation_metrics.json").open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(all_metrics, ensure_ascii=False, indent=2))

    with (output_root / "guarded_gate_audit.jsonl").open("w", encoding="utf-8") as handle:
        for variant, metrics in all_metrics.items():
            for record in metrics.get("guarded_gate_audit_records", []):
                handle.write(json.dumps({"variant": variant, **record}, ensure_ascii=False) + "\n")

    with (output_root / "a4_evidence_audit.jsonl").open("w", encoding="utf-8") as handle:
        for variant, metrics in all_metrics.items():
            for record in metrics.get("a4_evidence_audit_records", []):
                handle.write(json.dumps({"variant": variant, **record}, ensure_ascii=False) + "\n")


def _write_guarded_gate_audit(path: Path, metrics: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in metrics.get("guarded_gate_audit_records", []):
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_a4_evidence_audit(path: Path, metrics: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in metrics.get("a4_evidence_audit_records", []):
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _row_summary_has_accept_metadata(row: dict) -> bool:
    if row.get("final_stop_reason") != "final_answer_accepted":
        return False

    turn_summaries = row.get("turn_summaries", [])

    if not isinstance(turn_summaries, list):
        return False

    for turn_summary in reversed(turn_summaries):
        if not isinstance(turn_summary, dict):
            continue

        if turn_summary.get("stop_reason") == "final_answer_accepted":
            return bool(turn_summary.get("best_answer_verifier_metadata_complete", False))

    return False


def main() -> int:
    args = parse_args()
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    cases = _load_cases(args)
    variants = _parse_variants(args.variants)

    if len(cases) == 0:
        raise SystemExit("没有匹配到任何 focused replay 病例。")

    if len(args.cases_file.strip()) == 0:
        write_cases_jsonl(cases, output_root / "seed_cases.jsonl")

    all_rows: list[dict] = []
    all_metrics: dict[str, dict] = {}

    for variant in variants:
        focused_rows, metrics = _run_variant(args, variant, cases, output_root)
        all_rows.extend(focused_rows)
        all_metrics[variant] = metrics

    _write_combined_outputs(output_root, all_rows, all_metrics)
    print(json.dumps({"metrics": all_metrics, "output_root": str(output_root)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
