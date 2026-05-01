"""测试 service 层在 verifier 介入后的停止控制流。"""

from brain.action_builder import ActionBuilder
from brain.question_selector import QuestionSelector
from brain.report_builder import ReportBuilder
from brain.service import BrainDependencies, ConsultationBrain
from brain.state_tracker import StateTracker
from brain.types import (
    A1ExtractionResult,
    FinalAnswerScore,
    HypothesisScore,
    MctsAction,
    PatientContext,
    QuestionCandidate,
    ReasoningTrajectory,
    RouteDecision,
    SearchResult,
    SessionState,
    StopDecision,
    TurnInterpretationResult,
)


class DummyRetriever:
    """提供最小 retriever 占位对象，满足 ConsultationBrain 初始化。"""

    client = object()


class ColdStartRetriever(DummyRetriever):
    """提供固定冷启动问题，验证稀疏输入下不会空转。"""

    def get_cold_start_questions(self) -> list[QuestionCandidate]:
        return [
            QuestionCandidate(
                node_id="risk_hiv",
                label="RiskFactor",
                name="HIV感染或免疫抑制背景",
                topic_id="RiskFactor",
                priority=3.0,
                graph_weight=1.0,
            )
        ]


class EvidenceProfileRetriever(DummyRetriever):
    """提供固定候选证据画像，验证 service 会注入 A2 展示字段。"""

    def retrieve_candidate_evidence_profile(self, hypothesis: object, session_state: SessionState) -> list[dict]:
        _ = hypothesis, session_state
        return [
            {
                "node_id": "symptom_fever",
                "name": "发热",
                "label": "ClinicalFinding",
                "relation_type": "MANIFESTS_AS",
                "question_type_hint": "symptom",
                "group": "symptom",
                "status": "matched",
                "status_label": "已命中",
            },
            {
                "node_id": "lab_cd4_low",
                "name": "CD4+ T淋巴细胞计数 < 200/μL",
                "label": "LabFinding",
                "relation_type": "HAS_LAB_FINDING",
                "question_type_hint": "lab",
                "group": "lab",
                "status": "unknown",
                "status_label": "待验证",
            },
        ]


class FakeStateTracker:
    """返回固定会话状态，供 finalize / finalize_from_search 使用。"""

    def __init__(self, state: SessionState) -> None:
        self.state = state

    def get_session(self, session_id: str) -> SessionState:
        assert session_id == self.state.session_id
        return self.state


class FakeStopRuleEngine:
    """返回预设 accept decision，并保留一个基础 sufficiency 结果。"""

    def __init__(self, accept_decision: StopDecision) -> None:
        self.accept_decision = accept_decision

    def should_accept_final_answer(
        self,
        answer_score: FinalAnswerScore | None,
        session_state: SessionState | None = None,
    ) -> StopDecision:
        _ = answer_score, session_state
        return self.accept_decision

    def check_sufficiency(self, session_state: SessionState, hypotheses: list[object]) -> StopDecision:
        _ = session_state, hypotheses
        return StopDecision(True, "top1_margin_sufficient", 1.0)


class FakeTrajectoryEvaluator:
    """总是返回预设的最佳答案评分。"""

    def __init__(self, best_answer: FinalAnswerScore) -> None:
        self.best_answer = best_answer

    def select_best_answer(self, scores: list[FinalAnswerScore]) -> FinalAnswerScore | None:
        _ = scores
        return self.best_answer


class EmptyMedExtractor:
    """模拟“你好医生”这类没有临床信息的输入。"""

    def extract_patient_context(self, patient_text: str) -> PatientContext:
        return PatientContext(raw_text=patient_text)


