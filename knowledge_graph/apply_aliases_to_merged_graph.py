"""对已合并搜索图谱再次应用人工 alias 合并规则。"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from merge_nodes_by_aliases import (
    ALIAS_FILE_TO_LABEL,
    build_canonical_edge_id,
    build_canonical_node_id,
    clean_text,
    normalize_lookup_key,
    sort_edges,
    sort_nodes,
)
from reclassify_merged_graph import (
    build_edge_base,
    build_node_base,
    export_node_names,
    label_counts,
    merge_existing_edge,
    merge_existing_node,
    node_canonical_name,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_GRAPH = (
    PROJECT_ROOT
    / "test_outputs"
    / "search_kg"
    / "search_kg_20260419_125328"
    / "relation_repair"
    / "alias_merge"
    / "merged_graph_by_aliases_pruned_le3_no_isolates_reclassified.json"
)
DEFAULT_ALIAS_DIR = (
    PROJECT_ROOT
    / "test_outputs"
    / "search_kg"
    / "search_kg_20260419_125328"
    / "aliases_le3"
)
DEFAULT_OUTPUT_GRAPH = (
    PROJECT_ROOT
    / "test_outputs"
    / "search_kg"
    / "search_kg_20260419_125328"
    / "relation_repair"
    / "alias_merge"
    / "merged_graph_by_aliases_pruned_le3_reclassified_aliases_le3_no_isolates.json"
)
DEFAULT_REPORT_FILE = (
    PROJECT_ROOT
    / "test_outputs"
    / "search_kg"
    / "search_kg_20260419_125328"
    / "relation_repair"
    / "alias_merge"
    / "merged_graph_by_aliases_pruned_le3_reclassified_aliases_le3_no_isolates_report.json"
)
DEFAULT_NODE_LIST_FILE = (
    PROJECT_ROOT
    / "test_outputs"
    / "search_kg"
    / "search_kg_20260419_125328"
    / "normalization_candidates"
    / "node_names_by_label_pruned_le3_reclassified_aliases_le3.json"
)
DELETE_ACTION = "delete"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="对已合并图谱应用 alias 合并与 delete 规则。")
    parser.add_argument("--input-graph", default=str(DEFAULT_INPUT_GRAPH), help="输入已合并图谱 JSON。")
    parser.add_argument("--alias-dir", default=str(DEFAULT_ALIAS_DIR), help="alias 规则目录。")
    parser.add_argument(
        "--alias-file",
        action="append",
        default=[],
        help="只加载指定 alias JSON 文件；可重复传入。默认加载 alias-dir 下所有已知文件。",
    )
    parser.add_argument("--output-graph", default=str(DEFAULT_OUTPUT_GRAPH), help="输出图谱 JSON。")
    parser.add_argument("--report-file", default=str(DEFAULT_REPORT_FILE), help="输出处理报告 JSON。")
    parser.add_argument("--node-list-file", default=str(DEFAULT_NODE_LIST_FILE), help="输出按标签分组节点清单 JSON。")
    parser.add_argument(
        "--keep-isolated",
        action="store_true",
        help="保留 alias 合并和删点后产生的非疾病孤立节点；默认会清理这些节点。",
    )
    return parser.parse_args()


def resolve_alias_file_specs(alias_dir: Path, alias_files: list[str]) -> list[tuple[Path, str]]:
    if not alias_files:
        return [
            (alias_dir / filename, label)
            for filename, label in ALIAS_FILE_TO_LABEL.items()
            if (alias_dir / filename).exists()
        ]

    specs: list[tuple[Path, str]] = []
    for raw_file in alias_files:
        path = Path(raw_file).expanduser()
        if not path.is_absolute():
            path = alias_dir / path
        path = path.resolve()
        label = ALIAS_FILE_TO_LABEL.get(path.name)
        if label is None:
            raise RuntimeError(f"Unknown alias filename: {path.name}")
        specs.append((path, label))
    return specs


def load_alias_rules(
    alias_dir: Path,
    alias_files: list[str] | None = None,
) -> tuple[dict[tuple[str, str], str], set[tuple[str, str]], dict[str, Any]]:
    alias_to_canonical: dict[tuple[str, str], str] = {}
    delete_keys: set[tuple[str, str]] = set()
    alias_group_count_by_label: Counter[str] = Counter()
    delete_count_by_label: Counter[str] = Counter()
    loaded_files: list[str] = []
    conflicts: list[dict[str, str]] = []

    for path, label in resolve_alias_file_specs(alias_dir, alias_files or []):
        if not path.exists():
            raise RuntimeError(f"Alias file does not exist: {path}")

        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            continue

        loaded_files.append(str(path))
        merge_groups = payload.get("merge_groups", [])
        if isinstance(merge_groups, list):
            for group in merge_groups:
                if not isinstance(group, dict):
                    continue

                canonical_name = clean_text(group.get("canonical_name"))
                if canonical_name is None:
                    continue

                alias_group_count_by_label[label] += 1
                alias_values = [canonical_name]
                aliases = group.get("aliases")
                if isinstance(aliases, list):
                    alias_values.extend(aliases)

                for raw_alias in alias_values:
                    alias_name = clean_text(raw_alias)
                    if alias_name is None:
                        continue

                    key = (label, normalize_lookup_key(alias_name))
                    previous = alias_to_canonical.get(key)
                    if previous is not None and previous != canonical_name:
                        conflicts.append(
                            {
                                "label": label,
                                "name": alias_name,
                                "previous_canonical": previous,
                                "new_canonical": canonical_name,
                                "file": str(path),
                            }
                        )
                        continue

                    alias_to_canonical[key] = canonical_name

        delete_names = payload.get(DELETE_ACTION, [])
        if isinstance(delete_names, list):
            for raw_name in delete_names:
                name = clean_text(raw_name)
                if name is None:
                    continue

                key = (label, normalize_lookup_key(name))
                if key in alias_to_canonical:
                    conflicts.append(
                        {
                            "label": label,
                            "name": name,
                            "previous_canonical": alias_to_canonical[key],
                            "new_canonical": DELETE_ACTION,
                            "file": str(path),
                        }
                    )
                    continue

                delete_keys.add(key)
                delete_count_by_label[label] += 1

    if conflicts:
        raise RuntimeError("Conflicting alias entries: " + json.dumps(conflicts[:20], ensure_ascii=False))

    report = {
        "loaded_alias_files": loaded_files,
        "alias_group_count_by_label": dict(sorted(alias_group_count_by_label.items())),
        "delete_entry_count_by_label": dict(sorted(delete_count_by_label.items())),
    }
    return alias_to_canonical, delete_keys, report


def lookup_delete(node: dict[str, Any], delete_keys: set[tuple[str, str]]) -> str | None:
    label = clean_text(node.get("label"))
    if label is None:
        return None

    for raw_name in [node.get("canonical_name"), node.get("name")]:
        name = clean_text(raw_name)
        if name is None:
            continue
        if (label, normalize_lookup_key(name)) in delete_keys:
            return name

    return None


def resolve_alias_canonical(
    node: dict[str, Any],
    alias_to_canonical: dict[tuple[str, str], str],
) -> tuple[str, list[str]]:
    label = clean_text(node.get("label"))
    fallback = node_canonical_name(node)
    if label is None:
        return fallback, []

    candidates = [node.get("canonical_name"), node.get("name")] + list(node.get("aliases", []))
    matched: dict[str, list[str]] = defaultdict(list)
    for raw_candidate in candidates:
        candidate = clean_text(raw_candidate)
        if candidate is None:
            continue

        canonical_name = alias_to_canonical.get((label, normalize_lookup_key(candidate)))
        if canonical_name is not None:
            matched[canonical_name].append(candidate)

    if len(matched) > 1:
        raise RuntimeError(
            "Node matched multiple canonical names: "
            + json.dumps(
                {
                    "node_id": node.get("id"),
                    "label": label,
                    "name": fallback,
                    "matches": matched,
                },
                ensure_ascii=False,
            )
        )

    if matched:
        canonical_name = next(iter(matched))
        return canonical_name, matched[canonical_name]

    return fallback, []


def cleanup_isolated_nodes(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    drop_isolated_non_disease: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    connected_node_ids = {
        endpoint
        for edge in edges
        for endpoint in (clean_text(edge.get("source_id")), clean_text(edge.get("target_id")))
        if endpoint is not None
    }
    isolated_nodes = [
        node
        for node in nodes
        if clean_text(node.get("id")) not in connected_node_ids
    ]
    removed_isolated_nodes = [
        node
        for node in isolated_nodes
        if drop_isolated_non_disease and clean_text(node.get("label")) != "Disease"
    ]
    removed_ids = {
        clean_text(node.get("id"))
        for node in removed_isolated_nodes
        if clean_text(node.get("id")) is not None
    }
    kept_nodes = [
        node
        for node in nodes
        if clean_text(node.get("id")) not in removed_ids
    ]
    kept_ids = {
        clean_text(node.get("id"))
        for node in kept_nodes
        if clean_text(node.get("id")) is not None
    }
    kept_edges = [
        edge
        for edge in edges
        if clean_text(edge.get("source_id")) in kept_ids
        and clean_text(edge.get("target_id")) in kept_ids
    ]
    remaining_isolated_nodes = [
        node
        for node in kept_nodes
        if clean_text(node.get("id")) not in connected_node_ids
    ]
    return kept_nodes, kept_edges, removed_isolated_nodes, remaining_isolated_nodes


def apply_aliases(
    graph: dict[str, Any],
    alias_to_canonical: dict[tuple[str, str], str],
    delete_keys: set[tuple[str, str]],
    *,
    drop_isolated_non_disease: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, list[str]]]:
    raw_nodes = graph.get("nodes", [])
    raw_edges = graph.get("edges", [])
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise RuntimeError("Input graph must contain list fields: nodes, edges")

    canonical_nodes: dict[str, dict[str, Any]] = {}
    old_to_new_node_id: dict[str, str] = {}
    deleted_node_ids: set[str] = set()
    deleted_by_label: Counter[str] = Counter()
    renamed_by_label: Counter[str] = Counter()
    matched_delete_entries: set[tuple[str, str]] = set()
    matched_alias_values: set[tuple[str, str]] = set()
    duplicate_node_groups: dict[str, list[str]] = defaultdict(list)
    renamed_nodes: list[dict[str, str]] = []

    for raw_node in raw_nodes:
        if not isinstance(raw_node, dict):
            continue

        node = dict(raw_node)
        old_id = clean_text(node.get("id"))
        label = clean_text(node.get("label"))
        if old_id is None or label is None:
            continue

        deleted_name = lookup_delete(node, delete_keys)
        if deleted_name is not None:
            deleted_node_ids.add(old_id)
            deleted_by_label[label] += 1
            matched_delete_entries.add((label, normalize_lookup_key(deleted_name)))
            continue

        old_canonical_name = node_canonical_name(node)
        canonical_name, matched_aliases = resolve_alias_canonical(node, alias_to_canonical)
        for matched_alias in matched_aliases:
            matched_alias_values.add((label, normalize_lookup_key(matched_alias)))

        merge_key = f"{label}::{canonical_name}"
        new_id = build_canonical_node_id(merge_key)
        old_to_new_node_id[old_id] = new_id
        duplicate_node_groups[new_id].append(old_id)

        resolved_node = build_node_base(
            node=node,
            canonical_id=new_id,
            label=label,
            canonical_name=canonical_name,
        )

        if matched_aliases:
            resolved_node["name"] = canonical_name

        if canonical_name != old_canonical_name:
            renamed_by_label[label] += 1
            renamed_nodes.append(
                {
                    "id": old_id,
                    "new_id": new_id,
                    "label": label,
                    "old_name": old_canonical_name,
                    "new_name": canonical_name,
                }
            )

        if new_id not in canonical_nodes:
            canonical_nodes[new_id] = resolved_node
        else:
            merge_existing_node(canonical_nodes[new_id], resolved_node, canonical_name)
            canonical_nodes[new_id]["name"] = canonical_name

    canonical_edges: dict[str, dict[str, Any]] = {}
    deleted_incident_edge_count = 0
    skipped_unmapped_edge_count = 0
    skipped_self_loop_edge_count = 0
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

    output_nodes_before_cleanup = sort_nodes(list(canonical_nodes.values()))
    output_edges_before_cleanup = sort_edges(list(canonical_edges.values()))
    output_nodes, output_edges, removed_isolated_nodes, remaining_isolated_nodes = cleanup_isolated_nodes(
        output_nodes_before_cleanup,
        output_edges_before_cleanup,
        drop_isolated_non_disease=drop_isolated_non_disease,
    )
    node_names_by_label = export_node_names(output_nodes)

    duplicate_node_merge_groups = {
        node_id: old_ids
        for node_id, old_ids in sorted(duplicate_node_groups.items())
        if len(old_ids) > 1
    }
    missing_delete_entries = [
        {
            "label": label,
            "name": original_key,
        }
        for label, original_key in sorted(delete_keys - matched_delete_entries)
    ]
    summary = {
        **dict(graph.get("summary", {})),
        "alias_merge_source_node_count": len(raw_nodes),
        "alias_merge_source_edge_count": len(raw_edges),
        "alias_merge_deleted_node_count": len(deleted_node_ids),
        "alias_merge_deleted_node_count_by_label": dict(sorted(deleted_by_label.items())),
        "alias_merge_deleted_incident_edge_count": deleted_incident_edge_count,
        "alias_merge_renamed_node_count": len(renamed_nodes),
        "alias_merge_renamed_node_count_by_label": dict(sorted(renamed_by_label.items())),
        "alias_merge_duplicate_node_group_count": len(duplicate_node_merge_groups),
        "alias_merge_duplicate_node_merged_away_count": sum(
            len(ids) - 1 for ids in duplicate_node_merge_groups.values()
        ),
        "alias_merge_deduplicated_edge_count": (
            len(raw_edges) - deleted_incident_edge_count - len(output_edges_before_cleanup)
        ),
        "alias_merge_remapped_edge_count": remapped_edge_count,
        "alias_merge_skipped_self_loop_edge_count": skipped_self_loop_edge_count,
        "alias_merge_skipped_unmapped_edge_count": skipped_unmapped_edge_count,
        "alias_merge_drop_isolated_non_disease": drop_isolated_non_disease,
        "alias_merge_removed_isolated_node_count": len(removed_isolated_nodes),
        "alias_merge_removed_isolated_node_count_by_label": label_counts(removed_isolated_nodes),
        "alias_merge_remaining_isolated_node_count": len(remaining_isolated_nodes),
        "post_alias_merge_node_count": len(output_nodes),
        "post_alias_merge_edge_count": len(output_edges),
        "post_alias_merge_node_count_by_label": label_counts(output_nodes),
    }

    output_graph = {
        "summary": summary,
        "nodes": output_nodes,
        "edges": output_edges,
    }
    report = {
        "summary": summary,
        "pre_alias_merge_node_count_by_label": label_counts(
            [node for node in raw_nodes if isinstance(node, dict)]
        ),
        "post_alias_merge_node_count_by_label": summary["post_alias_merge_node_count_by_label"],
        "renamed_nodes": renamed_nodes,
        "missing_delete_entries": missing_delete_entries,
        "duplicate_node_merge_groups": duplicate_node_merge_groups,
        "removed_isolated_nodes": [
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
        "matched_alias_value_count": len(matched_alias_values),
    }
    return output_graph, report, node_names_by_label


def main() -> int:
    args = parse_args()
    input_graph = Path(args.input_graph).resolve()
    alias_dir = Path(args.alias_dir).resolve()
    output_graph_file = Path(args.output_graph).resolve()
    report_file = Path(args.report_file).resolve()
    node_list_file = Path(args.node_list_file).resolve()

    if not input_graph.exists():
        raise RuntimeError(f"Input graph does not exist: {input_graph}")
    if not alias_dir.exists():
        raise RuntimeError(f"Alias directory does not exist: {alias_dir}")

    graph = json.loads(input_graph.read_text(encoding="utf-8"))
    alias_to_canonical, delete_keys, alias_report = load_alias_rules(alias_dir, args.alias_file)
    output_graph, report, node_names_by_label = apply_aliases(
        graph,
        alias_to_canonical,
        delete_keys,
        drop_isolated_non_disease=not args.keep_isolated,
    )
    report.update(
        {
            **alias_report,
            "input_graph": str(input_graph),
            "alias_dir": str(alias_dir),
            "alias_files": alias_report["loaded_alias_files"] if args.alias_file else [],
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
                "alias_dir": str(alias_dir),
                "alias_files": alias_report["loaded_alias_files"] if args.alias_file else [],
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
