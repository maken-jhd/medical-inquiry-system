"""测试局部 simulation 对候选动作的基础估值逻辑。"""

from brain.simulation_engine import SimulationEngine
from brain.types import HypothesisCandidate, MctsAction, SessionState


# 验证高价值关系类型会得到更高的预演收益。
def test_simulation_engine_gives_higher_reward_to_lab_finding_action() -> None:
    engine = SimulationEngine()
    hypothesis = HypothesisCandidate(node_id="d1", name="肺孢子菌肺炎", score=3.0)
    state = SessionState(session_id="s1")
    lab_action = MctsAction(
        action_id="a1",
        action_type="verify_evidence",
        target_node_id="n1",
        target_node_label="LabFinding",
        target_node_name="低氧血症",
        prior_score=2.0,
        metadata={"relation_type": "HAS_LAB_FINDING", "is_red_flag": True},
    )
    detail_action = MctsAction(
        action_id="a2",
        action_type="verify_evidence",
        target_node_id="n2",
        target_node_label="ClinicalAttribute",
        target_node_name="症状持续时间",
        prior_score=2.0,
        metadata={"relation_type": "REQUIRES_DETAIL", "is_red_flag": False},
    )

    lab_outcome = engine.simulate_action(lab_action, state, hypothesis)
    detail_outcome = engine.simulate_action(detail_action, state, hypothesis)

    assert lab_outcome.expected_reward > detail_outcome.expected_reward
