"""负责对候选动作执行浅层 simulation 预演。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional

from .types import HypothesisCandidate, HypothesisScore, MctsAction, SessionState, SimulationOutcome


@dataclass
class SimulationConfig:
    """保存局部 rollout 的基础参数。"""

    positive_branch_probability: float = 0.6
    positive_reward_multiplier: float = 1.0
    negative_reward_multiplier: float = 0.45
    relation_bonus_map: dict[str, float] | None = None

    # 初始化默认的关系收益加成表。
    def __post_init__(self) -> None:
        if self.relation_bonus_map is None:
            self.relation_bonus_map = {
                "MANIFESTS_AS": 1.0,
                "HAS_LAB_FINDING": 1.15,
                "DIAGNOSED_BY": 1.2,
                "REQUIRES_DETAIL": 0.8,
                "ASSOCIATED_WITH": 0.7,
            }


class SimulationEngine:
    """根据局部动作和当前假设做浅层前瞻预演。"""

    # 初始化 simulation 参数。
    def __init__(self, config: SimulationConfig | None = None) -> None:
        self.config = config or SimulationConfig()

    # 对一组候选动作做批量浅层预演。
    def simulate_actions(
        self,
        actions: Iterable[MctsAction],
        session_state: SessionState,
        primary_hypothesis: HypothesisCandidate | HypothesisScore | None = None,
    ) -> List[SimulationOutcome]:
        return [
            self.simulate_action(action, session_state, primary_hypothesis)
            for action in actions
        ]

    # 对单个候选动作做正反两分支的浅层收益估计。
    def simulate_action(
        self,
        action: MctsAction,
        session_state: SessionState,
        primary_hypothesis: HypothesisCandidate | HypothesisScore | None = None,
    ) -> SimulationOutcome:
        relation_type = str(action.metadata.get("relation_type", ""))
        relation_bonus = float(self.config.relation_bonus_map.get(relation_type, 0.75))
        hypothesis_score = float(primary_hypothesis.score) if primary_hypothesis is not None else 0.0
        positive_probability = self._estimate_positive_probability(action, session_state)

        positive_reward = (
            action.prior_score * relation_bonus * self.config.positive_reward_multiplier
            + hypothesis_score * 0.35
        )
        negative_reward = (
            action.prior_score * 0.25 * self.config.negative_reward_multiplier
            + hypothesis_score * 0.10
        )
        expected_reward = positive_probability * positive_reward + (1 - positive_probability) * negative_reward

        return SimulationOutcome(
            action_id=action.action_id,
            expected_reward=expected_reward,
            positive_branch_reward=positive_reward,
            negative_branch_reward=negative_reward,
            depth=2,
            metadata={
                "positive_probability": positive_probability,
                "relation_type": relation_type,
            },
        )

    # 根据动作类型、红旗程度和历史提问情况估算阳性回答概率。
    def _estimate_positive_probability(
        self,
        action: MctsAction,
        session_state: SessionState,
    ) -> float:
        probability = self.config.positive_branch_probability

        if bool(action.metadata.get("is_red_flag", False)):
            probability += 0.1

        if action.target_node_id in session_state.asked_node_ids:
            probability -= 0.15

        relation_type = str(action.metadata.get("relation_type", ""))

        if relation_type == "REQUIRES_DETAIL":
            probability -= 0.1
        elif relation_type in {"HAS_LAB_FINDING", "DIAGNOSED_BY"}:
            probability += 0.05

        return min(max(probability, 0.1), 0.9)
