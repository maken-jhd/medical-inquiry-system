"""测试 service 层能够在 legacy 与 modular_v2 skeleton 间切换。"""

from brain.action_builder import ActionBuilder
from brain.evidence_parser import EvidenceParser
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
from brain.types import HypothesisScore, MentionContextItem, PatientContext


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


def _chalk_rows() -> list[dict]:
    return [
        {
            "node_id": "symptom_chalk",
            "label": "ClinicalFinding",
            "name": "lithoptysis（咳出白粉笔样物质）",
            "relation_type": "MANIFESTS_AS",
            "question_type_hint": "symptom",
            "acquisition_mode": "direct_ask",
            "evidence_cost": "low",
            "priority": 4.2,
            "contradiction_priority": 0.85,
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


# 验证患者开场已明确提到的内容会在搜索前直接当作 confirmed evidence，
# 系统不再围绕同一语义证据重复生成 verify 问题。
def test_service_trusts_patient_stated_content_before_search() -> None:
    tracker = StateTracker()
    brain = ConsultationBrain(
        BrainDependencies(
            state_tracker=tracker,
            retriever=StaticRetriever({"broncho": _chalk_rows()}),
            med_extractor=object(),
            entity_linker=object(),
            question_selector=object(),
            acceptance_controller=object(),
            report_builder=object(),
            evidence_parser=EvidenceParser(),
            hypothesis_manager=HypothesisManager(),
            action_builder=ActionBuilder(),
            router=ReasoningRouter(),
            mcts_engine=MctsEngine(
                MctsConfig(
                    search_impl="modular_v2",
                    num_rollouts=1,
                    max_depth=2,
                    max_child_nodes=2,
                ),
                state_signature_builder=BeliefStateSignatureBuilder(),
            ),
            simulation_engine=SimulationEngine(
                SimulationConfig(
                    search_impl="modular_v2",
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
    state = tracker.create_session("s_trust")
    state.candidate_hypotheses = [
        HypothesisScore(node_id="broncho", label="Disease", name="支气管结石病", score=0.82),
    ]
    state.mention_context["咳出白粉笔样物质"] = MentionContextItem(
        normalized_name="咳出白粉笔样物质",
        display_name="咳出白粉笔样物质",
        polarity="present",
        evidence=["我最近咳出白粉笔样物质。"],
    )

    result = brain.run_reasoning_search("s_trust", PatientContext(raw_text="我最近咳出白粉笔样物质。"))
    updated_state = tracker.get_session("s_trust")

    assert "symptom_chalk" in updated_state.evidence_states
    assert updated_state.evidence_states["symptom_chalk"].metadata["patient_stated_semantic_trust"] is True
    assert updated_state.candidate_hypotheses[0].score > 0.82
    assert result.selected_action is None
