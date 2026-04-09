"""测试图谱检索器的 R1 / R2 基础行为。"""

from brain.retriever import GraphRetriever
from brain.types import KeyFeature, SessionState, SlotState


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
                    "label": "DiseasePhase",
                    "name": "原发性肺部感染",
                    "relation_count": 1.0,
                    "matched_feature_count": 1,
                    "candidate_weight": 1.0,
                    "direction_confidence": 0.65,
                    "relation_types": ["ASSOCIATED_WITH"],
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
