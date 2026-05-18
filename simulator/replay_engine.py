"""保留旧导入路径的 replay 兼容壳。"""

from __future__ import annotations

from time import perf_counter

from .replay.analysis import (
    STANDARD_ANALYSIS_GROUPS,
    STANDARD_COST_BUCKETS,
    STANDARD_QUESTION_GROUPS,
    extract_case_benchmark_fields,
)
from .replay.engine import ReplayEngine as _ReplayEngine
from .replay.io import write_replay_results_jsonl
from .replay.types import ReplayConfig, ReplayResult, ReplayTurn


class ReplayEngine(_ReplayEngine):
    """兼容旧路径的回放门面，同时保留 perf_counter monkeypatch 能力。"""

    def __init__(self, brain, patient_agent, config: ReplayConfig | None = None) -> None:
        # 测试会 monkeypatch 当前模块的 perf_counter；这里在构造时显式透传。
        super().__init__(brain, patient_agent, config=config, perf_counter_fn=perf_counter)


__all__ = [
    "ReplayConfig",
    "ReplayEngine",
    "ReplayResult",
    "ReplayTurn",
    "STANDARD_ANALYSIS_GROUPS",
    "STANDARD_COST_BUCKETS",
    "STANDARD_QUESTION_GROUPS",
    "extract_case_benchmark_fields",
    "write_replay_results_jsonl",
]
