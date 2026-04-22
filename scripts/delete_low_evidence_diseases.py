"""根据图谱审计结果删除低证据疾病节点及其关联边。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from neo4j.exceptions import ServiceUnavailable


# 支持从仓库根目录直接执行脚本。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain.neo4j_client import Neo4jClient


DEFAULT_AUDIT_JSON = (
    PROJECT_ROOT
    / "test_outputs"
    / "graph_audit"
    / "all_diseases_20260419"
    / "low_evidence_diseases_le2.json"
)
DEFAULT_OUTPUT_REPORT = (
    PROJECT_ROOT
    / "test_outputs"
    / "graph_audit"
    / "all_diseases_20260419"
    / "delete_low_evidence_diseases_le2_report.json"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="删除 evidence_count 小于等于阈值的 Disease 节点及其关联边。")
    parser.add_argument(
        "--audit-json",
        default=str(DEFAULT_AUDIT_JSON),
        help="low_evidence_diseases_le*.json 路径。",
    )
    parser.add_argument(
        "--output-report",
        default=str(DEFAULT_OUTPUT_REPORT),
        help="删除报告 JSON 输出路径。",
    )
    parser.add_argument(
        "--max-evidence-count",
        type=int,
        default=2,
        help="仅删除 evidence_count 小于等于该值的疾病。",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正执行删除。未传入时只 dry-run。",
    )
    return parser.parse_args()


def load_target_diseases(path: Path, max_evidence_count: int) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items", [])
    targets: list[dict[str, Any]] = []

    for item in items:
        disease_id = str(item.get("disease_id") or "").strip()
        evidence_count = int(item.get("evidence_count") or 0)

        if not disease_id or evidence_count > max_evidence_count:
            continue

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


def get_graph_counts(client: Neo4jClient) -> dict[str, int]:
    row = client.run_query(
        """
        MATCH (n)
        WITH count(n) AS node_count
        MATCH ()-[r]->()
        WITH node_count, count(r) AS relationship_count
        MATCH (d:Disease)
        RETURN node_count,
               relationship_count,
               count(d) AS disease_count
        """
    )[0]
    return {
        "node_count": int(row.get("node_count") or 0),
        "relationship_count": int(row.get("relationship_count") or 0),
        "disease_count": int(row.get("disease_count") or 0),
    }


def inspect_targets(client: Neo4jClient, target_ids: list[str]) -> dict[str, Any]:
    rows = client.run_query(
        """
        MATCH (d:Disease)
        WHERE d.id IN $ids
        WITH collect(d) AS diseases
        UNWIND diseases AS d
        OPTIONAL MATCH (d)-[r]-()
        WITH diseases, collect(DISTINCT elementId(r)) AS rel_ids
        RETURN size(diseases) AS matched_disease_count,
               size(rel_ids) AS incident_relationship_count,
               [d IN diseases | {
                   disease_id: d.id,
                   disease_name: coalesce(d.canonical_name, d.name)
               }] AS matched_diseases
        """,
        {"ids": target_ids},
    )

    if len(rows) == 0:
        return {
            "matched_disease_count": 0,
            "incident_relationship_count": 0,
            "matched_diseases": [],
            "missing_disease_ids": sorted(set(target_ids)),
        }

    row = rows[0]
    matched = row.get("matched_diseases") or []
    matched_ids = {str(item.get("disease_id") or "") for item in matched}
    return {
        "matched_disease_count": int(row.get("matched_disease_count") or 0),
        "incident_relationship_count": int(row.get("incident_relationship_count") or 0),
        "matched_diseases": matched,
        "missing_disease_ids": sorted(set(target_ids) - matched_ids),
    }


def delete_targets(client: Neo4jClient, target_ids: list[str]) -> int:
    row = client.run_query(
        """
        MATCH (d:Disease)
        WHERE d.id IN $ids
        WITH collect(d) AS nodes, count(d) AS deleted_count
        FOREACH (node IN nodes | DETACH DELETE node)
        RETURN deleted_count
        """,
        {"ids": target_ids},
    )[0]
    return int(row.get("deleted_count") or 0)


def inspect_isolated_nodes(client: Neo4jClient) -> dict[str, Any]:
    rows = client.run_query(
        """
        MATCH (n)
        WHERE NOT (n)--()
        WITH labels(n)[0] AS label, count(n) AS count
        RETURN label, count
        ORDER BY count DESC, label
        """
    )
    total = sum(int(row.get("count") or 0) for row in rows)
    return {
        "isolated_node_count": total,
        "by_label": [
            {"label": str(row.get("label") or ""), "count": int(row.get("count") or 0)}
            for row in rows
        ],
    }


def main() -> int:
    args = parse_args()
    audit_json = Path(args.audit_json).resolve()
    output_report = Path(args.output_report).resolve()
    output_report.parent.mkdir(parents=True, exist_ok=True)

    if not audit_json.exists():
        print(json.dumps({"status": "missing_audit_json", "path": str(audit_json)}, ensure_ascii=False, indent=2))
        return 1

    targets = load_target_diseases(audit_json, args.max_evidence_count)
    target_ids = [item["disease_id"] for item in targets]

    try:
        with Neo4jClient.from_env() as client:
            before_counts = get_graph_counts(client)
            before_target_state = inspect_targets(client, target_ids)
            deleted_count = 0

            if args.apply and target_ids:
                deleted_count = delete_targets(client, target_ids)

            after_counts = get_graph_counts(client)
            after_target_state = inspect_targets(client, target_ids)
            isolated_after = inspect_isolated_nodes(client)

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

    report = {
        "status": "applied" if args.apply else "dry_run",
        "audit_json": str(audit_json),
        "max_evidence_count": args.max_evidence_count,
        "target_count_from_audit": len(targets),
        "deleted_disease_count": deleted_count,
        "before_counts": before_counts,
        "after_counts": after_counts,
        "before_target_state": before_target_state,
        "after_target_state": after_target_state,
        "isolated_after": isolated_after,
        "targets": targets,
    }
    output_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**report, "output_report": str(output_report)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
