"""按人工重分类清单修正已合并搜索图谱。"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from merge_nodes_by_aliases import (
    EDGE_SYSTEM_KEYS,
    NODE_SYSTEM_KEYS,
    build_canonical_edge_id,
    build_canonical_node_id,
    choose_detail_required,
    clean_text,
    merge_generic_field,
    merge_unique_strings,
    normalize_lookup_key,
    sort_edges,
    sort_nodes,
)


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
DEFAULT_RECLASSIFY_FILE = (
    PROJECT_ROOT
    / "test_outputs"
    / "search_kg"
    / "search_kg_20260419_125328"
    / "relation_repair"
    / "aliases_reclasify_le3"
    / "aliases_reclasify_le3.json"
)
DEFAULT_OUTPUT_GRAPH = (
    PROJECT_ROOT
    / "test_outputs"
    / "search_kg"
    / "search_kg_20260419_125328"
    / "relation_repair"
    / "alias_merge"
    / "merged_graph_by_aliases_pruned_le3_no_isolates_reclassified.json"
)
DEFAULT_REPORT_FILE = (
    PROJECT_ROOT
    / "test_outputs"
    / "search_kg"
    / "search_kg_20260419_125328"
    / "relation_repair"
    / "alias_merge"
    / "merged_graph_by_aliases_pruned_le3_no_isolates_reclassified_report.json"
)
DEFAULT_NODE_LIST_FILE = (
    PROJECT_ROOT
    / "test_outputs"
    / "search_kg"
    / "search_kg_20260419_125328"
    / "normalization_candidates"
    / "node_names_by_label_pruned_le3_reclassified.json"
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
DELETE_ACTION = "delete"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按人工清单重分类已合并图谱节点。")
    parser.add_argument("--input-graph", default=str(DEFAULT_INPUT_GRAPH), help="输入合并图谱 JSON。")
    parser.add_argument(
        "--reclassify-file",
        default=str(DEFAULT_RECLASSIFY_FILE),
        help="重分类 JSON，结构为 原标签 -> 目标标签/delete -> 节点名称列表。",
    )
    parser.add_argument("--output-graph", default=str(DEFAULT_OUTPUT_GRAPH), help="输出重分类图谱 JSON。")
    parser.add_argument("--report-file", default=str(DEFAULT_REPORT_FILE), help="输出处理报告 JSON。")
    parser.add_argument("--node-list-file", default=str(DEFAULT_NODE_LIST_FILE), help="输出按标签分组节点清单 JSON。")
    parser.add_argument(
        "--keep-isolated",
        action="store_true",
        help="保留重分类和删点后产生的非疾病孤立节点；默认会清理这些节点。",
    )
    return parser.parse_args()


def node_canonical_name(node: dict[str, Any]) -> str:
    return (
        clean_text(node.get("canonical_name"))
        or clean_text(node.get("name"))
        or clean_text(node.get("id"))
        or "unknown"
    )


def node_display_name(node: dict[str, Any], canonical_name: str) -> str:
    return clean_text(node.get("name")) or canonical_name


def load_reclassify_actions(path: Path) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], set[str]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Reclassify file must be an object: {path}")

    actions: dict[tuple[str, str], str] = {}
    entry_names: dict[tuple[str, str], set[str]] = defaultdict(set)
    conflicts: list[dict[str, str]] = []

    for source_label, target_mapping in payload.items():
        if not isinstance(source_label, str) or not isinstance(target_mapping, dict):
            continue

        for target_label, raw_names in target_mapping.items():
            if not isinstance(target_label, str) or not isinstance(raw_names, list):
                continue

            action = DELETE_ACTION if target_label == DELETE_ACTION else target_label
            for raw_name in raw_names:
                name = clean_text(raw_name)
                if name is None:
                    continue

                key = (source_label, normalize_lookup_key(name))
                previous_action = actions.get(key)
                if previous_action is not None and previous_action != action:
                    conflicts.append(
                        {
                            "source_label": source_label,
                            "name": name,
                            "previous_action": previous_action,
                            "new_action": action,
                        }
                    )
                    continue

                actions[key] = action
                entry_names[(source_label, action)].add(name)

    if conflicts:
        raise RuntimeError(
            "Conflicting reclassification entries: "
            + json.dumps(conflicts[:20], ensure_ascii=False)
        )

    return actions, entry_names


def lookup_action(node: dict[str, Any], actions: dict[tuple[str, str], str]) -> tuple[str | None, str | None]:
    label = clean_text(node.get("label"))
    if label is None:
        return None, None

    candidates = [
        clean_text(node.get("canonical_name")),
        clean_text(node.get("name")),
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        action = actions.get((label, normalize_lookup_key(candidate)))
        if action is not None:
            return action, candidate

    return None, None


def build_node_base(node: dict[str, Any], canonical_id: str, label: str, canonical_name: str) -> dict[str, Any]:
    merged = dict(node)
    merged["record_type"] = "canonical_node"
    merged["id"] = canonical_id
    merged["label"] = label
    merged["name"] = node_display_name(node, canonical_name)
    merged["canonical_name"] = canonical_name
    merged["aliases"] = merge_unique_strings(
        [],
        [canonical_name, node.get("name"), node.get("canonical_name")] + list(node.get("aliases", [])),
    )
    merged.setdefault("occurrence_count", 0)
    merged.setdefault("weight", 0.0)
    merged.setdefault("detail_required", "minimal")
    merged.setdefault("source_node_ids", [])
    merged.setdefault("source_files", [])
    merged.setdefault("source_heading_paths", [])
    merged.setdefault("merge_methods", [])
    return merged


def merge_existing_node(target: dict[str, Any], node: dict[str, Any], canonical_name: str) -> None:
    target["aliases"] = merge_unique_strings(
        target.get("aliases", []),
        [canonical_name, node.get("name"), node.get("canonical_name")] + list(node.get("aliases", [])),
    )
    target["source_node_ids"] = merge_unique_strings(
        target.get("source_node_ids", []),
        list(node.get("source_node_ids", [])),
    )
    target["source_files"] = merge_unique_strings(
        target.get("source_files", []),
        list(node.get("source_files", [])),
    )
    target["source_heading_paths"] = merge_unique_strings(
        target.get("source_heading_paths", []),
        list(node.get("source_heading_paths", [])),
    )
    target["merge_methods"] = merge_unique_strings(
        target.get("merge_methods", []),
        list(node.get("merge_methods", [])),
    )
    target["occurrence_count"] = int(target.get("occurrence_count", 0)) + int(
        node.get("occurrence_count", 0) or 0
    )
    target["weight"] = max(float(target.get("weight", 0.0)), float(node.get("weight", 0.0) or 0.0))
    target["detail_required"] = choose_detail_required(
        target.get("detail_required"),
        node.get("detail_required"),
    )

    for key, value in node.items():
        if key in NODE_SYSTEM_KEYS:
            continue
        merge_generic_field(target, key, value)


def build_edge_base(edge: dict[str, Any], edge_id: str, source_id: str, target_id: str) -> dict[str, Any]:
    merged = dict(edge)
    merged["record_type"] = "canonical_edge"
    merged["id"] = edge_id
    merged["source_id"] = source_id
    merged["target_id"] = target_id
    merged.setdefault("occurrence_count", 0)
    merged.setdefault("weight", 0.0)
    merged.setdefault("detail_required", "minimal")
    merged.setdefault("source_edge_ids", [])
    merged.setdefault("source_files", [])
    merged.setdefault("source_heading_paths", [])
    return merged


def merge_existing_edge(target: dict[str, Any], edge: dict[str, Any]) -> None:
    target["source_edge_ids"] = merge_unique_strings(
        target.get("source_edge_ids", []),
        list(edge.get("source_edge_ids", [])),
    )
    target["source_files"] = merge_unique_strings(
        target.get("source_files", []),
        list(edge.get("source_files", [])),
    )
    target["source_heading_paths"] = merge_unique_strings(
        target.get("source_heading_paths", []),
        list(edge.get("source_heading_paths", [])),
    )
    target["occurrence_count"] = int(target.get("occurrence_count", 0)) + int(
        edge.get("occurrence_count", 0) or 0
    )
    target["weight"] = max(float(target.get("weight", 0.0)), float(edge.get("weight", 0.0) or 0.0))
    target["detail_required"] = choose_detail_required(
        target.get("detail_required"),
        edge.get("detail_required"),
    )

    for key, value in edge.items():
        if key in EDGE_SYSTEM_KEYS:
            continue
        merge_generic_field(target, key, value)


def label_counts(nodes: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(node.get("label") or "") for node in nodes if isinstance(node, dict))
    return {label: counts[label] for label in sorted(counts)}


def export_node_names(nodes: list[dict[str, Any]]) -> dict[str, list[str]]:
    grouped: dict[str, set[str]] = {label: set() for label in LABEL_ORDER}
    for node in nodes:
        label = clean_text(node.get("label"))
        name = node_canonical_name(node)
        if label is None:
            continue
        grouped.setdefault(label, set()).add(name)

    ordered_labels = list(LABEL_ORDER) + sorted(label for label in grouped if label not in LABEL_ORDER)
    return {
        label: sorted(grouped[label], key=lambda value: value.lower())
        for label in ordered_labels
    }


def reclassify_graph(
    graph: dict[str, Any],
    actions: dict[tuple[str, str], str],
    entry_names: dict[tuple[str, str], set[str]],
    *,
    drop_isolated_non_disease: bool = True,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, list[str]]]:
    raw_nodes = graph.get("nodes", [])
    raw_edges = graph.get("edges", [])
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise RuntimeError("Input graph must contain list fields: nodes, edges")

    canonical_nodes: dict[str, dict[str, Any]] = {}
    old_to_new_node_id: dict[str, str] = {}
    deleted_node_ids: set[str] = set()
    matched_entries: dict[tuple[str, str], set[str]] = defaultdict(set)
    reclassified_by_pair: Counter[tuple[str, str]] = Counter()
    deleted_by_label: Counter[str] = Counter()
    duplicate_node_groups: dict[str, list[str]] = defaultdict(list)

    for raw_node in raw_nodes:
        if not isinstance(raw_node, dict):
            continue

        node = dict(raw_node)
        old_id = clean_text(node.get("id"))
        old_label = clean_text(node.get("label"))
        if old_id is None or old_label is None:
            continue

        action, matched_name = lookup_action(node, actions)
        if action is not None and matched_name is not None:
            matched_entries[(old_label, action)].add(matched_name)

        if action == DELETE_ACTION:
            deleted_node_ids.add(old_id)
            deleted_by_label[old_label] += 1
            continue

        new_label = action or old_label
        canonical_name = node_canonical_name(node)
        merge_key = f"{new_label}::{canonical_name}"
        new_id = build_canonical_node_id(merge_key)
        old_to_new_node_id[old_id] = new_id
        duplicate_node_groups[new_id].append(old_id)

        if new_label != old_label:
            reclassified_by_pair[(old_label, new_label)] += 1

        reclassified_node = build_node_base(
            node=node,
            canonical_id=new_id,
            label=new_label,
            canonical_name=canonical_name,
        )

        if new_id not in canonical_nodes:
            canonical_nodes[new_id] = reclassified_node
        else:
            merge_existing_node(canonical_nodes[new_id], reclassified_node, canonical_name)

    canonical_edges: dict[str, dict[str, Any]] = {}
    deleted_incident_edge_count = 0
    skipped_self_loop_edge_count = 0
    skipped_unmapped_edge_count = 0
    remapped_edge_count = 0

    for raw_edge in raw_edges:
        if not isinstance(raw_edge, dict):
            continue

        edge = dict(raw_edge)
        source_id = clean_text(edge.get("source_id"))
        target_id = clean_text(edge.get("target_id"))
        edge_type = clean_text(edge.get("type"))
        if source_id is None or target_id is None or edge_type is None:
            continue

        if source_id in deleted_node_ids or target_id in deleted_node_ids:
            deleted_incident_edge_count += 1
            continue

        new_source_id = old_to_new_node_id.get(source_id)
        new_target_id = old_to_new_node_id.get(target_id)
        if new_source_id is None or new_target_id is None:
            skipped_unmapped_edge_count += 1
            continue

        if new_source_id == new_target_id:
            skipped_self_loop_edge_count += 1
            continue

        if new_source_id != source_id or new_target_id != target_id:
            remapped_edge_count += 1

        condition_text = clean_text(edge.get("condition_text")) or ""
        new_edge_id = build_canonical_edge_id(
            edge_type=edge_type,
            source_id=new_source_id,
            target_id=new_target_id,
            condition_text=condition_text,
        )
        remapped_edge = build_edge_base(
            edge=edge,
            edge_id=new_edge_id,
            source_id=new_source_id,
            target_id=new_target_id,
        )

        if new_edge_id not in canonical_edges:
            canonical_edges[new_edge_id] = remapped_edge
        else:
            merge_existing_edge(canonical_edges[new_edge_id], remapped_edge)

    output_edges = sort_edges(list(canonical_edges.values()))
    output_nodes_before_isolate_cleanup = sort_nodes(list(canonical_nodes.values()))
    connected_node_ids = {
        endpoint
        for edge in output_edges
        for endpoint in (clean_text(edge.get("source_id")), clean_text(edge.get("target_id")))
        if endpoint is not None
    }
    isolated_nodes = [
        node
        for node in output_nodes_before_isolate_cleanup
        if clean_text(node.get("id")) not in connected_node_ids
    ]
    removed_isolated_nodes = [
        node
        for node in isolated_nodes
        if drop_isolated_non_disease and clean_text(node.get("label")) != "Disease"
    ]
    removed_isolated_node_ids = {
        clean_text(node.get("id"))
        for node in removed_isolated_nodes
        if clean_text(node.get("id")) is not None
    }
    output_nodes = [
        node
        for node in output_nodes_before_isolate_cleanup
        if clean_text(node.get("id")) not in removed_isolated_node_ids
    ]
    remaining_node_ids = {
        clean_text(node.get("id"))
        for node in output_nodes
        if clean_text(node.get("id")) is not None
    }
    remaining_isolated_nodes = [
        node
        for node in output_nodes
        if clean_text(node.get("id")) not in connected_node_ids
    ]
    output_edges = [
        edge
        for edge in output_edges
        if clean_text(edge.get("source_id")) in remaining_node_ids
        and clean_text(edge.get("target_id")) in remaining_node_ids
    ]
    node_names_by_label = export_node_names(output_nodes)

    duplicate_node_merge_groups = {
        node_id: old_ids
        for node_id, old_ids in sorted(duplicate_node_groups.items())
        if len(old_ids) > 1
    }
    missing_entries = []
    for key, names in sorted(entry_names.items()):
        matched_names = matched_entries.get(key, set())
        for name in sorted(names - matched_names, key=lambda value: value.lower()):
            missing_entries.append(
                {
                    "source_label": key[0],
                    "action": key[1],
                    "name": name,
                }
            )

    summary = {
        **dict(graph.get("summary", {})),
        "pre_reclass_node_count": len(raw_nodes),
        "pre_reclass_edge_count": len(raw_edges),
        "deleted_node_count": len(deleted_node_ids),
        "deleted_node_count_by_label": dict(sorted(deleted_by_label.items())),
        "deleted_incident_edge_count": deleted_incident_edge_count,
        "reclassified_node_count": sum(reclassified_by_pair.values()),
        "reclassified_node_count_by_pair": {
            f"{source}->{target}": count
            for (source, target), count in sorted(reclassified_by_pair.items())
        },
        "duplicate_node_merge_group_count": len(duplicate_node_merge_groups),
        "duplicate_node_merged_away_count": sum(len(ids) - 1 for ids in duplicate_node_merge_groups.values()),
        "deduplicated_edge_count": len(raw_edges) - deleted_incident_edge_count - len(output_edges),
        "remapped_edge_count": remapped_edge_count,
        "skipped_self_loop_edge_count": skipped_self_loop_edge_count,
        "skipped_unmapped_edge_count": skipped_unmapped_edge_count,
        "drop_isolated_non_disease_after_reclass": drop_isolated_non_disease,
        "removed_isolated_after_reclass_count": len(removed_isolated_nodes),
        "removed_isolated_after_reclass_by_label": label_counts(removed_isolated_nodes),
        "remaining_isolated_node_count": len(remaining_isolated_nodes),
        "post_reclass_node_count": len(output_nodes),
        "post_reclass_edge_count": len(output_edges),
        "post_reclass_node_count_by_label": label_counts(output_nodes),
    }

    output_graph = {
        "summary": summary,
        "nodes": output_nodes,
        "edges": output_edges,
    }
    report = {
        "summary": summary,
        "pre_reclass_node_count_by_label": label_counts([node for node in raw_nodes if isinstance(node, dict)]),
        "post_reclass_node_count_by_label": summary["post_reclass_node_count_by_label"],
        "missing_reclassification_entries": missing_entries,
        "duplicate_node_merge_groups": duplicate_node_merge_groups,
        "removed_isolated_after_reclass_nodes": [
            {
                "id": clean_text(node.get("id")),
                "label": clean_text(node.get("label")),
                "name": node_canonical_name(node),
            }
            for node in removed_isolated_nodes
        ],
        "remaining_isolated_nodes": [
            {
                "id": clean_text(node.get("id")),
                "label": clean_text(node.get("label")),
                "name": node_canonical_name(node),
            }
            for node in remaining_isolated_nodes
        ],
    }
    return output_graph, report, node_names_by_label


def main() -> int:
    args = parse_args()
    input_graph = Path(args.input_graph).resolve()
    reclassify_file = Path(args.reclassify_file).resolve()
    output_graph_file = Path(args.output_graph).resolve()
    report_file = Path(args.report_file).resolve()
    node_list_file = Path(args.node_list_file).resolve()

    if not input_graph.exists():
        raise RuntimeError(f"Input graph does not exist: {input_graph}")
    if not reclassify_file.exists():
        raise RuntimeError(f"Reclassify file does not exist: {reclassify_file}")

    graph = json.loads(input_graph.read_text(encoding="utf-8"))
    actions, entry_names = load_reclassify_actions(reclassify_file)
    output_graph, report, node_names_by_label = reclassify_graph(
        graph,
        actions,
        entry_names,
        drop_isolated_non_disease=not args.keep_isolated,
    )
    report.update(
        {
            "input_graph": str(input_graph),
            "reclassify_file": str(reclassify_file),
            "output_graph": str(output_graph_file),
            "report_file": str(report_file),
            "node_list_file": str(node_list_file),
        }
    )

    output_graph_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    node_list_file.parent.mkdir(parents=True, exist_ok=True)
    output_graph_file.write_text(
        json.dumps(output_graph, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_file.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    node_list_file.write_text(
        json.dumps(node_names_by_label, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "status": "ok",
                "input_graph": str(input_graph),
                "reclassify_file": str(reclassify_file),
                "output_graph": str(output_graph_file),
                "report_file": str(report_file),
                "node_list_file": str(node_list_file),
                "summary": output_graph["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("[fatal] interrupted by user", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:  # noqa: BLE001
        print(f"[fatal] {exc}", file=sys.stderr)
        raise SystemExit(1)
