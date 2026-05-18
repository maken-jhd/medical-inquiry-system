"""定义 benchmark 摘要结果类型。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BenchmarkSummary:
    """表示一组回放结果的评测摘要。"""

    # 本次汇总包含的病例总数。
    case_count: int
    # 已正常完成并产出 final_report 的病例数。
    completed_count: int
    # completed_count / case_count。
    completion_rate: float
    # 达到最大轮次后仍未 accepted 的病例数。
    max_turn_reached_count: int
    # 平均每个病例问了多少轮。
    average_turns: float
    # 平均每个病例实际揭示了多少个槽位。
    average_revealed_slots: float
    # 候选假设列表是否命中真实病种的病例数。
    hypothesis_hit_count: int
    # hypothesis_hit_count / case_count。
    hypothesis_hit_rate: float
    # Top-3 候选是否命中真实病种的病例数。
    top3_hypothesis_hit_count: int
    # top3_hypothesis_hit_count / case_count。
    top3_hypothesis_hit_rate: float
    # 成功抽取到最终答案名称的病例数。
    final_answer_count: int
    # 最终答案与真实病种严格一致的病例数。
    final_answer_exact_hit_count: int
    # final_answer_exact_hit_count / case_count。
    final_answer_exact_hit_rate: float
    # Top-1 最终答案严格命中的病例数；当前与 exact_hit 同口径。
    top1_final_answer_hit_count: int
    # top1_final_answer_hit_count / case_count。
    top1_final_answer_hit_rate: float
    # 最终答案在宽松 family 匹配下命中的病例数。
    final_answer_family_hit_count: int
    # final_answer_family_hit_count / case_count。
    final_answer_family_hit_rate: float
    # 被结构化 stop 接受的最终答案数量。
    accepted_final_answer_count: int
    # 被接受的最终答案中，严格命中的数量。
    accepted_exact_hit_count: int
    # accepted_exact_hit_count / accepted_final_answer_count。
    accepted_exact_accuracy: float
    # 被接受的最终答案中，family 命中的数量。
    accepted_family_hit_count: int
    # accepted_family_hit_count / accepted_final_answer_count。
    accepted_family_accuracy: float
    # 被接受但严格错误的数量。
    wrong_accepted_count: int
    # 被接受但 family 仍错误的数量。
    family_wrong_accepted_count: int
    # Top-1 已正确但 verifier/stop 未放行的数量。
    top_exact_correct_but_rejected_count: int
    # family 层面已正确但 verifier/stop 未放行的数量。
    top_family_correct_but_rejected_count: int
    # 含红旗线索的病例数量。
    red_flag_case_count: int
    # 红旗线索至少命中一次的病例数量。
    red_flag_hit_count: int
    # red_flag_hit_count / red_flag_case_count。
    red_flag_hit_rate: float
    # 各种状态值的分布，如 completed / max_turn_reached / failed。
    status_breakdown: dict[str, int] = field(default_factory=dict)
