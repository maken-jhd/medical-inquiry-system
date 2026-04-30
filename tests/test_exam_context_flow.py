"""测试检查上下文动作在 service 层的状态更新、追问分流和阶段性结束。"""

from brain.action_builder import ActionBuilder
from brain.evidence_parser import EvidenceParser
from brain.hypothesis_manager import HypothesisManager
from brain.report_builder import ReportBuilder
from brain.service import BrainDependencies, ConsultationBrain
from brain.state_tracker import StateTracker
from brain.types import ExamContextState, HypothesisScore, MctsAction, PatientContext


class ExamFlowRetriever:
    """提供最小 R2 返回，避免测试依赖真实 Neo4j。"""

    client = object()

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []

    def retrieve_r2_expected_evidence(
        self,
        hypothesis: HypothesisScore,
        session_state: object,
        top_k: int | None = None,
    ) -> list[dict]:
        _ = hypothesis, session_state, top_k
        return list(self.rows)

    def get_cold_start_questions(self) -> list:
        return []


class FakeExamContextLlmClient:
    """按测试场景返回固定 exam_context 结构化结果。"""

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def is_available(self) -> bool:
        return True

    def run_structured_prompt(self, prompt_name: str, variables: dict, schema):
        _ = variables, schema
        assert prompt_name == "exam_context_interpretation"
        return dict(self.payload)


def _build_brain(
    tracker: StateTracker,
    rows: list[dict] | None = None,
    llm_client: object | None = None,
) -> ConsultationBrain:
    return ConsultationBrain(
        BrainDependencies(
            state_tracker=tracker,
            retriever=ExamFlowRetriever(rows),
            med_extractor=object(),
            entity_linker=object(),
            question_selector=object(),
            stop_rule_engine=object(),
            report_builder=ReportBuilder(),
            evidence_parser=EvidenceParser(llm_client=llm_client),
            hypothesis_manager=HypothesisManager(),
            action_builder=ActionBuilder(),
            router=object(),
            mcts_engine=object(),
            simulation_engine=object(),
            trajectory_evaluator=object(),
            llm_client=object(),
        )
    )


def _collect_lab_action() -> MctsAction:
    return MctsAction(
        action_id="collect_exam::pcp::general",
        action_type="collect_general_exam_context",
        target_node_id="__exam_context__::general",
        target_node_label="ExamContext",
        target_node_name="医院检查情况",
        hypothesis_id="pcp",
        topic_id="Disease",
        prior_score=2.0,
        metadata={
            "exam_kind": "general",
            "question_type_hint": "exam_context",
            "candidate_exam_kinds": ["lab", "imaging", "pathogen"],
            "exam_candidate_evidence": [
                {
                    "node_id": "lab_bdg_high",
                    "label": "LabFinding",
                    "name": "(1,3)-β-D-葡聚糖检测升高",
                    "priority": 3.0,
                    "contradiction_priority": 0.7,
                    "discriminative_gain": 0.6,
                    "recommended_match_score": 0.2,
                    "acquisition_mode": "needs_lab_test",
                    "evidence_cost": "high",
                    "exam_kind": "lab",
                    "question_type_hint": "lab",
                },
                {
                    "node_id": "lab_cd4_low",
                    "label": "LabFinding",
                    "name": "CD4+ T淋巴细胞计数 < 200/μL",
                    "priority": 2.0,
                    "contradiction_priority": 0.9,
                    "discriminative_gain": 1.1,
                    "recommended_match_score": 0.9,
                    "joint_recommended_match_score": 0.8,
                    "recommended_evidence_bonus": 0.4,
                    "acquisition_mode": "needs_lab_test",
                    "evidence_cost": "high",
                    "exam_kind": "lab",
                    "question_type_hint": "lab",
                },
                {
                    "node_id": "ct_ground_glass",
                    "label": "ImagingFinding",
                    "name": "胸部CT磨玻璃影",
                    "priority": 2.4,
                    "contradiction_priority": 0.8,
                    "discriminative_gain": 0.7,
                    "recommended_match_score": 0.3,
                    "acquisition_mode": "needs_imaging",
                    "evidence_cost": "high",
                    "exam_kind": "imaging",
                    "question_type_hint": "imaging",
                },
            ],
        },
    )


# 患者做过且给出结果时，应直接写入对应槽位和检查上下文状态。
def test_service_exam_context_done_with_result_updates_slot_and_state() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s_exam_result")
    state.candidate_hypotheses = [HypothesisScore(node_id="pcp", label="Disease", name="PCP", score=1.0)]
    tracker.set_pending_action("s_exam_result", _collect_lab_action())
    brain = _build_brain(
        tracker,
        llm_client=FakeExamContextLlmClient(
            {
                "availability": "done",
                "mentioned_tests": ["CD4"],
                "mentioned_results": [
                    {
                        "test_name": "CD4",
                        "raw_text": "CD4 150",
                        "normalized_result": "low",
                    }
                ],
                "needs_followup": False,
                "followup_reason": "",
                "reasoning": "患者说做过 CD4，且数值提示偏低。",
            }
        ),
    )

    a4_result, _, route_after_a4, updates = brain.update_from_pending_action(
        "s_exam_result",
        PatientContext(raw_text="做过 CD4，结果 150。"),
        "做过 CD4，结果 150。",
        turn_index=2,
    )

    state = tracker.get_session("s_exam_result")
    assert a4_result is not None
    assert a4_result.existence == "exist"
    assert route_after_a4.stage == "A3"
    assert state.exam_context["general"].availability == "done"
    assert state.exam_context["lab"].availability == "done"
    assert len(updates) == 1
    assert updates[0].node_id == "lab_cd4_low"
    assert state.slots["lab_cd4_low"].status == "true"


