"""封装 benchmark 核心摘要指标。"""

from __future__ import annotations

from typing import Iterable

from ..replay.types import ReplayResult
from .helpers import (
    build_status_breakdown,
    count_revealed_slots,
    extract_final_answer_name,
    is_final_answer_accepted,
    is_final_answer_exact_hit,
    is_final_answer_family_hit,
    is_hypothesis_hit,
    is_red_flag_hit,
    is_top3_hypothesis_hit,
)
from .types import BenchmarkSummary


def benchmark_summary_to_payload(summary: BenchmarkSummary) -> dict:
    """将 `BenchmarkSummary` 转成可直接写 JSON 的 payload。"""

    return {
        "case_count": int(summary.case_count),
        "completed_count": int(summary.completed_count),
        "completion_rate": float(summary.completion_rate),
        "max_turn_reached_count": int(summary.max_turn_reached_count),
        "average_turns": float(summary.average_turns),
        "average_revealed_slots": float(summary.average_revealed_slots),
        "hypothesis_hit_count": int(summary.hypothesis_hit_count),
        "hypothesis_hit_rate": float(summary.hypothesis_hit_rate),
        "top3_hypothesis_hit_count": int(summary.top3_hypothesis_hit_count),
        "top3_hypothesis_hit_rate": float(summary.top3_hypothesis_hit_rate),
        "final_answer_count": int(summary.final_answer_count),
        "final_answer_exact_hit_count": int(summary.final_answer_exact_hit_count),
        "final_answer_exact_hit_rate": float(summary.final_answer_exact_hit_rate),
        "top1_final_answer_hit_count": int(summary.top1_final_answer_hit_count),
        "top1_final_answer_hit_rate": float(summary.top1_final_answer_hit_rate),
        "final_answer_family_hit_count": int(summary.final_answer_family_hit_count),
        "final_answer_family_hit_rate": float(summary.final_answer_family_hit_rate),
        "accepted_final_answer_count": int(summary.accepted_final_answer_count),
        "accepted_exact_hit_count": int(summary.accepted_exact_hit_count),
        "accepted_exact_accuracy": float(summary.accepted_exact_accuracy),
        "accepted_family_hit_count": int(summary.accepted_family_hit_count),
        "accepted_family_accuracy": float(summary.accepted_family_accuracy),
        "wrong_accepted_count": int(summary.wrong_accepted_count),
        "family_wrong_accepted_count": int(summary.family_wrong_accepted_count),
        "top_exact_correct_but_rejected_count": int(summary.top_exact_correct_but_rejected_count),
        "top_family_correct_but_rejected_count": int(summary.top_family_correct_but_rejected_count),
        "red_flag_case_count": int(summary.red_flag_case_count),
        "red_flag_hit_count": int(summary.red_flag_hit_count),
        "red_flag_hit_rate": float(summary.red_flag_hit_rate),
        "status_breakdown": dict(summary.status_breakdown),
    }


