from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

KG_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = KG_ROOT.parent

DETAIL_LEVEL_ORDER = {"minimal": 0, "standard": 1, "full": 2}
ALIAS_FILE_TO_LABEL = {
    "clinical_attribute_aliases.json": "ClinicalAttribute",
    "clinical_finding_aliases.json": "ClinicalFinding",
    "comorbidity_aliases.json": "Disease",
    "disease_aliases.json": "Disease",
    "disease_phase_aliases.json": "Disease",
    "imaging_finding_aliases.json": "ImagingFinding",
    "lab_finding_aliases.json": "LabFinding",
    "lab_test_aliases.json": "LabTest",
    "opportunistic_infection_aliases.json": "Disease",
    "pathogen_aliases.json": "Pathogen",
    "population_group_aliases.json": "PopulationGroup",
    "poplilation_group_aliases.json": "PopulationGroup",
    "risk_behavior_aliases.json": "RiskFactor",
    "risk_factor_aliases.json": "RiskFactor",
    "sign_aliases.json": "ClinicalFinding",
    "symptom_aliases.json": "ClinicalFinding",
    "syndrome_or_complication_aliases.json": "Disease",
    "syndrome_or_coplication_aliases.json": "Disease",
    "tumor_aliases.json": "Disease",
}
NODE_SYSTEM_KEYS = {
    "record_type",
    "id",
    "label",
    "name",
    "canonical_name",
    "aliases",
    "occurrence_count",
    "source_node_ids",
    "source_files",
    "source_heading_paths",
    "sample_context",
    "weight",
    "detail_required",
}
EDGE_SYSTEM_KEYS = {
    "record_type",
    "id",
    "type",
    "source_id",
    "target_id",
    "occurrence_count",
    "source_edge_ids",
    "source_files",
    "source_heading_paths",
    "sample_context",
    "weight",
    "detail_required",
}


@dataclass
class MergeConfig:
    input_file: Path
    output_file: Path
    report_file: Path
    alias_dir: Path


def read_env_config() -> MergeConfig:
    input_file = Path(
        os.getenv("MERGE_INPUT_FILE", str(PROJECT_ROOT / "output_graph_test.jsonl"))
    ).resolve()
    output_root = Path(
        os.getenv("MERGE_OUTPUT_ROOT", str(PROJECT_ROOT / "test_outputs" / "alias_merge"))
    ).resolve()
    output_file = Path(
        os.getenv("MERGE_OUTPUT_FILE", str(output_root / "merged_graph_by_aliases.json"))
    ).resolve()
    report_file = Path(
        os.getenv("MERGE_REPORT_FILE", str(output_root / "merged_graph_by_aliases_report.json"))
    ).resolve()
    alias_dir = Path(os.getenv("ALIAS_DIR", str(KG_ROOT / "aliases"))).resolve()
    return MergeConfig(
        input_file=input_file,
        output_file=output_file,
        report_file=report_file,
        alias_dir=alias_dir,
    )


def clean_text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None

    cleaned = re.sub(r"[ \t]{2,}", " ", value.strip())

    if len(cleaned) == 0:
        return None

    return cleaned


def normalize_lookup_key(value: str) -> str:
    normalized = value.strip().lower()
    normalized = normalized.replace("（", "(").replace("）", ")")
    normalized = normalized.replace("，", ",").replace("：", ":")
    normalized = re.sub(r"\s+", "", normalized)
    return normalized


def merge_unique_strings(existing: List[str], new_values: List[Any]) -> List[str]:
    merged = list(existing)

    for raw_value in new_values:
        cleaned = clean_text(raw_value)

        if cleaned is None:
            continue

        if cleaned not in merged:
            merged.append(cleaned)

    return merged


def flatten_attributes(item: Dict[str, Any]) -> None:
    attributes = item.get("attributes")

    if not isinstance(attributes, dict):
        return

    for key, value in attributes.items():
        if key not in item:
            item[key] = value


def load_alias_indexes(alias_dir: Path) -> Dict[str, Dict[str, str]]:
    indexes: Dict[str, Dict[str, str]] = {}

    for filename, label in ALIAS_FILE_TO_LABEL.items():
        path = alias_dir / filename

        if not path.exists():
            continue

        payload = json.loads(path.read_text(encoding="utf-8"))
        merge_groups = payload.get("merge_groups")

        if not isinstance(merge_groups, list):
            continue

        label_index: Dict[str, str] = {}

        for group in merge_groups:
            if not isinstance(group, dict):
                continue

            canonical_name = clean_text(group.get("canonical_name"))

            if canonical_name is None:
                continue

            alias_values: List[str] = [canonical_name]
            aliases = group.get("aliases")

            if isinstance(aliases, list):
                for alias in aliases:
                    cleaned_alias = clean_text(alias)

                    if cleaned_alias is not None:
                        alias_values.append(cleaned_alias)

            for value in alias_values:
                label_index[normalize_lookup_key(value)] = canonical_name

        if len(label_index) > 0:
            indexes[label] = label_index

    return indexes


