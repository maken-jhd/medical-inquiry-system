"""从图谱驱动病例集中抽取可回放的 smoke 病例文件。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from simulator.generate_cases import load_cases_jsonl, write_cases_json, write_cases_jsonl  # noqa: E402
from simulator.graph_case_generator import (  # noqa: E402
    CASE_QC_ELIGIBLE,
    CASE_TYPE_ORDER,
    render_case_type_sample_markdown,
)


DEFAULT_CASES_FILE = (
    PROJECT_ROOT / "test_outputs" / "simulator_cases" / "graph_cases_20260502_role_qc" / "cases.jsonl"
)
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / "test_outputs" / "simulator_cases" / "graph_cases_20260502_role_qc" / "smoke20"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从图谱病例集中抽取 role-QC eligible smoke 病例。")
    parser.add_argument("--cases-file", default=str(DEFAULT_CASES_FILE), help="输入病例文件，支持 JSONL 或 JSON 数组。")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="输出 smoke 目录。")
    parser.add_argument("--total-size", type=int, default=20, help="总 smoke 病例数量。")
    parser.add_argument("--target-size-per-type", type=int, default=5, help="每个病例类型的优先目标数量。")
    parser.add_argument("--seed", type=int, default=42, help="随机种子。")
    parser.add_argument(
        "--qc-status",
        action="append",
        default=None,
        help="允许抽样的 case_qc_status，可重复指定；默认只抽 eligible。",
    )
    parser.add_argument(
        "--case-types",
        default=",".join(CASE_TYPE_ORDER),
        help="逗号分隔的病例类型列表，默认 ordinary,low_cost,exam_driven,competitive。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cases_file = Path(args.cases_file).resolve()
    output_root = Path(args.output_root).resolve()
    case_types = tuple(item.strip() for item in str(args.case_types).split(",") if item.strip())
    raw_qc_statuses = args.qc_status if args.qc_status is not None else [CASE_QC_ELIGIBLE]
    qc_statuses = tuple(dict.fromkeys(str(status).strip() for status in raw_qc_statuses if str(status).strip()))

    if not cases_file.exists():
        raise SystemExit(f"病例文件不存在：{cases_file}")

    cases = load_cases_jsonl(cases_file)
    sampled_by_type = _sample_balanced_cases(
        cases,
        total_size=args.total_size,
        target_size_per_type=args.target_size_per_type,
        seed=args.seed,
        case_types=case_types,
        qc_statuses=qc_statuses,
    )
    sampled_cases = [
        case
        for case_type in case_types
        for case in sorted(sampled_by_type.get(case_type) or [], key=lambda item: item.case_id)
    ]
    payload = _build_smoke_payload(
        source_file=cases_file,
        cases=cases,
        sampled_by_type=sampled_by_type,
        total_size=args.total_size,
        target_size_per_type=args.target_size_per_type,
        seed=args.seed,
        case_types=case_types,
        qc_statuses=qc_statuses,
    )

    output_root.mkdir(parents=True, exist_ok=True)
    cases_jsonl = output_root / "cases.jsonl"
    cases_json = output_root / "cases.json"
    manifest_file = output_root / "manifest.json"
    summary_file = output_root / "summary.md"

    write_cases_jsonl(sampled_cases, cases_jsonl)
    write_cases_json(sampled_cases, cases_json)
    manifest = {
        **payload,
        "output_root": str(output_root),
        "cases_jsonl": str(cases_jsonl),
        "cases_json": str(cases_json),
        "summary_file": str(summary_file),
        "sampled_case_ids": [case.case_id for case in sampled_cases],
        "sampled_cases": [asdict(case) for case in sampled_cases],
    }
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary_file.write_text(render_case_type_sample_markdown(payload), encoding="utf-8")

    print(
        json.dumps(
            {
                "status": "ok",
                "cases_file": str(cases_file),
                "output_root": str(output_root),
                "cases_jsonl": str(cases_jsonl),
                "cases_json": str(cases_json),
                "manifest_file": str(manifest_file),
                "summary_file": str(summary_file),
                "sampled_case_count": len(sampled_cases),
                "total_size": args.total_size,
                "target_size_per_type": args.target_size_per_type,
                "qc_statuses": list(qc_statuses),
                "available_case_count_by_type": payload["available_case_count_by_type"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _sample_balanced_cases(
    cases,
    *,
    total_size: int,
    target_size_per_type: int,
    seed: int,
    case_types: tuple[str, ...],
    qc_statuses: tuple[str, ...],
):
    """优先按类型均衡抽样；某类不足时用其他 eligible 病例补齐。"""

    import random

    rng = random.Random(seed)
    allowed_qc_statuses = set(qc_statuses)
    grouped = {case_type: [] for case_type in case_types}
    for case in cases:
        case_type = str(case.metadata.get("case_type") or "")
        case_qc_status = str(case.metadata.get("case_qc_status") or case.metadata.get("benchmark_qc_status") or "")
        if case_type not in grouped:
            continue
        if allowed_qc_statuses and case_qc_status not in allowed_qc_statuses:
            continue
        grouped[case_type].append(case)

    for case_type in case_types:
        grouped[case_type] = sorted(grouped[case_type], key=lambda item: item.case_id)

    sampled = {case_type: [] for case_type in case_types}
    selected_ids: set[str] = set()
    for case_type in case_types:
        pool = grouped.get(case_type) or []
        draw_count = min(target_size_per_type, len(pool), max(total_size - len(selected_ids), 0))
        if draw_count <= 0:
            continue
        drawn = rng.sample(pool, draw_count)
        sampled[case_type].extend(drawn)
        selected_ids.update(case.case_id for case in drawn)

    if len(selected_ids) < total_size:
        remainder = [
            case
            for case_type in case_types
            for case in grouped.get(case_type) or []
            if case.case_id not in selected_ids
        ]
        draw_count = min(total_size - len(selected_ids), len(remainder))
        for case in rng.sample(remainder, draw_count):
            sampled[str(case.metadata.get("case_type") or "")].append(case)
            selected_ids.add(case.case_id)

    if len(selected_ids) < total_size:
        raise ValueError(f"可用病例数量不足：需要 {total_size}，实际只有 {len(selected_ids)}")

    return sampled


def _build_smoke_payload(
    *,
    source_file: Path,
    cases,
    sampled_by_type,
    total_size: int,
    target_size_per_type: int,
    seed: int,
    case_types: tuple[str, ...],
    qc_statuses: tuple[str, ...],
) -> dict:
    """构造 smoke manifest，同时兼容原抽样 Markdown 渲染器需要的字段。"""

    allowed_qc_statuses = set(qc_statuses)
    return {
        "source_file": str(source_file),
        "sample_size_per_type": target_size_per_type,
        "target_size_per_type": target_size_per_type,
        "total_size": total_size,
        "seed": seed,
        "qc_statuses": sorted(allowed_qc_statuses),
        "requested_case_types": list(case_types),
        "available_case_count_by_type": {
            case_type: sum(
                1
                for case in cases
                if str(case.metadata.get("case_type") or "") == case_type
                and (
                    not allowed_qc_statuses
                    or str(case.metadata.get("case_qc_status") or case.metadata.get("benchmark_qc_status") or "")
                    in allowed_qc_statuses
                )
            )
            for case_type in case_types
        },
        "sampled_case_count": sum(len(items) for items in sampled_by_type.values()),
        "sampled_cases_by_type": {
            case_type: [asdict(case) for case in sorted(sampled_by_type.get(case_type) or [], key=lambda item: item.case_id)]
            for case_type in case_types
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
