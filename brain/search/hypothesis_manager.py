"""负责 A2 假设生成、排序与基于证据的简单增减权。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional

from ..integrations.llm import LlmClient
from ..state import (
    A2HypothesisResult,
    EvidenceState,
    HypothesisCandidate,
    HypothesisScore,
    PatientContext,
)


@dataclass
class HypothesisManagerConfig:
    """保存假设分数调整阶段的基础参数。"""

    positive_clear_bonus: float = 1.0
    positive_hedged_bonus: float = 0.4
    negative_clear_penalty: float = 1.0
    negative_hedged_penalty: float = 0.4
    unclear_penalty: float = 0.18
    expand_top_k_hypotheses: int = 3
    unique_evidence_bonus: float = 0.18
    overlap_penalty: float = 0.10
    feature_coverage_bonus: float = 0.20
    semantic_score_bonus: float = 0.22
    disease_specific_anchor_bonus: float = 0.35
    verifier_alt_bonus: float = 0.35
    verifier_hedged_penalty: float = 0.25
    verifier_hard_negative_penalty: float = 0.32
    verifier_missing_support_penalty: float = 0.12
    verifier_trajectory_penalty: float = 0.08
    verifier_repeated_missing_support_penalty: float = 0.08
    verifier_observed_anchor_alt_bonus: float = 0.18
    enable_multi_hypothesis_feedback: bool = True
    use_scope_weighted_feedback: bool = True
    max_related_hypotheses_per_evidence: int = 5
    enable_top3_candidate_rescue: bool = False
    top3_rescue_rank_window: int = 8
    top3_rescue_bonus: float = 0.08
    enable_candidate_rank_memory: bool = False
    candidate_rank_memory_bonus: float = 0.05
    candidate_rank_memory_decay: float = 0.6
    enable_evidence_supported_rerank: bool = False
    positive_evidence_support_weight: float = 0.04
    evidence_family_diversity_weight: float = 0.03
    contradiction_penalty_weight: float = 0.05


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
        # A2 的目标是把 R1 候选压成“当前主假设 + 若干强备选”，供后续 search 围绕它们展开。
        # 先按当前 R1 score 做一次基础排序，再用候选之间的竞争关系轻量重排。
        sorted_candidates = sorted(
            candidates,
            key=lambda item: (-item.score, item.name),
        )
        # 轻量竞争性重排
        ranked_candidates = self._rerank_candidates_with_competition(sorted_candidates)

        primary_hypothesis: Optional[HypothesisCandidate] = None
        alternatives: List[HypothesisCandidate] = []

        if len(ranked_candidates) > 0:
            # 若可用，LLM 负责把“为什么 top1 胜过 alternatives”补成更强的语义判断；
            # 否则继续使用规则重排后的排序结果。
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
        # 从这里开始，候选会进入 session_state 的标准 HypothesisScore 形态，后续反馈和 repair 都按它处理。
        hypotheses = [
            HypothesisScore(
                node_id=item.node_id,
                label=item.label,
                name=item.name,
                score=item.score,
                evidence_node_ids=[
                    str(node_id)
                    for node_id in item.metadata.get("evidence_node_ids", [])
                    if len(str(node_id).strip()) > 0
                ],
                metadata=dict(item.metadata),
            )
            for item in candidates
        ]
        return self.refresh_candidate_ranking(hypotheses, reset_candidate_raw_score=True)

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
        feedback_weights: Optional[dict[str, float]] = None,
    ) -> List[HypothesisScore]:
        hypothesis_list = [self._clone_hypothesis(item) for item in hypotheses]
        target_ids = {
            str(item).strip()
            for item in (related_hypothesis_ids or [])
            if len(str(item).strip()) > 0
        }
        resolved_weights = dict(feedback_weights or {})

        if len(resolved_weights) == 0:
            resolved_weights = self.resolve_evidence_feedback_weights(
                hypothesis_list,
                evidence_state,
                related_hypothesis_ids=target_ids,
            )

        if len(resolved_weights) == 0 and len(target_ids) == 0 and not self.config.enable_multi_hypothesis_feedback:
            resolved_weights = {item.node_id: 1.0 for item in hypothesis_list}

        updated: List[HypothesisScore] = []
        delta = self._score_delta_from_evidence(evidence_state)

        for hypothesis in hypothesis_list:
            metadata = dict(hypothesis.metadata)
            raw_score = float(metadata.get("candidate_raw_score", hypothesis.score) or hypothesis.score)
            weight = float(resolved_weights.get(hypothesis.node_id, 0.0) or 0.0)

            if weight > 0.0:
                raw_score += delta * weight
                metadata = self._update_evidence_support_metadata(
                    metadata,
                    evidence_state,
                    weight=weight,
                    delta=delta,
                )

            updated.append(
                HypothesisScore(
                    node_id=hypothesis.node_id,
                    label=hypothesis.label,
                    name=hypothesis.name,
                    score=max(raw_score, 0.0),
                    evidence_node_ids=list(hypothesis.evidence_node_ids),
                    metadata={
                        **metadata,
                        "candidate_raw_score": max(raw_score, 0.0),
                        "last_evidence_feedback_weight": round(weight, 4),
                        "last_evidence_feedback_node_id": evidence_state.node_id,
                        "last_evidence_feedback_polarity": evidence_state.effective_polarity(),
                    },
                )
            )

        return self.refresh_candidate_ranking(updated, reset_candidate_raw_score=False)

    # 将一条真实/模拟证据映射到所有相关 hypothesis，而不是只更新当前动作所属的单个候选。
    def resolve_evidence_feedback_weights(
        self,
        hypotheses: Iterable[HypothesisScore],
        evidence_state: EvidenceState,
        related_hypothesis_ids: Optional[Iterable[str]] = None,
    ) -> dict[str, float]:
        hypothesis_list = [self._clone_hypothesis(item) for item in hypotheses]
        focus_ids = {
            str(item).strip()
            for item in (related_hypothesis_ids or [])
            if len(str(item).strip()) > 0
        }

        if len(hypothesis_list) == 0:
            return {}

        if not self.config.enable_multi_hypothesis_feedback:
            if len(focus_ids) > 0:
                return {item.node_id: 1.0 for item in hypothesis_list if item.node_id in focus_ids}
            return {item.node_id: 1.0 for item in hypothesis_list}

        scored: list[tuple[float, HypothesisScore]] = []

        for hypothesis in hypothesis_list:
            weight = self._feedback_weight_for_hypothesis(
                hypothesis,
                evidence_state,
                focus_ids=focus_ids,
            )

            if weight <= 0.0:
                continue

            scored.append((weight, hypothesis))

        if len(scored) == 0:
            if len(focus_ids) > 0:
                return {item.node_id: 1.0 for item in hypothesis_list if item.node_id in focus_ids}

            if len(hypothesis_list) == 1:
                return {hypothesis_list[0].node_id: 1.0}

            return {}

        max_related = max(int(self.config.max_related_hypotheses_per_evidence), 1)
        ranked = sorted(
            scored,
            key=lambda item: (-item[0], item[1].name),
        )[:max_related]
        return {
            item.node_id: round(weight, 4)
            for weight, item in ranked
            if weight > 0.0
        }

    # 根据 verifier 的拒停理由对主备选假设做一次显式重排。
    def apply_verifier_repair(
        self,
        hypotheses: Iterable[HypothesisScore],
        current_answer_id: str | None,
        reject_reason: str,
        recommended_next_evidence: list[str] | None = None,
        alternative_candidates: list[dict] | None = None,
        repair_feedback_counts: dict | None = None,
    ) -> List[HypothesisScore]:
        # verifier repair 不直接产生下一问，它先通过分数重排把“该关注谁、该补什么”写回候选态。
        # repair 不直接原地改 session_state，而是先 clone 一份 hypothesis 列表再做分数调整。
        ranked = [self._clone_hypothesis(item) for item in hypotheses]
        alternative_items = list(alternative_candidates or [])
        preferred_evidence = [
            str(item).strip()
            for item in (recommended_next_evidence or [])
            if len(str(item).strip()) > 0
        ]

        for index, hypothesis in enumerate(ranked):
            score_delta = 0.0
            metadata = dict(hypothesis.metadata)
            matched_alternative = self._match_verifier_alternative(hypothesis, alternative_items)
            feedback_count = self._repair_feedback_count(repair_feedback_counts, hypothesis.node_id, reject_reason)

            # 当前被 verifier 拒停的答案会按 reject_reason 受到不同幅度的下调。
            if current_answer_id and hypothesis.node_id == current_answer_id:
                if reject_reason in {
                    "strong_alternative_not_ruled_out",
                    "strong_unresolved_alternative_candidates",
                    "anchored_alternative_exists",
                }:
                    score_delta -= self.config.verifier_hedged_penalty
                elif reject_reason == "hard_negative_key_evidence":
                    score_delta -= self.config.verifier_hard_negative_penalty
                elif reject_reason in {
                    "missing_key_support",
                    "missing_required_anchor",
                    "insufficient_evidence_family_coverage",
                }:
                    score_delta -= self.config.verifier_missing_support_penalty
                    score_delta -= min(
                        feedback_count * self.config.verifier_repeated_missing_support_penalty,
                        self.config.verifier_missing_support_penalty * 2.5,
                    )
                elif reject_reason == "trajectory_insufficient":
                    score_delta -= self.config.verifier_trajectory_penalty

            # 如果 verifier 明确点名某个备选诊断，就给它额外抬分，帮助 repair 阶段真正关注竞争者。
            if matched_alternative is not None:
                score_delta += max(self.config.verifier_alt_bonus - index * 0.05, self.config.verifier_alt_bonus * 0.5)
                observed_anchor_bonus = self._observed_anchor_repair_bonus(metadata)
                score_delta += observed_anchor_bonus
                metadata["verifier_alternative_reason"] = matched_alternative.get("reason", "")
                metadata["verifier_observed_anchor_alt_bonus"] = round(observed_anchor_bonus, 4)

            # repair 推荐证据会和 hypothesis 自己已有的推荐证据合并，
            # 供后续 action_builder 在排序时同时感知“当前假设缺什么”和“verifier 想补什么”。
            hypothesis_preferred_evidence = self._merge_recommended_evidence(
                metadata.get("recommended_next_evidence", []),
                [],
            )
            merged_evidence = self._merge_recommended_evidence(
                hypothesis_preferred_evidence,
                preferred_evidence,
            )
            metadata.update(
                {
                    "recommended_next_evidence": merged_evidence,
                    "hypothesis_recommended_next_evidence": hypothesis_preferred_evidence,
                    "verifier_recommended_next_evidence": preferred_evidence,
                    "verifier_reject_reason": reject_reason,
                    "repair_feedback_count": feedback_count,
                    "verifier_adjustment": score_delta,
                    "verifier_role": "alternative" if matched_alternative is not None else metadata.get("verifier_role", "current"),
                }
            )

            ranked[index] = HypothesisScore(
                node_id=hypothesis.node_id,
                label=hypothesis.label,
                name=hypothesis.name,
                score=max(float(metadata.get("candidate_raw_score", hypothesis.score) or hypothesis.score) + score_delta, 0.0),
                evidence_node_ids=list(hypothesis.evidence_node_ids),
                metadata={
                    **metadata,
                    "candidate_raw_score": max(
                        float(metadata.get("candidate_raw_score", hypothesis.score) or hypothesis.score) + score_delta,
                        0.0,
                    ),
                },
            )

        return self.refresh_candidate_ranking(ranked, reset_candidate_raw_score=False)

    # 将外部排序后的 hypothesis 列表重新补齐 rank memory / rescue / debug 字段。
    def refresh_candidate_ranking(
        self,
        hypotheses: Iterable[HypothesisScore],
        *,
        reset_candidate_raw_score: bool = False,
    ) -> List[HypothesisScore]:
        ranked_input = sorted(
            [self._clone_hypothesis(item) for item in hypotheses],
            key=lambda item: (-item.score, item.name),
        )

        if len(ranked_input) == 0:
            return []

        rescored = [
            self._apply_candidate_rerank_adjustments(
                hypothesis,
                current_rank=index,
                reset_candidate_raw_score=reset_candidate_raw_score,
            )
            for index, hypothesis in enumerate(ranked_input, start=1)
        ]
        reranked = sorted(rescored, key=lambda item: (-item.score, item.name))
        finalized: List[HypothesisScore] = []

        for index, hypothesis in enumerate(reranked, start=1):
            metadata = dict(hypothesis.metadata)
            previous_best_rank = int(metadata.get("candidate_previous_best_rank", metadata.get("best_rank_seen", index)) or index)
            previous_presence_count = int(metadata.get("consecutive_presence_count", 0) or 0)
            metadata.update(
                {
                    "new_rank": index,
                    "last_rank": index,
                    "best_rank_seen": min(previous_best_rank, index),
                    "consecutive_presence_count": min(previous_presence_count + 1, 12),
                    "final_candidate_score": round(float(hypothesis.score), 6),
                }
            )
            finalized.append(
                HypothesisScore(
                    node_id=hypothesis.node_id,
                    label=hypothesis.label,
                    name=hypothesis.name,
                    score=hypothesis.score,
                    evidence_node_ids=list(hypothesis.evidence_node_ids),
                    metadata=metadata,
                )
            )

        return finalized

    # 读取 service 持久化的 repair 反馈次数，用于把重复缺口变成显式排序惩罚。
    def _repair_feedback_count(self, payload: dict | None, hypothesis_id: str, reject_reason: str) -> int:
        if not isinstance(payload, dict):
            return 0

        by_hypothesis = payload.get(hypothesis_id, {})
        if not isinstance(by_hypothesis, dict):
            return 0

        return int(by_hypothesis.get(reject_reason, 0) or 0)

    # 备选诊断已有真实 observed anchor 时，repair 重排应该更敢把它拉起来。
    def _observed_anchor_repair_bonus(self, metadata: dict) -> float:
        exact_score = float(metadata.get("exact_scope_anchor_score", 0.0) or 0.0)
        family_score = float(metadata.get("family_scope_anchor_score", 0.0) or 0.0)
        phenotype_score = float(metadata.get("phenotype_support_score", 0.0) or 0.0)
        bonus = exact_score * 0.22 + family_score * 0.14 + phenotype_score * 0.04
        return min(bonus, self.config.verifier_observed_anchor_alt_bonus)

    # 估算某条证据与单个 hypothesis 的相关性，并把 scope/anchor 强度折成反馈权重。
    def _feedback_weight_for_hypothesis(
        self,
        hypothesis: HypothesisScore,
        evidence_state: EvidenceState,
        *,
        focus_ids: set[str],
    ) -> float:
        metadata = dict(hypothesis.metadata)
        normalized_targets = self._feedback_target_names(evidence_state)
        related_node_ids = self._normalize_metadata_string_list(metadata.get("evidence_node_ids", [])) | set(
            self._normalize_match_text(str(item))
            for item in hypothesis.evidence_node_ids
            if len(str(item).strip()) > 0
        )
        related_names = self._hypothesis_evidence_name_set(metadata)
        relation_types = self._normalize_metadata_string_list(metadata.get("relation_types", []))
        evidence_relation_type = self._normalize_match_text(str(evidence_state.metadata.get("relation_type") or ""))
        scope_weight = self._feedback_scope_weight(metadata)
        focused = hypothesis.node_id in focus_ids

        if self._normalize_match_text(evidence_state.node_id) in related_node_ids:
            return min(1.0, 0.78 + scope_weight * 0.24 + (0.04 if focused else 0.0))

        if self._has_feedback_name_overlap(normalized_targets, related_names):
            return min(0.95, 0.58 + scope_weight * 0.28 + (0.04 if focused else 0.0))

        if focused and len(evidence_relation_type) > 0 and evidence_relation_type in relation_types:
            return min(0.82, 0.48 + scope_weight * 0.22)

        if focused:
            return min(0.68, 0.40 + scope_weight * 0.18)

        if len(evidence_relation_type) > 0 and evidence_relation_type in relation_types and scope_weight >= 0.55:
            return min(0.52, 0.18 + scope_weight * 0.32)

        return 0.0

    # 把 observed anchor / family anchor / phenotype support 统一折算成证据反馈权重。
    def _feedback_scope_weight(self, metadata: dict) -> float:
        if not self.config.use_scope_weighted_feedback:
            return 1.0

        anchor_tier = str(metadata.get("anchor_tier") or "")
        exact_score = float(metadata.get("exact_scope_anchor_score", 0.0) or 0.0)
        family_score = float(metadata.get("family_scope_anchor_score", 0.0) or 0.0)
        phenotype_score = float(metadata.get("phenotype_support_score", 0.0) or 0.0)
        observed_anchor_score = float(metadata.get("observed_anchor_score", 0.0) or 0.0)

        if anchor_tier in {"strong_anchor", "definition_anchor"} or exact_score >= 0.55:
            return 1.0

        if anchor_tier == "family_anchor" or family_score >= 0.35:
            return 0.78

        if anchor_tier == "phenotype_supported" or phenotype_score > 0.0:
            return 0.55

        if anchor_tier == "background_supported" or observed_anchor_score > 0.0:
            return 0.32

        return 0.18

    # 将 evidence_state 中的 node name / normalized name 统一转成可比对的字符串集合。
    def _feedback_target_names(self, evidence_state: EvidenceState) -> set[str]:
        names = {
            self._normalize_match_text(str(item))
            for item in (
                evidence_state.metadata.get("target_node_name"),
                evidence_state.metadata.get("normalized_name"),
                evidence_state.node_id,
            )
            if len(str(item).strip()) > 0
        }
        return {item for item in names if len(item) > 0}

    # 从 hypothesis metadata 中抽出所有可用于比对的候选证据名称。
    def _hypothesis_evidence_name_set(self, metadata: dict) -> set[str]:
        names = self._normalize_metadata_string_list(metadata.get("evidence_names", []))

        for payload in metadata.get("evidence_payloads", []):
            if not isinstance(payload, dict):
                continue

            for key in ("name", "display_name"):
                text = str(payload.get(key) or "").strip()
                if len(text) == 0:
                    continue
                names.add(self._normalize_match_text(text))

        return {item for item in names if len(item) > 0}

    # 允许“完全相等”和“较稳定的包含关系”两种匹配，避免一个别名变化就断开反馈。
    def _has_feedback_name_overlap(self, left: set[str], right: set[str]) -> bool:
        for left_item in left:
            for right_item in right:
                if left_item == right_item:
                    return True

                if min(len(left_item), len(right_item)) >= 4 and (
                    left_item in right_item or right_item in left_item
                ):
                    return True

        return False

    # 将 metadata 里的名称/关系列表轻量标准化，供 feedback 与 repair 复用。
    def _normalize_metadata_string_list(self, values: object) -> set[str]:
        if not isinstance(values, list):
            return set()

        return {
            self._normalize_match_text(str(item))
            for item in values
            if len(str(item).strip()) > 0
        }

    def _normalize_match_text(self, text: str) -> str:
        return (
            text.strip()
            .lower()
            .replace(" ", "")
            .replace("（", "(")
            .replace("）", ")")
            .replace("，", ",")
            .replace("。", "")
            .replace("、", "")
            .replace("-", "")
            .replace("_", "")
            .replace("/", "")
        )

    # 根据证据存在性和回答清晰度计算对假设分数的调整值。
    def _score_delta_from_evidence(self, evidence_state: EvidenceState) -> float:
        relation_type = str(evidence_state.metadata.get("relation_type", ""))
        relation_multiplier = self._relation_multiplier(relation_type)
        polarity = evidence_state.effective_polarity()

        if polarity == "present" and evidence_state.resolution == "clear":
            return self.config.positive_clear_bonus * relation_multiplier

        if polarity == "present" and evidence_state.resolution == "hedged":
            return self.config.positive_hedged_bonus * relation_multiplier

        if polarity == "absent" and evidence_state.resolution == "clear":
            return -self.config.negative_clear_penalty * relation_multiplier

        if polarity == "absent" and evidence_state.resolution == "hedged":
            return -self.config.negative_hedged_penalty * relation_multiplier

        if polarity == "unclear":
            return -self.config.unclear_penalty * relation_multiplier

        return 0.0

    # 根据关系类型给不同证据强度设置不同倍率。
    def _relation_multiplier(self, relation_type: str) -> float:
        if relation_type == "DIAGNOSED_BY":
            return 1.35

        if relation_type == "HAS_PATHOGEN":
            return 1.3

        if relation_type == "HAS_LAB_FINDING":
            return 1.15

        if relation_type == "HAS_IMAGING_FINDING":
            return 1.15

        if relation_type == "MANIFESTS_AS":
            return 1.0

        if relation_type == "RISK_FACTOR_FOR":
            return 0.7

        if relation_type == "REQUIRES_DETAIL":
            return 0.6

        return 0.9

    # 将真实反馈折成 candidate rescue / rerank 能复用的轻量支持计数。
    def _update_evidence_support_metadata(
        self,
        metadata: dict,
        evidence_state: EvidenceState,
        *,
        weight: float,
        delta: float,
    ) -> dict:
        updated = dict(metadata)
        polarity = evidence_state.effective_polarity()
        support_strength = abs(delta) * max(weight, 0.0)

        if support_strength <= 0.0:
            return updated

        relation_family = self._relation_family_from_evidence(evidence_state)
        evidence_name = str(
            evidence_state.metadata.get("target_node_name")
            or evidence_state.metadata.get("normalized_name")
            or evidence_state.node_id
        ).strip()

        if polarity == "present":
            updated["positive_evidence_support_count"] = int(updated.get("positive_evidence_support_count", 0) or 0) + 1
            updated["positive_evidence_support_score"] = round(
                float(updated.get("positive_evidence_support_score", 0.0) or 0.0) + support_strength,
                6,
            )
            updated["positive_evidence_support_families"] = self._merge_string_list(
                updated.get("positive_evidence_support_families", []),
                [relation_family],
            )
            updated["positive_support_evidence_names"] = self._merge_string_list(
                updated.get("positive_support_evidence_names", []),
                [evidence_name],
                limit=6,
            )
            return updated

        if polarity == "absent":
            updated["negative_evidence_support_count"] = int(updated.get("negative_evidence_support_count", 0) or 0) + 1
            updated["negative_evidence_support_score"] = round(
                float(updated.get("negative_evidence_support_score", 0.0) or 0.0) + support_strength,
                6,
            )
            updated["negative_evidence_support_families"] = self._merge_string_list(
                updated.get("negative_evidence_support_families", []),
                [relation_family],
            )
            updated["negative_support_evidence_names"] = self._merge_string_list(
                updated.get("negative_support_evidence_names", []),
                [evidence_name],
                limit=6,
            )
            return updated

        updated["unclear_evidence_support_count"] = int(updated.get("unclear_evidence_support_count", 0) or 0) + 1
        updated["unclear_evidence_support_score"] = round(
            float(updated.get("unclear_evidence_support_score", 0.0) or 0.0) + support_strength * 0.5,
            6,
        )
        return updated

    # 在不改主干打分体系的前提下，给 Top-3 附近候选增加一层轻量保留和解释性 debug。
    def _apply_candidate_rerank_adjustments(
        self,
        hypothesis: HypothesisScore,
        *,
        current_rank: int,
        reset_candidate_raw_score: bool,
    ) -> HypothesisScore:
        metadata = dict(hypothesis.metadata)
        raw_score = float(hypothesis.score if reset_candidate_raw_score else metadata.get("candidate_raw_score", hypothesis.score) or hypothesis.score)
        previous_best_rank = int(metadata.get("best_rank_seen", metadata.get("new_rank", current_rank)) or current_rank)
        previous_presence_count = int(metadata.get("consecutive_presence_count", 0) or 0)

        positive_support_signal = self._positive_support_signal(metadata)
        diversity_signal = self._evidence_diversity_signal(metadata)
        contradiction_signal = self._contradiction_signal(metadata)

        positive_bonus = 0.0
        diversity_bonus = 0.0
        contradiction_penalty = 0.0
        if self.config.enable_evidence_supported_rerank:
            positive_bonus = self.config.positive_evidence_support_weight * positive_support_signal
            diversity_bonus = self.config.evidence_family_diversity_weight * diversity_signal
            contradiction_penalty = self.config.contradiction_penalty_weight * contradiction_signal

        rescue_bonus = 0.0
        rescue_window = max(int(self.config.top3_rescue_rank_window), 4)
        rescue_signal = max(0.0, positive_support_signal + diversity_signal * 0.25 - contradiction_signal)
        if self.config.enable_top3_candidate_rescue and 4 <= current_rank <= rescue_window and rescue_signal > 0.08:
            rank_proximity = (rescue_window - current_rank + 1) / max(rescue_window - 3, 1)
            rescue_bonus = self.config.top3_rescue_bonus * min(rescue_signal, 1.0) * max(rank_proximity, 0.0)

        memory_bonus = 0.0
        if self.config.enable_candidate_rank_memory and current_rank <= rescue_window:
            support_floor = positive_support_signal + diversity_signal * 0.2 - contradiction_signal * 0.65
            if previous_best_rank <= 5 and support_floor > 0.06:
                presence_scale = min((previous_presence_count + 1) / 3.0, 1.0)
                memory_decay = min(max(self.config.candidate_rank_memory_decay, 0.0), 1.0)
                rank_gap = max(current_rank - previous_best_rank, 0)
                memory_bonus = (
                    self.config.candidate_rank_memory_bonus
                    * presence_scale
                    * (memory_decay ** rank_gap)
                    * min(max(support_floor, 0.0), 1.0)
                )

        final_score = max(raw_score + positive_bonus + diversity_bonus + rescue_bonus + memory_bonus - contradiction_penalty, 0.0)
        metadata.update(
            {
                "candidate_raw_score": round(raw_score, 6),
                "candidate_previous_best_rank": previous_best_rank,
                "old_rank": current_rank,
                "positive_support_signal": round(positive_support_signal, 6),
                "evidence_family_diversity_signal": round(diversity_signal, 6),
                "contradiction_signal": round(contradiction_signal, 6),
                "positive_evidence_support_bonus": round(positive_bonus, 6),
                "evidence_family_diversity_bonus": round(diversity_bonus, 6),
                "top3_rescue_bonus": round(rescue_bonus, 6),
                "rank_memory_bonus": round(memory_bonus, 6),
                "contradiction_penalty": round(contradiction_penalty, 6),
                "candidate_rescue_eligible": bool(4 <= current_rank <= rescue_window and rescue_signal > 0.08),
            }
        )
        return HypothesisScore(
            node_id=hypothesis.node_id,
            label=hypothesis.label,
            name=hypothesis.name,
            score=final_score,
            evidence_node_ids=list(hypothesis.evidence_node_ids),
            metadata=metadata,
        )

    def _positive_support_signal(self, metadata: dict) -> float:
        positive_count = int(metadata.get("positive_evidence_support_count", 0) or 0)
        positive_score = float(metadata.get("positive_evidence_support_score", 0.0) or 0.0)
        exact_score = float(metadata.get("exact_scope_anchor_score", 0.0) or 0.0)
        family_score = float(metadata.get("family_scope_anchor_score", 0.0) or 0.0)
        phenotype_score = float(metadata.get("phenotype_support_score", 0.0) or 0.0)
        observed_score = float(metadata.get("observed_anchor_score", 0.0) or 0.0)
        signal = (
            positive_score * 0.34
            + positive_count * 0.08
            + exact_score * 0.72
            + family_score * 0.46
            + phenotype_score * 0.24
            + observed_score * 0.18
        )
        return min(max(signal, 0.0), 1.0)

    def _evidence_diversity_signal(self, metadata: dict) -> float:
        families = self._normalize_string_list(metadata.get("positive_evidence_support_families", []))
        if len(families) == 0:
            return 0.0
        return min(len(families) / 3.0, 1.0)

    def _contradiction_signal(self, metadata: dict) -> float:
        negative_count = int(metadata.get("negative_evidence_support_count", 0) or 0)
        negative_score = float(metadata.get("negative_evidence_support_score", 0.0) or 0.0)
        unclear_count = int(metadata.get("unclear_evidence_support_count", 0) or 0)
        unclear_score = float(metadata.get("unclear_evidence_support_score", 0.0) or 0.0)
        anchor_negative = float(metadata.get("anchor_negative_score", 0.0) or 0.0)
        scope_mismatch = float(metadata.get("scope_mismatch_score", 0.0) or 0.0)
        generic_scope_penalty = float(metadata.get("generic_scope_penalty", 0.0) or 0.0)
        signal = (
            negative_score * 0.34
            + unclear_score * 0.12
            + negative_count * 0.08
            + unclear_count * 0.04
            + anchor_negative * 0.58
            + scope_mismatch * 0.42
            + generic_scope_penalty * 0.35
        )
        return min(max(signal, 0.0), 1.0)

    def _relation_family_from_evidence(self, evidence_state: EvidenceState) -> str:
        question_type_hint = str(evidence_state.metadata.get("question_type_hint") or "").strip()
        if len(question_type_hint) > 0:
            return question_type_hint

        relation_type = str(evidence_state.metadata.get("relation_type") or "").strip()
        if relation_type in {"DIAGNOSED_BY", "HAS_LAB_FINDING"}:
            return "lab"
        if relation_type == "HAS_IMAGING_FINDING":
            return "imaging"
        if relation_type == "HAS_PATHOGEN":
            return "pathogen"
        if relation_type == "MANIFESTS_AS":
            return "symptom"
        if relation_type == "RISK_FACTOR_FOR":
            return "risk"
        if relation_type == "REQUIRES_DETAIL":
            return "detail"
        return "other"

    def _merge_string_list(
        self,
        existing: object,
        values: Iterable[str],
        *,
        limit: int = 8,
    ) -> list[str]:
        merged = list(self._normalize_string_list(existing))

        for value in values:
            text = str(value).strip()
            if len(text) == 0 or text in merged:
                continue
            merged.append(text)
            if len(merged) >= limit:
                break

        return merged

    def _normalize_string_list(self, values: object) -> list[str]:
        if not isinstance(values, list):
            return []
        normalized: list[str] = []
        for item in values:
            text = str(item).strip()
            if len(text) == 0 or text in normalized:
                continue
            normalized.append(text)
        return normalized

    # 克隆假设对象，避免直接修改原列表中的引用。
    def _clone_hypothesis(self, hypothesis: HypothesisScore) -> HypothesisScore:
        return HypothesisScore(
            node_id=hypothesis.node_id,
            label=hypothesis.label,
            name=hypothesis.name,
            score=hypothesis.score,
            evidence_node_ids=list(hypothesis.evidence_node_ids),
            metadata=dict(hypothesis.metadata),
        )

    # 将 verifier 推荐的下一步证据与现有推荐证据做去重合并。
    def _merge_recommended_evidence(
        self,
        existing: object,
        preferred_evidence: list[str],
    ) -> list[str]:
        merged: list[str] = []

        if isinstance(existing, list):
            for item in existing:
                text = str(item).strip()

                if len(text) > 0 and text not in merged:
                    merged.append(text)

        for item in preferred_evidence:
            if item not in merged:
                merged.append(item)

        return merged

    # 将 verifier 提及的替代诊断与当前 hypothesis 对齐。
    def _match_verifier_alternative(
        self,
        hypothesis: HypothesisScore,
        alternative_candidates: list[dict],
    ) -> dict | None:
        for item in alternative_candidates:
            answer_id = str(item.get("answer_id") or "").strip()
            answer_name = str(item.get("answer_name") or "").strip()

            if len(answer_id) > 0 and answer_id == hypothesis.node_id:
                return item

            if len(answer_name) > 0 and answer_name == hypothesis.name:
                return item

        return None

    # 尝试使用 LLM 对候选假设进行更贴近论文的排序。
    def _try_rank_with_llm(
        self,
        patient_context: PatientContext | None,
        candidates: list[HypothesisCandidate],
    ) -> A2HypothesisResult | None:
        # 没有 patient_context 或没有可用 LLM 时直接跳过，不强行走模型排序。
        if self.llm_client is None or not self.llm_client.is_available() or patient_context is None:
            return None

        try:
            # 只把 top 几个高分候选送给 LLM，避免 prompt 过长且削弱排序聚焦。
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

        # LLM 返回的候选要尽量回对到现有图谱候选，避免凭空生成一个新疾病对象。
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

        # 先统计“哪些证据被多个候选共享”，后面才能区分 unique evidence 和 overlap evidence。
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
            # 对每个候选计算：
            # - unique_evidence_count：越多越有区分性
            # - overlap_ratio：共享证据越多越泛化
            # - feature_coverage / semantic_score：保留原始 R1 支持强度
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
            disease_specific_anchor_score = float(candidate.metadata.get("disease_specific_anchor_score", 0.0))
            rerank_bonus = (
                unique_evidence_count * self.config.unique_evidence_bonus
                + feature_coverage * self.config.feature_coverage_bonus
                + semantic_score * self.config.semantic_score_bonus
                + disease_specific_anchor_score * self.config.disease_specific_anchor_bonus
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
                        "disease_specific_anchor_score": disease_specific_anchor_score,
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
            # primary 会记录“支持它的特征、冲突特征、为什么比 alternatives 更优”。
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
            # alternatives 只保留相对 primary 的竞争角色和 why_not_primary 说明，供前端解释使用。
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

        # 优先回对已有候选，只有完全对不齐时才构造一个 llm_only 占位对象。
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
