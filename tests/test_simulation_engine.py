"""测试局部 simulation 对候选动作的基础估值逻辑。"""

from brain.action_builder import ActionBuilder
from brain.hypothesis_manager import HypothesisManager
from brain.response_transition_model import (
    ResponseTransitionModelConfig,
    StatisticalResponseTransitionModel,
    TransitionBranch,
)
from brain.reward_model import BeliefAwareRolloutRewardModel, HeuristicRolloutRewardModel, RolloutRewardModelConfig
from brain.router import ReasoningRouter
from brain.simulation_engine import SimulationConfig, SimulationEngine
from brain.transition_statistics import TransitionStatistics
from brain.types import HypothesisCandidate, HypothesisScore, MctsAction, PatientContext, PendingActionResult, SessionState, TreeNode


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


class StubRetriever:
    """返回固定 R2 结果，供 rollout 测试使用。"""

    def retrieve_r2_expected_evidence(self, hypothesis: HypothesisScore, session_state: SessionState, top_k: int | None = None) -> list[dict]:
        _ = hypothesis
        _ = top_k
        if "n2" in session_state.asked_node_ids:
            return []

        return [
            {
                "node_id": "n2",
                "label": "ClinicalFinding",
                "name": "干咳",
                "relation_type": "MANIFESTS_AS",
                "relation_weight": 0.7,
                "node_weight": 0.8,
                "similarity_confidence": 1.0,
                "contradiction_priority": 0.4,
                "question_type_hint": "symptom",
                "priority": 1.2,
                "topic_id": "Disease",
            }
        ]


# 验证 rollout_from_tree_node 会执行多步 A3 -> A4 -> ROUTE 路径，而不只是两步动作日志。
def test_simulation_engine_rollout_from_tree_node_produces_multi_step_path() -> None:
    engine = SimulationEngine()
    router = ReasoningRouter()
    hypothesis_manager = HypothesisManager()
    action_builder = ActionBuilder()
    retriever = StubRetriever()
    state = SessionState(
        session_id="s2",
        candidate_hypotheses=[
            HypothesisScore(node_id="d1", label="Disease", name="肺孢子菌肺炎", score=3.0),
            HypothesisScore(node_id="d2", label="Disease", name="肺结核", score=2.6),
        ],
    )
    action = MctsAction(
        action_id="a1",
        action_type="verify_evidence",
        target_node_id="n1",
        target_node_label="LabFinding",
        target_node_name="低氧血症",
        hypothesis_id="d1",
        prior_score=2.0,
        metadata={"relation_type": "HAS_LAB_FINDING", "is_red_flag": True, "question_type_hint": "lab"},
    )
    node = TreeNode(
        node_id="root::a1",
        state_signature="sig-a1",
        parent_id="root",
        action_from_parent=action.action_id,
        stage="A3",
        depth=1,
        metadata={"action": action, "hypothesis_id": "d1"},
    )

    trajectory = engine.rollout_from_tree_node(
        node,
        state,
        PatientContext(raw_text="最近发热干咳，活动后气促。"),
        router=router,
        hypothesis_manager=hypothesis_manager,
        retriever=retriever,  # type: ignore[arg-type]
        action_builder=action_builder,
        max_depth=3,
        current_hypothesis=state.candidate_hypotheses[0],
        competing_hypotheses=[state.candidate_hypotheses[1]],
    )

    assert trajectory.metadata["rollout_depth"] >= 2
    assert [step["stage"] for step in trajectory.steps].count("A3") >= 2
    assert any(step["stage"] == "ROUTE" for step in trajectory.steps)


