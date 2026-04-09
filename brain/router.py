"""负责根据 A4 演绎结果决定下一步进入的推理阶段。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .types import (
    A4DeductiveResult,
    DeductiveDecision,
    MctsAction,
    RouteDecision,
    SessionState,
    SimulationOutcome,
)


@dataclass
class RouterConfig:
    """保存 A4 路由阶段的基础策略开关。"""

    prefer_reverify_on_doubt: bool = True
    fallback_fail_count: int = 2


class ReasoningRouter:
    """根据状态与演绎分析结果在 A1-A4 阶段之间切换。"""

    # 初始化路由器配置。
    def __init__(self, config: RouterConfig | None = None) -> None:
        self.config = config or RouterConfig()

    # 在槽位更新后决定下一步应进入的推理阶段。
    def route_after_slot_update(self, session_state: SessionState) -> RouteDecision:
        if len(session_state.slots) == 0:
            return RouteDecision(stage="A1", reason="当前没有任何槽位信息，先提取核心线索。")

        if len(session_state.candidate_hypotheses) == 0:
            return RouteDecision(stage="A2", reason="已有基础线索，但尚未形成假设，需要执行 A2。")

        return RouteDecision(stage="A3", reason="已有假设，可进入 A3 选择验证动作。")

    # 在 A4 演绎分析后，根据存在性与确定性做阶段路由。
    def route_after_question_answer(
        self,
        deductive_result: A4DeductiveResult,
        action: Optional[MctsAction],
        session_state: SessionState,
    ) -> RouteDecision:
        if session_state.fail_count >= self.config.fallback_fail_count:
            return RouteDecision(stage="FALLBACK", reason="失败计数达到阈值，进入兜底流程。")

        deductive_decision = self.build_deductive_decision(deductive_result, action, session_state)
        return self.decide_next_stage(deductive_decision, session_state)

    # 根据 A4 结果构造更适合代码路由的演绎决策对象。
    def build_deductive_decision(
        self,
        deductive_result: A4DeductiveResult,
        action: Optional[MctsAction],
        session_state: SessionState,
    ) -> DeductiveDecision:
        hypothesis_id = action.hypothesis_id if action is not None else None
        topic_id = action.topic_id if action is not None else None

        if deductive_result.existence == "exist" and deductive_result.certainty == "confident":
            next_stage = "STOP" if self._hypothesis_margin_is_sufficient(session_state) else "A3"
            return DeductiveDecision(
                existence=deductive_result.existence,
                certainty=deductive_result.certainty,
                decision_type="confirm_hypothesis",
                diagnostic_rationale="验证结果为存在且确信，支持当前假设。",
                next_stage=next_stage,
                metadata={"next_topic_id": topic_id, "next_hypothesis_id": hypothesis_id},
            )

        if deductive_result.existence == "non_exist" and deductive_result.certainty == "confident":
            return DeductiveDecision(
                existence=deductive_result.existence,
                certainty=deductive_result.certainty,
                decision_type="exclude_hypothesis",
                contradiction_explanation="验证结果为不存在且确信，需要下调或排除当前假设。",
                diagnostic_rationale="关键验证点被明确否定。",
                next_stage="A2",
                metadata={"next_topic_id": topic_id, "next_hypothesis_id": hypothesis_id},
            )

        if deductive_result.existence == "exist" and deductive_result.certainty == "doubt":
            return DeductiveDecision(
                existence=deductive_result.existence,
                certainty=deductive_result.certainty,
                decision_type="reverify_hypothesis",
                diagnostic_rationale="验证点疑似存在，但仍需继续细化确认。",
                next_stage="A3" if self.config.prefer_reverify_on_doubt else "A2",
                metadata={"next_topic_id": topic_id, "next_hypothesis_id": hypothesis_id},
            )

        if deductive_result.existence == "non_exist" and deductive_result.certainty == "doubt":
            return DeductiveDecision(
                existence=deductive_result.existence,
                certainty=deductive_result.certainty,
                decision_type="need_more_information",
                contradiction_explanation="当前否定证据仍不稳固，需要补充一次矛盾分析。",
                diagnostic_rationale="验证点可能不存在，但尚不足以直接排除。",
                next_stage="A3",
                metadata={
                    "next_topic_id": topic_id,
                    "next_hypothesis_id": hypothesis_id,
                    "need_contradiction_analysis": True,
                },
            )

        return DeductiveDecision(
            existence=deductive_result.existence,
            certainty=deductive_result.certainty,
            decision_type="switch_hypothesis",
            diagnostic_rationale="当前证据不足以支持既有假设，建议回到上游重新整理线索。",
            next_stage="A1",
            metadata={"next_topic_id": topic_id, "next_hypothesis_id": hypothesis_id},
        )

    # 根据演绎决策决定下一步应进入的阶段。
    def decide_next_stage(
        self,
        deductive_decision: DeductiveDecision,
        session_state: SessionState,
    ) -> RouteDecision:
        if session_state.fail_count >= self.config.fallback_fail_count:
            return RouteDecision(stage="FALLBACK", reason="失败计数达到阈值，进入兜底流程。")

        return RouteDecision(
            stage=deductive_decision.next_stage,
            reason=deductive_decision.diagnostic_rationale or deductive_decision.contradiction_explanation or "已根据 A4 结果更新下一阶段。",
            next_topic_id=deductive_decision.metadata.get("next_topic_id"),
            next_hypothesis_id=deductive_decision.metadata.get("next_hypothesis_id"),
            metadata=dict(deductive_decision.metadata),
        )

    # 在 simulation 完成后，根据收益高低决定继续验证还是回退。
    def route_after_simulation(
        self,
        outcome: SimulationOutcome,
        current_hypothesis_id: Optional[str] = None,
    ) -> RouteDecision:
        if outcome.expected_reward > 0:
            return RouteDecision(
                stage="A3",
                reason="局部 simulation 显示继续验证当前动作有正收益。",
                next_hypothesis_id=current_hypothesis_id,
                metadata={"expected_reward": outcome.expected_reward},
            )

        return RouteDecision(
            stage="A2",
            reason="局部 simulation 收益偏低，建议回到 A2 考察其他假设。",
            next_hypothesis_id=current_hypothesis_id,
            metadata={"expected_reward": outcome.expected_reward},
        )

    # 判断当前主假设是否已经形成足够明显的分数优势。
    def _hypothesis_margin_is_sufficient(self, session_state: SessionState) -> bool:
        if len(session_state.candidate_hypotheses) < 2:
            return len(session_state.candidate_hypotheses) == 1

        ranked = sorted(session_state.candidate_hypotheses, key=lambda item: (-item.score, item.name))
        return ranked[0].score - ranked[1].score >= 1.0
