"""封装 benchmark 报表、cohort 汇总与异常病例索引。"""

from __future__ import annotations

from typing import Callable, Iterable

from ..replay.analysis import STANDARD_ANALYSIS_GROUPS, STANDARD_COST_BUCKETS, STANDARD_QUESTION_GROUPS
from ..replay.types import ReplayResult
from .helpers import (
    count_revealed_slots,
    extract_final_answer_name,
    is_final_answer_exact_hit,
    is_final_answer_family_hit,
    is_hypothesis_hit,
    is_top3_hypothesis_hit,
)
from .metrics import benchmark_summary_to_payload, summarize_benchmark


def build_non_completed_case_report(results: Iterable[ReplayResult]) -> dict:
    """构建未正常 accepted 的病例索引，便于全量 benchmark 后优先复盘异常样本。"""

    results_list = list(results)
    records = [
        _build_non_completed_case_record(result)
        for result in results_list
        if result.status != "completed"
    ]
    category_breakdown: dict[str, int] = {}
    categories: dict[str, list[dict]] = {}

    for record in records:
        category = str(record.get("category") or "unknown")
        category_breakdown[category] = category_breakdown.get(category, 0) + 1
        categories.setdefault(category, []).append(record)

    return {
        "case_count": len(results_list),
        "non_completed_count": len(records),
        "category_breakdown": dict(sorted(category_breakdown.items(), key=lambda item: item[0])),
        "categories": dict(sorted(categories.items(), key=lambda item: item[0])),
        "cases": records,
    }


def build_benchmark_cohort_summary(results: Iterable[ReplayResult]) -> dict:
    """按病例 QC 状态与病例类型生成分层 benchmark 指标。"""

    results_list = list(results)
    return {
        "analysis_summary": build_replay_analysis_summary(results_list),
        "metadata_field_coverage": {
            "case_qc_status": _field_coverage_count(results_list, "case_qc_status"),
            "benchmark_qc_status": _field_coverage_count(results_list, "benchmark_qc_status"),
            "case_type": _field_coverage_count(results_list, "case_type"),
        },
        "eligible_summary": benchmark_summary_to_payload(
            summarize_benchmark(_filter_by_field(results_list, "case_qc_status", "eligible"))
        ),
        "eligible_analysis_summary": build_replay_analysis_summary(
            _filter_by_field(results_list, "case_qc_status", "eligible")
        ),
        "case_qc_status_summaries": _build_grouped_summaries(
            results_list,
            key_fn=lambda item: str(getattr(item, "case_qc_status", "") or "").strip() or "unknown",
        ),
        "benchmark_qc_status_summaries": _build_grouped_summaries(
            results_list,
            key_fn=lambda item: str(getattr(item, "benchmark_qc_status", "") or "").strip() or "unknown",
        ),
        "case_type_summaries": _build_grouped_summaries(
            results_list,
            key_fn=lambda item: str(getattr(item, "case_type", "") or "").strip() or "unknown",
        ),
    }


