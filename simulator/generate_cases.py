"""保留旧导入路径的种子病例与 IO 兼容壳。"""

from __future__ import annotations

from .cases.io import load_cases_jsonl, write_cases_json, write_cases_jsonl
from .cases.seed_cases import build_seed_cases

__all__ = [
    "build_seed_cases",
    "load_cases_jsonl",
    "write_cases_json",
    "write_cases_jsonl",
]
