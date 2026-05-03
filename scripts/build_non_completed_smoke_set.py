"""从 replay 未完成病例中抽取后续 smoke 病例集。"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from simulator.generate_cases import load_cases_jsonl, write_cases_json, write_cases_jsonl  # noqa: E402


DEFAULT_CASES_FILE = (
    PROJECT_ROOT / "test_outputs" / "simulator_cases" / "graph_cases_20260502_role_qc" / "cases.jsonl"
)
DEFAULT_NON_COMPLETED_FILE = (
    PROJECT_ROOT
    / "test_outputs"
    / "simulator_replay"
    / "graph_cases_20260502_role_qc_full"
    / "non_completed_cases.json"
)
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT
    / "test_outputs"
    / "simulator_cases"
    / "graph_cases_20260502_role_qc"
    / "non_completed_smoke80"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从 non_completed_cases.json 精确抽取未完成病例骨架。")
    parser.add_argument("--cases-file", default=str(DEFAULT_CASES_FILE), help="完整病例文件，支持 JSONL 或 JSON 数组。")
    parser.add_argument(
        "--non-completed-file",
        default=str(DEFAULT_NON_COMPLETED_FILE),
        help="run_batch_replay.py 产出的 non_completed_cases.json。",
    )
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="输出 smoke 目录。")
    parser.add_argument(
        "--include-category",
        action="append",
        default=None,
        help="只抽取指定异常类别；可重复指定。默认抽取全部未完成病例。",
    )
    parser.add_argument("--limit", type=int, default=0, help="最多抽取 N 个病例；0 表示不限制。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cases_file = Path(args.cases_file).resolve()
    non_completed_file = Path(args.non_completed_file).resolve()
    output_root = Path(args.output_root).resolve()

    if not cases_file.exists():
        raise SystemExit(f"病例文件不存在：{cases_file}")
    if not non_completed_file.exists():
        raise SystemExit(f"未完成病例报告不存在：{non_completed_file}")

    cases = load_cases_jsonl(cases_file)
    non_completed_payload = json.loads(non_completed_file.read_text(encoding="utf-8"))
    include_categories = tuple(str(item).strip() for item in args.include_category or [] if str(item).strip())
    selected_cases, manifest = build_non_completed_smoke_payload(
        cases=cases,
        non_completed_payload=non_completed_payload,
        source_cases_file=cases_file,
        non_completed_file=non_completed_file,
        output_root=output_root,
        include_categories=include_categories,
        limit=args.limit,
    )

    output_root.mkdir(parents=True, exist_ok=True)
    cases_jsonl = output_root / "cases.jsonl"
    cases_json = output_root / "cases.json"
    manifest_file = output_root / "manifest.json"
    summary_file = output_root / "summary.md"

    write_cases_jsonl(selected_cases, cases_jsonl)
    write_cases_json(selected_cases, cases_json)
    manifest.update(
        {
            "cases_jsonl": str(cases_jsonl),
            "cases_json": str(cases_json),
            "manifest_file": str(manifest_file),
            "summary_file": str(summary_file),
            "selected_cases": [asdict(case) for case in selected_cases],
        }
    )
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary_file.write_text(render_non_completed_smoke_markdown(manifest), encoding="utf-8")

    print(
        json.dumps(
            {
                "status": "ok",
                "cases_file": str(cases_file),
                "non_completed_file": str(non_completed_file),
                "output_root": str(output_root),
                "cases_jsonl": str(cases_jsonl),
                "cases_json": str(cases_json),
                "manifest_file": str(manifest_file),
                "summary_file": str(summary_file),
                "selected_case_count": len(selected_cases),
                "missing_case_count": len(manifest["missing_case_ids"]),
                "category_breakdown": manifest["selected_category_breakdown"],
                "case_type_breakdown": manifest["selected_case_type_breakdown"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


# 根据未完成报告和完整病例集构造可复跑的 smoke payload。
def build_non_completed_smoke_payload(
    *,
    cases,
    non_completed_payload: dict[str, Any],
    source_cases_file: Path,
    non_completed_file: Path,
    output_root: Path,
    include_categories: Sequence[str] = (),
    limit: int = 0,
):
    records = _filter_non_completed_records(non_completed_payload, include_categories=include_categories)
    if limit > 0:
        records = records[:limit]

    case_by_id = {case.case_id: case for case in cases}
    selected_cases = [case_by_id[record["case_id"]] for record in records if record["case_id"] in case_by_id]
    missing_case_ids = [record["case_id"] for record in records if record["case_id"] not in case_by_id]
    selected_case_ids = [case.case_id for case in selected_cases]
    selected_id_set = set(selected_case_ids)
    selected_records = [record for record in records if record["case_id"] in selected_id_set]

    manifest = {
        "source_cases_file": str(source_cases_file),
        "non_completed_file": str(non_completed_file),
        "output_root": str(output_root),
        "source_case_count": len(cases),
        "non_completed_case_count": int(non_completed_payload.get("non_completed_count", len(non_completed_payload.get("cases", [])))),
        "requested_category_filter": list(include_categories),
        "limit": int(limit),
        "selected_case_count": len(selected_cases),
        "selected_case_ids": selected_case_ids,
        "missing_case_ids": missing_case_ids,
        "selected_category_breakdown": _count_records_by_key(selected_records, "category"),
        "selected_case_type_breakdown": _count_cases_by_type(selected_cases),
        "selected_records_by_category": _group_records_by_category(selected_records),
        "selected_records": selected_records,
    }
    return selected_cases, manifest


# 从 non_completed_cases.json 中取出需要进入 smoke 的异常病例记录。
def _filter_non_completed_records(non_completed_payload: dict[str, Any], *, include_categories: Sequence[str]) -> list[dict]:
    raw_records = non_completed_payload.get("cases", [])
    if not isinstance(raw_records, list):
        return []

    allowed_categories = {str(item) for item in include_categories if str(item)}
    records = []
    seen_case_ids: set[str] = set()

    for raw_record in raw_records:
        if not isinstance(raw_record, dict):
            continue

        case_id = str(raw_record.get("case_id") or "").strip()
        category = str(raw_record.get("category") or "").strip()
        if len(case_id) == 0 or case_id in seen_case_ids:
            continue
        if allowed_categories and category not in allowed_categories:
            continue

        records.append(dict(raw_record))
        seen_case_ids.add(case_id)

    return records


def _count_records_by_key(records: Sequence[dict], key: str) -> dict[str, int]:
    counter = Counter(str(record.get(key) or "unknown") for record in records)
    return dict(sorted(counter.items(), key=lambda item: item[0]))


def _count_cases_by_type(cases) -> dict[str, int]:
    counter = Counter(str(case.metadata.get("case_type") or "unknown") for case in cases)
    return dict(sorted(counter.items(), key=lambda item: item[0]))


def _group_records_by_category(records: Sequence[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        grouped[str(record.get("category") or "unknown")].append(record)
    return {key: grouped[key] for key in sorted(grouped)}


def render_non_completed_smoke_markdown(manifest: dict[str, Any]) -> str:
    lines = [
        "# 未完成病例 Smoke 集",
        "",
        "## 来源",
        "",
        f"- source_cases_file: `{manifest.get('source_cases_file')}`",
        f"- non_completed_file: `{manifest.get('non_completed_file')}`",
        f"- output_root: `{manifest.get('output_root')}`",
        f"- source_case_count: `{manifest.get('source_case_count')}`",
        f"- non_completed_case_count: `{manifest.get('non_completed_case_count')}`",
        f"- selected_case_count: `{manifest.get('selected_case_count')}`",
        f"- missing_case_count: `{len(manifest.get('missing_case_ids') or [])}`",
        "",
        "## 异常类别分布",
        "",
    ]
    lines.extend(_render_count_table(manifest.get("selected_category_breakdown") or {}))
    lines.extend(["", "## 病例类型分布", ""])
    lines.extend(_render_count_table(manifest.get("selected_case_type_breakdown") or {}))
    lines.extend(["", "## 病例清单", ""])
    lines.append("| case_id | case_type | category | true_conditions | final_answer | stop_reason |")
    lines.append("| --- | --- | --- | --- | --- | --- |")

    selected_records = manifest.get("selected_records") or []
    selected_cases = manifest.get("selected_cases") or []
    case_type_by_id = {
        str(case.get("case_id") or ""): str((case.get("metadata") or {}).get("case_type") or "")
        for case in selected_cases
        if isinstance(case, dict)
    }

    for record in selected_records:
        if not isinstance(record, dict):
            continue
        case_id = str(record.get("case_id") or "")
        true_conditions = "、".join(str(item) for item in record.get("true_conditions") or [])
        lines.append(
            "| "
            + " | ".join(
                [
                    case_id,
                    case_type_by_id.get(case_id, ""),
                    str(record.get("category") or ""),
                    true_conditions,
                    str(record.get("final_answer_name") or ""),
                    str(record.get("stop_reason") or ""),
                ]
            )
            + " |"
        )

    if len(manifest.get("missing_case_ids") or []) > 0:
        lines.extend(["", "## 缺失病例", ""])
        for case_id in manifest.get("missing_case_ids") or []:
            lines.append(f"- `{case_id}`")

    return "\n".join(lines) + "\n"


def _render_count_table(counts: dict[str, int]) -> list[str]:
    if not counts:
        return ["无。"]

    lines = ["| name | count |", "| --- | ---: |"]
    for name, count in counts.items():
        lines.append(f"| {name} | {count} |")
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
