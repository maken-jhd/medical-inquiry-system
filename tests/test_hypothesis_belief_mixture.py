"""测试候选假设 belief mixture 的归一化与输出偏置效果。"""

from brain.response_transition_model import ResponseTransitionModelConfig, StatisticalResponseTransitionModel
from brain.transition_statistics import TransitionStatistics, build_normalized_hypothesis_belief
from brain.types import HypothesisScore, MctsAction, SessionState


# 验证 score 会被归一化成稳定的 top-k belief 权重。
def test_build_normalized_hypothesis_belief_normalizes_scores() -> None:
    belief = build_normalized_hypothesis_belief(
        [
            HypothesisScore(node_id="d1", label="Disease", name="PCP", score=3.0),
            HypothesisScore(node_id="d2", label="Disease", name="结核病", score=1.0),
            HypothesisScore(node_id="d3", label="Disease", name="隐球菌病", score=0.2),
        ],
        top_k=2,
    )

    assert len(belief) == 2
    assert round(sum(item.weight for item in belief), 6) == 1.0
    assert belief[0].disease_id == "d1"
    assert belief[0].weight > belief[1].weight


# 验证 statistical transition model 会真正消费 belief mixture，而不是只看 primary hypothesis。
def test_statistical_transition_model_shifts_distribution_with_belief_mixture() -> None:
    statistics = TransitionStatistics(smoothing_alpha=0.01, min_total_count=1)
    for _ in range(9):
        statistics.record_verify_observation(
            disease_id="d1",
            evidence_family="respiratory_symptom",
            question_type="symptom",
            outcome="present",
        )
        statistics.record_verify_observation(
            disease_id="d2",
            evidence_family="respiratory_symptom",
            question_type="symptom",
            outcome="absent",
        )
    statistics.record_verify_observation(
        disease_id="d1",
        evidence_family="respiratory_symptom",
        question_type="symptom",
        outcome="absent",
    )
    statistics.record_verify_observation(
        disease_id="d2",
        evidence_family="respiratory_symptom",
        question_type="symptom",
        outcome="present",
    )
    model = StatisticalResponseTransitionModel(
        ResponseTransitionModelConfig(
            model_type="statistical",
            statistics_top_k_hypotheses=2,
            enable_belief_mixture=True,
        ),
        statistics=statistics,
    )
    action = MctsAction(
        action_id="a_mix",
        action_type="verify_evidence",
        target_node_id="slot_cough",
        target_node_label="ClinicalFinding",
        target_node_name="干咳",
        metadata={
            "question_type_hint": "symptom",
            "evidence_families": ["respiratory_symptom"],
        },
    )
    optimistic_branches = model.predict_branches(
        action,
        SessionState(session_id="s_mix_1"),
        candidate_hypotheses=[
            HypothesisScore(node_id="d1", label="Disease", name="PCP", score=0.8),
            HypothesisScore(node_id="d2", label="Disease", name="结核病", score=0.2),
        ],
    )
    skeptical_branches = model.predict_branches(
        action,
        SessionState(session_id="s_mix_2"),
        candidate_hypotheses=[
            HypothesisScore(node_id="d1", label="Disease", name="PCP", score=0.2),
            HypothesisScore(node_id="d2", label="Disease", name="结核病", score=0.8),
        ],
    )
    optimistic_positive = next(
        branch.probability
        for branch in optimistic_branches
        if branch.branch_name == "positive"
    )
    skeptical_positive = next(
        branch.probability
        for branch in skeptical_branches
        if branch.branch_name == "positive"
    )

    assert round(sum(branch.probability for branch in optimistic_branches), 6) == 1.0
    assert optimistic_positive > skeptical_positive
