"""对虚拟病人批量回放结果做结构化评测汇总。"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Iterable, List

from .replay_engine import ReplayResult


FAMILY_MATCH_RATIO_THRESHOLD = 0.88


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
    top3_hypothesis_hit_count: int
    top3_hypothesis_hit_rate: float
    final_answer_count: int
    final_answer_exact_hit_count: int
    final_answer_exact_hit_rate: float
    top1_final_answer_hit_count: int
    top1_final_answer_hit_rate: float
    final_answer_family_hit_count: int
    final_answer_family_hit_rate: float
    accepted_final_answer_count: int
    accepted_exact_hit_count: int
    accepted_exact_accuracy: float
    accepted_family_hit_count: int
    accepted_family_accuracy: float
    wrong_accepted_count: int
    family_wrong_accepted_count: int
    top_exact_correct_but_rejected_count: int
    top_family_correct_but_rejected_count: int
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
    total_revealed_slots = sum(_count_revealed_slots(item) for item in results_list)
    hypothesis_hit_count = sum(1 for item in results_list if _is_hypothesis_hit(item))
    top3_hypothesis_hit_count = sum(1 for item in results_list if _is_top3_hypothesis_hit(item))
    final_answer_count = sum(1 for item in results_list if len(_extract_final_answer_name(item)) > 0)
    final_answer_exact_hit_count = sum(1 for item in results_list if _is_final_answer_exact_hit(item))
    top1_final_answer_hit_count = final_answer_exact_hit_count
    final_answer_family_hit_count = sum(1 for item in results_list if _is_final_answer_family_hit(item))
    accepted_results = [item for item in results_list if _is_final_answer_accepted(item)]
    accepted_final_answer_count = len(accepted_results)
    accepted_exact_hit_count = sum(1 for item in accepted_results if _is_final_answer_exact_hit(item))
    accepted_family_hit_count = sum(1 for item in accepted_results if _is_final_answer_family_hit(item))
    wrong_accepted_count = accepted_final_answer_count - accepted_exact_hit_count
    family_wrong_accepted_count = accepted_final_answer_count - accepted_family_hit_count
    top_exact_correct_but_rejected_count = sum(
        1
        for item in results_list
        if not _is_final_answer_accepted(item) and _is_final_answer_exact_hit(item)
    )
    top_family_correct_but_rejected_count = sum(
        1
        for item in results_list
        if not _is_final_answer_accepted(item) and _is_final_answer_family_hit(item)
    )
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


# 构建未正常 accepted 的病例索引，便于全量 benchmark 后优先复盘异常样本。
def build_non_completed_case_report(results: Iterable[ReplayResult]) -> dict:
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
    return _matches_expected_name_list(predicted_names, result, match_mode="family")


# 判断真实答案是否进入最终候选前三名。
def _is_top3_hypothesis_hit(result: ReplayResult) -> bool:
    report = result.final_report or {}
    candidate_hypotheses = report.get("candidate_hypotheses", [])
    predicted_names = [str(item.get("name", "")) for item in candidate_hypotheses[:3]]
    return _matches_expected_name_list(predicted_names, result, match_mode="family")


# 判断候选名称列表里是否包含病例真实条件或阶段。
def _matches_expected_name_list(predicted_names: list[str], result: ReplayResult, *, match_mode: str) -> bool:
    expected_targets = list(result.true_conditions)

    if result.true_disease_phase is not None:
        expected_targets.append(result.true_disease_phase)

    normalized_predictions = [_normalize_text(item) for item in predicted_names if len(item) > 0]
    normalized_expected = [_normalize_text(item) for item in expected_targets if len(item) > 0]

    for expected in normalized_expected:
        for predicted in normalized_predictions:
            if _is_name_match(predicted, expected, match_mode=match_mode):
                return True

    return False


# 判断最终 top answer 是否严格命中病例真实条件或阶段。
def _is_final_answer_exact_hit(result: ReplayResult) -> bool:
    answer_name = _extract_final_answer_name(result)
    return _matches_expected_answer(answer_name, result, match_mode="exact")


# 判断最终 top answer 是否宽松命中病例真实条件或阶段。
def _is_final_answer_family_hit(result: ReplayResult) -> bool:
    answer_name = _extract_final_answer_name(result)
    return _matches_expected_answer(answer_name, result, match_mode="family")


# 判断最终答案是否已经被结构化 stop 接受。
def _is_final_answer_accepted(result: ReplayResult) -> bool:
    report = result.final_report or {}
    stop_reason = str(report.get("stop_reason") or "")
    return result.status == "completed" or stop_reason == "final_answer_accepted"


# 从最终报告中抽取实际被评估的 top answer 名称，兼容不同报告结构。
def _extract_final_answer_name(result: ReplayResult) -> str:
    report = result.final_report or {}
    best_final_answer = report.get("best_final_answer")

    if isinstance(best_final_answer, dict):
        answer_name = str(best_final_answer.get("answer_name") or "").strip()
        if len(answer_name) > 0:
            return answer_name

    for key in ("answer_group_scores", "final_answer_scores"):
        scores = report.get(key, [])
        if not isinstance(scores, list) or len(scores) == 0:
            continue

        first_score = scores[0]
        if not isinstance(first_score, dict):
            continue

        answer_name = str(first_score.get("answer_name") or "").strip()
        if len(answer_name) > 0:
            return answer_name

    return str(report.get("best_answer_name") or "").strip()


# 判断某个答案名是否命中真实条件；family 模式使用轻量名称相似近似。
def _matches_expected_answer(answer_name: str, result: ReplayResult, *, match_mode: str) -> bool:
    normalized_answer = _normalize_text(answer_name)

    if len(normalized_answer) == 0:
        return False

    expected_targets = list(result.true_conditions)

    if result.true_disease_phase is not None:
        expected_targets.append(result.true_disease_phase)

    for expected in expected_targets:
        normalized_expected = _normalize_text(str(expected))

        if len(normalized_expected) == 0:
            continue

        if _is_name_match(normalized_answer, normalized_expected, match_mode=match_mode):
            return True

    return False


# 判断两个已归一化名称是否匹配。
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


# 生成单条未完成病例的轻量复盘记录。
def _build_non_completed_case_record(result: ReplayResult) -> dict:
    report = result.final_report or {}
    answer_name = _extract_final_answer_name(result)
    category = _classify_non_completed_case(result)
    last_turn = result.turns[-1] if len(result.turns) > 0 else None

    return {
        "case_id": result.case_id,
        "case_title": result.case_title,
        "status": result.status,
        "category": category,
        "true_conditions": list(result.true_conditions),
        "true_disease_phase": result.true_disease_phase,
        "final_answer_name": answer_name,
        "final_answer_exact_hit": _is_final_answer_exact_hit(result),
        "final_answer_family_hit": _is_final_answer_family_hit(result),
        "top1_final_answer_hit": _is_final_answer_exact_hit(result),
        "top3_hypothesis_hit": _is_top3_hypothesis_hit(result),
        "hypothesis_hit": _is_hypothesis_hit(result),
        "stop_reason": str(report.get("stop_reason") or ""),
        "turn_count": len(result.turns),
        "revealed_slot_count": _count_revealed_slots(result),
        "candidate_hypotheses_top5": _compact_named_items(report.get("candidate_hypotheses", []), limit=5),
        "final_answer_scores_top5": _compact_named_items(
            report.get("answer_group_scores") or report.get("final_answer_scores") or [],
            limit=5,
        ),
        "last_turn": _compact_last_turn(last_turn),
        "error": _compact_error(result.error),
        "timing": _compact_timing(result.timing),
    }


# 将未完成病例按最需要看的问题类型粗分组。
def _classify_non_completed_case(result: ReplayResult) -> str:
    status = str(result.status or "unknown")

    if status == "failed":
        error_code = str((result.error or {}).get("code") or "unknown_error")
        return f"failed::{error_code}"

    if status == "max_turn_reached":
        if _is_final_answer_exact_hit(result):
            return "max_turn_reached::top_exact_correct_but_rejected"
        if _is_final_answer_family_hit(result):
            return "max_turn_reached::top_family_correct_but_rejected"
        if len(_extract_final_answer_name(result)) == 0:
            return "max_turn_reached::no_final_answer"
        if _is_hypothesis_hit(result):
            return "max_turn_reached::true_candidate_but_final_wrong"
        return "max_turn_reached::true_candidate_missing"

    return f"non_completed::{status}"


# 保留候选/答案条目的关键字段，避免异常报告膨胀成完整 final_report。
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


# 压缩最后一轮问答，帮助快速判断卡在了哪一问。
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


# 压缩错误信息，便于按 code/stage 聚合失败病例。
def _compact_error(error: dict) -> dict:
    if not isinstance(error, dict) or len(error) == 0:
        return {}

    return {
        key: error.get(key)
        for key in ("code", "stage", "prompt_name", "message", "attempts", "error_type")
        if key in error
    }


# 保留病例级耗时字段，便于排查全量运行中的慢样本。
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


# 统一文本格式，便于做宽松命中比较。
def _normalize_text(value: str) -> str:
    return (
        value.strip()
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
