"""从当前 Neo4j 导出 Disease 与全类型证据关系并生成 evidence family 目录。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from neo4j.exceptions import ServiceUnavailable


# 将项目根目录加入导入路径，确保脚本可直接运行。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain.neo4j_client import Neo4jClient
from simulator.evidence_family_catalog import (
    build_disease_evidence_catalog,
    render_disease_evidence_catalog_markdown,
)


DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "test_outputs" / "evidence_family" / "disease_evidence_catalog"
DEFAULT_DISEASE_LABELS = ("Disease",)
DEFAULT_EVIDENCE_LABELS = (
    "ClinicalFinding",
    "RiskFactor",
    "PopulationGroup",
    "ClinicalAttribute",
    "LabFinding",
    "LabTest",
    "ImagingFinding",
    "Pathogen",
)
DEFAULT_RELATION_TYPES = (
    "MANIFESTS_AS",
    "HAS_LAB_FINDING",
    "HAS_IMAGING_FINDING",
    "HAS_PATHOGEN",
    "DIAGNOSED_BY",
    "REQUIRES_DETAIL",
    "COMPLICATED_BY",
    "RISK_FACTOR_FOR",
    "APPLIES_TO",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出当前 Neo4j 的疾病-全证据 family 目录。")
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="输出目录，默认写入 test_outputs/evidence_family/disease_evidence_catalog。",
    )
    parser.add_argument(
        "--disease-labels",
        default=",".join(DEFAULT_DISEASE_LABELS),
        help="作为疾病候选的 label，逗号分隔。",
    )
    parser.add_argument(
        "--evidence-labels",
        default=",".join(DEFAULT_EVIDENCE_LABELS),
        help="纳入 catalog 的证据节点 label，逗号分隔。",
    )
    parser.add_argument(
        "--relation-types",
        default=",".join(DEFAULT_RELATION_TYPES),
        help="疾病-证据关系类型，逗号分隔。",
    )
    parser.add_argument("--limit", type=int, default=10000, help="最多导出的疾病节点数量。")
    parser.add_argument(
        "--max-groups-per-disease",
        type=int,
        default=8,
        help="每个疾病最多建议多少个全局最低证据组。",
    )
    parser.add_argument(
        "--max-groups-per-evidence-group",
        type=int,
        default=2,
        help="每个 symptom/lab/imaging 等大组最多建议多少个最低证据族。",
    )
    return parser.parse_args()


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def fetch_diseases(
    client: Neo4jClient,
    *,
    disease_labels: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    """从 Neo4j 读取疾病节点基础信息。"""

    rows = client.run_query(
        """
        MATCH (d)
        WHERE any(label IN labels(d) WHERE label IN $disease_labels)
        RETURN d.id AS disease_id,
               labels(d)[0] AS disease_label,
               coalesce(d.canonical_name, d.name) AS disease_name,
               coalesce(d.aliases, []) AS aliases,
               coalesce(d.weight, 0.0) AS node_weight
        ORDER BY disease_name, disease_id
        LIMIT $limit
        """,
        {"disease_labels": disease_labels, "limit": limit},
    )
    return [
        {
            "disease_id": str(row.get("disease_id") or ""),
            "disease_label": str(row.get("disease_label") or ""),
            "disease_name": str(row.get("disease_name") or ""),
            "aliases": list(row.get("aliases") or []),
            "node_weight": float(row.get("node_weight") or 0.0),
        }
        for row in rows
        if str(row.get("disease_id") or "")
    ]


def fetch_disease_evidence_edges(
    client: Neo4jClient,
    *,
    disease_ids: list[str],
    disease_labels: list[str],
    evidence_labels: list[str],
    relation_types: list[str],
) -> list[dict[str, Any]]:
    """从 Neo4j 读取疾病与各类证据节点之间的边。"""

    if not disease_ids:
        return []

    rows = client.run_query(
        """
        MATCH (d)-[r]-(e)
        WHERE d.id IN $disease_ids
          AND any(label IN labels(d) WHERE label IN $disease_labels)
          AND any(label IN labels(e) WHERE label IN $evidence_labels)
          AND type(r) IN $relation_types
        RETURN d.id AS disease_id,
               coalesce(d.canonical_name, d.name) AS disease_name,
               e.id AS evidence_id,
               labels(e)[0] AS evidence_label,
               coalesce(e.canonical_name, e.name) AS evidence_name,
               coalesce(e.aliases, []) AS evidence_aliases,
               coalesce(e.attributes, {}) AS attributes,
               type(r) AS relation_type,
               coalesce(r.weight, 0.0) AS relation_weight,
               CASE WHEN startNode(r).id = d.id THEN 'outgoing' ELSE 'incoming' END AS direction
        ORDER BY disease_name, evidence_label, evidence_name, evidence_id
        """,
        {
            "disease_ids": disease_ids,
            "disease_labels": disease_labels,
            "evidence_labels": evidence_labels,
            "relation_types": relation_types,
        },
    )
    return _dedupe_edges(
        [
            {
                "disease_id": str(row.get("disease_id") or ""),
                "disease_name": str(row.get("disease_name") or ""),
                "evidence_id": str(row.get("evidence_id") or ""),
                "evidence_label": str(row.get("evidence_label") or ""),
                "evidence_name": str(row.get("evidence_name") or ""),
                "evidence_aliases": list(row.get("evidence_aliases") or []),
                "attributes": row.get("attributes") or {},
                "relation_type": str(row.get("relation_type") or ""),
                "relation_weight": float(row.get("relation_weight") or 0.0),
                "direction": str(row.get("direction") or ""),
            }
            for row in rows
        ]
    )


def write_catalog_outputs(catalog: dict[str, Any], output_root: Path) -> dict[str, str]:
    """写出 full catalog、证据节点清单和疾病最低证据组。"""

    output_root.mkdir(parents=True, exist_ok=True)
    catalog_json = output_root / "disease_evidence_family_catalog.json"
    catalog_md = output_root / "disease_evidence_family_catalog.md"
    evidence_nodes_json = output_root / "evidence_family_nodes.json"
    disease_requirements_json = output_root / "disease_minimum_evidence_groups.json"

    catalog_json.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    catalog_md.write_text(render_disease_evidence_catalog_markdown(catalog), encoding="utf-8")
    evidence_nodes_json.write_text(
        json.dumps(catalog.get("evidence_nodes") or [], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    disease_requirements_json.write_text(
        json.dumps(
            [
                {
                    "disease_id": disease.get("disease_id"),
                    "disease_name": disease.get("disease_name"),
                    "evidence_count": disease.get("evidence_count"),
                    "evidence_counts_by_group": disease.get("evidence_counts_by_group"),
                    "evidence_family_counts": disease.get("evidence_family_counts"),
                    "minimum_evidence_groups": disease.get("minimum_evidence_groups"),
                    "minimum_evidence_groups_by_evidence_group": disease.get(
                        "minimum_evidence_groups_by_evidence_group"
                    ),
                }
                for disease in catalog.get("diseases") or []
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "catalog_json": str(catalog_json),
        "catalog_markdown": str(catalog_md),
        "evidence_nodes_json": str(evidence_nodes_json),
        "disease_requirements_json": str(disease_requirements_json),
    }


def main() -> int:
    args = parse_args()
    output_root = Path(args.output_root).resolve()
    disease_labels = split_csv(args.disease_labels)
    evidence_labels = split_csv(args.evidence_labels)
    relation_types = split_csv(args.relation_types)

    try:
        with Neo4jClient.from_env() as client:
            diseases = fetch_diseases(client, disease_labels=disease_labels, limit=args.limit)
            edges = fetch_disease_evidence_edges(
                client,
                disease_ids=[item["disease_id"] for item in diseases],
                disease_labels=disease_labels,
                evidence_labels=evidence_labels,
                relation_types=relation_types,
            )
            catalog = build_disease_evidence_catalog(
                diseases,
                edges,
                max_groups_per_disease=args.max_groups_per_disease,
                max_groups_per_evidence_group=args.max_groups_per_evidence_group,
            )
            catalog["source"] = {
                "neo4j_uri": client.settings.uri,
                "neo4j_database": client.settings.database,
                "disease_labels": disease_labels,
                "evidence_labels": evidence_labels,
                "relation_types": relation_types,
                "exported_at": datetime.now().isoformat(timespec="seconds"),
            }
            paths = write_catalog_outputs(catalog, output_root)

            print(
                json.dumps(
                    {
                        "status": "ok",
                        "output_root": str(output_root),
                        "disease_count": catalog["disease_count"],
                        "evidence_node_count": catalog["evidence_node_count"],
                        "disease_evidence_edge_count": catalog["disease_evidence_edge_count"],
                        "unclassified_evidence_node_count": len(catalog.get("unclassified_evidence_nodes") or []),
                        **paths,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
    except ServiceUnavailable as exc:
        print(
            json.dumps(
                {
                    "status": "neo4j_unavailable",
                    "message": "无法连接 Neo4j，请确认本地数据库已启动且环境变量 NEO4J_* 正确。",
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1


def _dedupe_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for edge in edges:
        key = (
            str(edge.get("disease_id") or ""),
            str(edge.get("evidence_id") or ""),
            str(edge.get("relation_type") or ""),
            str(edge.get("direction") or ""),
        )
        if all(key):
            deduped.setdefault(key, edge)
    return list(deduped.values())


if __name__ == "__main__":
    raise SystemExit(main())
