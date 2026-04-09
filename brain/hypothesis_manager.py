"""负责 A2 假设生成、排序与基于证据的简单增减权。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional

from .llm_client import LlmClient
from .types import (
    A2HypothesisResult,
    EvidenceState,
    HypothesisCandidate,
    HypothesisScore,
    PatientContext,
)


@dataclass
class HypothesisManagerConfig:
    """保存假设分数调整阶段的基础参数。"""

    positive_confident_bonus: float = 1.0
    positive_uncertain_bonus: float = 0.4
    negative_confident_penalty: float = 1.0
    negative_uncertain_penalty: float = 0.4
    expand_top_k_hypotheses: int = 3
    unique_evidence_bonus: float = 0.18
    overlap_penalty: float = 0.10
    feature_coverage_bonus: float = 0.20
    semantic_score_bonus: float = 0.22


class HypothesisManager:
    """根据 R1 候选和证据状态管理主假设与备选假设。"""

    # 初始化假设管理器配置。
    def __init__(
        self,
        llm_client: LlmClient | None = None,
        config: HypothesisManagerConfig | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.config = config or HypothesisManagerConfig()

    # 将 R1 返回的原始候选结合患者上下文整形成 A2 阶段输出结构。
    def run_a2_hypothesis_generation(
        self,
        patient_context: PatientContext | None,
        candidates: Iterable[HypothesisCandidate],
    ) -> A2HypothesisResult:
        sorted_candidates = sorted(
            candidates,
            key=lambda item: (-item.score, item.name),
        )
        ranked_candidates = self._rerank_candidates_with_competition(sorted_candidates)

        primary_hypothesis: Optional[HypothesisCandidate] = None
        alternatives: List[HypothesisCandidate] = []

        if len(ranked_candidates) > 0:
            llm_result = self._try_rank_with_llm(patient_context, ranked_candidates)

            if llm_result is not None:
                return self._attach_llm_competition_metadata(llm_result)

            primary_hypothesis = ranked_candidates[0]
            alternatives = ranked_candidates[1 : self.config.expand_top_k_hypotheses]

        reasoning = "已根据患者上下文与图谱 R1 候选分数生成当前主假设和备选假设。"

        if primary_hypothesis is None:
            reasoning = "当前没有足够的 R1 候选，建议回到更基础的症状或流行病学史提问。"
        elif patient_context is not None:
            feature_names = [item.normalized_name for item in patient_context.clinical_features[:5]]
            reasoning = (
                f"已结合患者一般信息与线索 {', '.join(feature_names) or '无明显核心特征'}"
                " 生成当前主假设和备选假设，并做了竞争性重排。"
            )

        return A2HypothesisResult(
            primary_hypothesis=primary_hypothesis,
            alternatives=alternatives,
            reasoning=reasoning,
        )

    # 将 A2 假设候选转换为当前系统使用的假设得分对象。
    def build_hypothesis_scores(
        self,
        candidates: Iterable[HypothesisCandidate],
    ) -> List[HypothesisScore]:
        return [
            HypothesisScore(
                node_id=item.node_id,
                label=item.label,
                name=item.name,
                score=item.score,
                metadata=dict(item.metadata),
            )
            for item in candidates
        ]

    # 选择本轮需要继续展开的主假设和备选假设。
    def select_expandable_hypotheses(
        self,
        hypotheses: list[HypothesisScore],
        top_k: int | None = None,
    ) -> list[HypothesisScore]:
        limit = top_k or self.config.expand_top_k_hypotheses
        ranked = sorted(hypotheses, key=lambda item: (-item.score, item.name))
        return ranked[:limit]

    # 根据新证据状态对当前假设分数做按关系类型加权的增减权。
    def apply_evidence_feedback(
        self,
        hypotheses: Iterable[HypothesisScore],
        evidence_state: EvidenceState,
        related_hypothesis_ids: Optional[Iterable[str]] = None,
    ) -> List[HypothesisScore]:
        target_ids = set(related_hypothesis_ids or [])
        updated: List[HypothesisScore] = []

        for hypothesis in hypotheses:
            score = hypothesis.score

            if len(target_ids) == 0 or hypothesis.node_id in target_ids:
                score += self._score_delta_from_evidence(evidence_state)

            updated.append(
                HypothesisScore(
                    node_id=hypothesis.node_id,
                    label=hypothesis.label,
                    name=hypothesis.name,
                    score=max(score, 0.0),
                    evidence_node_ids=list(hypothesis.evidence_node_ids),
                    metadata=dict(hypothesis.metadata),
                )
            )

        return sorted(updated, key=lambda item: (-item.score, item.name))

    # 根据证据存在性和确定性计算对假设分数的调整值。
    def _score_delta_from_evidence(self, evidence_state: EvidenceState) -> float:
        relation_type = str(evidence_state.metadata.get("relation_type", ""))
        relation_multiplier = self._relation_multiplier(relation_type)

        if evidence_state.existence == "exist" and evidence_state.certainty == "confident":
            return self.config.positive_confident_bonus * relation_multiplier

        if evidence_state.existence == "exist" and evidence_state.certainty == "doubt":
            return self.config.positive_uncertain_bonus * relation_multiplier

        if evidence_state.existence == "non_exist" and evidence_state.certainty == "confident":
            return -self.config.negative_confident_penalty * relation_multiplier

        if evidence_state.existence == "non_exist" and evidence_state.certainty == "doubt":
            return -self.config.negative_uncertain_penalty * relation_multiplier

        return 0.0

    # 根据关系类型给不同证据强度设置不同倍率。
    def _relation_multiplier(self, relation_type: str) -> float:
        if relation_type == "DIAGNOSED_BY":
            return 1.25

        if relation_type == "HAS_LAB_FINDING":
            return 1.15

        if relation_type == "MANIFESTS_AS":
            return 1.0

        if relation_type == "RISK_FACTOR_FOR":
            return 0.7

        if relation_type == "REQUIRES_DETAIL":
            return 0.6

        return 0.9

    # 尝试使用 LLM 对候选假设进行更贴近论文的排序。
    def _try_rank_with_llm(
        self,
        patient_context: PatientContext | None,
        candidates: list[HypothesisCandidate],
    ) -> A2HypothesisResult | None:
        if self.llm_client is None or not self.llm_client.is_available() or patient_context is None:
            return None

        try:
            payload = self.llm_client.run_structured_prompt(
                "a2_hypothesis_generation",
                {
                    "patient_context": patient_context,
                    "candidates": candidates[: self.config.expand_top_k_hypotheses + 2],
                },
                dict,
            )
        except Exception:
            return None

        primary_payload = payload.get("primary_hypothesis")

        if not isinstance(primary_payload, dict):
            return None

        primary_hypothesis = self._coerce_candidate(primary_payload, candidates)
        alternatives = [
            self._coerce_candidate(item, candidates)
            for item in payload.get("alternatives", [])
            if isinstance(item, dict)
        ]
        alternatives = [item for item in alternatives if item is not None]

        return A2HypothesisResult(
            primary_hypothesis=primary_hypothesis,
            alternatives=alternatives[: self.config.expand_top_k_hypotheses],
            reasoning=payload.get("reasoning", "已由 LLM 完成假设排序。"),
            metadata={
                "source": "llm",
                "supporting_features": payload.get("supporting_features", []),
                "conflicting_features": payload.get("conflicting_features", []),
                "why_primary_beats_alternatives": payload.get("why_primary_beats_alternatives", ""),
                "recommended_next_evidence": payload.get("recommended_next_evidence", []),
            },
        )

    # 在进入最终 A2 之前，先用候选之间的竞争关系做一次轻量重排。
    def _rerank_candidates_with_competition(
        self,
        candidates: list[HypothesisCandidate],
    ) -> list[HypothesisCandidate]:
        if len(candidates) <= 1:
            return candidates

        evidence_frequency: dict[str, int] = {}

        for candidate in candidates:
            evidence_names = {
                str(item)
                for item in candidate.metadata.get("evidence_names", [])
                if len(str(item)) > 0
            }

            for evidence_name in evidence_names:
                evidence_frequency[evidence_name] = evidence_frequency.get(evidence_name, 0) + 1

        reranked: list[HypothesisCandidate] = []

        for candidate in candidates:
            evidence_names = [
                str(item)
                for item in candidate.metadata.get("evidence_names", [])
                if len(str(item)) > 0
            ]
            unique_evidence_count = sum(1 for item in evidence_names if evidence_frequency.get(item, 0) == 1)
            overlap_ratio = 0.0

            if len(evidence_names) > 0:
                overlap_ratio = sum(1 for item in evidence_names if evidence_frequency.get(item, 0) > 1) / len(evidence_names)

            feature_coverage = float(candidate.metadata.get("feature_coverage", 0.0))
            semantic_score = float(candidate.metadata.get("semantic_score", candidate.score))
            rerank_bonus = (
                unique_evidence_count * self.config.unique_evidence_bonus
                + feature_coverage * self.config.feature_coverage_bonus
                + semantic_score * self.config.semantic_score_bonus
                - overlap_ratio * self.config.overlap_penalty
            )
            reranked.append(
                HypothesisCandidate(
                    node_id=candidate.node_id,
                    name=candidate.name,
                    label=candidate.label,
                    score=candidate.score + rerank_bonus,
                    reasoning=candidate.reasoning,
                    metadata={
                        **dict(candidate.metadata),
                        "unique_evidence_count": unique_evidence_count,
                        "overlap_ratio": overlap_ratio,
                        "competition_rerank_bonus": rerank_bonus,
                    },
                )
            )

        return sorted(reranked, key=lambda item: (-item.score, item.name))

    # 将 LLM 输出的竞争性信息真正写回主假设和备选假设 metadata。
    def _attach_llm_competition_metadata(self, result: A2HypothesisResult) -> A2HypothesisResult:
        primary = result.primary_hypothesis
        alternatives = list(result.alternatives)
        recommended_next_evidence = result.metadata.get("recommended_next_evidence", [])
        supporting_features = result.metadata.get("supporting_features", [])
        conflicting_features = result.metadata.get("conflicting_features", [])
        why_primary_beats_alternatives = result.metadata.get("why_primary_beats_alternatives", "")

        if primary is not None:
            primary = HypothesisCandidate(
                node_id=primary.node_id,
                name=primary.name,
                label=primary.label,
                score=primary.score,
                reasoning=primary.reasoning,
                metadata={
                    **dict(primary.metadata),
                    "recommended_next_evidence": recommended_next_evidence,
                    "supporting_features": supporting_features,
                    "conflicting_features": conflicting_features,
                    "why_primary_beats_alternatives": why_primary_beats_alternatives,
                    "competition_role": "primary",
                },
            )

        enriched_alternatives: list[HypothesisCandidate] = []

        for alternative in alternatives:
            enriched_alternatives.append(
                HypothesisCandidate(
                    node_id=alternative.node_id,
                    name=alternative.name,
                    label=alternative.label,
                    score=alternative.score,
                    reasoning=alternative.reasoning,
                    metadata={
                        **dict(alternative.metadata),
                        "competition_role": "alternative",
                        "primary_candidate_id": primary.node_id if primary is not None else None,
                        "why_not_primary": why_primary_beats_alternatives,
                    },
                )
            )

        return A2HypothesisResult(
            primary_hypothesis=primary,
            alternatives=enriched_alternatives,
            reasoning=result.reasoning,
            metadata=dict(result.metadata),
        )

    # 将 LLM 返回的候选信息与现有图谱候选做对齐。
    def _coerce_candidate(
        self,
        payload: dict,
        candidates: list[HypothesisCandidate],
    ) -> HypothesisCandidate | None:
        node_id = payload.get("node_id")
        name = payload.get("name")

        for item in candidates:
            if node_id and item.node_id == node_id:
                return item

            if name and item.name == name:
                return item

        if not isinstance(name, str) or len(name) == 0:
            return None

        return HypothesisCandidate(
            node_id=str(node_id or name),
            name=name,
            label=str(payload.get("label", "Disease")),
            score=float(payload.get("score", 0.0)),
            reasoning=str(payload.get("reasoning", "")),
            metadata={"source": "llm_only"},
        )
