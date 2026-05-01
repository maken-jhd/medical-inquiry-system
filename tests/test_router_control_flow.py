"""测试上一轮动作解释是否真正驱动下一阶段路由。"""

from brain.router import ReasoningRouter
from brain.types import HypothesisScore, MctsAction, PendingActionResult, SessionState


# 验证存在且回答清晰且主假设优势明显时会进入终止阶段。
def test_router_stops_when_positive_evidence_and_margin_is_sufficient() -> None:
    router = ReasoningRouter()
    action = MctsAction(
        action_id="a1",
        action_type="verify_evidence",
        target_node_id="symptom_fever",
        target_node_label="ClinicalFinding",
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

    decision = router.route_after_pending_action(
        PendingActionResult(polarity="present", resolution="clear", reasoning="明确存在"),
        action,
        state,
    )

    assert decision.stage == "STOP"
    assert decision.metadata["should_terminate_current_path"] is True


# 验证不存在且回答清晰会回到 A2 重新审视假设。
def test_router_returns_to_a2_when_negative_evidence_is_clear() -> None:
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

    decision = router.route_after_pending_action(
        PendingActionResult(polarity="absent", resolution="clear", reasoning="明确不存在"),
        action,
        state,
    )

    assert decision.stage == "A2"
    assert decision.metadata["should_spawn_alternative_hypotheses"] is True


# 验证 router 会优先消费统一提及链路写入的 polarity，而不只依赖旧 existence 字段。
def test_router_prefers_metadata_polarity_from_turn_interpreter() -> None:
    router = ReasoningRouter()
    action = MctsAction(
        action_id="a3",
        action_type="verify_evidence",
        target_node_id="symptom_cough",
        target_node_label="ClinicalFinding",
        target_node_name="咳嗽",
        hypothesis_id="d1",
    )
    state = SessionState(session_id="s3")

    decision = router.route_after_pending_action(
        PendingActionResult(
            polarity="absent",
            resolution="clear",
            reasoning="统一提及抽取器命中咳嗽为明确不存在。",
        ),
        action,
        state,
    )

    assert decision.stage == "A2"
    assert decision.metadata["polarity"] == "absent"
