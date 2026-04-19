"""导出主诊断与竞争病之间的 shared / target_only / competitor_only 差异证据报告。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from neo4j.exceptions import ServiceUnavailable


# 将项目根目录加入导入路径，确保脚本可直接运行。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain.neo4j_client import Neo4jClient
from simulator.graph_audit import (
    DISEASE_LABELS,
    DiseaseGraphAuditor,
    DiseaseNode,
    differential_report_to_dict,
    write_differential_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出疾病对差异证据审计报告。")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--target-name", help="主诊断疾病名称。")
    target.add_argument("--target-id", help="主诊断疾病 node id。")

    competitor = parser.add_mutually_exclusive_group(required=True)
    competitor.add_argument("--competitor-name", help="竞争病疾病名称。")
    competitor.add_argument("--competitor-id", help="竞争病疾病 node id。")

    parser.add_argument(
        "--labels",
        default=",".join(DISEASE_LABELS),
        help="可匹配的疾病标签，逗号分隔。",
    )
    parser.add_argument("--top-k", type=int, default=80, help="每个疾病最多导出的邻接证据数量。")
    parser.add_argument(
        "--output-root",
        default=str(PROJECT_ROOT / "test_outputs" / "graph_audit" / "differential_pairs"),
        help="报告输出目录。",
    )
    return parser.parse_args()


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def resolve_one_disease(
    auditor: DiseaseGraphAuditor,
    *,
    disease_name: str | None,
    disease_id: str | None,
    labels: tuple[str, ...],
    role: str,
) -> DiseaseNode:
    diseases = auditor.find_diseases(
        disease_names=[disease_name] if disease_name else [],
        disease_ids=[disease_id] if disease_id else [],
        labels=labels,
        limit=20,
    )

    if len(diseases) == 0:
        raise ValueError(f"未找到{role}疾病节点：{disease_name or disease_id}")

    if len(diseases) > 1:
        exact = [
            disease
            for disease in diseases
            if disease.disease_name == disease_name or disease.disease_id == disease_id
        ]

        if exact:
            return exact[0]

        raise ValueError(
            f"{role}匹配到多个疾病节点，请改用 node id："
            + "；".join(f"{item.disease_name}({item.disease_id})" for item in diseases[:8])
        )

    return diseases[0]


def main() -> int:
    args = parse_args()
    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    labels = tuple(split_csv(args.labels))

    try:
        with Neo4jClient.from_env() as client:
            auditor = DiseaseGraphAuditor(client)
            target = resolve_one_disease(
                auditor,
                disease_name=args.target_name,
                disease_id=args.target_id,
                labels=labels,
                role="主诊断",
            )
            competitor = resolve_one_disease(
                auditor,
                disease_name=args.competitor_name,
                disease_id=args.competitor_id,
                labels=labels,
                role="竞争病",
            )
            report = auditor.audit_differential_pair(target, competitor, top_k=args.top_k)
            paths = write_differential_report(report, output_root)
            payload = {
                "status": "ok",
                "output_root": str(output_root),
                "target": target.disease_name,
                "competitor": competitor.disease_name,
                "summary": differential_report_to_dict(report)["summary"],
                "json": str(paths["json"]),
                "markdown": str(paths["markdown"]),
                "llm_prompt": str(paths["prompt"]),
            }
            (output_root / "latest_report.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
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
    except ValueError as exc:
        print(json.dumps({"status": "invalid_input", "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
