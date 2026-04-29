"""根据疾病级图谱审计输出生成图谱驱动虚拟病人病例。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from simulator.graph_case_generator import (  # noqa: E402
    GraphCaseGenerator,
    GraphCaseGeneratorConfig,
    write_graph_case_outputs,
)


DEFAULT_AUDIT_ROOT = (
    PROJECT_ROOT / "test_outputs" / "graph_audit" / "all_diseases_20260420_disease_aliases_only"
)
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "test_outputs" / "simulator_cases" / "graph_cases_20260426_final"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="根据疾病图谱审计结果生成图谱驱动虚拟病人病例。")
    parser.add_argument(
        "--audit-root",
        default=str(DEFAULT_AUDIT_ROOT),
        help="疾病级图谱审计输出目录。",
    )
    parser.add_argument(
        "--output-file",
        default=str(DEFAULT_OUTPUT_ROOT / "cases.jsonl"),
        help="输出病例 JSONL 文件。",
    )
    parser.add_argument(
        "--output-json-file",
        default=str(DEFAULT_OUTPUT_ROOT / "cases.json"),
        help="输出病例 JSON 数组文件，便于人工查看。",
    )
    parser.add_argument(
        "--manifest-file",
        default=str(DEFAULT_OUTPUT_ROOT / "manifest.json"),
        help="输出 manifest JSON 文件。",
    )
    parser.add_argument(
        "--summary-file",
        default=str(DEFAULT_OUTPUT_ROOT / "summary.md"),
        help="输出 Markdown 摘要文件。",
    )
    parser.add_argument("--ordinary-min-total-pool", type=int, default=4, help="ordinary 最小总证据池。")
    parser.add_argument("--ordinary-min-chief-pool", type=int, default=2, help="ordinary 最小 chief-friendly 池。")
    parser.add_argument("--low-cost-min-pool", type=int, default=4, help="low_cost 最小低成本证据池。")
    parser.add_argument("--exam-driven-min-exam-pool", type=int, default=3, help="exam_driven 最小检查池。")
    parser.add_argument(
        "--exam-driven-min-high-value-pool",
        type=int,
        default=2,
        help="exam_driven 最小高价值检查池。",
    )
    parser.add_argument(
        "--competitive-min-shared-pool",
        type=int,
        default=2,
        help="competitive 最小 shared_low_cost 池。",
    )
    parser.add_argument(
        "--competitive-min-target-only-pool",
        type=int,
        default=2,
        help="competitive 最小 target_only_discriminative 池。",
    )
    parser.add_argument(
        "--competitive-min-competitor-negative-pool",
        type=int,
        default=1,
        help="competitive 最小 competitor_only_negative 池。",
    )
    parser.add_argument(
        "--max-competitors-per-disease",
        type=int,
        default=1,
        help="每个疾病最多生成多少个竞争病例。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = GraphCaseGeneratorConfig(
        ordinary_min_total_pool=args.ordinary_min_total_pool,
        ordinary_min_chief_pool=args.ordinary_min_chief_pool,
        low_cost_min_pool=args.low_cost_min_pool,
        exam_driven_min_exam_pool=args.exam_driven_min_exam_pool,
        exam_driven_min_high_value_pool=args.exam_driven_min_high_value_pool,
        competitive_min_shared_pool=args.competitive_min_shared_pool,
        competitive_min_target_only_pool=args.competitive_min_target_only_pool,
        competitive_min_competitor_negative_pool=args.competitive_min_competitor_negative_pool,
        max_competitors_per_disease=args.max_competitors_per_disease,
    )
    generator = GraphCaseGenerator(config=config)
    audit_root = Path(args.audit_root).resolve()
    output_file = Path(args.output_file).resolve()
    output_json_file = Path(args.output_json_file).resolve()
    manifest_file = Path(args.manifest_file).resolve()
    summary_file = Path(args.summary_file).resolve()

    if not audit_root.exists():
        raise SystemExit(f"审计目录不存在：{audit_root}")

    result = generator.generate_from_audit_root(audit_root)
    write_graph_case_outputs(
        result,
        output_file=output_file,
        manifest_file=manifest_file,
        output_json_file=output_json_file,
        summary_file=summary_file,
    )
    payload = {
        "status": "ok",
        "audit_root": str(audit_root),
        "output_file": str(output_file),
        "output_json_file": str(output_json_file),
        "manifest_file": str(manifest_file),
        "summary_file": str(summary_file),
        "generated_case_count": result.manifest["generated_case_count"],
        "generated_case_count_by_type": result.manifest["generated_case_count_by_type"],
        "skipped_case_count_by_reason": result.manifest["skipped_case_count_by_reason"],
        "valid_disease_report_count": result.manifest["valid_disease_report_count"],
        "invalid_disease_report_count": result.manifest["invalid_disease_report_count"],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