# 患者做过且只给出检查名时，应优先生成具体结果追问，而不是泛化澄清。
def test_service_exam_context_with_test_name_builds_specific_result_followup() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s_exam_followup")
    state.candidate_hypotheses = [HypothesisScore(node_id="pcp", label="Disease", name="PCP", score=1.0)]
    tracker.set_pending_action("s_exam_followup", _collect_lab_action())
    brain = _build_brain(
        tracker,
        llm_client=FakeExamContextLlmClient(
            {
                "availability": "done",
                "mentioned_tests": ["CD4"],
                "mentioned_results": [],
                "needs_followup": True,
                "followup_reason": "患者只提到做过 CD4，但没有提供具体结果。",
                "reasoning": "需要继续追问 CD4 的具体数值。",
            }
        ),
    )

    brain.update_from_pending_action(
        "s_exam_followup",
        PatientContext(raw_text="做过 CD4，但具体数值不记得了。"),
        "做过 CD4，但具体数值不记得了。",
        turn_index=2,
    )

    followup_action = tracker.get_session("s_exam_followup").metadata["exam_context_followup_action"]
    assert followup_action.action_type == "verify_evidence"
    assert followup_action.target_node_id == "lab_cd4_low"
    assert followup_action.metadata["exam_followup_mode"] == "specific_result"
    assert "CD4" in followup_action.metadata["question_text"]
    assert followup_action.metadata["exam_kind"] == "lab"
    assert followup_action.metadata["source_exam_kind"] == "general"


# 患者明确没做过检查时，应记录 not_done，且不生成具体结果追问。
def test_service_exam_context_not_done_records_state_without_followup() -> None:
    tracker = StateTracker()
    tracker.create_session("s_exam_not_done")
    tracker.set_pending_action("s_exam_not_done", _collect_lab_action())
    brain = _build_brain(
        tracker,
        llm_client=FakeExamContextLlmClient(
            {
                "availability": "not_done",
                "mentioned_tests": [],
                "mentioned_results": [],
                "needs_followup": False,
                "followup_reason": "",
                "reasoning": "患者明确表示最近没有做过相关检查。",
            }
        ),
    )

    a4_result, _, _, updates = brain.update_from_pending_action(
        "s_exam_not_done",
        PatientContext(raw_text="最近没有做过这些化验。"),
        "最近没有做过这些化验。",
        turn_index=2,
    )

    state = tracker.get_session("s_exam_not_done")
    assert a4_result is not None
    assert a4_result.existence == "non_exist"
    assert updates == []
    assert state.exam_context["general"].availability == "not_done"
    assert state.exam_context["lab"].availability == "not_done"
    assert state.exam_context["imaging"].availability == "not_done"
    assert state.exam_context["pathogen"].availability == "not_done"
    assert "exam_context_followup_action" not in state.metadata


# 当检查未做且 R2 中没有剩余低成本证据时，service 给出显式阶段性结束原因。
def test_service_stage_stop_when_exam_not_done_and_no_low_cost_questions() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s_stage_stop")
    state.exam_context["general"] = ExamContextState(exam_kind="general", availability="not_done")
    state.candidate_hypotheses = [HypothesisScore(node_id="pcp", label="Disease", name="PCP", score=1.0)]
    brain = _build_brain(
        tracker,
        rows=[
            {
                "node_id": "lab_cd4_low",
                "label": "LabFinding",
                "name": "CD4+ T淋巴细胞计数 < 200/μL",
                "acquisition_mode": "needs_lab_test",
                "evidence_cost": "high",
            }
        ],
    )

    decision = brain._build_exam_limited_stage_stop_decision("s_stage_stop")

    assert decision is not None
    assert decision.should_stop is True
    assert decision.reason == "no_exam_and_no_low_cost_questions"
    assert decision.metadata["stage_end_reason"] == "insufficient_observable_evidence"


# 若仍有未问过的低成本症状/风险证据，则不应阶段性结束。
def test_service_stage_stop_keeps_asking_when_low_cost_question_exists() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s_stage_continue")
    state.exam_context["lab"] = ExamContextState(exam_kind="lab", availability="not_done")
    state.candidate_hypotheses = [HypothesisScore(node_id="pcp", label="Disease", name="PCP", score=1.0)]
    brain = _build_brain(
        tracker,
        rows=[
            {
                "node_id": "symptom_dry_cough",
                "label": "ClinicalFinding",
                "name": "干咳",
                "acquisition_mode": "direct_ask",
                "evidence_cost": "low",
            }
        ],
    )

    assert brain._build_exam_limited_stage_stop_decision("s_stage_continue") is None
