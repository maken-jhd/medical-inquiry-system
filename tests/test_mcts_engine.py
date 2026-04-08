"""测试 UCT 选择器的基础动作排序行为。"""

from brain.mcts_engine import MctsEngine
from brain.types import ActionStats, MctsAction, SessionState, SimulationOutcome, StateVisitStats


# 验证 UCT 选择器会优先选择 simulation 收益更高的动作。
def test_mcts_engine_prefers_higher_simulation_reward() -> None:
    engine = MctsEngine()
    state = SessionState(
        session_id="s1",
        action_stats={
            "a1": ActionStats(action_id="a1", visit_count=2, total_value=1.0, average_value=0.5),
            "a2": ActionStats(action_id="a2", visit_count=2, total_value=1.0, average_value=0.5),
        },
        state_visit_stats={
            "sig": StateVisitStats(state_signature="sig", visit_count=4),
        },
    )
    a1 = MctsAction(
        action_id="a1",
        action_type="verify_evidence",
        target_node_id="n1",
        target_node_label="Symptom",
        target_node_name="发热",
        prior_score=1.0,
    )
    a2 = MctsAction(
        action_id="a2",
        action_type="verify_evidence",
        target_node_id="n2",
        target_node_label="LabFinding",
        target_node_name="低氧血症",
        prior_score=1.0,
    )
    outcomes = [
        SimulationOutcome(action_id="a1", expected_reward=0.2),
        SimulationOutcome(action_id="a2", expected_reward=0.9),
    ]

    selected = engine.select_action([a1, a2], state, outcomes, state_signature="sig")

    assert selected is not None
    assert selected.action_id == "a2"
