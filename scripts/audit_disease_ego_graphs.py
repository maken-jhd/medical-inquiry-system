"""按疾病导出 1-hop 邻接证据并生成图谱审计报告。"""

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
    disease_report_to_dict,
    write_disease_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按疾病导出局部子图审计报告。")
    parser.add_argument("--disease-name", action="append", default=[], help="疾病名称，可重复传入。")
    parser.add_argument("--disease-id", action="append", default=[], help="疾病 node id，可重复传入。")
    parser.add_argument("--all", action="store_true", help="对所有候选疾病标签批量审计。")
    parser.add_argument(
        "--labels",
        default=",".join(DISEASE_LABELS),
        help="批量审计时包含的疾病标签，逗号分隔。",
    )
    parser.add_argument("--top-k", type=int, default=80, help="每个疾病最多导出的邻接证据数量。")
    parser.add_argument("--limit", type=int, default=200, help="批量模式最多审计的疾病节点数量。")
    parser.add_argument(
        "--output-root",
        default=str(PROJECT_ROOT / "test_outputs" / "graph_audit" / "disease_ego"),
        help="报告输出目录。",
    )
    return parser.parse_args()


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def main() -> int:
    args = parse_args()
    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    labels = tuple(split_csv(args.labels))

    try:
        with Neo4jClient.from_env() as client:
            auditor = DiseaseGraphAuditor(client)
            diseases = auditor.find_diseases(
                disease_names=args.disease_name,
                disease_ids=args.disease_id,
                all_candidates=args.all,
                labels=labels,
                limit=args.limit,
            )

            if len(diseases) == 0:
                print(
                    json.dumps(
                        {
                            "status": "no_disease_found",
                            "message": "没有匹配到疾病节点，请检查 disease-name / disease-id。",
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 1

            manifest = {
                "output_root": str(output_root),
                "report_count": 0,
                "reports": [],
            }

            for disease in diseases:
                report = auditor.audit_disease(disease, top_k=args.top_k)
                paths = write_disease_report(report, output_root)
                manifest["report_count"] += 1
                manifest["reports"].append(
                    {
                        "disease_id": disease.disease_id,
                        "disease_name": disease.disease_name,
                        "disease_label": disease.disease_label,
                        "summary": disease_report_to_dict(report)["summary"],
                        "json": str(paths["json"]),
                        "markdown": str(paths["markdown"]),
                        "llm_prompt": str(paths["prompt"]),
                    }
                )

            manifest_path = output_root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"status": "ok", **manifest, "manifest": str(manifest_path)}, ensure_ascii=False, indent=2))
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