def resolve_canonical_name(
    node: Dict[str, Any],
    alias_indexes: Dict[str, Dict[str, str]],
) -> Tuple[str, str]:
    label = clean_text(node.get("label")) or "Unknown"
    alias_index = alias_indexes.get(label, {})
    candidates: List[str] = []

    for raw_value in [node.get("canonical_name"), node.get("name")] + list(node.get("aliases", [])):
        cleaned = clean_text(raw_value)

        if cleaned is not None and cleaned not in candidates:
            candidates.append(cleaned)

    for candidate in candidates:
        lookup_key = normalize_lookup_key(candidate)

        if lookup_key in alias_index:
            return alias_index[lookup_key], "alias"

    fallback_name = clean_text(node.get("canonical_name")) or clean_text(node.get("name"))

    if fallback_name is None:
        fallback_name = str(node.get("id", "unknown"))

    return fallback_name, "self"


def build_merge_key(
    label: str,
    canonical_name: str,
    node: Dict[str, Any],
    record_context: Dict[str, Any],
) -> str:
    _ = node, record_context
    return f"{label}::{canonical_name}"


def build_canonical_node_id(merge_key: str) -> str:
    digest = hashlib.sha1(merge_key.encode("utf-8")).hexdigest()[:12]
    return f"merged_node_{digest}"


def build_canonical_edge_id(edge_type: str, source_id: str, target_id: str, condition_text: str) -> str:
    digest = hashlib.sha1(
        f"{edge_type}|{source_id}|{target_id}|{condition_text}".encode("utf-8")
    ).hexdigest()[:12]
    return f"merged_edge_{digest}"


def choose_detail_required(existing_value: Any, new_value: Any) -> str:
    existing = clean_text(existing_value) or "standard"
    new = clean_text(new_value) or "standard"

    if DETAIL_LEVEL_ORDER.get(new, 1) > DETAIL_LEVEL_ORDER.get(existing, 1):
        return new

    return existing


def merge_generic_field(target: Dict[str, Any], key: str, value: Any) -> None:
    if value in [None, "", [], {}]:
        return

    if key not in target or target.get(key) in [None, "", [], {}]:
        target[key] = value
        return

    existing = target.get(key)

    if isinstance(existing, list) and isinstance(value, list):
        merged = list(existing)

        for item in value:
            if item not in merged:
                merged.append(item)

        target[key] = merged
        return

    if isinstance(existing, dict) and isinstance(value, dict):
        merged = dict(existing)

        for nested_key, nested_value in value.items():
            if nested_key not in merged or merged[nested_key] in [None, "", [], {}]:
                merged[nested_key] = nested_value
            elif merged[nested_key] != nested_value:
                variants_key = f"{nested_key}_variants"
                existing_variants = merged.get(variants_key, [])

                if not isinstance(existing_variants, list):
                    existing_variants = [existing_variants]

                for candidate in [merged[nested_key], nested_value]:
                    if candidate not in existing_variants:
                        existing_variants.append(candidate)

                merged[variants_key] = existing_variants

        target[key] = merged
        return

    if existing == value:
        return

    variants_key = f"{key}_variants"
    existing_variants = target.get(variants_key, [])

    if not isinstance(existing_variants, list):
        existing_variants = [existing_variants]

    for candidate in [existing, value]:
        if candidate not in existing_variants:
            existing_variants.append(candidate)

    target[variants_key] = existing_variants


def build_canonical_node_base(
    canonical_id: str,
    label: str,
    canonical_name: str,
    display_name: str,
    record_context: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "record_type": "canonical_node",
        "id": canonical_id,
        "label": label,
        "name": display_name,
        "canonical_name": canonical_name,
        "aliases": [],
        "occurrence_count": 0,
        "weight": 0.0,
        "detail_required": "minimal",
        "source_node_ids": [],
        "source_files": [],
        "source_heading_paths": [],
        "sample_context": {
            "relative_path": record_context.get("relative_path"),
            "heading_path": record_context.get("heading_path", []),
            "line_start": record_context.get("line_start"),
            "line_end": record_context.get("line_end"),
        },
        "merge_methods": [],
    }


