"""测试图谱检索器的 R1 / R2 基础行为。"""

from brain.retriever import GraphRetriever, RetrievalConfig
from brain.types import EvidenceState, HypothesisScore, KeyFeature, SessionState, SlotState


class FakeNeo4jClient:
    """使用固定结果模拟 Neo4j 查询返回。"""

    # 初始化假的查询客户端并保存预设返回值。
    def __init__(self) -> None:
        self.last_query = ""
        self.last_params = {}

    # 根据查询内容返回 R1 或 R2 的固定结果。
    def run_query(self, query: str, params: dict | None = None) -> list[dict]:
        self.last_query = query
        self.last_params = params or {}

        if "MATCH (feature)-[r]->(candidate)" in query:
            return [
                {
                    "node_id": "disease_pcp",
                    "label": "Disease",
                    "name": "肺孢子菌肺炎 (PCP)",
                    "relation_count": 2.0,
                    "candidate_weight": 1.0,
                    "direction_confidence": 1.0,
                    "evidence_names": ["发热", "干咳"],
                    "evidence_node_ids": ["symptom_fever", "symptom_dry_cough"],
                }
            ]

        return [
            {
                "node_id": "lab_po2",
                "label": "LabFinding",
                "name": "低氧血症",
                "relation_type": "HAS_LAB_FINDING",
                "acquisition_mode": "needs_lab_test",
                "evidence_cost": "high",
                "priority": 2.5,
                "is_red_flag": True,
                "topic_id": "Disease",
            }
        ]


# 验证 R1 能根据核心特征返回候选假设。
def test_retriever_returns_r1_candidates() -> None:
    retriever = GraphRetriever(FakeNeo4jClient())
    key_features = [
        KeyFeature(name="发热", normalized_name="发热"),
        KeyFeature(name="干咳", normalized_name="干咳"),
    ]

    candidates = retriever.retrieve_r1_candidates(key_features)

    assert len(candidates) == 1
    assert candidates[0].node_id == "disease_pcp"
    assert candidates[0].name == "肺孢子菌肺炎 (PCP)"
    assert candidates[0].metadata["evidence_node_ids"] == ["symptom_fever", "symptom_dry_cough"]
    assert candidates[0].metadata["direction_confidence"] == 1.0


# 验证 R2 会根据主假设返回待验证证据。
def test_retriever_returns_r2_expected_evidence() -> None:
    retriever = GraphRetriever(FakeNeo4jClient())
    state = SessionState(
        session_id="s1",
        slots={"发热": SlotState(node_id="发热", status="true")},
    )
    hypothesis = retriever.retrieve_r1_candidates([KeyFeature(name="发热", normalized_name="发热")])[0]

    rows = retriever.retrieve_r2_expected_evidence(hypothesis, state)

    assert len(rows) == 1
    assert rows[0]["node_id"] == "lab_po2"
    assert rows[0]["relation_type"] == "HAS_LAB_FINDING"
    assert "acquisition_mode" in retriever.client.last_query
    assert rows[0]["acquisition_mode"] == "needs_lab_test"
    assert rows[0]["evidence_cost"] == "high"


class SemanticFakeNeo4jClient(FakeNeo4jClient):
    """提供更接近真实场景的 R1 候选，验证语义收紧后的排序。"""

    def run_query(self, query: str, params: dict | None = None) -> list[dict]:
        self.last_query = query
        self.last_params = params or {}

        if "MATCH (feature)-[r]->(candidate)" in query:
            return [
                {
                    "node_id": "disease_good",
                    "label": "Disease",
                    "name": "肺孢子菌肺炎 (PCP)",
                    "relation_count": 2.0,
                    "matched_feature_count": 2,
                    "candidate_weight": 0.8,
                    "direction_confidence": 1.0,
                    "relation_types": ["MANIFESTS_AS", "HAS_LAB_FINDING"],
                    "evidence_names": ["发热", "干咳"],
                    "evidence_node_ids": ["symptom_fever", "symptom_dry_cough"],
                },
                {
                    "node_id": "phase_generic",
                    "label": "Disease",
                    "name": "原发性肺部感染",
                    "relation_count": 1.0,
                    "matched_feature_count": 1,
                    "candidate_weight": 1.0,
                    "direction_confidence": 0.65,
                    "relation_types": ["APPLIES_TO"],
                    "evidence_names": ["呼吸困难"],
                    "evidence_node_ids": ["symptom_dyspnea"],
                },
            ]

        return super().run_query(query, params)


