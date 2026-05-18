"""测试 KG RAG 检索适配器的最小行为。"""

from __future__ import annotations

import pytest

from baselines.kg_rag_retriever import KgRagRetriever
from baselines.llm_baseline_types import BaselineObservedFeature
from brain.state import HypothesisCandidate


class FakeNeo4jClient:
    """用固定查询结果替代真实 Neo4j，验证适配器整形逻辑。"""

    def __init__(self) -> None:
        self.query_log: list[str] = []

    def run_query(self, query: str, params=None):
        self.query_log.append(query)
        _ = params or {}
        if "MATCH (candidate:Disease)" in query:
            return [
                {
                    "node_id": "d_pcp",
                    "label": "Disease",
                    "name": "肺孢子菌肺炎",
                    "score": 0.88,
                    "aliases": ["PCP"],
                }
            ]
        if "AS relation_specificity" in query:
            return [
                {
                    "node_id": "symptom_dyspnea",
                    "label": "ClinicalFinding",
                    "name": "呼吸困难",
                    "relation_type": "MANIFESTS_AS",
                    "priority": 0.82,
                    "question_type_hint": "symptom",
                    "evidence_cost": "low",
                    "acquisition_mode": "direct_ask",
                }
            ]
        if "contradiction_priority" in query:
            return [
                {
                    "node_id": "lab_ldh_high",
                    "label": "LabFinding",
                    "name": "LDH升高",
                    "relation_type": "HAS_LAB_FINDING",
                    "priority": 1.16,
                    "question_type_hint": "lab",
                    "evidence_cost": "high",
                    "acquisition_mode": "needs_lab_test",
                }
            ]
        raise AssertionError(query)

    def close(self) -> None:
        return None


