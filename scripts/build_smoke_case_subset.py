"""按病例类型做可复现抽样，生成 balanced smoke case 子集。"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from simulator.case_schema import VirtualPatientCase
from simulator.generate_cases import load_cases_jsonl, write_cases_jsonl


# 默认按当前图谱驱动病例的四类主分桶均衡抽样，便于做有代表性的 smoke 回归。
DEFAULT_CASE_TYPES = ("ordinary", "low_cost", "exam_driven", "competitive")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从全量病例中按病例类型均衡抽样生成 smoke 子集。")
    parser.add_argument(
        "--cases-file",
        required=True,
        help="输入病例 JSONL/JSON 文件。",
    )
    parser.add_argument(
        "--output-root",
        required=True,
        help="输出目录；会写入 cases.jsonl 和 sample_summary.json。",
    )
    parser.add_argument(
        "--per-case-type",
        type=int,
        default=15,
        help="每种 case_type 抽样多少例。",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260505,
        help="固定随机种子，保证抽样可复现。",
    )
    parser.add_argument(
        "--case-types",
        nargs="*",
        default=list(DEFAULT_CASE_TYPES),
        help="需要抽样的 case_type 列表，默认 ordinary/low_cost/exam_driven/competitive。",
    )
    return parser.parse_args()


def build_balanced_case_subset(
    cases: Iterable[VirtualPatientCase],
    *,
    case_types: list[str],
    per_case_type: int,
    seed: int,
) -> list[VirtualPatientCase]:
    grouped: dict[str, list[VirtualPatientCase]] = {case_type: [] for case_type in case_types}

    for case in cases:
        case_type = str(case.metadata.get("case_type") or "").strip()
        if case_type in grouped:
            grouped[case_type].append(case)

    rng = random.Random(seed)
    sampled: list[VirtualPatientCase] = []

    for case_type in case_types:
        candidates = list(grouped.get(case_type) or [])
        if len(candidates) < per_case_type:
            raise ValueError(
                f"case_type={case_type} 只有 {len(candidates)} 例，少于要求的 {per_case_type} 例。"
            )

        picked = rng.sample(candidates, per_case_type)
        sampled.extend(sorted(picked, key=lambda item: item.case_id))

    return sampled


def build_sample_summary(
    sampled_cases: list[VirtualPatientCase],
    *,
    cases_file: str,
    per_case_type: int,
    seed: int,
) -> dict:
    case_type_counter = Counter(str(case.metadata.get("case_type") or "").strip() for case in sampled_cases)
    return {
        "case_count": len(sampled_cases),
        "cases_file": cases_file,
        "per_case_type": int(per_case_type),
        "seed": int(seed),
        "case_type_counts": dict(sorted(case_type_counter.items(), key=lambda item: item[0])),
        "sampled_case_ids": [case.case_id for case in sampled_cases],
    }


def main() -> int:
    args = parse_args()
    cases_file = Path(args.cases_file)
    output_root = Path(args.output_root)
    case_types = [str(item).strip() for item in args.case_types if len(str(item).strip()) > 0]

    cases = load_cases_jsonl(cases_file)
    sampled_cases = build_balanced_case_subset(
        cases,
        case_types=case_types,
        per_case_type=max(int(args.per_case_type), 1),
        seed=int(args.seed),
    )

    output_root.mkdir(parents=True, exist_ok=True)
    write_cases_jsonl(sampled_cases, output_root / "cases.jsonl")
    (output_root / "sample_summary.json").write_text(
        json.dumps(
            build_sample_summary(
                sampled_cases,
                cases_file=str(cases_file),
                per_case_type=max(int(args.per_case_type), 1),
                seed=int(args.seed),
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
