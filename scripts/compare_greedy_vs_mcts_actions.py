"""对比 No-Tree Greedy 与 MCTS 主线在同一批 benchmark case 上的决策差异。"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


FAMILY_MATCH_RATIO_THRESHOLD = 0.88
STANDARD_ACTION_TYPES = ("detail", "symptom", "risk", "lab", "imaging", "pathogen", "exam_context", "unknown")
SLICE_NAMES = (
    "greedy_correct_mcts_wrong",
    "mcts_correct_greedy_wrong",
    "both_correct_but_different_root_action",
    "both_wrong",
    "mcts_top3_hit_but_top1_miss",
    "mcts_root_disagree_and_top3_drop",
)


@dataclass
class CaseDecisionRecord:
    """保存单个 case 的轻量决策快照。"""

    case_id: str
    case_title: str
    mode: str
    gold_conditions: list[str]
    status: str
    turn_count: int
    accepted: bool
    top1_answer: str
    top1_answer_id: str
    top1_exact_hit: bool
    top1_family_hit: bool
    hypothesis_hit: bool
    top3_hypothesis_hit: bool
    top3_hypotheses: list[str]
    top3_answers: list[str]
    root_action_signature: str
    root_action_id: str
    root_action_name: str
    root_action_type: str
    root_action_target_id: str
    root_action_target_name: str
    root_action_prior_score: float
    root_action_breakdown: dict[str, Any]
    root_search_metadata: dict[str, Any]
    first_turn_top_answer_metadata: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="比较 No-Tree Greedy 与 MCTS 主线的 case-level root action 差异。")
    parser.add_argument("--mcts-dir", required=True, help="MCTS 主线 benchmark 输出目录。")
    parser.add_argument("--greedy-dir", required=True, help="No-Tree Greedy benchmark 输出目录。")
    parser.add_argument("--output-dir", default="", help="分析输出目录；默认写到 MCTS 目录下 compare_greedy_vs_mcts。")
    parser.add_argument("--mcts-label", default="mcts", help="MCTS 标签。")
    parser.add_argument("--greedy-label", default="no_tree_greedy", help="Greedy 标签。")
    return parser.parse_args()


def _normalize_text(value: object) -> str:
    return "".join(str(value or "").strip().lower().split())


def _is_name_match(left: str, right: str, *, match_mode: str) -> bool:
    if len(left) == 0 or len(right) == 0:
        return False
    if left == right:
        return True
    if match_mode != "family":
        return False
    if left in right or right in left:
        return True
    return SequenceMatcher(None, left, right).ratio() >= FAMILY_MATCH_RATIO_THRESHOLD


def _matches_expected_answer(answer_name: str, row: dict[str, Any], *, match_mode: str) -> bool:
    normalized_answer = _normalize_text(answer_name)
    if len(normalized_answer) == 0:
        return False

    expected_targets = list(row.get("true_conditions") or [])
    true_disease_phase = row.get("true_disease_phase")
    if true_disease_phase is not None:
        expected_targets.append(true_disease_phase)

    for expected in expected_targets:
        normalized_expected = _normalize_text(expected)
        if _is_name_match(normalized_answer, normalized_expected, match_mode=match_mode):
            return True
    return False


def _matches_expected_name_list(predicted_names: list[str], row: dict[str, Any], *, match_mode: str) -> bool:
    expected_targets = list(row.get("true_conditions") or [])
    true_disease_phase = row.get("true_disease_phase")
    if true_disease_phase is not None:
        expected_targets.append(true_disease_phase)

    normalized_predictions = [_normalize_text(item) for item in predicted_names if len(str(item or "").strip()) > 0]
    normalized_expected = [_normalize_text(item) for item in expected_targets if len(str(item or "").strip()) > 0]

    for expected in normalized_expected:
        for predicted in normalized_predictions:
            if _is_name_match(predicted, expected, match_mode=match_mode):
                return True
    return False


def _extract_final_answer_name(report: dict[str, Any]) -> str:
    best_final_answer = report.get("best_final_answer")
    if isinstance(best_final_answer, dict):
        answer_name = str(best_final_answer.get("answer_name") or "").strip()
        if len(answer_name) > 0:
            return answer_name

    for key in ("answer_group_scores", "final_answer_scores"):
        scores = report.get(key) or []
        if not isinstance(scores, list) or len(scores) == 0:
            continue
        first_score = scores[0]
        if not isinstance(first_score, dict):
            continue
        answer_name = str(first_score.get("answer_name") or "").strip()
        if len(answer_name) > 0:
            return answer_name

    return str(report.get("best_answer_name") or "").strip()


def _extract_top3_hypotheses(report: dict[str, Any]) -> list[str]:
    candidates = report.get("candidate_hypotheses") or []
    if not isinstance(candidates, list):
        return []
    return [
        str(item.get("name") or "").strip()
        for item in candidates[:3]
        if isinstance(item, dict) and len(str(item.get("name") or "").strip()) > 0
    ]


def _extract_top3_answers(report: dict[str, Any]) -> list[str]:
    scores = report.get("answer_group_scores") or report.get("final_answer_scores") or []
    if not isinstance(scores, list):
        return []
    return [
        str(item.get("answer_name") or "").strip()
        for item in scores[:3]
        if isinstance(item, dict) and len(str(item.get("answer_name") or "").strip()) > 0
    ]


def _is_final_answer_accepted(row: dict[str, Any]) -> bool:
    report = row.get("final_report") or {}
    stop_reason = str(report.get("stop_reason") or "")
    analysis = row.get("analysis") or {}
    if isinstance(analysis, dict) and "accepted_final_answer" in analysis:
        return bool(analysis.get("accepted_final_answer"))
    return row.get("status") == "completed" or stop_reason == "final_answer_accepted"


def _normalize_root_action_type(action: dict[str, Any]) -> str:
    if not isinstance(action, dict):
        return "unknown"
    metadata = action.get("metadata") or {}
    question_type_hint = str(metadata.get("question_type_hint") or "").strip()
    if question_type_hint in STANDARD_ACTION_TYPES:
        return question_type_hint
    action_type = str(action.get("action_type") or "").strip()
    if action_type in {"collect_general_exam_context", "collect_exam_context"}:
        return "exam_context"
    return "unknown"


def _extract_root_action(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    turns = row.get("turns") or []
    if not isinstance(turns, list) or len(turns) == 0:
        return {}, {}, {}
    turn0 = turns[0] if isinstance(turns[0], dict) else {}
    search_report = turn0.get("search_report") or {}
    search_metadata = turn0.get("search_metadata") or search_report.get("search_metadata") or {}
    selected_action = search_report.get("selected_action") or search_report.get("root_best_action") or {}
    top_answer_metadata: dict[str, Any] = {}
    final_answer_scores = search_report.get("final_answer_scores") or []
    if isinstance(final_answer_scores, list) and len(final_answer_scores) > 0 and isinstance(final_answer_scores[0], dict):
        top_answer_metadata = dict(final_answer_scores[0].get("metadata") or {})
    return selected_action if isinstance(selected_action, dict) else {}, search_metadata if isinstance(search_metadata, dict) else {}, top_answer_metadata


def _build_root_action_breakdown(action: dict[str, Any], search_metadata: dict[str, Any], top_answer_metadata: dict[str, Any]) -> dict[str, Any]:
    metadata = action.get("metadata") or {}
    breakdown = {
        "prior_score": float(action.get("prior_score", 0.0) or 0.0),
        "question_type_hint": str(metadata.get("question_type_hint") or ""),
        "relation_type": str(metadata.get("relation_type") or ""),
        "evidence_cost": str(metadata.get("evidence_cost") or ""),
        "acquisition_mode": str(metadata.get("acquisition_mode") or ""),
        "answerability_score": float(metadata.get("answerability_score", 0.0) or 0.0),
        "discriminative_gain": float(metadata.get("discriminative_gain", 0.0) or 0.0),
        "alternative_overlap": float(metadata.get("alternative_overlap", 0.0) or 0.0),
        "patient_burden": float(metadata.get("patient_burden", 0.0) or 0.0),
        "selected_action_source": str(metadata.get("selected_action_source") or search_metadata.get("selected_action_source") or "unknown"),
        "selected_action_source_priority_rank": int(
            metadata.get("selected_action_source_priority_rank", search_metadata.get("selected_action_source_priority_rank", 0)) or 0
        ),
        "rollouts_executed": int(search_metadata.get("rollouts_executed", 0) or 0),
        "rollout_trajectory_count": int(search_metadata.get("rollout_trajectory_count", 0) or 0),
        "answer_group_count": int(search_metadata.get("answer_group_count", 0) or 0),
    }
    for key in (
        "belief_margin_proxy",
        "acceptance_risk_proxy",
        "top1_top3_separation_proxy",
        "competitor_elimination_proxy",
        "competitor_coverage_proxy",
        "alternative_preservation_proxy",
        "discriminative_support_quality",
        "discriminative_answer_bonus",
        "top3_coverage_stability_bonus",
        "candidate_rank_position",
    ):
        if key in top_answer_metadata:
            breakdown[key] = top_answer_metadata[key]
    return breakdown


def _build_case_record(row: dict[str, Any], *, mode: str) -> CaseDecisionRecord:
    report = row.get("final_report") or {}
    selected_action, search_metadata, top_answer_metadata = _extract_root_action(row)
    action_metadata = selected_action.get("metadata") or {}
    root_action_type = _normalize_root_action_type(selected_action)
    root_target_id = str(selected_action.get("target_node_id") or "").strip()
    root_action_signature = f"{str(selected_action.get('action_type') or '').strip()}::{root_target_id}"
    top1_answer = _extract_final_answer_name(report)
    top3_hypotheses = _extract_top3_hypotheses(report)
    top3_answers = _extract_top3_answers(report)
    top1_exact_hit = _matches_expected_answer(top1_answer, row, match_mode="exact")
    top1_family_hit = _matches_expected_answer(top1_answer, row, match_mode="family")
    hypothesis_names = [
        str(item.get("name") or "").strip()
        for item in (report.get("candidate_hypotheses") or [])
        if isinstance(item, dict)
    ]
    return CaseDecisionRecord(
        case_id=str(row.get("case_id") or ""),
        case_title=str(row.get("case_title") or ""),
        mode=mode,
        gold_conditions=[str(item) for item in (row.get("true_conditions") or []) if len(str(item).strip()) > 0],
        status=str(row.get("status") or ""),
        turn_count=len(row.get("turns") or []),
        accepted=_is_final_answer_accepted(row),
        top1_answer=top1_answer,
        top1_answer_id=str((report.get("best_final_answer") or {}).get("answer_id") or ""),
        top1_exact_hit=top1_exact_hit,
        top1_family_hit=top1_family_hit,
        hypothesis_hit=_matches_expected_name_list(hypothesis_names, row, match_mode="family"),
        top3_hypothesis_hit=_matches_expected_name_list(top3_hypotheses, row, match_mode="family"),
        top3_hypotheses=top3_hypotheses,
        top3_answers=top3_answers,
        root_action_signature=root_action_signature,
        root_action_id=str(selected_action.get("action_id") or ""),
        root_action_name=str(selected_action.get("target_node_name") or selected_action.get("action_type") or "").strip(),
        root_action_type=root_action_type,
        root_action_target_id=root_target_id,
        root_action_target_name=str(selected_action.get("target_node_name") or "").strip(),
        root_action_prior_score=float(selected_action.get("prior_score", 0.0) or 0.0),
        root_action_breakdown=_build_root_action_breakdown(selected_action, search_metadata, top_answer_metadata),
        root_search_metadata=dict(search_metadata),
        first_turn_top_answer_metadata=dict(top_answer_metadata),
    )


def _load_case_records(run_dir: Path, *, mode: str) -> tuple[list[CaseDecisionRecord], dict[str, Any]]:
    replay_results_path = run_dir / "replay_results.jsonl"
    summary_path = run_dir / "benchmark_summary.json"
    if not replay_results_path.exists():
        raise FileNotFoundError(f"未找到 replay_results.jsonl: {replay_results_path}")
    rows = [
        json.loads(line)
        for line in replay_results_path.read_text(encoding="utf-8").splitlines()
        if len(line.strip()) > 0
    ]
    records = [_build_case_record(row, mode=mode) for row in rows]
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    return records, summary


def _infer_reason_labels(mcts_record: CaseDecisionRecord, greedy_record: CaseDecisionRecord) -> list[str]:
    labels: list[str] = []
    mcts_breakdown = dict(mcts_record.root_action_breakdown or {})
    greedy_breakdown = dict(greedy_record.root_action_breakdown or {})

    if not mcts_record.hypothesis_hit:
        labels.append("candidate_miss")
    if greedy_record.top3_hypothesis_hit and not mcts_record.top3_hypothesis_hit:
        labels.append("top3_coverage_loss")
    if mcts_record.top3_hypothesis_hit and not mcts_record.top1_exact_hit and greedy_record.top1_exact_hit:
        labels.append("ranking_stage_flip")
    if (
        mcts_record.root_action_type == "detail"
        and greedy_record.root_action_type != "detail"
        and greedy_record.top1_exact_hit
        and not mcts_record.top1_exact_hit
    ):
        labels.append("root_detail_bias")
    if (
        str(mcts_breakdown.get("evidence_cost") or "") == "high"
        and float(mcts_breakdown.get("answerability_score", 0.0) or 0.0) < 0.35
        and not mcts_record.top1_exact_hit
        and (greedy_record.top1_exact_hit or greedy_record.top3_hypothesis_hit)
    ):
        labels.append("high_cost_low_value_action")
    if (
        float(mcts_breakdown.get("discriminative_gain", 0.0) or 0.0)
        >= float(greedy_breakdown.get("discriminative_gain", 0.0) or 0.0) + 0.2
        and mcts_record.root_action_type in {"lab", "imaging", "pathogen"}
        and greedy_record.root_action_type in {"symptom", "risk", "exam_context"}
        and greedy_record.top1_exact_hit
        and not mcts_record.top1_exact_hit
    ):
        labels.append("root_discriminative_overreach")
    if (
        float(mcts_breakdown.get("competitor_elimination_proxy", 0.0) or 0.0) >= 0.18
        and float(mcts_breakdown.get("alternative_preservation_proxy", 0.0) or 0.0) <= 0.25
        and greedy_record.top3_hypothesis_hit
        and not mcts_record.top3_hypothesis_hit
    ):
        labels.append("over_competitor_elimination")
    if len(labels) == 0:
        labels.append("uncategorized")
    return labels


def _record_to_payload(record: CaseDecisionRecord) -> dict[str, Any]:
    return {
        "case_id": record.case_id,
        "case_title": record.case_title,
        "mode": record.mode,
        "gold_conditions": list(record.gold_conditions),
        "status": record.status,
        "turn_count": record.turn_count,
        "accepted": record.accepted,
        "top1_answer": record.top1_answer,
        "top1_answer_id": record.top1_answer_id,
        "top1_exact_hit": record.top1_exact_hit,
        "top1_family_hit": record.top1_family_hit,
        "hypothesis_hit": record.hypothesis_hit,
        "top3_hypothesis_hit": record.top3_hypothesis_hit,
        "top3_hypotheses": list(record.top3_hypotheses),
        "top3_answers": list(record.top3_answers),
        "root_action_signature": record.root_action_signature,
        "root_action_id": record.root_action_id,
        "root_action_name": record.root_action_name,
        "root_action_type": record.root_action_type,
        "root_action_target_id": record.root_action_target_id,
        "root_action_target_name": record.root_action_target_name,
        "root_action_prior_score": record.root_action_prior_score,
        "root_action_breakdown": dict(record.root_action_breakdown),
        "root_search_metadata": dict(record.root_search_metadata),
        "first_turn_top_answer_metadata": dict(record.first_turn_top_answer_metadata),
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False))
            file.write("\n")


def _format_action(record: CaseDecisionRecord) -> str:
    target = record.root_action_target_name or record.root_action_target_id or "UNKNOWN"
    return f"{record.root_action_type}:{target}"


def _build_pair_payload(
    *,
    mcts_record: CaseDecisionRecord,
    greedy_record: CaseDecisionRecord,
    reason_labels: list[str],
) -> dict[str, Any]:
    root_action_same = mcts_record.root_action_signature == greedy_record.root_action_signature
    return {
        "case_id": mcts_record.case_id,
        "case_title": mcts_record.case_title,
        "gold_conditions": list(mcts_record.gold_conditions),
        "root_action_same": root_action_same,
        "mcts": _record_to_payload(mcts_record),
        "greedy": _record_to_payload(greedy_record),
        "reason_labels": list(reason_labels),
    }


def _build_slice_map(pairs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    slices: dict[str, list[dict[str, Any]]] = {name: [] for name in SLICE_NAMES}
    for pair in pairs:
        mcts_record = pair["mcts"]
        greedy_record = pair["greedy"]
        root_action_same = bool(pair["root_action_same"])

        if bool(greedy_record["top1_exact_hit"]) and not bool(mcts_record["top1_exact_hit"]):
            slices["greedy_correct_mcts_wrong"].append(pair)
        if bool(mcts_record["top1_exact_hit"]) and not bool(greedy_record["top1_exact_hit"]):
            slices["mcts_correct_greedy_wrong"].append(pair)
        if bool(mcts_record["top1_exact_hit"]) and bool(greedy_record["top1_exact_hit"]) and not root_action_same:
            slices["both_correct_but_different_root_action"].append(pair)
        if not bool(mcts_record["top1_exact_hit"]) and not bool(greedy_record["top1_exact_hit"]):
            slices["both_wrong"].append(pair)
        if bool(mcts_record["top3_hypothesis_hit"]) and not bool(mcts_record["top1_exact_hit"]):
            slices["mcts_top3_hit_but_top1_miss"].append(pair)
        if (not root_action_same) and bool(greedy_record["top3_hypothesis_hit"]) and not bool(mcts_record["top3_hypothesis_hit"]):
            slices["mcts_root_disagree_and_top3_drop"].append(pair)
    return slices


def _counter_to_sorted_payload(counter: Counter[str]) -> dict[str, int]:
    return {key: int(counter[key]) for key in sorted(counter.keys())}


def _build_summary(
    pairs: list[dict[str, Any]],
    *,
    mcts_summary: dict[str, Any],
    greedy_summary: dict[str, Any],
    mcts_label: str,
    greedy_label: str,
) -> dict[str, Any]:
    shared_case_count = len(pairs)
    root_action_same_count = sum(1 for item in pairs if bool(item["root_action_same"]))
    root_action_disagree_count = shared_case_count - root_action_same_count
    greedy_correct_mcts_wrong = sum(
        1
        for item in pairs
        if bool(item["greedy"]["top1_exact_hit"]) and not bool(item["mcts"]["top1_exact_hit"])
    )
    mcts_correct_greedy_wrong = sum(
        1
        for item in pairs
        if bool(item["mcts"]["top1_exact_hit"]) and not bool(item["greedy"]["top1_exact_hit"])
    )
    both_correct_but_different_root_action = sum(
        1
        for item in pairs
        if bool(item["mcts"]["top1_exact_hit"]) and bool(item["greedy"]["top1_exact_hit"]) and not bool(item["root_action_same"])
    )
    both_wrong = sum(
        1
        for item in pairs
        if not bool(item["mcts"]["top1_exact_hit"]) and not bool(item["greedy"]["top1_exact_hit"])
    )
    reason_counter: Counter[str] = Counter()
    mcts_action_types: Counter[str] = Counter()
    greedy_action_types: Counter[str] = Counter()
    disagreement_mcts_action_types: Counter[str] = Counter()
    disagreement_greedy_action_types: Counter[str] = Counter()
    disagreement_pairs: Counter[str] = Counter()

    for item in pairs:
        reason_counter.update(item["reason_labels"])
        mcts_type = str(item["mcts"]["root_action_type"] or "unknown")
        greedy_type = str(item["greedy"]["root_action_type"] or "unknown")
        mcts_action_types[mcts_type] += 1
        greedy_action_types[greedy_type] += 1
        if not bool(item["root_action_same"]):
            disagreement_mcts_action_types[mcts_type] += 1
            disagreement_greedy_action_types[greedy_type] += 1
            disagreement_pairs[f"{greedy_type} -> {mcts_type}"] += 1

    slices = _build_slice_map(pairs)
    return {
        "shared_case_count": shared_case_count,
        "root_action_same_count": root_action_same_count,
        "root_action_same_rate": round(root_action_same_count / shared_case_count, 4) if shared_case_count > 0 else 0.0,
        "root_action_disagree_count": root_action_disagree_count,
        "root_action_disagree_rate": round(root_action_disagree_count / shared_case_count, 4) if shared_case_count > 0 else 0.0,
        "greedy_correct_mcts_wrong_count": greedy_correct_mcts_wrong,
        "mcts_correct_greedy_wrong_count": mcts_correct_greedy_wrong,
        "both_correct_but_different_root_action_count": both_correct_but_different_root_action,
        "both_wrong_count": both_wrong,
        "reason_label_counts": _counter_to_sorted_payload(reason_counter),
        "root_action_type_counts": {
            greedy_label: _counter_to_sorted_payload(greedy_action_types),
            mcts_label: _counter_to_sorted_payload(mcts_action_types),
        },
        "disagreement_action_type_counts": {
            greedy_label: _counter_to_sorted_payload(disagreement_greedy_action_types),
            mcts_label: _counter_to_sorted_payload(disagreement_mcts_action_types),
            "pair_counts": _counter_to_sorted_payload(disagreement_pairs),
        },
        "slice_counts": {
            name: len(items)
            for name, items in slices.items()
        },
        "run_metric_snapshot": {
            greedy_label: {
                "top1_final_answer_hit_rate": greedy_summary.get("top1_final_answer_hit_rate"),
                "top3_hypothesis_hit_rate": greedy_summary.get("top3_hypothesis_hit_rate"),
                "completion_rate": greedy_summary.get("completion_rate"),
                "accepted_exact_accuracy": greedy_summary.get("accepted_exact_accuracy"),
                "wrong_accepted_count": greedy_summary.get("wrong_accepted_count"),
            },
            mcts_label: {
                "top1_final_answer_hit_rate": mcts_summary.get("top1_final_answer_hit_rate"),
                "top3_hypothesis_hit_rate": mcts_summary.get("top3_hypothesis_hit_rate"),
                "completion_rate": mcts_summary.get("completion_rate"),
                "accepted_exact_accuracy": mcts_summary.get("accepted_exact_accuracy"),
                "wrong_accepted_count": mcts_summary.get("wrong_accepted_count"),
            },
        },
    }


def _build_markdown_summary(summary: dict[str, Any], *, mcts_label: str, greedy_label: str) -> str:
    lines = [
        "# Greedy vs MCTS Action Diff",
        "",
        "| 指标 | 数值 |",
        "| --- | ---: |",
        f"| 共享 case 数 | {summary['shared_case_count']} |",
        f"| root action 一致数 | {summary['root_action_same_count']} |",
        f"| root action 一致率 | {summary['root_action_same_rate']:.4f} |",
        f"| root action 不一致数 | {summary['root_action_disagree_count']} |",
        f"| greedy 对 / mcts 错 | {summary['greedy_correct_mcts_wrong_count']} |",
        f"| mcts 对 / greedy 错 | {summary['mcts_correct_greedy_wrong_count']} |",
        f"| 二者都对但首问不同 | {summary['both_correct_but_different_root_action_count']} |",
        f"| 二者都错 | {summary['both_wrong_count']} |",
        "",
        "## 动作类型统计",
        "",
        f"### {greedy_label}",
        "",
    ]
    for key, value in summary["root_action_type_counts"][greedy_label].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", f"### {mcts_label}", ""])
    for key, value in summary["root_action_type_counts"][mcts_label].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## 分歧原因标签统计", ""])
    for key, value in summary["reason_label_counts"].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## 分歧动作类型对", ""])
    for key, value in summary["disagreement_action_type_counts"]["pair_counts"].items():
        lines.append(f"- `{key}`: {value}")
    return "\n".join(lines) + "\n"


def _write_slice_files(output_dir: Path, slices: dict[str, list[dict[str, Any]]]) -> None:
    for slice_name, items in slices.items():
        path = output_dir / f"{slice_name}.jsonl"
        rows = []
        for item in items:
            rows.append(
                {
                    "case_id": item["case_id"],
                    "case_title": item["case_title"],
                    "gold_conditions": item["gold_conditions"],
                    "reason_labels": item["reason_labels"],
                    "greedy_root_action": f"{item['greedy']['root_action_type']}:{item['greedy']['root_action_target_name']}",
                    "mcts_root_action": f"{item['mcts']['root_action_type']}:{item['mcts']['root_action_target_name']}",
                    "greedy_top1_answer": item["greedy"]["top1_answer"],
                    "mcts_top1_answer": item["mcts"]["top1_answer"],
                    "greedy_top3_hypotheses": item["greedy"]["top3_hypotheses"],
                    "mcts_top3_hypotheses": item["mcts"]["top3_hypotheses"],
                    "greedy_status": item["greedy"]["status"],
                    "mcts_status": item["mcts"]["status"],
                    "greedy_accepted": item["greedy"]["accepted"],
                    "mcts_accepted": item["mcts"]["accepted"],
                }
            )
        _write_jsonl(path, rows)


def compare_runs(
    *,
    mcts_dir: Path,
    greedy_dir: Path,
    output_dir: Path,
    mcts_label: str,
    greedy_label: str,
) -> dict[str, Any]:
    mcts_records, mcts_summary = _load_case_records(mcts_dir, mode=mcts_label)
    greedy_records, greedy_summary = _load_case_records(greedy_dir, mode=greedy_label)
    mcts_by_case = {item.case_id: item for item in mcts_records}
    greedy_by_case = {item.case_id: item for item in greedy_records}
    shared_case_ids = sorted(set(mcts_by_case) & set(greedy_by_case))

    pairs: list[dict[str, Any]] = []
    for case_id in shared_case_ids:
        mcts_record = mcts_by_case[case_id]
        greedy_record = greedy_by_case[case_id]
        reason_labels = _infer_reason_labels(mcts_record, greedy_record)
        pairs.append(
            _build_pair_payload(
                mcts_record=mcts_record,
                greedy_record=greedy_record,
                reason_labels=reason_labels,
            )
        )

    summary = _build_summary(
        pairs,
        mcts_summary=mcts_summary,
        greedy_summary=greedy_summary,
        mcts_label=mcts_label,
        greedy_label=greedy_label,
    )
    slices = _build_slice_map(pairs)

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "comparison_summary.json", summary)
    (output_dir / "comparison_summary.md").write_text(
        _build_markdown_summary(summary, mcts_label=mcts_label, greedy_label=greedy_label),
        encoding="utf-8",
    )
    _write_jsonl(output_dir / f"{mcts_label}_case_records.jsonl", [_record_to_payload(item) for item in mcts_records])
    _write_jsonl(output_dir / f"{greedy_label}_case_records.jsonl", [_record_to_payload(item) for item in greedy_records])
    _write_jsonl(output_dir / "pairwise_case_diff.jsonl", pairs)
    _write_json(output_dir / "slice_counts.json", {key: len(value) for key, value in slices.items()})
    _write_slice_files(output_dir, slices)
    return {
        "summary": summary,
        "slice_counts": {key: len(value) for key, value in slices.items()},
        "shared_case_ids": shared_case_ids,
    }


def main() -> None:
    args = parse_args()
    mcts_dir = Path(args.mcts_dir).resolve()
    greedy_dir = Path(args.greedy_dir).resolve()
    output_dir = Path(args.output_dir).resolve() if len(str(args.output_dir).strip()) > 0 else (mcts_dir / "compare_greedy_vs_mcts")
    result = compare_runs(
        mcts_dir=mcts_dir,
        greedy_dir=greedy_dir,
        output_dir=output_dir,
        mcts_label=str(args.mcts_label),
        greedy_label=str(args.greedy_label),
    )
    print(f"分析完成，输出目录：{output_dir}")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
