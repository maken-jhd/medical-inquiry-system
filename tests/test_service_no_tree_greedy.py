"""测试 No-Tree Greedy 变体会跳过树搜索并直接按当前动作先验选下一问。"""

from types import SimpleNamespace

from brain.action_builder import ActionBuilder
from brain.service import BrainDependencies, ConsultationBrain, RepairPolicyConfig, SearchPolicyConfig
from brain.state_tracker import StateTracker
from brain.types import FinalAnswerScore, HypothesisScore, PatientContext


class StaticRetriever:
    """返回固定 R2 证据，避免测试依赖真实图数据库。"""

    client = object()

    def __init__(self, rows_by_hypothesis: dict[str, list[dict]]) -> None:
        self.rows_by_hypothesis = rows_by_hypothesis

    def retrieve_r2_expected_evidence(self, hypothesis: HypothesisScore, session_state, top_k=None) -> list[dict]:
        _ = session_state, top_k
        return list(self.rows_by_hypothesis.get(hypothesis.node_id, []))


class GuardedMctsEngine:
    """若 no-tree 路径误用树搜索入口，测试应立即失败。"""

    def __init__(self) -> None:
        self.config = SimpleNamespace(
            max_child_nodes=4,
            num_rollouts=8,
            max_depth=6,
        )

    def select_leaf(self, *args, **kwargs):  # pragma: no cover - 一旦被调用就会直接失败
        raise AssertionError("no_tree_greedy 不应调用 select_leaf()")

    def expand_node(self, *args, **kwargs):  # pragma: no cover - 一旦被调用就会直接失败
        raise AssertionError("no_tree_greedy 不应调用 expand_node()")

    def backpropagate(self, *args, **kwargs):  # pragma: no cover - 一旦被调用就会直接失败
        raise AssertionError("no_tree_greedy 不应调用 backpropagate()")


class StaticTrajectoryEvaluator:
    """用最小实现返回候选态最终分数，验证 no-tree fallback 会真正生效。"""

    def score_candidate_hypotheses_without_trajectories(
        self,
        hypotheses: list[HypothesisScore],
        patient_context: PatientContext,
    ) -> list[FinalAnswerScore]:
        _ = patient_context
        return [
            FinalAnswerScore(
                answer_id=item.node_id,
                answer_name=item.name,
                consistency=0.0,
                diversity=0.0,
                agent_evaluation=min(max(float(item.score), 0.0), 1.0),
                final_score=float(item.score),
                metadata={
                    "verifier_mode": "observed_evidence_final_evaluator",
                    "verifier_should_accept": False,
                },
            )
            for item in hypotheses
        ]

    def select_best_answer(self, scores: list[FinalAnswerScore]) -> FinalAnswerScore | None:
        if len(scores) == 0:
            return None
        return sorted(scores, key=lambda item: (-item.final_score, item.answer_name))[0]


def _build_brain(rows_by_hypothesis: dict[str, list[dict]]) -> tuple[ConsultationBrain, StateTracker]:
    tracker = StateTracker()
    brain = ConsultationBrain(
        BrainDependencies(
            state_tracker=tracker,
            retriever=StaticRetriever(rows_by_hypothesis),
            med_extractor=object(),
            entity_linker=object(),
            question_selector=object(),
            acceptance_controller=object(),
            report_builder=object(),
            evidence_parser=object(),
            hypothesis_manager=object(),
            action_builder=ActionBuilder(),
            router=object(),
            mcts_engine=GuardedMctsEngine(),
            simulation_engine=object(),
            trajectory_evaluator=StaticTrajectoryEvaluator(),
            evidence_anchor_analyzer=object(),
            llm_client=object(),
            repair_policy=RepairPolicyConfig(),
            search_policy=SearchPolicyConfig(root_action_mode="no_tree_greedy"),
        )
    )
    return brain, tracker


def _no_tree_rows() -> list[dict]:
    return [
        {
            "node_id": "symptom_fever",
            "label": "ClinicalFinding",
            "name": "发热",
            "relation_type": "MANIFESTS_AS",
            "question_type_hint": "symptom",
            "acquisition_mode": "direct_ask",
            "evidence_cost": "low",
            "priority": 2.1,
            "contradiction_priority": 0.4,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
        },
        {
            "node_id": "symptom_cough",
            "label": "ClinicalFinding",
            "name": "咳嗽",
            "relation_type": "MANIFESTS_AS",
            "question_type_hint": "symptom",
            "acquisition_mode": "direct_ask",
            "evidence_cost": "low",
            "priority": 2.9,
            "contradiction_priority": 0.7,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
        },
    ]


# no-tree greedy 应直接按 root candidate 的局部先验选动作，不进入树搜索 rollout。
def test_run_reasoning_search_no_tree_greedy_skips_tree_search() -> None:
    brain, tracker = _build_brain({"pcp": _no_tree_rows()})
    state = tracker.create_session("s_no_tree")
    state.candidate_hypotheses = [
        HypothesisScore(node_id="pcp", label="Disease", name="PCP", score=0.82),
        HypothesisScore(node_id="tb", label="Disease", name="结核病", score=0.61),
    ]

    result = brain.run_reasoning_search(
        "s_no_tree",
        PatientContext(raw_text="咳嗽、发热，来咨询一下。"),
    )

    assert result.root_best_action is not None
    assert result.root_best_action.target_node_id == "symptom_cough"
    assert result.selected_action is result.root_best_action
    assert result.metadata["root_action_mode"] == "no_tree_greedy"
    assert result.metadata["no_tree_greedy"] is True
    assert result.metadata["rollouts_requested"] == 0
    assert result.metadata["rollouts_executed"] == 0
    assert result.metadata["rollout_trajectory_count"] == 0
    assert result.metadata["candidate_state_answer_fallback"] is True
    assert result.best_answer_id == "pcp"
    assert tracker.get_session("s_no_tree").metadata["last_search_result"] is result
