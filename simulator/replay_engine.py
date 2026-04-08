"""驱动问诊大脑与虚拟病人自动对战并记录回放结果。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

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


@dataclass
class ReplayResult:
    """表示单个病例自动对战完成后的回放结果。"""

    case_id: str
    case_title: str = ""
    true_conditions: List[str] = field(default_factory=list)
    true_disease_phase: Optional[str] = None
    red_flags: List[str] = field(default_factory=list)
    turns: List[ReplayTurn] = field(default_factory=list)
    final_report: dict = field(default_factory=dict)
    initial_output: dict = field(default_factory=dict)
    status: str = "pending"


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
        self.brain.start_session(session_id)
        result = ReplayResult(
            case_id=case.case_id,
            case_title=case.title,
            true_conditions=list(case.true_conditions),
            true_disease_phase=case.true_disease_phase,
            red_flags=list(case.red_flags),
        )
        current_output = self.brain.process_turn(session_id, case.chief_complaint)
        result.initial_output = current_output

        if current_output.get("final_report") is not None:
            result.final_report = current_output["final_report"]
            result.status = "completed"
            return result

        for turn_index in range(1, self.config.max_turns + 1):
            question_text = str(current_output.get("next_question") or "")
            pending_action = current_output.get("pending_action") or {}
            question_node_id = str(pending_action.get("target_node_id") or "")

            if len(question_text) == 0 or len(question_node_id) == 0:
                break

            reply = self.patient_agent.answer_question(question_node_id, question_text, case)
            result.turns.append(
                ReplayTurn(
                    question_node_id=question_node_id,
                    question_text=question_text,
                    answer_text=reply.answer_text,
                    turn_index=turn_index,
                    revealed_slot_id=reply.revealed_slot_id,
                )
            )
            current_output = self.brain.process_turn(session_id, reply.answer_text)

            if current_output.get("final_report") is not None:
                result.final_report = current_output["final_report"]
                result.status = "completed"
                return result

        result.final_report = self.brain.finalize(session_id)
        result.status = "max_turn_reached"
        return result

    # 批量运行多个病例的自动对战。
    def run_cases(self, cases: Iterable[VirtualPatientCase]) -> List[ReplayResult]:
        return [self.run_case(case) for case in cases]


# 将批量回放结果写入 JSONL，便于后续复盘分析。
def write_replay_results_jsonl(results: Iterable[ReplayResult], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with output_file.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result, ensure_ascii=False, default=lambda obj: obj.__dict__) + "\n")
