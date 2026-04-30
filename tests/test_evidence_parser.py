"""测试 EvidenceParser 在 LLM-first 模式下的 A1 / A4 / exam_context 行为。"""

from __future__ import annotations

import pytest

from brain.errors import LlmEmptyExtractionError, LlmUnavailableError
from brain.evidence_parser import EvidenceParser
from brain.types import MctsAction, PatientContext


def test_a1_raises_when_llm_returns_no_key_features() -> None:
    class FakeLlmClient:
        def is_available(self) -> bool:
            return True

        def run_structured_prompt(self, prompt_name: str, variables: dict, schema):
            _ = prompt_name, variables, schema
            return {
                "key_features": [],
                "reasoning_summary": "LLM 未提取到核心线索。",
            }

    parser = EvidenceParser(llm_client=FakeLlmClient())

    with pytest.raises(LlmEmptyExtractionError):
        parser.run_a1_key_symptom_extraction(PatientContext(raw_text="最近主要是畏光，还伴有视力下降。"))


def test_a1_normalizes_llm_feature_names() -> None:
    class FakeLlmClient:
        def is_available(self) -> bool:
            return True

        def run_structured_prompt(self, prompt_name: str, variables: dict, schema):
            _ = prompt_name, variables, schema
            return {
                "key_features": [
                    {"name": "咳嗽", "normalized_name": "咳嗽", "status": "exist", "certainty": "doubt"},
                    {"name": "艾滋病", "normalized_name": "艾滋病", "status": "exist", "certainty": "confident"},
                ],
                "reasoning_summary": "已提取关键线索。",
            }

    parser = EvidenceParser(llm_client=FakeLlmClient())
    result = parser.run_a1_key_symptom_extraction(PatientContext(raw_text="最近咳嗽，还担心艾滋病。"))

    feature_names = {item.normalized_name for item in result.key_features}
    assert "干咳" in feature_names
    assert "HIV感染" in feature_names


def test_a4_long_answer_uses_llm_target_interpretation() -> None:
    class FakeLlmClient:
        def is_available(self) -> bool:
            return True

        def run_structured_prompt(self, prompt_name: str, variables: dict, schema):
            assert prompt_name == "a4_target_answer_interpretation"
            assert variables["target_node_name"] == "发热"
            return {
                "existence": "non_exist",
                "certainty": "confident",
                "supporting_span": "",
                "negation_span": "发热没有",
                "uncertain_span": "",
                "reasoning": "患者明确否认发热。",
            }

    parser = EvidenceParser(llm_client=FakeLlmClient())
    action = MctsAction(
        action_id="a3",
        action_type="verify_evidence",
        target_node_id="node_fever",
        target_node_label="ClinicalFinding",
        target_node_name="发热",
    )

    result = parser.run_a4_deductive_analysis("发热没有，但是有点咳嗽。", action)

    assert result.existence == "non_exist"
    assert result.negation_span == "发热没有"
    assert result.metadata["interpretation_source"] == "llm"


def test_a4_short_direct_reply_skips_llm_and_judge_uses_rule_path() -> None:
    class FakeLlmClient:
        def __init__(self) -> None:
            self.called = False

        def is_available(self) -> bool:
            return True

        def run_structured_prompt(self, prompt_name: str, variables: dict, schema):
            _ = prompt_name, variables, schema
            self.called = True
            return {}

    client = FakeLlmClient()
    parser = EvidenceParser(llm_client=client)
    action = MctsAction(
        action_id="a5",
        action_type="verify_evidence",
        target_node_id="node_walk",
        target_node_label="ClinicalFinding",
        target_node_name="步态异常",
        hypothesis_id="d1",
        topic_id="Disease",
    )
    patient_context = PatientContext(raw_text="没有步态异常。")
    interpretation = parser.interpret_answer_for_target("没有步态异常。", action)

    decision = parser.judge_deductive_result(
        patient_context,
        action,
        interpretation,
        None,
        [],
    )

    assert client.called is False
    assert interpretation.existence == "non_exist"
    assert decision.metadata["judge_source"] == "rule"
    assert decision.existence == "non_exist"


