"""封装外部 baseline 使用的轻量 KG RAG 检索适配器。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from brain.integrations import Neo4jClient
from brain.search import GraphRetriever
from brain.state import ClinicalFeatureItem, HypothesisCandidate, SessionState

from .llm_baseline_types import BaselineObservedFeature


# 这里定义 baseline prompt 每个候选病种允许保留的证据分组配额。
# 目的不是穷举所有邻接边，而是给 LLM 一份更厚但仍可读的候选疾病证据摘要。
_PER_CANDIDATE_GROUP_LIMITS: dict[str, int] = {
    "symptom": 5,
    "lab": 4,
    "imaging": 2,
    "pathogen": 2,
    "risk": 2,
    "detail": 2,
}


# 症状反查分支第一版只纳入这些已知特征分组，避免把 detail 类长尾描述放大成噪声召回。
_SYMPTOM_RECALL_FEATURE_GROUPS = {
    "symptom",
    "risk",
    "lab",
    "imaging",
    "pathogen",
}


@dataclass
class KgRagHit:
    """表示一条可直接注入 baseline prompt 的图谱证据块。"""

    node_id: str
    name: str
    label: str
    disease_name: str
    relation_type: str
    question_type_hint: str
    evidence_cost: str
    priority: float
    acquisition_mode: str = ""
    retrieval_mode: str = "candidate_profile"
    candidate_rank: int = 0


@dataclass
class KgRagCandidateDisease:
    """表示基于已知特征从图谱反查得到的候选疾病。"""

    disease_name: str
    score: float
    matched_features: list[str] = field(default_factory=list)
    evidence_names: list[str] = field(default_factory=list)
    retrieval_mode: str = "symptom_candidate_recall"
    already_in_top3: bool = False


@dataclass
class KgRagQueryResult:
    """汇总 KG RAG baseline 当前轮要注入的证据块和候选疾病列表。"""

    hits: list[KgRagHit] = field(default_factory=list)
    candidate_diseases: list[KgRagCandidateDisease] = field(default_factory=list)
    candidate_disease_total: int = 0
    candidate_disease_has_more: bool = False
    candidate_disease_notice: str = ""


class KgRagRetriever:
    """将 GraphRetriever 的候选疾病画像整理为 baseline 可消费的 KG 上下文。"""

    def __init__(
        self,
        client: Neo4jClient,
        *,
        graph_retriever: GraphRetriever | None = None,
        candidate_limit: int = 3,
        per_candidate_profile_limit: int = 2,
        per_candidate_expected_limit: int = 2,
    ) -> None:
        self.client = client
        self.graph_retriever = graph_retriever or GraphRetriever(client)
        self.candidate_limit = max(int(candidate_limit), 1)
        self.per_candidate_profile_limit = max(int(per_candidate_profile_limit), 1)
        self.per_candidate_expected_limit = max(int(per_candidate_expected_limit), 1)

    @classmethod
    def from_env(
        cls,
        *,
        verify_connection: bool = True,
        candidate_limit: int = 3,
        per_candidate_profile_limit: int = 2,
        per_candidate_expected_limit: int = 2,
    ) -> "KgRagRetriever":
        password = str(os.getenv("NEO4J_PASSWORD") or "").strip()
        if len(password) == 0:
            raise RuntimeError("kg_rag 模式需要可用的 Neo4j 环境变量，当前缺少 NEO4J_PASSWORD。")

        client = Neo4jClient.from_env()
        try:
            if verify_connection:
                client.run_query("RETURN 1 AS ok")
        except Exception as exc:  # pragma: no cover - 真实连接错误在单测里不稳定
            client.close()
            raise RuntimeError(f"kg_rag 模式需要可用的 Neo4j 连接：{exc}") from exc

        return cls(
            client,
            candidate_limit=candidate_limit,
            per_candidate_profile_limit=per_candidate_profile_limit,
            per_candidate_expected_limit=per_candidate_expected_limit,
        )

    def close(self) -> None:
        close_fn = getattr(self.client, "close", None)
        if callable(close_fn):
            close_fn()

    def query(
        self,
        query_text: str,
        *,
        top_k: int = 4,
        candidate_disease_names: list[str] | None = None,
        observed_features: list[BaselineObservedFeature] | None = None,
        enable_symptom_candidate_recall: bool = False,
        symptom_candidate_top_k: int = 10,
        symptom_candidate_min_features: int = 2,
        disease_scope_names: list[str] | None = None,
    ) -> KgRagQueryResult:
        _ = str(query_text or "").strip()
        result = KgRagQueryResult()

        hypotheses = self._resolve_candidate_hypotheses(candidate_disease_names or [])
        if len(hypotheses) > 0:
            session_state = SessionState(session_id="baseline_kg_rag")
            raw_hits: list[KgRagHit] = []
            candidate_count = min(len(hypotheses), self.candidate_limit, max(int(top_k), 1))
            profile_limit = max(self.per_candidate_profile_limit, self._per_candidate_profile_fetch_limit())
            expected_limit = max(self.per_candidate_expected_limit, self._per_candidate_injection_budget())

            # 主分支仍保持围绕当前 top 候选疾病拉“疾病画像 + 待验证证据”。
            for candidate_rank, hypothesis in enumerate(hypotheses[:candidate_count]):
                profile_rows = self.graph_retriever.retrieve_candidate_evidence_profile(
                    hypothesis,
                    session_state,
                    top_k=profile_limit,
                    group_limit=self._per_candidate_profile_group_limit(),
                    total_limit=profile_limit,
                )
                raw_hits.extend(
                    self._rows_to_hits(
                        profile_rows,
                        disease_name=hypothesis.name,
                        retrieval_mode="candidate_profile",
                        candidate_rank=candidate_rank,
                    )
                )

                expected_rows = self.graph_retriever.retrieve_r2_expected_evidence(
                    hypothesis,
                    session_state,
                    top_k=expected_limit,
                )
                raw_hits.extend(
                    self._rows_to_hits(
                        expected_rows,
                        disease_name=hypothesis.name,
                        retrieval_mode="expected_evidence",
                        candidate_rank=candidate_rank,
                    )
                )

            merged_hits = self._merge_hits(raw_hits)
            ranked_hits = sorted(
                merged_hits,
                key=lambda item: (
                    item.candidate_rank,
                    0 if item.retrieval_mode == "expected_evidence" else 1,
                    -item.priority,
                    item.name,
                ),
            )
            result.hits = self._apply_per_candidate_group_quotas(ranked_hits)

        if enable_symptom_candidate_recall:
            result = self._attach_symptom_candidate_diseases(
                result,
                observed_features=observed_features or [],
                current_top3_names=candidate_disease_names or [],
                disease_scope_names=disease_scope_names or [],
                symptom_candidate_top_k=symptom_candidate_top_k,
                symptom_candidate_min_features=symptom_candidate_min_features,
            )

        return result

    def _attach_symptom_candidate_diseases(
        self,
        result: KgRagQueryResult,
        *,
        observed_features: list[BaselineObservedFeature],
        current_top3_names: list[str],
        disease_scope_names: list[str],
        symptom_candidate_top_k: int,
        symptom_candidate_min_features: int,
    ) -> KgRagQueryResult:
        feature_items = self._build_symptom_recall_feature_items(observed_features)
        if len(feature_items) < max(int(symptom_candidate_min_features), 1):
            return result

        scope_names = self._dedupe_names(disease_scope_names)
        fetch_limit = len(scope_names) if len(scope_names) > 0 else max(max(int(symptom_candidate_top_k), 1) * 4, 50)
        recalled_candidates = self.graph_retriever.retrieve_r1_candidates(feature_items, top_k=fetch_limit)

        scope_name_set = {self._normalize_text(item) for item in scope_names}
        filtered_candidates = [
            item
            for item in recalled_candidates
            if len(scope_name_set) == 0 or self._normalize_text(item.name) in scope_name_set
        ]
        filtered_candidates = sorted(filtered_candidates, key=lambda item: (-item.score, item.name))

        top3_name_set = {self._normalize_text(item) for item in self._dedupe_names(current_top3_names)}
        candidate_limit = max(int(symptom_candidate_top_k), 1)
        displayed_candidates = filtered_candidates[:candidate_limit]
        result.candidate_disease_total = len(filtered_candidates)
        result.candidate_disease_has_more = len(filtered_candidates) > candidate_limit
        result.candidate_disease_notice = (
            f"基于当前已知症状从图谱反查到更多候选疾病，当前仅展示前 {candidate_limit} 个。"
            if result.candidate_disease_has_more
            else ""
        )
        result.candidate_diseases = [
            KgRagCandidateDisease(
                disease_name=item.name,
                score=float(item.score),
                matched_features=[str(name) for name in item.metadata.get("evidence_names", []) if len(str(name).strip()) > 0],
                evidence_names=[str(name) for name in item.metadata.get("evidence_names", []) if len(str(name).strip()) > 0],
                retrieval_mode="symptom_candidate_recall",
                already_in_top3=self._normalize_text(item.name) in top3_name_set,
            )
            for item in displayed_candidates
        ]
        return result

    def _build_symptom_recall_feature_items(
        self,
        observed_features: list[BaselineObservedFeature],
    ) -> list[ClinicalFeatureItem]:
        feature_items: list[ClinicalFeatureItem] = []
        seen: set[str] = set()

        for feature in observed_features:
            if feature.mention_state != "present":
                continue

            feature_group = self._normalize_observed_feature_group(feature)
            if feature_group not in _SYMPTOM_RECALL_FEATURE_GROUPS:
                continue

            preferred_name = str(feature.canonical_name or feature.normalized_name).strip()
            normalized_name = str(feature.normalized_name or preferred_name).strip()
            feature_key = self._normalize_text(preferred_name or normalized_name)
            if len(feature_key) == 0 or feature_key in seen:
                continue
            seen.add(feature_key)

            feature_items.append(
                ClinicalFeatureItem(
                    name=preferred_name or normalized_name,
                    normalized_name=normalized_name or preferred_name,
                    category=feature_group,
                    mention_state="present",
                    evidence_text=str(feature.metadata.get("evidence_text") or ""),
                    node_id=feature.node_id or None,
                    metadata=dict(feature.metadata),
                )
            )

        return feature_items

    def _normalize_observed_feature_group(self, feature: BaselineObservedFeature) -> str:
        metadata_group = self._normalize_text(str(feature.metadata.get("question_group") or feature.metadata.get("category") or ""))
        if metadata_group in _SYMPTOM_RECALL_FEATURE_GROUPS:
            return metadata_group

        normalized_label = self._normalize_text(feature.label)
        if normalized_label == "clinicalfinding":
            return "symptom"
        if normalized_label in {"labfinding", "labtest"}:
            return "lab"
        if normalized_label == "imagingfinding":
            return "imaging"
        if normalized_label == "pathogen":
            return "pathogen"
        if normalized_label in {"riskfactor", "populationgroup"}:
            return "risk"
        return "detail"

    def _resolve_candidate_hypotheses(self, candidate_disease_names: list[str]) -> list[HypothesisCandidate]:
        requested_names = self._dedupe_names(candidate_disease_names)
        if len(requested_names) == 0:
            return []

        rows = self.client.run_query(
            """
            MATCH (candidate:Disease)
            WHERE coalesce(candidate.canonical_name, candidate.name) IN $candidate_names
               OR coalesce(candidate.name, '') IN $candidate_names
               OR any(alias IN coalesce(candidate.aliases, []) WHERE alias IN $candidate_names)
            RETURN candidate.id AS node_id,
                   labels(candidate)[0] AS label,
                   coalesce(candidate.canonical_name, candidate.name) AS name,
                   coalesce(candidate.weight, 0.0) AS score,
                   coalesce(candidate.aliases, []) AS aliases
            """,
            {"candidate_names": requested_names},
        )

        order_by_name = {
            self._normalize_text(name): index
            for index, name in enumerate(requested_names)
            if len(self._normalize_text(name)) > 0
        }

        hypotheses: list[HypothesisCandidate] = []
        seen_node_ids: set[str] = set()
        for row in sorted(rows, key=lambda item: self._candidate_sort_key(item, order_by_name)):
            node_id = str(row.get("node_id") or "").strip()
            if len(node_id) == 0 or node_id in seen_node_ids:
                continue
            seen_node_ids.add(node_id)
            hypotheses.append(
                HypothesisCandidate(
                    node_id=node_id,
                    name=str(row.get("name") or "").strip(),
                    label=str(row.get("label") or "Disease").strip() or "Disease",
                    score=float(row.get("score", 0.0) or 0.0),
                )
            )
        return hypotheses

    def _candidate_sort_key(self, row: dict, order_by_name: dict[str, int]) -> tuple[int, str]:
        candidate_names = [str(row.get("name") or "").strip()]
        candidate_names.extend(str(item or "").strip() for item in row.get("aliases", []))
        for name in candidate_names:
            normalized_name = self._normalize_text(name)
            if normalized_name in order_by_name:
                return order_by_name[normalized_name], normalized_name
        return len(order_by_name) + 1, self._normalize_text(str(row.get("name") or ""))

    def _rows_to_hits(
        self,
        rows: list[dict],
        *,
        disease_name: str,
        retrieval_mode: str,
        candidate_rank: int,
    ) -> list[KgRagHit]:
        hits: list[KgRagHit] = []
        for row in rows:
            node_id = str(row.get("node_id") or "").strip()
            name = str(row.get("name") or "").strip()
            if len(node_id) == 0 or len(name) == 0:
                continue
            hits.append(
                KgRagHit(
                    node_id=node_id,
                    name=name,
                    label=str(row.get("label") or "").strip(),
                    disease_name=disease_name,
                    relation_type=str(row.get("relation_type") or "").strip(),
                    question_type_hint=str(row.get("question_type_hint") or "").strip(),
                    evidence_cost=str(row.get("evidence_cost") or "").strip(),
                    priority=float(row.get("priority", 0.0) or 0.0),
                    acquisition_mode=str(row.get("acquisition_mode") or "").strip(),
                    retrieval_mode=retrieval_mode,
                    candidate_rank=candidate_rank,
                )
            )
        return hits

    def _merge_hits(self, hits: list[KgRagHit]) -> list[KgRagHit]:
        merged_by_key: dict[tuple[str, str], KgRagHit] = {}
        for hit in hits:
            key = (self._normalize_text(hit.disease_name), hit.node_id)
            existing = merged_by_key.get(key)
            if existing is None:
                merged_by_key[key] = hit
                continue

            combined_mode = existing.retrieval_mode
            if existing.retrieval_mode != hit.retrieval_mode:
                combined_mode = "candidate_profile_and_expected_evidence"

            if hit.priority > existing.priority:
                merged_by_key[key] = KgRagHit(
                    node_id=hit.node_id,
                    name=hit.name,
                    label=hit.label,
                    disease_name=hit.disease_name,
                    relation_type=hit.relation_type or existing.relation_type,
                    question_type_hint=hit.question_type_hint or existing.question_type_hint,
                    evidence_cost=hit.evidence_cost or existing.evidence_cost,
                    priority=hit.priority,
                    acquisition_mode=hit.acquisition_mode or existing.acquisition_mode,
                    retrieval_mode=combined_mode,
                    candidate_rank=min(existing.candidate_rank, hit.candidate_rank),
                )
            else:
                merged_by_key[key] = KgRagHit(
                    node_id=existing.node_id,
                    name=existing.name,
                    label=existing.label,
                    disease_name=existing.disease_name,
                    relation_type=existing.relation_type or hit.relation_type,
                    question_type_hint=existing.question_type_hint or hit.question_type_hint,
                    evidence_cost=existing.evidence_cost or hit.evidence_cost,
                    priority=existing.priority,
                    acquisition_mode=existing.acquisition_mode or hit.acquisition_mode,
                    retrieval_mode=combined_mode,
                    candidate_rank=min(existing.candidate_rank, hit.candidate_rank),
                )
        return list(merged_by_key.values())

    def _apply_per_candidate_group_quotas(self, ranked_hits: list[KgRagHit]) -> list[KgRagHit]:
        selected_hits: list[KgRagHit] = []
        group_counts_by_candidate: dict[tuple[int, str], dict[str, int]] = {}

        for hit in ranked_hits:
            candidate_key = (hit.candidate_rank, self._normalize_text(hit.disease_name))
            question_group = self._normalize_hit_group(hit)
            group_limit = _PER_CANDIDATE_GROUP_LIMITS.get(question_group, 0)
            if group_limit <= 0:
                continue

            counts = group_counts_by_candidate.setdefault(candidate_key, {})
            if counts.get(question_group, 0) >= group_limit:
                continue

            selected_hits.append(hit)
            counts[question_group] = counts.get(question_group, 0) + 1

        return selected_hits

    def _normalize_hit_group(self, hit: KgRagHit) -> str:
        normalized_hint = self._normalize_text(hit.question_type_hint)
        if normalized_hint in _PER_CANDIDATE_GROUP_LIMITS:
            return normalized_hint

        if hit.label in {"LabFinding", "LabTest"}:
            if hit.acquisition_mode == "needs_imaging":
                return "imaging"
            if hit.acquisition_mode == "needs_pathogen_test":
                return "pathogen"
            return "lab"

        if hit.label == "ImagingFinding":
            return "imaging"

        if hit.label == "Pathogen":
            return "pathogen"

        if hit.label in {"RiskFactor", "PopulationGroup"} or hit.relation_type == "RISK_FACTOR_FOR":
            return "risk"

        if hit.label == "ClinicalFinding":
            return "symptom"

        return "detail"

    def _per_candidate_injection_budget(self) -> int:
        return sum(_PER_CANDIDATE_GROUP_LIMITS.values())

    def _per_candidate_profile_group_limit(self) -> int:
        return max(_PER_CANDIDATE_GROUP_LIMITS.values())

    def _per_candidate_profile_fetch_limit(self) -> int:
        # profile 路径本身会优先返回高权重 symptom/lab；这里多抓一倍，
        # 再按最终 prompt 配额裁剪，避免 risk/detail 被前几类高优先证据完全挤掉。
        return self._per_candidate_injection_budget() * 2

    def _dedupe_names(self, names: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for item in names:
            name = str(item or "").strip()
            normalized_name = self._normalize_text(name)
            if len(normalized_name) == 0 or normalized_name in seen:
                continue
            seen.add(normalized_name)
            deduped.append(name)
        return deduped

    def _normalize_text(self, value: str) -> str:
        return str(value or "").strip().replace(" ", "").replace("-", "").replace("_", "").lower()