# 验证 rollout 内的模拟证据反馈也会联动多个相关 hypothesis，避免只围绕当前分支自嗨。
def test_simulation_engine_rollout_feedback_updates_multiple_related_hypotheses() -> None:
    engine = SimulationEngine()
    hypothesis_manager = HypothesisManager()
    state = SessionState(
        session_id="s_rollout_multi",
        candidate_hypotheses=[
            HypothesisScore(
                node_id="generic_pneumonia",
                label="Disease",
                name="原发性肺部感染",
                score=1.0,
                metadata={
                    "relation_types": ["HAS_LAB_FINDING"],
                    "evidence_node_ids": ["lab_bdg"],
                    "anchor_tier": "background_supported",
                    "observed_anchor_score": 0.0,
                },
            ),
            HypothesisScore(
                node_id="pcp",
                label="Disease",
                name="肺孢子菌肺炎",
                score=0.9,
                metadata={
                    "relation_types": ["HAS_LAB_FINDING"],
                    "evidence_node_ids": ["lab_bdg"],
                    "anchor_tier": "strong_anchor",
                    "observed_anchor_score": 0.7,
                    "exact_scope_anchor_score": 0.66,
                },
            ),
            HypothesisScore(
                node_id="obesity",
                label="Disease",
                name="肥胖",
                score=0.8,
                metadata={
                    "relation_types": ["REQUIRES_DETAIL"],
                    "evidence_node_ids": ["bmi_high"],
                },
            ),
        ],
    )
    action = MctsAction(
        action_id="verify::generic::lab_bdg",
        action_type="verify_evidence",
        target_node_id="lab_bdg",
        target_node_label="LabFinding",
        target_node_name="β-D 葡聚糖升高",
        hypothesis_id="generic_pneumonia",
        metadata={"relation_type": "HAS_LAB_FINDING"},
    )
    pending_action_result = PendingActionResult(
        action_type="verify_evidence",
        target_node_id="lab_bdg",
        target_node_name="β-D 葡聚糖升高",
        polarity="present",
        resolution="clear",
        reasoning="模拟回答明确支持。",
        supporting_span="β-D 葡聚糖升高",
    )

    engine._apply_rollout_state_update(
        state,
        action,
        pending_action_result,
        1,
        hypothesis_manager,
    )
    by_id = {item.node_id: item.score for item in state.candidate_hypotheses}

    assert by_id["generic_pneumonia"] > 1.0
    assert by_id["pcp"] > 0.9
    assert by_id["obesity"] == 0.8


# 验证第三批 rollout 会为同一个 child 同时保留正向与一个非正向 seed，减少单分支塌缩。
def test_simulation_engine_multi_branch_rollout_keeps_positive_and_negative_seeds() -> None:
    engine = SimulationEngine(
        SimulationConfig(
            enable_multi_branch_rollout=True,
            branch_budget_per_action=2,
        )
    )
    router = ReasoningRouter()
    hypothesis_manager = HypothesisManager()
    action_builder = ActionBuilder()
    retriever = StubRetriever()
    state = SessionState(
        session_id="s_branch_seed",
        candidate_hypotheses=[
            HypothesisScore(
                node_id="generic_infection",
                label="Disease",
                name="感染",
                score=1.6,
                metadata={
                    "anchor_tier": "speculative",
                    "observed_anchor_score": 0.0,
                },
            ),
            HypothesisScore(node_id="d2", label="Disease", name="肺结核", score=1.2),
        ],
    )
    action = MctsAction(
        action_id="a_seed",
        action_type="verify_evidence",
        target_node_id="n_seed",
        target_node_label="LabFinding",
        target_node_name="痰分枝杆菌培养",
        hypothesis_id="generic_infection",
        prior_score=2.4,
        metadata={"relation_type": "DIAGNOSED_BY", "question_type_hint": "lab"},
    )
    node = TreeNode(
        node_id="root::a_seed",
        state_signature="sig-seed",
        parent_id="root",
        action_from_parent=action.action_id,
        stage="A3",
        depth=1,
        metadata={"action": action, "hypothesis_id": "generic_infection"},
    )

    trajectories = engine.rollout_trajectories_from_tree_node(
        node,
        state,
        PatientContext(raw_text="反复发热咳嗽。"),
        router=router,
        hypothesis_manager=hypothesis_manager,
        retriever=retriever,  # type: ignore[arg-type]
        action_builder=action_builder,
        max_depth=2,
        current_hypothesis=state.candidate_hypotheses[0],
        competing_hypotheses=[state.candidate_hypotheses[1]],
    )

    seeds = {trajectory.metadata["branch_seed"] for trajectory in trajectories}

    assert len(trajectories) == 2


