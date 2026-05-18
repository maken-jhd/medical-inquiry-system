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
from ..state import HypothesisCandidate, HypothesisScore, MctsAction, PatientContext, SessionState


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
    statistics_min_total_count: int = 3
    graph_case_paths: tuple[str, ...] = ()
    replay_result_paths: tuple[str, ...] = ()
    evidence_catalog_paths: tuple[str, ...] = ()
    graph_case_glob: str = "test_outputs/simulator_cases/**/cases.jsonl"
    replay_result_glob: str = "test_outputs/simulator_replay/**/replay_results.jsonl"
    evidence_catalog_glob: str = "test_outputs/evidence_family/**/disease_evidence_family_catalog.json"
    hybrid_enable_count_aware_mixing: bool = True
    hybrid_count_threshold_low: float = 3.0
    hybrid_count_threshold_mid: float = 8.0
    hybrid_count_threshold_high: float = 20.0
    hybrid_lambda_low: float = 0.25
    hybrid_lambda_mid: float = 0.5
    hybrid_lambda_high: float = 0.8
    backoff_discount_disease_family_question_type: float = 1.0
    backoff_discount_disease_question_type: float = 0.85
    backoff_discount_family_question_type: float = 0.65
    backoff_discount_question_type: float = 0.4
    backoff_discount_global: float = 0.15


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
            branches = self._predict_exam_branches(
                action,
                session_state,
                action_context,
                belief,
                primary_hypothesis=primary_hypothesis,
                candidate_hypotheses=candidate_hypotheses,
            )
        else:
            if not self.statistics.has_verify_statistics() and self.config.fallback_to_heuristic:
                return self._predict_with_heuristic_fallback(
                    action,
                    session_state,
                    primary_hypothesis=primary_hypothesis,
                    candidate_hypotheses=candidate_hypotheses,
                    reason="verify_statistics_unavailable",
                )
            branches = self._predict_verify_branches(
                action,
                session_state,
                action_context,
                belief,
                primary_hypothesis=primary_hypothesis,
                candidate_hypotheses=candidate_hypotheses,
            )

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
        session_state: SessionState,
        action_context: TransitionActionContext,
        belief: Sequence[HypothesisBeliefWeight],
        *,
        primary_hypothesis: HypothesisCandidate | HypothesisScore | None,
        candidate_hypotheses: Sequence[HypothesisScore] | None,
    ) -> list[TransitionBranch]:
        statistical_probability_map = {
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
            statistical_probability_map["positive"] += belief_item.weight * distribution.probabilities["present"]
            statistical_probability_map["negative"] += belief_item.weight * distribution.probabilities["absent"]
            statistical_probability_map["doubtful"] += belief_item.weight * distribution.probabilities["unclear"]
            mixture_components.append(
                self._build_component_metadata(
                    belief_item=belief_item,
                    distribution=distribution,
                    action_context=disease_context,
                    selected_family=selected_family,
                )
            )

        heuristic_probability_map = self._predict_heuristic_probability_map(
            action,
            session_state,
            action_context=action_context,
            primary_hypothesis=primary_hypothesis,
            candidate_hypotheses=candidate_hypotheses,
        )
        hybrid_summary = self._build_hybrid_transition_summary(
            mixture_components=mixture_components,
            statistical_probability_map=statistical_probability_map,
            heuristic_probability_map=heuristic_probability_map,
        )
        final_probability_map = self._mix_probability_maps(
            statistical_probability_map,
            heuristic_probability_map,
            hybrid_summary["hybrid_transition_lambda"],
        )
        branch_metadata = {
            "source": hybrid_summary["source"],
            "branch_schema": "verify",
            "question_type": action_context.question_type,
            "evidence_family": action_context.evidence_family,
            "evidence_families": list(action_context.evidence_families),
            "belief_components": mixture_components,
            **hybrid_summary,
        }
        return [
            TransitionBranch(
                branch_name="positive",
                probability=final_probability_map["positive"],
                polarity="present",
                resolution="clear",
                metadata=self._attach_branch_probability_metadata(
                    branch_name="positive",
                    metadata=branch_metadata,
                    statistical_probability_map=statistical_probability_map,
                    heuristic_probability_map=heuristic_probability_map,
                    final_probability_map=final_probability_map,
                ),
            ),
            TransitionBranch(
                branch_name="negative",
                probability=final_probability_map["negative"],
                polarity="absent",
                resolution="clear",
                metadata=self._attach_branch_probability_metadata(
                    branch_name="negative",
                    metadata=branch_metadata,
                    statistical_probability_map=statistical_probability_map,
                    heuristic_probability_map=heuristic_probability_map,
                    final_probability_map=final_probability_map,
                ),
            ),
            TransitionBranch(
                branch_name="doubtful",
                probability=final_probability_map["doubtful"],
                polarity="unclear",
                resolution="hedged",
                metadata=self._attach_branch_probability_metadata(
                    branch_name="doubtful",
                    metadata=branch_metadata,
                    statistical_probability_map=statistical_probability_map,
                    heuristic_probability_map=heuristic_probability_map,
                    final_probability_map=final_probability_map,
                ),
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
        session_state: SessionState,
        action_context: TransitionActionContext,
        belief: Sequence[HypothesisBeliefWeight],
        *,
        primary_hypothesis: HypothesisCandidate | HypothesisScore | None,
        candidate_hypotheses: Sequence[HypothesisScore] | None,
    ) -> list[TransitionBranch]:
        statistical_probability_map = {
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
            hybrid_backoff_level = _coarser_backoff_level(
                availability_distribution.backoff_level,
                result_distribution.backoff_level,
            )
            hybrid_total_count = min(
                float(availability_distribution.total_count),
                float(result_distribution.total_count),
            )
            confidence_profile = self._estimate_statistical_confidence(
                total_count=hybrid_total_count,
                backoff_level=hybrid_backoff_level,
            )
            done_probability = availability_distribution.probabilities["done"]
            not_done_probability = availability_distribution.probabilities["not_done"]
            unclear_availability_probability = availability_distribution.probabilities["unclear"]

            statistical_probability_map["done_positive"] += (
                belief_item.weight * done_probability * result_distribution.probabilities["positive"]
            )
            statistical_probability_map["done_negative"] += (
                belief_item.weight * done_probability * result_distribution.probabilities["negative"]
            )
            statistical_probability_map["done_unclear"] += belief_item.weight * (
                done_probability * result_distribution.probabilities["unclear"] + unclear_availability_probability
            )
            statistical_probability_map["not_done"] += belief_item.weight * not_done_probability
            mixture_components.append(
                {
                    "disease_id": belief_item.disease_id,
                    "weight": round(float(belief_item.weight), 4),
                    "raw_score": round(float(belief_item.raw_score), 4),
                    "backoff_level": hybrid_backoff_level,
                    "total_count": round(float(hybrid_total_count), 4),
                    "statistical_count_lambda": round(float(confidence_profile["count_lambda"]), 4),
                    "statistical_backoff_discount": round(float(confidence_profile["backoff_discount"]), 4),
                    "statistical_confidence": round(float(confidence_profile["confidence"]), 4),
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

        heuristic_probability_map = self._predict_heuristic_probability_map(
            action,
            session_state,
            action_context=action_context,
            primary_hypothesis=primary_hypothesis,
            candidate_hypotheses=candidate_hypotheses,
        )
        hybrid_summary = self._build_hybrid_transition_summary(
            mixture_components=mixture_components,
            statistical_probability_map=statistical_probability_map,
            heuristic_probability_map=heuristic_probability_map,
        )
        final_probability_map = self._mix_probability_maps(
            statistical_probability_map,
            heuristic_probability_map,
            hybrid_summary["hybrid_transition_lambda"],
        )
        branch_metadata = {
            "source": hybrid_summary["source"],
            "branch_schema": "exam_context",
            "question_type": action_context.question_type,
            "exam_kind": action_context.exam_kind or "general",
            "test_type": action_context.test_type or action_context.exam_kind or "general",
            "mapping_rule": (
                "先估计 done/not_done；若 done，再用结果分布映射到 done_positive/done_negative/done_unclear。"
            ),
            "belief_components": mixture_components,
            **hybrid_summary,
        }
        return [
            TransitionBranch(
                branch_name="done_positive",
                probability=final_probability_map["done_positive"],
                polarity="present",
                resolution="clear",
                metadata=self._attach_branch_probability_metadata(
                    branch_name="done_positive",
                    metadata=branch_metadata,
                    statistical_probability_map=statistical_probability_map,
                    heuristic_probability_map=heuristic_probability_map,
                    final_probability_map=final_probability_map,
                ),
            ),
            TransitionBranch(
                branch_name="done_negative",
                probability=final_probability_map["done_negative"],
                polarity="absent",
                resolution="clear",
                metadata=self._attach_branch_probability_metadata(
                    branch_name="done_negative",
                    metadata=branch_metadata,
                    statistical_probability_map=statistical_probability_map,
                    heuristic_probability_map=heuristic_probability_map,
                    final_probability_map=final_probability_map,
                ),
            ),
            TransitionBranch(
                branch_name="done_unclear",
                probability=final_probability_map["done_unclear"],
                polarity="unclear",
                resolution="hedged",
                metadata=self._attach_branch_probability_metadata(
                    branch_name="done_unclear",
                    metadata=branch_metadata,
                    statistical_probability_map=statistical_probability_map,
                    heuristic_probability_map=heuristic_probability_map,
                    final_probability_map=final_probability_map,
                ),
            ),
            TransitionBranch(
                branch_name="not_done",
                probability=final_probability_map["not_done"],
                polarity="unclear",
                resolution="hedged",
                metadata=self._attach_branch_probability_metadata(
                    branch_name="not_done",
                    metadata=branch_metadata,
                    statistical_probability_map=statistical_probability_map,
                    heuristic_probability_map=heuristic_probability_map,
                    final_probability_map=final_probability_map,
                ),
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
        queried_distributions: list[tuple[str, float, ConditionalBranchDistribution]] = []

        for test_type, weight in test_type_weights:
            distribution = self.statistics.query_exam_result_distribution(
                disease_id=belief_item.disease_id,
                test_type=test_type,
            )
            queried_distributions.append((test_type, weight, distribution))
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
                    distribution_weight * distribution.total_count
                    for _, distribution_weight, distribution in queried_distributions
                )
            ),
            backoff_level=dominant_distribution.backoff_level if dominant_distribution is not None else "global",
            source_key=dominant_distribution.source_key if dominant_distribution is not None else ("global",),
            metadata={
                "mixed_test_types": [
                    {"test_type": test_type, "weight": round(float(weight), 4)}
                    for test_type, weight, _ in queried_distributions
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
        confidence_profile = self._estimate_statistical_confidence(
            total_count=distribution.total_count,
            backoff_level=distribution.backoff_level,
        )
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
            "statistical_count_lambda": round(float(confidence_profile["count_lambda"]), 4),
            "statistical_backoff_discount": round(float(confidence_profile["backoff_discount"]), 4),
            "statistical_confidence": round(float(confidence_profile["confidence"]), 4),
            "present_probability": round(float(distribution.probabilities["present"]), 6),
            "absent_probability": round(float(distribution.probabilities["absent"]), 6),
            "unclear_probability": round(float(distribution.probabilities["unclear"]), 6),
        }

    # statistical 只在计数充足且 backoff 足够细时强介入，其余情况由 heuristic 兜底。
    def _estimate_statistical_confidence(
        self,
        *,
        total_count: float,
        backoff_level: str,
    ) -> dict[str, float]:
        count_lambda = self._estimate_count_lambda(total_count)
        backoff_discount = self._estimate_backoff_discount(backoff_level)
        confidence = _clamp_probability(count_lambda * backoff_discount)
        return {
            "count_lambda": count_lambda,
            "backoff_discount": backoff_discount,
            "confidence": confidence,
        }

    def _estimate_count_lambda(self, total_count: float) -> float:
        normalized_total = max(float(total_count), 0.0)
        low_threshold = max(float(self.config.hybrid_count_threshold_low), 0.0)
        mid_threshold = max(float(self.config.hybrid_count_threshold_mid), low_threshold)
        high_threshold = max(float(self.config.hybrid_count_threshold_high), mid_threshold)

        if normalized_total < low_threshold:
            return 0.0
        if normalized_total < mid_threshold:
            return _clamp_probability(self.config.hybrid_lambda_low)
        if normalized_total < high_threshold:
            return _clamp_probability(self.config.hybrid_lambda_mid)
        return _clamp_probability(self.config.hybrid_lambda_high)

    def _estimate_backoff_discount(self, backoff_level: str) -> float:
        normalized_bucket = _normalize_transition_backoff_bucket(backoff_level)
        if normalized_bucket == "disease_family_question_type":
            return _clamp_probability(self.config.backoff_discount_disease_family_question_type)
        if normalized_bucket == "disease_question_type":
            return _clamp_probability(self.config.backoff_discount_disease_question_type)
        if normalized_bucket == "family_question_type":
            return _clamp_probability(self.config.backoff_discount_family_question_type)
        if normalized_bucket == "question_type":
            return _clamp_probability(self.config.backoff_discount_question_type)
        return _clamp_probability(self.config.backoff_discount_global)

    def _predict_heuristic_probability_map(
        self,
        action: MctsAction,
        session_state: SessionState,
        *,
        action_context: TransitionActionContext,
        primary_hypothesis: HypothesisCandidate | HypothesisScore | None,
        candidate_hypotheses: Sequence[HypothesisScore] | None,
    ) -> dict[str, float]:
        heuristic_branches = self.heuristic_fallback.predict_branches(
            action,
            session_state,
            primary_hypothesis=primary_hypothesis,
            candidate_hypotheses=candidate_hypotheses,
        )
        heuristic_probability_map = {
            str(branch.branch_name): float(branch.probability)
            for branch in heuristic_branches
        }
        if action_context.branch_schema != "exam_context":
            return {
                "positive": heuristic_probability_map.get("positive", 0.0),
                "negative": heuristic_probability_map.get("negative", 0.0),
                "doubtful": heuristic_probability_map.get("doubtful", 0.0),
            }

        doubtful_probability = heuristic_probability_map.get("doubtful", 0.0)
        return {
            "done_positive": heuristic_probability_map.get("positive", 0.0),
            "done_negative": heuristic_probability_map.get("negative", 0.0),
            # exam_context 的 heuristic 兜底没有显式 not_done/result 建模，
            # 因此把 uncertain 质量均分给“做过但结果不清楚”和“可能没做过”两类分支。
            "done_unclear": doubtful_probability * 0.5,
            "not_done": doubtful_probability * 0.5,
        }

    def _build_hybrid_transition_summary(
        self,
        *,
        mixture_components: Sequence[dict[str, Any]],
        statistical_probability_map: dict[str, float],
        heuristic_probability_map: dict[str, float],
    ) -> dict[str, Any]:
        statistical_profile = self._summarize_statistical_profile(mixture_components)
        mixing_enabled = (
            self.config.hybrid_enable_count_aware_mixing
            and self.config.fallback_to_heuristic
            and self.heuristic_fallback is not None
        )
        if mixing_enabled:
            hybrid_lambda = _clamp_probability(statistical_profile["statistical_confidence"])
        else:
            hybrid_lambda = 1.0

        if not mixing_enabled:
            source = "statistical"
            hybrid_source = "statistical_only"
        elif hybrid_lambda <= 0.0:
            source = "heuristic_fallback"
            hybrid_source = "heuristic_only_low_confidence_statistical"
        elif hybrid_lambda >= 0.999:
            source = "statistical"
            hybrid_source = "statistical_high_confidence_override"
        else:
            source = "hybrid_statistical_heuristic"
            hybrid_source = "count_aware_statistical_mix"

        return {
            "source": source,
            "statistical_total_count": round(float(statistical_profile["statistical_total_count"]), 4),
            "statistical_backoff_level": statistical_profile["statistical_backoff_level"],
            "statistical_backoff_discount": round(float(statistical_profile["statistical_backoff_discount"]), 4),
            "statistical_confidence": round(float(statistical_profile["statistical_confidence"]), 4),
            "heuristic_confidence_share": round(float(1.0 - hybrid_lambda), 4),
            "hybrid_transition_lambda": round(float(hybrid_lambda), 4),
            "hybrid_transition_source": hybrid_source,
            "statistical_probability_map": _round_probability_map(statistical_probability_map),
            "heuristic_probability_map": _round_probability_map(heuristic_probability_map),
        }

    def _summarize_statistical_profile(
        self,
        mixture_components: Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        weighted_total_count = 0.0
        weighted_backoff_discount = 0.0
        weighted_confidence = 0.0
        dominant_backoff_level = "global"
        dominant_score = -1.0

        for component in mixture_components:
            weight = max(float(component.get("weight", 0.0) or 0.0), 0.0)
            total_count = max(float(component.get("total_count", 0.0) or 0.0), 0.0)
            backoff_level = str(component.get("backoff_level") or "").strip() or "global"
            backoff_discount = _clamp_probability(component.get("statistical_backoff_discount", 0.0) or 0.0)
            confidence = _clamp_probability(component.get("statistical_confidence", 0.0) or 0.0)

            weighted_total_count += weight * total_count
            weighted_backoff_discount += weight * backoff_discount
            weighted_confidence += weight * confidence

            component_score = weight * confidence
            if component_score > dominant_score or (
                abs(component_score - dominant_score) <= 1e-9
                and _transition_backoff_rank(backoff_level) < _transition_backoff_rank(dominant_backoff_level)
            ):
                dominant_score = component_score
                dominant_backoff_level = backoff_level

        return {
            "statistical_total_count": weighted_total_count,
            "statistical_backoff_level": dominant_backoff_level,
            "statistical_backoff_discount": weighted_backoff_discount,
            "statistical_confidence": _clamp_probability(weighted_confidence),
        }

    def _mix_probability_maps(
        self,
        statistical_probability_map: dict[str, float],
        heuristic_probability_map: dict[str, float],
        lambda_weight: float,
    ) -> dict[str, float]:
        final_probability_map: dict[str, float] = {}
        normalized_lambda = _clamp_probability(lambda_weight)
        branch_names = sorted(set(statistical_probability_map) | set(heuristic_probability_map))

        for branch_name in branch_names:
            statistical_probability = max(float(statistical_probability_map.get(branch_name, 0.0) or 0.0), 0.0)
            heuristic_probability = max(float(heuristic_probability_map.get(branch_name, 0.0) or 0.0), 0.0)
            final_probability_map[branch_name] = (
                normalized_lambda * statistical_probability
                + (1.0 - normalized_lambda) * heuristic_probability
            )

        total_probability = sum(final_probability_map.values())
        if total_probability <= 0.0:
            return dict(statistical_probability_map)
        return {
            branch_name: probability / total_probability
            for branch_name, probability in final_probability_map.items()
        }

    def _attach_branch_probability_metadata(
        self,
        *,
        branch_name: str,
        metadata: dict[str, Any],
        statistical_probability_map: dict[str, float],
        heuristic_probability_map: dict[str, float],
        final_probability_map: dict[str, float],
    ) -> dict[str, Any]:
        branch_metadata = dict(metadata)
        branch_metadata["statistical_branch_probability"] = round(
            float(statistical_probability_map.get(branch_name, 0.0) or 0.0),
            6,
        )
        branch_metadata["heuristic_branch_probability"] = round(
            float(heuristic_probability_map.get(branch_name, 0.0) or 0.0),
            6,
        )
        branch_metadata["hybrid_branch_probability"] = round(
            float(final_probability_map.get(branch_name, 0.0) or 0.0),
            6,
        )
        if branch_metadata.get("source") == "heuristic_fallback":
            branch_metadata["fallback_reason"] = "low_statistical_confidence"
            branch_metadata["fallback_from"] = "statistical"
        return branch_metadata

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
                    "hybrid_transition_source": "heuristic_fallback",
                    "statistical_total_count": 0.0,
                    "statistical_backoff_level": "unavailable",
                    "statistical_backoff_discount": 0.0,
                    "statistical_confidence": 0.0,
                    "heuristic_confidence_share": 1.0,
                    "hybrid_transition_lambda": 0.0,
                },
            )
            for branch in fallback_branches
        ]

def _verify_backoff_rank(level: str) -> int:
    return _transition_backoff_rank(level)


def _normalize_transition_backoff_bucket(level: str) -> str:
    normalized = str(level or "").strip()
    return {
        "disease_family_question_type": "disease_family_question_type",
        "disease_exam_kind": "disease_family_question_type",
        "disease_test_type": "disease_family_question_type",
        "disease_question_type": "disease_question_type",
        "disease": "disease_question_type",
        "family_question_type": "family_question_type",
        "exam_kind": "family_question_type",
        "test_type": "family_question_type",
        "question_type": "question_type",
        "global": "global",
    }.get(normalized, "global")


def _transition_backoff_rank(level: str) -> int:
    return {
        "disease_family_question_type": 0,
        "disease_question_type": 1,
        "family_question_type": 2,
        "question_type": 3,
        "global": 4,
    }.get(_normalize_transition_backoff_bucket(level), 5)


def _coarser_backoff_level(left: str, right: str) -> str:
    left_level = str(left or "").strip() or "global"
    right_level = str(right or "").strip() or "global"
    if _transition_backoff_rank(left_level) >= _transition_backoff_rank(right_level):
        return left_level
    return right_level


def _clamp_probability(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


def _round_probability_map(probability_map: dict[str, float]) -> dict[str, float]:
    return {
        str(branch_name): round(float(probability), 6)
        for branch_name, probability in probability_map.items()
    }
