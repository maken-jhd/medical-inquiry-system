"""负责从 Neo4j 图谱中执行 R1/R2 双向检索。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence

from .neo4j_client import Neo4jClient
from .types import (
    ClinicalFeatureItem,
    HypothesisCandidate,
    HypothesisScore,
    KeyFeature,
    LinkedEntity,
    PatientContext,
    QuestionCandidate,
    SessionState,
)


@dataclass
class RetrievalConfig:
    """保存各类检索操作的默认返回上限。"""

    cold_start_limit: int = 8
    r1_limit: int = 6
    r2_limit: int = 10
    evidence_profile_limit: int = 12
    cold_start_labels: tuple[str, ...] = (
        "RiskFactor",
        "ClinicalFinding",
        "ClinicalAttribute",
        "PopulationGroup",
    )
    r1_feature_labels: tuple[str, ...] = (
        "ClinicalFinding",
        "LabFinding",
        "LabTest",
        "ImagingFinding",
        "Pathogen",
        "ClinicalAttribute",
        "RiskFactor",
        "PopulationGroup",
    )
    r1_candidate_labels: tuple[str, ...] = (
        "Disease",
    )
    r1_relation_types: tuple[str, ...] = (
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
    r2_relation_types: tuple[str, ...] = (
        "MANIFESTS_AS",
        "HAS_LAB_FINDING",
        "HAS_IMAGING_FINDING",
        "HAS_PATHOGEN",
        "DIAGNOSED_BY",
        "REQUIRES_DETAIL",
        "COMPLICATED_BY",
        "RISK_FACTOR_FOR",
    )
    r2_target_labels: tuple[str, ...] = (
        "ClinicalFinding",
        "LabFinding",
        "LabTest",
        "ImagingFinding",
        "Pathogen",
        "ClinicalAttribute",
        "RiskFactor",
        "PopulationGroup",
    )
    evidence_profile_group_limit: int = 4
    kg_similarity_threshold: float = 0.72
    disable_kg_below_threshold: bool = True
    r1_min_semantic_score: float = 0.48


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
                   coalesce(n.acquisition_mode,
                     CASE
                       WHEN labels(n)[0] IN ['ClinicalFinding', 'RiskFactor', 'ClinicalAttribute'] THEN 'direct_ask'
                       WHEN labels(n)[0] = 'PopulationGroup' THEN 'history_known'
                       ELSE ''
                     END
                   ) AS acquisition_mode,
                   coalesce(n.evidence_cost,
                     CASE
                       WHEN labels(n)[0] IN ['ClinicalFinding', 'RiskFactor', 'ClinicalAttribute', 'PopulationGroup'] THEN 'low'
                       ELSE ''
                     END
                   ) AS evidence_cost,
                   CASE
                     WHEN labels(n)[0] IN ['RiskFactor', 'PopulationGroup'] THEN 3
                     WHEN labels(n)[0] = 'ClinicalFinding' THEN 2
                     ELSE 1
                   END AS label_priority,
                   CASE
                     WHEN coalesce(n.evidence_cost, '') = 'low' THEN 0.25
                     WHEN coalesce(n.acquisition_mode, '') IN ['direct_ask', 'history_known'] THEN 0.2
                     ELSE 0.0
                   END AS accessibility_priority
            ORDER BY label_priority DESC, accessibility_priority DESC, graph_weight DESC, name
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
                metadata={
                    "acquisition_mode": row.get("acquisition_mode", ""),
                    "evidence_cost": row.get("evidence_cost", ""),
                },
            )
            for row in rows
        ]

    # 执行论文中的 R1：从核心特征反向检索候选疾病或阶段。
    def retrieve_r1_candidates(
        self,
        linked_features: Sequence[LinkedEntity | ClinicalFeatureItem | KeyFeature],
        patient_context: PatientContext | None = None,
        session_state: SessionState | None = None,
        top_k: int | None = None,
    ) -> List[HypothesisCandidate]:
        feature_names = self._collect_positive_feature_names(linked_features, patient_context, session_state)
        limit = top_k or self.config.r1_limit
        link_similarity_map = self._build_link_similarity_map(linked_features)

        if len(feature_names) == 0:
            return []

        if self.config.disable_kg_below_threshold and self._all_link_confidence_low(linked_features):
            return []

        rows = self.client.run_query(
            """
            CALL () {
              MATCH (feature)-[r]->(candidate)
              WHERE type(r) IN $relation_types
                AND any(label IN labels(feature) WHERE label IN $feature_labels)
                AND (
                      coalesce(feature.name, '') IN $feature_names
                   OR coalesce(feature.canonical_name, '') IN $feature_names
                   OR any(alias IN coalesce(feature.aliases, []) WHERE alias IN $feature_names)
                )
                AND any(label IN labels(candidate) WHERE label IN $candidate_labels)
              RETURN candidate, feature, r, 1.0 AS direction_confidence
              UNION
              MATCH (candidate)-[r]->(feature)
              WHERE type(r) IN $relation_types
                AND any(label IN labels(feature) WHERE label IN $feature_labels)
                AND (
                      coalesce(feature.name, '') IN $feature_names
                   OR coalesce(feature.canonical_name, '') IN $feature_names
                   OR any(alias IN coalesce(feature.aliases, []) WHERE alias IN $feature_names)
                )
                AND any(label IN labels(candidate) WHERE label IN $candidate_labels)
              RETURN candidate, feature, r, 0.65 AS direction_confidence
            }
            WITH candidate,
                 collect(direction_confidence) AS direction_confidences,
                 count(r) AS relation_count,
                 count(DISTINCT feature) AS matched_feature_count,
                 collect(DISTINCT type(r)) AS relation_types,
                 collect(DISTINCT coalesce(feature.canonical_name, feature.name)) AS evidence_names,
                 collect(DISTINCT feature.id) AS evidence_node_ids
            RETURN candidate.id AS node_id,
                   labels(candidate)[0] AS label,
                   coalesce(candidate.canonical_name, candidate.name) AS name,
                   relation_count AS relation_count,
                   matched_feature_count AS matched_feature_count,
                   coalesce(candidate.weight, 0.0) AS candidate_weight,
                   reduce(total = 0.0, item IN direction_confidences | total + item) / size(direction_confidences) AS direction_confidence,
                   relation_types AS relation_types,
                   evidence_names AS evidence_names,
                   evidence_node_ids AS evidence_node_ids
            ORDER BY relation_count DESC, candidate_weight DESC, name
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
        total_feature_count = max(len(feature_names), 1)

        for row in rows:
            evidence_names = row.get("evidence_names", [])
            link_similarity = self._estimate_link_similarity(evidence_names, link_similarity_map)
            direction_confidence = float(row.get("direction_confidence", 1.0))
            matched_feature_count = int(row.get("matched_feature_count", len(evidence_names)))
            relation_types = [str(item) for item in row.get("relation_types", [])]
            semantic_score, semantic_metadata = self._score_r1_candidate(
                row,
                total_feature_count=total_feature_count,
                matched_feature_count=matched_feature_count,
                direction_confidence=direction_confidence,
                link_similarity=link_similarity,
                relation_types=relation_types,
            )

            if semantic_score < self.config.r1_min_semantic_score:
                continue

            candidates.append(
                HypothesisCandidate(
                    node_id=row["node_id"],
                    name=row["name"],
                    label=row["label"],
                    score=semantic_score,
                    reasoning=f"R1 根据核心特征 {', '.join(evidence_names[:4])} 检索到该候选。",
                    metadata={
                        "evidence_names": evidence_names,
                        "evidence_node_ids": row.get("evidence_node_ids", []),
                        "matched_feature_count": matched_feature_count,
                        "feature_coverage": semantic_metadata["feature_coverage"],
                        "relation_types": relation_types,
                        "label_prior": semantic_metadata["label_prior"],
                        "relation_specificity": semantic_metadata["relation_specificity"],
                        "generic_single_feature_penalty": semantic_metadata["generic_single_feature_penalty"],
                        "direction_confidence": direction_confidence,
                        "entity_link_similarity": link_similarity,
                        "semantic_score": semantic_score,
                        "semantic_score_breakdown": semantic_metadata,
                    },
                )
            )

        return sorted(candidates, key=lambda item: (-item.score, item.name))

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
            CALL () {
              MATCH (hyp)-[r]->(target)
              WHERE hyp.id = $hypothesis_id
                AND type(r) IN $relation_types
                AND NOT target.id IN $asked_ids
                AND NOT target.id IN $known_slot_ids
                AND any(label IN labels(target) WHERE label IN $target_labels)
              RETURN target, r, 1.0 AS direction_confidence
              UNION
              MATCH (target)-[r]->(hyp)
              WHERE hyp.id = $hypothesis_id
                AND type(r) IN $relation_types
                AND NOT target.id IN $asked_ids
                AND NOT target.id IN $known_slot_ids
                AND any(label IN labels(target) WHERE label IN $target_labels)
              RETURN target, r, 0.65 AS direction_confidence
            }
            RETURN target.id AS node_id,
                   labels(target)[0] AS label,
                   coalesce(target.canonical_name, target.name) AS name,
                   type(r) AS relation_type,
                   coalesce(r.weight, 0.0) AS relation_weight,
                   coalesce(target.weight, 0.0) AS node_weight,
                   coalesce(target.acquisition_mode,
                     CASE
                       WHEN labels(target)[0] IN ['ClinicalFinding', 'RiskFactor', 'ClinicalAttribute'] THEN 'direct_ask'
                       WHEN labels(target)[0] IN ['PopulationGroup'] THEN 'history_known'
                       WHEN labels(target)[0] IN ['LabFinding', 'LabTest'] THEN 'needs_lab_test'
                       WHEN labels(target)[0] = 'ImagingFinding' THEN 'needs_imaging'
                       WHEN labels(target)[0] = 'Pathogen' THEN 'needs_pathogen_test'
                       WHEN labels(target)[0] = 'ClinicalAttribute' THEN 'direct_ask'
                       ELSE ''
                     END
                   ) AS acquisition_mode,
                   coalesce(target.evidence_cost,
                     CASE
                       WHEN labels(target)[0] IN ['ClinicalFinding', 'RiskFactor', 'PopulationGroup', 'ClinicalAttribute'] THEN 'low'
                       WHEN labels(target)[0] IN ['LabFinding', 'LabTest', 'ImagingFinding', 'Pathogen'] THEN 'high'
                       ELSE ''
                     END
                   ) AS evidence_cost,
                   direction_confidence AS similarity_confidence,
                   CASE
                     WHEN type(r) IN ['HAS_LAB_FINDING', 'HAS_IMAGING_FINDING', 'HAS_PATHOGEN', 'DIAGNOSED_BY'] THEN 1.0
                     WHEN type(r) = 'MANIFESTS_AS' THEN 0.85
                     WHEN type(r) = 'REQUIRES_DETAIL' THEN 0.55
                     ELSE 0.45
                   END AS contradiction_priority,
                   CASE
                     WHEN labels(target)[0] IN ['LabFinding', 'LabTest'] THEN 'lab'
                     WHEN labels(target)[0] = 'ImagingFinding' THEN 'imaging'
                     WHEN labels(target)[0] = 'Pathogen' THEN 'pathogen'
                     WHEN labels(target)[0] IN ['RiskFactor', 'PopulationGroup'] THEN 'risk'
                     WHEN labels(target)[0] = 'ClinicalAttribute' THEN 'detail'
                     ELSE 'symptom'
                   END AS question_type_hint,
                   (coalesce(target.weight, 0.0) + coalesce(r.weight, 0.0)) * direction_confidence AS priority,
                   CASE
                     WHEN labels(target)[0] IN ['ClinicalFinding', 'LabFinding', 'ImagingFinding'] THEN true
                     ELSE false
                   END AS is_red_flag,
                   labels(target)[0] AS topic_id
            ORDER BY priority DESC, contradiction_priority DESC, name
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

    # 面向前端解释展示：返回候选诊断的关键证据画像，不过滤已问/已知证据。
    def retrieve_candidate_evidence_profile(
        self,
        hypothesis: HypothesisCandidate | HypothesisScore,
        session_state: SessionState,
        top_k: int | None = None,
    ) -> list[dict]:
        limit = top_k or self.config.evidence_profile_limit
        rows = self.client.run_query(
            """
            CALL () {
              MATCH (hyp)-[r]->(target)
              WHERE hyp.id = $hypothesis_id
                AND type(r) IN $relation_types
                AND any(label IN labels(target) WHERE label IN $target_labels)
              RETURN target, r, 1.0 AS direction_confidence
              UNION
              MATCH (target)-[r]->(hyp)
              WHERE hyp.id = $hypothesis_id
                AND type(r) IN $relation_types
                AND any(label IN labels(target) WHERE label IN $target_labels)
              RETURN target, r, 0.65 AS direction_confidence
            }
            RETURN target.id AS node_id,
                   labels(target)[0] AS label,
                   coalesce(target.canonical_name, target.name) AS name,
                   type(r) AS relation_type,
                   coalesce(r.weight, 0.0) AS relation_weight,
                   coalesce(target.weight, 0.0) AS node_weight,
                   coalesce(target.acquisition_mode,
                     CASE
                       WHEN labels(target)[0] IN ['ClinicalFinding', 'RiskFactor', 'ClinicalAttribute'] THEN 'direct_ask'
                       WHEN labels(target)[0] IN ['PopulationGroup'] THEN 'history_known'
                       WHEN labels(target)[0] IN ['LabFinding', 'LabTest'] THEN 'needs_lab_test'
                       WHEN labels(target)[0] = 'ImagingFinding' THEN 'needs_imaging'
                       WHEN labels(target)[0] = 'Pathogen' THEN 'needs_pathogen_test'
                       WHEN labels(target)[0] = 'ClinicalAttribute' THEN 'direct_ask'
                       ELSE ''
                     END
                   ) AS acquisition_mode,
                   coalesce(target.evidence_cost,
                     CASE
                       WHEN labels(target)[0] IN ['ClinicalFinding', 'RiskFactor', 'PopulationGroup', 'ClinicalAttribute'] THEN 'low'
                       WHEN labels(target)[0] IN ['LabFinding', 'LabTest', 'ImagingFinding', 'Pathogen'] THEN 'high'
                       ELSE ''
                     END
                   ) AS evidence_cost,
                   direction_confidence AS similarity_confidence,
                   CASE
                     WHEN labels(target)[0] IN ['LabFinding', 'LabTest'] THEN 'lab'
                     WHEN labels(target)[0] = 'ImagingFinding' THEN 'imaging'
                     WHEN labels(target)[0] = 'Pathogen' THEN 'pathogen'
                     WHEN labels(target)[0] IN ['RiskFactor', 'PopulationGroup'] THEN 'risk'
                     WHEN labels(target)[0] = 'ClinicalAttribute' THEN 'detail'
                     ELSE 'symptom'
                   END AS question_type_hint,
                   CASE
                     WHEN type(r) IN ['HAS_LAB_FINDING', 'HAS_IMAGING_FINDING', 'HAS_PATHOGEN', 'DIAGNOSED_BY'] THEN 1.0
                     WHEN type(r) = 'MANIFESTS_AS' THEN 0.85
                     WHEN type(r) = 'REQUIRES_DETAIL' THEN 0.55
                     ELSE 0.45
                   END AS relation_specificity,
                   (coalesce(target.weight, 0.0) + coalesce(r.weight, 0.0)) * direction_confidence AS priority
            ORDER BY priority DESC, relation_specificity DESC, name
            LIMIT $limit
            """,
            {
                "hypothesis_id": hypothesis.node_id,
                "relation_types": list(self.config.r2_relation_types),
                "target_labels": list(self.config.r2_target_labels),
                "limit": max(limit * 2, limit),
            },
        )
        deduped = self._dedupe_profile_rows(rows)
        enriched: list[dict] = []

        for row in deduped[:limit]:
            status_payload = self._resolve_evidence_profile_status(row, session_state)
            group_key = self._profile_group_key(row)
            enriched.append(
                {
                    **row,
                    "question_type_hint": group_key,
                    "group": group_key,
                    **status_payload,
                }
            )

        return self._limit_profile_groups(enriched)

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
        candidates = self.retrieve_r1_candidates([], None, session_state, top_k)
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
                metadata={
                    "relation_type": row.get("relation_type"),
                    "acquisition_mode": row.get("acquisition_mode", ""),
                    "evidence_cost": row.get("evidence_cost", ""),
                },
            )
            for row in rows
        ]

    # 从 A1 核心特征、实体链接结果和当前槽位中汇总阳性特征名称。
    def _collect_positive_feature_names(
        self,
        linked_features: Sequence[LinkedEntity | ClinicalFeatureItem | KeyFeature],
        patient_context: PatientContext | None = None,
        session_state: SessionState | None = None,
    ) -> List[str]:
        feature_names: list[str] = []

        for feature in linked_features:
            if isinstance(feature, LinkedEntity):
                if feature.canonical_name and feature.canonical_name not in feature_names:
                    feature_names.append(feature.canonical_name)

                if feature.mention not in feature_names:
                    feature_names.append(feature.mention)
                continue

            if feature.status != "exist":
                continue

            if feature.normalized_name not in feature_names:
                feature_names.append(feature.normalized_name)

            if feature.name not in feature_names:
                feature_names.append(feature.name)

        if patient_context is not None:
            for feature in patient_context.clinical_features:
                if feature.status != "exist":
                    continue

                if feature.normalized_name not in feature_names:
                    feature_names.append(feature.normalized_name)

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

    # 去重展示证据，保留同一节点下优先级最高的一条关系。
    def _dedupe_profile_rows(self, rows: Sequence[dict]) -> list[dict]:
        best_by_node_id: dict[str, dict] = {}

        for row in rows:
            node_id = str(row.get("node_id") or "").strip()

            if len(node_id) == 0:
                continue

            existing = best_by_node_id.get(node_id)

            if existing is None or self._profile_row_sort_score(row) > self._profile_row_sort_score(existing):
                best_by_node_id[node_id] = dict(row)

        return sorted(
            best_by_node_id.values(),
            key=lambda item: (-self._profile_row_sort_score(item), str(item.get("name") or "")),
        )

    # 展示证据优先按关系强度与节点权重排序，而不是按是否已问过过滤。
    def _profile_row_sort_score(self, row: dict) -> float:
        return (
            float(row.get("priority", 0.0))
            + float(row.get("relation_specificity", 0.0)) * 0.45
            + float(row.get("relation_weight", 0.0)) * 0.2
            + float(row.get("node_weight", 0.0)) * 0.1
        )

    # 从结构化会话状态判断某条证据当前是已命中、已否定还是待验证。
    def _resolve_evidence_profile_status(self, row: dict, session_state: SessionState) -> dict:
        node_id = str(row.get("node_id") or "").strip()
        name = str(row.get("name") or "").strip()
        matched_keys = {item for item in (node_id, name) if len(item) > 0}

        evidence_state = session_state.evidence_states.get(node_id)

        if evidence_state is not None:
            if evidence_state.existence == "exist":
                return {
                    "status": "matched",
                    "status_label": "已命中",
                    "certainty": evidence_state.certainty,
                    "evidence_text": evidence_state.reasoning,
                }

            if evidence_state.existence == "non_exist":
                return {
                    "status": "negative",
                    "status_label": "已否定",
                    "certainty": evidence_state.certainty,
                    "evidence_text": evidence_state.reasoning,
                }

        for slot in session_state.slots.values():
            slot_keys = {
                slot.node_id,
                str(slot.metadata.get("normalized_name") or ""),
                str(slot.metadata.get("target_node_name") or ""),
            }
            slot_keys = {item for item in slot_keys if len(item) > 0}

            if len(matched_keys & slot_keys) == 0:
                continue

            if slot.status == "true":
                return {
                    "status": "matched",
                    "status_label": "已命中",
                    "certainty": slot.certainty,
                    "evidence_text": "；".join(slot.evidence),
                }

            if slot.status == "false":
                return {
                    "status": "negative",
                    "status_label": "已否定",
                    "certainty": slot.certainty,
                    "evidence_text": "；".join(slot.evidence),
                }

        return {
            "status": "unknown",
            "status_label": "待验证",
            "certainty": "unknown",
            "evidence_text": "",
        }

    # 将图谱标签 / 问题类型映射成前端分组。
    def _profile_group_key(self, row: dict) -> str:
        question_type = str(row.get("question_type_hint") or "").strip()
        label = str(row.get("label") or "").strip()
        relation_type = str(row.get("relation_type") or "").strip()
        acquisition_mode = str(row.get("acquisition_mode") or "").strip()

        if label in {"LabFinding", "LabTest"} and acquisition_mode == "needs_imaging":
            return "imaging"

        if label in {"LabFinding", "LabTest"} and acquisition_mode == "needs_pathogen_test":
            return "pathogen"

        if question_type in {"symptom", "risk", "lab", "imaging", "pathogen", "detail"}:
            return question_type

        if label == "ClinicalFinding":
            return "symptom"

        if label in {"RiskFactor", "PopulationGroup"} or relation_type == "RISK_FACTOR_FOR":
            return "risk"

        if label in {"LabFinding", "LabTest"} or relation_type == "HAS_LAB_FINDING":
            return "lab"

        if label == "ImagingFinding" or relation_type == "HAS_IMAGING_FINDING":
            return "imaging"

        if label == "Pathogen" or relation_type == "HAS_PATHOGEN":
            return "pathogen"

        return "detail"

    # 控制每个诊断卡片里的证据数量，避免前端过长。
    def _limit_profile_groups(self, rows: Sequence[dict]) -> list[dict]:
        group_counts: dict[str, int] = {}
        limited: list[dict] = []

        for row in rows:
            group = str(row.get("group") or "detail")
            count = group_counts.get(group, 0)

            if count >= self.config.evidence_profile_group_limit:
                continue

            limited.append(dict(row))
            group_counts[group] = count + 1

            if len(limited) >= self.config.evidence_profile_limit:
                break

        return limited

    # 判断当前链接实体是否整体都低于可信阈值。
    def _all_link_confidence_low(
        self,
        linked_features: Sequence[LinkedEntity | ClinicalFeatureItem | KeyFeature],
    ) -> bool:
        scores: list[float] = []

        for feature in linked_features:
            if isinstance(feature, LinkedEntity):
                scores.append(feature.similarity)

        if len(scores) == 0:
            return False

        return max(scores) < self.config.kg_similarity_threshold

    # 汇总已链接实体的相似度，供 R1 候选分数融合。
    def _build_link_similarity_map(
        self,
        linked_features: Sequence[LinkedEntity | ClinicalFeatureItem | KeyFeature],
    ) -> dict[str, float]:
        similarity_map: dict[str, float] = {}

        for feature in linked_features:
            if not isinstance(feature, LinkedEntity):
                continue

            if feature.canonical_name:
                similarity_map[feature.canonical_name] = max(
                    feature.similarity,
                    similarity_map.get(feature.canonical_name, 0.0),
                )

            similarity_map[feature.mention] = max(feature.similarity, similarity_map.get(feature.mention, 0.0))

        return similarity_map

    # 估计候选假设所依赖特征的实体链接可信度。
    def _estimate_link_similarity(
        self,
        evidence_names: Sequence[str],
        similarity_map: dict[str, float],
    ) -> float:
        scores = [
            similarity_map[name]
            for name in evidence_names
            if name in similarity_map
        ]

        if len(scores) == 0:
            return 0.0

        return sum(scores) / len(scores)

    # 对单个 R1 候选进行更严格的语义评分，降低泛化候选和单证据弱候选的排序位置。
    def _score_r1_candidate(
        self,
        row: dict,
        total_feature_count: int,
        matched_feature_count: int,
        direction_confidence: float,
        link_similarity: float,
        relation_types: Sequence[str],
    ) -> tuple[float, dict]:
        feature_coverage = min(matched_feature_count / total_feature_count, 1.0)
        candidate_weight = min(float(row.get("candidate_weight", 0.0)), 1.0)
        relation_count = min(float(row.get("relation_count", 0.0)) / max(total_feature_count, 1), 1.0)
        relation_specificity = self._compute_relation_specificity(relation_types)
        label_prior = self._label_prior(str(row.get("label", "")))
        generic_single_feature_penalty = self._generic_single_feature_penalty(
            label=str(row.get("label", "")),
            matched_feature_count=matched_feature_count,
            total_feature_count=total_feature_count,
            relation_types=relation_types,
        )

        score = (
            feature_coverage * 0.34
            + relation_count * 0.18
            + relation_specificity * 0.16
            + label_prior * 0.14
            + direction_confidence * 0.10
            + candidate_weight * 0.04
            + link_similarity * 0.04
            - generic_single_feature_penalty
        )
        score = max(score, 0.0)

        return score, {
            "feature_coverage": feature_coverage,
            "relation_count_ratio": relation_count,
            "relation_specificity": relation_specificity,
            "label_prior": label_prior,
            "direction_confidence": direction_confidence,
            "candidate_weight": candidate_weight,
            "entity_link_similarity": link_similarity,
            "generic_single_feature_penalty": generic_single_feature_penalty,
        }

    # 根据关系类型估计该候选的语义支持强度。
    def _compute_relation_specificity(self, relation_types: Sequence[str]) -> float:
        if len(relation_types) == 0:
            return 0.0

        weights = {
            "DIAGNOSED_BY": 1.0,
            "HAS_LAB_FINDING": 0.95,
            "HAS_IMAGING_FINDING": 0.95,
            "HAS_PATHOGEN": 0.92,
            "MANIFESTS_AS": 0.9,
            "REQUIRES_DETAIL": 0.55,
            "RISK_FACTOR_FOR": 0.5,
            "COMPLICATED_BY": 0.3,
            "APPLIES_TO": 0.2,
        }
        scores = [weights.get(item, 0.25) for item in relation_types]
        return sum(scores) / len(scores)

    # 候选诊断已统一为 Disease；仅保留非 Disease 兜底降权，避免旧标签继续影响排序。
    def _label_prior(self, label: str) -> float:
        return 1.0 if label == "Disease" else 0.7

    # 对“总特征较多但只吃到一个泛化证据”的候选做额外降权。
    def _generic_single_feature_penalty(
        self,
        label: str,
        matched_feature_count: int,
        total_feature_count: int,
        relation_types: Sequence[str],
    ) -> float:
        if total_feature_count <= 1 or matched_feature_count > 1:
            return 0.0

        if label != "Disease":
            return 0.0

        weak_relation_types = {"APPLIES_TO", "COMPLICATED_BY", "RISK_FACTOR_FOR"}

        if len(relation_types) > 0 and all(item in weak_relation_types for item in relation_types):
            return 0.24

        return 0.18

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
