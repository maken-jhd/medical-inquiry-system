"""测试局部 simulation 对候选动作的基础估值逻辑。"""

from brain.action_builder import ActionBuilder
from brain.hypothesis_manager import HypothesisManager
from brain.router import ReasoningRouter
from brain.simulation_engine import SimulationConfig, SimulationEngine
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
    assert "positive" in seeds
    assert "negative" in seeds
