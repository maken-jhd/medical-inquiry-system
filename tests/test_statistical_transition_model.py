"""测试 statistical transition model 的分支输出与安全 fallback 行为。"""

from brain.response_transition_model import (
    HeuristicResponseTransitionModel,
    ResponseTransitionModelConfig,
    StatisticalResponseTransitionModel,
)
from brain.transition_statistics import TransitionStatistics
from brain.types import HypothesisScore, MctsAction, SessionState


def _build_statistics() -> TransitionStatistics:
    statistics = TransitionStatistics(smoothing_alpha=0.05, min_total_count=1)
    for _ in range(8):
        statistics.record_verify_observation(
            disease_id="d1",
            evidence_family="respiratory_symptom",
            question_type="symptom",
            outcome="present",
        )
    statistics.record_verify_observation(
        disease_id="d1",
        evidence_family="respiratory_symptom",
        question_type="symptom",
        outcome="absent",
    )
    statistics.record_verify_observation(
        disease_id="d1",
        evidence_family="respiratory_symptom",
        question_type="symptom",
        outcome="unclear",
    )
    for _ in range(3):
        statistics.record_exam_availability_observation(
            disease_id="d1",
            exam_kind="lab",
            outcome="done",
        )
    statistics.record_exam_availability_observation(
        disease_id="d1",
        exam_kind="lab",
        outcome="not_done",
    )
    for _ in range(2):
        statistics.record_exam_result_observation(
            disease_id="d1",
            test_type="lab",
            outcome="positive",
        )
    statistics.record_exam_result_observation(
        disease_id="d1",
        test_type="lab",
        outcome="negative",
    )
    return statistics


# 验证有统计数据时，普通问诊会直接使用统计版分支概率。
def test_statistical_transition_model_uses_statistics_for_verify_branches() -> None:
    model = StatisticalResponseTransitionModel(
        ResponseTransitionModelConfig(model_type="statistical"),
        statistics=_build_statistics(),
    )
    action = MctsAction(
        action_id="a_stat_verify",
        action_type="verify_evidence",
        target_node_id="slot_cough",
        target_node_label="ClinicalFinding",
        target_node_name="干咳",
        metadata={
            "question_type_hint": "symptom",
            "evidence_families": ["respiratory_symptom"],
        },
    )

    branches = model.predict_branches(
        action,
        SessionState(session_id="s_stat_verify"),
        candidate_hypotheses=[HypothesisScore(node_id="d1", label="Disease", name="PCP", score=1.0)],
    )
    positive = next(branch for branch in branches if branch.branch_name == "positive")
    negative = next(branch for branch in branches if branch.branch_name == "negative")

    assert positive.metadata["source"] == "statistical"
    assert positive.probability > negative.probability


# 验证 exam_context 动作会走 done/not_done -> result 的两层映射，而不是退回普通三分支。
def test_statistical_transition_model_supports_exam_context_branches() -> None:
    model = StatisticalResponseTransitionModel(
        ResponseTransitionModelConfig(model_type="statistical"),
        statistics=_build_statistics(),
    )
    action = MctsAction(
        action_id="a_stat_exam",
        action_type="collect_exam_context",
        target_node_id="__exam_context__::lab",
        target_node_label="ExamContext",
        target_node_name="化验检查情况",
        metadata={
            "exam_kind": "lab",
            "question_type_hint": "exam_context",
            "candidate_exam_kinds": ["lab"],
        },
    )

    branches = model.predict_branches(
        action,
        SessionState(session_id="s_stat_exam"),
        candidate_hypotheses=[HypothesisScore(node_id="d1", label="Disease", name="PCP", score=1.0)],
    )
    branch_names = {branch.branch_name for branch in branches}

    assert branch_names == {"done_positive", "done_negative", "done_unclear", "not_done"}
    assert round(sum(branch.probability for branch in branches), 6) == 1.0
    assert any(branch.metadata["branch_schema"] == "exam_context" for branch in branches)


# 验证统计数据缺失时会安全回退到 heuristic，而不是直接失败。
def test_statistical_transition_model_falls_back_to_heuristic_when_statistics_are_missing() -> None:
    model = StatisticalResponseTransitionModel(
        ResponseTransitionModelConfig(
            model_type="statistical",
            fallback_to_heuristic=True,
        ),
        statistics=TransitionStatistics(),
        heuristic_fallback=HeuristicResponseTransitionModel(),
    )
    action = MctsAction(
        action_id="a_stat_fallback",
        action_type="verify_evidence",
        target_node_id="slot_red_flag",
        target_node_label="LabFinding",
        target_node_name="低氧血症",
        metadata={
            "relation_type": "HAS_LAB_FINDING",
            "is_red_flag": True,
            "question_type_hint": "lab",
        },
    )

    branches = model.predict_branches(action, SessionState(session_id="s_stat_fallback"))

    assert any(branch.metadata["source"] == "heuristic_fallback" for branch in branches)
