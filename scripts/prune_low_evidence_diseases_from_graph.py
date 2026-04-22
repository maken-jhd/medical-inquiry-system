"""从合并图谱 JSON 中删除低证据疾病，并清理非疾病孤立节点。"""

from __future__ import annotations

import argparse
import json
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
    / "merged_graph_by_aliases_pruned_no_isolates.json"
)
DEFAULT_AUDIT_JSON = (
    PROJECT_ROOT
    / "test_outputs"
    / "graph_audit"
    / "all_diseases_20260419"
    / "low_evidence_diseases_le3_old_graph.json"
)
DEFAULT_OUTPUT_GRAPH = (
    PROJECT_ROOT
    / "test_outputs"
    / "search_kg"
    / "search_kg_20260419_125328"
    / "relation_repair"
    / "alias_merge"
    / "merged_graph_by_aliases_pruned_le3_no_isolates.json"
)
DEFAULT_OUTPUT_REPORT = (
    PROJECT_ROOT
    / "test_outputs"
    / "search_kg"
    / "search_kg_20260419_125328"
    / "relation_repair"
    / "alias_merge"
    / "pruned_le3_no_isolates_report.json"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="基于疾病审计结果 prune 合并图谱 JSON。")
    parser.add_argument("--input-graph", default=str(DEFAULT_INPUT_GRAPH), help="输入合并图谱 JSON。")
    parser.add_argument("--audit-json", default=str(DEFAULT_AUDIT_JSON), help="low evidence 疾病清单 JSON。")
    parser.add_argument("--output-graph", default=str(DEFAULT_OUTPUT_GRAPH), help="输出 prune 后图谱 JSON。")
    parser.add_argument("--output-report", default=str(DEFAULT_OUTPUT_REPORT), help="输出 prune 报告 JSON。")
    parser.add_argument("--max-evidence-count", type=int, default=3, help="删除 evidence_count 小于等于该值的 Disease。")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"missing file: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def load_target_diseases(path: Path, max_evidence_count: int) -> list[dict[str, Any]]:
    payload = load_json(path)
    targets: list[dict[str, Any]] = []
    for item in payload.get("items", []):
        if not isinstance(item, dict):
            continue
        disease_id = str(item.get("disease_id") or "").strip()
        evidence_count = int(item.get("evidence_count") or 0)
        if disease_id and evidence_count <= max_evidence_count:
            targets.append(
                {
                    "disease_id": disease_id,
                    "disease_name": str(item.get("disease_name") or ""),
                    "evidence_count": evidence_count,
                    "issue_count": int(item.get("issue_count") or 0),
                }
            )
    targets.sort(key=lambda item: (item["evidence_count"], item["disease_name"]))
    return targets


def node_id(node: dict[str, Any]) -> str:
    return str(node.get("id") or "").strip()


def edge_source(edge: dict[str, Any]) -> str:
    return str(edge.get("source_id") or "").strip()


def edge_target(edge: dict[str, Any]) -> str:
    return str(edge.get("target_id") or "").strip()


def prune_graph(graph: dict[str, Any], targets: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    nodes = [dict(item) for item in graph.get("nodes", []) if isinstance(item, dict)]
    edges = [dict(item) for item in graph.get("edges", []) if isinstance(item, dict)]
    target_ids = {item["disease_id"] for item in targets}
    existing_target_ids = {node_id(node) for node in nodes if node_id(node) in target_ids and node.get("label") == "Disease"}
    missing_target_ids = sorted(target_ids - existing_target_ids)

    kept_nodes_after_disease = [node for node in nodes if node_id(node) not in existing_target_ids]
    removed_edges_for_disease = [
        edge
        for edge in edges
        if edge_source(edge) in existing_target_ids or edge_target(edge) in existing_target_ids
    ]
    kept_edges_after_disease = [
        edge
        for edge in edges
        if edge_source(edge) not in existing_target_ids and edge_target(edge) not in existing_target_ids
    ]

    connected_node_ids = {
        endpoint
        for edge in kept_edges_after_disease
        for endpoint in (edge_source(edge), edge_target(edge))
        if endpoint
    }
    isolated_non_disease_nodes = [
        node
        for node in kept_nodes_after_disease
        if node_id(node) not in connected_node_ids and node.get("label") != "Disease"
    ]
    isolated_non_disease_ids = {node_id(node) for node in isolated_non_disease_nodes}
    final_nodes = [node for node in kept_nodes_after_disease if node_id(node) not in isolated_non_disease_ids]
    final_node_ids = {node_id(node) for node in final_nodes}
    final_edges = [
        edge
        for edge in kept_edges_after_disease
        if edge_source(edge) in final_node_ids and edge_target(edge) in final_node_ids
    ]

    summary = dict(graph.get("summary") or {})
    summary.update(
        {
            "prune_low_evidence_threshold": 3,
            "prune_source": "low_evidence_diseases_le3_old_graph",
            "pre_prune_node_count": len(nodes),
            "pre_prune_edge_count": len(edges),
            "removed_disease_node_count": len(existing_target_ids),
            "removed_incident_edge_count": len(removed_edges_for_disease),
            "removed_isolated_non_disease_node_count": len(isolated_non_disease_nodes),
            "post_prune_node_count": len(final_nodes),
            "post_prune_edge_count": len(final_edges),
        }
    )
    pruned = {**graph, "summary": summary, "nodes": final_nodes, "edges": final_edges}
    report = {
        "target_count_from_audit": len(targets),
        "matched_target_count": len(existing_target_ids),
        "missing_target_ids": missing_target_ids,
        "removed_disease_nodes": [
            item for item in targets if item["disease_id"] in existing_target_ids
        ],
        "removed_disease_node_count": len(existing_target_ids),
        "removed_incident_edge_count": len(removed_edges_for_disease),
        "removed_isolated_non_disease_node_count": len(isolated_non_disease_nodes),
        "removed_isolated_non_disease_nodes": [
            {
                "id": node_id(node),
                "label": str(node.get("label") or ""),
                "name": str(node.get("canonical_name") or node.get("name") or ""),
            }
            for node in isolated_non_disease_nodes
        ],
        "before_counts": {
            "nodes": len(nodes),
            "edges": len(edges),
            "diseases": sum(1 for node in nodes if node.get("label") == "Disease"),
        },
        "after_counts": {
            "nodes": len(final_nodes),
            "edges": len(final_edges),
            "diseases": sum(1 for node in final_nodes if node.get("label") == "Disease"),
        },
    }
    return pruned, report


def main() -> int:
    args = parse_args()
    input_graph = Path(args.input_graph).resolve()
    audit_json = Path(args.audit_json).resolve()
    output_graph = Path(args.output_graph).resolve()
    output_report = Path(args.output_report).resolve()
    output_graph.parent.mkdir(parents=True, exist_ok=True)
    output_report.parent.mkdir(parents=True, exist_ok=True)

    graph = load_json(input_graph)
    targets = load_target_diseases(audit_json, args.max_evidence_count)
    pruned, report = prune_graph(graph, targets)
    report.update(
        {
            "input_graph": str(input_graph),
            "audit_json": str(audit_json),
            "output_graph": str(output_graph),
            "max_evidence_count": args.max_evidence_count,
        }
    )
    pruned["summary"]["prune_low_evidence_threshold"] = args.max_evidence_count

    output_graph.write_text(json.dumps(pruned, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", **report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
