"""测试 service 层能够在 legacy 与 modular_v2 skeleton 间切换。"""

from brain.action_builder import ActionBuilder
from brain.hypothesis_manager import HypothesisManager
from brain.mcts_engine import MctsConfig, MctsEngine
from brain.response_transition_model import HeuristicResponseTransitionModel
from brain.reward_model import HeuristicRolloutRewardModel
from brain.router import ReasoningRouter
from brain.service import BrainDependencies, ConsultationBrain, RepairPolicyConfig, SearchPolicyConfig
from brain.simulation_engine import SimulationConfig, SimulationEngine
from brain.state_signature import BeliefStateSignatureBuilder
from brain.state_tracker import StateTracker
from brain.trajectory_evaluator import TrajectoryEvaluator
from brain.types import HypothesisScore, PatientContext


class StaticRetriever:
    """返回固定 R2 候选，避免测试依赖真实图数据库。"""

    client = object()

    def __init__(self, rows_by_hypothesis: dict[str, list[dict]]) -> None:
        self.rows_by_hypothesis = rows_by_hypothesis

    def retrieve_r2_expected_evidence(self, hypothesis: HypothesisScore, session_state, top_k=None) -> list[dict]:
        _ = session_state, top_k
        return list(self.rows_by_hypothesis.get(hypothesis.node_id, []))


def _rows() -> list[dict]:
    return [
        {
            "node_id": "symptom_cough",
            "label": "ClinicalFinding",
            "name": "咳嗽",
            "relation_type": "MANIFESTS_AS",
            "question_type_hint": "symptom",
            "acquisition_mode": "direct_ask",
            "evidence_cost": "low",
            "priority": 2.0,
            "contradiction_priority": 0.4,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
        }
    ]


def _build_brain(search_impl: str) -> tuple[ConsultationBrain, StateTracker]:
    tracker = StateTracker()
    brain = ConsultationBrain(
        BrainDependencies(
            state_tracker=tracker,
            retriever=StaticRetriever({"pcp": _rows()}),
            med_extractor=object(),
            entity_linker=object(),
            question_selector=object(),
            acceptance_controller=object(),
            report_builder=object(),
            evidence_parser=object(),
            hypothesis_manager=HypothesisManager(),
            action_builder=ActionBuilder(),
            router=ReasoningRouter(),
            mcts_engine=MctsEngine(
                MctsConfig(
                    search_impl=search_impl,
                    num_rollouts=1,
                    max_depth=2,
                    max_child_nodes=2,
                ),
                state_signature_builder=BeliefStateSignatureBuilder(),
            ),
            simulation_engine=SimulationEngine(
                SimulationConfig(
                    search_impl=search_impl,
                    enable_multi_branch_rollout=False,
                    branch_budget_per_action=1,
                ),
                transition_model=HeuristicResponseTransitionModel(),
                reward_model=HeuristicRolloutRewardModel(),
            ),
            trajectory_evaluator=TrajectoryEvaluator(),
            evidence_anchor_analyzer=object(),
            llm_client=object(),
            repair_policy=RepairPolicyConfig(),
            search_policy=SearchPolicyConfig(root_action_mode="mcts"),
        )
    )
    return brain, tracker


# 验证 legacy 路径仍可运行，并继续保留 path-id 风格 child signature。
def test_service_run_reasoning_search_keeps_legacy_path_available() -> None:
    brain, tracker = _build_brain("legacy")
    state = tracker.create_session("s_legacy")
    state.candidate_hypotheses = [
        HypothesisScore(node_id="pcp", label="Disease", name="PCP", score=0.85),
        HypothesisScore(node_id="tb", label="Disease", name="结核病", score=0.6),
    ]

    result = brain.run_reasoning_search("s_legacy", PatientContext(raw_text="发热咳嗽。"))
    tree = tracker.get_bound_search_tree("s_legacy")

    assert result.metadata["search_impl"] == "legacy"
    assert tree is not None
    assert len(tree.get_node(tree.root_id).children_ids) > 0
    child = tree.get_node(tree.get_node(tree.root_id).children_ids[0])
    assert child.state_signature == child.node_id


# 验证 modular_v2 skeleton 可启用，并使用 belief-state child signature。
def test_service_run_reasoning_search_enables_modular_v2_skeleton() -> None:
    brain, tracker = _build_brain("modular_v2")
    state = tracker.create_session("s_modular")
    state.candidate_hypotheses = [
        HypothesisScore(node_id="pcp", label="Disease", name="PCP", score=0.85),
        HypothesisScore(node_id="tb", label="Disease", name="结核病", score=0.6),
    ]

    result = brain.run_reasoning_search("s_modular", PatientContext(raw_text="发热咳嗽。"))
    tree = tracker.get_bound_search_tree("s_modular")

    assert result.metadata["search_impl"] == "modular_v2"
    assert tree is not None
    assert len(tree.get_node(tree.root_id).children_ids) > 0
    child = tree.get_node(tree.get_node(tree.root_id).children_ids[0])
    assert child.state_signature != child.node_id
    assert child.metadata["state_signature_source"] == "belief_state_post_action"
