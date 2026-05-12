"""负责为 MCTS rollout 估计回答分支转移概率。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .transition_statistics import (
    ConditionalBranchDistribution,
    HypothesisBeliefWeight,
    TransitionActionContext,
    TransitionStatistics,
    build_normalized_hypothesis_belief,
    infer_action_context,
)
from .types import HypothesisCandidate, HypothesisScore, MctsAction, PatientContext, SessionState


@dataclass
class TransitionBranch:
    """描述一个候选回答分支及其条件概率。"""

    branch_name: str
    probability: float
    polarity: str
    resolution: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResponseTransitionModelConfig:
    """保存启发式 transition model 的基础超参数。"""

    model_type: str = "heuristic"
    base_positive_probability: float = 0.6
    base_doubtful_probability: float = 0.15
    red_flag_positive_bonus: float = 0.1
    asked_before_positive_penalty: float = 0.15
    detail_positive_penalty: float = 0.1
    strong_relation_positive_bonus: float = 0.05
    statistics_source_mode: str = "auto"
    statistics_top_k_hypotheses: int = 5
    enable_belief_mixture: bool = True
    fallback_to_heuristic: bool = True
    statistics_smoothing_alpha: float = 0.5
    statistics_min_total_count: int = 1
    graph_case_paths: tuple[str, ...] = ()
    replay_result_paths: tuple[str, ...] = ()
    evidence_catalog_paths: tuple[str, ...] = ()
    graph_case_glob: str = "test_outputs/simulator_cases/**/cases.jsonl"
    replay_result_glob: str = "test_outputs/simulator_replay/**/replay_results.jsonl"
    evidence_catalog_glob: str = "test_outputs/evidence_family/**/disease_evidence_family_catalog.json"


class ResponseTransitionModel:
    """定义 rollout 中条件化回答分支分布的统一接口。"""

    def predict_branches(
        self,
        action: MctsAction,
        session_state: SessionState,
        *,
        primary_hypothesis: HypothesisCandidate | HypothesisScore | None = None,
        candidate_hypotheses: Sequence[HypothesisScore] | None = None,
        patient_context: PatientContext | None = None,
    ) -> list[TransitionBranch]:
        raise NotImplementedError

    # 统一归一化，保证任何实现返回的都是合法概率分布。
    def _normalize_probabilities(self, branches: Sequence[TransitionBranch]) -> list[TransitionBranch]:
        total_probability = sum(max(float(item.probability), 0.0) for item in branches)

        if total_probability <= 0.0:
            uniform_probability = 1.0 / max(len(branches), 1)
            return [
                TransitionBranch(
                    branch_name=item.branch_name,
                    probability=uniform_probability,
                    polarity=item.polarity,
                    resolution=item.resolution,
                    metadata=dict(item.metadata),
                )
                for item in branches
            ]

        return [
            TransitionBranch(
                branch_name=item.branch_name,
                probability=max(float(item.probability), 0.0) / total_probability,
                polarity=item.polarity,
                resolution=item.resolution,
                metadata=dict(item.metadata),
            )
            for item in branches
        ]


class HeuristicResponseTransitionModel(ResponseTransitionModel):
    """复用当前启发式规则生成回答分支概率分布。"""

    def __init__(self, config: ResponseTransitionModelConfig | None = None) -> None:
        self.config = config or ResponseTransitionModelConfig()

    # 基于动作元数据、历史追问痕迹和关系类型估计三分支概率。
    def predict_branches(
        self,
        action: MctsAction,
        session_state: SessionState,
        *,
        primary_hypothesis: HypothesisCandidate | HypothesisScore | None = None,
        candidate_hypotheses: Sequence[HypothesisScore] | None = None,
        patient_context: PatientContext | None = None,
    ) -> list[TransitionBranch]:
        _ = primary_hypothesis, candidate_hypotheses, patient_context
        positive_probability = self._estimate_positive_probability(action, session_state)
        doubtful_probability = min(
            self.config.base_doubtful_probability,
            max(0.0, 1.0 - positive_probability),
        )
        negative_probability = max(0.05, 1.0 - positive_probability - doubtful_probability)

        branches = [
            TransitionBranch(
                branch_name="positive",
                probability=positive_probability,
                polarity="present",
                resolution="clear",
                metadata={"confidence_mode": "supportive"},
            ),
            TransitionBranch(
                branch_name="negative",
                probability=negative_probability,
                polarity="absent",
                resolution="clear",
                metadata={"confidence_mode": "contradictory"},
            ),
            TransitionBranch(
                branch_name="doubtful",
                probability=doubtful_probability,
                polarity="unclear",
                resolution="hedged",
                metadata={"confidence_mode": "uncertain"},
            ),
        ]
        return self._normalize_probabilities(branches)

    # 当前默认启发式仍保留“红旗更可能阳性、重复问法更不容易拿到新阳性”的判断。
    def _estimate_positive_probability(
        self,
        action: MctsAction,
        session_state: SessionState,
    ) -> float:
        probability = self.config.base_positive_probability

        if bool(action.metadata.get("is_red_flag", False)):
            probability += self.config.red_flag_positive_bonus

        if action.target_node_id in session_state.asked_node_ids:
            probability -= self.config.asked_before_positive_penalty

        relation_type = str(action.metadata.get("relation_type", ""))

        if relation_type == "REQUIRES_DETAIL":
            probability -= self.config.detail_positive_penalty
        elif relation_type in {"HAS_LAB_FINDING", "DIAGNOSED_BY"}:
            probability += self.config.strong_relation_positive_bonus

        return min(max(probability, 0.1), 0.9)

class StatisticalResponseTransitionModel(ResponseTransitionModel):
    """基于离线频数统计与 belief mixture 生成条件化回答分支。"""

    def __init__(
        self,
        config: ResponseTransitionModelConfig | None = None,
        *,
        statistics: TransitionStatistics | None = None,
        heuristic_fallback: HeuristicResponseTransitionModel | None = None,
    ) -> None:
        self.config = config or ResponseTransitionModelConfig(model_type="statistical")
        self.statistics = statistics or TransitionStatistics(
            smoothing_alpha=self.config.statistics_smoothing_alpha,
            min_total_count=self.config.statistics_min_total_count,
        )
        self.heuristic_fallback = heuristic_fallback or HeuristicResponseTransitionModel(self.config)

    def predict_branches(
        self,
        action: MctsAction,
        session_state: SessionState,
        *,
        primary_hypothesis: HypothesisCandidate | HypothesisScore | None = None,
        candidate_hypotheses: Sequence[HypothesisScore] | None = None,
        patient_context: PatientContext | None = None,
    ) -> list[TransitionBranch]:
        _ = patient_context
        belief = self._build_belief(candidate_hypotheses, primary_hypothesis, action)
        default_disease_id = belief[0].disease_id if len(belief) > 0 else str(action.hypothesis_id or "")
        action_context = infer_action_context(
            action,
            statistics=self.statistics,
            disease_id=default_disease_id,
        )

        if action_context.branch_schema == "exam_context":
            if not self.statistics.has_exam_statistics() and self.config.fallback_to_heuristic:
                return self._predict_with_heuristic_fallback(
                    action,
                    session_state,
                    primary_hypothesis=primary_hypothesis,
                    candidate_hypotheses=candidate_hypotheses,
                    reason="exam_statistics_unavailable",
                )
            branches = self._predict_exam_branches(action, action_context, belief)
        else:
            if not self.statistics.has_verify_statistics() and self.config.fallback_to_heuristic:
                return self._predict_with_heuristic_fallback(
                    action,
                    session_state,
                    primary_hypothesis=primary_hypothesis,
                    candidate_hypotheses=candidate_hypotheses,
                    reason="verify_statistics_unavailable",
                )
            branches = self._predict_verify_branches(action, action_context, belief)

        if len(branches) == 0 and self.config.fallback_to_heuristic:
            return self._predict_with_heuristic_fallback(
                action,
                session_state,
                primary_hypothesis=primary_hypothesis,
                candidate_hypotheses=candidate_hypotheses,
                reason="empty_statistical_distribution",
            )
        return self._normalize_probabilities(branches)

    def _build_belief(
        self,
        candidate_hypotheses: Sequence[HypothesisScore] | None,
        primary_hypothesis: HypothesisCandidate | HypothesisScore | None,
        action: MctsAction,
    ) -> list[HypothesisBeliefWeight]:
        if self.config.enable_belief_mixture:
            effective_candidates: list[HypothesisScore | HypothesisCandidate] = list(candidate_hypotheses or [])
            if len(effective_candidates) == 0 and primary_hypothesis is not None:
                effective_candidates = [primary_hypothesis]
            belief = build_normalized_hypothesis_belief(
                effective_candidates,
                top_k=max(int(self.config.statistics_top_k_hypotheses), 1),
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

    def _predict_verify_branches(
        self,
        action: MctsAction,
        action_context: TransitionActionContext,
        belief: Sequence[HypothesisBeliefWeight],
    ) -> list[TransitionBranch]:
        probability_accumulator = {
            "positive": 0.0,
            "negative": 0.0,
            "doubtful": 0.0,
        }
        mixture_components: list[dict[str, Any]] = []
        effective_belief = list(belief) or [HypothesisBeliefWeight(disease_id="", weight=1.0, raw_score=0.0)]

        for belief_item in effective_belief:
            disease_context = infer_action_context(
                action,
                statistics=self.statistics,
                disease_id=belief_item.disease_id,
            )
            distribution, selected_family = self._select_verify_distribution_for_context(
                disease_id=belief_item.disease_id,
                action_context=disease_context,
            )
            probability_accumulator["positive"] += belief_item.weight * distribution.probabilities["present"]
            probability_accumulator["negative"] += belief_item.weight * distribution.probabilities["absent"]
            probability_accumulator["doubtful"] += belief_item.weight * distribution.probabilities["unclear"]
            mixture_components.append(
                self._build_component_metadata(
                    belief_item=belief_item,
                    distribution=distribution,
                    action_context=disease_context,
                    selected_family=selected_family,
                )
            )

        return [
            TransitionBranch(
                branch_name="positive",
                probability=probability_accumulator["positive"],
                polarity="present",
                resolution="clear",
                metadata={
                    "source": "statistical",
                    "branch_schema": "verify",
                    "question_type": action_context.question_type,
                    "evidence_family": action_context.evidence_family,
                    "evidence_families": list(action_context.evidence_families),
                    "belief_components": mixture_components,
                },
            ),
            TransitionBranch(
                branch_name="negative",
                probability=probability_accumulator["negative"],
                polarity="absent",
                resolution="clear",
                metadata={
                    "source": "statistical",
                    "branch_schema": "verify",
                    "question_type": action_context.question_type,
                    "evidence_family": action_context.evidence_family,
                    "evidence_families": list(action_context.evidence_families),
                    "belief_components": mixture_components,
                },
            ),
            TransitionBranch(
                branch_name="doubtful",
                probability=probability_accumulator["doubtful"],
                polarity="unclear",
                resolution="hedged",
                metadata={
                    "source": "statistical",
                    "branch_schema": "verify",
                    "question_type": action_context.question_type,
                    "evidence_family": action_context.evidence_family,
                    "evidence_families": list(action_context.evidence_families),
                    "belief_components": mixture_components,
                },
            ),
        ]

    # 某些动作同时带多个 family tag，这里优先选“更具体、统计量更扎实”的 verify 分布，而不是只吃第一个 family。
    def _select_verify_distribution_for_context(
        self,
        *,
        disease_id: str,
        action_context: TransitionActionContext,
    ) -> tuple[ConditionalBranchDistribution, str]:
        candidate_families = list(action_context.evidence_families or ())
        if len(candidate_families) == 0:
            candidate_families = [action_context.evidence_family]

        candidates: list[tuple[str, ConditionalBranchDistribution]] = []
        for family in candidate_families:
            normalized_family = str(family or "").strip()
            if len(normalized_family) == 0:
                continue
            candidates.append(
                (
                    normalized_family,
                    self.statistics.query_verify_distribution(
                        disease_id=disease_id,
                        evidence_family=normalized_family,
                        question_type=action_context.question_type,
                    ),
                )
            )

        if len(candidates) == 0:
            fallback_family = str(action_context.evidence_family or "").strip()
            fallback_distribution = self.statistics.query_verify_distribution(
                disease_id=disease_id,
                evidence_family=fallback_family,
                question_type=action_context.question_type,
            )
            return fallback_distribution, fallback_family

        best_family, best_distribution = sorted(
            candidates,
            key=lambda item: (
                _verify_backoff_rank(item[1].backoff_level),
                -float(item[1].total_count),
                item[0],
            ),
        )[0]
        return best_distribution, best_family

    def _predict_exam_branches(
        self,
        action: MctsAction,
        action_context: TransitionActionContext,
        belief: Sequence[HypothesisBeliefWeight],
    ) -> list[TransitionBranch]:
        probability_accumulator = {
            "done_positive": 0.0,
            "done_negative": 0.0,
            "done_unclear": 0.0,
            "not_done": 0.0,
        }
        mixture_components: list[dict[str, Any]] = []
        effective_belief = list(belief) or [HypothesisBeliefWeight(disease_id="", weight=1.0, raw_score=0.0)]

        for belief_item in effective_belief:
            availability_distribution = self.statistics.query_exam_availability_distribution(
                disease_id=belief_item.disease_id,
                exam_kind=action_context.exam_kind or "general",
            )
            result_distribution = self._mix_exam_result_distribution(
                action=action,
                action_context=action_context,
                belief_item=belief_item,
            )
            done_probability = availability_distribution.probabilities["done"]
            not_done_probability = availability_distribution.probabilities["not_done"]
            unclear_availability_probability = availability_distribution.probabilities["unclear"]

            probability_accumulator["done_positive"] += (
                belief_item.weight * done_probability * result_distribution.probabilities["positive"]
            )
            probability_accumulator["done_negative"] += (
                belief_item.weight * done_probability * result_distribution.probabilities["negative"]
            )
            probability_accumulator["done_unclear"] += belief_item.weight * (
                done_probability * result_distribution.probabilities["unclear"] + unclear_availability_probability
            )
            probability_accumulator["not_done"] += belief_item.weight * not_done_probability
            mixture_components.append(
                {
                    "disease_id": belief_item.disease_id,
                    "weight": round(float(belief_item.weight), 4),
                    "raw_score": round(float(belief_item.raw_score), 4),
                    "availability_backoff": availability_distribution.backoff_level,
                    "availability_total_count": round(float(availability_distribution.total_count), 4),
                    "result_backoff": result_distribution.backoff_level,
                    "result_total_count": round(float(result_distribution.total_count), 4),
                    "exam_kind": action_context.exam_kind or "general",
                    "test_type": action_context.test_type or action_context.exam_kind or "general",
                    "done_probability": round(float(done_probability), 6),
                    "not_done_probability": round(float(not_done_probability), 6),
                    "availability_unclear_probability": round(float(unclear_availability_probability), 6),
                    "result_positive_probability": round(float(result_distribution.probabilities["positive"]), 6),
                    "result_negative_probability": round(float(result_distribution.probabilities["negative"]), 6),
                    "result_unclear_probability": round(float(result_distribution.probabilities["unclear"]), 6),
                    "done_positive_probability": round(
                        float(done_probability * result_distribution.probabilities["positive"]),
                        6,
                    ),
                    "done_negative_probability": round(
                        float(done_probability * result_distribution.probabilities["negative"]),
                        6,
                    ),
                    "done_unclear_probability": round(
                        float(
                            done_probability * result_distribution.probabilities["unclear"]
                            + unclear_availability_probability
                        ),
                        6,
                    ),
                }
            )

        branch_metadata = {
            "source": "statistical",
            "branch_schema": "exam_context",
            "question_type": action_context.question_type,
            "exam_kind": action_context.exam_kind or "general",
            "test_type": action_context.test_type or action_context.exam_kind or "general",
            "mapping_rule": (
                "先估计 done/not_done；若 done，再用结果分布映射到 done_positive/done_negative/done_unclear。"
            ),
            "belief_components": mixture_components,
        }
        return [
            TransitionBranch(
                branch_name="done_positive",
                probability=probability_accumulator["done_positive"],
                polarity="present",
                resolution="clear",
                metadata=dict(branch_metadata),
            ),
            TransitionBranch(
                branch_name="done_negative",
                probability=probability_accumulator["done_negative"],
                polarity="absent",
                resolution="clear",
                metadata=dict(branch_metadata),
            ),
            TransitionBranch(
                branch_name="done_unclear",
                probability=probability_accumulator["done_unclear"],
                polarity="unclear",
                resolution="hedged",
                metadata=dict(branch_metadata),
            ),
            TransitionBranch(
                branch_name="not_done",
                probability=probability_accumulator["not_done"],
                polarity="unclear",
                resolution="hedged",
                metadata=dict(branch_metadata),
            ),
        ]

    def _mix_exam_result_distribution(
        self,
        *,
        action: MctsAction,
        action_context: TransitionActionContext,
        belief_item: HypothesisBeliefWeight,
    ) -> ConditionalBranchDistribution:
        test_type_weights = self._build_exam_test_type_weights(action, action_context)
        if len(test_type_weights) == 0:
            return self.statistics.query_exam_result_distribution(
                disease_id=belief_item.disease_id,
                test_type=action_context.test_type or action_context.exam_kind or "general",
            )

        mixed_probability = {
            "positive": 0.0,
            "negative": 0.0,
            "unclear": 0.0,
        }
        dominant_distribution: ConditionalBranchDistribution | None = None
        dominant_weight = -1.0

        for test_type, weight in test_type_weights:
            distribution = self.statistics.query_exam_result_distribution(
                disease_id=belief_item.disease_id,
                test_type=test_type,
            )
            mixed_probability["positive"] += weight * distribution.probabilities["positive"]
            mixed_probability["negative"] += weight * distribution.probabilities["negative"]
            mixed_probability["unclear"] += weight * distribution.probabilities["unclear"]
            if weight > dominant_weight:
                dominant_distribution = distribution
                dominant_weight = weight

        return ConditionalBranchDistribution(
            probabilities=mixed_probability,
            total_count=float(
                sum(
                    distribution_weight
                    * self.statistics.query_exam_result_distribution(
                        disease_id=belief_item.disease_id,
                        test_type=test_type,
                    ).total_count
                    for test_type, distribution_weight in test_type_weights
                )
            ),
            backoff_level=dominant_distribution.backoff_level if dominant_distribution is not None else "global",
            source_key=dominant_distribution.source_key if dominant_distribution is not None else ("global",),
            metadata={
                "mixed_test_types": [
                    {"test_type": test_type, "weight": round(float(weight), 4)}
                    for test_type, weight in test_type_weights
                ]
            },
        )

    # general exam_context 可能同时替多类高成本证据探路，这里按候选 evidence priority 粗粒度加权。
    def _build_exam_test_type_weights(
        self,
        action: MctsAction,
        action_context: TransitionActionContext,
    ) -> list[tuple[str, float]]:
        if action_context.test_type not in {None, "", "general"}:
            return [(str(action_context.test_type), 1.0)]

        metadata = dict(action.metadata or {})
        candidate_evidence = metadata.get("exam_candidate_evidence") or []
        raw_weights: dict[str, float] = {}
        if isinstance(candidate_evidence, list):
            for item in candidate_evidence:
                if not isinstance(item, dict):
                    continue
                candidate_type = str(
                    item.get("exam_kind")
                    or item.get("question_type_hint")
                    or ""
                ).strip()
                if candidate_type not in {"lab", "imaging", "pathogen"}:
                    continue
                raw_weights[candidate_type] = raw_weights.get(candidate_type, 0.0) + max(
                    float(item.get("priority", 0.0) or 0.0),
                    0.1,
                )
        if len(raw_weights) == 0:
            candidate_exam_kinds = metadata.get("candidate_exam_kinds") or []
            if isinstance(candidate_exam_kinds, list):
                for item in candidate_exam_kinds:
                    candidate_type = str(item or "").strip()
                    if candidate_type in {"lab", "imaging", "pathogen"}:
                        raw_weights[candidate_type] = 1.0

        if len(raw_weights) == 0:
            fallback_type = action_context.exam_kind or action_context.test_type or "general"
            return [(fallback_type, 1.0)]

        total_weight = sum(raw_weights.values())
        if total_weight <= 0.0:
            uniform = 1.0 / len(raw_weights)
            return [(test_type, uniform) for test_type in sorted(raw_weights)]
        return [
            (test_type, weight / total_weight)
            for test_type, weight in sorted(raw_weights.items())
        ]

    def _build_component_metadata(
        self,
        *,
        belief_item: HypothesisBeliefWeight,
        distribution: ConditionalBranchDistribution,
        action_context: TransitionActionContext,
        selected_family: str,
    ) -> dict[str, Any]:
        return {
            "disease_id": belief_item.disease_id,
            "weight": round(float(belief_item.weight), 4),
            "raw_score": round(float(belief_item.raw_score), 4),
            "question_type": action_context.question_type,
            "evidence_family": selected_family,
            "candidate_evidence_families": list(action_context.evidence_families),
            "backoff_level": distribution.backoff_level,
            "source_key": list(distribution.source_key),
            "total_count": round(float(distribution.total_count), 4),
            "present_probability": round(float(distribution.probabilities["present"]), 6),
            "absent_probability": round(float(distribution.probabilities["absent"]), 6),
            "unclear_probability": round(float(distribution.probabilities["unclear"]), 6),
        }

    def _predict_with_heuristic_fallback(
        self,
        action: MctsAction,
        session_state: SessionState,
        *,
        primary_hypothesis: HypothesisCandidate | HypothesisScore | None,
        candidate_hypotheses: Sequence[HypothesisScore] | None,
        reason: str,
    ) -> list[TransitionBranch]:
        fallback_branches = self.heuristic_fallback.predict_branches(
            action,
            session_state,
            primary_hypothesis=primary_hypothesis,
            candidate_hypotheses=candidate_hypotheses,
        )
        return [
            TransitionBranch(
                branch_name=branch.branch_name,
                probability=branch.probability,
                polarity=branch.polarity,
                resolution=branch.resolution,
                metadata={
                    **dict(branch.metadata),
                    "source": "heuristic_fallback",
                    "fallback_reason": reason,
                    "fallback_from": "statistical",
                },
            )
            for branch in fallback_branches
        ]


class LearnedResponseTransitionModel(ResponseTransitionModel):
    """为后续学习化 transition model 预留的占位接口。"""

    def predict_branches(
        self,
        action: MctsAction,
        session_state: SessionState,
        *,
        primary_hypothesis: HypothesisCandidate | HypothesisScore | None = None,
        candidate_hypotheses: Sequence[HypothesisScore] | None = None,
        patient_context: PatientContext | None = None,
    ) -> list[TransitionBranch]:
        _ = action, session_state, primary_hypothesis, candidate_hypotheses, patient_context
        raise NotImplementedError(
            "LearnedResponseTransitionModel 目前只保留接口占位；当前版本未接入训练与推理流程。"
        )


def _verify_backoff_rank(level: str) -> int:
    return {
        "disease_family_question_type": 0,
        "disease_question_type": 1,
        "family_question_type": 2,
        "question_type": 3,
        "global": 4,
    }.get(str(level or "").strip(), 5)
