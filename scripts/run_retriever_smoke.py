"""提供基于真实 Neo4j 图谱的 retriever 联调脚本。"""

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
from brain.retriever import GraphRetriever
from brain.types import KeyFeature, SessionState


# 解析命令行参数。
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="对真实 Neo4j 图谱执行 retriever 联调。")
    parser.add_argument(
        "--features",
        default="发热,干咳",
        help="用于执行 R1 检索的核心线索，多个值用英文逗号分隔。",
    )
    return parser.parse_args()


# 将逗号分隔的特征文本转换为 KeyFeature 列表。
def build_key_features(raw_features: str) -> list[KeyFeature]:
    features: list[KeyFeature] = []

    for raw_feature in raw_features.split(","):
        feature = raw_feature.strip()

        if len(feature) == 0:
            continue

        features.append(
            KeyFeature(
                name=feature,
                normalized_name=feature,
                status="exist",
                certainty="confident",
            )
        )

    return features


# 运行一次完整的 retriever smoke 流程。
def main() -> int:
    args = parse_args()
    key_features = build_key_features(args.features)

    try:
        with Neo4jClient.from_env() as client:
            retriever = GraphRetriever(client)
            smoke = retriever.run_live_schema_smoke_checks()
            session_state = SessionState(session_id="retriever_smoke")
            r1_candidates = retriever.retrieve_r1_candidates(key_features, session_state=session_state, top_k=5)

            payload: dict[str, object] = {
                "smoke": smoke,
                "r1_candidates": [
                    {
                        "node_id": item.node_id,
                        "label": item.label,
                        "name": item.name,
                        "score": item.score,
                        "metadata": item.metadata,
                    }
                    for item in r1_candidates
                ],
            }

            if len(r1_candidates) > 0:
                r2_rows = retriever.retrieve_r2_expected_evidence(r1_candidates[0], session_state, top_k=5)
                payload["r2_candidates"] = r2_rows

            print(json.dumps(payload, ensure_ascii=False, indent=2))
    except ServiceUnavailable as exc:
        print(
            json.dumps(
                {
                    "status": "neo4j_unavailable",
                    "message": "当前无法连接 Neo4j，请先确认本地数据库已启动且 7687 端口可访问。",
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    return 0


# 程序主入口。
if __name__ == "__main__":
    raise SystemExit(main())
