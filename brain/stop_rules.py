"""负责判断问诊是否可以停止，或是否需要降级处理。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .types import HypothesisScore, SessionState, StopDecision


@dataclass
class StopRuleConfig:
    """保存终止条件与 fallback 条件相关阈值。"""

    min_top1_margin: float = 0.25
    min_top1_score: float = 1.0
    max_fail_count: int = 2
    min_candidate_count: int = 1


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