class EmptyEvidenceParser:
    """模拟 A1 没有抽到任何关键线索。"""

    def interpret_turn(self, patient_text: str, pending_action: MctsAction | None = None) -> TurnInterpretationResult:
        _ = patient_text, pending_action
        return TurnInterpretationResult()

    def build_patient_context_from_turn(self, turn_result: TurnInterpretationResult, patient_text: str) -> PatientContext:
        _ = turn_result
        return PatientContext(raw_text=patient_text)

    def run_a1_key_symptom_extraction(
        self,
        patient_context: PatientContext,
        known_feature_names: list[str] | None = None,
    ) -> A1ExtractionResult:
        _ = patient_context, known_feature_names
        return A1ExtractionResult()

    def build_slot_updates_from_a1(self, extraction_result: A1ExtractionResult, turn_index: int | None = None) -> list:
        _ = extraction_result, turn_index
        return []


class EmptyEntityLinker:
    """模拟没有实体可链接。"""

    def link_clinical_features(self, features: list) -> list:
        _ = features
        return []


class MinimalRouter:
    """提供 process_turn 所需的最小路由能力。"""

    def route_after_slot_update(self, state: SessionState) -> RouteDecision:
        _ = state
        return RouteDecision(stage="A2", reason="minimal_test_route")


class NonStoppingRuleEngine:
    """测试中始终保持继续问诊。"""

    def check_sufficiency(self, session_state: SessionState, hypotheses: list[object]) -> StopDecision:
        _ = session_state, hypotheses
        return StopDecision(False, "insufficient_information", 0.0)

    def should_accept_final_answer(self, answer_score: object, session_state: SessionState) -> StopDecision:
        _ = answer_score, session_state
        return StopDecision(False, "no_answer_score", 0.0)


class EmptyTrajectoryEvaluator:
    """没有搜索答案时返回 None。"""

    def select_best_answer(self, scores: list) -> None:
        _ = scores
        return None


def _build_brain(state: SessionState, accept_decision: StopDecision, best_answer: FinalAnswerScore) -> ConsultationBrain:
    return ConsultationBrain(
        BrainDependencies(
            state_tracker=FakeStateTracker(state),
            retriever=DummyRetriever(),
            med_extractor=object(),
            entity_linker=object(),
            question_selector=object(),
            stop_rule_engine=FakeStopRuleEngine(accept_decision),
            report_builder=ReportBuilder(),
            evidence_parser=object(),
            hypothesis_manager=object(),
            action_builder=object(),
            router=object(),
            mcts_engine=object(),
            simulation_engine=object(),
            trajectory_evaluator=FakeTrajectoryEvaluator(best_answer),
            llm_client=object(),
        )
    )


def test_service_does_not_emit_final_report_when_verifier_rejects_search_stop() -> None:
    state = SessionState(session_id="s1", turn_index=1)
    best_answer = FinalAnswerScore(
        answer_id="phase_acute",
        answer_name="急性期",
        consistency=1.0,
        diversity=0.6,
        agent_evaluation=0.3,
        final_score=0.61,
        metadata={
            "trajectory_count": 4,
            "verifier_mode": "llm_verifier",
            "verifier_should_accept": False,
        },
    )
    brain = _build_brain(
        state,
        StopDecision(False, "verifier_rejected_stop", 0.3),
        best_answer,
    )
    search_result = SearchResult(
        selected_action=MctsAction(
            action_id="verify::acute::cd4",
            action_type="verify_evidence",
            target_node_id="lab_cd4",
            target_node_label="LabFinding",
            target_node_name="CD4+ T淋巴细胞计数 < 200/μL",
            hypothesis_id="phase_acute",
        ),
        final_answer_scores=[best_answer],
    )

    should_emit = brain._should_emit_final_report(
        search_result,
        search_result.selected_action,
        StopDecision(True, "top1_margin_sufficient", 1.0),
        StopDecision(False, "verifier_rejected_stop", 0.3),
    )

    assert should_emit is False