def _build_competitive_statistics() -> TransitionStatistics:
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
    for _ in range(2):
        statistics.record_verify_observation(
            disease_id="d2",
            evidence_family="respiratory_symptom",
            question_type="symptom",
            outcome="present",
        )
    for _ in range(6):
        statistics.record_verify_observation(
            disease_id="d2",
            evidence_family="respiratory_symptom",
            question_type="symptom",
            outcome="absent",
        )
    statistics.record_verify_observation(
        disease_id="d2",
        evidence_family="respiratory_symptom",
        question_type="symptom",
        outcome="unclear",
    )
    return statistics


# 验证 modular_v2 + statistical transition + belief-aware reward 会真正产出新的分支区分度 breakdown。
def test_simulation_engine_supports_belief_aware_reward_with_statistical_transition() -> None:
    statistics = _build_competitive_statistics()
    transition_model = StatisticalResponseTransitionModel(
        ResponseTransitionModelConfig(model_type="statistical"),
        statistics=statistics,
    )
    belief_engine = SimulationEngine(
        SimulationConfig(
            search_impl="modular_v2",
            transition_model_type="statistical",
            reward_model_type="belief_aware_v1",
        ),
        transition_model=transition_model,
        reward_model=BeliefAwareRolloutRewardModel(
            RolloutRewardModelConfig(model_type="belief_aware_v1")
        ),
    )
    heuristic_engine = SimulationEngine(
        SimulationConfig(
            search_impl="modular_v2",
            transition_model_type="statistical",
            reward_model_type="heuristic_v2",
        ),
        transition_model=transition_model,
        reward_model=HeuristicRolloutRewardModel(
            RolloutRewardModelConfig(model_type="heuristic_v2")
        ),
    )
    state = SessionState(
        session_id="s_stat_reward",
        candidate_hypotheses=[
            HypothesisScore(node_id="d1", label="Disease", name="PCP", score=0.58),
            HypothesisScore(node_id="d2", label="Disease", name="结核病", score=0.42),
        ],
    )
    action = MctsAction(
        action_id="verify::cough",
        action_type="verify_evidence",
        target_node_id="slot_cough",
        target_node_label="ClinicalFinding",
        target_node_name="干咳",
        prior_score=1.8,
        metadata={
            "relation_type": "MANIFESTS_AS",
            "question_type_hint": "symptom",
            "evidence_families": ["respiratory_symptom"],
            "evidence_cost": "low",
        },
    )

    belief_outcome = belief_engine.simulate_action(
        action,
        state,
        state.candidate_hypotheses[0],
        candidate_hypotheses=state.candidate_hypotheses,
    )
    heuristic_outcome = heuristic_engine.simulate_action(
        action,
        state,
        state.candidate_hypotheses[0],
        candidate_hypotheses=state.candidate_hypotheses,
    )
    positive_branch = next(
        item
        for item in belief_outcome.metadata["branch_estimates"]
        if item["branch"] == "positive"
    )

    assert belief_outcome.metadata["reward_model_type"] == "belief_aware_v1"
    assert "top1_top2_margin_gain_surrogate" in positive_branch["reward_breakdown"]
    assert "top1_top3_separation_gain_surrogate" in positive_branch["reward_breakdown"]
    assert "competitor_elimination_surrogate" in positive_branch["reward_breakdown"]
    assert "acceptance_risk_proxy" in positive_branch["reward_breakdown"]
    assert "alternative_preservation_quality" in positive_branch["reward_breakdown"]
    assert "selection_score" in positive_branch
    assert belief_outcome.metadata["discriminative_action_bonus"] >= 0.0
    assert belief_outcome.metadata["alternative_preservation_action_bonus"] >= 0.0
    assert belief_outcome.metadata["competitor_coverage_bonus"] >= 0.0
    assert belief_outcome.positive_branch_reward != heuristic_outcome.positive_branch_reward


