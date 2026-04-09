"""实现轨迹聚合、多维评分与最终答案选择。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

from .llm_client import LlmClient
from .types import FinalAnswerScore, PatientContext, ReasoningTrajectory


@dataclass
class TrajectoryEvaluatorConfig:
    """保存轨迹聚合评分阶段的权重配置。"""

    consistency_weight: float = 0.3
    diversity_weight: float = 0.4
    agent_eval_weight: float = 0.3
    agent_eval_mode: str = "fallback"


class TrajectoryEvaluator:
    """按照最终答案对轨迹聚类并输出聚合评分。"""

    # 初始化轨迹评估器配置。
    def __init__(
        self,
        config: TrajectoryEvaluatorConfig | None = None,
        llm_client: LlmClient | None = None,
    ) -> None:
        self.config = config or TrajectoryEvaluatorConfig()
        self.llm_client = llm_client

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
    def score_groups(
        self,
        grouped: Dict[Tuple[str, str], List[ReasoningTrajectory]],
        patient_context: PatientContext | None = None,
    ) -> List[FinalAnswerScore]:
        total_trajectories = sum(len(items) for items in grouped.values())
        scores: List[FinalAnswerScore] = []

        for (answer_id, answer_name), trajectories in grouped.items():
            consistency = len(trajectories) / total_trajectories if total_trajectories > 0 else 0.0
            diversity = self._compute_diversity(trajectories)
            agent_evaluation, agent_metadata = self._compute_agent_evaluation(
                trajectories,
                answer_id=answer_id,
                answer_name=answer_name,
                patient_context=patient_context,
            )
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
                    metadata={"trajectory_count": len(trajectories), **agent_metadata},
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
        if len(trajectories) <= 1:
            return 0.0

        pairwise_scores: list[float] = []

        for index, left in enumerate(trajectories):
            for right in trajectories[index + 1 :]:
                pairwise_scores.append(1.0 - self._trajectory_similarity(left, right))

        if len(pairwise_scores) == 0:
            return 0.0

        return max(min(sum(pairwise_scores) / len(pairwise_scores), 1.0), 0.0)

    # 估计代理级整体评分，当前先使用轨迹平均得分。
    def _compute_agent_evaluation(
        self,
        trajectories: List[ReasoningTrajectory],
        answer_id: str,
        answer_name: str,
        patient_context: PatientContext | None = None,
    ) -> tuple[float, dict]:
        if len(trajectories) == 0:
            return 0.0, {"verifier_mode": "empty"}

        if self.config.agent_eval_mode == "llm_verifier":
            llm_result = self._compute_llm_agent_evaluation(
                trajectories,
                answer_id=answer_id,
                answer_name=answer_name,
                patient_context=patient_context,
            )

            if llm_result is not None:
                return llm_result["score"], {
                    "verifier_mode": "llm_verifier",
                    "verifier_should_accept": llm_result["should_accept_stop"],
                    "verifier_reasoning": llm_result["reasoning"],
                    "verifier_missing_evidence": llm_result["missing_evidence"],
                    "verifier_risk_flags": llm_result["risk_flags"],
                }

        if self.config.agent_eval_mode != "fallback":
            total_score = sum(item.score for item in trajectories)
            normalized = total_score / len(trajectories)
            return max(min(normalized, 1.0), 0.0), {"verifier_mode": self.config.agent_eval_mode}

        total_score = sum(item.score for item in trajectories)
        best_score = max(item.score for item in trajectories)
        terminal_ratio = (
            sum(1 for item in trajectories if bool(item.metadata.get("path_terminal", False))) / len(trajectories)
        )
        normalized = total_score / len(trajectories)
        normalized = normalized * 0.55 + best_score * 0.3 + terminal_ratio * 0.15
        return max(min(normalized, 1.0), 0.0), {"verifier_mode": "fallback"}

    # 使用可选的 LLM verifier 对某个答案组做一次代理级评审。
    def _compute_llm_agent_evaluation(
        self,
        trajectories: List[ReasoningTrajectory],
        answer_id: str,
        answer_name: str,
        patient_context: PatientContext | None = None,
    ) -> dict | None:
        if self.llm_client is None or not self.llm_client.is_available() or patient_context is None:
            return None

        best_trajectory = sorted(trajectories, key=lambda item: (-item.score, item.trajectory_id))[0]

        try:
            payload = self.llm_client.run_structured_prompt(
                "trajectory_agent_verifier",
                {
                    "patient_context": patient_context,
                    "answer_id": answer_id,
                    "answer_name": answer_name,
                    "best_trajectory": best_trajectory,
                    "trajectory_count": len(trajectories),
                },
                dict,
            )
        except Exception:
            return None

        try:
            score = float(payload.get("score", 0.0))
        except Exception:
            return None

        return {
            "score": max(min(score, 1.0), 0.0),
            "should_accept_stop": bool(payload.get("should_accept_stop", score >= 0.75)),
            "reasoning": str(payload.get("reasoning", "")),
            "missing_evidence": list(payload.get("missing_evidence", [])) if isinstance(payload.get("missing_evidence", []), list) else [],
            "risk_flags": list(payload.get("risk_flags", [])) if isinstance(payload.get("risk_flags", []), list) else [],
        }

    # 使用动作序列 Jaccard 估计两条轨迹的相似度。
    def _trajectory_similarity(self, left: ReasoningTrajectory, right: ReasoningTrajectory) -> float:
        left_actions = self._extract_action_sequence(left)
        right_actions = self._extract_action_sequence(right)

        if len(left_actions) == 0 and len(right_actions) == 0:
            return 1.0

        left_set = set(left_actions)
        right_set = set(right_actions)
        union_size = len(left_set | right_set)

        if union_size == 0:
            return 0.0

        return len(left_set & right_set) / union_size

    # 提取轨迹里的动作名序列，忽略纯路由类步骤。
    def _extract_action_sequence(self, trajectory: ReasoningTrajectory) -> list[str]:
        action_names: list[str] = []

        for step in trajectory.steps:
            name = str(step.get("action_name", step.get("target_node_name", ""))).strip()

            if len(name) == 0:
                continue

            action_names.append(name)

        return action_names