def test_finalize_from_search_preserves_verifier_rejection_reason() -> None:
    state = SessionState(session_id="s2", turn_index=1)
    best_answer = FinalAnswerScore(
        answer_id="phase_acute",
        answer_name="急性期",
        consistency=1.0,
        diversity=0.6,
        agent_evaluation=0.3,
        final_score=0.61,
        metadata={
            "trajectory_count": 4,
            "verifier_mode": "llm_verifier",
            "verifier_should_accept": False,
        },
    )
    brain = _build_brain(
        state,
        StopDecision(False, "verifier_rejected_stop", 0.3),
        best_answer,
    )
    search_result = SearchResult(
        final_answer_scores=[best_answer],
        best_answer_id="phase_acute",
        best_answer_name="急性期",
        trajectories=[
            ReasoningTrajectory(
                trajectory_id="trajectory::acute::1",
                final_answer_id="phase_acute",
                final_answer_name="急性期",
                score=1.2,
            )
        ],
    )

    report = brain.finalize_from_search("s2", search_result)

    assert report["stop_reason"] == "verifier_rejected_stop"
    assert report["best_final_answer"]["answer_name"] == "急性期"


def test_choose_cold_start_probe_action_when_a2_a3_has_no_action() -> None:
    state = SessionState(session_id="s3", turn_index=1)
    brain = ConsultationBrain(
        BrainDependencies(
            state_tracker=FakeStateTracker(state),
            retriever=ColdStartRetriever(),
            med_extractor=object(),
            entity_linker=object(),
            question_selector=QuestionSelector(),
            stop_rule_engine=object(),
            report_builder=ReportBuilder(),
            evidence_parser=object(),
            hypothesis_manager=object(),
            action_builder=ActionBuilder(),
            router=object(),
            mcts_engine=object(),
            simulation_engine=object(),
            trajectory_evaluator=object(),
            llm_client=object(),
        )
    )

    action = brain._choose_cold_start_probe_action("s3")

    assert action is not None
    assert action.action_type == "probe_feature"
    assert action.target_node_name == "HIV感染或免疫抑制背景"


def test_process_turn_asks_chief_complaint_when_patient_only_greets() -> None:
    tracker = StateTracker()
    tracker.create_session("s4")
    brain = ConsultationBrain(
        BrainDependencies(
            state_tracker=tracker,
            retriever=DummyRetriever(),
            med_extractor=EmptyMedExtractor(),
            entity_linker=EmptyEntityLinker(),
            question_selector=QuestionSelector(),
            stop_rule_engine=NonStoppingRuleEngine(),
            report_builder=ReportBuilder(),
            evidence_parser=EmptyEvidenceParser(),
            hypothesis_manager=object(),
            action_builder=ActionBuilder(),
            router=MinimalRouter(),
            mcts_engine=object(),
            simulation_engine=object(),
            trajectory_evaluator=EmptyTrajectoryEvaluator(),
            llm_client=object(),
        )
    )

    result = brain.process_turn("s4", "你好，医生")
    pending_action = tracker.get_pending_action("s4")

    assert pending_action is not None
    assert pending_action.action_type == "collect_chief_complaint"
    assert "哪里不舒服" in result["next_question"]
    assert result["pending_action"]["target_node_name"] == "主要不适 / 就诊原因"


def test_chief_complaint_pending_action_routes_next_reply_back_to_a1() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s5")
    intake_action = ActionBuilder().build_a3_verification_result(
        MctsAction(
            action_id="intake::chief_complaint",
            action_type="collect_chief_complaint",
            target_node_id="__chief_complaint__",
            target_node_label="Intake",
            target_node_name="主要不适 / 就诊原因",
        )
    ).relevant_symptom
    assert intake_action is not None
    tracker.set_pending_action("s5", intake_action)
    brain = ConsultationBrain(
        BrainDependencies(
            state_tracker=tracker,
            retriever=DummyRetriever(),
            med_extractor=EmptyMedExtractor(),
            entity_linker=EmptyEntityLinker(),
            question_selector=QuestionSelector(),
            stop_rule_engine=NonStoppingRuleEngine(),
            report_builder=ReportBuilder(),
            evidence_parser=EmptyEvidenceParser(),
            hypothesis_manager=object(),
            action_builder=ActionBuilder(),
            router=MinimalRouter(),
            mcts_engine=object(),
            simulation_engine=object(),
            trajectory_evaluator=EmptyTrajectoryEvaluator(),
            llm_client=object(),
        )
    )

    route_result = brain.update_from_pending_action("s5", PatientContext(raw_text="我发热"), "我发热", 2)

    assert route_result[0] is None
    assert route_result[2].stage == "A1"
    assert tracker.get_pending_action("s5") is None
    assert state.metadata["last_answered_action"].action_type == "collect_chief_complaint"


