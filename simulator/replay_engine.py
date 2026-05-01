"""驱动问诊大脑与虚拟病人自动对战并记录回放结果。"""

from __future__ import annotations

import json
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Iterable, List, Optional

from brain.errors import BrainDomainError
from brain.service import ConsultationBrain

from .case_schema import VirtualPatientCase
from .patient_agent import VirtualPatientAgent


@dataclass
class ReplayTurn:
    """表示自动对战中的单轮问答记录。"""

    question_node_id: str
    question_text: str
    answer_text: str
    turn_index: int
    revealed_slot_id: Optional[str] = None
    stage: str = "A3"
    patient_answer_seconds: float = 0.0
    brain_turn_seconds: float = 0.0
    total_seconds: float = 0.0


@dataclass
class ReplayResult:
    """表示单个病例自动对战完成后的回放结果。"""

    case_id: str
    case_title: str = ""
    opening_text: str = ""
    true_conditions: List[str] = field(default_factory=list)
    true_disease_phase: Optional[str] = None
    red_flags: List[str] = field(default_factory=list)
    turns: List[ReplayTurn] = field(default_factory=list)
    final_report: dict = field(default_factory=dict)
    initial_output: dict = field(default_factory=dict)
    status: str = "pending"
    timing: dict = field(default_factory=dict)
    error: dict = field(default_factory=dict)


@dataclass
class ReplayConfig:
    """保存自动回放的基础参数。"""

    max_turns: int = 8


class ReplayEngine:
    """协调问诊大脑与虚拟病人完成自动回放。"""

    # 初始化回放引擎并注入问诊大脑与病人代理。
    def __init__(
        self,
        brain: ConsultationBrain,
        patient_agent: VirtualPatientAgent,
        config: ReplayConfig | None = None,
    ) -> None:
        self.brain = brain
        self.patient_agent = patient_agent
        self.config = config or ReplayConfig()

    # 运行单个病例的自动对战并返回回放结果。
    def run_case(self, case: VirtualPatientCase) -> ReplayResult:
        session_id = f"replay::{case.case_id}"
        started_at = self._now_iso()
        case_started = perf_counter()
        result = ReplayResult(
            case_id=case.case_id,
            case_title=case.title,
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
        try:
            self.brain.start_session(session_id)
            opening_started = perf_counter()
            opening = self.patient_agent.open_case(case)
            result.timing["opening_seconds"] = perf_counter() - opening_started
            result.opening_text = opening.opening_text
            initial_brain_started = perf_counter()
            current_output = self.brain.process_turn(session_id, opening.opening_text)
            result.timing["initial_brain_seconds"] = perf_counter() - initial_brain_started
            result.initial_output = current_output

            if current_output.get("final_report") is not None:
                result.final_report = current_output["final_report"]
                result.status = "completed"
                self._finalize_timing(result, case_started)
                return result

            for turn_index in range(1, self.config.max_turns + 1):
                question_text = str(current_output.get("next_question") or "")
                pending_action = current_output.get("pending_action") or {}
                question_node_id = str(pending_action.get("target_node_id") or "")

                if len(question_text) == 0 or len(question_node_id) == 0:
                    break

                answer_started = perf_counter()
                reply = self.patient_agent.answer_question(question_node_id, question_text, case)
                patient_answer_seconds = perf_counter() - answer_started
                brain_turn_started = perf_counter()
                current_output = self.brain.process_turn(session_id, reply.answer_text)
                brain_turn_seconds = perf_counter() - brain_turn_started
                turn_total_seconds = patient_answer_seconds + brain_turn_seconds
                result.turns.append(
                    ReplayTurn(
                        question_node_id=question_node_id,
                        question_text=question_text,
                        answer_text=reply.answer_text,
                        turn_index=turn_index,
                        revealed_slot_id=reply.revealed_slot_id,
                        patient_answer_seconds=round(patient_answer_seconds, 4),
                        brain_turn_seconds=round(brain_turn_seconds, 4),
                        total_seconds=round(turn_total_seconds, 4),
                    )
                )
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

                if current_output.get("final_report") is not None:
                    result.final_report = current_output["final_report"]
                    result.status = "completed"
                    self._finalize_timing(result, case_started)
                    return result

            finalize_started = perf_counter()
            result.final_report = self.brain.finalize(session_id)
            result.timing["finalize_seconds"] = perf_counter() - finalize_started
            result.status = "max_turn_reached"
        except BrainDomainError as exc:
            result.status = "failed"
            result.error = exc.to_dict()
            result.final_report = {}
        except Exception as exc:
            # 普通 Python 异常也按单病例失败落盘，避免直接中断整批 replay。
            result.status = "failed"
            result.error = self._build_unexpected_error_payload(exc)
            result.final_report = {}
        self._finalize_timing(result, case_started)
        return result

    # 批量运行多个病例的自动对战。
    def run_cases(self, cases: Iterable[VirtualPatientCase]) -> List[ReplayResult]:
        return [self.run_case(case) for case in cases]

    def _finalize_timing(self, result: ReplayResult, case_started: float) -> None:
        result.timing["finished_at"] = self._now_iso()
        result.timing["total_seconds"] = perf_counter() - case_started
        result.timing["turn_count"] = len(result.turns)
        self._round_timing_fields(result)

    def _round_timing_fields(self, result: ReplayResult) -> None:
        for key in (
            "opening_seconds",
            "initial_brain_seconds",
            "patient_answer_seconds_total",
            "brain_turn_seconds_total",
            "finalize_seconds",
            "total_seconds",
            "max_patient_answer_seconds",
            "max_brain_turn_seconds",
            "slowest_turn_total_seconds",
        ):
            result.timing[key] = round(float(result.timing.get(key, 0.0) or 0.0), 4)

    def _now_iso(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _build_unexpected_error_payload(self, exc: Exception) -> dict:
        return {
            "code": "unexpected_runtime_error",
            "stage": "replay_engine",
            "prompt_name": "",
            "message": f"{type(exc).__name__}: {exc}",
            "attempts": 1,
            "error_type": type(exc).__name__,
        }


# 将批量回放结果写入 JSONL，便于后续复盘分析。
def write_replay_results_jsonl(results: Iterable[ReplayResult], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with output_file.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result, ensure_ascii=False, default=lambda obj: obj.__dict__) + "\n")