class FakeQuotaNeo4jClient:
    """返回跨多种证据分组的固定结果，用于验证分组配额裁剪。"""

    def run_query(self, query: str, params=None):
        _ = params or {}
        if "MATCH (candidate:Disease)" in query:
            return [
                {
                    "node_id": "d_te",
                    "label": "Disease",
                    "name": "弓形虫脑炎",
                    "score": 0.92,
                    "aliases": [],
                }
            ]
        if "AS relation_specificity" in query:
            return [
                {
                    "node_id": "symptom_1",
                    "label": "ClinicalFinding",
                    "name": "头痛",
                    "relation_type": "MANIFESTS_AS",
                    "priority": 0.96,
                    "question_type_hint": "symptom",
                    "evidence_cost": "low",
                    "acquisition_mode": "direct_ask",
                },
                {
                    "node_id": "symptom_2",
                    "label": "ClinicalFinding",
                    "name": "发热",
                    "relation_type": "MANIFESTS_AS",
                    "priority": 0.95,
                    "question_type_hint": "symptom",
                    "evidence_cost": "low",
                    "acquisition_mode": "direct_ask",
                },
                {
                    "node_id": "symptom_3",
                    "label": "ClinicalFinding",
                    "name": "呕吐",
                    "relation_type": "MANIFESTS_AS",
                    "priority": 0.94,
                    "question_type_hint": "symptom",
                    "evidence_cost": "low",
                    "acquisition_mode": "direct_ask",
                },
                {
                    "node_id": "symptom_4",
                    "label": "ClinicalFinding",
                    "name": "癫痫",
                    "relation_type": "MANIFESTS_AS",
                    "priority": 0.93,
                    "question_type_hint": "symptom",
                    "evidence_cost": "low",
                    "acquisition_mode": "direct_ask",
                },
                {
                    "node_id": "symptom_5",
                    "label": "ClinicalFinding",
                    "name": "意识模糊",
                    "relation_type": "MANIFESTS_AS",
                    "priority": 0.92,
                    "question_type_hint": "symptom",
                    "evidence_cost": "low",
                    "acquisition_mode": "direct_ask",
                },
                {
                    "node_id": "symptom_6",
                    "label": "ClinicalFinding",
                    "name": "偏瘫",
                    "relation_type": "MANIFESTS_AS",
                    "priority": 0.91,
                    "question_type_hint": "symptom",
                    "evidence_cost": "low",
                    "acquisition_mode": "direct_ask",
                },
                {
                    "node_id": "lab_1",
                    "label": "LabFinding",
                    "name": "脑脊液蛋白升高",
                    "relation_type": "HAS_LAB_FINDING",
                    "priority": 0.88,
                    "question_type_hint": "lab",
                    "evidence_cost": "high",
                    "acquisition_mode": "needs_lab_test",
                },
                {
                    "node_id": "lab_2",
                    "label": "LabFinding",
                    "name": "淋巴细胞减少",
                    "relation_type": "HAS_LAB_FINDING",
                    "priority": 0.87,
                    "question_type_hint": "lab",
                    "evidence_cost": "high",
                    "acquisition_mode": "needs_lab_test",
                },
                {
                    "node_id": "lab_3",
                    "label": "LabTest",
                    "name": "血清弓形虫抗体",
                    "relation_type": "DIAGNOSED_BY",
                    "priority": 0.86,
                    "question_type_hint": "lab",
                    "evidence_cost": "high",
                    "acquisition_mode": "needs_lab_test",
                },
                {
                    "node_id": "imaging_1",
                    "label": "ImagingFinding",
                    "name": "多发环形强化灶",
                    "relation_type": "HAS_IMAGING_FINDING",
                    "priority": 0.84,
                    "question_type_hint": "imaging",
                    "evidence_cost": "high",
                    "acquisition_mode": "needs_imaging",
                },
                {
                    "node_id": "imaging_2",
                    "label": "ImagingFinding",
                    "name": "基底节病灶",
                    "relation_type": "HAS_IMAGING_FINDING",
                    "priority": 0.83,
                    "question_type_hint": "imaging",
                    "evidence_cost": "high",
                    "acquisition_mode": "needs_imaging",
                },
                {
                    "node_id": "pathogen_1",
                    "label": "Pathogen",
                    "name": "刚地弓形虫",
                    "relation_type": "HAS_PATHOGEN",
                    "priority": 0.82,
                    "question_type_hint": "pathogen",
                    "evidence_cost": "high",
                    "acquisition_mode": "needs_pathogen_test",
                },
                {
                    "node_id": "pathogen_2",
                    "label": "Pathogen",
                    "name": "弓形虫DNA",
                    "relation_type": "HAS_PATHOGEN",
                    "priority": 0.81,
                    "question_type_hint": "pathogen",
                    "evidence_cost": "high",
                    "acquisition_mode": "needs_pathogen_test",
                },
                {
                    "node_id": "risk_1",
                    "label": "RiskFactor",
                    "name": "猫接触史",
                    "relation_type": "RISK_FACTOR_FOR",
                    "priority": 0.79,
                    "question_type_hint": "risk",
                    "evidence_cost": "low",
                    "acquisition_mode": "direct_ask",
                },
                {
                    "node_id": "risk_2",
                    "label": "RiskFactor",
                    "name": "未熟肉类暴露",
                    "relation_type": "RISK_FACTOR_FOR",
                    "priority": 0.78,
                    "question_type_hint": "risk",
                    "evidence_cost": "low",
                    "acquisition_mode": "direct_ask",
                },
                {
                    "node_id": "risk_3",
                    "label": "PopulationGroup",
                    "name": "严重免疫抑制人群",
                    "relation_type": "APPLIES_TO",
                    "priority": 0.77,
                    "question_type_hint": "risk",
                    "evidence_cost": "low",
                    "acquisition_mode": "history_known",
                },
                {
                    "node_id": "detail_1",
                    "label": "ClinicalAttribute",
                    "name": "亚急性起病",
                    "relation_type": "REQUIRES_DETAIL",
                    "priority": 0.76,
                    "question_type_hint": "detail",
                    "evidence_cost": "low",
                    "acquisition_mode": "direct_ask",
                },
                {
                    "node_id": "detail_2",
                    "label": "ClinicalAttribute",
                    "name": "病情持续加重",
                    "relation_type": "REQUIRES_DETAIL",
                    "priority": 0.75,
                    "question_type_hint": "detail",
                    "evidence_cost": "low",
                    "acquisition_mode": "direct_ask",
                },
                {
                    "node_id": "detail_3",
                    "label": "ClinicalAttribute",
                    "name": "病程超过两周",
                    "relation_type": "REQUIRES_DETAIL",
                    "priority": 0.74,
                    "question_type_hint": "detail",
                    "evidence_cost": "low",
                    "acquisition_mode": "direct_ask",
                },
            ]
        if "contradiction_priority" in query:
            return [
                {
                    "node_id": "lab_expected_1",
                    "label": "LabFinding",
                    "name": "脑脊液弓形虫DNA阳性",
                    "relation_type": "HAS_LAB_FINDING",
                    "priority": 1.16,
                    "question_type_hint": "lab",
                    "evidence_cost": "high",
                    "acquisition_mode": "needs_lab_test",
                },
                {
                    "node_id": "imaging_expected_1",
                    "label": "ImagingFinding",
                    "name": "MRI多发占位伴环形强化",
                    "relation_type": "HAS_IMAGING_FINDING",
                    "priority": 1.14,
                    "question_type_hint": "imaging",
                    "evidence_cost": "high",
                    "acquisition_mode": "needs_imaging",
                },
                {
                    "node_id": "pathogen_expected_1",
                    "label": "Pathogen",
                    "name": "弓形虫病原学检测",
                    "relation_type": "HAS_PATHOGEN",
                    "priority": 1.13,
                    "question_type_hint": "pathogen",
                    "evidence_cost": "high",
                    "acquisition_mode": "needs_pathogen_test",
                },
                {
                    "node_id": "risk_expected_1",
                    "label": "RiskFactor",
                    "name": "近期免疫抑制加重",
                    "relation_type": "RISK_FACTOR_FOR",
                    "priority": 1.12,
                    "question_type_hint": "risk",
                    "evidence_cost": "low",
                    "acquisition_mode": "history_known",
                },
                {
                    "node_id": "detail_expected_1",
                    "label": "ClinicalAttribute",
                    "name": "症状进行性恶化",
                    "relation_type": "REQUIRES_DETAIL",
                    "priority": 1.11,
                    "question_type_hint": "detail",
                    "evidence_cost": "low",
                    "acquisition_mode": "direct_ask",
                },
            ]
        raise AssertionError(query)

    def close(self) -> None:
        return None


