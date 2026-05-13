"""测试 KG RAG baseline 医生的 prompt 注入与结果元信息。"""

from __future__ import annotations

from brain.types import ClinicalFeatureItem, PatientContext

from baselines.kg_rag_retriever import KgRagCandidateDisease, KgRagHit, KgRagQueryResult
from baselines.llm_baseline_types import BaselineHypothesisCandidate
from baselines.llm_kg_rag_consultation_brain import KgRagConsultationBrain


class FakeBaselineLlmClient:
    """提供可控结构化输出，避免测试依赖真实模型。"""

    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def is_available(self) -> bool:
        return True

    def run_structured_prompt(self, prompt_name: str, variables: dict, schema: type) -> object:
        self.calls.append((prompt_name, variables))
        response = self.responses.pop(0)
        if isinstance(response, dict) and schema is not dict:
            return schema(**response)
        return response


class FakeKgRagRetriever:
    """返回固定 KG 证据块，验证 brain 的注入行为。"""

    def query(
        self,
        query_text: str,
        *,
        top_k: int = 4,
        candidate_disease_names: list[str] | None = None,
        observed_features=None,
        enable_symptom_candidate_recall: bool = False,
        symptom_candidate_top_k: int = 10,
        symptom_candidate_min_features: int = 2,
        disease_scope_names: list[str] | None = None,
    ) -> KgRagQueryResult:
        _ = (
            query_text,
            top_k,
            candidate_disease_names,
            observed_features,
            enable_symptom_candidate_recall,
            symptom_candidate_top_k,
            symptom_candidate_min_features,
            disease_scope_names,
        )
        return KgRagQueryResult(
            hits=[
                KgRagHit(
                    node_id="lab_ldh_high",
                    name="LDH升高",
                    label="LabFinding",
                    disease_name="肺孢子菌肺炎",
                    relation_type="HAS_LAB_FINDING",
                    question_type_hint="lab",
                    evidence_cost="high",
                    priority=1.1,
                    acquisition_mode="needs_lab_test",
                    retrieval_mode="expected_evidence",
                )
            ],
            candidate_diseases=[
                KgRagCandidateDisease(
                    disease_name="肺孢子菌肺炎",
                    score=0.88,
                    matched_features=["咳嗽", "呼吸困难"],
                    evidence_names=["咳嗽", "呼吸困难"],
                    retrieval_mode="symptom_candidate_recall",
                    already_in_top3=True,
                )
            ],
            candidate_disease_total=1,
            candidate_disease_has_more=False,
            candidate_disease_notice="",
        )


class FakeFeatureExtractor:
    """返回固定 opening 特征，验证 baseline 轻量观察状态写入。"""

    def extract_patient_context(self, patient_text: str) -> PatientContext:
        return PatientContext(
            clinical_features=[
                ClinicalFeatureItem(
                    name="咳嗽",
                    normalized_name="咳嗽",
                    category="symptom",
                    mention_state="present",
                    evidence_text=patient_text,
                )
            ],
            raw_text=patient_text,
        )


class FakeEntityLinker:
    """把 opening 特征标为可信图谱实体，避免测试依赖真实 Neo4j。"""

    def link_clinical_features(self, features):
        _ = features
        return [
            type(
                "LinkedEntityStub",
                (),
                {
                    "canonical_name": "咳嗽",
                    "node_id": "symptom_cough",
                    "label": "ClinicalFinding",
                    "similarity": 0.93,
                    "is_trusted": True,
                },
            )()
        ]


# 验证 kg_rag baseline 会把图谱证据块注入 prompt，并写入 search_report 元信息。
def test_kg_rag_brain_injects_retrieved_kg_context_and_search_metadata() -> None:
    fake_llm = FakeBaselineLlmClient(
        responses=[
            {
                "decision": "ask",
                "question_text": "最近有没有做过 LDH 相关化验？",
                "target_name": "LDH升高",
                "top3": [
                    {"name": "肺孢子菌肺炎", "confidence": 0.71},
                    {"name": "活动性结核病", "confidence": 0.21},
                    {"name": "巨细胞病毒(CMV)肺炎", "confidence": 0.08},
                ],
                "reasoning": "先验证图谱里最区分当前候选的高价值实验室证据。",
            }
        ]
    )
    brain = KgRagConsultationBrain(
        llm_client=fake_llm,
        retriever=FakeKgRagRetriever(),
        max_turns=8,
        retrieval_top_k=2,
        disease_scope=["肺孢子菌肺炎", "活动性结核病", "巨细胞病毒(CMV)肺炎"],
    )

    session = brain.start_session("kg_rag_session")
    session.last_model_top3 = [
        BaselineHypothesisCandidate(name="肺孢子菌肺炎", confidence=0.71),
        BaselineHypothesisCandidate(name="活动性结核病", confidence=0.21),
        BaselineHypothesisCandidate(name="巨细胞病毒(CMV)肺炎", confidence=0.08),
    ]
    turn_output = brain.process_turn("kg_rag_session", "这几天一直咳嗽，活动后呼吸困难。")

    prompt_name, variables = fake_llm.calls[0]
    assert prompt_name == "baseline_consultation_turn"
    assert variables["retrieved_kg_context"]
    assert variables["retrieved_kg_context"][0]["node_id"] == "lab_ldh_high"
    assert turn_output["pending_action"]["metadata"]["selected_action_source"] == "baseline_llm_kg_rag"
    assert turn_output["search_report"]["search_metadata"]["backend"] == "llm_kg_rag"
    assert turn_output["search_report"]["search_metadata"]["retrieved_node_ids"] == ["lab_ldh_high"]
    assert turn_output["search_report"]["search_metadata"]["retrieval_mode"] == "mixed"
    assert turn_output["search_report"]["search_metadata"]["retrieved_kg_context"][0]["node_id"] == "lab_ldh_high"
    assert turn_output["search_report"]["search_metadata"]["retrieved_kg_context_count"] == 1
    assert variables["retrieved_kg_candidate_diseases"][0]["disease_name"] == "肺孢子菌肺炎"
    assert turn_output["search_report"]["search_metadata"]["retrieved_candidate_disease_names_from_symptoms"] == ["肺孢子菌肺炎"]