def merge_canonical_node(
    canonical_entry: Dict[str, Any],
    node: Dict[str, Any],
    canonical_name: str,
    merge_method: str,
    record_context: Dict[str, Any],
) -> None:
    alias_candidates = [canonical_name, node.get("name"), node.get("canonical_name")] + list(
        node.get("aliases", [])
    )
    canonical_entry["aliases"] = merge_unique_strings(
        canonical_entry.get("aliases", []),
        alias_candidates,
    )
    canonical_entry["source_node_ids"] = merge_unique_strings(
        canonical_entry.get("source_node_ids", []),
        [node.get("id")],
    )
    canonical_entry["source_files"] = merge_unique_strings(
        canonical_entry.get("source_files", []),
        [record_context.get("relative_path")],
    )
    heading_path = record_context.get("heading_path", [])

    if isinstance(heading_path, list):
        canonical_entry["source_heading_paths"] = merge_unique_strings(
            canonical_entry.get("source_heading_paths", []),
            [" > ".join(str(part) for part in heading_path if isinstance(part, str))],
        )

    canonical_entry["merge_methods"] = merge_unique_strings(
        canonical_entry.get("merge_methods", []),
        [merge_method],
    )
    canonical_entry["occurrence_count"] = int(canonical_entry.get("occurrence_count", 0)) + 1
    canonical_entry["weight"] = max(
        float(canonical_entry.get("weight", 0.0)),
        float(node.get("weight", 0.0)),
    )
    canonical_entry["detail_required"] = choose_detail_required(
        canonical_entry.get("detail_required"),
        node.get("detail_required"),
    )

    for key, value in node.items():
        if key in NODE_SYSTEM_KEYS:
            continue

        merge_generic_field(canonical_entry, key, value)


def build_canonical_edge_base(
    edge_id: str,
    edge_type: str,
    source_id: str,
    target_id: str,
    record_context: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "record_type": "canonical_edge",
        "id": edge_id,
        "type": edge_type,
        "source_id": source_id,
        "target_id": target_id,
        "occurrence_count": 0,
        "weight": 0.0,
        "detail_required": "minimal",
        "source_edge_ids": [],
        "source_files": [],
        "source_heading_paths": [],
        "sample_context": {
            "relative_path": record_context.get("relative_path"),
            "heading_path": record_context.get("heading_path", []),
            "line_start": record_context.get("line_start"),
            "line_end": record_context.get("line_end"),
        },
    }


def merge_canonical_edge(
    canonical_entry: Dict[str, Any],
    edge: Dict[str, Any],
    record_context: Dict[str, Any],
) -> None:
    canonical_entry["source_edge_ids"] = merge_unique_strings(
        canonical_entry.get("source_edge_ids", []),
        [edge.get("id")],
    )
    canonical_entry["source_files"] = merge_unique_strings(
        canonical_entry.get("source_files", []),
        [record_context.get("relative_path")],
    )
    heading_path = record_context.get("heading_path", [])

    if isinstance(heading_path, list):
        canonical_entry["source_heading_paths"] = merge_unique_strings(
            canonical_entry.get("source_heading_paths", []),
            [" > ".join(str(part) for part in heading_path if isinstance(part, str))],
        )

    canonical_entry["occurrence_count"] = int(canonical_entry.get("occurrence_count", 0)) + 1
    canonical_entry["weight"] = max(
        float(canonical_entry.get("weight", 0.0)),
        float(edge.get("weight", 0.0)),
    )
    canonical_entry["detail_required"] = choose_detail_required(
        canonical_entry.get("detail_required"),
        edge.get("detail_required"),
    )

    for key, value in edge.items():
        if key in EDGE_SYSTEM_KEYS:
            continue

        merge_generic_field(canonical_entry, key, value)


def sort_nodes(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        nodes,
        key=lambda item: (
            str(item.get("label", "")),
            str(item.get("name", "")),
            str(item.get("id", "")),
        ),
    )


def sort_edges(edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        edges,
        key=lambda item: (
            str(item.get("type", "")),
            str(item.get("source_id", "")),
            str(item.get("target_id", "")),
            str(item.get("id", "")),
        ),
    )


