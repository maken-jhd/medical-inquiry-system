"""覆盖 facade 重构后新增 coordinator 的最小委托关系。"""

from brain.acceptance import AcceptanceCoordinator
from brain.search import SearchCoordinator
from brain.state import MctsAction, PatientContext, SearchResult
from brain.turn import TurnCoordinator


class FakeRuntime:
    """提供 coordinator 需要的最小 runtime 行为。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def _process_turn_impl(self, session_id: str, patient_text: str) -> dict:
        self.calls.append(("process_turn", (session_id, patient_text)))
        return {"session_id": session_id, "patient_text": patient_text}

    def _run_reasoning_search_impl(self, session_id: str, patient_context: PatientContext) -> SearchResult:
        self.calls.append(("run_reasoning_search", (session_id, patient_context.raw_text)))
        return SearchResult(best_answer_name="PCP")

    def _choose_next_question_from_search_impl(
        self,
        session_id: str,
        search_result: SearchResult,
    ) -> MctsAction | None:
        self.calls.append(("choose_next_question", (session_id, search_result.best_answer_name)))
        return MctsAction(
            action_id="verify::pcp::cough",
            action_type="verify_evidence",
            target_node_id="cough",
            target_node_label="ClinicalFinding",
            target_node_name="咳嗽",
        )

    def finalize(self, session_id: str) -> dict:
        self.calls.append(("finalize", session_id))
        return {"decision": "finalize", "session_id": session_id}

    def _finalize_from_search_impl(self, session_id: str, search_result: SearchResult) -> dict:
        self.calls.append(("finalize_from_search", (session_id, search_result.best_answer_name)))
        return {"decision": "finalize_from_search", "session_id": session_id}


def test_turn_coordinator_delegates_to_runtime() -> None:
    runtime = FakeRuntime()
    coordinator = TurnCoordinator(runtime)

    result = coordinator.process_turn("s_turn", "我最近咳嗽。")

    assert result["session_id"] == "s_turn"
    assert runtime.calls == [("process_turn", ("s_turn", "我最近咳嗽。"))]


def test_search_coordinator_delegates_to_runtime() -> None:
    runtime = FakeRuntime()
    coordinator = SearchCoordinator(runtime)

    search_result = coordinator.run_reasoning_search("s_search", PatientContext(raw_text="我最近发热。"))
    next_action = coordinator.choose_next_question_from_search("s_search", search_result)

    assert search_result.best_answer_name == "PCP"
    assert next_action is not None
    assert next_action.target_node_name == "咳嗽"
    assert runtime.calls == [
        ("run_reasoning_search", ("s_search", "我最近发热。")),
        ("choose_next_question", ("s_search", "PCP")),
    ]


def test_acceptance_coordinator_delegates_to_runtime() -> None:
    runtime = FakeRuntime()
    coordinator = AcceptanceCoordinator(runtime)
    search_result = SearchResult(best_answer_name="PCP")

    final_report = coordinator.finalize("s_accept")
    final_from_search = coordinator.finalize_from_search("s_accept", search_result)

    assert final_report["decision"] == "finalize"
    assert final_from_search["decision"] == "finalize_from_search"
    assert runtime.calls == [
        ("finalize", "s_accept"),
        ("finalize_from_search", ("s_accept", "PCP")),
    ]
