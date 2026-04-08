"""封装问诊大脑访问 Neo4j 所需的基础客户端。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from neo4j import GraphDatabase


@dataclass
class Neo4jSettings:
    """保存 Neo4j 连接参数。"""

    uri: str
    user: str
    password: str
    database: str = "neo4j"


class Neo4jClient:
    """提供查询执行、生命周期管理与上下文管理能力。"""

    # 初始化 Neo4j 驱动并保存连接配置。
    def __init__(self, settings: Neo4jSettings) -> None:
        self.settings = settings
        self._driver = GraphDatabase.driver(
            settings.uri,
            auth=(settings.user, settings.password),
        )

    # 从环境变量中读取配置并构造客户端。
    @classmethod
    def from_env(cls) -> "Neo4jClient":
        settings = Neo4jSettings(
            uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            user=os.getenv("NEO4J_USER", "neo4j"),
            password=os.getenv("NEO4J_PASSWORD", ""),
            database=os.getenv("NEO4J_DATABASE", "neo4j"),
        )
        return cls(settings)

    # 执行一条 Cypher 查询并将结果转换为字典列表。
    def run_query(self, query: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        with self._driver.session(database=self.settings.database) as session:
            result = session.run(query, params or {})
            return [dict(record) for record in result]

    # 主动关闭 Neo4j 驱动连接。
    def close(self) -> None:
        self._driver.close()

    # 进入上下文管理器时返回当前客户端实例。
    def __enter__(self) -> "Neo4jClient":
        return self

    # 离开上下文管理器时自动关闭连接。
    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