def build_replay_analysis_summary(results: Iterable[ReplayResult]) -> dict:
    """汇总 replay 里的问法分布、truth 命中与 required family coverage 指标。"""

    results_list = list(results)
    case_count = len(results_list)
    question_count_by_group = _empty_counter(STANDARD_QUESTION_GROUPS)
    question_truth_hit_count_by_group = _empty_counter(STANDARD_QUESTION_GROUPS)
    question_count_by_cost = _empty_counter(STANDARD_COST_BUCKETS)
    askable_positive_truth_count_by_group = _empty_counter(STANDARD_ANALYSIS_GROUPS)
    revealed_positive_truth_count_by_group = _empty_counter(STANDARD_ANALYSIS_GROUPS)
    selected_action_source_count: dict[str, int] = {}

    question_count_total = 0
    truth_hit_question_count_total = 0
    analysis_populated_count = 0
    required_family_group_count_total = 0
    required_family_groups_covered_on_opening_total = 0
    required_family_groups_covered_after_replay_total = 0
    required_family_coverage_gain_total = 0
    cases_with_askable_positive_truth_by_group = _empty_counter(STANDARD_ANALYSIS_GROUPS)
    cases_zero_revealed_positive_truth_by_group = _empty_counter(STANDARD_ANALYSIS_GROUPS)

    for result in results_list:
        analysis = getattr(result, "analysis", {}) or {}
        if not isinstance(analysis, dict) or len(analysis) == 0:
            continue

        analysis_populated_count += 1
        question_count_total += int(analysis.get("question_count_total", 0) or 0)
        truth_hit_question_count_total += int(analysis.get("truth_hit_question_count_total", 0) or 0)
        required_family_group_count_total += int(analysis.get("required_family_group_count", 0) or 0)
        required_family_groups_covered_on_opening_total += int(
            analysis.get("required_family_groups_covered_on_opening", 0) or 0
        )
        required_family_groups_covered_after_replay_total += int(
            analysis.get("required_family_groups_covered_after_replay", 0) or 0
        )
        required_family_coverage_gain_total += int(analysis.get("required_family_coverage_gain", 0) or 0)

        _merge_counter(question_count_by_group, analysis.get("question_count_by_group"), allowed_keys=STANDARD_QUESTION_GROUPS)
        _merge_counter(
            question_truth_hit_count_by_group,
            analysis.get("question_truth_hit_count_by_group"),
            allowed_keys=STANDARD_QUESTION_GROUPS,
        )
        _merge_counter(question_count_by_cost, analysis.get("question_count_by_cost"), allowed_keys=STANDARD_COST_BUCKETS)
        _merge_counter(
            askable_positive_truth_count_by_group,
            analysis.get("askable_positive_truth_count_by_group"),
            allowed_keys=STANDARD_ANALYSIS_GROUPS,
        )
        _merge_counter(
            revealed_positive_truth_count_by_group,
            analysis.get("revealed_positive_truth_count_by_group"),
            allowed_keys=STANDARD_ANALYSIS_GROUPS,
        )

        selected_sources = analysis.get("selected_action_source_count") or {}
        if isinstance(selected_sources, dict):
            for source, count in selected_sources.items():
                normalized_source = str(source).strip() or "unknown"
                selected_action_source_count[normalized_source] = (
                    selected_action_source_count.get(normalized_source, 0) + int(count or 0)
                )

        askable_positive_by_group = analysis.get("askable_positive_truth_count_by_group") or {}
        revealed_positive_by_group = analysis.get("revealed_positive_truth_count_by_group") or {}
        if isinstance(askable_positive_by_group, dict) and isinstance(revealed_positive_by_group, dict):
            for group in STANDARD_ANALYSIS_GROUPS:
                askable_count = int(askable_positive_by_group.get(group, 0) or 0)
                revealed_count = int(revealed_positive_by_group.get(group, 0) or 0)
                if askable_count <= 0:
                    continue
                cases_with_askable_positive_truth_by_group[group] += 1
                if revealed_count <= 0:
                    cases_zero_revealed_positive_truth_by_group[group] += 1

    return {
        "case_analysis_populated_count": analysis_populated_count,
        "case_count": case_count,
        "question_count_total": question_count_total,
        "average_question_count_total": round(question_count_total / case_count, 4) if case_count > 0 else 0.0,
        "truth_hit_question_count_total": truth_hit_question_count_total,
        "truth_hit_question_rate_total": (
            round(truth_hit_question_count_total / float(question_count_total), 4)
            if question_count_total > 0
            else 0.0
        ),
        "question_count_by_group": _counter_payload(question_count_by_group, case_count=case_count),
        "question_count_by_cost": _counter_payload(question_count_by_cost, case_count=case_count),
        "question_truth_hit_by_group": {
            group: {
                "question_count": question_count_by_group[group],
                "truth_hit_count": question_truth_hit_count_by_group[group],
                "truth_hit_rate": (
                    round(question_truth_hit_count_by_group[group] / float(question_count_by_group[group]), 4)
                    if question_count_by_group[group] > 0
                    else None
                ),
            }
            for group in STANDARD_QUESTION_GROUPS
        },
        "revealed_positive_coverage_by_group": {
            group: {
                "askable_positive_truth_count": askable_positive_truth_count_by_group[group],
                "revealed_positive_truth_count": revealed_positive_truth_count_by_group[group],
                "coverage_rate": (
                    round(
                        revealed_positive_truth_count_by_group[group]
                        / float(askable_positive_truth_count_by_group[group]),
                        4,
                    )
                    if askable_positive_truth_count_by_group[group] > 0
                    else None
                ),
                "cases_with_askable_positive_truth": cases_with_askable_positive_truth_by_group[group],
                "cases_zero_revealed_positive_truth": cases_zero_revealed_positive_truth_by_group[group],
            }
            for group in STANDARD_ANALYSIS_GROUPS
        },
        "selected_action_source_count": dict(sorted(selected_action_source_count.items(), key=lambda item: item[0])),
        "required_family_coverage": {
            "required_family_group_count_total": required_family_group_count_total,
            "required_family_groups_covered_on_opening_total": required_family_groups_covered_on_opening_total,
            "required_family_groups_covered_after_replay_total": required_family_groups_covered_after_replay_total,
            "required_family_coverage_gain_total": required_family_coverage_gain_total,
            "average_required_family_coverage_gain": (
                round(required_family_coverage_gain_total / case_count, 4) if case_count > 0 else 0.0
            ),
        },
    }


def _field_coverage_count(results: list[ReplayResult], field_name: str) -> dict[str, int]:
    populated_count = sum(1 for item in results if len(str(getattr(item, field_name, "") or "").strip()) > 0)
    return {
        "populated_count": populated_count,
        "missing_count": max(len(results) - populated_count, 0),
    }


def _filter_by_field(results: list[ReplayResult], field_name: str, expected_value: str) -> list[ReplayResult]:
    return [
        item
        for item in results
        if str(getattr(item, field_name, "") or "").strip() == expected_value
    ]


