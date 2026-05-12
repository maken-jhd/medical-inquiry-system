"""负责为 rollout 回答分支计算可替换的 reward。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from .response_transition_model import TransitionBranch
from .transition_statistics import HypothesisBeliefWeight, build_normalized_hypothesis_belief
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
    belief_top_k_hypotheses: int = 3
    enable_belief_margin_gain: bool = True
    enable_top3_separation_gain: bool = True
    enable_uncertainty_reduction: bool = True
    enable_competitor_elimination_bonus: bool = True
    enable_discriminative_support_bonus: bool = True
    enable_acceptance_risk_penalty: bool = True
    margin_gain_weight: float = 0.32
    top3_separation_weight: float = 0.24
    uncertainty_reduction_weight: float = 0.28
    competitor_elimination_weight: float = 0.26
    discriminative_support_weight: float = 0.18
    acceptance_risk_weight: float = 0.16
    branch_likelihood_floor: float = 0.05
    min_branch_support_count: float = 3.0
    low_value_high_cost_penalty_multiplier: float = 1.15
    detail_non_discriminative_penalty: float = 0.05

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


class BeliefAwareRolloutRewardModel(HeuristicRolloutRewardModel):
    """基于候选 belief 与分支后区分度变化估计 rollout reward。"""

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
        _ = updated_rollout_state
        relation_type = str(action.metadata.get("relation_type", ""))
        relation_bonus = float(self.config.relation_bonus_map.get(relation_type, 0.75))
        contradiction_priority = float(action.metadata.get("contradiction_priority", 0.0) or 0.0)
        action_cost = str(action.metadata.get("evidence_cost") or "")
        question_type_hint = str(action.metadata.get("question_type_hint") or "")
        prior_signal = min(max(float(action.prior_score), 0.0) / 3.0, 1.0)
        confidence_shift = self.estimate_branch_confidence_shift(
            session_state=session_state,
            action=action,
            branch=branch,
            primary_hypothesis=primary_hypothesis,
            candidate_hypotheses=candidate_hypotheses,
        )

        information_gain = (
            prior_signal
            * relation_bonus
            * (0.75 + max(float(branch.probability), 0.0) * 0.25)
            * self.config.information_gain_weight
        )
        margin_gain = (
            confidence_shift["margin_gain"] * self.config.margin_gain_weight
            if self.config.enable_belief_margin_gain
            else 0.0
        )
        top3_separation_gain = (
            confidence_shift["top3_separation_gain"] * self.config.top3_separation_weight
            if self.config.enable_top3_separation_gain
            else 0.0
        )
        uncertainty_reduction = (
            confidence_shift["uncertainty_reduction"] * self.config.uncertainty_reduction_weight
            if self.config.enable_uncertainty_reduction
            else 0.0
        )
        competitor_elimination_bonus = (
            confidence_shift["competitor_elimination_gain"] * self.config.competitor_elimination_weight
            if self.config.enable_competitor_elimination_bonus
            else 0.0
        )
        discriminative_support_bonus = (
            confidence_shift["discriminative_support_quality"] * self.config.discriminative_support_weight
            if self.config.enable_discriminative_support_bonus
            else 0.0
        )
        hypothesis_alignment = confidence_shift["hypothesis_alignment"] * self.config.hypothesis_alignment_weight
        contradiction_signal = contradiction_priority * self.config.contradiction_signal_weight
        if branch.polarity == "present":
            contradiction_signal *= 0.22
        elif branch.polarity == "unclear":
            contradiction_signal *= 0.45

        repeat_penalty = self.config.repeat_penalty if action.target_node_id in session_state.asked_node_ids else 0.0
        high_cost_penalty = self.config.high_cost_penalty if action_cost == "high" else 0.0
        if (
            action_cost == "high"
            and confidence_shift["margin_gain"] < 0.04
            and confidence_shift["uncertainty_reduction"] < 0.04
        ):
            high_cost_penalty *= self.config.low_value_high_cost_penalty_multiplier
        turn_cost = self.config.turn_cost
        uncertainty_penalty = self.config.uncertainty_penalty if branch.polarity == "unclear" else 0.0
        negative_resolution_penalty = self.config.negative_resolution_penalty if branch.polarity == "absent" else 0.0
        non_discriminative_detail_penalty = self._estimate_non_discriminative_detail_penalty(
            action=action,
            confidence_shift=confidence_shift,
        )
        context_bonus = self._estimate_context_bonus(action, patient_context)
        acceptance_risk_proxy = confidence_shift["acceptance_risk_proxy"]
        raw_acceptance_risk_penalty = (
            acceptance_risk_proxy * self.config.acceptance_risk_weight
            if self.config.enable_acceptance_risk_penalty
            else 0.0
        )
        acceptance_risk_discount = self._estimate_acceptance_risk_discount(confidence_shift)
        acceptance_risk_penalty = raw_acceptance_risk_penalty * (1.0 - acceptance_risk_discount)

        reward = max(
            information_gain
            + margin_gain
            + top3_separation_gain
            + uncertainty_reduction
            + competitor_elimination_bonus
            + discriminative_support_bonus
            + hypothesis_alignment
            + contradiction_signal
            + context_bonus
            - repeat_penalty
            - high_cost_penalty
            - turn_cost
            - uncertainty_penalty
            - negative_resolution_penalty
            - non_discriminative_detail_penalty
            - acceptance_risk_penalty,
            0.0,
        )
        return RewardEvaluation(
            reward=reward,
            metadata={
                "branch_name": branch.branch_name,
                "branch_polarity": branch.polarity,
                "branch_resolution": branch.resolution,
                "information_gain_surrogate": round(information_gain, 4),
                "belief_entropy_surrogate": round(confidence_shift["posterior_entropy"], 4),
                "prior_belief_entropy_surrogate": round(confidence_shift["prior_entropy"], 4),
                "uncertainty_reduction_surrogate": round(confidence_shift["uncertainty_reduction"], 4),
                "top1_top2_margin_surrogate": round(confidence_shift["posterior_margin"], 4),
                "prior_top1_top2_margin_surrogate": round(confidence_shift["prior_margin"], 4),
                "top1_top2_margin_gain_surrogate": round(confidence_shift["margin_gain"], 4),
                "top1_top3_separation_surrogate": round(confidence_shift["posterior_top3_separation"], 4),
                "prior_top1_top3_separation_surrogate": round(confidence_shift["prior_top3_separation"], 4),
                "top1_top3_separation_gain_surrogate": round(confidence_shift["top3_separation_gain"], 4),
                "competitor_elimination_surrogate": round(confidence_shift["competitor_elimination_gain"], 4),
                "competitor_coverage_surrogate": round(confidence_shift["competitor_coverage"], 4),
                "competitor_suppressed_ids": list(confidence_shift["suppressed_competitor_ids"]),
                "discriminative_support_quality": round(confidence_shift["discriminative_support_quality"], 4),
                "hypothesis_alignment": round(hypothesis_alignment, 4),
                "contradiction_signal": round(contradiction_signal, 4),
                "turn_cost": round(turn_cost, 4),
                "repeat_penalty": round(repeat_penalty, 4),
                "high_cost_penalty": round(high_cost_penalty, 4),
                "uncertainty_penalty": round(uncertainty_penalty, 4),
                "negative_resolution_penalty": round(negative_resolution_penalty, 4),
                "non_discriminative_detail_penalty": round(non_discriminative_detail_penalty, 4),
                "context_bonus": round(context_bonus, 4),
                "branch_probability": round(float(branch.probability), 4),
                "branch_support_quality": round(confidence_shift["branch_support_quality"], 4),
                "belief_margin_proxy": round(confidence_shift["posterior_margin"], 4),
                "acceptance_risk_proxy": round(confidence_shift["acceptance_risk_proxy"], 4),
                "raw_acceptance_risk_penalty": round(raw_acceptance_risk_penalty, 4),
                "acceptance_risk_discount": round(acceptance_risk_discount, 4),
                "acceptance_risk_penalty": round(acceptance_risk_penalty, 4),
                "posterior_top1_disease_id": confidence_shift["posterior_top1_disease_id"],
                "posterior_top1_weight": round(confidence_shift["posterior_top1_weight"], 4),
                "posterior_top2_weight": round(confidence_shift["posterior_top2_weight"], 4),
                "posterior_top3_weight": round(confidence_shift["posterior_top3_weight"], 4),
                "focus_disease_id": confidence_shift["focus_disease_id"],
                "focus_disease_prior_weight": round(confidence_shift["focus_prior_weight"], 4),
                "focus_disease_posterior_weight": round(confidence_shift["focus_posterior_weight"], 4),
                "question_type_hint": question_type_hint,
                "relation_type": relation_type,
                "reward_model_type": self.config.model_type,
            },
        )

    # 基于当前 belief 与 branch likelihood 估计一次轻量 posterior shift。
    def estimate_branch_confidence_shift(
        self,
        *,
        session_state: SessionState,
        action: MctsAction,
        branch: TransitionBranch,
        primary_hypothesis: HypothesisCandidate | HypothesisScore | None = None,
        candidate_hypotheses: Sequence[HypothesisScore] | None = None,
    ) -> dict[str, Any]:
        _ = session_state
        prior_belief = self._build_prior_belief(
            branch=branch,
            candidate_hypotheses=candidate_hypotheses,
            primary_hypothesis=primary_hypothesis,
            action=action,
        )
        prior_weights = {item.disease_id: float(item.weight) for item in prior_belief}
        posterior_weights = self._estimate_branch_posterior(
            prior_belief=prior_belief,
            action=action,
            branch=branch,
        )
        prior_entropy = _normalized_belief_entropy(prior_weights)
        posterior_entropy = _normalized_belief_entropy(posterior_weights)
        prior_margin, prior_top1_id, prior_top1_weight, prior_top2_weight = _belief_margin(prior_weights)
        posterior_margin, posterior_top1_id, posterior_top1_weight, posterior_top2_weight = _belief_margin(posterior_weights)
        prior_top3_separation, _, prior_top3_weight = _belief_topk_separation(prior_weights, top_k=3)
        posterior_top3_separation, _, posterior_top3_weight = _belief_topk_separation(
            posterior_weights,
            top_k=3,
        )
        focus_disease_id = self._resolve_focus_disease_id(
            prior_top1_id=prior_top1_id,
            primary_hypothesis=primary_hypothesis,
            action=action,
        )
        focus_prior_weight = float(prior_weights.get(focus_disease_id, 0.0))
        focus_posterior_weight = float(posterior_weights.get(focus_disease_id, 0.0))
        if branch.polarity == "present":
            hypothesis_alignment = max(focus_posterior_weight - focus_prior_weight, 0.0)
        elif branch.polarity == "absent":
            contradiction_priority = max(float(action.metadata.get("contradiction_priority", 0.0) or 0.0), 0.35)
            hypothesis_alignment = max(focus_prior_weight - focus_posterior_weight, 0.0) * contradiction_priority
        else:
            hypothesis_alignment = max(focus_posterior_weight - focus_prior_weight, 0.0) * 0.25

        branch_support_quality = self._estimate_branch_support_quality(branch)
        discriminative_support_quality = self._estimate_discriminative_support_quality(
            branch=branch,
            action=action,
            branch_support_quality=branch_support_quality,
        )
        competitor_elimination_gain, competitor_coverage, suppressed_competitor_ids = _competitor_elimination_gain(
            prior_weights=prior_weights,
            posterior_weights=posterior_weights,
            focus_disease_id=focus_disease_id,
        )
        top1_confidence_gain = max(posterior_top1_weight - prior_top1_weight, 0.0)
        answer_switch_risk = (
            0.08
            if posterior_top1_id != prior_top1_id and branch_support_quality < 0.55 and len(prior_weights) > 1
            else 0.0
        )
        ambiguity_risk = 0.12 if branch.branch_name in {"doubtful", "done_unclear", "not_done"} else 0.0
        low_margin_risk = max(0.1 - posterior_margin, 0.0)
        branch_rarity_risk = max(0.18 - float(branch.probability), 0.0) * 0.35
        overclaim_risk = top1_confidence_gain * max(1.0 - branch_support_quality, 0.0)
        acceptance_risk_proxy = min(
            max(
                ambiguity_risk
                + low_margin_risk
                + branch_rarity_risk
                + overclaim_risk
                + answer_switch_risk,
                0.0,
            ),
            1.0,
        )

        return {
            "prior_entropy": prior_entropy,
            "posterior_entropy": posterior_entropy,
            "uncertainty_reduction": prior_entropy - posterior_entropy,
            "prior_margin": prior_margin,
            "posterior_margin": posterior_margin,
            "margin_gain": posterior_margin - prior_margin,
            "prior_top3_separation": prior_top3_separation,
            "posterior_top3_separation": posterior_top3_separation,
            "top3_separation_gain": posterior_top3_separation - prior_top3_separation,
            "hypothesis_alignment": hypothesis_alignment,
            "focus_disease_id": focus_disease_id,
            "focus_prior_weight": focus_prior_weight,
            "focus_posterior_weight": focus_posterior_weight,
            "posterior_top1_disease_id": posterior_top1_id,
            "posterior_top1_weight": posterior_top1_weight,
            "posterior_top2_weight": posterior_top2_weight,
            "posterior_top3_weight": posterior_top3_weight,
            "branch_support_quality": branch_support_quality,
            "discriminative_support_quality": discriminative_support_quality,
            "competitor_elimination_gain": competitor_elimination_gain,
            "competitor_coverage": competitor_coverage,
            "suppressed_competitor_ids": suppressed_competitor_ids,
            "acceptance_risk_proxy": acceptance_risk_proxy,
        }

    def _build_prior_belief(
        self,
        *,
        branch: TransitionBranch,
        candidate_hypotheses: Sequence[HypothesisScore] | None,
        primary_hypothesis: HypothesisCandidate | HypothesisScore | None,
        action: MctsAction,
    ) -> list[HypothesisBeliefWeight]:
        branch_components = branch.metadata.get("belief_components", [])
        if isinstance(branch_components, list) and len(branch_components) > 0:
            belief: list[HypothesisBeliefWeight] = []
            for item in branch_components:
                if not isinstance(item, dict):
                    continue
                disease_id = str(item.get("disease_id") or "").strip()
                if len(disease_id) == 0:
                    continue
                belief.append(
                    HypothesisBeliefWeight(
                        disease_id=disease_id,
                        weight=max(float(item.get("weight", 0.0) or 0.0), 0.0),
                        raw_score=float(item.get("raw_score", 0.0) or 0.0),
                        name=str(item.get("name") or ""),
                        metadata=dict(item),
                    )
                )
            normalized = _normalize_belief_weights(belief)
            if len(normalized) > 0:
                return normalized

        effective_candidates: list[HypothesisScore | HypothesisCandidate] = list(candidate_hypotheses or [])
        if len(effective_candidates) == 0 and primary_hypothesis is not None:
            effective_candidates = [primary_hypothesis]
        belief = build_normalized_hypothesis_belief(
            effective_candidates,
            top_k=max(int(self.config.belief_top_k_hypotheses), 1),
        )
        if len(belief) > 0:
            return belief

        if primary_hypothesis is not None:
            return [
                HypothesisBeliefWeight(
                    disease_id=primary_hypothesis.node_id,
                    weight=1.0,
                    raw_score=float(primary_hypothesis.score),
                    name=primary_hypothesis.name,
                    metadata=dict(getattr(primary_hypothesis, "metadata", {}) or {}),
                )
            ]

        if action.hypothesis_id:
            return [
                HypothesisBeliefWeight(
                    disease_id=str(action.hypothesis_id),
                    weight=1.0,
                    raw_score=1.0,
                    name="",
                )
            ]

        return []

    def _estimate_branch_posterior(
        self,
        *,
        prior_belief: Sequence[HypothesisBeliefWeight],
        action: MctsAction,
        branch: TransitionBranch,
    ) -> dict[str, float]:
        posterior_weights: dict[str, float] = {}

        for belief_item in prior_belief:
            likelihood = self._resolve_branch_likelihood(
                belief_item=belief_item,
                action=action,
                branch=branch,
            )
            posterior_weights[belief_item.disease_id] = max(float(belief_item.weight), 0.0) * likelihood

        total = sum(posterior_weights.values())
        if total <= 0.0:
            return {item.disease_id: float(item.weight) for item in prior_belief}

        return {
            disease_id: weight / total
            for disease_id, weight in posterior_weights.items()
        }

    def _resolve_branch_likelihood(
        self,
        *,
        belief_item: HypothesisBeliefWeight,
        action: MctsAction,
        branch: TransitionBranch,
    ) -> float:
        component_metadata = dict(belief_item.metadata or {})
        branch_probability_key = {
            "positive": "present_probability",
            "negative": "absent_probability",
            "doubtful": "unclear_probability",
            "done_positive": "done_positive_probability",
            "done_negative": "done_negative_probability",
            "done_unclear": "done_unclear_probability",
            "not_done": "not_done_probability",
        }.get(branch.branch_name)
        if branch_probability_key is not None and branch_probability_key in component_metadata:
            return _clamp_probability(
                float(component_metadata.get(branch_probability_key, 0.0) or 0.0),
                self.config.branch_likelihood_floor,
            )

        return self._estimate_heuristic_branch_likelihood(
            belief_item=belief_item,
            action=action,
            branch=branch,
        )

    def _estimate_heuristic_branch_likelihood(
        self,
        *,
        belief_item: HypothesisBeliefWeight,
        action: MctsAction,
        branch: TransitionBranch,
    ) -> float:
        relation_type = str(action.metadata.get("relation_type", ""))
        contradiction_priority = float(action.metadata.get("contradiction_priority", 0.0) or 0.0)
        support_factor = self._estimate_candidate_support_factor(belief_item, action, relation_type)
        base_probability = max(float(branch.probability), self.config.branch_likelihood_floor)

        if branch.branch_name == "not_done":
            likelihood = 0.18 if support_factor >= 0.7 else 0.28
            return _clamp_probability(likelihood, self.config.branch_likelihood_floor)

        if branch.polarity == "present":
            likelihood = base_probability * (0.7 + support_factor * 0.45)
            return _clamp_probability(likelihood, self.config.branch_likelihood_floor)

        if branch.polarity == "absent":
            likelihood = (
                (1.0 - support_factor) * 0.45
                + max(contradiction_priority, 0.15) * 0.35
                + max(0.25 - base_probability, 0.0)
            )
            if support_factor >= 0.85:
                likelihood *= 0.65
            return _clamp_probability(likelihood, self.config.branch_likelihood_floor)

        likelihood = 0.22 + (1.0 - support_factor) * 0.18
        return _clamp_probability(likelihood, self.config.branch_likelihood_floor)

    def _estimate_candidate_support_factor(
        self,
        belief_item: HypothesisBeliefWeight,
        action: MctsAction,
        relation_type: str,
    ) -> float:
        metadata = dict(belief_item.metadata or {})
        evidence_node_ids = {
            str(item).strip()
            for item in metadata.get("evidence_node_ids", []) or []
            if len(str(item).strip()) > 0
        }
        relation_types = {
            str(item).strip()
            for item in metadata.get("relation_types", []) or []
            if len(str(item).strip()) > 0
        }
        if action.target_node_id in evidence_node_ids:
            return 0.95
        if len(relation_type) > 0 and relation_type in relation_types:
            return 0.78
        if str(action.hypothesis_id or "") == belief_item.disease_id:
            return 0.72
        return 0.42

    def _resolve_focus_disease_id(
        self,
        *,
        prior_top1_id: str,
        primary_hypothesis: HypothesisCandidate | HypothesisScore | None,
        action: MctsAction,
    ) -> str:
        if primary_hypothesis is not None and len(str(primary_hypothesis.node_id or "").strip()) > 0:
            return str(primary_hypothesis.node_id).strip()
        if len(str(action.hypothesis_id or "").strip()) > 0:
            return str(action.hypothesis_id).strip()
        return prior_top1_id

    def _estimate_branch_support_quality(self, branch: TransitionBranch) -> float:
        branch_components = branch.metadata.get("belief_components", [])
        if not isinstance(branch_components, list) or len(branch_components) == 0:
            source = str(branch.metadata.get("source") or "").strip()
            if source == "statistical":
                return 0.55
            if source == "heuristic_fallback":
                return 0.42
            return 0.48

        weighted_total = 0.0
        weighted_backoff_risk = 0.0
        total_weight = 0.0

        for item in branch_components:
            if not isinstance(item, dict):
                continue
            weight = max(float(item.get("weight", 0.0) or 0.0), 0.0)
            if weight <= 0.0:
                continue
            total_count = float(
                item.get("total_count", item.get("availability_total_count", 0.0)) or 0.0
            )
            backoff_level = str(item.get("backoff_level") or item.get("availability_backoff") or "")
            if len(backoff_level) == 0:
                backoff_level = str(item.get("result_backoff") or "")
            weighted_total += weight * max(total_count, 0.0)
            weighted_backoff_risk += weight * _backoff_risk(backoff_level)
            total_weight += weight

        if total_weight <= 0.0:
            return 0.48

        average_total = weighted_total / total_weight
        average_backoff_risk = weighted_backoff_risk / total_weight
        count_confidence = average_total / (average_total + max(self.config.min_branch_support_count, 1.0))
        quality = 0.55 * (1.0 - average_backoff_risk) + 0.45 * count_confidence
        return max(min(quality, 1.0), 0.0)

    def _estimate_discriminative_support_quality(
        self,
        *,
        branch: TransitionBranch,
        action: MctsAction,
        branch_support_quality: float,
    ) -> float:
        relation_type = str(action.metadata.get("relation_type", "") or "")
        question_type_hint = str(action.metadata.get("question_type_hint", "") or "")
        action_discriminative_gain = min(max(float(action.metadata.get("discriminative_gain", 0.0) or 0.0), 0.0), 1.6) / 1.6
        alternative_overlap = min(max(float(action.metadata.get("alternative_overlap", 0.0) or 0.0), 0.0), 1.0)
        relation_specificity = {
            "DIAGNOSED_BY": 1.0,
            "HAS_PATHOGEN": 0.98,
            "HAS_LAB_FINDING": 0.9,
            "HAS_IMAGING_FINDING": 0.88,
            "MANIFESTS_AS": 0.62,
            "RISK_FACTOR_FOR": 0.42,
            "APPLIES_TO": 0.36,
            "REQUIRES_DETAIL": 0.3,
        }.get(relation_type, 0.48)

        quality = branch_support_quality * (
            0.52
            + relation_specificity * 0.22
            + action_discriminative_gain * 0.18
            + (1.0 - alternative_overlap) * 0.08
        )
        if question_type_hint == "detail" and action_discriminative_gain < 0.28:
            quality *= 0.7
        elif question_type_hint == "risk" and relation_specificity < 0.5:
            quality *= 0.82
        if branch.branch_name in {"doubtful", "not_done", "done_unclear"}:
            quality *= 0.84
        return max(min(quality, 1.0), 0.0)

    def _estimate_non_discriminative_detail_penalty(
        self,
        *,
        action: MctsAction,
        confidence_shift: dict[str, Any],
    ) -> float:
        question_type_hint = str(action.metadata.get("question_type_hint", "") or "")
        if question_type_hint != "detail":
            return 0.0
        if confidence_shift["top3_separation_gain"] >= 0.035:
            return 0.0
        if confidence_shift["competitor_elimination_gain"] >= 0.03:
            return 0.0
        if confidence_shift["discriminative_support_quality"] >= 0.48:
            return 0.0
        return self.config.detail_non_discriminative_penalty

    def _estimate_acceptance_risk_discount(self, confidence_shift: dict[str, Any]) -> float:
        discriminative_pressure = (
            max(float(confidence_shift["margin_gain"]), 0.0) * 0.85
            + max(float(confidence_shift["top3_separation_gain"]), 0.0) * 0.9
            + max(float(confidence_shift["competitor_elimination_gain"]), 0.0) * 0.75
            + max(float(confidence_shift["discriminative_support_quality"]), 0.0) * 0.22
        )
        return max(min(discriminative_pressure, 0.55), 0.0)


def _normalize_belief_weights(belief: Sequence[HypothesisBeliefWeight]) -> list[HypothesisBeliefWeight]:
    if len(belief) == 0:
        return []

    total = sum(max(float(item.weight), 0.0) for item in belief)
    if total <= 0.0:
        uniform = 1.0 / len(belief)
        return [
            HypothesisBeliefWeight(
                disease_id=item.disease_id,
                weight=uniform,
                raw_score=item.raw_score,
                name=item.name,
                metadata=dict(item.metadata or {}),
            )
            for item in belief
        ]

    return [
        HypothesisBeliefWeight(
            disease_id=item.disease_id,
            weight=max(float(item.weight), 0.0) / total,
            raw_score=item.raw_score,
            name=item.name,
            metadata=dict(item.metadata or {}),
        )
        for item in belief
    ]


def _normalized_belief_entropy(weights: dict[str, float]) -> float:
    normalized_values = [max(float(value), 0.0) for value in weights.values() if float(value) > 0.0]
    if len(normalized_values) <= 1:
        return 0.0

    total = sum(normalized_values)
    if total <= 0.0:
        return 0.0

    normalized_values = [value / total for value in normalized_values]
    entropy = -sum(value * math.log(value) for value in normalized_values if value > 0.0)
    return max(min(entropy / math.log(len(normalized_values)), 1.0), 0.0)


def _belief_margin(weights: dict[str, float]) -> tuple[float, str, float, float]:
    if len(weights) == 0:
        return 0.0, "", 0.0, 0.0

    ranked = sorted(weights.items(), key=lambda item: (-float(item[1]), item[0]))
    top1_id, top1_weight = ranked[0]
    top2_weight = float(ranked[1][1]) if len(ranked) > 1 else 0.0
    return max(float(top1_weight) - top2_weight, 0.0), str(top1_id), float(top1_weight), top2_weight


def _belief_topk_separation(weights: dict[str, float], *, top_k: int) -> tuple[float, list[tuple[str, float]], float]:
    if len(weights) == 0:
        return 0.0, [], 0.0

    ranked = sorted(weights.items(), key=lambda item: (-float(item[1]), item[0]))[: max(top_k, 1)]
    if len(ranked) == 0:
        return 0.0, [], 0.0

    top1_weight = float(ranked[0][1])
    if len(ranked) == 1:
        return top1_weight, [(str(ranked[0][0]), top1_weight)], 0.0

    competitor_weights = [float(weight) for _, weight in ranked[1:]]
    topk_mean = sum(competitor_weights) / len(competitor_weights)
    topk_tail = competitor_weights[-1] if len(competitor_weights) > 0 else 0.0
    return max(top1_weight - topk_mean, 0.0), [(str(key), float(value)) for key, value in ranked], topk_tail


def _competitor_elimination_gain(
    *,
    prior_weights: dict[str, float],
    posterior_weights: dict[str, float],
    focus_disease_id: str,
) -> tuple[float, float, list[str]]:
    ranked_prior = [
        (str(disease_id), float(weight))
        for disease_id, weight in sorted(prior_weights.items(), key=lambda item: (-float(item[1]), item[0]))
        if str(disease_id) != str(focus_disease_id)
    ][:2]
    if len(ranked_prior) == 0:
        return 0.0, 0.0, []

    weighted_drop = 0.0
    total_reference = 0.0
    suppressed_ids: list[str] = []

    for index, (disease_id, prior_weight) in enumerate(ranked_prior):
        rank_weight = 1.0 if index == 0 else 0.72
        posterior_weight = float(posterior_weights.get(disease_id, 0.0))
        drop = max(prior_weight - posterior_weight, 0.0)
        weighted_drop += drop * rank_weight
        total_reference += max(prior_weight, 0.05) * rank_weight
        if drop >= 0.015:
            suppressed_ids.append(disease_id)

    if total_reference <= 0.0:
        return 0.0, 0.0, suppressed_ids

    gain = max(min(weighted_drop / total_reference, 1.0), 0.0)
    coverage = len(suppressed_ids) / len(ranked_prior)
    return gain, coverage, suppressed_ids


def _clamp_probability(value: float, floor: float) -> float:
    return min(max(float(value), max(float(floor), 1e-4)), 0.999)


def _backoff_risk(backoff_level: str) -> float:
    normalized = str(backoff_level or "").strip()
    return {
        "disease_family_question_type": 0.0,
        "disease_question_type": 0.03,
        "family_question_type": 0.06,
        "question_type": 0.1,
        "disease_exam_kind": 0.02,
        "disease_test_type": 0.02,
        "disease": 0.08,
        "exam_kind": 0.08,
        "test_type": 0.08,
        "global": 0.14,
    }.get(normalized, 0.1)
