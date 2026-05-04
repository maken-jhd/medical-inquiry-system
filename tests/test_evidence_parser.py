"""测试 EvidenceParser 在 LLM-first 模式下的 A1 / pending-action / exam_context 行为。"""

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
                "mentions": [],
                "reasoning_summary": "LLM 未提取到核心线索。",
            }

    parser = EvidenceParser(llm_client=FakeLlmClient())

    with pytest.raises(LlmEmptyExtractionError):
        parser.run_a1_key_symptom_extraction(PatientContext(raw_text="最近主要是畏光，还伴有视力下降。"))


# 验证 service 兜底传入 empty_extraction_fallback 时，A1 不再二次触发空抽取失败。
def test_a1_empty_extraction_fallback_returns_none_salient() -> None:
    parser = EvidenceParser(llm_client=None)

    result = parser.run_a1_key_symptom_extraction(
        PatientContext(raw_text="症状比较轻微，来问问。", metadata={"empty_extraction_fallback": True})
    )

    assert result.key_features == []
    assert result.selection_decision == "none_salient"


def test_a1_normalizes_llm_feature_names() -> None:
    class FakeLlmClient:
        def is_available(self) -> bool:
            return True

        def run_structured_prompt(self, prompt_name: str, variables: dict, schema):
            _ = prompt_name, variables, schema
            return {
                "mentions": [
                    {"name": "咳嗽", "polarity": "present", "evidence_span": "最近咳嗽"},
                    {"name": "艾滋病", "polarity": "present", "evidence_span": "担心艾滋病"},
                ],
                "selection_decision": "selected",
                "reasoning_summary": "已提取关键线索。",
            }

    parser = EvidenceParser(llm_client=FakeLlmClient())
    result = parser.run_a1_key_symptom_extraction(PatientContext(raw_text="最近咳嗽，还担心艾滋病。"))

    feature_names = {item.normalized_name for item in result.key_features}
    assert "干咳" in feature_names
    assert "HIV感染" in feature_names


def test_pending_action_result_uses_turn_interpreter() -> None:
    class FakeLlmClient:
        def is_available(self) -> bool:
            return True

        def run_structured_prompt(self, prompt_name: str, variables: dict, schema):
            assert prompt_name == "turn_interpreter"
            assert variables["pending_target_name"] == "发热"
            return {
                "mentions": [
                    {"name": "发热", "polarity": "absent", "evidence_span": "发热没有", "reasoning": "患者明确否认发热。"},
                    {"name": "咳嗽", "polarity": "present", "evidence_span": "有点咳嗽"},
                ],
                "reasoning_summary": "已识别目标症状是否定，同时提到新的症状。",
            }

    parser = EvidenceParser(llm_client=FakeLlmClient())
    action = MctsAction(
        action_id="a3",
        action_type="verify_evidence",
        target_node_id="node_fever",
        target_node_label="ClinicalFinding",
        target_node_name="发热",
    )

    result = parser.derive_pending_action_result_from_text("发热没有，但是有点咳嗽。", action)

    assert result.polarity == "absent"
    assert result.negation_span == "发热没有"
    assert result.metadata["interpretation_source"] == "turn_interpreter"


def test_pending_action_short_direct_reply_skips_llm() -> None:
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
    interpretation = parser.derive_pending_action_result_from_text("没有步态异常。", action)

    assert client.called is False
    assert interpretation.polarity == "absent"
    assert interpretation.metadata["interpretation_source"] == "turn_interpreter"


# 验证高成本检查的否定短答会交回 LLM 解析，不再被直接截成明确 absent。
def test_pending_action_high_cost_negative_reply_defers_to_llm_as_unclear() -> None:
    class FakeLlmClient:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def is_available(self) -> bool:
            return True

        def run_structured_prompt(self, prompt_name: str, variables: dict, schema):
            self.prompts.append(prompt_name)
            _ = variables, schema
            if prompt_name == "turn_interpreter":
                return {
                    "mentions": [
                        {
                            "name": "头颅CT低密度病灶",
                            "polarity": "unclear",
                            "evidence_span": "没做过这项检查",
                            "reasoning": "患者描述的是未做检查而不是明确阴性结果。",
                        }
                    ],
                    "reasoning_summary": "高成本检查的否定短答应按未检查/不确定处理。",
                }
            return {}

    client = FakeLlmClient()
    parser = EvidenceParser(llm_client=client)
    action = MctsAction(
        action_id="a6",
        action_type="verify_evidence",
        target_node_id="node_ct",
        target_node_label="ImagingFinding",
        target_node_name="头颅CT低密度病灶",
        hypothesis_id="d1",
        topic_id="Disease",
        metadata={
            "question_type_hint": "imaging",
            "acquisition_mode": "needs_imaging",
            "evidence_cost": "high",
            "relation_type": "HAS_IMAGING_FINDING",
        },
    )

    interpretation = parser.derive_pending_action_result_from_text("没做过这项检查。", action)

    assert client.prompts == ["turn_interpreter"]
    assert interpretation.polarity == "unclear"
    assert interpretation.resolution == "hedged"


def test_pending_action_unclear_answer_marks_hedged() -> None:
    class FakeLlmClient:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def is_available(self) -> bool:
            return True

        def run_structured_prompt(self, prompt_name: str, variables: dict, schema):
            _ = variables, schema
            self.prompts.append(prompt_name)
            if prompt_name == "turn_interpreter":
                return {
                    "mentions": [
                        {
                            "name": "发热",
                            "polarity": "unclear",
                            "evidence_span": "好像有点发热",
                            "reasoning": "患者给出了模糊阳性回答。",
                        }
                    ],
                    "reasoning_summary": "目标症状存在但回答保留。",
                }
            return {}

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

    interpretation = parser.derive_pending_action_result_from_text("好像有点发热。", action)

    assert client.prompts == ["turn_interpreter"]
    assert interpretation.polarity == "unclear"
    assert interpretation.resolution == "hedged"


def test_pending_action_long_answer_raises_when_llm_unavailable() -> None:
    parser = EvidenceParser()
    action = MctsAction(
        action_id="a4",
        action_type="verify_evidence",
        target_node_id="node_fever",
        target_node_label="ClinicalFinding",
        target_node_name="发热",
    )

    with pytest.raises(LlmUnavailableError):
        parser.derive_pending_action_result_from_text("发热没有，但是有点咳嗽。", action)


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
