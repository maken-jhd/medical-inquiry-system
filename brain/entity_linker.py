"""实现患者特征到知识图谱节点的实体链接与阈值过滤。"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable, List

from .normalization import NameNormalizer
from .neo4j_client import Neo4jClient
from .types import ClinicalFeatureItem, LinkedEntity


@dataclass
class EntityLinkerConfig:
    """保存实体链接阶段的主要阈值配置。"""

    entity_link_threshold: float = 0.72
    top_k_entity_matches: int = 5
    disable_kg_below_threshold: bool = True
    synonym_bonus_map: dict[str, list[str]] | None = None

    # 初始化医学同义词加成表。
    def __post_init__(self) -> None:
        if self.synonym_bonus_map is None:
            self.synonym_bonus_map = {
                "发热": ["发烧", "低热", "高热"],
                "干咳": ["咳嗽"],
                "呼吸困难": ["气促", "喘不上气", "胸闷"],
            }


class EntityLinker:
    """根据 mention 与图谱候选节点的相似度做链接。"""

    # 初始化实体链接器。
    def __init__(self, client: Neo4jClient, config: EntityLinkerConfig | None = None) -> None:
        self.client = client
        self.config = config or EntityLinkerConfig()
        self.normalizer = NameNormalizer()

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
        normalized_mention = self.normalizer.normalize_graph_mention(mention)

        # 查询阶段同时覆盖：
        # - 精确 name / canonical_name
        # - alias 命中
        # - 包含关系
        # 这样能兼顾标准术语与患者口语化表述。
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
            {"mention": normalized_mention, "limit": self.config.top_k_entity_matches},
        )

        if len(rows) == 0:
            return LinkedEntity(
                mention=normalized_mention,
                metadata={"raw_mention": mention},
            )

        # 排序时统一走同一套相似度函数，避免查询阶段的命中顺序直接决定最终链接结果。
        scored = sorted(
            rows,
            key=lambda item: -self._compute_similarity(
                normalized_mention,
                str(item.get("canonical_name", "")),
                item.get("aliases", []),
            ),
        )
        best = scored[0]
        similarity = self._compute_similarity(
            normalized_mention,
            str(best.get("canonical_name", "")),
            best.get("aliases", []),
        )

        # top_matches 会挂到 metadata，方便 R1 调试“为什么链接到了这个图谱节点”。
        top_matches = [
            {
                "node_id": item.get("node_id"),
                "canonical_name": item.get("canonical_name"),
                "similarity": self._compute_similarity(
                    normalized_mention,
                    str(item.get("canonical_name", "")),
                    item.get("aliases", []),
                ),
                "label": item.get("label"),
            }
            for item in scored[: self.config.top_k_entity_matches]
        ]

        return LinkedEntity(
            mention=normalized_mention,
            node_id=best.get("node_id"),
            canonical_name=best.get("canonical_name"),
            similarity=similarity,
            is_trusted=similarity >= self.config.entity_link_threshold,
            label=best.get("label"),
            metadata={
                "raw_mention": mention,
                "aliases": list(best.get("aliases", [])),
                "top_matches": top_matches,
            },
        )

    # 计算 mention 与候选标准名/别名的最佳相似度。
    def _compute_similarity(self, mention: str, canonical_name: str, aliases: list[str]) -> float:
        normalized_mention = self._normalize_text(mention)
        candidates = [canonical_name, *aliases]
        scores: list[float] = []

        # 基础相似度来自字符串编辑相似度，
        # 再叠加 exact match / alias exact / 医学同义词的小幅加成。
        for candidate in candidates:
            if len(candidate) == 0:
                continue

            normalized_candidate = self._normalize_text(candidate)
            score = SequenceMatcher(None, normalized_mention, normalized_candidate).ratio()

            if normalized_mention == normalized_candidate:
                score += 0.2

            if candidate in aliases and mention == candidate:
                score += 0.15

            if self._is_medical_synonym(mention, candidate):
                score += 0.1

            scores.append(min(score, 1.0))

        if len(scores) == 0:
            return 0.0

        return max(scores)

    # 统一文本形式，减少空格和大小写的影响。
    def _normalize_text(self, value: str) -> str:
        return value.strip().replace(" ", "").lower()

    # 判断 mention 与候选名是否属于预置医学同义表。
    def _is_medical_synonym(self, mention: str, candidate: str) -> bool:
        synonym_bonus_map = self.config.synonym_bonus_map or {}

        for canonical_name, aliases in synonym_bonus_map.items():
            family = {canonical_name, *aliases}

            if mention in family and candidate in family:
                return True

        return False
