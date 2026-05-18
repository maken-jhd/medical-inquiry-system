"""封装 benchmark 摘要、cohort 报告与 replay 分析报表。"""

from .metrics import benchmark_summary_to_payload, summarize_benchmark
from .reports import (
    build_benchmark_cohort_summary,
    build_non_completed_case_report,
    build_replay_analysis_summary,
)
from .types import BenchmarkSummary

__all__ = [
    "BenchmarkSummary",
    "benchmark_summary_to_payload",
    "build_benchmark_cohort_summary",
    "build_non_completed_case_report",
    "build_replay_analysis_summary",
    "summarize_benchmark",
]
