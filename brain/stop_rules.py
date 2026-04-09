"""负责判断问诊是否可以停止，或是否需要降级处理。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .types import FinalAnswerScore, HypothesisScore, SessionState, StopDecision


@dataclass
class StopRuleConfig:
    """保存终止条件与 fallback 条件相关阈值。"""

    min_top1_margin: float = 0.25
    min_top1_score: float = 1.0
    max_fail_count: int = 2
    min_candidate_count: int = 1
    max_rollouts: int = 8
    max_tree_depth: int = 6
    min_answer_consistency: float = 0.45
    min_agent_eval_score: float = 0.65
    min_final_score: float = 0.55


class StopRuleEngine:
    """根据候选假设分数与失败次数做终止判断。"""

    # 初始化终止规则配置。
    def __init__(self, config: StopRuleConfig | None = None) -> None:
        self.config = config or StopRuleConfig()

    # 判断当前证据是否足以结束问诊并输出结果。
    def check_sufficiency(
        self,
        session_state: SessionState,
        hypotheses: Iterable[HypothesisScore],
    ) -> StopDecision:
        ranked = list(hypotheses)

        if len(ranked) == 0:
            return StopDecision(False, "no_hypothesis")

        if len(ranked) == 1 and ranked[0].score >= self.config.min_top1_score:
            return StopDecision(True, "single_hypothesis_confident", ranked[0].score)

        if len(ranked) >= 2:
            margin = ranked[0].score - ranked[1].score

            if ranked[0].score >= self.config.min_top1_score and margin >= self.config.min_top1_margin:
                return StopDecision(
                    True,
                    "top1_margin_sufficient",
                    ranked[0].score,
                    {"margin": margin},
                )

        return StopDecision(False, "insufficient_evidence")

    # 判断当前会话是否应进入降级或兜底流程。
    def should_fallback(self, session_state: SessionState) -> StopDecision:
        if session_state.fail_count >= self.config.max_fail_count:
            return StopDecision(True, "fallback_due_to_fail_count")

        return StopDecision(False, "continue")

    # 判断单条 rollout 是否应停止继续向下扩展。
    def should_stop_rollout(self, current_depth: int, fail_count: int = 0) -> StopDecision:
        if current_depth >= self.config.max_tree_depth:
            return StopDecision(True, "max_tree_depth_reached")

        if fail_count >= self.config.max_fail_count:
            return StopDecision(True, "rollout_fail_threshold_reached")

        return StopDecision(False, "continue_rollout")

    # 判断当前搜索过程是否应整体停止。
    def should_stop_search(self, rollout_count: int, fail_count: int = 0) -> StopDecision:
        if rollout_count >= self.config.max_rollouts:
            return StopDecision(True, "max_rollouts_reached")

        if fail_count >= self.config.max_fail_count:
            return StopDecision(True, "search_fail_threshold_reached")

        return StopDecision(False, "continue_search")

    # 判断最终答案评分是否足以被接受为当前结果。
    def should_accept_final_answer(self, answer_score: FinalAnswerScore | None) -> StopDecision:
        if answer_score is None:
            return StopDecision(False, "no_answer_score")

        if answer_score.consistency < self.config.min_answer_consistency:
            return StopDecision(False, "consistency_too_low", answer_score.consistency)

        if answer_score.agent_evaluation < self.config.min_agent_eval_score:
            return StopDecision(False, "agent_eval_too_low", answer_score.agent_evaluation)

        if answer_score.final_score < self.config.min_final_score:
            return StopDecision(False, "final_score_too_low", answer_score.final_score)

        return StopDecision(True, "final_answer_accepted", answer_score.final_score)
