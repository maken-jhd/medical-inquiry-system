"""测试第一批里 repair / explorer 仲裁与 early exam-context rescue。"""

from brain.action_builder import ActionBuilder
from brain.hypothesis_manager import HypothesisManager
from brain.service import BrainDependencies, ConsultationBrain
from brain.state_tracker import StateTracker
from brain.types import HypothesisScore, MctsAction, SearchResult, SessionState


class RescueRetriever:
    """返回固定 R2 证据，避免测试依赖真实 Neo4j。"""

    client = object()

    def __init__(self, rows_by_hypothesis: dict[str, list[dict]]) -> None:
        self.rows_by_hypothesis = rows_by_hypothesis

    def retrieve_r2_expected_evidence(
        self,
        hypothesis: HypothesisScore,
        session_state: SessionState,
        top_k: int | None = None,
    ) -> list[dict]:
        _ = session_state, top_k
        return list(self.rows_by_hypothesis.get(hypothesis.node_id, []))


def _build_brain(
    tracker: StateTracker,
    rows_by_hypothesis: dict[str, list[dict]],
) -> ConsultationBrain:
    return ConsultationBrain(
        BrainDependencies(
            state_tracker=tracker,
            retriever=RescueRetriever(rows_by_hypothesis),
            med_extractor=object(),
            entity_linker=object(),
            question_selector=object(),
            acceptance_controller=object(),
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


def _low_cost_symptom_action() -> MctsAction:
    return MctsAction(
        action_id="verify::pcp::fever",
        action_type="verify_evidence",
        target_node_id="symptom_fever",
        target_node_label="ClinicalFinding",
        target_node_name="发热",
        hypothesis_id="pcp",
        prior_score=2.1,
        metadata={
            "question_type_hint": "symptom",
            "acquisition_mode": "direct_ask",
            "evidence_cost": "low",
            "relation_type": "MANIFESTS_AS",
            "evidence_tags": ["phenotype", "type:symptom"],
        },
    )


def _general_exam_action() -> MctsAction:
    return MctsAction(
        action_id="collect_exam::pcp::general",
        action_type="collect_general_exam_context",
        target_node_id="__exam_context__::general",
        target_node_label="ExamContext",
        target_node_name="医院检查情况",
        hypothesis_id="pcp",
        prior_score=2.8,
        metadata={
            "exam_kind": "general",
            "question_type_hint": "exam_context",
            "candidate_exam_kinds": ["lab"],
            "exam_candidate_evidence": [
                {
                    "node_id": "lab_cd4_low",
                    "label": "LabFinding",
                    "name": "CD4+ T淋巴细胞计数 < 200/μL",
                    "relation_type": "HAS_LAB_FINDING",
                    "question_type_hint": "lab",
                    "acquisition_mode": "needs_lab_test",
                    "evidence_cost": "high",
                    "exam_kind": "lab",
                    "priority": 2.8,
                    "discriminative_gain": 1.1,
                    "recommended_match_score": 0.8,
                    "verifier_recommended_match_score": 0.8,
                    "joint_recommended_match_score": 0.8,
                }
            ],
        },
    )


def _exam_driven_rows() -> list[dict]:
    return [
        {
            "node_id": "symptom_fever",
            "label": "ClinicalFinding",
            "name": "发热",
            "relation_type": "MANIFESTS_AS",
            "question_type_hint": "symptom",
            "acquisition_mode": "direct_ask",
            "evidence_cost": "low",
            "priority": 2.4,
            "contradiction_priority": 0.4,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
        },
        {
            "node_id": "lab_cd4_low",
            "label": "LabFinding",
            "name": "CD4+ T淋巴细胞计数 < 200/μL",
            "relation_type": "HAS_LAB_FINDING",
            "question_type_hint": "lab",
            "acquisition_mode": "needs_lab_test",
            "evidence_cost": "high",
            "priority": 2.6,
            "contradiction_priority": 0.9,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
        },
        {
            "node_id": "ct_ground_glass",
            "label": "ImagingFinding",
            "name": "胸部CT磨玻璃影",
            "relation_type": "HAS_IMAGING_FINDING",
            "question_type_hint": "imaging",
            "acquisition_mode": "needs_imaging",
            "evidence_cost": "high",
            "priority": 2.7,
            "contradiction_priority": 0.85,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
        },
        {
            "node_id": "pcr_positive",
            "label": "Pathogen",
            "name": "肺孢子菌 PCR 阳性",
            "relation_type": "HAS_PATHOGEN",
            "question_type_hint": "pathogen",
            "acquisition_mode": "needs_pathogen_test",
            "evidence_cost": "high",
            "priority": 2.9,
            "contradiction_priority": 0.95,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
        },
    ]


# repair 动作仍可问时，不允许 low-cost explorer 抢走这一轮。
def test_service_protects_askable_repair_action_from_low_cost_explorer() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s_repair_guard")
    state.candidate_hypotheses = [
        HypothesisScore(node_id="pcp", label="Disease", name="PCP", score=1.0),
    ]
    brain = _build_brain(tracker, {"pcp": _exam_driven_rows()})
    repair_action = _general_exam_action()
    search_result = SearchResult(repair_selected_action=repair_action)

    should_skip, reason = brain._should_skip_low_cost_explorer_after_repair(
        "s_repair_guard",
        search_result,
        repair_action,
    )

    assert should_skip is True
    assert reason == "repair_action_protected"


# repair 动作已经不可问时，允许继续退回 explorer 找替代动作。
def test_service_allows_low_cost_explorer_after_unaskable_repair_action() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s_repair_unaskable")
    state.candidate_hypotheses = [
        HypothesisScore(node_id="pcp", label="Disease", name="PCP", score=1.0),
    ]
    state.asked_node_ids.append("__exam_context__::general")
    brain = _build_brain(tracker, {"pcp": _exam_driven_rows()})
    repair_action = _general_exam_action()
    search_result = SearchResult(repair_selected_action=repair_action)

    should_skip, reason = brain._should_skip_low_cost_explorer_after_repair(
        "s_repair_unaskable",
        search_result,
        repair_action,
    )

    assert should_skip is False
    assert reason == "repair_action_unaskable"


# 前两轮且候选明显 exam-driven 时，应主动把问题拉回 general exam context。
def test_service_early_exam_context_rescue_prefers_general_exam_entry() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s_exam_rescue")
    state.candidate_hypotheses = [
        HypothesisScore(
            node_id="pcp",
            label="Disease",
            name="PCP",
            score=1.0,
            metadata={"anchor_tier": "background_supported"},
        )
    ]
    brain = _build_brain(tracker, {"pcp": _exam_driven_rows()})
    search_result = SearchResult()

    action = brain._maybe_choose_early_exam_context_rescue_action(
        "s_exam_rescue",
        search_result,
        _low_cost_symptom_action(),
        repair_context=None,
        turn_index=1,
    )

    assert action is not None
    assert action.action_type == "collect_general_exam_context"
    assert action.target_node_id == "__exam_context__::general"
    assert action.metadata["selected_by_early_exam_context_rescue"] is True
    assert search_result.metadata["selected_action_source"] == "early_exam_context_rescue"
    assert search_result.metadata["early_exam_context_rescue"]["triggered"] is True


# general 入口问过后，early rescue 应继续落到具体的 lab / imaging / pathogen 结果入口。
def test_service_early_exam_context_rescue_prefers_specific_exam_after_general_was_asked() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s_exam_rescue_specific")
    state.candidate_hypotheses = [
        HypothesisScore(
            node_id="pcp",
            label="Disease",
            name="PCP",
            score=1.0,
            metadata={"anchor_tier": "background_supported"},
        )
    ]
    state.asked_node_ids.append("__exam_context__::general")
    brain = _build_brain(
        tracker,
        {
            "pcp": [
                {
                    "node_id": "symptom_fever",
                    "label": "ClinicalFinding",
                    "name": "发热",
                    "relation_type": "MANIFESTS_AS",
                    "question_type_hint": "symptom",
                    "acquisition_mode": "direct_ask",
                    "evidence_cost": "low",
                    "priority": 2.0,
                    "contradiction_priority": 0.4,
                    "node_weight": 1.0,
                    "similarity_confidence": 1.0,
                },
                {
                    "node_id": "lab_bdg",
                    "label": "LabFinding",
                    "name": "(1,3)-β-D-葡聚糖检测升高",
                    "relation_type": "HAS_LAB_FINDING",
                    "question_type_hint": "lab",
                    "acquisition_mode": "needs_lab_test",
                    "evidence_cost": "high",
                    "priority": 2.8,
                    "contradiction_priority": 0.9,
                    "node_weight": 1.0,
                    "similarity_confidence": 1.0,
                },
            ]
        },
    )
    search_result = SearchResult()

    action = brain._maybe_choose_early_exam_context_rescue_action(
        "s_exam_rescue_specific",
        search_result,
        _low_cost_symptom_action(),
        repair_context=None,
        turn_index=2,
    )

    assert action is not None
    assert action.action_type == "collect_exam_context"
    assert action.target_node_id == "__exam_context__::lab"
    assert action.metadata["selected_by_early_exam_context_rescue"] is True
