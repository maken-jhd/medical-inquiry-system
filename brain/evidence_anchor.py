"""基于真实会话证据计算诊断候选的 observed anchor。"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any, Sequence

from .types import EvidenceState, HypothesisScore, SessionState, SlotState


# 这些标签通常代表病原、检查结果或检查项目本身；
# 一旦在真实会话里被明确命中，后续会优先作为强锚点或准强锚点参与排序。
STRONG_ANCHOR_LABELS = {"Pathogen", "LabFinding", "ImagingFinding", "LabTest"}

# observed anchor 需要进一步区分“精确命中 / 同族命中 / 背景命中 / 竞争性命中”。
ANCHOR_SCOPE_EXACT = "exact_scope"
ANCHOR_SCOPE_FAMILY = "family_scope"
ANCHOR_SCOPE_PROVISIONAL = "provisional_scope"
ANCHOR_SCOPE_PHENOTYPE = "phenotype_scope"
ANCHOR_SCOPE_BACKGROUND = "background_scope"
ANCHOR_SCOPE_COMPETING = "competing_scope"

# 这组关系表示“疾病 <-> 证据”之间较强的诊断、确认或检查支持链路；
# 命中这些关系时，会优先把证据视作 anchor 候选，而不是普通背景线索。
ANCHOR_RELATION_TYPES = {
    "HAS_PATHOGEN",
    "DIAGNOSED_BY",
    "HAS_LAB_FINDING",
    "HAS_IMAGING_FINDING",
    "CONFIRMED_BY",
    "DETECTED_BY",
}

# 这组关系更偏“诊断标准 / 定义性细节”；
# 命中后即使不是病原或影像，也可能进入 definition_anchor 分支。
DETAIL_DEFINITION_RELATION_TYPES = {"DIAGNOSED_BY", "REQUIRES_DETAIL"}

# 当真实会话直接提到某个疾病节点自身时，不走普通 KG 证据边，
# 而是统一挂一个伪关系类型，方便后续按“疾病自身命中”处理强锚点。
DISEASE_SELF_ANCHOR_RELATION_TYPE = "SELF_DISEASE_MATCH"

# 这些 family tag 大多是高连接、低区分度的背景信息；
# 它们可以提供背景支持，但不能单独构成足以接受最终答案的强锚点。
BACKGROUND_FAMILY_TAGS = {
    "immune_status",
    "underlying_infection",
    "general_risk",
    "constitutional_symptom",
}

# low-cost evidence profile 只统计“低成本且有区分度”的真实阳性线索；
# 因此需要把背景族、viral load、人群泛风险这类共享信号排除掉。
LOW_COST_PROFILE_EXCLUDED_FAMILY_TAGS = {
    *BACKGROUND_FAMILY_TAGS,
    "viral_load",
    "population_risk",
}

# 这些 family tag 代表病原、影像、特异实验室检查、病理等更能区分疾病的证据族；
# 只要真实会话命中它们，通常会优先归入 disease_specific_anchor。
SPECIFIC_ANCHOR_FAMILY_TAGS = {
    "pathogen",
    "imaging",
    "disease_specific_lab",
    "serology",
    "tumor_marker",
    "pathology",
    "oxygenation",
}

# 这些 family tag 更接近定义阈值、诊断标准或关键细节要求；
# 命中后即使不是最强病原证据，也会优先进入 definition_anchor 判断。
DEFINITION_FAMILY_TAGS = {
    "detail",
    "metabolic_definition",
}


@dataclass
class EvidenceAnchorConfig:
    """保存 observed anchor 计算的通用权重。"""

    strong_anchor_bonus: float = 2.4
    provisional_anchor_bonus: float = 0.95
    family_anchor_bonus: float = 1.1
    background_support_bonus: float = 0.10
    negative_anchor_penalty: float = 1.45
    background_attractor_penalty: float = 0.82
    scope_mismatch_penalty: float = 0.92
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
            DISEASE_SELF_ANCHOR_RELATION_TYPE: 1.0,
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
            "Disease": 1.0,
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
                -float(item.metadata.get("exact_scope_anchor_score", 0.0)),
                -float(item.metadata.get("family_scope_anchor_score", 0.0)),
                -float(item.metadata.get("role_specificity_score", 0.0)),
                -float(item.metadata.get("scope_specificity_score", 0.0)),
                -float(item.metadata.get("observed_anchor_score", 0.0)),
                float(item.metadata.get("background_attractor_score", 0.0)),
                float(item.metadata.get("scope_mismatch_score", 0.0)),
                float(item.metadata.get("generic_scope_penalty", 0.0)),
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

    # 给 observed anchor 计算一个“作用域系数”，避免背景命中和精确命中的权重混在一起。
    def _anchor_scope_weight(self, scope: str) -> float:
        return {
            ANCHOR_SCOPE_EXACT: 1.0,
            ANCHOR_SCOPE_FAMILY: 0.72,
            ANCHOR_SCOPE_PROVISIONAL: 0.58,
            ANCHOR_SCOPE_PHENOTYPE: 0.35,
            ANCHOR_SCOPE_COMPETING: 0.24,
            ANCHOR_SCOPE_BACKGROUND: 0.12,
        }.get(scope, 0.2)

    # 将原始 role 收敛成更稳定的作用域标签，供 A2 / stop / repair 共用。
    def _scope_adjusted_role(self, role: str, scope: str) -> str:
        if scope == ANCHOR_SCOPE_BACKGROUND:
            return "background_context"

        if scope in {ANCHOR_SCOPE_FAMILY, ANCHOR_SCOPE_COMPETING} and role in {"disease_specific_anchor", "definition_anchor"}:
            return "family_anchor"

        return role

    # 根据候选名、证据名与关系类型，判断这条证据是精确命中、同族命中还是背景命中。
    def _infer_anchor_scope(
        self,
        *,
        item: ObservedEvidenceItem,
        payload: dict,
        label: str,
        relation_type: str,
        evidence_name: str,
        candidate_name: str,
        evidence_tags: set[str],
        role: str,
    ) -> tuple[str, str]:
        evidence_text = self._normalize_text(evidence_name)
        candidate_text = self._normalize_text(candidate_name)
        payload_name = self._normalize_text(str(payload.get("name") or ""))
        exact_name_match = (
            len(evidence_text) > 0
            and len(candidate_text) > 0
            and (evidence_text == candidate_text or evidence_text in candidate_text or candidate_text in evidence_text)
        )
        scope_gap = self._clinical_scope_gap(candidate_text, evidence_text, payload_name)

        if role == "background_context":
            return ANCHOR_SCOPE_BACKGROUND, "role_background_context"

        if relation_type == DISEASE_SELF_ANCHOR_RELATION_TYPE:
            return ANCHOR_SCOPE_EXACT, "disease_self_match"

        if self._is_background_evidence(evidence_name, candidate_name, evidence_tags, max(int(payload.get("disease_degree") or 0), 1)):
            return ANCHOR_SCOPE_BACKGROUND, "background_keyword_or_family"

        if scope_gap is not None:
            return scope_gap

        if label == "Pathogen" or relation_type == "HAS_PATHOGEN" or "type:pathogen" in evidence_tags:
            if relation_type in {"RISK_FACTOR_FOR", "APPLIES_TO", "COMPLICATED_BY"}:
                return ANCHOR_SCOPE_BACKGROUND, "pathogen_as_background_or_comorbidity"
            if self._is_pathogen_family_scope(candidate_text, evidence_text, payload_name):
                if self._is_pathogen_exact_scope(candidate_text, evidence_text, payload_name):
                    return ANCHOR_SCOPE_EXACT, "pathogen_exact_match"
                return ANCHOR_SCOPE_FAMILY, "pathogen_family_match"
            return ANCHOR_SCOPE_BACKGROUND, "pathogen_not_named_in_candidate"

        if exact_name_match:
            return ANCHOR_SCOPE_EXACT, "normalized_name_match"

        # 定义性关系本身就代表“这条细节值得拿来约束候选”，即使名称还没和疾病名精确对齐。
        if relation_type in DETAIL_DEFINITION_RELATION_TYPES:
            return ANCHOR_SCOPE_EXACT, "definition_relation_direct"

        if label in {"LabFinding", "LabTest", "ImagingFinding", "ClinicalAttribute"}:
            if self._has_site_specificity_tokens(candidate_text) and self._has_site_specificity_tokens(evidence_text):
                return ANCHOR_SCOPE_EXACT, "site_specific_detail_match"

            if len(evidence_tags & SPECIFIC_ANCHOR_FAMILY_TAGS) > 0 or relation_type in ANCHOR_RELATION_TYPES:
                return ANCHOR_SCOPE_FAMILY, "specific_family_match"

        if label == "ClinicalFinding" or relation_type == "MANIFESTS_AS":
            return ANCHOR_SCOPE_PHENOTYPE, "phenotype_support"

        if len(evidence_tags & SPECIFIC_ANCHOR_FAMILY_TAGS) > 0:
            return ANCHOR_SCOPE_FAMILY, "specific_family_tag_match"

        return ANCHOR_SCOPE_BACKGROUND, "fallback_background"

    # 判断候选疾病的临床作用域是否被当前 observed evidence 真正覆盖。
    # 这一步不是疾病补丁，而是防止“局部证据 -> 播散/泛化诊断”或“属级病原 -> 具体种/部位诊断”。
    def _clinical_scope_gap(self, candidate_text: str, evidence_text: str, payload_name: str) -> tuple[str, str] | None:
        candidate_tokens = self._clinical_scope_tokens(candidate_text)
        evidence_tokens = self._clinical_scope_tokens(" ".join([evidence_text, payload_name]))
        critical_candidate_tokens = self._critical_scope_tokens(candidate_tokens)

        if len(critical_candidate_tokens) > 0:
            missing = [
                token
                for token in sorted(critical_candidate_tokens)
                if not self._scope_token_covered(token, evidence_tokens)
            ]
            if len(missing) > 0:
                reason = "candidate_scope_not_observed:" + ",".join(missing)
                if self._has_specific_scope_tokens(evidence_tokens):
                    return ANCHOR_SCOPE_COMPETING, reason
                return ANCHOR_SCOPE_FAMILY, reason

        if self._is_generic_candidate_scope(candidate_text, candidate_tokens) and self._has_specific_scope_tokens(evidence_tokens):
            return ANCHOR_SCOPE_FAMILY, "observed_evidence_more_specific_than_candidate"

        return None

    # 将候选疾病名和真实证据名的 scope facet 汇总成最终粒度画像。
    # 它服务于“泛疾病 vs 部位特异疾病 / 基础感染 vs IRIS / 局部 vs 播散”的通用裁决。
    def _build_candidate_scope_profile(
        self,
        *,
        candidate_name: str,
        observed_scope_facets: set[str],
        scope_mismatch_reasons: Sequence[str],
        has_observed_anchor: bool,
    ) -> dict:
        candidate_text = self._normalize_text(candidate_name)
        candidate_facets = self._clinical_scope_tokens(candidate_text)
        candidate_critical = self._critical_scope_tokens(candidate_facets)
        observed_critical = self._critical_scope_tokens(observed_scope_facets)
        missing_facets = [
            token
            for token in sorted(candidate_critical)
            if not self._scope_token_covered(token, observed_scope_facets)
        ]

        covered_facets = [
            token
            for token in sorted(candidate_critical)
            if self._scope_token_covered(token, observed_scope_facets)
        ]
        coverage_ratio = len(covered_facets) / len(candidate_critical) if len(candidate_critical) > 0 else 0.0
        observed_site_facets = observed_critical & {"lung", "cns", "skin", "gi", "eye", "liver", "cervix", "mediastinum"}
        candidate_site_facets = candidate_critical & {"lung", "cns", "skin", "gi", "eye", "liver", "cervix", "mediastinum"}
        observed_more_specific = (
            self._is_generic_candidate_scope(candidate_text, candidate_facets) and len(observed_critical) > 0
        ) or (len(observed_site_facets) > 0 and len(candidate_site_facets) == 0)
        generic_scope_penalty = 0.38 if observed_more_specific else 0.0
        scope_requirement_missing_score = 0.0

        if has_observed_anchor and len(missing_facets) > 0:
            scope_requirement_missing_score = min(len(missing_facets) * 0.18, 0.54)

        if "iris" in missing_facets:
            scope_requirement_missing_score += 0.22

        if "disseminated" in missing_facets:
            scope_requirement_missing_score += 0.18

        scope_specificity_score = 0.0
        if len(candidate_critical) > 0:
            scope_specificity_score = coverage_ratio * 0.7 + min(len(covered_facets) * 0.08, 0.24)
        elif observed_more_specific:
            scope_specificity_score = 0.0

        return {
            "candidate_scope_facets": sorted(candidate_facets),
            "observed_scope_facets": sorted(observed_scope_facets),
            "missing_scope_facets": missing_facets,
            "covered_scope_facets": covered_facets,
            "scope_specificity_score": round(scope_specificity_score, 4),
            "generic_scope_penalty": round(generic_scope_penalty, 4),
            "scope_requirement_missing_score": round(scope_requirement_missing_score, 4),
            "scope_mismatch_reasons": list(scope_mismatch_reasons),
        }

    # 把疾病名和证据名里的部位、病程、病原精度等信息压成通用 scope token。
    def _clinical_scope_tokens(self, text: str) -> set[str]:
        normalized = self._normalize_text(text)
        tokens: set[str] = set()

        scope_rules = {
            "lung": ("肺", "肺炎", "胸部", "支气管", "痰", "呼吸道", "肺泡灌洗", "bal", "balf"),
            "cns": ("脑", "脑炎", "脑膜", "脑脊液", "颅内", "中枢", "神经"),
            "skin": ("皮肤", "皮损", "皮疹", "软组织", "溃疡", "皮肤分泌物"),
            "gi": ("食管", "结肠", "肠炎", "胃肠", "肠道"),
            "eye": ("视网膜", "眼", "眼底"),
            "liver": ("肝", "肝炎", "肝癌", "肝细胞"),
            "cervix": ("宫颈",),
            "mediastinum": ("纵隔",),
            "disseminated": ("播散", "全身", "多器官", "血培养", "血液培养", "血液分枝杆菌"),
            "iris": ("免疫重建", "iris", "抗病毒治疗后", "抗逆转录病毒治疗", "art", "art后", "病情稳定时突然恶化", "近期恶化", "重新恶化"),
            "drug_resistant": ("耐药", "利福平"),
            "malignancy": ("癌", "肉瘤", "淋巴瘤", "肿瘤", "恶性"),
            "active_tb": ("活动性结核",),
            "tb": ("结核", "mtb", "结核分枝杆菌", "xpert"),
            "ntm": ("非结核", "ntm", "龟分枝杆菌", "偶然分枝杆菌", "脓肿分枝杆菌"),
            "mycobacteria": ("分枝杆菌", "抗酸"),
        }

        for token, keywords in scope_rules.items():
            if any(keyword in normalized for keyword in keywords):
                tokens.add(token)

        return tokens

    def _critical_scope_tokens(self, tokens: set[str]) -> set[str]:
        return {
            token
            for token in tokens
            if token
            in {
                "lung",
                "cns",
                "skin",
                "gi",
                "eye",
                "liver",
                "cervix",
                "mediastinum",
                "disseminated",
                "iris",
                "drug_resistant",
                "malignancy",
                "active_tb",
                "tb",
                "ntm",
            }
        }

    def _scope_token_covered(self, token: str, evidence_tokens: set[str]) -> bool:
        if token == "disseminated":
            site_tokens = evidence_tokens & {"lung", "cns", "skin", "gi", "eye", "liver", "cervix", "mediastinum"}
            return "disseminated" in evidence_tokens or len(site_tokens) >= 2

        if token == "active_tb":
            return "active_tb" in evidence_tokens or ("tb" in evidence_tokens and "ntm" not in evidence_tokens)

        if token == "tb":
            return "tb" in evidence_tokens or "active_tb" in evidence_tokens

        if token == "ntm":
            return "ntm" in evidence_tokens

        return token in evidence_tokens

    def _has_specific_scope_tokens(self, tokens: set[str]) -> bool:
        return len(self._critical_scope_tokens(tokens)) > 0 or "mycobacteria" in tokens

    def _is_generic_candidate_scope(self, candidate_text: str, candidate_tokens: set[str]) -> bool:
        if len(self._critical_scope_tokens(candidate_tokens)) > 0:
            return False

        return any(keyword in candidate_text for keyword in ("感染", "病", "阳性"))

    # 病原证据如果只命中到同一病原家族但没有落到同一部位/同一疾病定义，应降为 family scope。
    def _is_pathogen_family_scope(self, candidate_text: str, evidence_text: str, payload_name: str) -> bool:
        if len(candidate_text) == 0 or len(evidence_text) == 0:
            return False

        if self._is_pathogen_exact_scope(candidate_text, evidence_text, payload_name):
            return True

        if evidence_text in candidate_text and self._has_site_specificity_tokens(candidate_text):
            return True

        pathogen_tokens = self._pathogen_family_tokens(evidence_text)
        if len(pathogen_tokens) == 0:
            return False

        if not any(token in candidate_text for token in pathogen_tokens):
            return False

        return self._has_site_specificity_tokens(candidate_text)

    # 如果候选本身只是“X感染 / X阳性”这一层，不带明确部位，病原证据可以视作更接近 exact scope。
    def _is_pathogen_exact_scope(self, candidate_text: str, evidence_text: str, payload_name: str) -> bool:
        if len(candidate_text) == 0 or len(evidence_text) == 0:
            return False

        if evidence_text == payload_name and not self._has_site_specificity_tokens(candidate_text):
            return True

        if evidence_text in candidate_text and not self._has_site_specificity_tokens(candidate_text):
            return True

        pathogen_tokens = self._pathogen_family_tokens(evidence_text)
        if len(pathogen_tokens) == 0:
            return False

        return any(token in candidate_text for token in pathogen_tokens) and not self._has_site_specificity_tokens(candidate_text)

    # 判断是否出现了明显的部位 / 病种细化词，用于区分 family 和 exact scope。
    def _has_site_specificity_tokens(self, text: str) -> bool:
        if len(text) == 0:
            return False

        site_tokens = (
            "脑膜",
            "脑炎",
            "脑病",
            "肺炎",
            "肺部",
            "肺孢子",
            "呼吸道",
            "食管",
            "结肠",
            "肠炎",
            "胃炎",
            "肝炎",
            "肝癌",
            "胆道",
            "胰腺",
            "心内膜",
            "骨髓",
            "视网膜",
            "眼",
            "皮肤",
            "软组织",
            "尿路",
            "泌尿",
        )
        return any(token in text for token in site_tokens)

    # 把常见病原名字拆成一个小的同族词表，避免同一种病原的不同部位都被当成 exact。
    def _pathogen_family_tokens(self, text: str) -> set[str]:
        rules = (
            ("水痘带状疱疹", ("水痘带状疱疹", "带状疱疹", "vzv")),
            ("巨细胞病毒", ("巨细胞病毒", "cmv")),
            ("弓形虫", ("弓形虫", "toxoplasma", "toxoplasmagondii", "弓形虫病")),
            ("隐球菌", ("隐球菌", "cryptococcus")),
            ("结核分枝杆菌", ("结核", "分枝杆菌", "mtb", "xpert", "抗酸")),
            ("肺孢子菌", ("肺孢子", "pcp", "pjp", "pneumocystis")),
            ("人类疱疹病毒8", ("hhv8", "人类疱疹病毒8", "卡波西")),
            ("乙肝病毒", ("乙肝", "hbv", "乙型肝炎")),
            ("丙肝病毒", ("丙肝", "hcv", "丙型肝炎")),
            ("hiv", ("hiv", "艾滋", "获得性免疫缺陷")),
            ("新冠", ("新冠", "sarscov2", "covid")),
        )
        values: set[str] = set()

        for family, keywords in rules:
            if any(keyword in text for keyword in keywords):
                values.add(family)

        return values

    def _summarize_candidate(
        self,
        hypothesis: HypothesisScore,
        observed_items: Sequence[ObservedEvidenceItem],
        match_frequency: dict[str, int],
    ) -> dict:
        strong: list[dict] = []
        definition: list[dict] = []
        family_anchor: list[dict] = []
        provisional: list[dict] = []
        phenotype: list[dict] = []
        background: list[dict] = []
        negative: list[dict] = []
        low_cost_support: list[dict] = []
        low_cost_families: set[str] = set()
        observed_families: set[str] = set()
        observed_scope_facets: set[str] = set()
        scope_mismatch_reasons: list[str] = []
        scope_mismatch_score = 0.0

        for item in observed_items:
            payload = self._best_payload_match(item, hypothesis)

            # anchor 必须能回到候选自己的 KG evidence payload；
            # 仅靠历史 EvidenceState 上的 hypothesis_id 不能证明这条证据与该疾病直连。
            if payload is None:
                continue

            payload = dict(payload)
            relation_type = str(payload.get("relation_type") or item.metadata.get("relation_type") or "")
            label = str(payload.get("label") or item.label or "")
            evidence_name = str(payload.get("name") or item.name or item.node_id)
            evidence_tags = self._evidence_tags(item, payload, label)
            score = self._anchor_score(
                item,
                relation_type=relation_type,
                label=label,
                disease_degree=max(
                    int(payload.get("disease_degree") or 0),
                    match_frequency.get(item.node_id, self.config.default_disease_degree),
                ),
            )
            role = self._classify_evidence_role(
                item=item,
                relation_type=relation_type,
                label=label,
                evidence_name=evidence_name,
                candidate_name=hypothesis.name,
                evidence_tags=evidence_tags,
                disease_degree=max(
                    int(payload.get("disease_degree") or 0),
                    match_frequency.get(item.node_id, self.config.default_disease_degree),
                ),
            )
            scope, scope_reason = self._infer_anchor_scope(
                item=item,
                payload=payload,
                label=label,
                relation_type=relation_type,
                evidence_name=evidence_name,
                candidate_name=hypothesis.name,
                evidence_tags=evidence_tags,
                role=role,
            )
            if item.polarity == "present" and item.resolution in {"clear", "hedged"}:
                observed_scope_facets.update(self._clinical_scope_tokens(" ".join([evidence_name, item.name])))
            if scope == ANCHOR_SCOPE_COMPETING or scope_reason.startswith("candidate_scope_not_observed"):
                if scope_reason not in scope_mismatch_reasons:
                    scope_mismatch_reasons.append(scope_reason)
            adjusted_role = self._scope_adjusted_role(role, scope)
            scope_weight = self._anchor_scope_weight(scope)
            adjusted_score = round(score * scope_weight, 4)
            compact = {
                "node_id": item.node_id,
                "name": evidence_name,
                "observed_name": item.name,
                "label": label,
                "polarity": item.polarity,
                "resolution": item.resolution,
                "relation_type": relation_type,
                "evidence_role": adjusted_role,
                "raw_evidence_role": role,
                "anchor_scope": scope,
                "anchor_scope_reason": scope_reason,
                "anchor_scope_weight": round(scope_weight, 4),
                "raw_anchor_score": round(score, 4),
                "anchor_score": adjusted_score,
                "source": item.source,
                "source_turns": list(item.source_turns),
                "evidence_tags": sorted(evidence_tags),
                "acquisition_mode": str(payload.get("acquisition_mode") or item.metadata.get("acquisition_mode") or ""),
                "evidence_cost": str(payload.get("evidence_cost") or item.metadata.get("evidence_cost") or ""),
            }

            if self._is_negative_anchor(item, relation_type, label, role, scope):
                negative.append(compact)
                continue

            if item.polarity != "present":
                continue

            if item.resolution == "clear" and scope != ANCHOR_SCOPE_BACKGROUND:
                observed_families.update(self._canonical_family_tags(evidence_tags))
                if self._is_low_cost_profile_support(
                    item=item,
                    payload=payload,
                    label=label,
                    relation_type=relation_type,
                    role=adjusted_role,
                    evidence_tags=evidence_tags,
                ):
                    families = self._low_cost_profile_families(evidence_tags, adjusted_role)
                    low_cost_support.append({**compact, "low_cost_families": sorted(families)})
                    low_cost_families.update(families)

            if scope == ANCHOR_SCOPE_BACKGROUND:
                background.append(compact)
                continue

            if adjusted_role == "definition_anchor":
                if item.resolution == "clear":
                    definition.append(compact)
                else:
                    provisional.append(compact)
                continue

            if adjusted_role == "family_anchor":
                family_anchor.append(compact)
                if scope == ANCHOR_SCOPE_COMPETING:
                    scope_mismatch_score += adjusted_score
                continue

            if adjusted_role == "disease_specific_anchor":
                if item.resolution == "clear":
                    strong.append(compact)
                else:
                    provisional.append(compact)
                continue

            if adjusted_role == "phenotype_support":
                phenotype.append(compact)
            else:
                background.append(compact)

        strong_score = sum(float(item.get("anchor_score", 0.0)) for item in strong)
        definition_score = sum(float(item.get("anchor_score", 0.0)) for item in definition)
        family_score = sum(float(item.get("anchor_score", 0.0)) for item in family_anchor)
        provisional_score = sum(float(item.get("anchor_score", 0.0)) for item in provisional)
        phenotype_score = sum(float(item.get("anchor_score", 0.0)) for item in phenotype)
        background_score = sum(float(item.get("anchor_score", 0.0)) for item in background)
        negative_score = sum(float(item.get("anchor_score", 0.0)) for item in negative)
        exact_scope_total = strong_score + definition_score
        provisional_total = provisional_score * 0.65
        background_attractor_score = background_score if exact_scope_total + family_score == 0 else 0.0
        tier = self._select_anchor_tier(strong, definition, family_anchor, provisional, phenotype, background, negative)
        missing_families, minimum_groups_available = self._minimum_family_gaps(hypothesis, observed_families)
        low_cost_present_clear_count = len(low_cost_support)
        low_cost_core_family_count = len(low_cost_families)
        scope_profile = self._build_candidate_scope_profile(
            candidate_name=hypothesis.name,
            observed_scope_facets=observed_scope_facets,
            scope_mismatch_reasons=scope_mismatch_reasons,
            has_observed_anchor=(exact_scope_total + family_score + provisional_score + phenotype_score) > 0.0,
        )
        scope_mismatch_score += float(scope_profile["scope_requirement_missing_score"])

        return {
            "observed_anchor_score": round(exact_scope_total + family_score + provisional_total, 4),
            "exact_scope_anchor_score": round(exact_scope_total, 4),
            "family_scope_anchor_score": round(family_score, 4),
            "strong_anchor_score": round(strong_score, 4),
            "definition_anchor_score": round(definition_score, 4),
            "provisional_anchor_score": round(provisional_score, 4),
            "family_anchor_score": round(family_score, 4),
            "phenotype_support_score": round(phenotype_score, 4),
            "background_support_score": round(background_score, 4),
            "background_attractor_score": round(background_attractor_score, 4),
            "scope_mismatch_score": round(scope_mismatch_score, 4),
            "scope_specificity_score": round(float(scope_profile["scope_specificity_score"]), 4),
            "generic_scope_penalty": round(float(scope_profile["generic_scope_penalty"]), 4),
            "scope_requirement_missing_score": round(float(scope_profile["scope_requirement_missing_score"]), 4),
            "candidate_scope_facets": scope_profile["candidate_scope_facets"],
            "observed_scope_facets": scope_profile["observed_scope_facets"],
            "missing_scope_facets": scope_profile["missing_scope_facets"],
            "scope_mismatch_reasons": scope_profile["scope_mismatch_reasons"],
            "anchor_negative_score": round(negative_score, 4),
            "role_specificity_score": round(exact_scope_total + family_score * 0.8 + provisional_total + phenotype_score * 0.25, 4),
            "anchor_tier": tier,
            "anchor_scope": ANCHOR_SCOPE_EXACT if exact_scope_total > 0 else ANCHOR_SCOPE_FAMILY if family_score > 0 else ANCHOR_SCOPE_PROVISIONAL if provisional_score > 0 else ANCHOR_SCOPE_PHENOTYPE if phenotype_score > 0 else ANCHOR_SCOPE_BACKGROUND if background_score > 0 else ANCHOR_SCOPE_COMPETING if negative_score > 0 else "speculative",
            "anchor_supporting_evidence": strong + definition,
            "definition_anchor_evidence": definition,
            "family_anchor_evidence": family_anchor,
            "provisional_anchor_evidence": provisional,
            "phenotype_supporting_evidence": phenotype,
            "background_supporting_evidence": background,
            "anchor_negative_evidence": negative,
            "observed_evidence_families": sorted(observed_families),
            "anchor_missing_evidence_families": missing_families,
            "minimum_evidence_groups_available": minimum_groups_available,
            "minimum_evidence_family_coverage_satisfied": len(missing_families) == 0,
            "missing_evidence_roles": self._missing_evidence_roles(tier),
            "low_cost_supporting_evidence": low_cost_support,
            "low_cost_support_families": sorted(low_cost_families),
            "low_cost_core_family_count": low_cost_core_family_count,
            "low_cost_present_clear_count": low_cost_present_clear_count,
            "low_cost_profile_satisfied": low_cost_present_clear_count >= 2 and low_cost_core_family_count >= 2,
            "evidence_profile_acceptance_candidate": (
                tier not in {"strong_anchor", "definition_anchor", "family_anchor", "provisional_anchor", "negative_anchor"}
                and low_cost_present_clear_count >= 2
                and low_cost_core_family_count >= 2
            ),
        }

    def _apply_summary_to_hypothesis(self, hypothesis: HypothesisScore, summary: dict) -> HypothesisScore:
        existing_metadata = dict(hypothesis.metadata)
        base_score = float(existing_metadata.get("pre_anchor_score", hypothesis.score))
        bonus = (
            float(summary.get("strong_anchor_score", 0.0)) * self.config.strong_anchor_bonus
            + float(summary.get("definition_anchor_score", 0.0)) * self.config.strong_anchor_bonus
            + float(summary.get("family_anchor_score", 0.0)) * self.config.family_anchor_bonus
            + float(summary.get("provisional_anchor_score", 0.0)) * self.config.provisional_anchor_bonus
            + float(summary.get("phenotype_support_score", 0.0)) * 0.35
            + float(summary.get("background_support_score", 0.0)) * self.config.background_support_bonus
            + float(summary.get("scope_specificity_score", 0.0)) * 0.42
            - float(summary.get("anchor_negative_score", 0.0)) * self.config.negative_anchor_penalty
            - float(summary.get("background_attractor_score", 0.0)) * self.config.background_attractor_penalty
            - float(summary.get("scope_mismatch_score", 0.0)) * self.config.scope_mismatch_penalty
            - float(summary.get("generic_scope_penalty", 0.0)) * 0.85
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
                    "anchor_scope": str(summary.get("anchor_scope") or "speculative"),
                    "observed_anchor_score": float(summary.get("observed_anchor_score", 0.0)),
                    "exact_scope_anchor_score": float(summary.get("exact_scope_anchor_score", 0.0)),
                    "family_scope_anchor_score": float(summary.get("family_scope_anchor_score", 0.0)),
                    "role_specificity_score": float(summary.get("role_specificity_score", 0.0)),
                    "family_anchor_score": float(summary.get("family_anchor_score", 0.0)),
                    "background_support_score": float(summary.get("background_support_score", 0.0)),
                    "background_attractor_score": float(summary.get("background_attractor_score", 0.0)),
                    "scope_mismatch_score": float(summary.get("scope_mismatch_score", 0.0)),
                    "scope_specificity_score": float(summary.get("scope_specificity_score", 0.0)),
                    "generic_scope_penalty": float(summary.get("generic_scope_penalty", 0.0)),
                    "scope_requirement_missing_score": float(summary.get("scope_requirement_missing_score", 0.0)),
                    "candidate_scope_facets": summary.get("candidate_scope_facets", []),
                    "observed_scope_facets": summary.get("observed_scope_facets", []),
                    "missing_scope_facets": summary.get("missing_scope_facets", []),
                    "scope_mismatch_reasons": summary.get("scope_mismatch_reasons", []),
                    "anchor_negative_score": float(summary.get("anchor_negative_score", 0.0)),
                    "anchor_supporting_evidence": summary.get("anchor_supporting_evidence", []),
                    "definition_anchor_evidence": summary.get("definition_anchor_evidence", []),
                    "family_anchor_evidence": summary.get("family_anchor_evidence", []),
                    "phenotype_supporting_evidence": summary.get("phenotype_supporting_evidence", []),
                    "anchor_negative_evidence": summary.get("anchor_negative_evidence", []),
                    "low_cost_support_families": summary.get("low_cost_support_families", []),
                    "low_cost_core_family_count": int(summary.get("low_cost_core_family_count", 0) or 0),
                    "low_cost_present_clear_count": int(summary.get("low_cost_present_clear_count", 0) or 0),
                    "low_cost_profile_satisfied": bool(summary.get("low_cost_profile_satisfied", False)),
                    "missing_evidence_roles": summary.get("missing_evidence_roles", []),
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
                item for item in candidates if str(item.get("anchor_tier") or "") in {"strong_anchor", "definition_anchor"}
            ],
            "family_anchor_candidates": [
                item for item in candidates if str(item.get("anchor_tier") or "") == "family_anchor"
            ],
            "anchored_candidate_ids": [
                str(item.get("candidate_id"))
                for item in candidates
                if str(item.get("anchor_tier") or "") in {
                    "strong_anchor",
                    "definition_anchor",
                    "family_anchor",
                    "provisional_anchor",
                }
            ],
        }

    def _best_payload_match(self, item: ObservedEvidenceItem, hypothesis: HypothesisScore) -> dict | None:
        if item.node_id == hypothesis.node_id or self._normalize_text(item.name) == self._normalize_text(hypothesis.name):
            return {
                "node_id": hypothesis.node_id,
                "name": hypothesis.name,
                "label": "Disease",
                "relation_type": DISEASE_SELF_ANCHOR_RELATION_TYPE,
                "disease_degree": 1,
            }

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

    def _classify_evidence_role(
        self,
        *,
        item: ObservedEvidenceItem,
        relation_type: str,
        label: str,
        evidence_name: str,
        candidate_name: str,
        evidence_tags: set[str],
        disease_degree: int,
    ) -> str:
        if relation_type == DISEASE_SELF_ANCHOR_RELATION_TYPE:
            return "disease_specific_anchor"

        if self._is_background_evidence(evidence_name, candidate_name, evidence_tags, disease_degree):
            return "background_context"

        if label == "Pathogen" or relation_type == "HAS_PATHOGEN" or "type:pathogen" in evidence_tags:
            return "disease_specific_anchor"

        if label == "Disease":
            return "disease_specific_anchor"

        if relation_type in DETAIL_DEFINITION_RELATION_TYPES or len(evidence_tags & DEFINITION_FAMILY_TAGS) > 0:
            return "definition_anchor"

        if relation_type in ANCHOR_RELATION_TYPES and label in {"LabFinding", "LabTest", "ImagingFinding"}:
            return "disease_specific_anchor"

        if len(evidence_tags & SPECIFIC_ANCHOR_FAMILY_TAGS) > 0:
            return "disease_specific_anchor"

        if label == "ClinicalAttribute":
            return "definition_anchor"

        if label in {"RiskFactor", "PopulationGroup"} or relation_type in {"RISK_FACTOR_FOR", "APPLIES_TO"}:
            return "risk_or_comorbidity"

        if label == "ClinicalFinding" or relation_type == "MANIFESTS_AS":
            return "phenotype_support"

        if self._is_high_value_anchor(label, relation_type):
            return "disease_specific_anchor"

        return "background_context"

    def _is_negative_anchor(self, item: ObservedEvidenceItem, relation_type: str, label: str, role: str, scope: str) -> bool:
        if item.polarity != "absent" or item.resolution != "clear":
            return False

        if scope == ANCHOR_SCOPE_BACKGROUND:
            return False

        return role in {"disease_specific_anchor", "definition_anchor"} or relation_type in ANCHOR_RELATION_TYPES

    def _is_low_cost_profile_support(
        self,
        *,
        item: ObservedEvidenceItem,
        payload: dict,
        label: str,
        relation_type: str,
        role: str,
        evidence_tags: set[str],
    ) -> bool:
        if item.polarity != "present" or item.resolution != "clear":
            return False

        if role == "background_context":
            return False

        if len(self._low_cost_profile_families(evidence_tags, role)) == 0:
            return False

        acquisition_mode = str(payload.get("acquisition_mode") or item.metadata.get("acquisition_mode") or "")
        evidence_cost = str(payload.get("evidence_cost") or item.metadata.get("evidence_cost") or "")

        if acquisition_mode in {"needs_lab_test", "needs_imaging", "needs_pathogen_test"} or evidence_cost == "high":
            return False

        if acquisition_mode in {"direct_ask", "history_known"} or evidence_cost == "low":
            return True

        if label == "ClinicalFinding" or relation_type == "MANIFESTS_AS":
            return True

        return label in {"RiskFactor", "PopulationGroup", "ClinicalAttribute"} and evidence_cost in {"", "low"}

    def _low_cost_profile_families(self, evidence_tags: set[str], role: str) -> set[str]:
        families = {
            tag
            for tag in self._canonical_family_tags(evidence_tags)
            if tag not in LOW_COST_PROFILE_EXCLUDED_FAMILY_TAGS
        }

        if len(families) > 0:
            return families

        if role == "phenotype_support":
            return {"phenotype"}

        if role == "risk_or_comorbidity":
            return {"risk_or_comorbidity"}

        if role == "definition_anchor":
            return {"definition_detail"}

        if role == "family_anchor":
            return {"family_anchor"}

        return set()

    # 将旧审计标签和 catalog 标签压到同一命名空间，避免同一证据被重复计入 family coverage。
    def _canonical_family_tags(self, evidence_tags: set[str]) -> set[str]:
        aliases = {
            "respiratory": "respiratory_symptom",
            "systemic": "constitutional_symptom",
            "risk": "exposure_risk",
            "viral": "viral_pathogen",
            "detail": "general_detail",
        }
        values: set[str] = set()

        for tag in evidence_tags:
            if tag.startswith("type:"):
                continue
            values.add(aliases.get(tag, tag))

        return values

    def _is_background_evidence(
        self,
        evidence_name: str,
        candidate_name: str,
        evidence_tags: set[str],
        disease_degree: int,
    ) -> bool:
        evidence_text = self._normalize_text(evidence_name)
        candidate_text = self._normalize_text(candidate_name)

        if self._is_hiv_specific_marker(evidence_text):
            return not any(keyword in candidate_text for keyword in ("hiv", "艾滋", "获得性免疫缺陷"))

        if any(keyword in evidence_text for keyword in ("乙肝", "hbv", "乙型肝炎", "丙肝", "hcv", "丙型肝炎")):
            return not any(keyword in candidate_text for keyword in ("乙肝", "hbv", "乙型肝炎", "丙肝", "hcv", "丙型肝炎", "肝炎"))

        if len(evidence_tags & BACKGROUND_FAMILY_TAGS) > 0 and len(evidence_tags & SPECIFIC_ANCHOR_FAMILY_TAGS) == 0:
            return True

        if disease_degree >= 6 and len(evidence_tags & SPECIFIC_ANCHOR_FAMILY_TAGS) == 0:
            return True

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
        tags.update(
            str(tag)
            for tag in payload.get("evidence_tags", [])
            if len(str(tag).strip()) > 0
        )
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
            "immune_status": ("cd4", "t淋巴", "免疫", "hiv感染", "艾滋"),
            "underlying_infection": ("hiv感染", "hiv/aids", "艾滋"),
            "general_risk": ("既往病史", "年龄", "性别", "高危"),
            "constitutional_symptom": ("发热", "发烧", "乏力", "盗汗", "体重下降"),
            "pathogen": ("病原", "病毒", "细菌", "真菌", "pcr", "核酸", "阳性", "检出"),
            "disease_specific_lab": ("抗体阳性", "pcr", "核酸", "培养", "抗酸", "xpert", "葡聚糖"),
            "imaging": ("ct", "胸片", "影像", "磨玻璃", "mri"),
            "oxygenation": ("低氧", "血氧", "氧分压", "pao2", "spo2"),
            "respiratory_symptom": ("咳", "气促", "呼吸困难", "胸闷"),
            "tuberculosis": ("结核", "抗酸", "分枝杆菌", "mtb", "xpert"),
            "serology": ("igg", "igm", "抗体", "血清"),
            "metabolic_definition": ("ldl", "hdl", "甘油三酯", "总胆固醇", "血脂", "bmi"),
            "tumor_marker": ("afp", "肿瘤标志物", "甲胎蛋白"),
            "pathology": ("病理", "活检", "组织学", "免疫组化"),
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
        definition: Sequence[dict],
        family_anchor: Sequence[dict],
        provisional: Sequence[dict],
        phenotype: Sequence[dict],
        background: Sequence[dict],
        negative: Sequence[dict],
    ) -> str:
        if len(strong) > 0:
            return "strong_anchor"
        if len(definition) > 0:
            return "definition_anchor"
        if len(family_anchor) > 0:
            return "family_anchor"
        if len(provisional) > 0:
            return "provisional_anchor"
        if len(phenotype) > 0:
            return "phenotype_supported"
        if len(background) > 0:
            return "background_supported"
        if len(negative) > 0:
            return "negative_anchor"
        return "speculative"

    def _anchor_tier_priority(self, tier: str) -> int:
        return {
            "strong_anchor": 5,
            "definition_anchor": 5,
            "family_anchor": 4,
            "provisional_anchor": 3,
            "phenotype_supported": 2,
            "background_supported": 1,
            "speculative": 1,
            "negative_anchor": 0,
        }.get(tier, 1)

    def _missing_evidence_roles(self, tier: str) -> list[str]:
        if tier == "provisional_anchor":
            return ["clear_confirmation"]
        if tier == "family_anchor":
            return ["disease_specific_anchor", "definition_anchor", "clear_confirmation"]
        if tier in {"background_supported", "speculative", "negative_anchor"}:
            return ["disease_specific_anchor", "definition_anchor"]
        if tier == "phenotype_supported":
            return ["disease_specific_anchor", "definition_anchor"]
        return []

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
