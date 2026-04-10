"""运行 focused repair ablation，并统一导出可复现实验摘要。"""

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
from scripts.run_focused_repair_replay import _extract_turn_summary, _summarize_focused_rows
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


def _build_config_overrides(variant: str) -> dict:
    return {"repair": deepcopy(VARIANT_REPAIR_OVERRIDES[variant])}


def _build_ablation_flags(variant: str) -> dict:
    overrides = VARIANT_REPAIR_OVERRIDES[variant]
    return {
        "variant": variant,
        "disable_verifier_reshuffle": not overrides["enable_verifier_hypothesis_reshuffle"],
        "disable_best_repair_action": not overrides["enable_best_repair_action"],
        "disable_reroot": not overrides["enable_tree_reroot"],
    }


def _run_variant(args: argparse.Namespace, variant: str, cases: list, output_root: Path) -> tuple[list[dict], dict]:
    variant_output_root = output_root / variant
    variant_output_root.mkdir(parents=True, exist_ok=True)
    brain = build_default_brain_from_env(config_overrides=_build_config_overrides(variant))
    patient_agent = VirtualPatientAgent()
    ablation_flags = _build_ablation_flags(variant)
    focused_rows: list[dict] = []

    for case in cases:
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

    summary_path = variant_output_root / "focused_repair_summary.jsonl"
    with summary_path.open("w", encoding="utf-8") as handle:
        for row in focused_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    metrics = _augment_metrics(_summarize_focused_rows(focused_rows, ablation_flags), focused_rows)
    with (variant_output_root / "focused_metrics.json").open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(metrics, ensure_ascii=False, indent=2))

    return focused_rows, metrics


def _augment_metrics(metrics: dict, focused_rows: list[dict]) -> dict:
    semantic_repeat_turns = 0

    for row in focused_rows:
        for turn_summary in row.get("turn_summaries", []):
            if bool(turn_summary.get("semantic_repeat_as_previous", False)):
                semantic_repeat_turns += 1

    repair_turns = int(metrics.get("repair_turns", 0))
    repair_override_turns = int(metrics.get("repair_override_turns", 0))
    metrics["semantic_repeat_turns"] = semantic_repeat_turns
    metrics["root_vs_repair_diff_rate"] = repair_override_turns / repair_turns if repair_turns > 0 else 0.0
    metrics["hypothesis_switch_turns"] = int(metrics.get("repair_hypothesis_switch_turns", 0))
    metrics["repeated_turns"] = int(metrics.get("repeated_question_turns", 0))
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
