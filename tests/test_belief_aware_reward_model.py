"""测试 belief-aware reward model 会利用候选 belief 与分支区分度。"""

from brain.response_transition_model import TransitionBranch
from brain.reward_model import BeliefAwareRolloutRewardModel, RolloutRewardModelConfig
from brain.types import HypothesisScore, MctsAction, SessionState


def _candidate_hypotheses() -> list[HypothesisScore]:
    return [
        HypothesisScore(
            node_id="pcp",
            label="Disease",
            name="肺孢子菌肺炎",
            score=0.62,
            metadata={
                "evidence_node_ids": ["slot_cough", "lab_ldh"],
                "relation_types": ["MANIFESTS_AS", "HAS_LAB_FINDING"],
            },
        ),
        HypothesisScore(
            node_id="tb",
            label="Disease",
            name="肺结核",
            score=0.38,
            metadata={
                "evidence_node_ids": ["slot_cough", "img_cavity"],
                "relation_types": ["MANIFESTS_AS", "HAS_IMAGING_FINDING"],
            },
        ),
    ]


def _verify_components() -> list[dict]:
    return [
        {
            "disease_id": "pcp",
            "weight": 0.62,
            "raw_score": 0.62,
            "backoff_level": "disease_family_question_type",
            "total_count": 12.0,
            "present_probability": 0.82,
            "absent_probability": 0.08,
            "unclear_probability": 0.10,
        },
        {
            "disease_id": "tb",
            "weight": 0.38,
            "raw_score": 0.38,
            "backoff_level": "disease_family_question_type",
            "total_count": 10.0,
            "present_probability": 0.24,
            "absent_probability": 0.56,
            "unclear_probability": 0.20,
        },
    ]


def _exam_components() -> list[dict]:
    return [
        {
            "disease_id": "pcp",
            "weight": 0.62,
            "raw_score": 0.62,
            "availability_backoff": "disease_exam_kind",
            "availability_total_count": 8.0,
            "result_backoff": "disease_test_type",
            "result_total_count": 7.0,
            "done_positive_probability": 0.68,
            "done_negative_probability": 0.06,
            "done_unclear_probability": 0.12,
            "not_done_probability": 0.14,
        },
        {
            "disease_id": "tb",
            "weight": 0.38,
            "raw_score": 0.38,
            "availability_backoff": "disease_exam_kind",
            "availability_total_count": 7.0,
            "result_backoff": "disease_test_type",
            "result_total_count": 7.0,
            "done_positive_probability": 0.22,
            "done_negative_probability": 0.33,
            "done_unclear_probability": 0.18,
            "not_done_probability": 0.27,
        },
    ]


# 区分度更高的正向分支应获得更高 reward，而不是与模糊分支近似等价。
def test_belief_aware_reward_prefers_margin_separating_branch() -> None:
    model = BeliefAwareRolloutRewardModel(RolloutRewardModelConfig(model_type="belief_aware_v1"))
    state = SessionState(session_id="s_reward_margin")
    action = MctsAction(
        action_id="verify::cough",
        action_type="verify_evidence",
        target_node_id="slot_cough",
        target_node_label="ClinicalFinding",
        target_node_name="干咳",
        prior_score=1.9,
        metadata={
            "relation_type": "MANIFESTS_AS",
            "question_type_hint": "symptom",
            "evidence_cost": "low",
            "contradiction_priority": 0.45,
        },
    )
    candidates = _candidate_hypotheses()
    positive = model.evaluate_branch(
        session_state=state,
        action=action,
        branch=TransitionBranch(
            branch_name="positive",
            probability=0.60,
            polarity="present",
            resolution="clear",
            metadata={
                "source": "statistical",
                "branch_schema": "verify",
                "belief_components": _verify_components(),
            },
        ),
        candidate_hypotheses=candidates,
    )
    doubtful = model.evaluate_branch(
        session_state=state,
        action=action,
        branch=TransitionBranch(
            branch_name="doubtful",
            probability=0.18,
            polarity="unclear",
            resolution="hedged",
            metadata={
                "source": "statistical",
                "branch_schema": "verify",
                "belief_components": _verify_components(),
            },
        ),
        candidate_hypotheses=candidates,
    )

    assert positive.reward > doubtful.reward
    assert positive.metadata["top1_top2_margin_gain_surrogate"] > doubtful.metadata["top1_top2_margin_gain_surrogate"]
    assert positive.metadata["uncertainty_reduction_surrogate"] > doubtful.metadata["uncertainty_reduction_surrogate"]