# 验证 modular_v2 在 statistical transition model 下会真正消费统计版分支概率。
def test_simulation_engine_modular_v2_uses_statistical_transition_model() -> None:
    statistics = TransitionStatistics(smoothing_alpha=0.05, min_total_count=1)
    for _ in range(6):
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
    engine = SimulationEngine(
        SimulationConfig(
            search_impl="modular_v2",
            transition_model_type="statistical",
        ),
        transition_model=StatisticalResponseTransitionModel(
            ResponseTransitionModelConfig(model_type="statistical"),
            statistics=statistics,
        ),
    )
    state = SessionState(
        session_id="s_stat_rollout",
        candidate_hypotheses=[
            HypothesisScore(node_id="d1", label="Disease", name="PCP", score=1.0),
        ],
    )
    action = MctsAction(
        action_id="a_stat_rollout",
        action_type="verify_evidence",
        target_node_id="slot_cough",
        target_node_label="ClinicalFinding",
        target_node_name="干咳",
        metadata={
            "question_type_hint": "symptom",
            "evidence_families": ["respiratory_symptom"],
        },
    )

    outcome = engine.simulate_action(
        action,
        state,
        primary_hypothesis=state.candidate_hypotheses[0],
        candidate_hypotheses=state.candidate_hypotheses,
    )
    by_branch = {
        item["branch"]: item
        for item in outcome.metadata["branch_estimates"]
    }

    assert outcome.metadata["transition_model_type"] == "statistical"
    assert by_branch["positive"]["transition_metadata"]["source"] == "statistical"
    assert by_branch["positive"]["probability"] > by_branch["negative"]["probability"]


# 当动作没有显式 evidence_family 时，statistical transition 也应能从 evidence_tags 回退到可区分 family。
def test_simulation_engine_statistical_transition_uses_evidence_tags_family_fallback() -> None:
    statistics = TransitionStatistics(smoothing_alpha=0.05, min_total_count=1)
    for _ in range(7):
        statistics.record_verify_observation(
            disease_id="d1",
            evidence_family="respiratory_symptom",
            question_type="symptom",
            outcome="present",
        )
    for _ in range(2):
        statistics.record_verify_observation(
            disease_id="d1",
            evidence_family="respiratory_symptom",
            question_type="symptom",
            outcome="absent",
        )

    engine = SimulationEngine(
        SimulationConfig(
            search_impl="modular_v2",
            transition_model_type="statistical",
        ),
        transition_model=StatisticalResponseTransitionModel(
            ResponseTransitionModelConfig(model_type="statistical"),
            statistics=statistics,
        ),
    )
    state = SessionState(
        session_id="s_stat_tags",
        candidate_hypotheses=[HypothesisScore(node_id="d1", label="Disease", name="PCP", score=1.0)],
    )
    action = MctsAction(
        action_id="a_stat_tags",
        action_type="verify_evidence",
        target_node_id="slot_cough",
        target_node_label="ClinicalFinding",
        target_node_name="干咳",
        metadata={
            "question_type_hint": "symptom",
            "evidence_tags": ["respiratory", "type:symptom"],
        },
    )

    outcome = engine.simulate_action(
        action,
        state,
        primary_hypothesis=state.candidate_hypotheses[0],
        candidate_hypotheses=state.candidate_hypotheses,
    )
    positive_branch = next(item for item in outcome.metadata["branch_estimates"] if item["branch"] == "positive")

    assert positive_branch["transition_metadata"]["source"] == "statistical"
    assert positive_branch["probability"] > 0.5


