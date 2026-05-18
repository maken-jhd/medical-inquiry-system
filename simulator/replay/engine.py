"""提供稳定的 replay 公共门面。"""

from __future__ import annotations

from time import perf_counter
from typing import Any

from brain.service import ConsultationBrain

from ..patient.agent import VirtualPatientAgent
from .runtime import ReplayRuntime
from .types import ReplayConfig


class ReplayEngine:
    """对外暴露稳定接口的自动回放门面。"""

    def __init__(
        self,
        brain: ConsultationBrain,
        patient_agent: VirtualPatientAgent,
        config: ReplayConfig | None = None,
        *,
        perf_counter_fn=perf_counter,
    ) -> None:
        # 门面只负责装配 runtime；真实 run_case 主链放到 replay.runtime。
        self.runtime = ReplayRuntime(
            brain=brain,
            patient_agent=patient_agent,
            config=config,
            perf_counter_fn=perf_counter_fn,
        )
        self.brain = self.runtime.brain
        self.patient_agent = self.runtime.patient_agent
        self.config = self.runtime.config

    def __getattr__(self, name: str) -> Any:
        return getattr(self.runtime, name)

    def run_case(self, case):
        # 稳定入口：运行单个病例回放。
        return self.runtime.run_case(case)

    def run_cases(self, cases):
        # 稳定入口：批量运行多个病例回放。
        return self.runtime.run_cases(cases)
