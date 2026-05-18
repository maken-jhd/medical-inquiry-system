"""测试 rollout reward model 的基础拆解与惩罚项。"""

from brain.search import TransitionBranch
from brain.search import HeuristicRolloutRewardModel
from brain.state import MctsAction, PatientContext, SessionState


# 验证 reward model 会输出 breakdown，并对高成本与重复动作施加惩罚。
def test_reward_model_exposes_breakdown_and_penalties() -> None:
    model = HeuristicRolloutRewardModel()
    state = SessionState(session_id="s_reward", asked_node_ids=["node_lab"])
    action = MctsAction(
        action_id="a_reward",
        action_type="verify_evidence",
        target_node_id="node_lab",
        target_node_label="LabFinding",
        target_node_name="低氧血症",
        prior_score=2.0,
        metadata={
            "relation_type": "HAS_LAB_FINDING",
            "evidence_cost": "high",
            "question_type_hint": "lab",
            "contradiction_priority": 0.6,
        },
    )
    branch = TransitionBranch(
        branch_name="positive",
        probability=0.7,
        polarity="present",
        resolution="clear",
    )

    result = model.evaluate_branch(
        session_state=state,
        action=action,
        branch=branch,
        patient_context=PatientContext(raw_text="最近检查提示低氧血症。"),
    )

    assert result.reward >= 0.0
    assert "information_gain_surrogate" in result.metadata
    assert result.metadata["repeat_penalty"] > 0.0
    assert result.metadata["high_cost_penalty"] > 0.0


# 验证不同回答分支会得到不同 reward，而不是继续共用一个硬编码结果。
def test_reward_model_distinguishes_positive_negative_and_doubtful_branches() -> None:
    model = HeuristicRolloutRewardModel()
    state = SessionState(session_id="s_reward_branch")
    action = MctsAction(
        action_id="a_reward_branch",
        action_type="verify_evidence",
        target_node_id="node_symptom",
        target_node_label="ClinicalFinding",
        target_node_name="干咳",
        prior_score=1.4,
        metadata={
            "relation_type": "MANIFESTS_AS",
            "evidence_cost": "low",
            "question_type_hint": "symptom",
            "contradiction_priority": 0.45,
        },
    )
    positive = model.evaluate_branch(
        session_state=state,
        action=action,
        branch=TransitionBranch("positive", 0.5, "present", "clear"),
    )
    negative = model.evaluate_branch(
        session_state=state,
        action=action,
        branch=TransitionBranch("negative", 0.3, "absent", "clear"),
    )
    doubtful = model.evaluate_branch(
        session_state=state,
        action=action,
        branch=TransitionBranch("doubtful", 0.2, "unclear", "hedged"),
    )

    assert positive.reward > doubtful.reward
    assert positive.reward != negative.reward
    assert doubtful.metadata["uncertainty_penalty"] > 0.0
