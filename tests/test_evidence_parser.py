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
