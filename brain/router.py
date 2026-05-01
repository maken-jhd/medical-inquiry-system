"""负责根据上一轮动作解释结果决定下一步进入的推理阶段。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .types import (
    MctsAction,
    PendingActionDecision,
    PendingActionResult,
    RouteDecision,
    SessionState,
    SimulationOutcome,
)


@dataclass
class RouterConfig:
    """保存上一轮动作路由阶段的基础策略开关。"""

    prefer_reverify_on_hedged: bool = True
    fallback_fail_count: int = 2


class ReasoningRouter:
    """根据状态与上一轮动作解释结果在 A1/A2/A3 间切换。"""

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

    # 在上一轮动作解释后，根据 polarity 与回答清晰度做阶段路由。
    def route_after_pending_action(
        self,
        pending_action_result: PendingActionResult,
        action: Optional[MctsAction],
        session_state: SessionState,
    ) -> RouteDecision:
        if session_state.fail_count >= self.config.fallback_fail_count:
            return RouteDecision(stage="FALLBACK", reason="失败计数达到阈值，进入兜底流程。")

        pending_action_decision = self.build_pending_action_decision(
            pending_action_result,
            action,
            session_state,
        )
        return self.decide_next_stage(pending_action_decision, session_state)

    # 根据上一轮动作结果构造更适合代码路由的决策对象。
    def build_pending_action_decision(
        self,
        pending_action_result: PendingActionResult,
        action: Optional[MctsAction],
        session_state: SessionState,
    ) -> PendingActionDecision:
        # action 里携带的是“当前到底在验证哪个 hypothesis/feature/topic”，
        # 先抽出来放进 metadata，后续路由和 audit 都会继续使用。
        hypothesis_id = action.hypothesis_id if action is not None else None
        topic_id = action.topic_id if action is not None else None
        contradicted_feature = action.target_node_id if action is not None else None
        contradicted_feature_name = action.target_node_name if action is not None else None
        polarity = self._result_polarity(pending_action_result)

        # 明确阳性：支持当前主假设；若 hypothesis margin 已足够大，可以给出 STOP 倾向。
        if polarity == "present" and pending_action_result.resolution == "clear":
            next_stage = "STOP" if self._hypothesis_margin_is_sufficient(session_state) else "A3"
            return PendingActionDecision(
                polarity=polarity,
                resolution=pending_action_result.resolution,
                decision_type="confirm_hypothesis",
                diagnostic_rationale="验证结果为明确存在，支持当前假设。",
                next_stage=next_stage,
                should_terminate_current_path=next_stage == "STOP",
                should_spawn_alternative_hypotheses=False,
                metadata={
                    "next_topic_id": topic_id,
                    "next_hypothesis_id": hypothesis_id,
                    "path_terminal": next_stage == "STOP",
                    "confirmed_feature": contradicted_feature,
                    "confirmed_feature_name": contradicted_feature_name,
                    "polarity": polarity,
                },
            )

        # 明确阴性：当前验证点与主假设直接矛盾，优先切回 A2 重整候选。
        if polarity == "absent" and pending_action_result.resolution == "clear":
            return PendingActionDecision(
                polarity=polarity,
                resolution=pending_action_result.resolution,
                decision_type="exclude_hypothesis",
                contradiction_explanation="验证结果为明确不存在，该证据与当前主假设直接矛盾，需要切回 A2 重整假设。",
                diagnostic_rationale="关键验证点被明确否定。",
                next_stage="A2",
                should_terminate_current_path=False,
                should_spawn_alternative_hypotheses=True,
                metadata={
                    "next_topic_id": topic_id,
                    "next_hypothesis_id": hypothesis_id,
                    "contradicted_feature": contradicted_feature,
                    "contradicted_feature_name": contradicted_feature_name,
                    "need_contradiction_analysis": False,
                    "path_terminal": False,
                    "polarity": polarity,
                },
            )

        # 模糊阳性：倾向继续 A3 复核，而不是过早切回 A2。
        if polarity == "present" and pending_action_result.resolution == "hedged":
            return PendingActionDecision(
                polarity=polarity,
                resolution=pending_action_result.resolution,
                decision_type="reverify_hypothesis",
                diagnostic_rationale="验证点倾向存在，但回答仍带保留，需继续细化确认。",
                next_stage="A3" if self.config.prefer_reverify_on_hedged else "A2",
                should_terminate_current_path=False,
                should_spawn_alternative_hypotheses=False,
                metadata={
                    "next_topic_id": topic_id,
                    "next_hypothesis_id": hypothesis_id,
                    "next_topic_id_hint": contradicted_feature,
                    "path_terminal": False,
                    "polarity": polarity,
                },
            )

        # 模糊阴性：先保留矛盾分析空间，避免把轻症/表达模糊误当成强反证。
        if polarity in {"absent", "unclear"} and pending_action_result.resolution == "hedged":
            return PendingActionDecision(
                polarity=polarity,
                resolution=pending_action_result.resolution,
                decision_type="need_more_information",
                contradiction_explanation=(
                    f"“{contradicted_feature_name or '当前验证点'}”目前呈现弱否定。"
                    "这可能是轻症、患者忽略、或提问方式不够贴切导致，建议继续做矛盾分析并改问法复核。"
                ),
                diagnostic_rationale="验证点可能不存在，但尚不足以直接排除当前假设，优先继续 A3 复核。",
                next_stage="A3",
                should_terminate_current_path=False,
                should_spawn_alternative_hypotheses=True,
                metadata={
                    "next_topic_id": topic_id,
                    "next_hypothesis_id": hypothesis_id,
                    "need_contradiction_analysis": True,
                    "contradicted_feature": contradicted_feature,
                    "contradicted_feature_name": contradicted_feature_name,
                    "path_terminal": False,
                    "polarity": polarity,
                },
            )

        # 其余情况统一视作“当前证据不足以维持原路径”，回上游重新整理。
        return PendingActionDecision(
            polarity=polarity,
            resolution=pending_action_result.resolution,
            decision_type="switch_hypothesis",
            diagnostic_rationale="当前证据不足以支持既有假设，建议回到上游重新整理线索。",
            next_stage="A1",
            should_terminate_current_path=False,
            should_spawn_alternative_hypotheses=True,
            metadata={
                "next_topic_id": topic_id,
                "next_hypothesis_id": hypothesis_id,
                "contradicted_feature": contradicted_feature,
                "contradicted_feature_name": contradicted_feature_name,
                "path_terminal": False,
                "polarity": polarity,
            },
        )

    # 根据演绎决策决定下一步应进入的阶段。
    def decide_next_stage(
        self,
        pending_action_decision: PendingActionDecision,
        session_state: SessionState,
    ) -> RouteDecision:
        if session_state.fail_count >= self.config.fallback_fail_count:
            return RouteDecision(stage="FALLBACK", reason="失败计数达到阈值，进入兜底流程。")

        return RouteDecision(
            stage=pending_action_decision.next_stage,
            reason=(
                pending_action_decision.diagnostic_rationale
                or pending_action_decision.contradiction_explanation
                or "已根据上一轮动作解释结果更新下一阶段。"
            ),
            next_topic_id=pending_action_decision.metadata.get("next_topic_id"),
            next_hypothesis_id=pending_action_decision.metadata.get("next_hypothesis_id"),
            metadata={
                **dict(pending_action_decision.metadata),
                "should_terminate_current_path": pending_action_decision.should_terminate_current_path,
                "should_spawn_alternative_hypotheses": pending_action_decision.should_spawn_alternative_hypotheses,
            },
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

    def _result_polarity(self, pending_action_result: PendingActionResult) -> str:
        if pending_action_result.polarity in {"present", "absent", "unclear"}:
            return pending_action_result.polarity
        metadata_polarity = str(pending_action_result.metadata.get("polarity") or "").strip()
        if metadata_polarity in {"present", "absent", "unclear"}:
            return metadata_polarity
        return "unclear"
