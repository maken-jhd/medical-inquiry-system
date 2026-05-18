"""终止接受、拒停修补与最终报告协调器。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..state import SearchResult

if TYPE_CHECKING:
    from ..app.brain import BrainRuntime


class AcceptanceCoordinator:
    """负责最终接受判定与拒停后的结果收口。"""

    def __init__(self, runtime: "BrainRuntime") -> None:
        # acceptance 层不维护独立状态，只委托 runtime 使用当前 session_state 做最终收口。
        self.runtime = runtime

    def finalize(self, session_id: str) -> dict:
        # 兼容旧 finalize 入口，优先复用当前会话里缓存的最后一次 search 结果。
        return self.runtime.finalize(session_id)

    def finalize_from_search(self, session_id: str, search_result: SearchResult) -> dict:
        # 主链显式持有 search_result 时，直接走 search-based final report 收口。
        return self.runtime._finalize_from_search_impl(session_id, search_result)
