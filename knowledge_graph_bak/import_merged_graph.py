from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from neo4j import GraphDatabase

KG_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = KG_ROOT.parent

VALID_GRAPH_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
DEFAULT_BATCH_SIZE = 500


@dataclass
class ImportConfig:
    input_file: Path
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    neo4j_database: str
    batch_size: int


def read_env_config() -> ImportConfig:
    input_file = Path(
        os.getenv(
            "MERGED_GRAPH_INPUT_FILE",
            str(PROJECT_ROOT / "test_outputs" / "alias_merge" / "merged_graph_by_aliases.json"),
        )
    ).resolve()
    neo4j_uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password = os.getenv("NEO4J_PASSWORD", "")
    neo4j_database = os.getenv("NEO4J_DATABASE", "neo4j")
    batch_size = parse_positive_int(os.getenv("NEO4J_IMPORT_BATCH_SIZE"), DEFAULT_BATCH_SIZE)
    return ImportConfig(
        input_file=input_file,
        neo4j_uri=neo4j_uri,
        neo4j_user=neo4j_user,
        neo4j_password=neo4j_password,
        neo4j_database=neo4j_database,
        batch_size=batch_size,
    )


def parse_positive_int(value: Optional[str], fallback: int) -> int:
    if value is None:
        return fallback

    try:
        parsed = int(value)
    except ValueError:
        return fallback

    if parsed > 0:
        return parsed

    return fallback


def require_safe_graph_name(value: str, kind: str) -> str:
    if not VALID_GRAPH_NAME.fullmatch(value):
        raise RuntimeError(f"Invalid {kind}: {value!r}")

    return value


def chunked(items: Sequence[Dict[str, Any]], batch_size: int) -> Iterable[List[Dict[str, Any]]]:
    for index in range(0, len(items), batch_size):
        yield list(items[index : index + batch_size])


def load_merged_graph(input_file: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    if not input_file.exists():
        raise RuntimeError(f"Input file does not exist: {input_file}")

    payload = json.loads(input_file.read_text(encoding="utf-8"))

    if not isinstance(payload, dict):
        raise RuntimeError("Merged graph file must be a JSON object.")

    nodes = payload.get("nodes")
    edges = payload.get("edges")
    summary = payload.get("summary", {})

    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise RuntimeError("Merged graph file must contain top-level 'nodes' and 'edges' arrays.")

    if not isinstance(summary, dict):
        summary = {}

    return nodes, edges, summary


def sanitize_property_value(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, list):
        sanitized_items: List[Any] = []
        item_types = set()

        for item in value:
            if item is None:
                continue

            if isinstance(item, (str, int, float, bool)):
                sanitized_items.append(item)
                item_types.add(type(item).__name__)
                continue

            return json.dumps(value, ensure_ascii=False)

        if len(item_types) > 1:
            return json.dumps(value, ensure_ascii=False)

        return sanitized_items

    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)

    return str(value)


def sanitize_graph_item(item: Dict[str, Any]) -> Dict[str, Any]:
    sanitized: Dict[str, Any] = {}

    for key, value in item.items():
        if key == "sample_context":
            if isinstance(value, dict):
                relative_path = sanitize_property_value(value.get("relative_path"))
                heading_path = sanitize_property_value(value.get("heading_path"))
                line_start = sanitize_property_value(value.get("line_start"))
                line_end = sanitize_property_value(value.get("line_end"))

                if relative_path is not None:
                    sanitized["sample_context_relative_path"] = relative_path

                if heading_path is not None:
                    sanitized["sample_context_heading_path"] = heading_path

                if line_start is not None:
                    sanitized["sample_context_line_start"] = line_start

                if line_end is not None:
                    sanitized["sample_context_line_end"] = line_end

                sanitized["sample_context_json"] = json.dumps(value, ensure_ascii=False)
            else:
                sanitized["sample_context_json"] = sanitize_property_value(value)

            continue

        sanitized_value = sanitize_property_value(value)

        if sanitized_value is not None:
            sanitized[key] = sanitized_value

    return sanitized