def main() -> None:
    config = read_env_config()

    if not config.input_file.exists():
        raise RuntimeError(f"Input file does not exist: {config.input_file}")

    if not config.alias_dir.exists():
        raise RuntimeError(f"Alias directory does not exist: {config.alias_dir}")

    alias_indexes = load_alias_indexes(config.alias_dir)
    canonical_nodes: Dict[str, Dict[str, Any]] = {}
    canonical_edges: Dict[str, Dict[str, Any]] = {}
    stats = {
        "record_count": 0,
        "chunk_count": 0,
        "source_node_count": 0,
        "source_edge_count": 0,
        "alias_merged_node_count": 0,
        "self_merged_node_count": 0,
    }

    with config.input_file.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()

            if len(line) == 0:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Invalid JSON at line {line_number}: {exc}") from exc

            stats["record_count"] += 1

            if record.get("record_type") != "chunk_result":
                continue

            extraction = record.get("extraction")

            if not isinstance(extraction, dict):
                continue

            nodes = extraction.get("nodes", [])
            edges = extraction.get("edges", [])

            if not isinstance(nodes, list) or not isinstance(edges, list):
                continue

            stats["chunk_count"] += 1
            local_to_canonical: Dict[str, str] = {}
            record_context = {
                "relative_path": record.get("relative_path"),
                "heading_path": record.get("heading_path", []),
                "line_start": record.get("line_start"),
                "line_end": record.get("line_end"),
            }

            for raw_node in nodes:
                if not isinstance(raw_node, dict):
                    continue

                node = dict(raw_node)
                flatten_attributes(node)
                label = clean_text(node.get("label"))

                if label is None:
                    continue

                canonical_name, merge_method = resolve_canonical_name(node, alias_indexes)
                merge_key = build_merge_key(label, canonical_name, node, record_context)
                canonical_id = build_canonical_node_id(merge_key)
                local_node_id = clean_text(node.get("id"))

                if local_node_id is not None:
                    local_to_canonical[local_node_id] = canonical_id

                if canonical_id not in canonical_nodes:
                    display_name = clean_text(node.get("name")) or canonical_name
                    canonical_nodes[canonical_id] = build_canonical_node_base(
                        canonical_id=canonical_id,
                        label=label,
                        canonical_name=canonical_name,
                        display_name=display_name,
                        record_context=record_context,
                    )

                merge_canonical_node(
                    canonical_nodes[canonical_id],
                    node,
                    canonical_name,
                    merge_method,
                    record_context,
                )

                stats["source_node_count"] += 1

                if merge_method == "alias":
                    stats["alias_merged_node_count"] += 1
                else:
                    stats["self_merged_node_count"] += 1

            for raw_edge in edges:
                if not isinstance(raw_edge, dict):
                    continue

                edge = dict(raw_edge)
                flatten_attributes(edge)
                source_id = clean_text(edge.get("source_id"))
                target_id = clean_text(edge.get("target_id"))
                edge_type = clean_text(edge.get("type"))

                if source_id is None or target_id is None or edge_type is None:
                    continue

                canonical_source_id = local_to_canonical.get(source_id)
                canonical_target_id = local_to_canonical.get(target_id)

                if canonical_source_id is None or canonical_target_id is None:
                    continue

                condition_text = clean_text(edge.get("condition_text")) or ""
                canonical_edge_id = build_canonical_edge_id(
                    edge_type=edge_type,
                    source_id=canonical_source_id,
                    target_id=canonical_target_id,
                    condition_text=condition_text,
                )

                if canonical_edge_id not in canonical_edges:
                    canonical_edges[canonical_edge_id] = build_canonical_edge_base(
                        edge_id=canonical_edge_id,
                        edge_type=edge_type,
                        source_id=canonical_source_id,
                        target_id=canonical_target_id,
                        record_context=record_context,
                    )

                merge_canonical_edge(
                    canonical_edges[canonical_edge_id],
                    edge,
                    record_context,
                )
                stats["source_edge_count"] += 1

    output_payload = {
        "summary": {
            **stats,
            "alias_label_count": len(alias_indexes),
            "merged_node_count": len(canonical_nodes),
            "merged_edge_count": len(canonical_edges),
        },
        "nodes": sort_nodes(list(canonical_nodes.values())),
        "edges": sort_edges(list(canonical_edges.values())),
    }

    report_payload = {
        "input_file": str(config.input_file),
        "output_file": str(config.output_file),
        "alias_dir": str(config.alias_dir),
        "summary": output_payload["summary"],
        "alias_labels": sorted(alias_indexes.keys()),
    }

    config.output_file.parent.mkdir(parents=True, exist_ok=True)
    config.report_file.parent.mkdir(parents=True, exist_ok=True)
    config.output_file.write_text(
        json.dumps(output_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    config.report_file.write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"[merge] input={config.input_file}")
    print(f"[merge] alias_dir={config.alias_dir}")
    print(f"[merge] output={config.output_file}")
    print(f"[merge] report={config.report_file}")
    print(
        "[merge] "
        f"chunks={stats['chunk_count']} "
        f"source_nodes={stats['source_node_count']} "
        f"merged_nodes={len(canonical_nodes)} "
        f"source_edges={stats['source_edge_count']} "
        f"merged_edges={len(canonical_edges)}"
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[fatal] interrupted by user", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:  # noqa: BLE001
        print(f"[fatal] {exc}", file=sys.stderr)
        raise SystemExit(1)