def _build_grouped_summaries(
    results: list[ReplayResult],
    *,
    key_fn: Callable[[ReplayResult], str],
) -> dict[str, dict]:
    grouped: dict[str, list[ReplayResult]] = {}
    for result in results:
        key = key_fn(result)
        grouped.setdefault(key, []).append(result)

    return {
        key: {
            **benchmark_summary_to_payload(summarize_benchmark(items)),
            "analysis_summary": build_replay_analysis_summary(items),
        }
        for key, items in sorted(grouped.items(), key=lambda item: item[0])
    }


def _empty_counter(keys: tuple[str, ...]) -> dict[str, int]:
    return {key: 0 for key in keys}


def _merge_counter(target: dict[str, int], source: object, *, allowed_keys: tuple[str, ...]) -> None:
    if not isinstance(source, dict):
        return
    for key in allowed_keys:
        target[key] += int(source.get(key, 0) or 0)


def _counter_payload(counter: dict[str, int], *, case_count: int) -> dict[str, dict[str, float | int]]:
    return {
        key: {
            "total": value,
            "average_per_case": round(value / case_count, 4) if case_count > 0 else 0.0,
        }
        for key, value in counter.items()
    }


def _build_non_completed_case_record(result: ReplayResult) -> dict:
    report = result.final_report or {}
    answer_name = extract_final_answer_name(result)
    category = _classify_non_completed_case(result)
    last_turn = result.turns[-1] if len(result.turns) > 0 else None

    return {
        "case_id": result.case_id,
        "case_title": result.case_title,
        "case_type": result.case_type,
        "case_qc_status": result.case_qc_status,
        "benchmark_qc_status": result.benchmark_qc_status,
        "case_qc_reasons": list(result.case_qc_reasons),
        "status": result.status,
        "category": category,
        "true_conditions": list(result.true_conditions),
        "true_disease_phase": result.true_disease_phase,
        "final_answer_name": answer_name,
        "final_answer_exact_hit": is_final_answer_exact_hit(result),
        "final_answer_family_hit": is_final_answer_family_hit(result),
        "top1_final_answer_hit": is_final_answer_exact_hit(result),
        "top3_hypothesis_hit": is_top3_hypothesis_hit(result),
        "hypothesis_hit": is_hypothesis_hit(result),
        "stop_reason": str(report.get("stop_reason") or ""),
        "turn_count": len(result.turns),
        "revealed_slot_count": count_revealed_slots(result),
        "candidate_hypotheses_top5": _compact_named_items(report.get("candidate_hypotheses", []), limit=5),
        "final_answer_scores_top5": _compact_named_items(
            report.get("answer_group_scores") or report.get("final_answer_scores") or [],
            limit=5,
        ),
        "last_turn": _compact_last_turn(last_turn),
        "error": _compact_error(result.error),
        "timing": _compact_timing(result.timing),
    }


def _classify_non_completed_case(result: ReplayResult) -> str:
    status = str(result.status or "unknown")

    if status == "failed":
        error_code = str((result.error or {}).get("code") or "unknown_error")
        return f"failed::{error_code}"
    if status == "max_turn_reached":
        if is_final_answer_exact_hit(result):
            return "max_turn_reached::top_exact_correct_but_rejected"
        if is_final_answer_family_hit(result):
            return "max_turn_reached::top_family_correct_but_rejected"
        if len(extract_final_answer_name(result)) == 0:
            return "max_turn_reached::no_final_answer"
        if is_hypothesis_hit(result):
            return "max_turn_reached::true_candidate_but_final_wrong"
        return "max_turn_reached::true_candidate_missing"
    return f"non_completed::{status}"


def _compact_named_items(raw_items, *, limit: int) -> list[dict]:
    if not isinstance(raw_items, list):
        return []

    compacted: list[dict] = []
    for item in raw_items[:limit]:
        if not isinstance(item, dict):
            continue
        compacted.append(
            {
                key: item.get(key)
                for key in (
                    "node_id",
                    "name",
                    "answer_id",
                    "answer_name",
                    "score",
                    "final_score",
                    "anchor_tier",
                    "observed_anchor_score",
                    "status",
                    "reason",
                )
                if key in item
            }
        )
    return compacted


def _compact_last_turn(turn) -> dict:
    if turn is None:
        return {}
    return {
        "turn_index": turn.turn_index,
        "question_node_id": turn.question_node_id,
        "question_text": turn.question_text,
        "answer_text": turn.answer_text,
        "revealed_slot_id": turn.revealed_slot_id,
        "stage": turn.stage,
    }


def _compact_error(error: dict) -> dict:
    if not isinstance(error, dict) or len(error) == 0:
        return {}
    return {
        key: error.get(key)
        for key in ("code", "stage", "prompt_name", "message", "attempts", "error_type")
        if key in error
    }


def _compact_timing(timing: dict) -> dict:
    if not isinstance(timing, dict) or len(timing) == 0:
        return {}
    return {
        key: timing.get(key)
        for key in (
            "started_at",
            "finished_at",
            "total_seconds",
            "turn_count",
            "opening_seconds",
            "initial_brain_seconds",
            "patient_answer_seconds_total",
            "brain_turn_seconds_total",
            "finalize_seconds",
        )
        if key in timing
    }
