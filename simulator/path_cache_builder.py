"""从回放结果中构建离线路径缓存。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable

from .replay_engine import ReplayResult


@dataclass
class PathCacheEntry:
    """表示某个状态签名下建议的下一问。"""

    state_signature: str
    best_next_question_node_id: str
    support_count: int


# 根据回放结果生成状态签名到下一问的缓存映射。
def build_path_cache(results: Iterable[ReplayResult]) -> Dict[str, PathCacheEntry]:
    # 后续在 replay 日志稳定后，用真实状态签名替换这个占位实现。
    _ = list(results)
    return {}