def test_a4_long_answer_judge_uses_llm() -> None:
    class FakeLlmClient:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def is_available(self) -> bool:
            return True

        def run_structured_prompt(self, prompt_name: str, variables: dict, schema):
            _ = variables, schema
            self.prompts.append(prompt_name)
            if prompt_name == "a4_target_answer_interpretation":
                return {
                    "existence": "exist",
                    "certainty": "doubt",
                    "supporting_span": "好像有点发热",
                    "negation_span": "",
                    "uncertain_span": "好像有点发热",
                    "reasoning": "患者给出了模糊阳性回答。",
                }
            return {
                "existence": "exist",
                "certainty": "doubt",
                "decision_type": "reverify_hypothesis",
                "next_stage": "A3",
                "diagnostic_rationale": "仍需继续验证。",
                "contradiction_explanation": "",
                "should_terminate_current_path": False,
                "should_spawn_alternative_hypotheses": False,
                "reasoning": "继续追问。",
            }

    client = FakeLlmClient()
    parser = EvidenceParser(llm_client=client)
    action = MctsAction(
        action_id="a4",
        action_type="verify_evidence",
        target_node_id="node_fever",
        target_node_label="ClinicalFinding",
        target_node_name="发热",
        hypothesis_id="d1",
        topic_id="Disease",
    )

    interpretation = parser.run_a4_deductive_analysis("好像有点发热。", action)
    decision = parser.judge_deductive_result(PatientContext(raw_text="好像有点发热。"), action, interpretation, None, [])

    assert client.prompts == ["a4_target_answer_interpretation", "a4_deductive_judge"]
    assert decision.metadata["judge_source"] == "llm"
    assert decision.decision_type == "reverify_hypothesis"


def test_a4_long_answer_raises_when_llm_unavailable() -> None:
    parser = EvidenceParser()
    action = MctsAction(
        action_id="a4",
        action_type="verify_evidence",
        target_node_id="node_fever",
        target_node_label="ClinicalFinding",
        target_node_name="发热",
    )

    with pytest.raises(LlmUnavailableError):
        parser.run_a4_deductive_analysis("发热没有，但是有点咳嗽。", action)


def test_exam_context_long_answer_uses_llm_and_normalizes_tests() -> None:
    class FakeLlmClient:
        def is_available(self) -> bool:
            return True

        def run_structured_prompt(self, prompt_name: str, variables: dict, schema):
            assert prompt_name == "exam_context_interpretation"
            _ = variables, schema
            return {
                "availability": "done",
                "mentioned_tests": ["CD4", "G试验", "CT"],
                "mentioned_results": [
                    {"test_name": "CD4", "raw_text": "CD4 很低", "normalized_result": "low"},
                    {"test_name": "G试验", "raw_text": "G试验阳性", "normalized_result": "positive"},
                ],
                "needs_followup": False,
                "followup_reason": "",
                "reasoning": "患者明确说出做过相关检查并给出了部分结果。",
            }

    parser = EvidenceParser(llm_client=FakeLlmClient())
    action = MctsAction(
        action_id="collect_exam::pcp::general",
        action_type="collect_general_exam_context",
        target_node_id="__exam_context__::general",
        target_node_label="ExamContext",
        target_node_name="医院检查情况",
        metadata={"exam_kind": "general"},
    )

    result = parser.interpret_exam_context_answer("做过 CD4、胸部 CT 和 G 试验，CD4 很低，G 试验阳性。", action)

    assert result.availability == "done"
    assert "CD4+ T淋巴细胞计数" in result.mentioned_tests
    assert "β-D-葡聚糖检测" in result.mentioned_tests
    assert "胸部CT" in result.mentioned_tests
    assert result.metadata["interpretation_source"] == "llm"


