"""测试 verifier 拒停后的 repair action 分流。"""

from brain.action_builder import ActionBuilder
from brain.hypothesis_manager import HypothesisManager
from brain.service import BrainDependencies, ConsultationBrain
from brain.state_tracker import StateTracker
from brain.types import HypothesisScore, MctsAction, SearchResult, SessionState


class RepairFakeRetriever:
    """提供固定 R2 候选，验证 repair action 的显式选择。"""

    client = object()

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def retrieve_r2_expected_evidence(self, hypothesis: HypothesisScore, session_state: SessionState, top_k: int | None = None) -> list[dict]:
        _ = hypothesis, session_state, top_k
        return list(self.rows)


def _build_brain(rows: list[dict], state_tracker: StateTracker) -> ConsultationBrain:
    return ConsultationBrain(
        BrainDependencies(
            state_tracker=state_tracker,
            retriever=RepairFakeRetriever(rows),
            med_extractor=object(),
            entity_linker=object(),
            question_selector=object(),
            stop_rule_engine=object(),
            report_builder=object(),
            evidence_parser=object(),
            hypothesis_manager=HypothesisManager(),
            action_builder=ActionBuilder(),
            router=object(),
            mcts_engine=object(),
            simulation_engine=object(),
            trajectory_evaluator=object(),
            llm_client=object(),
        )
    )


# 验证 missing_key_support 会优先选择 verifier 推荐的补强证据。
def test_service_prefers_recommended_repair_action_for_missing_support() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s1")
    state.candidate_hypotheses = [
        HypothesisScore(
            node_id="d1",
            label="Disease",
            name="肺孢子菌肺炎 (PCP)",
            score=1.0,
            metadata={"recommended_next_evidence": ["低氧血症"]},
        )
    ]
    rows = [
        {
            "node_id": "symptom_lymph",
            "label": "Sign",
            "name": "淋巴结肿大",
            "relation_type": "MANIFESTS_AS",
            "relation_weight": 1.0,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
            "contradiction_priority": 0.85,
            "question_type_hint": "symptom",
            "priority": 3.2,
            "is_red_flag": False,
            "topic_id": "Disease",
        },
        {
            "node_id": "lab_oxy",
            "label": "LabFinding",
            "name": "低氧血症",
            "relation_type": "HAS_LAB_FINDING",
            "relation_weight": 0.8,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
            "contradiction_priority": 1.0,
            "question_type_hint": "lab",
            "priority": 2.7,
            "is_red_flag": True,
            "topic_id": "Disease",
        },
    ]
    brain = _build_brain(rows, tracker)

    action = brain._choose_repair_action(
        "s1",
        SearchResult(),
        {"reject_reason": "missing_key_support", "recommended_next_evidence": ["低氧血症"]},
    )

    assert action is not None
    assert action.target_node_name == "低氧血症"


# 验证 trajectory_insufficient 会倾向切换到不同 question type，避免围绕同类问题打转。
def test_service_prefers_diversified_question_type_for_trajectory_insufficient() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s2")
    state.candidate_hypotheses = [
        HypothesisScore(
            node_id="phase_acute",
            label="DiseasePhase",
            name="急性期",
            score=1.0,
            metadata={},
        )
    ]
    state.metadata["last_answered_action"] = MctsAction(
        action_id="verify::phase_acute::lab_cd4",
        action_type="verify_evidence",
        target_node_id="lab_cd4",
        target_node_label="LabFinding",
        target_node_name="CD4+ T淋巴细胞计数 < 200/μL",
        metadata={"question_type_hint": "lab"},
    )
    rows = [
        {
            "node_id": "lab_viral_load",
            "label": "LabFinding",
            "name": "HIV RNA 病毒载量升高",
            "relation_type": "HAS_LAB_FINDING",
            "relation_weight": 0.7,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
            "contradiction_priority": 0.9,
            "question_type_hint": "lab",
            "priority": 2.2,
            "is_red_flag": False,
            "topic_id": "DiseasePhase",
        },
        {
            "node_id": "symptom_rash",
            "label": "Symptom",
            "name": "皮疹",
            "relation_type": "MANIFESTS_AS",
            "relation_weight": 0.9,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
            "contradiction_priority": 0.8,
            "question_type_hint": "symptom",
            "priority": 2.15,
            "is_red_flag": False,
            "topic_id": "DiseasePhase",
        },
    ]
    brain = _build_brain(rows, tracker)

    action = brain._choose_repair_action(
        "s2",
        SearchResult(),
        {"reject_reason": "trajectory_insufficient", "recommended_next_evidence": []},
    )

    assert action is not None
    assert action.target_node_name == "皮疹"
