"""测试 observed evidence anchor 的候选重排与模拟证据隔离。"""

from brain.evidence_anchor import EvidenceAnchorAnalyzer
from brain.types import EvidenceState, HypothesisScore, SessionState


# 真实病原体阳性应形成 strong anchor，并把对应疾病压过只靠泛证据的候选。
def test_observed_pathogen_anchor_reranks_candidate_over_background() -> None:
    state = SessionState(session_id="anchor")
    state.evidence_states["path_vzv"] = EvidenceState(
        node_id="path_vzv",
        polarity="present",
        existence="exist",
        resolution="clear",
        metadata={"target_node_name": "水痘-带状疱疹病毒", "target_node_label": "Pathogen"},
    )
    state.evidence_states["lab_cd4"] = EvidenceState(
        node_id="lab_cd4",
        polarity="present",
        existence="exist",
        resolution="clear",
        metadata={"target_node_name": "CD4+ T淋巴细胞计数 < 200/μL", "target_node_label": "LabFinding"},
    )
    hypotheses = [
        HypothesisScore(
            node_id="tb",
            label="Disease",
            name="活动性结核病",
            score=2.0,
            metadata={
                "evidence_payloads": [
                    {
                        "node_id": "lab_cd4",
                        "name": "CD4+ T淋巴细胞计数 < 200/μL",
                        "label": "LabFinding",
                        "relation_type": "HAS_LAB_FINDING",
                    }
                ]
            },
        ),
        HypothesisScore(
            node_id="vzv",
            label="Disease",
            name="水痘-带状疱疹病毒感染",
            score=0.75,
            metadata={
                "evidence_payloads": [
                    {
                        "node_id": "path_vzv",
                        "name": "水痘-带状疱疹病毒",
                        "label": "Pathogen",
                        "relation_type": "HAS_PATHOGEN",
                    }
                ]
            },
        ),
    ]

    ranked, index = EvidenceAnchorAnalyzer().rerank_hypotheses(state, hypotheses)

    assert ranked[0].node_id == "vzv"
    assert ranked[0].metadata["anchor_tier"] == "strong_anchor"
    assert ranked[1].metadata["anchor_tier"] == "background_supported"
    assert index["strong_anchor_candidates"][0]["candidate_id"] == "vzv"


# 真实会话直接命中疾病节点时，应按疾病自身锚点处理，避免被 CD4 等背景证据压住。
def test_observed_disease_self_match_becomes_strong_anchor() -> None:
    state = SessionState(session_id="anchor_self")
    state.evidence_states["cmv"] = EvidenceState(
        node_id="cmv",
        polarity="present",
        existence="exist",
        resolution="clear",
        metadata={"target_node_name": "巨细胞病毒感染", "target_node_label": "Disease"},
    )
    state.evidence_states["cd4"] = EvidenceState(
        node_id="cd4",
        polarity="present",
        existence="exist",
        resolution="clear",
        metadata={"target_node_name": "CD4+ T淋巴细胞计数 < 200/μL", "target_node_label": "LabFinding"},
    )
    hypotheses = [
        HypothesisScore(
            node_id="ks",
            label="Disease",
            name="卡波西肉瘤",
            score=2.0,
            metadata={
                "evidence_payloads": [
                    {
                        "node_id": "cd4",
                        "name": "CD4+ T淋巴细胞计数 < 200/μL",
                        "label": "LabFinding",
                        "relation_type": "RISK_FACTOR_FOR",
                    }
                ]
            },
        ),
        HypothesisScore(node_id="cmv", label="Disease", name="巨细胞病毒感染", score=0.6, metadata={}),
    ]

    ranked, _ = EvidenceAnchorAnalyzer().rerank_hypotheses(state, hypotheses)

    assert ranked[0].node_id == "cmv"
    assert ranked[0].metadata["anchor_tier"] == "strong_anchor"
    assert ranked[0].metadata["anchor_supporting_evidence"][0]["evidence_role"] == "disease_specific_anchor"