def summarize_benchmark(results: Iterable[ReplayResult]) -> BenchmarkSummary:
    """汇总多条回放结果，生成结构化离线评测指标。"""

    results_list = list(results)

    if len(results_list) == 0:
        return BenchmarkSummary(
            case_count=0,
            completed_count=0,
            completion_rate=0.0,
            max_turn_reached_count=0,
            average_turns=0.0,
            average_revealed_slots=0.0,
            hypothesis_hit_count=0,
            hypothesis_hit_rate=0.0,
            top3_hypothesis_hit_count=0,
            top3_hypothesis_hit_rate=0.0,
            final_answer_count=0,
            final_answer_exact_hit_count=0,
            final_answer_exact_hit_rate=0.0,
            top1_final_answer_hit_count=0,
            top1_final_answer_hit_rate=0.0,
            final_answer_family_hit_count=0,
            final_answer_family_hit_rate=0.0,
            accepted_final_answer_count=0,
            accepted_exact_hit_count=0,
            accepted_exact_accuracy=0.0,
            accepted_family_hit_count=0,
            accepted_family_accuracy=0.0,
            wrong_accepted_count=0,
            family_wrong_accepted_count=0,
            top_exact_correct_but_rejected_count=0,
            top_family_correct_but_rejected_count=0,
            red_flag_case_count=0,
            red_flag_hit_count=0,
            red_flag_hit_rate=0.0,
            status_breakdown={},
        )

    completed_count = sum(1 for item in results_list if item.status == "completed")
    max_turn_reached_count = sum(1 for item in results_list if item.status == "max_turn_reached")
    total_turns = sum(len(item.turns) for item in results_list)
    total_revealed_slots = sum(count_revealed_slots(item) for item in results_list)
    hypothesis_hit_count = sum(1 for item in results_list if is_hypothesis_hit(item))
    top3_hypothesis_hit_count = sum(1 for item in results_list if is_top3_hypothesis_hit(item))
    final_answer_count = sum(1 for item in results_list if len(extract_final_answer_name(item)) > 0)
    final_answer_exact_hit_count = sum(1 for item in results_list if is_final_answer_exact_hit(item))
    top1_final_answer_hit_count = final_answer_exact_hit_count
    final_answer_family_hit_count = sum(1 for item in results_list if is_final_answer_family_hit(item))
    accepted_results = [item for item in results_list if is_final_answer_accepted(item)]
    accepted_final_answer_count = len(accepted_results)
    accepted_exact_hit_count = sum(1 for item in accepted_results if is_final_answer_exact_hit(item))
    accepted_family_hit_count = sum(1 for item in accepted_results if is_final_answer_family_hit(item))
    wrong_accepted_count = accepted_final_answer_count - accepted_exact_hit_count
    family_wrong_accepted_count = accepted_final_answer_count - accepted_family_hit_count
    top_exact_correct_but_rejected_count = sum(
        1
        for item in results_list
        if not is_final_answer_accepted(item) and is_final_answer_exact_hit(item)
    )
    top_family_correct_but_rejected_count = sum(
        1
        for item in results_list
        if not is_final_answer_accepted(item) and is_final_answer_family_hit(item)
    )
    red_flag_case_count = sum(1 for item in results_list if len(item.red_flags) > 0)
    red_flag_hit_count = sum(1 for item in results_list if is_red_flag_hit(item))
    status_breakdown = build_status_breakdown(results_list)

    case_count = len(results_list)
    return BenchmarkSummary(
        case_count=case_count,
        completed_count=completed_count,
        completion_rate=completed_count / case_count,
        max_turn_reached_count=max_turn_reached_count,
        average_turns=total_turns / case_count,
        average_revealed_slots=total_revealed_slots / case_count,
        hypothesis_hit_count=hypothesis_hit_count,
        hypothesis_hit_rate=hypothesis_hit_count / case_count,
        top3_hypothesis_hit_count=top3_hypothesis_hit_count,
        top3_hypothesis_hit_rate=top3_hypothesis_hit_count / case_count,
        final_answer_count=final_answer_count,
        final_answer_exact_hit_count=final_answer_exact_hit_count,
        final_answer_exact_hit_rate=final_answer_exact_hit_count / case_count,
        top1_final_answer_hit_count=top1_final_answer_hit_count,
        top1_final_answer_hit_rate=top1_final_answer_hit_count / case_count,
        final_answer_family_hit_count=final_answer_family_hit_count,
        final_answer_family_hit_rate=final_answer_family_hit_count / case_count,
        accepted_final_answer_count=accepted_final_answer_count,
        accepted_exact_hit_count=accepted_exact_hit_count,
        accepted_exact_accuracy=(
            accepted_exact_hit_count / accepted_final_answer_count
            if accepted_final_answer_count > 0
            else 0.0
        ),
        accepted_family_hit_count=accepted_family_hit_count,
        accepted_family_accuracy=(
            accepted_family_hit_count / accepted_final_answer_count
            if accepted_final_answer_count > 0
            else 0.0
        ),
        wrong_accepted_count=wrong_accepted_count,
        family_wrong_accepted_count=family_wrong_accepted_count,
        top_exact_correct_but_rejected_count=top_exact_correct_but_rejected_count,
        top_family_correct_but_rejected_count=top_family_correct_but_rejected_count,
        red_flag_case_count=red_flag_case_count,
        red_flag_hit_count=red_flag_hit_count,
        red_flag_hit_rate=(red_flag_hit_count / red_flag_case_count) if red_flag_case_count > 0 else 0.0,
        status_breakdown=status_breakdown,
    )
