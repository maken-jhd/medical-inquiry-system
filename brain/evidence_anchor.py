"""基于真实会话证据计算诊断候选的 observed anchor。"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any, Sequence

from .types import EvidenceState, HypothesisScore, SessionState, SlotState


STRONG_ANCHOR_LABELS = {"Pathogen", "LabFinding", "ImagingFinding", "LabTest"}
ANCHOR_RELATION_TYPES = {
    "HAS_PATHOGEN",
    "DIAGNOSED_BY",
    "HAS_LAB_FINDING",
    "HAS_IMAGING_FINDING",
    "CONFIRMED_BY",
    "DETECTED_BY",
}
DETAIL_DEFINITION_RELATION_TYPES = {"DIAGNOSED_BY", "REQUIRES_DETAIL"}


@dataclass
class EvidenceAnchorConfig:
    """保存 observed anchor 计算的通用权重。"""

    strong_anchor_bonus: float = 2.4
    provisional_anchor_bonus: float = 0.95
    background_support_bonus: float = 0.18
    negative_anchor_penalty: float = 1.45
    present_clear_confidence: float = 1.0
    present_hedged_confidence: float = 0.55
    absent_clear_confidence: float = 1.0
    default_disease_degree: int = 1
    relation_weights: dict[str, float] = field(
        default_factory=lambda: {
            "HAS_PATHOGEN": 1.0,
            "DIAGNOSED_BY": 1.0,
            "CONFIRMED_BY": 1.0,
            "DETECTED_BY": 1.0,
            "HAS_LAB_FINDING": 0.85,
            "HAS_IMAGING_FINDING": 0.85,
            "MANIFESTS_AS": 0.45,
            "REQUIRES_DETAIL": 0.55,
            "RISK_FACTOR_FOR": 0.35,
            "APPLIES_TO": 0.25,
        }
    )
    label_priors: dict[str, float] = field(
        default_factory=lambda: {
            "Pathogen": 1.0,
            "LabFinding": 0.85,
            "ImagingFinding": 0.85,
            "LabTest": 0.55,
            "ClinicalAttribute": 0.55,
            "ClinicalFinding": 0.35,
            "RiskFactor": 0.25,
            "PopulationGroup": 0.2,
        }
    )


@dataclass
class ObservedEvidenceItem:
    """表示从真实会话状态中抽出的单条证据。"""

    node_id: str
    name: str
    label: str = ""
    polarity: str = "unclear"
    resolution: str = "unknown"
    existence: str = "unknown"
    status: str = "unknown"
    source: str = ""
    source_turns: list[int] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class EvidenceAnchorAnalyzer:
    """把真实患者回答中的高特异证据整理成候选诊断锚点。"""

    def __init__(self, config: EvidenceAnchorConfig | None = None) -> None:
        self.config = config or EvidenceAnchorConfig()

    # 对当前候选做 observed-anchor 重排，并返回可写入 session metadata 的摘要。
    def rerank_hypotheses(
        self,
        state: SessionState,
        hypotheses: Sequence[HypothesisScore],
    ) -> tuple[list[HypothesisScore], dict]:
        observed_items = self.collect_observed_evidence(state)
        match_frequency = self._build_match_frequency(observed_items, hypotheses)
        enriched: list[HypothesisScore] = []
        candidate_summaries: dict[str, dict] = {}

        for hypothesis in hypotheses:
            summary = self._summarize_candidate(hypothesis, observed_items, match_frequency)
            candidate_summaries[hypothesis.node_id] = summary
            enriched.append(self._apply_summary_to_hypothesis(hypothesis, summary))

        ranked = sorted(
            enriched,
            key=lambda item: (
                -self._anchor_tier_priority(str(item.metadata.get("anchor_tier") or "")),
                -float(item.metadata.get("observed_anchor_score", 0.0)),
                -float(item.score),
                item.name,
            ),
        )
        index = self._build_anchor_index(observed_items, ranked, candidate_summaries)
        return ranked, index

    # 只从真实会话状态抽证据；rollout / simulation 来源会被过滤掉。
    def collect_observed_evidence(self, state: SessionState) -> list[ObservedEvidenceItem]:
        items: dict[str, ObservedEvidenceItem] = {}

        for evidence in state.evidence_states.values():
            if self._is_simulated_evidence(evidence.metadata):
                continue
            item = self._observed_item_from_evidence_state(evidence)
            if item.node_id:
                items[item.node_id] = item

        for slot in state.slots.values():
            if self._is_simulated_evidence(slot.metadata):
                continue
            item = self._observed_item_from_slot(slot)
            if not item.node_id:
                continue
            existing = items.get(item.node_id)
            if existing is None or self._observed_item_priority(item) > self._observed_item_priority(existing):
                items[item.node_id] = item

        return sorted(items.values(), key=lambda item: (item.name, item.node_id))

    def _observed_item_from_evidence_state(self, evidence: EvidenceState) -> ObservedEvidenceItem:
        metadata = dict(evidence.metadata)
        name = str(
            metadata.get("target_node_name")
            or metadata.get("normalized_name")
            or metadata.get("graph_grounded_canonical_name")
            or evidence.node_id
        )
        return ObservedEvidenceItem(
            node_id=evidence.node_id,
            name=name,
            label=self._metadata_label(metadata),
            polarity=evidence.effective_polarity(),
            resolution=evidence.resolution,
            existence=evidence.existence,
            source="evidence_state",
            source_turns=list(evidence.source_turns),
            metadata=metadata,
        )

    def _observed_item_from_slot(self, slot: SlotState) -> ObservedEvidenceItem:
        metadata = dict(slot.metadata)
        name = str(
            metadata.get("normalized_name")
            or metadata.get("target_node_name")
            or metadata.get("graph_grounded_canonical_name")
            or slot.value
            or slot.node_id
        )
        return ObservedEvidenceItem(
            node_id=slot.node_id,
            name=name,
            label=self._metadata_label(metadata),
            polarity=slot.effective_polarity(),
            resolution=slot.resolution,
            status=slot.status,
            source="slot",
            source_turns=list(slot.source_turns),
            metadata=metadata,
        )

    def _metadata_label(self, metadata: dict[str, Any]) -> str:
        for key in ("target_node_label", "linked_label", "graph_grounded_label", "label"):
            value = str(metadata.get(key) or "").strip()
            if len(value) > 0:
                return value
        return ""

    def _is_simulated_evidence(self, metadata: dict[str, Any]) -> bool:
        source_stage = str(metadata.get("source_stage") or "").lower()
        source = str(metadata.get("source") or "").lower()
        return (
            bool(metadata.get("simulated", False))
            or bool(metadata.get("rollout_simulated", False))
            or "rollout" in source_stage
            or "simulation" in source_stage
            or source in {"rollout", "simulation", "simulated_trajectory"}
        )

    def _build_match_frequency(
        self,
        observed_items: Sequence[ObservedEvidenceItem],
        hypotheses: Sequence[HypothesisScore],
    ) -> dict[str, int]:
        frequency: dict[str, int] = {}

        for item in observed_items:
            count = 0
            for hypothesis in hypotheses:
                if self._best_payload_match(item, hypothesis) is not None:
                    count += 1
            frequency[item.node_id] = max(count, 1)

        return frequency

    def _summarize_candidate(
        self,
        hypothesis: HypothesisScore,
        observed_items: Sequence[ObservedEvidenceItem],
        match_frequency: dict[str, int],
    ) -> dict:
        strong: list[dict] = []
        provisional: list[dict] = []
        background: list[dict] = []
        negative: list[dict] = []
        observed_families: set[str] = set()

        for item in observed_items:
            payload = self._best_payload_match(item, hypothesis)
            scoped_to_hypothesis = str(item.metadata.get("hypothesis_id") or "") == hypothesis.node_id

            if payload is None and not scoped_to_hypothesis:
                continue

            payload = dict(payload or {})
            relation_type = str(payload.get("relation_type") or item.metadata.get("relation_type") or "")
            label = str(payload.get("label") or item.label or "")
            evidence_name = str(payload.get("name") or item.name or item.node_id)
            evidence_tags = self._evidence_tags(item, payload, label)
            observed_families.update(tag for tag in evidence_tags if not tag.startswith("type:"))
            score = self._anchor_score(
                item,
                relation_type=relation_type,
                label=label,
                disease_degree=max(
                    int(payload.get("disease_degree") or 0),
                    match_frequency.get(item.node_id, self.config.default_disease_degree),
                ),
            )
            compact = {
                "node_id": item.node_id,
                "name": evidence_name,
                "observed_name": item.name,
                "label": label,
                "polarity": item.polarity,
                "resolution": item.resolution,
                "relation_type": relation_type,
                "anchor_score": round(score, 4),
                "source": item.source,
                "source_turns": list(item.source_turns),
                "evidence_tags": sorted(evidence_tags),
            }

            if self._is_negative_anchor(item, relation_type, label):
                negative.append(compact)
                continue

            if item.polarity != "present":
                continue

            if self._is_background_evidence(evidence_name, hypothesis.name):
                background.append(compact)
                continue

            if self._is_high_value_anchor(label, relation_type):
                if item.resolution == "clear":
                    strong.append(compact)
                else:
                    provisional.append(compact)
                continue

            if len(evidence_tags & {"pathogen", "imaging", "oxygenation", "pcp_specific"}) > 0:
                if item.resolution == "clear":
                    strong.append(compact)
                else:
                    provisional.append(compact)
            else:
                background.append(compact)

        strong_score = sum(float(item.get("anchor_score", 0.0)) for item in strong)
        provisional_score = sum(float(item.get("anchor_score", 0.0)) for item in provisional)
        background_score = sum(float(item.get("anchor_score", 0.0)) for item in background)
        negative_score = sum(float(item.get("anchor_score", 0.0)) for item in negative)
        tier = self._select_anchor_tier(strong, provisional, background, negative)
        missing_families, minimum_groups_available = self._minimum_family_gaps(hypothesis, observed_families)

        return {
            "observed_anchor_score": round(strong_score + provisional_score * 0.65, 4),
            "strong_anchor_score": round(strong_score, 4),
            "provisional_anchor_score": round(provisional_score, 4),
            "background_support_score": round(background_score, 4),
            "anchor_negative_score": round(negative_score, 4),
            "anchor_tier": tier,
            "anchor_supporting_evidence": strong,
            "provisional_anchor_evidence": provisional,
            "background_supporting_evidence": background,
            "anchor_negative_evidence": negative,
            "observed_evidence_families": sorted(observed_families),
            "anchor_missing_evidence_families": missing_families,
            "minimum_evidence_groups_available": minimum_groups_available,
            "minimum_evidence_family_coverage_satisfied": len(missing_families) == 0,
        }

    def _apply_summary_to_hypothesis(self, hypothesis: HypothesisScore, summary: dict) -> HypothesisScore:
        existing_metadata = dict(hypothesis.metadata)
        base_score = float(existing_metadata.get("pre_anchor_score", hypothesis.score))
        bonus = (
            float(summary.get("strong_anchor_score", 0.0)) * self.config.strong_anchor_bonus
            + float(summary.get("provisional_anchor_score", 0.0)) * self.config.provisional_anchor_bonus
            + float(summary.get("background_support_score", 0.0)) * self.config.background_support_bonus
            - float(summary.get("anchor_negative_score", 0.0)) * self.config.negative_anchor_penalty
        )
        metadata = {
            **existing_metadata,
            **summary,
            "observed_anchor_rank_bonus": round(bonus, 4),
            "pre_anchor_score": base_score,
        }
        return HypothesisScore(
            node_id=hypothesis.node_id,
            label=hypothesis.label,
            name=hypothesis.name,
            score=max(base_score + bonus, 0.0),
            evidence_node_ids=list(hypothesis.evidence_node_ids),
            metadata=metadata,
        )

    def _build_anchor_index(
        self,
        observed_items: Sequence[ObservedEvidenceItem],
        ranked: Sequence[HypothesisScore],
        candidate_summaries: dict[str, dict],
    ) -> dict:
        candidates = []

        for hypothesis in ranked:
            summary = candidate_summaries.get(hypothesis.node_id, {})
            candidates.append(
                {
                    "candidate_id": hypothesis.node_id,
                    "candidate_name": hypothesis.name,
                    "anchor_tier": str(summary.get("anchor_tier") or "speculative"),
                    "observed_anchor_score": float(summary.get("observed_anchor_score", 0.0)),
                    "background_support_score": float(summary.get("background_support_score", 0.0)),
                    "anchor_negative_score": float(summary.get("anchor_negative_score", 0.0)),
                    "anchor_supporting_evidence": summary.get("anchor_supporting_evidence", []),
                    "anchor_negative_evidence": summary.get("anchor_negative_evidence", []),
                }
            )

        return {
            "source": "observed_session_state",
            "observed_evidence": [
                {
                    "node_id": item.node_id,
                    "name": item.name,
                    "label": item.label,
                    "polarity": item.polarity,
                    "resolution": item.resolution,
                    "source": item.source,
                    "source_turns": list(item.source_turns),
                }
                for item in observed_items
            ],
            "candidate_anchor_summary": candidates,
            "strong_anchor_candidates": [
                item for item in candidates if str(item.get("anchor_tier") or "") == "strong_anchor"
            ],
            "anchored_candidate_ids": [
                str(item.get("candidate_id"))
                for item in candidates
                if str(item.get("anchor_tier") or "") in {"strong_anchor", "provisional_anchor"}
            ],
        }

    def _best_payload_match(self, item: ObservedEvidenceItem, hypothesis: HypothesisScore) -> dict | None:
        best_score = 0.0
        best_payload: dict | None = None

        for payload in self._candidate_payloads(hypothesis):
            match_score = self._payload_match_score(item, payload)

            if match_score > best_score:
                best_score = match_score
                best_payload = payload

        if best_score >= 0.72:
            return best_payload

        return None

    def _candidate_payloads(self, hypothesis: HypothesisScore) -> list[dict]:
        metadata = dict(hypothesis.metadata)
        payloads = [
            dict(item)
            for item in metadata.get("evidence_payloads", [])
            if isinstance(item, dict)
        ]

        if len(payloads) == 0:
            evidence_names = [str(item) for item in metadata.get("evidence_names", [])]
            evidence_node_ids = [str(item) for item in metadata.get("evidence_node_ids", [])]
            evidence_labels = [str(item) for item in metadata.get("evidence_labels", [])]
            relation_types = [str(item) for item in metadata.get("relation_types", [])]
            for index, name in enumerate(evidence_names):
                payloads.append(
                    {
                        "name": name,
                        "node_id": evidence_node_ids[index] if index < len(evidence_node_ids) else "",
                        "label": evidence_labels[index] if index < len(evidence_labels) else "",
                        "relation_type": relation_types[index] if index < len(relation_types) else "",
                    }
                )

        return payloads

    def _payload_match_score(self, item: ObservedEvidenceItem, payload: dict) -> float:
        payload_node_id = str(payload.get("node_id") or "").strip()

        if len(payload_node_id) > 0 and payload_node_id == item.node_id:
            return 1.0

        item_name = self._normalize_text(item.name)
        payload_name = self._normalize_text(str(payload.get("name") or ""))

        if len(item_name) == 0 or len(payload_name) == 0:
            return 0.0

        if item_name == payload_name:
            return 0.96

        if item_name in payload_name or payload_name in item_name:
            return 0.88

        item_tokens = self._semantic_tokens(item_name)
        payload_tokens = self._semantic_tokens(payload_name)

        if len(item_tokens) == 0 or len(payload_tokens) == 0:
            return 0.0

        overlap = len(item_tokens & payload_tokens) / len(item_tokens | payload_tokens)
        return overlap

    def _anchor_score(
        self,
        item: ObservedEvidenceItem,
        *,
        relation_type: str,
        label: str,
        disease_degree: int,
    ) -> float:
        confidence = 0.0

        if item.polarity == "present" and item.resolution == "clear":
            confidence = self.config.present_clear_confidence
        elif item.polarity == "present":
            confidence = self.config.present_hedged_confidence
        elif item.polarity == "absent" and item.resolution == "clear":
            confidence = self.config.absent_clear_confidence

        relation_weight = self.config.relation_weights.get(relation_type, 0.35)
        label_prior = self.config.label_priors.get(label, 0.3)
        degree = max(disease_degree, self.config.default_disease_degree)
        specificity = 1.0 / math.log2(2 + degree)
        return confidence * relation_weight * label_prior * specificity

    def _is_high_value_anchor(self, label: str, relation_type: str) -> bool:
        if label in STRONG_ANCHOR_LABELS:
            return True

        return label == "ClinicalAttribute" and relation_type in DETAIL_DEFINITION_RELATION_TYPES

    def _is_negative_anchor(self, item: ObservedEvidenceItem, relation_type: str, label: str) -> bool:
        if item.polarity != "absent" or item.resolution != "clear":
            return False

        return relation_type in ANCHOR_RELATION_TYPES or self._is_high_value_anchor(label, relation_type)

    def _is_background_evidence(self, evidence_name: str, candidate_name: str) -> bool:
        evidence_text = self._normalize_text(evidence_name)
        candidate_text = self._normalize_text(candidate_name)

        if self._is_hiv_specific_marker(evidence_text):
            return not any(keyword in candidate_text for keyword in ("hiv", "艾滋", "获得性免疫缺陷"))

        return any(
            keyword in evidence_text
            for keyword in (
                "cd4",
                "t淋巴",
                "免疫功能低下",
                "免疫抑制",
                "hiv感染者",
                "hivaids",
                "发热",
                "发烧",
                "年龄",
                "性别",
            )
        )

    def _is_hiv_specific_marker(self, text: str) -> bool:
        return any(keyword in text for keyword in ("hivrna", "hiv1", "hiv抗体", "病毒载量"))

    def _evidence_tags(self, item: ObservedEvidenceItem, payload: dict, label: str) -> set[str]:
        tags = {
            str(tag)
            for tag in item.metadata.get("evidence_tags", [])
            if len(str(tag).strip()) > 0
        }
        question_type = str(payload.get("question_type_hint") or item.metadata.get("question_type_hint") or "")

        if question_type:
            tags.add(f"type:{question_type}")

        if label in {"LabFinding", "LabTest"}:
            tags.add("type:lab")
        elif label == "ImagingFinding":
            tags.add("imaging")
            tags.add("type:imaging")
        elif label == "Pathogen":
            tags.add("pathogen")
            tags.add("type:pathogen")
        elif label == "ClinicalAttribute":
            tags.add("detail")
            tags.add("type:detail")

        normalized = self._normalize_text(str(payload.get("name") or item.name))
        family_rules = {
            "immune_status": ("cd4", "t淋巴", "免疫", "hiv感染"),
            "pathogen": ("病原", "病毒", "细菌", "真菌", "pcr", "核酸", "阳性", "检出"),
            "imaging": ("ct", "胸片", "影像", "磨玻璃", "mri"),
            "oxygenation": ("低氧", "血氧", "氧分压", "pao2", "spo2"),
            "respiratory": ("咳", "气促", "呼吸困难", "胸闷"),
            "tuberculosis": ("结核", "抗酸", "分枝杆菌", "mtb", "xpert"),
            "pcp_specific": ("肺孢子", "pcp", "pneumocystis", "βd葡聚糖", "bdg"),
        }

        for family, keywords in family_rules.items():
            if any(keyword in normalized for keyword in keywords):
                tags.add(family)

        return tags

    def _minimum_family_gaps(self, hypothesis: HypothesisScore, observed_families: set[str]) -> tuple[list[str], bool]:
        metadata = dict(hypothesis.metadata)
        raw_groups = metadata.get("minimum_evidence_groups") or metadata.get("minimum_evidence_family_groups") or []
        gaps: list[str] = []
        parsed_count = 0

        if not isinstance(raw_groups, list):
            return gaps, False

        for raw_group in raw_groups:
            if isinstance(raw_group, str):
                group = {raw_group}
            elif isinstance(raw_group, list):
                group = {str(item) for item in raw_group if len(str(item).strip()) > 0}
            else:
                continue

            if len(group) == 0:
                continue

            parsed_count += 1
            if len(group & observed_families) == 0:
                gaps.append("/".join(sorted(group)))

        return gaps, parsed_count > 0

    def _select_anchor_tier(
        self,
        strong: Sequence[dict],
        provisional: Sequence[dict],
        background: Sequence[dict],
        negative: Sequence[dict],
    ) -> str:
        if len(strong) > 0:
            return "strong_anchor"
        if len(provisional) > 0:
            return "provisional_anchor"
        if len(background) > 0:
            return "background_supported"
        if len(negative) > 0:
            return "negative_anchor"
        return "speculative"

    def _anchor_tier_priority(self, tier: str) -> int:
        return {
            "strong_anchor": 4,
            "provisional_anchor": 3,
            "background_supported": 2,
            "speculative": 1,
            "negative_anchor": 0,
        }.get(tier, 1)

    def _observed_item_priority(self, item: ObservedEvidenceItem) -> int:
        if item.polarity == "present" and item.resolution == "clear":
            return 4
        if item.polarity == "present":
            return 3
        if item.polarity == "unclear":
            return 2
        return 1

    def _normalize_text(self, text: str) -> str:
        return (
            str(text)
            .strip()
            .lower()
            .replace(" ", "")
            .replace("（", "(")
            .replace("）", ")")
            .replace("-", "")
            .replace("_", "")
            .replace("/", "")
            .replace("+", "")
        )

    def _semantic_tokens(self, text: str) -> set[str]:
        return {
            token
            for token in re.split(r"感染|疾病|综合征|阳性|阴性|检测|抗体|rna|dna|病毒|细菌|真菌|计数|<|>|小于|大于|低于|升高|降低", text)
            if len(token) >= 2
        }
