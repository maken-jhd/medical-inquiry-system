"""负责为 rollout 回答分支计算可替换的 reward。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .response_transition_model import TransitionBranch
from .types import HypothesisCandidate, HypothesisScore, MctsAction, PatientContext, SessionState


@dataclass
class RewardEvaluation:
    """描述单个回答分支的一步 reward 及其拆解。"""

    reward: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RolloutRewardModelConfig:
    """保存启发式 rollout reward model 的默认参数。"""

    model_type: str = "heuristic_v2"
    relation_bonus_map: dict[str, float] | None = None
    information_gain_weight: float = 0.6
    hypothesis_alignment_weight: float = 0.35
    contradiction_signal_weight: float = 0.25
    turn_cost: float = 0.06
    repeat_penalty: float = 0.18
    high_cost_penalty: float = 0.12
    uncertainty_penalty: float = 0.1
    negative_resolution_penalty: float = 0.08
    context_match_bonus: float = 0.1
    risk_context_bonus: float = 0.05

    # 默认关系加成仍沿用 legacy heuristic 的基本分组。
    def __post_init__(self) -> None:
        if self.relation_bonus_map is None:
            self.relation_bonus_map = {
                "MANIFESTS_AS": 1.0,
                "HAS_LAB_FINDING": 1.15,
                "HAS_IMAGING_FINDING": 1.15,
                "HAS_PATHOGEN": 1.12,
                "DIAGNOSED_BY": 1.2,
                "REQUIRES_DETAIL": 0.8,
                "ASSOCIATED_WITH": 0.7,
            }


class RolloutRewardModel:
    """定义 rollout 单步 reward 估计的统一接口。"""

    def evaluate_branch(
        self,
        *,
        session_state: SessionState,
        action: MctsAction,
        branch: TransitionBranch,
        primary_hypothesis: HypothesisCandidate | HypothesisScore | None = None,
        candidate_hypotheses: Sequence[HypothesisScore] | None = None,
        patient_context: PatientContext | None = None,
        updated_rollout_state: SessionState | None = None,
    ) -> RewardEvaluation:
        raise NotImplementedError


class HeuristicRolloutRewardModel(RolloutRewardModel):
    """以可拆解接口重写旧的启发式 reward 计算。"""

    def __init__(self, config: RolloutRewardModelConfig | None = None) -> None:
        self.config = config or RolloutRewardModelConfig()

    # 先拆出信息增益、假设对齐、成本和不确定性，再合成为当前步 reward。
    def evaluate_branch(
        self,
        *,
        session_state: SessionState,
        action: MctsAction,
        branch: TransitionBranch,
        primary_hypothesis: HypothesisCandidate | HypothesisScore | None = None,
        candidate_hypotheses: Sequence[HypothesisScore] | None = None,
        patient_context: PatientContext | None = None,
        updated_rollout_state: SessionState | None = None,
    ) -> RewardEvaluation:
        _ = candidate_hypotheses, updated_rollout_state
        relation_type = str(action.metadata.get("relation_type", ""))
        relation_bonus = float(self.config.relation_bonus_map.get(relation_type, 0.75))
        contradiction_priority = float(action.metadata.get("contradiction_priority", 0.0) or 0.0)
        hypothesis_score = float(primary_hypothesis.score) if primary_hypothesis is not None else 0.0
        action_cost = str(action.metadata.get("evidence_cost") or "")
        question_type_hint = str(action.metadata.get("question_type_hint") or "")

        information_gain = action.prior_score * relation_bonus * self.config.information_gain_weight
        hypothesis_alignment = hypothesis_score * self.config.hypothesis_alignment_weight
        contradiction_signal = contradiction_priority * self.config.contradiction_signal_weight
        repeat_penalty = self.config.repeat_penalty if action.target_node_id in session_state.asked_node_ids else 0.0
        high_cost_penalty = self.config.high_cost_penalty if action_cost == "high" else 0.0
        turn_cost = self.config.turn_cost
        uncertainty_penalty = self.config.uncertainty_penalty if branch.polarity == "unclear" else 0.0
        negative_resolution_penalty = self.config.negative_resolution_penalty if branch.polarity == "absent" else 0.0
        context_bonus = self._estimate_context_bonus(action, patient_context)

        if branch.polarity == "present":
            branch_signal = information_gain + hypothesis_alignment + context_bonus
        elif branch.polarity == "absent":
            branch_signal = information_gain * 0.45 + contradiction_signal + hypothesis_alignment * 0.18
        else:
            branch_signal = information_gain * 0.22 + contradiction_signal * 0.4 + context_bonus * 0.35

        reward = max(
            branch_signal
            - repeat_penalty
            - high_cost_penalty
            - turn_cost
            - uncertainty_penalty
            - negative_resolution_penalty,
            0.0,
        )
        return RewardEvaluation(
            reward=reward,
            metadata={
                "branch_name": branch.branch_name,
                "branch_polarity": branch.polarity,
                "branch_resolution": branch.resolution,
                "information_gain_surrogate": round(information_gain, 4),
                "hypothesis_alignment": round(hypothesis_alignment, 4),
                "contradiction_signal": round(contradiction_signal, 4),
                "turn_cost": round(turn_cost, 4),
                "repeat_penalty": round(repeat_penalty, 4),
                "high_cost_penalty": round(high_cost_penalty, 4),
                "uncertainty_penalty": round(uncertainty_penalty, 4),
                "negative_resolution_penalty": round(negative_resolution_penalty, 4),
                "context_bonus": round(context_bonus, 4),
                "question_type_hint": question_type_hint,
                "relation_type": relation_type,
            },
        )

    # 把患者当前上下文的直接命中折成轻量 bonus，后续可平滑替换为 learned context scorer。
    def _estimate_context_bonus(
        self,
        action: MctsAction,
        patient_context: PatientContext | None,
    ) -> float:
        if patient_context is None:
            return 0.0

        raw_text = str(patient_context.raw_text or "")
        normalized_feature_names = {
            item.normalized_name
            for item in patient_context.clinical_features
        }
        target_name = action.target_node_name
        question_type_hint = str(action.metadata.get("question_type_hint", "symptom"))

        if target_name in raw_text or target_name in normalized_feature_names:
            return self.config.context_match_bonus

        if question_type_hint == "risk" and len(patient_context.general_info.epidemiology) > 0:
            return self.config.risk_context_bonus

        return 0.0
