"""负责从 Neo4j 图谱中执行 R1/R2 双向检索。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence

from .neo4j_client import Neo4jClient
from .types import (
    HypothesisCandidate,
    HypothesisScore,
    KeyFeature,
    QuestionCandidate,
    SessionState,
)


@dataclass
class RetrievalConfig:
    """保存各类检索操作的默认返回上限。"""

    cold_start_limit: int = 8
    r1_limit: int = 6
    r2_limit: int = 10
    cold_start_labels: tuple[str, ...] = (
        "RiskFactor",
        "RiskBehavior",
        "Symptom",
        "Sign",
        "ClinicalAttribute",
        "ManagementAction",
        "PopulationGroup",
    )
    r1_feature_labels: tuple[str, ...] = (
        "Symptom",
        "Sign",
        "LabFinding",
        "LabTest",
        "ClinicalAttribute",
        "RiskFactor",
        "RiskBehavior",
        "PopulationGroup",
        "ManagementAction",
    )
    r1_candidate_labels: tuple[str, ...] = (
        "Disease",
        "DiseasePhase",
        "OpportunisticInfection",
        "Comorbidity",
        "SyndromeOrComplication",
        "Tumor",
    )
    r1_relation_types: tuple[str, ...] = (
        "MANIFESTS_AS",
        "HAS_LAB_FINDING",
        "DIAGNOSED_BY",
        "REQUIRES_DETAIL",
        "ASSOCIATED_WITH",
        "COMPLICATED_BY",
        "RISK_FACTOR_FOR",
        "APPLIES_TO",
    )
    r2_relation_types: tuple[str, ...] = (
        "MANIFESTS_AS",
        "HAS_LAB_FINDING",
        "DIAGNOSED_BY",
        "REQUIRES_DETAIL",
        "ASSOCIATED_WITH",
        "COMPLICATED_BY",
        "RISK_FACTOR_FOR",
    )
    r2_target_labels: tuple[str, ...] = (
        "Symptom",
        "Sign",
        "LabFinding",
        "LabTest",
        "ClinicalAttribute",
        "RiskFactor",
        "RiskBehavior",
        "PopulationGroup",
        "ManagementAction",
    )


class GraphRetriever:
    """封装问诊过程中所需的图谱检索逻辑。"""

    # 初始化图谱检索器并保存连接配置。
    def __init__(self, client: Neo4jClient, config: RetrievalConfig | None = None) -> None:
        self.client = client
        self.config = config or RetrievalConfig()

    # 在冷启动阶段返回全局优先级较高的首问候选节点。
    def get_cold_start_questions(self, top_k: int | None = None) -> List[QuestionCandidate]:
        limit = top_k or self.config.cold_start_limit
        rows = self.client.run_query(
            """
            MATCH (n)
            WHERE any(label IN labels(n) WHERE label IN $allowed_labels)
            RETURN n.id AS node_id,
                   labels(n)[0] AS label,
                   coalesce(n.canonical_name, n.name) AS name,
                   coalesce(n.weight, 0.0) AS graph_weight,
                   CASE
                     WHEN labels(n)[0] IN ['RiskFactor', 'RiskBehavior'] THEN 3
                     WHEN labels(n)[0] IN ['Symptom', 'Sign'] THEN 2
                     ELSE 1
                   END AS label_priority
            ORDER BY label_priority DESC, graph_weight DESC, name
            LIMIT $limit
            """,
            {
                "limit": limit,
                "allowed_labels": list(self.config.cold_start_labels),
            },
        )
        return [
            QuestionCandidate(
                node_id=row["node_id"],
                label=row["label"],
                name=row["name"],
                topic_id=row["label"],
                graph_weight=float(row.get("graph_weight", 0.0)),
                priority=float(row.get("label_priority", 0.0)),
            )
            for row in rows
        ]

    # 执行论文中的 R1：从核心特征反向检索候选疾病或阶段。
    def retrieve_r1_candidates(
        self,
        key_features: Sequence[KeyFeature],
        session_state: SessionState | None = None,
        top_k: int | None = None,
    ) -> List[HypothesisCandidate]:
        feature_names = self._collect_positive_feature_names(key_features, session_state)
        limit = top_k or self.config.r1_limit

        if len(feature_names) == 0:
            return []

        rows = self.client.run_query(
            """
            MATCH (feature)-[r]-(candidate)
            WHERE type(r) IN $relation_types
              AND any(label IN labels(feature) WHERE label IN $feature_labels)
              AND (
                    coalesce(feature.name, '') IN $feature_names
                 OR coalesce(feature.canonical_name, '') IN $feature_names
                 OR any(alias IN coalesce(feature.aliases, []) WHERE alias IN $feature_names)
              )
              AND any(label IN labels(candidate) WHERE label IN $candidate_labels)
            WITH candidate, feature, count(r) AS relation_count
            RETURN candidate.id AS node_id,
                   labels(candidate)[0] AS label,
                   coalesce(candidate.canonical_name, candidate.name) AS name,
                   relation_count + coalesce(candidate.weight, 0.0) AS score,
                   collect(DISTINCT coalesce(feature.canonical_name, feature.name)) AS evidence_names,
                   collect(DISTINCT feature.id) AS evidence_node_ids
            ORDER BY score DESC, name
            LIMIT $limit
            """,
            {
                "feature_names": feature_names,
                "limit": limit,
                "relation_types": list(self.config.r1_relation_types),
                "feature_labels": list(self.config.r1_feature_labels),
                "candidate_labels": list(self.config.r1_candidate_labels),
            },
        )

        candidates: List[HypothesisCandidate] = []

        for row in rows:
            candidates.append(
                HypothesisCandidate(
                    node_id=row["node_id"],
                    name=row["name"],
                    label=row["label"],
                    score=float(row["score"]),
                    reasoning=f"R1 根据核心特征 {', '.join(row.get('evidence_names', [])[:4])} 检索到该候选。",
                    metadata={
                        "evidence_names": row.get("evidence_names", []),
                        "evidence_node_ids": row.get("evidence_node_ids", []),
                    },
                )
            )

        return candidates

    # 执行论文中的 R2：从当前主假设反向检索最值得验证的证据节点。
    def retrieve_r2_expected_evidence(
        self,
        hypothesis: HypothesisCandidate | HypothesisScore,
        session_state: SessionState,
        top_k: int | None = None,
    ) -> List[dict]:
        limit = top_k or self.config.r2_limit
        asked_ids = list(session_state.asked_node_ids)
        known_slot_ids = list(session_state.slots.keys())
        hypothesis_id = hypothesis.node_id

        rows = self.client.run_query(
            """
            MATCH (hyp)-[r]-(target)
            WHERE hyp.id = $hypothesis_id
              AND type(r) IN $relation_types
              AND NOT target.id IN $asked_ids
              AND NOT target.id IN $known_slot_ids
              AND any(label IN labels(target) WHERE label IN $target_labels)
            RETURN target.id AS node_id,
                   labels(target)[0] AS label,
                   coalesce(target.canonical_name, target.name) AS name,
                   type(r) AS relation_type,
                   coalesce(target.weight, 0.0) + coalesce(r.weight, 0.0) AS priority,
                   CASE
                     WHEN labels(target)[0] IN ['Sign', 'LabFinding'] THEN true
                     ELSE false
                   END AS is_red_flag,
                   labels(target)[0] AS topic_id
            ORDER BY priority DESC, name
            LIMIT $limit
            """,
            {
                "hypothesis_id": hypothesis_id,
                "asked_ids": asked_ids,
                "known_slot_ids": known_slot_ids,
                "limit": limit,
                "relation_types": list(self.config.r2_relation_types),
                "target_labels": list(self.config.r2_target_labels),
            },
        )
        return rows

    # 对真实图谱执行一次轻量联调，验证关键标签、关系和 R1/R2 是否能正常返回结果。
    def run_live_schema_smoke_checks(self) -> dict:
        label_rows = self.client.run_query(
            """
            MATCH (n)
            UNWIND labels(n) AS label
            RETURN label, count(*) AS count
            ORDER BY count DESC, label
            LIMIT 20
            """
        )
        relation_rows = self.client.run_query(
            """
            MATCH ()-[r]->()
            RETURN type(r) AS relation_type, count(*) AS count
            ORDER BY count DESC, relation_type
            LIMIT 20
            """
        )
        cold_start = self.get_cold_start_questions(top_k=5)
        return {
            "label_rows": label_rows,
            "relation_rows": relation_rows,
            "cold_start_candidates": [
                {
                    "node_id": item.node_id,
                    "label": item.label,
                    "name": item.name,
                }
                for item in cold_start
            ],
        }

    # 兼容旧接口：根据当前阳性槽位向前检索候选疾病、阶段或并发问题。
    def get_forward_hypotheses(self, session_state: SessionState, top_k: int | None = None) -> List[HypothesisScore]:
        candidates = self.retrieve_r1_candidates([], session_state, top_k)
        return self._build_hypothesis_scores(candidates)

    # 兼容旧接口：从候选假设反向检索最值得继续验证的节点。
    def get_reverse_validation_questions(
        self,
        hypotheses: List[HypothesisScore],
        session_state: SessionState,
        top_k: int | None = None,
    ) -> List[QuestionCandidate]:
        if len(hypotheses) == 0:
            return []

        rows = self.retrieve_r2_expected_evidence(hypotheses[0], session_state, top_k)
        return [
            QuestionCandidate(
                node_id=row["node_id"],
                label=row["label"],
                name=row["name"],
                topic_id=row.get("topic_id"),
                priority=float(row.get("priority", 0.0)),
                red_flag_score=1.0 if bool(row.get("is_red_flag", False)) else 0.0,
                metadata={"relation_type": row.get("relation_type")},
            )
            for row in rows
        ]

    # 从 A1 核心特征和当前槽位中汇总阳性特征名称。
    def _collect_positive_feature_names(
        self,
        key_features: Sequence[KeyFeature],
        session_state: SessionState | None = None,
    ) -> List[str]:
        feature_names: list[str] = []

        for feature in key_features:
            if feature.status != "exist":
                continue

            if feature.normalized_name not in feature_names:
                feature_names.append(feature.normalized_name)

            if feature.name not in feature_names:
                feature_names.append(feature.name)

        if session_state is not None:
            for slot in session_state.slots.values():
                if slot.status != "true":
                    continue

                if slot.node_id not in feature_names:
                    feature_names.append(slot.node_id)

                normalized_name = slot.metadata.get("normalized_name")
                if isinstance(normalized_name, str) and normalized_name not in feature_names:
                    feature_names.append(normalized_name)

        return feature_names

    # 将 R1 候选转换为系统使用的假设分数对象。
    def _build_hypothesis_scores(self, candidates: Iterable[HypothesisCandidate]) -> List[HypothesisScore]:
        return [
            HypothesisScore(
                node_id=item.node_id,
                label=item.label,
                name=item.name,
                score=item.score,
                evidence_node_ids=list(item.metadata.get("evidence_node_ids", [])),
                metadata=dict(item.metadata),
            )
            for item in candidates
        ]
