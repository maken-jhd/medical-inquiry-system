"""测试 rollout transition model 的基础分支概率行为。"""

from brain.response_transition_model import HeuristicResponseTransitionModel
from brain.types import MctsAction, SessionState


# 验证启发式 transition model 输出的是合法概率分布。
def test_transition_model_probabilities_sum_to_one() -> None:
    model = HeuristicResponseTransitionModel()
    action = MctsAction(
        action_id="a_transition",
        action_type="verify_evidence",
        target_node_id="node_fever",
        target_node_label="ClinicalFinding",
        target_node_name="发热",
        metadata={"relation_type": "MANIFESTS_AS"},
    )

    branches = model.predict_branches(action, SessionState(session_id="s_transition"))

    assert len(branches) == 3
    assert round(sum(branch.probability for branch in branches), 6) == 1.0


# 验证 red_flag / asked_before / relation_type 等启发仍会影响分支概率。
def test_transition_model_respects_action_heuristics() -> None:
    model = HeuristicResponseTransitionModel()
    base_state = SessionState(session_id="s_transition_base")
    repeated_state = SessionState(session_id="s_transition_repeat", asked_node_ids=["node_lab"])
    base_action = MctsAction(
        action_id="a_base",
        action_type="verify_evidence",
        target_node_id="node_lab",
        target_node_label="LabFinding",
        target_node_name="低氧血症",
        metadata={"relation_type": "MANIFESTS_AS"},
    )
    red_flag_action = MctsAction(
        action_id="a_red",
        action_type="verify_evidence",
        target_node_id="node_lab_red",
        target_node_label="LabFinding",
        target_node_name="低氧血症",
        metadata={"relation_type": "HAS_LAB_FINDING", "is_red_flag": True},
    )
    detail_action = MctsAction(
        action_id="a_detail",
        action_type="verify_evidence",
        target_node_id="node_detail",
        target_node_label="ClinicalAttribute",
        target_node_name="症状持续时间",
        metadata={"relation_type": "REQUIRES_DETAIL"},
    )

    base_positive = next(
        branch.probability
        for branch in model.predict_branches(base_action, base_state)
        if branch.branch_name == "positive"
    )
    red_flag_positive = next(
        branch.probability
        for branch in model.predict_branches(red_flag_action, base_state)
        if branch.branch_name == "positive"
    )
    repeated_positive = next(
        branch.probability
        for branch in model.predict_branches(base_action, repeated_state)
        if branch.branch_name == "positive"
    )
    detail_positive = next(
        branch.probability
        for branch in model.predict_branches(detail_action, base_state)
        if branch.branch_name == "positive"
    )

    assert red_flag_positive > base_positive
    assert repeated_positive < base_positive
    assert detail_positive < base_positive
