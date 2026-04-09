"""实现患者特征到知识图谱节点的实体链接与阈值过滤。"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable, List

from .neo4j_client import Neo4jClient
from .types import ClinicalFeatureItem, LinkedEntity


@dataclass
class EntityLinkerConfig:
    """保存实体链接阶段的主要阈值配置。"""

    entity_link_threshold: float = 0.72
    top_k_entity_matches: int = 5
    disable_kg_below_threshold: bool = True


class EntityLinker:
    """根据 mention 与图谱候选节点的相似度做链接。"""

    # 初始化实体链接器。
    def __init__(self, client: Neo4jClient, config: EntityLinkerConfig | None = None) -> None:
        self.client = client
        self.config = config or EntityLinkerConfig()

    # 将 mention 列表链接到知识图谱节点。
    def link_mentions(self, mentions: Iterable[str]) -> List[LinkedEntity]:
        linked_results: List[LinkedEntity] = []

        for mention in mentions:
            linked_results.append(self._link_single_mention(mention))

        return linked_results

    # 将临床特征直接转换为链接实体，便于后续 R1 调用。
    def link_clinical_features(self, features: Iterable[ClinicalFeatureItem]) -> List[LinkedEntity]:
        mentions = [item.normalized_name for item in features if item.status == "exist"]
        return self.link_mentions(mentions)

    # 判断当前链接结果是否整体可信。
    def has_trusted_entities(self, linked_entities: Iterable[LinkedEntity]) -> bool:
        return any(item.is_trusted for item in linked_entities)

    # 对单个 mention 做候选查询并选择最佳匹配。
    def _link_single_mention(self, mention: str) -> LinkedEntity:
        rows = self.client.run_query(
            """
            MATCH (n)
            WHERE coalesce(n.name, '') = $mention
               OR coalesce(n.canonical_name, '') = $mention
               OR any(alias IN coalesce(n.aliases, []) WHERE alias = $mention)
               OR coalesce(n.name, '') CONTAINS $mention
               OR $mention CONTAINS coalesce(n.name, '')
            RETURN n.id AS node_id,
                   labels(n)[0] AS label,
                   coalesce(n.canonical_name, n.name) AS canonical_name,
                   coalesce(n.aliases, []) AS aliases
            LIMIT $limit
            """,
            {"mention": mention, "limit": self.config.top_k_entity_matches},
        )

        if len(rows) == 0:
            return LinkedEntity(mention=mention)

        scored = sorted(
            rows,
            key=lambda item: -self._compute_similarity(mention, str(item.get("canonical_name", "")), item.get("aliases", [])),
        )
        best = scored[0]
        similarity = self._compute_similarity(mention, str(best.get("canonical_name", "")), best.get("aliases", []))

        return LinkedEntity(
            mention=mention,
            node_id=best.get("node_id"),
            canonical_name=best.get("canonical_name"),
            similarity=similarity,
            is_trusted=similarity >= self.config.entity_link_threshold,
            label=best.get("label"),
            metadata={"aliases": list(best.get("aliases", []))},
        )

    # 计算 mention 与候选标准名/别名的最佳相似度。
    def _compute_similarity(self, mention: str, canonical_name: str, aliases: list[str]) -> float:
        normalized_mention = self._normalize_text(mention)
        candidates = [canonical_name, *aliases]
        scores = [
            SequenceMatcher(None, normalized_mention, self._normalize_text(candidate)).ratio()
            for candidate in candidates
            if len(candidate) > 0
        ]

        if len(scores) == 0:
            return 0.0

        return max(scores)

    # 统一文本形式，减少空格和大小写的影响。
    def _normalize_text(self, value: str) -> str:
        return value.strip().replace(" ", "").lower()
