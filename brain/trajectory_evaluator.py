"""实现轨迹聚合、多维评分与最终答案选择。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

from .types import FinalAnswerScore, ReasoningTrajectory


@dataclass
class TrajectoryEvaluatorConfig:
    """保存轨迹聚合评分阶段的权重配置。"""

    consistency_weight: float = 0.3
    diversity_weight: float = 0.4
    agent_eval_weight: float = 0.3


class TrajectoryEvaluator:
    """按照最终答案对轨迹聚类并输出聚合评分。"""

    # 初始化轨迹评估器配置。
    def __init__(self, config: TrajectoryEvaluatorConfig | None = None) -> None:
        self.config = config or TrajectoryEvaluatorConfig()

    # 按最终答案对轨迹进行分组。
    def group_by_answer(self, trajectories: Iterable[ReasoningTrajectory]) -> Dict[Tuple[str, str], List[ReasoningTrajectory]]:
        grouped: Dict[Tuple[str, str], List[ReasoningTrajectory]] = defaultdict(list)

        for trajectory in trajectories:
            key = (
                trajectory.final_answer_id or "UNKNOWN",
                trajectory.final_answer_name or "UNKNOWN",
            )
            grouped[key].append(trajectory)

        return dict(grouped)

    # 对每个答案分组计算一致性、多样性和代理评分。
    def score_groups(self, grouped: Dict[Tuple[str, str], List[ReasoningTrajectory]]) -> List[FinalAnswerScore]:
        total_trajectories = sum(len(items) for items in grouped.values())
        scores: List[FinalAnswerScore] = []

        for (answer_id, answer_name), trajectories in grouped.items():
            consistency = len(trajectories) / total_trajectories if total_trajectories > 0 else 0.0
            diversity = self._compute_diversity(trajectories)
            agent_evaluation = self._compute_agent_evaluation(trajectories)
            final_score = (
                consistency * self.config.consistency_weight
                + diversity * self.config.diversity_weight
                + agent_evaluation * self.config.agent_eval_weight
            )
            scores.append(
                FinalAnswerScore(
                    answer_id=answer_id,
                    answer_name=answer_name,
                    consistency=consistency,
                    diversity=diversity,
                    agent_evaluation=agent_evaluation,
                    final_score=final_score,
                    metadata={"trajectory_count": len(trajectories)},
                )
            )

        return sorted(scores, key=lambda item: (-item.final_score, item.answer_name))

    # 从已评分的答案分组中选出最终答案。
    def select_best_answer(self, scores: Iterable[FinalAnswerScore]) -> FinalAnswerScore | None:
        ranked = sorted(scores, key=lambda item: (-item.final_score, item.answer_name))

        if len(ranked) == 0:
            return None

        return ranked[0]

    # 估计同一答案下轨迹的多样性。
    def _compute_diversity(self, trajectories: List[ReasoningTrajectory]) -> float:
        unique_step_names = {
            str(step.get("action_name", step.get("target_node_name", "")))
            for trajectory in trajectories
            for step in trajectory.steps
            if len(str(step.get("action_name", step.get("target_node_name", "")))) > 0
        }

        if len(trajectories) == 0:
            return 0.0

        return min(len(unique_step_names) / max(len(trajectories), 1), 1.0)

    # 估计代理级整体评分，当前先使用轨迹平均得分。
    def _compute_agent_evaluation(self, trajectories: List[ReasoningTrajectory]) -> float:
        if len(trajectories) == 0:
            return 0.0

        total_score = sum(item.score for item in trajectories)
        normalized = total_score / len(trajectories)
        return max(min(normalized, 1.0), 0.0)