def prepare_node_rows(nodes: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    rows_by_label: Dict[str, List[Dict[str, Any]]] = {}

    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            continue

        label = raw_node.get("label")
        node_id = raw_node.get("id")

        if not isinstance(label, str) or not isinstance(node_id, str):
            continue

        safe_label = require_safe_graph_name(label, "label")
        sanitized = sanitize_graph_item(raw_node)
        rows_by_label.setdefault(safe_label, []).append(
            {
                "id": node_id,
                "props": sanitized,
            }
        )

    return rows_by_label


def prepare_edge_rows(edges: List[Dict[str, Any]], known_node_ids: set[str]) -> Dict[str, List[Dict[str, Any]]]:
    rows_by_type: Dict[str, List[Dict[str, Any]]] = {}

    for raw_edge in edges:
        if not isinstance(raw_edge, dict):
            continue

        edge_type = raw_edge.get("type")
        edge_id = raw_edge.get("id")
        source_id = raw_edge.get("source_id")
        target_id = raw_edge.get("target_id")

        if not all(isinstance(value, str) for value in [edge_type, edge_id, source_id, target_id]):
            continue

        if source_id not in known_node_ids or target_id not in known_node_ids:
            continue

        safe_type = require_safe_graph_name(edge_type, "relationship type")
        sanitized = sanitize_graph_item(raw_edge)
        rows_by_type.setdefault(safe_type, []).append(
            {
                "id": edge_id,
                "source_id": source_id,
                "target_id": target_id,
                "props": sanitized,
            }
        )

    return rows_by_type


def import_node_batch(tx: Any, label: str, rows: List[Dict[str, Any]]) -> None:
    query = f"""
    UNWIND $rows AS row
    MERGE (n:`{label}` {{id: row.id}})
    SET n += row.props
    """
    tx.run(query, rows=rows)


def import_edge_batch(tx: Any, edge_type: str, rows: List[Dict[str, Any]]) -> None:
    query = f"""
    UNWIND $rows AS row
    MATCH (source {{id: row.source_id}})
    MATCH (target {{id: row.target_id}})
    MERGE (source)-[r:`{edge_type}` {{id: row.id}}]->(target)
    SET r += row.props
    """
    tx.run(query, rows=rows)


def main() -> None:
    config = read_env_config()

    if len(config.neo4j_password) == 0:
        raise RuntimeError("Missing NEO4J_PASSWORD.")

    nodes, edges, summary = load_merged_graph(config.input_file)
    node_rows_by_label = prepare_node_rows(nodes)
    known_node_ids = {
        row["id"]
        for rows in node_rows_by_label.values()
        for row in rows
    }
    edge_rows_by_type = prepare_edge_rows(edges, known_node_ids)

    driver = GraphDatabase.driver(
        config.neo4j_uri,
        auth=(config.neo4j_user, config.neo4j_password),
    )

    try:
        with driver.session(database=config.neo4j_database) as session:
            session.run("RETURN 1").consume()

            imported_node_count = 0

            for label in sorted(node_rows_by_label):
                rows = node_rows_by_label[label]

                for batch in chunked(rows, config.batch_size):
                    session.execute_write(import_node_batch, label, batch)
                    imported_node_count += len(batch)

            imported_edge_count = 0

            for edge_type in sorted(edge_rows_by_type):
                rows = edge_rows_by_type[edge_type]

                for batch in chunked(rows, config.batch_size):
                    session.execute_write(import_edge_batch, edge_type, batch)
                    imported_edge_count += len(batch)
    finally:
        driver.close()

    print(f"[import] input={config.input_file}")
    print(f"[import] neo4j_uri={config.neo4j_uri}")
    print(f"[import] neo4j_database={config.neo4j_database}")
    print(
        "[import] "
        f"summary_nodes={summary.get('merged_node_count', len(nodes))} "
        f"summary_edges={summary.get('merged_edge_count', len(edges))}"
    )
    print(
        "[import] "
        f"imported_nodes={sum(len(rows) for rows in node_rows_by_label.values())} "
        f"imported_edges={sum(len(rows) for rows in edge_rows_by_type.values())} "
        f"node_labels={len(node_rows_by_label)} "
        f"edge_types={len(edge_rows_by_type)}"
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