# 验证 KG 检索会把候选疾病画像与待验证证据整理成统一 hit 列表。
def test_kg_rag_retriever_merges_profile_and_expected_evidence() -> None:
    retriever = KgRagRetriever(FakeNeo4jClient())

    result = retriever.query(
        "患者最近咳嗽、活动后呼吸困难。",
        top_k=4,
        candidate_disease_names=["肺孢子菌肺炎"],
    )

    assert len(result.hits) == 2
    assert result.hits[0].disease_name == "肺孢子菌肺炎"
    assert result.hits[0].node_id == "lab_ldh_high"
    assert result.hits[0].retrieval_mode == "expected_evidence"
    assert result.hits[1].node_id == "symptom_dyspnea"
    assert result.hits[1].retrieval_mode == "candidate_profile"


# 验证增强版 kg_rag 会按每个候选病种的证据分组配额选取注入块，而不是只截成全局 top_k。
def test_kg_rag_retriever_applies_per_candidate_group_quotas() -> None:
    retriever = KgRagRetriever(FakeQuotaNeo4jClient())

    result = retriever.query(
        "患者头痛、发热并伴有局灶性神经功能缺损。",
        top_k=4,
        candidate_disease_names=["弓形虫脑炎"],
    )

    question_type_counts: dict[str, int] = {}
    node_ids = {item.node_id for item in result.hits}
    for item in result.hits:
        question_type_counts[item.question_type_hint] = question_type_counts.get(item.question_type_hint, 0) + 1

    assert len(result.hits) == 17
    assert question_type_counts == {
        "symptom": 5,
        "lab": 4,
        "imaging": 2,
        "pathogen": 2,
        "risk": 2,
        "detail": 2,
    }
    assert "symptom_6" not in node_ids
    assert "imaging_2" not in node_ids
    assert "pathogen_2" not in node_ids
    assert "risk_2" not in node_ids
    assert "risk_3" not in node_ids
    assert "detail_2" not in node_ids
    assert "detail_3" not in node_ids


