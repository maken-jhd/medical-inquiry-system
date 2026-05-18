"""封装病例 schema、seed cases、IO 与图谱病例生成入口。"""

from .graph_generator import (
    CASE_QC_ELIGIBLE,
    CASE_QC_NOT_BENCHMARK_ELIGIBLE,
    CASE_QC_WEAK_ANCHOR,
    GraphCaseGenerationResult,
    GraphCaseGenerator,
    GraphCaseGeneratorConfig,
    build_case_type_sample_payload,
    sample_cases_by_type,
)
from .io import load_cases_jsonl, write_cases_json, write_cases_jsonl
from .schema import BehaviorStyle, SlotTruth, VirtualPatientCase
from .seed_cases import build_seed_cases

__all__ = [
    "BehaviorStyle",
    "CASE_QC_ELIGIBLE",
    "CASE_QC_NOT_BENCHMARK_ELIGIBLE",
    "CASE_QC_WEAK_ANCHOR",
    "GraphCaseGenerationResult",
    "GraphCaseGenerator",
    "GraphCaseGeneratorConfig",
    "SlotTruth",
    "VirtualPatientCase",
    "build_case_type_sample_payload",
    "build_seed_cases",
    "load_cases_jsonl",
    "sample_cases_by_type",
    "write_cases_json",
    "write_cases_jsonl",
]
