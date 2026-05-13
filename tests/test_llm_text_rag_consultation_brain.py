"""测试文本稀疏 RAG baseline 医生的 prompt 注入与结果落盘。"""

from __future__ import annotations

from baselines.llm_text_rag_consultation_brain import TextRagConsultationBrain
from baselines.text_rag_retriever import SparseTextRagRetriever, TextRagDocument


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


# 验证 text_rag baseline 会把稀疏检索结果注入 prompt，并写入 search_report 元信息。
def test_text_rag_brain_injects_retrieved_documents_and_search_metadata() -> None:
    retriever = SparseTextRagRetriever(
        [
            TextRagDocument(
                doc_id="pcp_doc",
                disease_name="肺孢子菌肺炎",
                title="PCP 画像",
                content="干咳、呼吸困难、低氧血症和双肺磨玻璃影较常见。",
                tags=["机会性感染"],
            ),
            TextRagDocument(
                doc_id="tb_doc",
                disease_name="活动性结核病",
                title="结核画像",
                content="盗汗、低热、咳嗽、消瘦和空洞影常见。",
                tags=["结核"],
            ),
        ]
    )
    fake_llm = FakeBaselineLlmClient(
        responses=[
            {
                "decision": "ask",
                "question_text": "最近有没有发热？",
                "target_name": "发热",
                "top3": [
                    {"name": "肺孢子菌肺炎", "confidence": 0.74},
                    {"name": "活动性结核病", "confidence": 0.20},
                    {"name": "巨细胞病毒(CMV)肺炎", "confidence": 0.06},
                ],
                "reasoning": "先确认是否存在与检索画像一致的核心症状。",
            }
        ]
    )
    brain = TextRagConsultationBrain(
        llm_client=fake_llm,
        retriever=retriever,
        max_turns=8,
        retrieval_top_k=2,
        disease_scope=["肺孢子菌肺炎", "活动性结核病", "巨细胞病毒(CMV)肺炎"],
    )

    brain.start_session("text_rag_session")
    turn_output = brain.process_turn("text_rag_session", "这几天一直咳嗽，活动后呼吸困难。")

    prompt_name, variables = fake_llm.calls[0]
    assert prompt_name == "baseline_consultation_turn"
    assert variables["retrieved_documents"]
    assert variables["retrieved_documents"][0]["doc_id"] == "pcp_doc"
    assert turn_output["pending_action"]["metadata"]["selected_action_source"] == "baseline_llm_text_rag"
    assert turn_output["search_report"]["search_metadata"]["backend"] == "llm_text_rag"
    assert turn_output["search_report"]["search_metadata"]["retrieved_doc_ids"] == ["pcp_doc", "tb_doc"]
    assert turn_output["search_report"]["search_metadata"]["retrieved_disease_names"] == [
        "肺孢子菌肺炎",
        "活动性结核病",
    ]
    assert turn_output["search_report"]["search_metadata"]["retrieved_documents"][0]["doc_id"] == "pcp_doc"
    assert turn_output["search_report"]["search_metadata"]["retrieved_document_count"] == 2


# 验证 text_rag baseline 在直接 final 时，也会把检索文档预览写进 final_report 元信息。
def test_text_rag_brain_writes_retrieved_documents_into_final_metadata() -> None:
    retriever = SparseTextRagRetriever(
        [
            TextRagDocument(
                doc_id="cmv_doc",
                disease_name="巨细胞病毒(CMV)肺炎",
                title="CMV 肺炎画像",
                content="CMV DNA 阳性、严重免疫抑制和发热呼吸困难可支持诊断。",
                tags=["机会性感染"],
            )
        ]
    )
    fake_llm = FakeBaselineLlmClient(
        responses=[
            {
                "decision": "final",
                "final_answer": "巨细胞病毒(CMV)肺炎",
                "compiled": True,
                "top3": [
                    {"name": "巨细胞病毒(CMV)肺炎", "confidence": 0.92},
                    {"name": "肺孢子菌肺炎", "confidence": 0.06},
                    {"name": "活动性结核病", "confidence": 0.02},
                ],
                "reasoning": "检索文档和当前患者主诉都更支持 CMV 肺炎。",
            }
        ]
    )
    brain = TextRagConsultationBrain(
        llm_client=fake_llm,
        retriever=retriever,
        max_turns=8,
        retrieval_top_k=1,
        disease_scope=["巨细胞病毒(CMV)肺炎", "肺孢子菌肺炎", "活动性结核病"],
    )

    brain.start_session("text_rag_final_session")
    turn_output = brain.process_turn("text_rag_final_session", "我最近呼吸困难，还伴有发热。")

    assert turn_output["final_report"]["metadata"]["retrieved_doc_ids"] == ["cmv_doc"]
    assert turn_output["final_report"]["metadata"]["retrieved_documents"][0]["doc_id"] == "cmv_doc"
    assert turn_output["final_report"]["metadata"]["retrieved_document_count"] == 1