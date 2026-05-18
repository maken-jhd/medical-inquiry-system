"""搜索、候选动作选择与轨迹聚合协调器。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..state import MctsAction, PatientContext, SearchResult

if TYPE_CHECKING:
    from ..app.brain import BrainRuntime


class SearchCoordinator:
    """负责驱动 A2/A3 搜索与下一问选择。"""

    def __init__(self, runtime: "BrainRuntime") -> None:
        # 搜索协调器只包一层稳定入口，便于把 facade 和真实实现解耦。
        self.runtime = runtime

    def run_reasoning_search(self, session_id: str, patient_context: PatientContext) -> SearchResult:
        # 搜索协调器保持轻薄，只负责把 A2/A3 搜索入口委托给运行时实现。
        return self.runtime._run_reasoning_search_impl(session_id, patient_context)

    def choose_next_question_from_search(self, session_id: str, search_result: SearchResult) -> MctsAction | None:
        # 下一问选择与搜索结果绑定，默认仍由运行时根据 selected_action / fallback 统一收口。
        return self.runtime._choose_next_question_from_search_impl(session_id, search_result)
