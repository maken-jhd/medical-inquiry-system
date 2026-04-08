"""根据候选节点分数选择下一问。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .types import QuestionCandidate, SessionState


@dataclass
class QuestionPolicy:
    """保存下一问排序所用的启发式权重。"""

    red_flag_multiplier: float = 2.0
    graph_weight_multiplier: float = 1.0
    information_gain_multiplier: float = 1.5
    repeat_penalty: float = 3.0


class QuestionSelector:
    """负责为候选问题打分并挑选当前最优下一问。"""

    # 初始化提问策略；未传入时使用默认启发式参数。
    def __init__(self, policy: QuestionPolicy | None = None) -> None:
        self.policy = policy or QuestionPolicy()

    # 结合红旗权重、图谱权重和重复惩罚计算单个候选问题得分。
    def score_candidate(self, candidate: QuestionCandidate, session_state: SessionState) -> float:
        score = 0.0
        score += candidate.red_flag_score * self.policy.red_flag_multiplier
        score += candidate.graph_weight * self.policy.graph_weight_multiplier
        score += candidate.information_gain * self.policy.information_gain_multiplier
        score += candidate.priority

        if candidate.node_id in session_state.asked_node_ids or candidate.asked_before:
            score -= self.policy.repeat_penalty

        return score

    # 对候选问题列表排序并返回当前最值得提问的节点。
    def select_next_question(
        self,
        candidates: Iterable[QuestionCandidate],
        session_state: SessionState,
    ) -> Optional[QuestionCandidate]:
        ranked = sorted(
            candidates,
            key=lambda candidate: (
                -self.score_candidate(candidate, session_state),
                candidate.name,
            ),
        )

        if len(ranked) == 0:
            return None

        return ranked[0]