# 验证 Neo4j 环境缺失时会给出明确错误，而不是静默退化。
def test_kg_rag_retriever_from_env_requires_password(monkeypatch) -> None:
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)

    with pytest.raises(RuntimeError, match="NEO4J_PASSWORD"):
        KgRagRetriever.from_env()


class FakeSymptomRecallGraphRetriever:
    """只模拟症状反查分支，避免测试依赖真实 R1 Cypher。"""

    def retrieve_candidate_evidence_profile(self, hypothesis, session_state, top_k=None, group_limit=None, total_limit=None):
        _ = hypothesis, session_state, top_k, group_limit, total_limit
        return []

    def retrieve_r2_expected_evidence(self, hypothesis, session_state, top_k=None):
        _ = hypothesis, session_state, top_k
        return []

    def retrieve_r1_candidates(self, linked_features, patient_context=None, session_state=None, top_k=None):
        _ = patient_context, session_state, top_k
        assert {item.normalized_name for item in linked_features} == {"咳嗽", "呼吸困难"}
        return [
            HypothesisCandidate(
                node_id=f"d_{index}",
                name=name,
                label="Disease",
                score=0.95 - index * 0.01,
                metadata={"evidence_names": ["咳嗽", "呼吸困难"]},
            )
            for index, name in enumerate(
                [
                    "肺孢子菌肺炎",
                    "活动性结核病",
                    "巨细胞病毒(CMV)肺炎",
                    "细菌性肺炎",
                    "真菌性肺炎",
                    "隐球菌肺炎",
                    "卡波西肉瘤",
                    "淋巴瘤",
                    "肺栓塞",
                    "慢性阻塞性肺疾病急性加重",
                    "真菌性鼻窦炎",
                    "范围外疾病",
                ]
            )
        ]


# 验证症状反查分支会返回 top10 候选疾病、计数与 has_more 标记，并按 disease scope 过滤。
def test_kg_rag_retriever_returns_symptom_candidate_diseases() -> None:
    retriever = KgRagRetriever(
        FakeNeo4jClient(),
        graph_retriever=FakeSymptomRecallGraphRetriever(),
    )

    result = retriever.query(
        "患者咳嗽、活动后呼吸困难。",
        candidate_disease_names=["肺孢子菌肺炎", "活动性结核病", "巨细胞病毒(CMV)肺炎"],
        observed_features=[
            BaselineObservedFeature(
                normalized_name="咳嗽",
                canonical_name="咳嗽",
                mention_state="present",
                metadata={"category": "symptom"},
            ),
            BaselineObservedFeature(
                normalized_name="呼吸困难",
                canonical_name="呼吸困难",
                mention_state="present",
                metadata={"category": "symptom"},
            ),
        ],
        enable_symptom_candidate_recall=True,
        symptom_candidate_top_k=10,
        disease_scope_names=[
            "肺孢子菌肺炎",
            "活动性结核病",
            "巨细胞病毒(CMV)肺炎",
            "细菌性肺炎",
            "真菌性肺炎",
            "隐球菌肺炎",
            "卡波西肉瘤",
            "淋巴瘤",
            "肺栓塞",
            "慢性阻塞性肺疾病急性加重",
            "真菌性鼻窦炎",
        ],
    )

    assert len(result.candidate_diseases) == 10
    assert result.candidate_disease_total == 11
    assert result.candidate_disease_has_more is True
    assert result.candidate_disease_notice
    assert result.candidate_diseases[0].disease_name == "肺孢子菌肺炎"
    assert result.candidate_diseases[0].already_in_top3 is True
    assert result.candidate_diseases[-1].disease_name == "慢性阻塞性肺疾病急性加重"