# 验证 R1 会压低单证据泛化候选，并优先保留覆盖更好的疾病候选。
def test_retriever_tightens_r1_candidate_semantics() -> None:
    retriever = GraphRetriever(SemanticFakeNeo4jClient())
    key_features = [
        KeyFeature(name="发热", normalized_name="发热"),
        KeyFeature(name="干咳", normalized_name="干咳"),
        KeyFeature(name="呼吸困难", normalized_name="呼吸困难"),
    ]

    candidates = retriever.retrieve_r1_candidates(key_features)

    assert len(candidates) == 1
    assert candidates[0].node_id == "disease_good"
    assert candidates[0].metadata["feature_coverage"] > 0.6


class DiseaseSpecificAnchorFakeNeo4jClient(FakeNeo4jClient):
    """提供病原体锚点和泛化免疫锚点，验证 R1 更偏向疾病特异证据。"""

    def run_query(self, query: str, params: dict | None = None) -> list[dict]:
        self.last_query = query
        self.last_params = params or {}

        if "MATCH (feature)-[r]->(candidate)" in query:
            return [
                {
                    "node_id": "disease_kaposi",
                    "label": "Disease",
                    "name": "卡波西肉瘤",
                    "relation_count": 1.0,
                    "matched_feature_count": 1,
                    "candidate_weight": 1.0,
                    "direction_confidence": 1.0,
                    "relation_types": ["HAS_LAB_FINDING"],
                    "evidence_names": ["CD4+ T淋巴细胞计数 < 200/μL"],
                    "evidence_labels": ["LabFinding"],
                    "evidence_node_ids": ["lab_cd4_low"],
                },
                {
                    "node_id": "disease_toxo",
                    "label": "Disease",
                    "name": "弓形虫病",
                    "relation_count": 1.0,
                    "matched_feature_count": 1,
                    "candidate_weight": 0.8,
                    "direction_confidence": 1.0,
                    "relation_types": ["HAS_PATHOGEN"],
                    "evidence_names": ["刚地弓形虫"],
                    "evidence_labels": ["Pathogen"],
                    "evidence_node_ids": ["pathogen_toxo"],
                },
            ]

        return super().run_query(query, params)


# 验证病原体/疾病名强相关证据会压过 CD4 这类共享免疫背景证据。
def test_retriever_prioritizes_disease_specific_anchor_over_generic_cd4() -> None:
    retriever = GraphRetriever(
        DiseaseSpecificAnchorFakeNeo4jClient(),
        RetrievalConfig(r1_min_semantic_score=0.0),
    )
    key_features = [
        KeyFeature(name="CD4低", normalized_name="CD4+ T淋巴细胞计数 < 200/μL"),
        KeyFeature(name="刚地弓形虫", normalized_name="刚地弓形虫"),
    ]

    candidates = retriever.retrieve_r1_candidates(key_features)

    assert [candidate.node_id for candidate in candidates[:2]] == ["disease_toxo", "disease_kaposi"]
    assert candidates[0].metadata["disease_specific_anchor_score"] > candidates[1].metadata["disease_specific_anchor_score"]


class HivSpecificAnchorFakeNeo4jClient(FakeNeo4jClient):
    """提供 HIV 病原锚点与泛化 CD4 背景，验证 R1 更偏向疾病特异证据。"""

    def run_query(self, query: str, params: dict | None = None) -> list[dict]:
        self.last_query = query
        self.last_params = params or {}

        if "MATCH (feature)-[r]->(candidate)" in query:
            return [
                {
                    "node_id": "disease_hiv",
                    "label": "Disease",
                    "name": "HIV感染",
                    "relation_count": 2.0,
                    "matched_feature_count": 2,
                    "candidate_weight": 1.0,
                    "direction_confidence": 1.0,
                    "relation_types": ["DIAGNOSED_BY", "HAS_LAB_FINDING"],
                    "evidence_names": ["HIV-1", "HIV RNA阳性"],
                    "evidence_labels": ["Pathogen", "LabFinding"],
                    "evidence_node_ids": ["pathogen_hiv1", "lab_hiv_rna"],
                },
                {
                    "node_id": "disease_kaposi",
                    "label": "Disease",
                    "name": "卡波西肉瘤",
                    "relation_count": 1.0,
                    "matched_feature_count": 1,
                    "candidate_weight": 0.8,
                    "direction_confidence": 1.0,
                    "relation_types": ["HAS_LAB_FINDING"],
                    "evidence_names": ["CD4+ T淋巴细胞计数 < 200/μL"],
                    "evidence_labels": ["LabFinding"],
                    "evidence_node_ids": ["lab_cd4_low"],
                },
            ]

        return super().run_query(query, params)


