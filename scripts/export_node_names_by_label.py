"""从合并图谱 JSON 导出按标签分组的节点名称清单。"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_GRAPH = (
    PROJECT_ROOT
    / "test_outputs"
    / "search_kg"
    / "search_kg_20260419_125328"
    / "relation_repair"
    / "alias_merge"
    / "merged_graph_by_aliases_pruned_le3_no_isolates.json"
)
DEFAULT_OUTPUT_JSON = (
    PROJECT_ROOT
    / "test_outputs"
    / "search_kg"
    / "search_kg_20260419_125328"
    / "normalization_candidates"
    / "node_names_by_label_pruned_le3.json"
)
DEFAULT_OUTPUT_MARKDOWN = (
    PROJECT_ROOT
    / "test_outputs"
    / "search_kg"
    / "search_kg_20260419_125328"
    / "normalization_candidates"
    / "node_names_by_label_pruned_le3.md"
)


LABEL_ORDER = (
    "Disease",
    "ClinicalFinding",
    "ClinicalAttribute",
    "RiskFactor",
    "PopulationGroup",
    "LabTest",
    "LabFinding",
    "ImagingFinding",
    "Pathogen",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出按 label 分组的节点名称清单。")
    parser.add_argument("--input-graph", default=str(DEFAULT_INPUT_GRAPH), help="输入合并图谱 JSON。")
    parser.add_argument("--output-json", default=str(DEFAULT_OUTPUT_JSON), help="输出 JSON 路径。")
    parser.add_argument("--output-markdown", default=str(DEFAULT_OUTPUT_MARKDOWN), help="输出 Markdown 路径。")
    return parser.parse_args()


def node_display_name(node: dict[str, Any]) -> str:
    return str(node.get("canonical_name") or node.get("name") or "").strip()


def dedupe_sorted(values: list[str]) -> list[str]:
    return sorted({value for value in values if value}, key=lambda value: value.lower())


def markdown_list(label_to_names: dict[str, list[str]]) -> str:
    lines = [
        "# 当前图谱节点名称按标签分组清单",
        "",
        f"- source_graph: `{DEFAULT_INPUT_GRAPH}`",
        "- 用途：人工检查每个节点是否放在正确类别，并为后续 alias / 重分类 / 元数据修复提供清单。",
        "",
    ]
    for label in ordered_labels(label_to_names):
        names = label_to_names[label]
        lines.extend(
            [
                f"## {label}",
                "",
                f"- count: {len(names)}",
                "",
            ]
        )
        lines.extend(f"- {name}" for name in names)
        lines.append("")
    return "\n".join(lines)


def ordered_labels(label_to_names: dict[str, list[str]]) -> list[str]:
    known = [label for label in LABEL_ORDER if label in label_to_names]
    extra = sorted(label for label in label_to_names if label not in LABEL_ORDER)
    return known + extra


def main() -> int:
    args = parse_args()
    input_graph = Path(args.input_graph).resolve()
    output_json = Path(args.output_json).resolve()
    output_markdown = Path(args.output_markdown).resolve()
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_markdown.parent.mkdir(parents=True, exist_ok=True)

    graph = json.loads(input_graph.read_text(encoding="utf-8"))
    grouped: dict[str, list[str]] = defaultdict(list)
    for node in graph.get("nodes", []):
        if not isinstance(node, dict):
            continue
        label = str(node.get("label") or "").strip()
        name = node_display_name(node)
        if label and name:
            grouped[label].append(name)

    label_to_names = {
        label: dedupe_sorted(grouped[label])
        for label in ordered_labels(grouped)
    }
    output_json.write_text(json.dumps(label_to_names, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_markdown.write_text(markdown_list(label_to_names) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "input_graph": str(input_graph),
                "output_json": str(output_json),
                "output_markdown": str(output_markdown),
                "label_counts": {label: len(names) for label, names in label_to_names.items()},
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
