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


def _metabolic_low_cost_rows() -> list[dict]:
    return [
        {
            "node_id": "bmi_range",
            "label": "RiskFactor",
            "name": "28.0<=BMI<32.5kg/m²",
            "relation_type": "RISK_FACTOR_FOR",
            "question_type_hint": "risk",
            "acquisition_mode": "direct_ask",
            "evidence_cost": "low",
            "priority": 2.4,
            "contradiction_priority": 0.4,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
        },
        {
            "node_id": "ldl_high",
            "label": "ClinicalAttribute",
            "name": "低密度脂蛋白胆固醇升高",
            "relation_type": "REQUIRES_DETAIL",
            "question_type_hint": "detail",
            "acquisition_mode": "history_known",
            "evidence_cost": "low",
            "priority": 2.1,
            "contradiction_priority": 0.8,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
        },
    ]


def _high_cost_hiv_lab_action(node_id: str = "lab_hiv_ab") -> MctsAction:
    return MctsAction(
        action_id=f"verify::hiv::{node_id}",
        action_type="verify_evidence",
        target_node_id=node_id,
        target_node_label="LabFinding",
        target_node_name="HIV抗体阳性",
        hypothesis_id="hiv",
        prior_score=2.6,
        metadata={
            "question_text": "如果做过 HIV 相关抽血检查，比如 HIV 抗体、抗原、核酸或病毒载量，结果有没有提示阳性、检出，或者医生有没有明确提到 HIV？",
            "question_type_hint": "lab",
            "acquisition_mode": "needs_lab_test",
            "evidence_cost": "high",
            "relation_type": "DIAGNOSED_BY",
        },
    )


def _high_cost_imaging_action() -> MctsAction:
    return MctsAction(
        action_id="verify::pcp::ct",
        action_type="verify_evidence",
        target_node_id="ct_ground_glass",
        target_node_label="ImagingFinding",
        target_node_name="胸部CT磨玻璃影",
        hypothesis_id="pcp",
        prior_score=2.8,
        metadata={
            "question_type_hint": "imaging",
            "acquisition_mode": "needs_imaging",
            "evidence_cost": "high",
            "relation_type": "HAS_IMAGING_FINDING",
        },
    )


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


# 即使 target_node_id 不同，只要连续两轮会问出完全相同的句子，也应被拦住。
def test_service_blocks_same_question_text_even_when_target_differs() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s_repeat_guard")
    brain = _build_brain(tracker, {})

    previous_action = _high_cost_hiv_lab_action("lab_hiv_prev")
    state.metadata["last_question_fingerprint"] = brain._action_question_fingerprint(previous_action)

    assert brain._selected_action_is_askable("s_repeat_guard", _high_cost_hiv_lab_action("lab_hiv_next")) is False
    assert (
        brain._selected_action_block_reason("s_repeat_guard", _high_cost_hiv_lab_action("lab_hiv_next"))
        == "same_question_as_previous_turn"
    )


# 高成本 HIV 检查如果刚收到“没做过”反馈，应短期冷却同一家族问法，避免连续追问。
def test_service_negative_feedback_cooldown_blocks_same_hiv_test_family() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s_hiv_cooldown")
    state.turn_index = 2
    brain = _build_brain(tracker, {})

    brain._record_negative_feedback_cooldown(
        "s_hiv_cooldown",
        _high_cost_hiv_lab_action("lab_hiv_prev"),
        turn_index=2,
        patient_text="没做过这项检查。",
    )

    assert brain._selected_action_is_askable("s_hiv_cooldown", _high_cost_hiv_lab_action("lab_hiv_next")) is False
    assert (
        brain._selected_action_block_reason("s_hiv_cooldown", _high_cost_hiv_lab_action("lab_hiv_next"))
        == "negative_feedback_cooldown"
    )


# 连续高成本检查落空后，应强制退回低成本定义性证据，而不是继续问 BMI 这类背景项。
def test_service_forces_low_cost_definition_fallback_after_repeated_exam_no_result() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s_low_cost_definition_fallback")
    state.candidate_hypotheses = [
        HypothesisScore(
            node_id="dyslipidemia",
            label="Disease",
            name="血脂异常",
            score=1.0,
        )
    ]
    state.metadata["recent_high_cost_no_result_streak"] = 2
    brain = _build_brain(tracker, {"dyslipidemia": _metabolic_low_cost_rows()})
    search_result = SearchResult()

    action = brain._choose_low_cost_explorer_action(
        "s_low_cost_definition_fallback",
        search_result,
        _high_cost_imaging_action(),
    )

    assert action is not None
    assert action.target_node_id == "ldl_high"
    assert action.metadata["selected_by_low_cost_explorer"] is True
    assert search_result.metadata["selected_action_source_reason"] == "recent_exam_no_result_streak"


# 关闭 best repair action 后，如需暴露 search root，本轮 askable root 不应再被 low-cost explorer 抢走。
def test_service_protects_search_root_action_from_low_cost_explorer_when_enabled() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s_search_root_guard")
    state.candidate_hypotheses = [
        HypothesisScore(node_id="pcp", label="Disease", name="PCP", score=1.0),
    ]
    brain = _build_brain(tracker, {"pcp": _exam_driven_rows()})
    brain.deps.repair_policy.protect_search_root_action_from_low_cost_explorer = True
    root_action = _low_cost_symptom_action()
    search_result = SearchResult(root_best_action=root_action)

    should_skip, reason = brain._should_skip_low_cost_explorer_after_search_root(
        "s_search_root_guard",
        search_result,
        root_action,
    )

    assert should_skip is True
    assert reason == "search_root_action_protected"


# 若 search root 自己已经不可问，则仍允许 explorer 接管，避免保护逻辑把会话卡死。
def test_service_allows_low_cost_explorer_after_unaskable_search_root() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s_search_root_unaskable")
    state.candidate_hypotheses = [
        HypothesisScore(node_id="pcp", label="Disease", name="PCP", score=1.0),
    ]
    state.asked_node_ids.append("symptom_fever")
    brain = _build_brain(tracker, {"pcp": _exam_driven_rows()})
    brain.deps.repair_policy.protect_search_root_action_from_low_cost_explorer = True
    root_action = _low_cost_symptom_action()
    search_result = SearchResult(root_best_action=root_action)

    should_skip, reason = brain._should_skip_low_cost_explorer_after_search_root(
        "s_search_root_unaskable",
        search_result,
        root_action,
    )

    assert should_skip is False
    assert reason == "search_root_action_unaskable"
