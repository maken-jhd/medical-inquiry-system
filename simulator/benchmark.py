"""对虚拟病人批量回放结果做结构化评测汇总。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List

from .replay_engine import ReplayResult


@dataclass
class BenchmarkSummary:
    """表示一组回放结果的评测摘要。"""

    case_count: int
    completed_count: int
    completion_rate: float
    max_turn_reached_count: int
    average_turns: float
    average_revealed_slots: float
    hypothesis_hit_count: int
    hypothesis_hit_rate: float
    red_flag_case_count: int
    red_flag_hit_count: int
    red_flag_hit_rate: float
    status_breakdown: dict[str, int] = field(default_factory=dict)


# 汇总多条回放结果，生成更完整的离线评测指标。
def summarize_benchmark(results: Iterable[ReplayResult]) -> BenchmarkSummary:
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
            red_flag_case_count=0,
            red_flag_hit_count=0,
            red_flag_hit_rate=0.0,
            status_breakdown={},
        )

    completed_count = sum(1 for item in results_list if item.status == "completed")
    max_turn_reached_count = sum(1 for item in results_list if item.status == "max_turn_reached")
    total_turns = sum(len(item.turns) for item in results_list)
    total_revealed_slots = sum(_count_revealed_slots(item) for item in results_list)
    hypothesis_hit_count = sum(1 for item in results_list if _is_hypothesis_hit(item))
    red_flag_case_count = sum(1 for item in results_list if len(item.red_flags) > 0)
    red_flag_hit_count = sum(1 for item in results_list if _is_red_flag_hit(item))
    status_breakdown = _build_status_breakdown(results_list)

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
        red_flag_case_count=red_flag_case_count,
        red_flag_hit_count=red_flag_hit_count,
        red_flag_hit_rate=(red_flag_hit_count / red_flag_case_count) if red_flag_case_count > 0 else 0.0,
        status_breakdown=status_breakdown,
    )


# 统计单个病例在回放中实际暴露了多少个槽位。
def _count_revealed_slots(result: ReplayResult) -> int:
    revealed = {
        turn.revealed_slot_id
        for turn in result.turns
        if turn.revealed_slot_id is not None
    }
    return len(revealed)


# 判断最终候选假设是否命中了病例的真实条件或阶段。
def _is_hypothesis_hit(result: ReplayResult) -> bool:
    report = result.final_report or {}
    candidate_hypotheses = report.get("candidate_hypotheses", [])
    predicted_names = [str(item.get("name", "")) for item in candidate_hypotheses]
    expected_targets = list(result.true_conditions)

    if result.true_disease_phase is not None:
        expected_targets.append(result.true_disease_phase)

    normalized_predictions = [_normalize_text(item) for item in predicted_names if len(item) > 0]
    normalized_expected = [_normalize_text(item) for item in expected_targets if len(item) > 0]

    for expected in normalized_expected:
        for predicted in normalized_predictions:
            if expected == predicted or expected in predicted or predicted in expected:
                return True

    return False


# 判断病例中标记的红旗线索是否至少有一个被系统成功确认。
def _is_red_flag_hit(result: ReplayResult) -> bool:
    if len(result.red_flags) == 0:
        return False

    report = result.final_report or {}
    confirmed_slots = report.get("confirmed_slots", [])
    confirmed_names = {
        _normalize_text(str(item.get("node_id", "")))
        for item in confirmed_slots
        if str(item.get("status", "")) == "true"
    }
    revealed_names = {
        _normalize_text(turn.revealed_slot_id)
        for turn in result.turns
        if turn.revealed_slot_id is not None
    }

    for red_flag in result.red_flags:
        normalized_flag = _normalize_text(red_flag)
        if normalized_flag in confirmed_names or normalized_flag in revealed_names:
            return True

    return False


# 统计每种回放结束状态出现的次数。
def _build_status_breakdown(results: List[ReplayResult]) -> dict[str, int]:
    breakdown: dict[str, int] = {}

    for result in results:
        breakdown[result.status] = breakdown.get(result.status, 0) + 1

    return dict(sorted(breakdown.items(), key=lambda item: item[0]))


# 统一文本格式，便于做宽松命中比较。
def _normalize_text(value: str) -> str:
    return value.strip().replace(" ", "").lower()
