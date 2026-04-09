"""测试 A4 演绎决策是否真正驱动下一阶段路由。"""

from brain.router import ReasoningRouter
from brain.types import A4DeductiveResult, HypothesisScore, MctsAction, SessionState


# 验证存在且确信且主假设优势明显时会进入终止阶段。
def test_router_stops_when_positive_evidence_and_margin_is_sufficient() -> None:
    router = ReasoningRouter()
    action = MctsAction(
        action_id="a1",
        action_type="verify_evidence",
        target_node_id="symptom_fever",
        target_node_label="Symptom",
        target_node_name="发热",
        hypothesis_id="d1",
    )
    state = SessionState(
        session_id="s1",
        candidate_hypotheses=[
            HypothesisScore(node_id="d1", label="Disease", name="肺孢子菌肺炎", score=2.5),
            HypothesisScore(node_id="d2", label="Disease", name="结核病", score=1.0),
        ],
    )

    decision = router.route_after_question_answer(
        A4DeductiveResult(existence="exist", certainty="confident", reasoning="明确存在"),
        action,
        state,
    )

    assert decision.stage == "STOP"


# 验证不存在且确信会回到 A2 重新审视假设。
def test_router_returns_to_a2_when_negative_evidence_is_confident() -> None:
    router = ReasoningRouter()
    action = MctsAction(
        action_id="a2",
        action_type="verify_evidence",
        target_node_id="lab_po2",
        target_node_label="LabFinding",
        target_node_name="低氧血症",
        hypothesis_id="d1",
    )
    state = SessionState(session_id="s2")

    decision = router.route_after_question_answer(
        A4DeductiveResult(existence="non_exist", certainty="confident", reasoning="明确不存在"),
        action,
        state,
    )

    assert decision.stage == "A2"