# 定义性 detail / 数值证据应形成 definition anchor，而非普通背景支持。
def test_definition_detail_anchor_is_role_driven() -> None:
    state = SessionState(session_id="anchor_definition")
    state.evidence_states["ldl"] = EvidenceState(
        node_id="ldl",
        polarity="present",
        existence="exist",
        resolution="clear",
        metadata={"target_node_name": "LDL-C ≥ 2.6 mmol/L", "target_node_label": "ClinicalAttribute"},
    )
    hypotheses = [
        HypothesisScore(
            node_id="dyslipidemia",
            label="Disease",
            name="血脂异常",
            score=0.8,
            metadata={
                "evidence_payloads": [
                    {
                        "node_id": "ldl",
                        "name": "LDL-C ≥ 2.6 mmol/L",
                        "label": "ClinicalAttribute",
                        "relation_type": "REQUIRES_DETAIL",
                    }
                ]
            },
        )
    ]

    ranked, _ = EvidenceAnchorAnalyzer().rerank_hypotheses(state, hypotheses)

    assert ranked[0].metadata["anchor_tier"] == "definition_anchor"
    assert ranked[0].metadata["definition_anchor_evidence"][0]["evidence_role"] == "definition_anchor"


# rollout 中模拟出来的阳性证据不能进入 observed anchor。
def test_rollout_simulated_positive_is_ignored_by_anchor_index() -> None:
    state = SessionState(session_id="anchor_simulated")
    state.evidence_states["mtb"] = EvidenceState(
        node_id="mtb",
        polarity="present",
        existence="exist",
        resolution="clear",
        metadata={
            "target_node_name": "MTB培养阳性",
            "target_node_label": "LabFinding",
            "source_stage": "ROLLOUT_SIMULATION",
        },
    )
    hypotheses = [
        HypothesisScore(
            node_id="tb",
            label="Disease",
            name="活动性结核病",
            score=1.0,
            metadata={
                "evidence_payloads": [
                    {
                        "node_id": "mtb",
                        "name": "MTB培养阳性",
                        "label": "LabFinding",
                        "relation_type": "DIAGNOSED_BY",
                    }
                ]
            },
        )
    ]

    ranked, index = EvidenceAnchorAnalyzer().rerank_hypotheses(state, hypotheses)

    assert ranked[0].metadata["anchor_tier"] == "speculative"
    assert ranked[0].metadata["observed_anchor_score"] == 0.0
    assert index["observed_evidence"] == []


# 明确否定当前候选定义性检查时，应生成 negative anchor 供 stop gate 拦截。
def test_clear_absent_definition_evidence_becomes_negative_anchor() -> None:
    state = SessionState(session_id="anchor_negative")
    state.evidence_states["ct"] = EvidenceState(
        node_id="ct",
        polarity="absent",
        existence="non_exist",
        resolution="clear",
        metadata={"target_node_name": "胸部CT磨玻璃影", "target_node_label": "ImagingFinding"},
    )
    hypotheses = [
        HypothesisScore(
            node_id="pcp",
            label="Disease",
            name="肺孢子菌肺炎",
            score=1.0,
            metadata={
                "evidence_payloads": [
                    {
                        "node_id": "ct",
                        "name": "胸部CT磨玻璃影",
                        "label": "ImagingFinding",
                        "relation_type": "HAS_IMAGING_FINDING",
                    }
                ]
            },
        )
    ]

    ranked, _ = EvidenceAnchorAnalyzer().rerank_hypotheses(state, hypotheses)

    assert ranked[0].metadata["anchor_tier"] == "negative_anchor"
    assert ranked[0].metadata["anchor_negative_evidence"][0]["name"] == "胸部CT磨玻璃影"


