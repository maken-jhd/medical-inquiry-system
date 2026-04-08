"""负责 A2 假设生成、排序与基于证据的简单增减权。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional

from .types import (
    A2HypothesisResult,
    EvidenceState,
    HypothesisCandidate,
    HypothesisScore,
)


@dataclass
class HypothesisManagerConfig:
    """保存假设分数调整阶段的基础参数。"""

    positive_confident_bonus: float = 1.0
    positive_uncertain_bonus: float = 0.4
    negative_confident_penalty: float = 1.0
    negative_uncertain_penalty: float = 0.4


class HypothesisManager:
    """根据 R1 候选和证据状态管理主假设与备选假设。"""

    # 初始化假设管理器配置。
    def __init__(self, config: HypothesisManagerConfig | None = None) -> None:
        self.config = config or HypothesisManagerConfig()

    # 将 R1 返回的原始候选整形成 A2 阶段输出结构。
    def run_a2_hypothesis_generation(
        self,
        candidates: Iterable[HypothesisCandidate],
    ) -> A2HypothesisResult:
        sorted_candidates = sorted(
            candidates,
            key=lambda item: (-item.score, item.name),
        )

        primary_hypothesis: Optional[HypothesisCandidate] = None
        alternatives: List[HypothesisCandidate] = []

        if len(sorted_candidates) > 0:
            primary_hypothesis = sorted_candidates[0]
            alternatives = sorted_candidates[1:]

        reasoning = "已根据图谱 R1 候选分数生成当前主假设和备选假设。"

        if primary_hypothesis is None:
            reasoning = "当前没有足够的 R1 候选，建议回到更基础的症状或流行病学史提问。"

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

    # 根据新证据状态对当前假设分数做简单增减权。
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
        if evidence_state.existence == "exist" and evidence_state.certainty == "confident":
            return self.config.positive_confident_bonus

        if evidence_state.existence == "exist" and evidence_state.certainty == "doubt":
            return self.config.positive_uncertain_bonus

        if evidence_state.existence == "non_exist" and evidence_state.certainty == "confident":
            return -self.config.negative_confident_penalty

        if evidence_state.existence == "non_exist" and evidence_state.certainty == "doubt":
            return -self.config.negative_uncertain_penalty

        return 0.0
