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
        target_node_label="ClinicalFinding",
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
        target_node_label="ClinicalFinding",
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
        target_node_label="ClinicalFinding",
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


# 验证统一检查入口可以一次识别化验、影像和病原学检查名。
def test_general_exam_context_parser_extracts_mixed_exam_names() -> None:
    parser = EvidenceParser()
    action = MctsAction(
        action_id="collect_exam::pcp::general",
        action_type="collect_general_exam_context",
        target_node_id="__exam_context__::general",
        target_node_label="ExamContext",
        target_node_name="医院检查情况",
        metadata={"exam_kind": "general"},
    )

    result = parser.interpret_exam_context_answer("做过 CD4、胸部 CT 和 PCR，但具体结果不太记得了。", action)

    assert result.availability == "done"
    assert "CD4" in result.mentioned_tests
    assert "胸部CT" in result.mentioned_tests
    assert "PCR" in result.mentioned_tests
    assert set(result.metadata["mentioned_exam_kinds"]) == {"lab", "imaging", "pathogen"}
    assert result.needs_followup is True


# 验证统一检查入口下给出结果时，仍能映射到不同内部检查类别的候选证据。
def test_general_exam_context_result_maps_to_mixed_candidate_evidence() -> None:
    parser = EvidenceParser()
    action = MctsAction(
        action_id="collect_exam::pcp::general",
        action_type="collect_general_exam_context",
        target_node_id="__exam_context__::general",
        target_node_label="ExamContext",
        target_node_name="医院检查情况",
        metadata={
            "exam_kind": "general",
            "exam_candidate_evidence": [
                {
                    "node_id": "lab_cd4_low",
                    "label": "LabFinding",
                    "name": "CD4+ T淋巴细胞计数 < 200/μL",
                    "exam_kind": "lab",
                },
                {
                    "node_id": "ct_ground_glass",
                    "label": "ImagingFinding",
                    "name": "胸部CT磨玻璃影",
                    "exam_kind": "imaging",
                },
            ],
        },
    )
    text = "做过 CD4 和胸部 CT，CD4 大概 150，CT 说有磨玻璃影。"
    result = parser.interpret_exam_context_answer(text, action)

    updates = parser.build_slot_updates_from_exam_context(action, result, text, turn_index=2)

    assert {item.node_id for item in updates} == {"lab_cd4_low", "ct_ground_glass"}
    assert all(item.status == "true" for item in updates)


# 验证做过检查但只给出检查名时，会进入“追问具体结果”分支。
def test_exam_context_parser_done_with_test_names_needs_specific_result_followup() -> None:
    parser = EvidenceParser()
    action = MctsAction(
        action_id="collect_exam::pcp::lab",
        action_type="collect_exam_context",
        target_node_id="__exam_context__::lab",
        target_node_label="ExamContext",
        target_node_name="化验检查情况",
        metadata={"exam_kind": "lab"},
    )

    result = parser.interpret_exam_context_answer("做过 CD4 和 β-D 葡聚糖，但具体结果不太记得了。", action)

    assert result.availability == "done"
    assert "CD4" in result.mentioned_tests
    assert "β-D 葡聚糖" in result.mentioned_tests
    assert result.mentioned_results == []
    assert result.needs_followup is True
    assert result.followup_reason == "mentioned_tests_without_results"


# 验证做过检查但回答模糊时，不会误写入具体证据，而是要求澄清。
def test_exam_context_parser_done_with_vague_imaging_answer_needs_followup() -> None:
    parser = EvidenceParser()
    action = MctsAction(
        action_id="collect_exam::pcp::imaging",
        action_type="collect_exam_context",
        target_node_id="__exam_context__::imaging",
        target_node_label="ExamContext",
        target_node_name="胸部影像检查情况",
        metadata={"exam_kind": "imaging"},
    )

    result = parser.interpret_exam_context_answer("好像拍过胸片，但报告具体写了什么我记不清。", action)

    assert result.availability == "done"
    assert "胸片" in result.mentioned_tests
    assert result.mentioned_results == []
    assert result.needs_followup is True


# 验证病原学检查上下文也能识别做过且给出阳性结果。
def test_exam_context_parser_extracts_pathogen_result() -> None:
    parser = EvidenceParser()
    action = MctsAction(
        action_id="collect_exam::pcp::pathogen",
        action_type="collect_exam_context",
        target_node_id="__exam_context__::pathogen",
        target_node_label="ExamContext",
        target_node_name="病原学检查情况",
        metadata={"exam_kind": "pathogen"},
    )

    result = parser.interpret_exam_context_answer("做过 PCR，结果是阳性。", action)

    assert result.availability == "done"
    assert "PCR" in result.mentioned_tests
    assert len(result.mentioned_results) == 1
    assert result.mentioned_results[0].normalized_result == "positive"
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


# 验证真实报告式数值结果可以直接映射到具体高价值证据节点。
def test_exam_context_numeric_cd4_result_maps_to_candidate_evidence() -> None:
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
    result = parser.interpret_exam_context_answer("做过 CD4，结果大概是 150。", action)

    updates = parser.build_slot_updates_from_exam_context(action, result, "做过 CD4，结果大概是 150。", turn_index=2)

    assert len(updates) == 1
    assert updates[0].node_id == "lab_cd4_low"
    assert updates[0].status == "true"
    assert updates[0].certainty == "certain"


# 验证 β-D 葡聚糖数值升高可以被识别为支持性实验室证据。
def test_exam_context_numeric_beta_d_glucan_result_maps_to_candidate_evidence() -> None:
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
                    "node_id": "lab_bdg_high",
                    "label": "LabFinding",
                    "name": "(1,3)-β-D-葡聚糖检测升高",
                }
            ],
        },
    )
    result = parser.interpret_exam_context_answer("β-D 葡聚糖结果是 200。", action)

    updates = parser.build_slot_updates_from_exam_context(action, result, "β-D 葡聚糖结果是 200。", turn_index=2)

    assert len(updates) == 1
    assert updates[0].node_id == "lab_bdg_high"
    assert updates[0].status == "true"


# 验证“未检出”类结果会映射成对应候选证据不存在。
def test_exam_context_hiv_rna_undetected_maps_to_negative_candidate_evidence() -> None:
    parser = EvidenceParser()
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
    assert updates[0].node_id == "lab_hiv_rna_high"
    assert updates[0].status == "false"


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
