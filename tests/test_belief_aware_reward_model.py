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


def _candidate_hypotheses_top5() -> list[HypothesisScore]:
    return [
        *_candidate_hypotheses(),
        HypothesisScore(
            node_id="cmv",
            label="Disease",
            name="巨细胞病毒肺炎",
            score=0.29,
            metadata={
                "evidence_node_ids": ["lab_ldh", "path_cmv"],
                "relation_types": ["HAS_LAB_FINDING", "HAS_PATHOGEN"],
            },
        ),
        HypothesisScore(
            node_id="crypto",
            label="Disease",
            name="隐球菌感染",
            score=0.21,
            metadata={
                "evidence_node_ids": ["path_crypto"],
                "relation_types": ["HAS_PATHOGEN"],
            },
        ),
        HypothesisScore(
            node_id="bacterial",
            label="Disease",
            name="细菌性肺炎",
            score=0.15,
            metadata={
                "evidence_node_ids": ["slot_fever"],
                "relation_types": ["MANIFESTS_AS"],
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


def _top3_preserved_components() -> list[dict]:
    return [
        {
            "disease_id": "pcp",
            "weight": 0.43,
            "raw_score": 0.43,
            "backoff_level": "disease_family_question_type",
            "total_count": 12.0,
            "present_probability": 0.84,
            "absent_probability": 0.07,
            "unclear_probability": 0.09,
        },
        {
            "disease_id": "tb",
            "weight": 0.29,
            "raw_score": 0.29,
            "backoff_level": "disease_family_question_type",
            "total_count": 10.0,
            "present_probability": 0.43,
            "absent_probability": 0.37,
            "unclear_probability": 0.20,
        },
        {
            "disease_id": "cmv",
            "weight": 0.18,
            "raw_score": 0.18,
            "backoff_level": "disease_family_question_type",
            "total_count": 8.0,
            "present_probability": 0.36,
            "absent_probability": 0.38,
            "unclear_probability": 0.26,
        },
    ]


def _top3_collapsed_components() -> list[dict]:
    return [
        {
            "disease_id": "pcp",
            "weight": 0.43,
            "raw_score": 0.43,
            "backoff_level": "disease_family_question_type",
            "total_count": 12.0,
            "present_probability": 0.9,
            "absent_probability": 0.04,
            "unclear_probability": 0.06,
        },
        {
            "disease_id": "tb",
            "weight": 0.29,
            "raw_score": 0.29,
            "backoff_level": "disease_family_question_type",
            "total_count": 10.0,
            "present_probability": 0.08,
            "absent_probability": 0.7,
            "unclear_probability": 0.22,
        },
        {
            "disease_id": "cmv",
            "weight": 0.18,
            "raw_score": 0.18,
            "backoff_level": "disease_family_question_type",
            "total_count": 8.0,
            "present_probability": 0.05,
            "absent_probability": 0.68,
            "unclear_probability": 0.27,
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
    assert positive.metadata["top1_top3_separation_gain_surrogate"] > doubtful.metadata["top1_top3_separation_gain_surrogate"]
    assert positive.metadata["uncertainty_reduction_surrogate"] > doubtful.metadata["uncertainty_reduction_surrogate"]
    assert "competitor_elimination_surrogate" in positive.metadata
    assert "discriminative_support_quality" in positive.metadata


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


# 非关键 detail 问题即使有同样的基础分支，也不应因为 rollout bonus 被抬得比高区分问题还高。
def test_belief_aware_reward_penalizes_non_discriminative_detail_action() -> None:
    model = BeliefAwareRolloutRewardModel(RolloutRewardModelConfig(model_type="belief_aware_v1"))
    state = SessionState(session_id="s_reward_detail")
    candidates = _candidate_hypotheses()
    symptom_action = MctsAction(
        action_id="verify::oxygenation",
        action_type="verify_evidence",
        target_node_id="lab_oxygen",
        target_node_label="LabFinding",
        target_node_name="低氧血症",
        prior_score=1.7,
        metadata={
            "relation_type": "HAS_LAB_FINDING",
            "question_type_hint": "lab",
            "evidence_cost": "high",
            "discriminative_gain": 1.1,
        },
    )
    detail_action = MctsAction(
        action_id="detail::cough::duration",
        action_type="verify_evidence",
        target_node_id="detail_cough_duration",
        target_node_label="ClinicalAttribute",
        target_node_name="咳嗽持续时间",
        prior_score=1.7,
        metadata={
            "relation_type": "REQUIRES_DETAIL",
            "question_type_hint": "detail",
            "evidence_cost": "low",
            "discriminative_gain": 0.05,
        },
    )

    weak_detail_components = [
        {
            "disease_id": "pcp",
            "weight": 0.62,
            "raw_score": 0.62,
            "backoff_level": "global",
            "total_count": 1.0,
            "present_probability": 0.51,
            "absent_probability": 0.25,
            "unclear_probability": 0.24,
        },
        {
            "disease_id": "tb",
            "weight": 0.38,
            "raw_score": 0.38,
            "backoff_level": "global",
            "total_count": 1.0,
            "present_probability": 0.47,
            "absent_probability": 0.28,
            "unclear_probability": 0.25,
        },
    ]
    branch = TransitionBranch(
        branch_name="positive",
        probability=0.58,
        polarity="present",
        resolution="clear",
        metadata={
            "source": "statistical",
            "branch_schema": "verify",
            "belief_components": weak_detail_components,
        },
    )

    symptom_result = model.evaluate_branch(
        session_state=state,
        action=symptom_action,
        branch=branch,
        candidate_hypotheses=candidates,
    )
    detail_result = model.evaluate_branch(
        session_state=state,
        action=detail_action,
        branch=branch,
        candidate_hypotheses=candidates,
    )

    assert symptom_result.reward > detail_result.reward
    assert detail_result.metadata["discriminative_support_quality"] < symptom_result.metadata["discriminative_support_quality"]
    assert symptom_result.metadata["competitor_elimination_surrogate"] >= 0.0


# belief-aware reward 应支持用 top-5 prior belief 保留更多边缘候选，而不是默认截到 top-3。
def test_belief_aware_reward_builds_top5_prior_belief() -> None:
    model = BeliefAwareRolloutRewardModel(
        RolloutRewardModelConfig(model_type="belief_aware_v1", belief_top_k_hypotheses=5)
    )
    action = MctsAction(
        action_id="verify::top5",
        action_type="verify_evidence",
        target_node_id="slot_cough",
        target_node_label="ClinicalFinding",
        target_node_name="干咳",
        prior_score=1.6,
        metadata={"relation_type": "MANIFESTS_AS", "question_type_hint": "symptom"},
    )
    prior_belief = model._build_prior_belief(
        branch=TransitionBranch(
            branch_name="positive",
            probability=0.6,
            polarity="present",
            resolution="clear",
            metadata={},
        ),
        candidate_hypotheses=_candidate_hypotheses_top5(),
        primary_hypothesis=None,
        action=action,
    )

    assert len(prior_belief) == 5
    assert abs(sum(item.weight for item in prior_belief) - 1.0) < 1e-6
    assert prior_belief[-1].disease_id == "bacterial"


# posterior_update_alpha 越高，belief 更新应越尖锐；越低则越保守。
def test_belief_aware_reward_posterior_update_alpha_controls_sharpness() -> None:
    action = MctsAction(
        action_id="verify::alpha",
        action_type="verify_evidence",
        target_node_id="slot_cough",
        target_node_label="ClinicalFinding",
        target_node_name="干咳",
        prior_score=1.7,
        metadata={"relation_type": "MANIFESTS_AS", "question_type_hint": "symptom"},
    )
    branch = TransitionBranch(
        branch_name="positive",
        probability=0.62,
        polarity="present",
        resolution="clear",
        metadata={
            "source": "statistical",
            "branch_schema": "verify",
            "belief_components": _top3_collapsed_components(),
        },
    )
    candidates = _candidate_hypotheses_top5()[:3]
    conservative = BeliefAwareRolloutRewardModel(
        RolloutRewardModelConfig(
            model_type="belief_aware_v1",
            belief_top_k_hypotheses=5,
            posterior_update_alpha=0.35,
        )
    )
    aggressive = BeliefAwareRolloutRewardModel(
        RolloutRewardModelConfig(
            model_type="belief_aware_v1",
            belief_top_k_hypotheses=5,
            posterior_update_alpha=0.9,
        )
    )

    conservative_shift = conservative.estimate_branch_confidence_shift(
        session_state=SessionState(session_id="s_alpha_lo"),
        action=action,
        branch=branch,
        candidate_hypotheses=candidates,
    )
    aggressive_shift = aggressive.estimate_branch_confidence_shift(
        session_state=SessionState(session_id="s_alpha_hi"),
        action=action,
        branch=branch,
        candidate_hypotheses=candidates,
    )

    assert aggressive_shift["posterior_margin"] > conservative_shift["posterior_margin"]
    assert aggressive_shift["posterior_top3_weight"] < conservative_shift["posterior_top3_weight"]


# alternative preservation bonus 应偏好“拉开第一名但保留健康 Top-3”的分支，而不是只奖励把竞争者全部压没。
def test_belief_aware_reward_rewards_alternative_preservation() -> None:
    model = BeliefAwareRolloutRewardModel(
        RolloutRewardModelConfig(
            model_type="belief_aware_v1",
            belief_top_k_hypotheses=5,
            enable_alternative_preservation_bonus=True,
            alternative_preservation_weight=0.16,
        )
    )
    state = SessionState(session_id="s_preservation")
    action = MctsAction(
        action_id="verify::preservation",
        action_type="verify_evidence",
        target_node_id="slot_cough",
        target_node_label="ClinicalFinding",
        target_node_name="干咳",
        prior_score=1.8,
        metadata={
            "relation_type": "MANIFESTS_AS",
            "question_type_hint": "symptom",
            "discriminative_gain": 0.9,
        },
    )
    candidates = _candidate_hypotheses_top5()[:3]
    preserved = model.evaluate_branch(
        session_state=state,
        action=action,
        branch=TransitionBranch(
            branch_name="positive",
            probability=0.6,
            polarity="present",
            resolution="clear",
            metadata={
                "source": "statistical",
                "branch_schema": "verify",
                "belief_components": _top3_preserved_components(),
            },
        ),
        candidate_hypotheses=candidates,
    )
    collapsed = model.evaluate_branch(
        session_state=state,
        action=action,
        branch=TransitionBranch(
            branch_name="positive",
            probability=0.6,
            polarity="present",
            resolution="clear",
            metadata={
                "source": "statistical",
                "branch_schema": "verify",
                "belief_components": _top3_collapsed_components(),
            },
        ),
        candidate_hypotheses=candidates,
    )

    assert preserved.metadata["alternative_preservation_quality"] > collapsed.metadata["alternative_preservation_quality"]
    assert preserved.metadata["alternative_preservation_bonus"] > collapsed.metadata["alternative_preservation_bonus"]
    assert collapsed.metadata["alternative_preservation_overcompression_penalty"] > 0.0


# 候选仍分散的前期，lab/pathogen/detail 这类窄证据不应因为局部高分被过度偏好。
def test_belief_aware_reward_applies_early_stage_coverage_control() -> None:
    model = BeliefAwareRolloutRewardModel(
        RolloutRewardModelConfig(
            model_type="belief_aware_v1",
            enable_stage_aware_coverage_control=True,
            early_narrow_evidence_penalty_weight=0.1,
            early_broad_coverage_bonus_weight=0.08,
            early_over_collapse_penalty_weight=0.1,
            stage_aware_competitor_elimination_scale=0.5,
        )
    )
    baseline_model = BeliefAwareRolloutRewardModel(
        RolloutRewardModelConfig(
            model_type="belief_aware_v1",
            enable_stage_aware_coverage_control=False,
        )
    )
    early_state = SessionState(session_id="s_stage_early", turn_index=0)
    candidates = _candidate_hypotheses_top5()[:3]

    broad_action = MctsAction(
        action_id="verify::cough::broad",
        action_type="verify_evidence",
        target_node_id="slot_cough",
        target_node_label="ClinicalFinding",
        target_node_name="咳嗽",
        prior_score=1.65,
        metadata={
            "relation_type": "MANIFESTS_AS",
            "question_type_hint": "symptom",
            "evidence_cost": "low",
            "discriminative_gain": 0.7,
        },
    )
    narrow_action = MctsAction(
        action_id="verify::cmv_dna::narrow",
        action_type="verify_evidence",
        target_node_id="path_cmv",
        target_node_label="Pathogen",
        target_node_name="CMV DNA",
        prior_score=1.7,
        metadata={
            "relation_type": "HAS_PATHOGEN",
            "question_type_hint": "pathogen",
            "evidence_cost": "high",
            "discriminative_gain": 0.92,
        },
    )

    broad_result = model.evaluate_branch(
        session_state=early_state,
        action=broad_action,
        branch=TransitionBranch(
            branch_name="positive",
            probability=0.58,
            polarity="present",
            resolution="clear",
            metadata={
                "source": "statistical",
                "branch_schema": "verify",
                "belief_components": _top3_preserved_components(),
            },
        ),
        candidate_hypotheses=candidates,
    )
    narrow_result = model.evaluate_branch(
        session_state=early_state,
        action=narrow_action,
        branch=TransitionBranch(
            branch_name="positive",
            probability=0.58,
            polarity="present",
            resolution="clear",
            metadata={
                "source": "statistical",
                "branch_schema": "verify",
                "belief_components": _top3_collapsed_components(),
            },
        ),
        candidate_hypotheses=candidates,
    )
    narrow_baseline = baseline_model.evaluate_branch(
        session_state=early_state,
        action=narrow_action,
        branch=TransitionBranch(
            branch_name="positive",
            probability=0.58,
            polarity="present",
            resolution="clear",
            metadata={
                "source": "statistical",
                "branch_schema": "verify",
                "belief_components": _top3_collapsed_components(),
            },
        ),
        candidate_hypotheses=candidates,
    )

    assert broad_result.metadata["stage_aware_phase"] == "coverage_first"
    assert broad_result.metadata["early_broad_coverage_bonus"] > 0.0
    assert narrow_result.metadata["early_narrow_evidence_penalty"] > 0.0
    assert narrow_result.metadata["early_over_collapse_penalty"] > 0.0
    assert narrow_result.metadata["stage_aware_competitor_elimination_scale"] < 1.0
    assert narrow_result.reward < narrow_baseline.reward


# 到后期后，区分性强的窄证据仍然可以保留优势，不会被 coverage control 永久压死。
def test_belief_aware_reward_restores_discriminative_preference_late_stage() -> None:
    model = BeliefAwareRolloutRewardModel(
        RolloutRewardModelConfig(
            model_type="belief_aware_v1",
            enable_stage_aware_coverage_control=True,
            early_narrow_evidence_penalty_weight=0.1,
            early_broad_coverage_bonus_weight=0.08,
            early_over_collapse_penalty_weight=0.1,
            stage_aware_competitor_elimination_scale=0.5,
        )
    )
    action = MctsAction(
        action_id="verify::cmv_dna::late",
        action_type="verify_evidence",
        target_node_id="path_cmv",
        target_node_label="Pathogen",
        target_node_name="CMV DNA",
        prior_score=1.7,
        metadata={
            "relation_type": "HAS_PATHOGEN",
            "question_type_hint": "pathogen",
            "evidence_cost": "high",
            "discriminative_gain": 0.92,
        },
    )
    candidates = _candidate_hypotheses_top5()[:3]
    branch = TransitionBranch(
        branch_name="positive",
        probability=0.58,
        polarity="present",
        resolution="clear",
        metadata={
            "source": "statistical",
            "branch_schema": "verify",
            "belief_components": _top3_collapsed_components(),
        },
    )

    early_result = model.evaluate_branch(
        session_state=SessionState(session_id="s_stage_early_2", turn_index=0),
        action=action,
        branch=branch,
        candidate_hypotheses=candidates,
    )
    late_result = model.evaluate_branch(
        session_state=SessionState(session_id="s_stage_late", turn_index=3),
        action=action,
        branch=branch,
        candidate_hypotheses=candidates,
    )

    assert late_result.reward > early_result.reward
    assert late_result.metadata["stage_aware_coverage_pressure"] < early_result.metadata["stage_aware_coverage_pressure"]
    assert late_result.metadata["stage_aware_competitor_elimination_scale"] > early_result.metadata["stage_aware_competitor_elimination_scale"]