# 验证 HIV 病原特异性锚点会压过单纯 CD4 背景证据。
def test_retriever_prioritizes_hiv_specific_anchor_over_generic_cd4_background() -> None:
    retriever = GraphRetriever(
        HivSpecificAnchorFakeNeo4jClient(),
        RetrievalConfig(r1_min_semantic_score=0.0),
    )
    key_features = [
        KeyFeature(name="HIV感染", normalized_name="HIV感染"),
        KeyFeature(name="CD4低", normalized_name="CD4+ T淋巴细胞计数 < 200/μL"),
    ]

    candidates = retriever.retrieve_r1_candidates(key_features)

    assert [candidate.node_id for candidate in candidates[:2]] == ["disease_hiv", "disease_kaposi"]
    assert candidates[0].metadata["disease_specific_anchor_score"] > candidates[1].metadata["disease_specific_anchor_score"]


class ProfileFakeNeo4jClient(FakeNeo4jClient):
    """提供候选诊断证据画像查询结果。"""

    def run_query(self, query: str, params: dict | None = None) -> list[dict]:
        self.last_query = query
        self.last_params = params or {}

        if "RETURN target.id AS node_id" in query and "NOT target.id IN" not in query:
            return [
                {
                    "node_id": "symptom_fever",
                    "label": "ClinicalFinding",
                    "name": "发热",
                    "relation_type": "MANIFESTS_AS",
                    "relation_weight": 0.8,
                    "node_weight": 0.7,
                    "priority": 1.5,
                    "question_type_hint": "symptom",
                },
                {
                    "node_id": "lab_cd4_low",
                    "label": "LabFinding",
                    "name": "CD4+ T淋巴细胞计数 < 200/μL",
                    "relation_type": "HAS_LAB_FINDING",
                    "relation_weight": 0.9,
                    "node_weight": 0.9,
                    "priority": 1.8,
                    "question_type_hint": "lab",
                },
                {
                    "node_id": "ct_ground_glass",
                    "label": "ImagingFinding",
                    "name": "胸部CT磨玻璃影",
                    "relation_type": "HAS_IMAGING_FINDING",
                    "relation_weight": 0.9,
                    "node_weight": 0.9,
                    "priority": 1.7,
                    "question_type_hint": "imaging",
                },
                {
                    "node_id": "ct_exam",
                    "label": "LabTest",
                    "name": "胸部CT",
                    "relation_type": "DIAGNOSED_BY",
                    "relation_weight": 0.9,
                    "node_weight": 0.8,
                    "priority": 1.6,
                    "question_type_hint": "lab",
                    "acquisition_mode": "needs_imaging",
                    "evidence_cost": "high",
                },
            ]

        return super().run_query(query, params)


# 验证候选诊断证据画像不排除已知节点，并基于结构化状态标注证据状态。
def test_retriever_builds_candidate_evidence_profile_with_statuses() -> None:
    retriever = GraphRetriever(ProfileFakeNeo4jClient())
    state = SessionState(
        session_id="s_profile",
        slots={"发热": SlotState(node_id="发热", status="true", resolution="clear")},
        evidence_states={
            "lab_cd4_low": EvidenceState(
                node_id="lab_cd4_low",
                existence="non_exist",
                resolution="clear",
                reasoning="患者否认 CD4 很低。",
            )
        },
    )
    hypothesis = HypothesisScore(node_id="pcp", label="Disease", name="肺孢子菌肺炎 (PCP)", score=1.0)

    rows = retriever.retrieve_candidate_evidence_profile(hypothesis, state, top_k=6)

    by_id = {item["node_id"]: item for item in rows}
    assert "NOT target.id IN" not in retriever.client.last_query
    assert by_id["symptom_fever"]["status"] == "matched"
    assert by_id["lab_cd4_low"]["status"] == "negative"
    assert by_id["ct_ground_glass"]["status"] == "unknown"
    assert by_id["ct_ground_glass"]["group"] == "imaging"
    assert by_id["ct_exam"]["group"] == "imaging"
    assert by_id["ct_exam"]["question_type_hint"] == "imaging"