def test_process_turn_stops_after_repeated_chief_complaint_without_signal() -> None:
    tracker = StateTracker()
    tracker.create_session("s5_repeat")
    brain = ConsultationBrain(
        BrainDependencies(
            state_tracker=tracker,
            retriever=DummyRetriever(),
            med_extractor=EmptyMedExtractor(),
            entity_linker=EmptyEntityLinker(),
            question_selector=QuestionSelector(),
            stop_rule_engine=NonStoppingRuleEngine(),
            report_builder=ReportBuilder(),
            evidence_parser=EmptyEvidenceParser(),
            hypothesis_manager=object(),
            action_builder=ActionBuilder(),
            router=MinimalRouter(),
            mcts_engine=object(),
            simulation_engine=object(),
            trajectory_evaluator=EmptyTrajectoryEvaluator(),
            llm_client=object(),
        )
    )

    first_turn = brain.process_turn("s5_repeat", "你好，医生")
    assert first_turn["pending_action"]["action_type"] == "collect_chief_complaint"

    second_turn = brain.process_turn("s5_repeat", "这个我也说不上来")

    assert second_turn["final_report"] is not None
    assert second_turn["final_report"]["stop_reason"] == "repeated_chief_complaint_without_signal"
    assert second_turn["pending_action"] is None
    assert tracker.get_pending_action("s5_repeat") is None


def test_service_skips_a2_refresh_during_regular_a3_followup() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s5_a3")
    state.candidate_hypotheses = [
        HypothesisScore(node_id="d1", label="Disease", name="候选病A", score=0.9),
        HypothesisScore(node_id="d2", label="Disease", name="候选病B", score=0.6),
    ]
    brain = ConsultationBrain(
        BrainDependencies(
            state_tracker=tracker,
            retriever=DummyRetriever(),
            med_extractor=EmptyMedExtractor(),
            entity_linker=EmptyEntityLinker(),
            question_selector=QuestionSelector(),
            stop_rule_engine=NonStoppingRuleEngine(),
            report_builder=ReportBuilder(),
            evidence_parser=EmptyEvidenceParser(),
            hypothesis_manager=object(),
            action_builder=ActionBuilder(),
            router=MinimalRouter(),
            mcts_engine=object(),
            simulation_engine=object(),
            trajectory_evaluator=EmptyTrajectoryEvaluator(),
            llm_client=object(),
        )
    )

    should_refresh = brain._should_refresh_a2(
        state,
        effective_stage="A3",
        should_run_a1=False,
        a1_result=A1ExtractionResult(),
    )

    assert should_refresh is False


def test_service_builds_a2_evidence_profiles_for_frontend() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s6")
    state.candidate_hypotheses = [
        HypothesisScore(node_id="pcp", label="Disease", name="肺孢子菌肺炎 (PCP)", score=0.82),
    ]
    brain = ConsultationBrain(
        BrainDependencies(
            state_tracker=tracker,
            retriever=EvidenceProfileRetriever(),
            med_extractor=object(),
            entity_linker=object(),
            question_selector=object(),
            stop_rule_engine=object(),
            report_builder=ReportBuilder(),
            evidence_parser=object(),
            hypothesis_manager=object(),
            action_builder=ActionBuilder(),
            router=object(),
            mcts_engine=object(),
            simulation_engine=object(),
            trajectory_evaluator=object(),
            llm_client=object(),
        )
    )

    profiles = brain._build_a2_evidence_profiles("s6")

    assert len(profiles) == 1
    assert profiles[0]["candidate_name"] == "肺孢子菌肺炎 (PCP)"
    assert profiles[0]["matched_count"] == 1
    assert profiles[0]["unknown_count"] == 1
    assert profiles[0]["evidence_groups"]["symptom"][0]["name"] == "发热"
    assert profiles[0]["evidence_groups"]["lab"][0]["status"] == "unknown"
