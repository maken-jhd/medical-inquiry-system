"""编排自动回放单病例运行链。 """

from __future__ import annotations

from time import perf_counter
from typing import Any, Callable, Iterable

from brain.shared import BrainDomainError
from brain.service import ConsultationBrain

from ..cases.schema import VirtualPatientCase
from ..patient.agent import VirtualPatientAgent
from .analysis import (
    build_case_analysis,
    build_turn_observation,
    build_unexpected_error_payload,
    extract_case_benchmark_fields,
    extract_turn_search_metadata,
    extract_turn_search_report,
    now_iso,
    round_timing_fields,
)
from .types import ReplayConfig, ReplayResult, ReplayTurn


class ReplayRuntime:
    """执行单病例 replay 的真实运行时。"""

    # 初始化 runtime，并注入 clock 函数方便测试稳定控制耗时。
    def __init__(
        self,
        brain: ConsultationBrain,
        patient_agent: VirtualPatientAgent,
        config: ReplayConfig | None = None,
        *,
        perf_counter_fn: Callable[[], float] = perf_counter,
    ) -> None:
        self.brain = brain
        self.patient_agent = patient_agent
        self.config = config or ReplayConfig()
        self.perf_counter = perf_counter_fn

    # 运行单个病例的自动对战并返回完整回放结果。
    def run_case(self, case: VirtualPatientCase) -> ReplayResult:
        session_id = f"replay::{case.case_id}"
        started_at = now_iso()
        case_started = self.perf_counter()
        case_benchmark_fields = extract_case_benchmark_fields(case)
        result = self._build_pending_result(case, case_benchmark_fields, started_at)

        try:
            self._start_case_session(session_id)
            current_output = self._run_opening(session_id, case, result)

            # 如果 opening 后 brain 已直接给出最终报告，立即收口。
            if current_output.get("final_report") is not None:
                return self._complete_result(result, case, case_started, current_output["final_report"], "completed")

            # 正式进入问答循环：取 next_question -> 虚拟病人回答 -> brain 消化 -> 记录 turn。
            for turn_index in range(1, self.config.max_turns + 1):
                question_text = str(current_output.get("next_question") or "")
                pending_action = current_output.get("pending_action") or {}
                question_node_id = str(pending_action.get("target_node_id") or "")

                if len(question_text) == 0 or len(question_node_id) == 0:
                    break

                current_output = self._run_single_turn(
                    session_id=session_id,
                    case=case,
                    result=result,
                    current_output=current_output,
                    pending_action=pending_action,
                    question_node_id=question_node_id,
                    question_text=question_text,
                    turn_index=turn_index,
                )

                if current_output.get("final_report") is not None:
                    return self._complete_result(
                        result,
                        case,
                        case_started,
                        current_output["final_report"],
                        "completed",
                    )

            # 到达最大轮数或没有下一问时，仍然调用 finalize 走统一收尾。
            finalize_started = self.perf_counter()
            result.final_report = self.brain.finalize(session_id)
            result.timing["finalize_seconds"] = self.perf_counter() - finalize_started
            result.status = "max_turn_reached"
        except BrainDomainError as exc:
            result.status = "failed"
            result.error = exc.to_dict()
            result.final_report = {}
        except Exception as exc:
            # 普通 Python 异常也按单病例失败落盘，避免整批 replay 被一个病例打断。
            result.status = "failed"
            result.error = build_unexpected_error_payload(exc)
            result.final_report = {}

        result.analysis = build_case_analysis(case, result)
        self._finalize_timing(result, case_started)
        return result

    # 批量运行多个病例的自动对战。
    def run_cases(self, cases: Iterable[VirtualPatientCase]) -> list[ReplayResult]:
        return [self.run_case(case) for case in cases]

    def _build_pending_result(
        self,
        case: VirtualPatientCase,
        case_benchmark_fields: dict[str, Any],
        started_at: str,
    ) -> ReplayResult:
        return ReplayResult(
            case_id=case.case_id,
            case_title=case.title,
            case_type=case_benchmark_fields["case_type"],
            case_qc_status=case_benchmark_fields["case_qc_status"],
            benchmark_qc_status=case_benchmark_fields["benchmark_qc_status"],
            case_qc_reasons=case_benchmark_fields["case_qc_reasons"],
            true_conditions=list(case.true_conditions),
            true_disease_phase=case.true_disease_phase,
            red_flags=list(case.red_flags),
            timing={
                "started_at": started_at,
                "finished_at": "",
                "opening_seconds": 0.0,
                "initial_brain_seconds": 0.0,
                "patient_answer_seconds_total": 0.0,
                "brain_turn_seconds_total": 0.0,
                "finalize_seconds": 0.0,
                "total_seconds": 0.0,
                "max_patient_answer_seconds": 0.0,
                "max_brain_turn_seconds": 0.0,
                "slowest_turn_index": 0,
                "slowest_turn_total_seconds": 0.0,
            },
        )

    def _start_case_session(self, session_id: str) -> None:
        self.brain.start_session(session_id)

    def _run_opening(
        self,
        session_id: str,
        case: VirtualPatientCase,
        result: ReplayResult,
    ) -> dict[str, Any]:
        # 先由虚拟病人生成 opening，再作为首轮输入交给 brain 建立初始状态。
        opening_started = self.perf_counter()
        opening = self.patient_agent.open_case(case)
        result.timing["opening_seconds"] = self.perf_counter() - opening_started
        result.opening_text = opening.opening_text
        result.opening_revealed_slot_ids = list(getattr(opening, "revealed_slot_ids", []) or [])

        initial_brain_started = self.perf_counter()
        current_output = self.brain.process_turn(session_id, opening.opening_text)
        result.timing["initial_brain_seconds"] = self.perf_counter() - initial_brain_started
        result.initial_output = current_output
        return current_output

    def _run_single_turn(
        self,
        *,
        session_id: str,
        case: VirtualPatientCase,
        result: ReplayResult,
        current_output: dict[str, Any],
        pending_action: dict[str, Any],
        question_node_id: str,
        question_text: str,
        turn_index: int,
    ) -> dict[str, Any]:
        # 病人先回答这轮问题，再让 brain 消化回答并产出下一步动作。
        answer_started = self.perf_counter()
        reply = self.patient_agent.answer_question(question_node_id, question_text, case)
        patient_answer_seconds = self.perf_counter() - answer_started

        brain_turn_started = self.perf_counter()
        next_output = self.brain.process_turn(session_id, reply.answer_text)
        brain_turn_seconds = self.perf_counter() - brain_turn_started
        turn_total_seconds = patient_answer_seconds + brain_turn_seconds

        # 把 pending action 和真实命中信息压成稳定字段，供后续离线分析。
        turn_observation = build_turn_observation(
            pending_action=pending_action,
            reply=reply,
            case=case,
        )
        result.turns.append(
            ReplayTurn(
                question_node_id=question_node_id,
                question_text=question_text,
                answer_text=reply.answer_text,
                turn_index=turn_index,
                revealed_slot_id=reply.revealed_slot_id,
                search_report=extract_turn_search_report(next_output),
                search_metadata=extract_turn_search_metadata(next_output),
                asked_action_id=turn_observation["asked_action_id"],
                asked_action_type=turn_observation["asked_action_type"],
                asked_target_node_label=turn_observation["asked_target_node_label"],
                asked_target_node_name=turn_observation["asked_target_node_name"],
                asked_action_hypothesis_id=turn_observation["asked_action_hypothesis_id"],
                asked_action_group=turn_observation["asked_action_group"],
                asked_action_question_type_hint=turn_observation["asked_action_question_type_hint"],
                asked_action_acquisition_mode=turn_observation["asked_action_acquisition_mode"],
                asked_action_evidence_cost=turn_observation["asked_action_evidence_cost"],
                asked_action_selected_source=turn_observation["asked_action_selected_source"],
                asked_action_selected_source_priority_rank=turn_observation[
                    "asked_action_selected_source_priority_rank"
                ],
                truth_hit=turn_observation["truth_hit"],
                revealed_slot_group=turn_observation["revealed_slot_group"],
                revealed_slot_label=turn_observation["revealed_slot_label"],
                revealed_slot_name=turn_observation["revealed_slot_name"],
                revealed_slot_value=turn_observation["revealed_slot_value"],
                revealed_slot_positive=turn_observation["revealed_slot_positive"],
                revealed_slot_families=turn_observation["revealed_slot_families"],
                patient_answer_seconds=round(patient_answer_seconds, 4),
                brain_turn_seconds=round(brain_turn_seconds, 4),
                total_seconds=round(turn_total_seconds, 4),
            )
        )
        self._accumulate_turn_timing(
            result=result,
            patient_answer_seconds=patient_answer_seconds,
            brain_turn_seconds=brain_turn_seconds,
            turn_total_seconds=turn_total_seconds,
            turn_index=turn_index,
        )
        return next_output

    def _accumulate_turn_timing(
        self,
        *,
        result: ReplayResult,
        patient_answer_seconds: float,
        brain_turn_seconds: float,
        turn_total_seconds: float,
        turn_index: int,
    ) -> None:
        result.timing["patient_answer_seconds_total"] = (
            float(result.timing["patient_answer_seconds_total"]) + patient_answer_seconds
        )
        result.timing["brain_turn_seconds_total"] = (
            float(result.timing["brain_turn_seconds_total"]) + brain_turn_seconds
        )
        if patient_answer_seconds > float(result.timing["max_patient_answer_seconds"]):
            result.timing["max_patient_answer_seconds"] = patient_answer_seconds
        if brain_turn_seconds > float(result.timing["max_brain_turn_seconds"]):
            result.timing["max_brain_turn_seconds"] = brain_turn_seconds
        if turn_total_seconds > float(result.timing["slowest_turn_total_seconds"]):
            result.timing["slowest_turn_total_seconds"] = turn_total_seconds
            result.timing["slowest_turn_index"] = turn_index

    def _complete_result(
        self,
        result: ReplayResult,
        case: VirtualPatientCase,
        case_started: float,
        final_report: dict[str, Any],
        status: str,
    ) -> ReplayResult:
        result.final_report = final_report
        result.status = status
        result.analysis = build_case_analysis(case, result)
        self._finalize_timing(result, case_started)
        return result

    def _finalize_timing(self, result: ReplayResult, case_started: float) -> None:
        result.timing["finished_at"] = now_iso()
        result.timing["total_seconds"] = self.perf_counter() - case_started
        result.timing["turn_count"] = len(result.turns)
        round_timing_fields(result.timing)
