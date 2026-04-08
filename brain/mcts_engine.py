"""负责基于 UCT 在候选动作中选择当前最优动作。"""

from __future__ import annotations

from dataclasses import dataclass
from math import log, sqrt
from typing import Iterable, Optional

from .types import MctsAction, SessionState, SimulationOutcome


@dataclass
class MctsConfig:
    """保存 UCT 选择阶段的核心超参数。"""

    exploration_constant: float = 1.4
    prior_weight: float = 0.35
    simulation_weight: float = 0.45
    unvisited_bonus: float = 0.2


class MctsEngine:
    """根据历史统计和 simulation 结果选择下一步动作。"""

    # 初始化 UCT 选择器配置。
    def __init__(self, config: MctsConfig | None = None) -> None:
        self.config = config or MctsConfig()

    # 构造当前状态的稳定签名，供访问统计与缓存复用。
    def build_state_signature(
        self,
        session_state: SessionState,
        hypothesis_id: Optional[str] = None,
    ) -> str:
        positive_slots = sorted(slot.node_id for slot in session_state.slots.values() if slot.status == "true")
        negative_slots = sorted(slot.node_id for slot in session_state.slots.values() if slot.status == "false")
        active_topics = sorted(session_state.active_topics)
        parts = [
            f"H={hypothesis_id or 'NONE'}",
            f"P={','.join(positive_slots[:8])}",
            f"N={','.join(negative_slots[:8])}",
            f"T={','.join(active_topics[:4])}",
        ]
        return "|".join(parts)

    # 按照 UCT 对候选动作打分并返回当前最优动作。
    def select_action(
        self,
        actions: Iterable[MctsAction],
        session_state: SessionState,
        simulation_outcomes: Iterable[SimulationOutcome] | None = None,
        state_signature: Optional[str] = None,
    ) -> Optional[MctsAction]:
        action_list = list(actions)

        if len(action_list) == 0:
            return None

        parent_signature = state_signature or self.build_state_signature(session_state)
        parent_visits = session_state.state_visit_stats.get(parent_signature)
        parent_visit_count = parent_visits.visit_count if parent_visits is not None else 0
        simulation_map = {
            outcome.action_id: outcome for outcome in (simulation_outcomes or [])
        }

        ranked = sorted(
            action_list,
            key=lambda action: (
                -self.score_action(action, session_state, parent_visit_count, simulation_map.get(action.action_id)),
                action.target_node_name,
            ),
        )
        return ranked[0]

    # 计算单个动作的 UCT 分数。
    def score_action(
        self,
        action: MctsAction,
        session_state: SessionState,
        parent_visit_count: int,
        simulation_outcome: SimulationOutcome | None = None,
    ) -> float:
        stats = session_state.action_stats.get(action.action_id)
        q_value = stats.average_value if stats is not None else 0.0
        visit_count = stats.visit_count if stats is not None else 0
        simulation_reward = simulation_outcome.expected_reward if simulation_outcome is not None else 0.0
        prior_score = action.prior_score * self.config.prior_weight

        blended_value = q_value + simulation_reward * self.config.simulation_weight + prior_score

        if visit_count == 0:
            exploration = self.config.exploration_constant * sqrt(log(parent_visit_count + 2))
            return blended_value + exploration + self.config.unvisited_bonus

        exploration = self.config.exploration_constant * sqrt(
            log(parent_visit_count + 2) / visit_count
        )
        return blended_value + exploration
