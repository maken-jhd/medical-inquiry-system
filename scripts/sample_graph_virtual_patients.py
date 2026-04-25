"""按病例类型抽样图谱驱动虚拟病人，便于人工检查。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from simulator.generate_cases import load_cases_jsonl  # noqa: E402
from simulator.graph_case_generator import (  # noqa: E402
    build_case_type_sample_payload,
    write_case_type_sample_markdown,
    write_case_type_sample_payload,
)


DEFAULT_CASES_FILE = PROJECT_ROOT / "test_outputs" / "simulator_cases" / "graph_cases_20260421" / "cases.json"
DEFAULT_OUTPUT_FILE = (
    PROJECT_ROOT / "test_outputs" / "simulator_cases" / "graph_cases_20260421" / "sampled_cases_4x5.json"
)
DEFAULT_SUMMARY_FILE = (
    PROJECT_ROOT / "test_outputs" / "simulator_cases" / "graph_cases_20260421" / "sampled_cases_4x5.md"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按病例类型从图谱驱动病例中抽样，便于人工检查。")
    parser.add_argument(
        "--cases-file",
        default=str(DEFAULT_CASES_FILE),
        help="输入病例文件，支持 JSONL 或 JSON 数组。",
    )
    parser.add_argument(
        "--output-file",
        default=str(DEFAULT_OUTPUT_FILE),
        help="输出抽样结果 JSON 文件。",
    )
    parser.add_argument(
        "--summary-file",
        default=str(DEFAULT_SUMMARY_FILE),
        help="输出抽样结果 Markdown 摘要文件。",
    )
    parser.add_argument(
        "--sample-size-per-type",
        type=int,
        default=5,
        help="每个病例类型抽样多少条。",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="随机种子，保证抽样可复现。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cases_file = Path(args.cases_file).resolve()
    output_file = Path(args.output_file).resolve()
    summary_file = Path(args.summary_file).resolve()

    if not cases_file.exists():
        raise SystemExit(f"病例文件不存在：{cases_file}")

    cases = load_cases_jsonl(cases_file)
    payload = build_case_type_sample_payload(
        cases,
        sample_size_per_type=args.sample_size_per_type,
        seed=args.seed,
        source_file=cases_file,
    )
    write_case_type_sample_payload(payload, output_file)
    write_case_type_sample_markdown(payload, summary_file)
    print(
        json.dumps(
            {
                "status": "ok",
                "cases_file": str(cases_file),
                "output_file": str(output_file),
                "summary_file": str(summary_file),
                "sample_size_per_type": args.sample_size_per_type,
                "seed": args.seed,
                "sampled_case_count": payload["sampled_case_count"],
                "available_case_count_by_type": payload["available_case_count_by_type"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