def test_exam_context_direct_negative_reply_skips_llm() -> None:
    class FakeLlmClient:
        def __init__(self) -> None:
            self.called = False

        def is_available(self) -> bool:
            return True

        def run_structured_prompt(self, prompt_name: str, variables: dict, schema):
            _ = prompt_name, variables, schema
            self.called = True
            return {}

    client = FakeLlmClient()
    parser = EvidenceParser(llm_client=client)
    action = MctsAction(
        action_id="collect_exam::pcp::imaging",
        action_type="collect_exam_context",
        target_node_id="__exam_context__::imaging",
        target_node_label="ExamContext",
        target_node_name="胸部影像检查情况",
        metadata={"exam_kind": "imaging"},
    )

    result = parser.interpret_exam_context_answer("没有做过。", action)

    assert client.called is False
    assert result.availability == "not_done"
    assert result.needs_followup is False


def test_exam_context_result_maps_to_candidate_evidence() -> None:
    class FakeLlmClient:
        def is_available(self) -> bool:
            return True

        def run_structured_prompt(self, prompt_name: str, variables: dict, schema):
            _ = prompt_name, variables, schema
            return {
                "availability": "done",
                "mentioned_tests": ["CD4"],
                "mentioned_results": [
                    {"test_name": "CD4", "raw_text": "CD4 很低", "normalized_result": "low"},
                ],
                "needs_followup": False,
                "followup_reason": "",
                "reasoning": "已给出具体结果。",
            }

    parser = EvidenceParser(llm_client=FakeLlmClient())
    action = MctsAction(
        action_id="collect_exam::pcp::lab",
        action_type="collect_exam_context",
        target_node_id="__exam_context__::lab",
        target_node_label="ExamContext",
        target_node_name="化验检查情况",
        metadata={
            "exam_kind": "lab",
            "exam_candidate_evidence": [
                {
                    "node_id": "lab_cd4_low",
                    "label": "LabFinding",
                    "name": "CD4+ T淋巴细胞计数 < 200/μL",
                }
            ],
        },
    )
    result = parser.interpret_exam_context_answer("做过 CD4，医生说 CD4 很低。", action)

    updates = parser.build_slot_updates_from_exam_context(action, result, "做过 CD4，医生说 CD4 很低。", turn_index=2)

    assert len(updates) == 1
    assert updates[0].node_id == "lab_cd4_low"
    assert updates[0].status == "true"


def test_exam_context_negative_result_maps_to_false_candidate_evidence() -> None:
    class FakeLlmClient:
        def is_available(self) -> bool:
            return True

        def run_structured_prompt(self, prompt_name: str, variables: dict, schema):
            _ = prompt_name, variables, schema
            return {
                "availability": "done",
                "mentioned_tests": ["HIV RNA"],
                "mentioned_results": [
                    {"test_name": "HIV RNA", "raw_text": "HIV RNA 未检出", "normalized_result": "negative"},
                ],
                "needs_followup": False,
                "followup_reason": "",
                "reasoning": "已给出具体结果。",
            }

    parser = EvidenceParser(llm_client=FakeLlmClient())
    action = MctsAction(
        action_id="collect_exam::phase::lab",
        action_type="collect_exam_context",
        target_node_id="__exam_context__::lab",
        target_node_label="ExamContext",
        target_node_name="化验检查情况",
        metadata={
            "exam_kind": "lab",
            "exam_candidate_evidence": [
                {
                    "node_id": "lab_hiv_rna_high",
                    "label": "LabFinding",
                    "name": "HIV RNA 病毒载量升高",
                }
            ],
        },
    )
    result = parser.interpret_exam_context_answer("HIV RNA 未检出。", action)

    updates = parser.build_slot_updates_from_exam_context(action, result, "HIV RNA 未检出。", turn_index=2)

    assert len(updates) == 1
    assert updates[0].status == "false"