# rollout 选分支时应允许区分性 bonus 轻量改写 weighted_reward 的排序，而不是永远只看概率乘 reward。
def test_simulation_engine_prefers_more_discriminative_branch_when_selection_score_higher() -> None:
    engine = SimulationEngine(
        SimulationConfig(
            search_impl="modular_v2",
            branch_selection_mode="greedy",
        )
    )
    branch_payloads = [
        {
            "branch": "positive",
            "probability": 0.6,
            "reward": 0.3,
            "weighted_reward": 0.18,
            "selection_score": 0.19,
        },
        {
            "branch": "negative",
            "probability": 0.42,
            "reward": 0.4,
            "weighted_reward": 0.168,
            "selection_score": 0.24,
        },
    ]

    selected = engine._select_best_branch_payload(branch_payloads, "a_branch")

    assert selected["branch"] == "negative"


# alternative preservation bonus 应真正参与分支选择，而不是只停留在 reward breakdown 里。
def test_simulation_engine_alternative_preservation_bonus_prefers_healthier_top3_branch() -> None:
    engine = SimulationEngine(
        SimulationConfig(
            search_impl="modular_v2",
            branch_selection_mode="greedy",
        )
    )
    action = MctsAction(
        action_id="verify::preservation",
        action_type="verify_evidence",
        target_node_id="slot_cough",
        target_node_label="ClinicalFinding",
        target_node_name="干咳",
        metadata={"question_type_hint": "symptom"},
    )
    branch = TransitionBranch(
        branch_name="positive",
        probability=0.58,
        polarity="present",
        resolution="clear",
        metadata={},
    )

    healthy_bonus = engine._estimate_branch_alternative_preservation_bonus(
        action=action,
        branch=branch,
        reward_breakdown={
            "alternative_preservation_quality": 0.68,
            "alternative_preservation_overcompression_penalty": 0.04,
        },
    )
    collapsed_bonus = engine._estimate_branch_alternative_preservation_bonus(
        action=action,
        branch=branch,
        reward_breakdown={
            "alternative_preservation_quality": 0.14,
            "alternative_preservation_overcompression_penalty": 0.42,
        },
    )

    assert healthy_bonus > collapsed_bonus


# stage-aware coverage bonus 也应进入 rollout 分支选择，帮助前期更偏向宽覆盖问题。
def test_simulation_engine_stage_aware_coverage_bonus_prefers_broad_branch() -> None:
    engine = SimulationEngine(
        SimulationConfig(
            search_impl="modular_v2",
            branch_selection_mode="greedy",
        )
    )
    branch = TransitionBranch(
        branch_name="positive",
        probability=0.56,
        polarity="present",
        resolution="clear",
        metadata={},
    )
    broad_action = MctsAction(
        action_id="verify::broad",
        action_type="verify_evidence",
        target_node_id="slot_cough",
        target_node_label="ClinicalFinding",
        target_node_name="咳嗽",
        metadata={"question_type_hint": "symptom"},
    )
    narrow_action = MctsAction(
        action_id="verify::narrow",
        action_type="verify_evidence",
        target_node_id="path_cmv",
        target_node_label="Pathogen",
        target_node_name="CMV DNA",
        metadata={"question_type_hint": "pathogen"},
    )

    broad_bonus = engine._estimate_branch_stage_aware_coverage_bonus(
        action=broad_action,
        branch=branch,
        reward_breakdown={
            "stage_aware_coverage_pressure": 0.82,
            "early_broad_coverage_bonus": 0.06,
            "early_narrow_evidence_penalty": 0.0,
            "early_over_collapse_penalty": 0.0,
        },
    )
    narrow_bonus = engine._estimate_branch_stage_aware_coverage_bonus(
        action=narrow_action,
        branch=branch,
        reward_breakdown={
            "stage_aware_coverage_pressure": 0.82,
            "early_broad_coverage_bonus": 0.0,
            "early_narrow_evidence_penalty": 0.08,
            "early_over_collapse_penalty": 0.07,
        },
    )

    assert broad_bonus > 0.0
    assert narrow_bonus < broad_bonus
