"""测试纯 LLM baseline 医生的最小问诊闭环。"""

from __future__ import annotations

from baselines.llm_consultation_brain import PureLlmConsultationBrain


class FakeBaselineLlmClient:
    """提供可控的结构化输出，避免测试依赖真实外部模型。"""

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


# 验证 pure LLM doctor 能先 ask，再在后续轮次输出 final_report。
def test_pure_llm_brain_asks_then_finalizes() -> None:
    fake_llm = FakeBaselineLlmClient(
        responses=[
            {
                "decision": "ask",
                "question_text": "最近有没有发热？",
                "target_name": "发热",
                "top3": [
                    {"name": "肺孢子菌肺炎", "confidence": 0.72},
                    {"name": "活动性结核病", "confidence": 0.18},
                    {"name": "细菌性肺炎", "confidence": 0.10},
                ],
                "reasoning": "需要先确认是否存在发热这一核心症状。",
            },
            {
                "decision": "final",
                "compiled": True,
                "final_answer": "肺孢子菌肺炎",
                "top3": [
                    {"name": "肺孢子菌肺炎", "confidence": 0.84},
                    {"name": "活动性结核病", "confidence": 0.11},
                    {"name": "细菌性肺炎", "confidence": 0.05},
                ],
                "reasoning": "当前线索已经足以优先支持 PCP。",
            },
        ]
    )
    brain = PureLlmConsultationBrain(llm_client=fake_llm, max_turns=8)

    brain.start_session("s1")
    first_turn = brain.process_turn("s1", "最近总是咳嗽，有点不舒服。")

    assert first_turn["final_report"] is None
    assert first_turn["next_question"] == "最近有没有发热？"
    assert first_turn["pending_action"]["target_node_id"] == "发热"
    assert first_turn["pending_action"]["metadata"]["question_type_hint"] == "symptom"
    assert first_turn["pending_action"]["metadata"]["evidence_cost"] == "low"
    assert first_turn["search_report"]["search_metadata"]["decision_confidence"] == 0.72
    assert first_turn["search_report"]["search_metadata"]["backend"] == "pure_llm"
    assert first_turn["search_report"]["search_metadata"]["selected_action_source"] == "baseline_llm"

    second_turn = brain.process_turn("s1", "有。")

    assert second_turn["pending_action"] is None
    assert second_turn["next_question"] is None
    assert second_turn["final_report"]["stop_reason"] == "final_answer_accepted"
    assert second_turn["final_report"]["best_final_answer"]["answer_name"] == "肺孢子菌肺炎"
    assert second_turn["final_report"]["candidate_hypotheses"][0]["name"] == "肺孢子菌肺炎"
    assert second_turn["final_report"]["metadata"]["decision_confidence"] == 0.84
    assert fake_llm.calls[0][0] == "baseline_consultation_turn"
    assert fake_llm.calls[0][1]["must_finalize"] is False


# 验证 finalize() 会在达到 turn limit 后强制收束为 top1 + top3。
def test_pure_llm_brain_finalize_forces_final_from_top3() -> None:
    fake_llm = FakeBaselineLlmClient(
        responses=[
            {
                "decision": "ask",
                "question_text": "最近有没有盗汗？",
                "target_name": "盗汗",
                "top3": [
                    {"name": "活动性结核病", "confidence": 0.61},
                    {"name": "肺孢子菌肺炎", "confidence": 0.25},
                    {"name": "急性HIV感染", "confidence": 0.14},
                ],
                "reasoning": "需要先确认是否存在盗汗。",
            },
            {
                "decision": "ask",
                "question_text": "最近有没有体重下降？",
                "target_name": "体重下降",
                "top3": [
                    {"name": "活动性结核病", "confidence": 0.66},
                    {"name": "肺孢子菌肺炎", "confidence": 0.21},
                    {"name": "急性HIV感染", "confidence": 0.13},
                ],
                "reasoning": "虽然还能继续追问，但已接近预算上限。",
            },
        ]
    )
    brain = PureLlmConsultationBrain(llm_client=fake_llm, max_turns=1)

    brain.start_session("s_finalize")
    first_turn = brain.process_turn("s_finalize", "最近总咳嗽。")

    assert first_turn["final_report"] is None

    final_report = brain.finalize("s_finalize")

    assert final_report["stop_reason"] == "baseline_turn_limit_finalize"
    assert final_report["best_final_answer"]["answer_name"] == "活动性结核病"
    assert final_report["metadata"]["forced_finalize"] is True
    assert fake_llm.calls[-1][1]["must_finalize"] is True


# 验证 exam_context 类问题会保留当前 patient agent 可识别的前缀约定。
def test_pure_llm_brain_preserves_exam_context_target_prefix() -> None:
    fake_llm = FakeBaselineLlmClient(
        responses=[
            {
                "decision": "ask",
                "question_text": "最近有没有做过实验室检查？",
                "target_name": "实验室检查",
                "top3": [
                    {"name": "肺孢子菌肺炎", "confidence": 0.58},
                    {"name": "活动性结核病", "confidence": 0.30},
                    {"name": "细菌性肺炎", "confidence": 0.12},
                ],
                "reasoning": "先确认是否有实验室检查结果可用。",
            }
        ]
    )
    brain = PureLlmConsultationBrain(llm_client=fake_llm, max_turns=8)

    brain.start_session("s_exam")
    turn_output = brain.process_turn("s_exam", "最近咳嗽得厉害。")

    assert turn_output["pending_action"]["target_node_id"] == "__exam_context__::lab"
    assert turn_output["pending_action"]["target_node_label"] == "ExamContext"
    assert turn_output["pending_action"]["metadata"]["question_type_hint"] == "exam_context"
    assert turn_output["pending_action"]["metadata"]["evidence_cost"] == "high"


# 验证注入 disease scope 后，prompt 会携带闭集候选，并优先把 final/top3 对齐到范围内病名。
def test_pure_llm_brain_injects_disease_scope_and_prefers_in_scope_answers() -> None:
    fake_llm = FakeBaselineLlmClient(
        responses=[
            {
                "decision": "final",
                "compiled": True,
                "final_answer": "HIV相关机会性感染",
                "top3": [
                    {"name": "HIV相关机会性感染", "confidence": 0.88},
                    {"name": "巨细胞病毒(CMV)肺炎", "confidence": 0.65},
                    {"name": "肺孢子菌肺炎", "confidence": 0.22},
                ],
                "reasoning": "更像 HIV 相关机会性感染，但闭集里最接近的是 CMV 肺炎。",
            }
        ]
    )
    brain = PureLlmConsultationBrain(
        llm_client=fake_llm,
        max_turns=8,
        disease_scope=["巨细胞病毒(CMV)肺炎", "肺孢子菌肺炎"],
    )

    brain.start_session("s_scope")
    turn_output = brain.process_turn("s_scope", "最近发热、呼吸困难，还提示巨细胞病毒。")

    assert fake_llm.calls[0][1]["disease_scope_count"] == 2
    assert fake_llm.calls[0][1]["disease_scope_names"] == ["巨细胞病毒(CMV)肺炎", "肺孢子菌肺炎"]
    assert turn_output["final_report"]["best_final_answer"]["answer_name"] == "巨细胞病毒(CMV)肺炎"
    assert turn_output["final_report"]["candidate_hypotheses"][0]["name"] == "巨细胞病毒(CMV)肺炎"
    assert turn_output["final_report"]["metadata"]["disease_scope_enabled"] is True
    assert turn_output["final_report"]["metadata"]["disease_scope_count"] == 2