# 负证据和不确定证据不能补 minimum evidence family coverage。
def test_negative_and_unclear_evidence_do_not_satisfy_family_coverage() -> None:
    state = SessionState(session_id="anchor_family")
    state.evidence_states["ct"] = EvidenceState(
        node_id="ct",
        polarity="absent",
        existence="non_exist",
        resolution="clear",
        metadata={"target_node_name": "胸部CT磨玻璃影", "target_node_label": "ImagingFinding"},
    )
    state.evidence_states["pathogen"] = EvidenceState(
        node_id="pathogen",
        polarity="unclear",
        existence="unknown",
        resolution="hedged",
        metadata={"target_node_name": "病原学PCR阳性", "target_node_label": "LabFinding"},
    )
    hypotheses = [
        HypothesisScore(
            node_id="pcp",
            label="Disease",
            name="肺孢子菌肺炎",
            score=1.0,
            metadata={
                "minimum_evidence_groups": [["imaging"], ["pathogen"]],
                "evidence_payloads": [
                    {
                        "node_id": "ct",
                        "name": "胸部CT磨玻璃影",
                        "label": "ImagingFinding",
                        "relation_type": "HAS_IMAGING_FINDING",
                    },
                    {
                        "node_id": "pathogen",
                        "name": "病原学PCR阳性",
                        "label": "LabFinding",
                        "relation_type": "DIAGNOSED_BY",
                    },
                ],
            },
        )
    ]

    ranked, _ = EvidenceAnchorAnalyzer().rerank_hypotheses(state, hypotheses)

    assert ranked[0].metadata["observed_evidence_families"] == []
    assert ranked[0].metadata["minimum_evidence_family_coverage_satisfied"] is False
    assert ranked[0].metadata["anchor_missing_evidence_families"] == ["imaging", "pathogen"]


# 只有 hypothesis_id 但没有匹配候选 KG payload 的证据，不能成为该候选 anchor。
def test_scoped_evidence_without_payload_match_does_not_create_anchor() -> None:
    state = SessionState(session_id="anchor_scoped")
    state.evidence_states["unmatched_pathogen"] = EvidenceState(
        node_id="unmatched_pathogen",
        polarity="present",
        existence="exist",
        resolution="clear",
        metadata={
            "target_node_name": "某个病原体阳性",
            "target_node_label": "Pathogen",
            "hypothesis_id": "d1",
            "relation_type": "HAS_PATHOGEN",
        },
    )
    hypotheses = [
        HypothesisScore(
            node_id="d1",
            label="Disease",
            name="候选疾病",
            score=1.0,
            metadata={
                "evidence_payloads": [
                    {
                        "node_id": "other_pathogen",
                        "name": "另一个病原体",
                        "label": "Pathogen",
                        "relation_type": "HAS_PATHOGEN",
                    }
                ]
            },
        )
    ]

    ranked, _ = EvidenceAnchorAnalyzer().rerank_hypotheses(state, hypotheses)

    assert ranked[0].metadata["anchor_tier"] == "speculative"
    assert ranked[0].metadata["observed_anchor_score"] == 0.0
    assert ranked[0].metadata["anchor_supporting_evidence"] == []


