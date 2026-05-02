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
