"""负责根据 A4 演绎结果决定下一步进入的推理阶段。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .types import A4DeductiveResult, MctsAction, RouteDecision, SessionState, SimulationOutcome


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

        hypothesis_id = action.hypothesis_id if action is not None else None
        topic_id = action.topic_id if action is not None else None

        if deductive_result.existence == "exist" and deductive_result.certainty == "confident":
            return RouteDecision(
                stage="A3",
                reason="验证结果为存在且确信，继续围绕当前假设做验证。",
                next_topic_id=topic_id,
                next_hypothesis_id=hypothesis_id,
            )

        if deductive_result.existence == "non_exist" and deductive_result.certainty == "confident":
            return RouteDecision(
                stage="A2",
                reason="验证结果为不存在且确信，建议回到 A2 调整或重生成假设。",
                next_topic_id=topic_id,
                next_hypothesis_id=hypothesis_id,
            )

        if deductive_result.existence == "exist" and deductive_result.certainty == "doubt":
            next_stage = "A3" if self.config.prefer_reverify_on_doubt else "A2"
            return RouteDecision(
                stage=next_stage,
                reason="验证结果为存在但存疑，优先继续细化验证。",
                next_topic_id=topic_id,
                next_hypothesis_id=hypothesis_id,
            )

        if deductive_result.existence == "non_exist" and deductive_result.certainty == "doubt":
            return RouteDecision(
                stage="A3",
                reason="验证结果为不存在但存疑，允许再进行一次补充验证。",
                next_topic_id=topic_id,
                next_hypothesis_id=hypothesis_id,
            )

        return RouteDecision(
            stage="A1",
            reason="当前证据仍然不足，回到 A1 提取新的核心线索。",
            next_topic_id=topic_id,
            next_hypothesis_id=hypothesis_id,
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