# 验证 kg_rag baseline 在直接 final 时，也会把图谱证据块写进 final_report 元信息。
def test_kg_rag_brain_writes_retrieved_kg_context_into_final_metadata() -> None:
    fake_llm = FakeBaselineLlmClient(
        responses=[
            {
                "decision": "final",
                "final_answer": "肺孢子菌肺炎",
                "compiled": True,
                "top3": [
                    {"name": "肺孢子菌肺炎", "confidence": 0.89},
                    {"name": "活动性结核病", "confidence": 0.08},
                    {"name": "巨细胞病毒(CMV)肺炎", "confidence": 0.03},
                ],
                "reasoning": "图谱证据块优先支持 PCP 的高价值化验证据。",
            }
        ]
    )
    brain = KgRagConsultationBrain(
        llm_client=fake_llm,
        retriever=FakeKgRagRetriever(),
        max_turns=8,
        retrieval_top_k=2,
        disease_scope=["肺孢子菌肺炎", "活动性结核病", "巨细胞病毒(CMV)肺炎"],
    )

    session = brain.start_session("kg_rag_final_session")
    session.last_model_top3 = [
        BaselineHypothesisCandidate(name="肺孢子菌肺炎", confidence=0.71),
        BaselineHypothesisCandidate(name="活动性结核病", confidence=0.21),
        BaselineHypothesisCandidate(name="巨细胞病毒(CMV)肺炎", confidence=0.08),
    ]
    turn_output = brain.process_turn("kg_rag_final_session", "这几天一直咳嗽，活动后呼吸困难。")

    assert turn_output["final_report"]["metadata"]["retrieved_node_ids"] == ["lab_ldh_high"]
    assert turn_output["final_report"]["metadata"]["retrieved_kg_context"][0]["node_id"] == "lab_ldh_high"
    assert turn_output["final_report"]["metadata"]["retrieved_kg_context_count"] == 1
    assert turn_output["final_report"]["metadata"]["retrieved_candidate_disease_names_from_symptoms"] == ["肺孢子菌肺炎"]


# 验证开启症状反查后，opening 特征与明确肯定短答会写入 baseline 轻量观察状态。
def test_kg_rag_brain_tracks_observed_features_from_opening_and_direct_reply() -> None:
    fake_llm = FakeBaselineLlmClient(
        responses=[
            {
                "decision": "ask",
                "question_text": "最近有没有发热？",
                "target_name": "发热",
                "top3": [
                    {"name": "肺孢子菌肺炎", "confidence": 0.71},
                    {"name": "活动性结核病", "confidence": 0.21},
                    {"name": "巨细胞病毒(CMV)肺炎", "confidence": 0.08},
                ],
                "reasoning": "先确认是否存在发热。",
            },
            {
                "decision": "ask",
                "question_text": "最近有没有盗汗？",
                "target_name": "盗汗",
                "top3": [
                    {"name": "肺孢子菌肺炎", "confidence": 0.66},
                    {"name": "活动性结核病", "confidence": 0.24},
                    {"name": "巨细胞病毒(CMV)肺炎", "confidence": 0.10},
                ],
                "reasoning": "继续补充症状鉴别。",
            },
        ]
    )
    brain = KgRagConsultationBrain(
        llm_client=fake_llm,
        retriever=FakeKgRagRetriever(),
        max_turns=8,
        retrieval_top_k=2,
        enable_symptom_candidate_recall=True,
        feature_extractor=FakeFeatureExtractor(),
        entity_linker=FakeEntityLinker(),
        disease_scope=["肺孢子菌肺炎", "活动性结核病", "巨细胞病毒(CMV)肺炎"],
    )

    brain.process_turn("kg_rag_feature_tracking", "这几天一直咳嗽，活动后呼吸困难。")
    brain.process_turn("kg_rag_feature_tracking", "有")

    session = brain.sessions["kg_rag_feature_tracking"]
    observed_feature_names = {item.canonical_name for item in session.observed_features}
    assert observed_feature_names == {"咳嗽", "发热"}