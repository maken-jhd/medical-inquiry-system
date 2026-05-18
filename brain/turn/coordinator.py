"""单轮解释与入口编排协调器。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app.brain import BrainRuntime


class TurnCoordinator:
    """负责驱动单轮问诊的统一入口。"""

    def __init__(self, runtime: "BrainRuntime") -> None:
        # 协调器自身不持有业务状态，只保留对统一 runtime 的引用。
        self.runtime = runtime

    def process_turn(self, session_id: str, patient_text: str) -> dict:
        # TurnCoordinator 只负责稳定入口转发，真正的单轮编排都在 BrainRuntime 内部完成。
        return self.runtime._process_turn_impl(session_id, patient_text)