# 多族低成本阳性证据应形成 evidence-profile acceptance 候选，但不伪装成 strong anchor。
def test_low_cost_multifamily_profile_is_recorded_without_strong_anchor() -> None:
    state = SessionState(session_id="anchor_low_cost")
    state.evidence_states["cough"] = EvidenceState(
        node_id="cough",
        polarity="present",
        existence="exist",
        resolution="clear",
        metadata={"target_node_name": "咳嗽", "target_node_label": "ClinicalFinding"},
    )
    state.evidence_states["rash"] = EvidenceState(
        node_id="rash",
        polarity="present",
        existence="exist",
        resolution="clear",
        metadata={"target_node_name": "皮疹", "target_node_label": "ClinicalFinding"},
    )
    state.evidence_states["fever"] = EvidenceState(
        node_id="fever",
        polarity="present",
        existence="exist",
        resolution="clear",
        metadata={"target_node_name": "发热", "target_node_label": "ClinicalFinding"},
    )
    hypotheses = [
        HypothesisScore(
            node_id="d1",
            label="Disease",
            name="候选疾病",
            score=1.0,
            metadata={
                "evidence_payloads": [
                    {
                        "node_id": "cough",
                        "name": "咳嗽",
                        "label": "ClinicalFinding",
                        "relation_type": "MANIFESTS_AS",
                        "acquisition_mode": "direct_ask",
                        "evidence_cost": "low",
                        "evidence_tags": ["respiratory_symptom"],
                    },
                    {
                        "node_id": "rash",
                        "name": "皮疹",
                        "label": "ClinicalFinding",
                        "relation_type": "MANIFESTS_AS",
                        "acquisition_mode": "direct_ask",
                        "evidence_cost": "low",
                        "evidence_tags": ["dermatologic_symptom"],
                    },
                    {
                        "node_id": "fever",
                        "name": "发热",
                        "label": "ClinicalFinding",
                        "relation_type": "MANIFESTS_AS",
                        "acquisition_mode": "direct_ask",
                        "evidence_cost": "low",
                        "evidence_tags": ["constitutional_symptom"],
                    },
                ]
            },
        )
    ]

    ranked, index = EvidenceAnchorAnalyzer().rerank_hypotheses(state, hypotheses)

    assert ranked[0].metadata["anchor_tier"] == "phenotype_supported"
    assert ranked[0].metadata["low_cost_present_clear_count"] == 2
    assert ranked[0].metadata["low_cost_core_family_count"] == 2
    assert ranked[0].metadata["low_cost_profile_satisfied"] is True
    assert ranked[0].metadata["evidence_profile_acceptance_candidate"] is True
    assert ranked[0].metadata["low_cost_support_families"] == ["dermatologic_symptom", "respiratory_symptom"]
    assert index["candidate_anchor_summary"][0]["low_cost_profile_satisfied"] is True


# 只有 HIV/CD4/发热等背景证据时，不能构成低成本 profile 放行候选。
def test_background_only_evidence_does_not_satisfy_low_cost_profile() -> None:
    state = SessionState(session_id="anchor_low_cost_background")
    state.evidence_states["fever"] = EvidenceState(
        node_id="fever",
        polarity="present",
        existence="exist",
        resolution="clear",
        metadata={"target_node_name": "发热", "target_node_label": "ClinicalFinding"},
    )
    state.evidence_states["immune"] = EvidenceState(
        node_id="immune",
        polarity="present",
        existence="exist",
        resolution="clear",
        metadata={"target_node_name": "免疫功能低下", "target_node_label": "RiskFactor"},
    )
    hypotheses = [
        HypothesisScore(
            node_id="d1",
            label="Disease",
            name="候选疾病",
            score=1.0,
            metadata={
                "evidence_payloads": [
                    {
                        "node_id": "fever",
                        "name": "发热",
                        "label": "ClinicalFinding",
                        "relation_type": "MANIFESTS_AS",
                        "acquisition_mode": "direct_ask",
                        "evidence_cost": "low",
                        "evidence_tags": ["constitutional_symptom"],
                    },
                    {
                        "node_id": "immune",
                        "name": "免疫功能低下",
                        "label": "RiskFactor",
                        "relation_type": "RISK_FACTOR_FOR",
                        "acquisition_mode": "history_known",
                        "evidence_cost": "low",
                        "evidence_tags": ["immune_status"],
                    },
                ]
            },
        )
    ]

    ranked, _ = EvidenceAnchorAnalyzer().rerank_hypotheses(state, hypotheses)

    assert ranked[0].metadata["anchor_tier"] == "background_supported"
    assert ranked[0].metadata["low_cost_present_clear_count"] == 0
    assert ranked[0].metadata["low_cost_profile_satisfied"] is False
