"""测试证据解析器在 A1 / A4 下的关键判断逻辑。"""

from brain.evidence_parser import EvidenceParser
from brain.types import MctsAction


# 验证“没有特别注意到”不会被误判为阳性。
def test_a4_unknown_reply_is_not_misclassified_as_positive() -> None:
    parser = EvidenceParser()
    action = MctsAction(
        action_id="a1",
        action_type="verify_evidence",
        target_node_id="node_x",
        target_node_label="LabFinding",
        target_node_name="ALT > 5 × ULN",
    )

    result = parser.run_a4_deductive_analysis("没有特别注意到。", action)

    assert result.existence == "unknown"
    assert result.certainty == "doubt"


# 验证明确否定回答会被识别为不存在且确信。
def test_a4_negative_reply_is_classified_as_non_exist() -> None:
    parser = EvidenceParser()
    action = MctsAction(
        action_id="a2",
        action_type="verify_evidence",
        target_node_id="node_y",
        target_node_label="Symptom",
        target_node_name="发热",
    )

    result = parser.run_a4_deductive_analysis("没有。", action)

    assert result.existence == "non_exist"
    assert result.certainty == "confident"


# 验证 target-aware 解析会提取否定片段，而不是只看整句情绪。
def test_a4_target_aware_parser_extracts_negation_span() -> None:
    parser = EvidenceParser()
    action = MctsAction(
        action_id="a3",
        action_type="verify_evidence",
        target_node_id="node_fever",
        target_node_label="Symptom",
        target_node_name="发热",
    )

    result = parser.run_a4_deductive_analysis("发热没有，但是有点咳嗽。", action)

    assert result.existence == "non_exist"
    assert "发热没有" in result.negation_span


# 验证模糊回答会保留 uncertain span，供后续 A4 judge / router 使用。
def test_a4_target_aware_parser_extracts_uncertain_span() -> None:
    parser = EvidenceParser()
    action = MctsAction(
        action_id="a4",
        action_type="verify_evidence",
        target_node_id="node_fever",
        target_node_label="Symptom",
        target_node_name="发热",
    )

    result = parser.run_a4_deductive_analysis("好像有点发热。", action)

    assert result.existence == "exist"
    assert result.certainty == "doubt"
    assert "好像有点发热" in result.uncertain_span


# 验证检查上下文回答能一次性识别做过检查、检查名称和结果。
def test_exam_context_parser_extracts_tests_and_results() -> None:
    parser = EvidenceParser()
    action = MctsAction(
        action_id="collect_exam::pcp::lab",
        action_type="collect_exam_context",
        target_node_id="__exam_context__::lab",
        target_node_label="ExamContext",
        target_node_name="化验检查情况",
        metadata={"exam_kind": "lab"},
    )

    result = parser.interpret_exam_context_answer("做过 CD4 和 β-D 葡聚糖，CD4 很低，G 试验阳性。", action)

    assert result.availability == "done"
    assert "CD4" in result.mentioned_tests
    assert "β-D 葡聚糖" in result.mentioned_tests
    assert len(result.mentioned_results) >= 2
    assert result.needs_followup is False


# 验证检查上下文结果可以映射回当前 R2 候选证据节点。
def test_exam_context_result_maps_to_candidate_evidence() -> None:
    parser = EvidenceParser()
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
    assert updates[0].certainty == "certain"


# 验证患者没做过检查时，不会要求追问具体结果。
def test_exam_context_parser_marks_not_done_without_followup() -> None:
    parser = EvidenceParser()
    action = MctsAction(
        action_id="collect_exam::pcp::imaging",
        action_type="collect_exam_context",
        target_node_id="__exam_context__::imaging",
        target_node_label="ExamContext",
        target_node_name="胸部影像检查情况",
        metadata={"exam_kind": "imaging"},
    )

    result = parser.interpret_exam_context_answer("最近没有做过胸部 CT。", action)

    assert result.availability == "not_done"
    assert result.needs_followup is False