# unclear / not_done 分支应被合理惩罚，避免高成本检查的模糊回答被误当成高价值收敛信号。
def test_belief_aware_reward_penalizes_not_done_exam_branch() -> None:
    model = BeliefAwareRolloutRewardModel(RolloutRewardModelConfig(model_type="belief_aware_v1"))
    state = SessionState(session_id="s_reward_exam")
    action = MctsAction(
        action_id="exam::lab",
        action_type="collect_exam_context",
        target_node_id="__exam_context__::lab",
        target_node_label="ExamContext",
        target_node_name="化验检查情况",
        prior_score=1.5,
        metadata={
            "relation_type": "DIAGNOSED_BY",
            "question_type_hint": "exam_context",
            "exam_kind": "lab",
            "evidence_cost": "high",
            "contradiction_priority": 0.35,
        },
    )
    candidates = _candidate_hypotheses()
    done_positive = model.evaluate_branch(
        session_state=state,
        action=action,
        branch=TransitionBranch(
            branch_name="done_positive",
            probability=0.51,
            polarity="present",
            resolution="clear",
            metadata={
                "source": "statistical",
                "branch_schema": "exam_context",
                "belief_components": _exam_components(),
            },
        ),
        candidate_hypotheses=candidates,
    )
    not_done = model.evaluate_branch(
        session_state=state,
        action=action,
        branch=TransitionBranch(
            branch_name="not_done",
            probability=0.19,
            polarity="unclear",
            resolution="hedged",
            metadata={
                "source": "statistical",
                "branch_schema": "exam_context",
                "belief_components": _exam_components(),
            },
        ),
        candidate_hypotheses=candidates,
    )

    assert done_positive.reward > not_done.reward
    assert not_done.metadata["uncertainty_penalty"] > 0.0
    assert not_done.metadata["acceptance_risk_penalty"] > 0.0


# 高成本但区分度弱、统计支持也弱的动作，其 reward 应明显低于低成本高区分度动作。
def test_belief_aware_reward_penalizes_high_cost_low_value_action() -> None:
    model = BeliefAwareRolloutRewardModel(RolloutRewardModelConfig(model_type="belief_aware_v1"))
    state = SessionState(session_id="s_reward_cost")
    candidates = _candidate_hypotheses()
    low_cost_action = MctsAction(
        action_id="verify::cough::low",
        action_type="verify_evidence",
        target_node_id="slot_cough",
        target_node_label="ClinicalFinding",
        target_node_name="干咳",
        prior_score=1.8,
        metadata={
            "relation_type": "MANIFESTS_AS",
            "question_type_hint": "symptom",
            "evidence_cost": "low",
        },
    )
    high_cost_action = MctsAction(
        action_id="verify::ldh::high",
        action_type="verify_evidence",
        target_node_id="lab_ldh",
        target_node_label="LabFinding",
        target_node_name="乳酸脱氢酶升高",
        prior_score=0.8,
        metadata={
            "relation_type": "HAS_LAB_FINDING",
            "question_type_hint": "lab",
            "evidence_cost": "high",
        },
    )

    low_cost_result = model.evaluate_branch(
        session_state=state,
        action=low_cost_action,
        branch=TransitionBranch(
            branch_name="positive",
            probability=0.60,
            polarity="present",
            resolution="clear",
            metadata={
                "source": "statistical",
                "branch_schema": "verify",
                "belief_components": _verify_components(),
            },
        ),
        candidate_hypotheses=candidates,
    )
    high_cost_result = model.evaluate_branch(
        session_state=state,
        action=high_cost_action,
        branch=TransitionBranch(
            branch_name="positive",
            probability=0.34,
            polarity="present",
            resolution="clear",
            metadata={
                "source": "statistical",
                "branch_schema": "verify",
                "belief_components": [
                    {
                        "disease_id": "pcp",
                        "weight": 0.62,
                        "raw_score": 0.62,
                        "backoff_level": "global",
                        "total_count": 1.0,
                        "present_probability": 0.40,
                        "absent_probability": 0.30,
                        "unclear_probability": 0.30,
                    },
                    {
                        "disease_id": "tb",
                        "weight": 0.38,
                        "raw_score": 0.38,
                        "backoff_level": "global",
                        "total_count": 1.0,
                        "present_probability": 0.28,
                        "absent_probability": 0.40,
                        "unclear_probability": 0.32,
                    },
                ],
            },
        ),
        candidate_hypotheses=candidates,
    )

    assert low_cost_result.reward > high_cost_result.reward
    assert "belief_entropy_surrogate" in low_cost_result.metadata
    assert "acceptance_risk_proxy" in high_cost_result.metadata
