"""封装 benchmark 统计复用的内部判断与归一化函数。"""

from __future__ import annotations

from difflib import SequenceMatcher

from ..replay.types import ReplayResult


FAMILY_MATCH_RATIO_THRESHOLD = 0.88


def count_revealed_slots(result: ReplayResult) -> int:
    """统计单个病例在回放中实际暴露了多少个槽位。"""

    revealed = {
        turn.revealed_slot_id
        for turn in result.turns
        if turn.revealed_slot_id is not None
    }
    return len(revealed)


def is_hypothesis_hit(result: ReplayResult) -> bool:
    """判断最终候选假设是否命中了病例的真实条件或阶段。"""

    report = result.final_report or {}
    candidate_hypotheses = report.get("candidate_hypotheses", [])
    predicted_names = [str(item.get("name", "")) for item in candidate_hypotheses]
    return matches_expected_name_list(predicted_names, result, match_mode="family")


def is_top3_hypothesis_hit(result: ReplayResult) -> bool:
    """判断真实答案是否进入最终候选前三名。"""

    report = result.final_report or {}
    candidate_hypotheses = report.get("candidate_hypotheses", [])
    predicted_names = [str(item.get("name", "")) for item in candidate_hypotheses[:3]]
    return matches_expected_name_list(predicted_names, result, match_mode="family")


def matches_expected_name_list(predicted_names: list[str], result: ReplayResult, *, match_mode: str) -> bool:
    """判断候选名称列表里是否包含病例真实条件或阶段。"""

    expected_targets = list(result.true_conditions)
    if result.true_disease_phase is not None:
        expected_targets.append(result.true_disease_phase)

    normalized_predictions = [normalize_text(item) for item in predicted_names if len(item) > 0]
    normalized_expected = [normalize_text(item) for item in expected_targets if len(item) > 0]

    for expected in normalized_expected:
        for predicted in normalized_predictions:
            if is_name_match(predicted, expected, match_mode=match_mode):
                return True
    return False


def is_final_answer_exact_hit(result: ReplayResult) -> bool:
    """判断最终 top answer 是否严格命中病例真实条件或阶段。"""

    answer_name = extract_final_answer_name(result)
    return matches_expected_answer(answer_name, result, match_mode="exact")


def is_final_answer_family_hit(result: ReplayResult) -> bool:
    """判断最终 top answer 是否宽松命中病例真实条件或阶段。"""

    answer_name = extract_final_answer_name(result)
    return matches_expected_answer(answer_name, result, match_mode="family")


def is_final_answer_accepted(result: ReplayResult) -> bool:
    """判断最终答案是否已经被结构化 stop 接受。"""

    report = result.final_report or {}
    stop_reason = str(report.get("stop_reason") or "")
    return result.status == "completed" or stop_reason == "final_answer_accepted"


def extract_final_answer_name(result: ReplayResult) -> str:
    """从最终报告中抽取实际被评估的 top answer 名称。"""

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


def matches_expected_answer(answer_name: str, result: ReplayResult, *, match_mode: str) -> bool:
    """判断某个答案名是否命中真实条件。"""

    normalized_answer = normalize_text(answer_name)
    if len(normalized_answer) == 0:
        return False

    expected_targets = list(result.true_conditions)
    if result.true_disease_phase is not None:
        expected_targets.append(result.true_disease_phase)

    for expected in expected_targets:
        normalized_expected = normalize_text(str(expected))
        if len(normalized_expected) == 0:
            continue
        if is_name_match(normalized_answer, normalized_expected, match_mode=match_mode):
            return True
    return False


def is_name_match(left: str, right: str, *, match_mode: str) -> bool:
    """判断两个已归一化名称是否匹配。"""

    if len(left) == 0 or len(right) == 0:
        return False
    if left == right:
        return True
    if match_mode != "family":
        return False
    if left in right or right in left:
        return True
    return SequenceMatcher(None, left, right).ratio() >= FAMILY_MATCH_RATIO_THRESHOLD


def is_red_flag_hit(result: ReplayResult) -> bool:
    """判断病例中标记的红旗线索是否至少有一个被系统成功确认。"""

    if len(result.red_flags) == 0:
        return False

    report = result.final_report or {}
    confirmed_slots = report.get("confirmed_slots", [])
    confirmed_names = {
        normalize_text(str(item.get("node_id", "")))
        for item in confirmed_slots
        if str(item.get("status", "")) == "true"
    }
    revealed_names = {
        normalize_text(turn.revealed_slot_id)
        for turn in result.turns
        if turn.revealed_slot_id is not None
    }

    for red_flag in result.red_flags:
        normalized_flag = normalize_text(red_flag)
        if normalized_flag in confirmed_names or normalized_flag in revealed_names:
            return True
    return False


def build_status_breakdown(results: list[ReplayResult]) -> dict[str, int]:
    """统计每种回放结束状态出现的次数。"""

    breakdown: dict[str, int] = {}
    for result in results:
        breakdown[result.status] = breakdown.get(result.status, 0) + 1
    return dict(sorted(breakdown.items(), key=lambda item: item[0]))


def normalize_text(value: str) -> str:
    """统一文本格式，便于做宽松命中比较。"""

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
