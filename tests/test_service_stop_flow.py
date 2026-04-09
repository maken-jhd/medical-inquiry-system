"""测试 service 层在 verifier 介入后的停止控制流。"""

from brain.report_builder import ReportBuilder
from brain.service import BrainDependencies, ConsultationBrain
from brain.types import FinalAnswerScore, MctsAction, ReasoningTrajectory, SearchResult, SessionState, StopDecision


class DummyRetriever:
    """提供最小 retriever 占位对象，满足 ConsultationBrain 初始化。"""

    client = object()


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
