"""实现接入 KG RAG 上下文的多轮问诊 baseline。"""

from __future__ import annotations

from typing import Any

from brain.integrations import EntityLinker
from brain.integrations import LlmClient
from brain.turn import MedExtractor

from .kg_rag_retriever import KgRagCandidateDisease, KgRagHit, KgRagQueryResult, KgRagRetriever
from .llm_baseline_types import BaselineFinalDecision, BaselineObservedFeature, BaselineSessionState
from .llm_consultation_brain import PureLlmConsultationBrain


# 症状反查插件第一版只吸纳这些已知特征分组，避免把 detail 等低辨别度描述直接送进 R1。
_TRACKED_OBSERVED_FEATURE_GROUPS = {
    "symptom",
    "risk",
    "lab",
    "imaging",
    "pathogen",
}

_POSITIVE_DIRECT_REPLIES = {
    "有",
    "有的",
    "是",
    "是的",
    "对",
    "对的",
    "会",
    "存在",
    "嗯",
}

_UNCLEAR_DIRECT_REPLIES = {
    "不确定",
    "不太清楚",
    "不清楚",
    "说不上来",
    "没注意",
    "没有特别注意到",
    "没听医生提过",
}


class KgRagConsultationBrain(PureLlmConsultationBrain):
    """在纯 LLM baseline 上叠加轻量图谱检索上下文。"""

    def __init__(
        self,
        *,
        llm_client: LlmClient,
        retriever: KgRagRetriever,
        max_turns: int = 8,
        retrieval_top_k: int = 4,
        enable_symptom_candidate_recall: bool = False,
        symptom_candidate_top_k: int = 10,
        symptom_candidate_min_features: int = 2,
        feature_extractor: MedExtractor | None = None,
        entity_linker: EntityLinker | None = None,
        backend_name: str = "llm_kg_rag",
        disease_scope: list[str] | None = None,
    ) -> None:
        super().__init__(
            llm_client=llm_client,
            max_turns=max_turns,
            backend_name=backend_name,
            disease_scope=disease_scope,
        )
        self.retriever = retriever
        self.retrieval_top_k = max(int(retrieval_top_k), 1)
        self.enable_symptom_candidate_recall = bool(enable_symptom_candidate_recall)
        self.symptom_candidate_top_k = max(int(symptom_candidate_top_k), 1)
        self.symptom_candidate_min_features = max(int(symptom_candidate_min_features), 1)
        self.feature_extractor = feature_extractor
        self.entity_linker = entity_linker
        if self.enable_symptom_candidate_recall and self.feature_extractor is None:
            self.feature_extractor = MedExtractor(llm_client=llm_client)
        if self.enable_symptom_candidate_recall and self.entity_linker is None and hasattr(retriever, "client"):
            self.entity_linker = EntityLinker(retriever.client)
        self._latest_retrieval_context_by_session: dict[str, dict[str, Any]] = {}

    def start_session(self, session_id: str) -> BaselineSessionState:
        session = super().start_session(session_id)
        self._latest_retrieval_context_by_session[session_id] = self._empty_retrieval_context()
        return session

    # 每轮 ask/final 前按当前 top3 候选疾病拉一层图谱证据块，再注入统一 prompt 契约。
    def _decide_next_step(
        self,
        session: BaselineSessionState,
        *,
        must_finalize: bool,
    ):
        self._latest_retrieval_context_by_session[session.session_id] = self._build_retrieval_context(session)
        return super()._decide_next_step(session, must_finalize=must_finalize)

    def _build_additional_prompt_variables(
        self,
        session: BaselineSessionState,
        *,
        must_finalize: bool,
    ) -> dict[str, Any]:
        _ = must_finalize
        retrieval_context = self._get_retrieval_context(session)
        return {
            "retrieved_kg_context": list(retrieval_context.get("retrieved_kg_context", [])),
            "retrieved_kg_candidate_diseases": list(retrieval_context.get("retrieved_kg_candidate_diseases", [])),
            "retrieved_kg_candidate_disease_total": retrieval_context.get("retrieved_kg_candidate_disease_total", 0),
            "retrieved_kg_candidate_disease_has_more": retrieval_context.get("retrieved_kg_candidate_disease_has_more", False),
            "retrieved_kg_candidate_disease_notice": retrieval_context.get("retrieved_kg_candidate_disease_notice", ""),
        }

    def _selected_action_source(self) -> str:
        return "baseline_llm_kg_rag"

    def _build_additional_search_metadata(
        self,
        session: BaselineSessionState,
        *,
        decision_kind: str,
        question_group: str,
        evidence_cost: str,
        final_answer: str,
    ) -> dict[str, Any]:
        _ = decision_kind, question_group, evidence_cost, final_answer
        retrieval_context = self._get_retrieval_context(session)
        return {
            "retrieval_query": retrieval_context.get("retrieval_query", ""),
            "retrieval_top_k": retrieval_context.get("retrieval_top_k", self.retrieval_top_k),
            "retrieved_node_ids": list(retrieval_context.get("retrieved_node_ids", [])),
            "retrieved_disease_names": list(retrieval_context.get("retrieved_disease_names", [])),
            "retrieved_kg_context": list(retrieval_context.get("retrieved_kg_context", [])),
            "retrieved_kg_candidate_diseases": list(retrieval_context.get("retrieved_kg_candidate_diseases", [])),
            "retrieved_candidate_disease_names_from_symptoms": list(
                retrieval_context.get("retrieved_candidate_disease_names_from_symptoms", [])
            ),
            "retrieved_candidate_disease_count_from_symptoms": retrieval_context.get(
                "retrieved_candidate_disease_count_from_symptoms",
                0,
            ),
            "retrieved_candidate_disease_total_from_symptoms": retrieval_context.get(
                "retrieved_candidate_disease_total_from_symptoms",
                0,
            ),
            "retrieved_candidate_disease_has_more_from_symptoms": retrieval_context.get(
                "retrieved_candidate_disease_has_more_from_symptoms",
                False,
            ),
            "retrieved_candidate_disease_notice_from_symptoms": retrieval_context.get(
                "retrieved_candidate_disease_notice_from_symptoms",
                "",
            ),
            "retrieval_mode": retrieval_context.get("retrieval_mode", ""),
            "retrieved_kg_context_count": len(retrieval_context.get("retrieved_kg_context", [])),
        }

    def _build_additional_final_metadata(
        self,
        session: BaselineSessionState,
        decision: BaselineFinalDecision,
        *,
        stop_reason: str,
        forced_finalize: bool,
    ) -> dict[str, Any]:
        _ = decision, stop_reason, forced_finalize
        retrieval_context = self._get_retrieval_context(session)
        return {
            "retrieval_query": retrieval_context.get("retrieval_query", ""),
            "retrieved_node_ids": list(retrieval_context.get("retrieved_node_ids", [])),
            "retrieved_disease_names": list(retrieval_context.get("retrieved_disease_names", [])),
            "retrieved_kg_context": list(retrieval_context.get("retrieved_kg_context", [])),
            "retrieved_kg_candidate_diseases": list(retrieval_context.get("retrieved_kg_candidate_diseases", [])),
            "retrieved_candidate_disease_names_from_symptoms": list(
                retrieval_context.get("retrieved_candidate_disease_names_from_symptoms", [])
            ),
            "retrieved_candidate_disease_count_from_symptoms": retrieval_context.get(
                "retrieved_candidate_disease_count_from_symptoms",
                0,
            ),
            "retrieved_candidate_disease_total_from_symptoms": retrieval_context.get(
                "retrieved_candidate_disease_total_from_symptoms",
                0,
            ),
            "retrieved_candidate_disease_has_more_from_symptoms": retrieval_context.get(
                "retrieved_candidate_disease_has_more_from_symptoms",
                False,
            ),
            "retrieved_candidate_disease_notice_from_symptoms": retrieval_context.get(
                "retrieved_candidate_disease_notice_from_symptoms",
                "",
            ),
            "retrieval_mode": retrieval_context.get("retrieval_mode", ""),
            "retrieved_kg_context_count": len(retrieval_context.get("retrieved_kg_context", [])),
        }

    def _record_patient_observation(self, session: BaselineSessionState, patient_text: str) -> None:
        if not self.enable_symptom_candidate_recall:
            return

        observed_features: list[BaselineObservedFeature] = []
        direct_reply_state = self._classify_direct_reply(patient_text)

        # 短答只做最小肯定写入：沿用上一轮 target_name，不在 baseline 里重建完整极性解析器。
        if direct_reply_state == "present":
            direct_reply_feature = self._build_direct_reply_feature(session, patient_text)
            if direct_reply_feature is not None:
                observed_features.append(direct_reply_feature)
        elif self._should_extract_observed_features_from_free_text(session, patient_text):
            observed_features.extend(self._extract_observed_features_from_free_text(session, patient_text))

        self._merge_observed_features(session, observed_features)

    def _build_retrieval_context(self, session: BaselineSessionState) -> dict[str, Any]:
        retrieval_query = self._build_retrieval_query(session)
        candidate_disease_names = [item.name for item in session.last_model_top3 if len(item.name) > 0]
        query_result = self.retriever.query(
            retrieval_query,
            top_k=self.retrieval_top_k,
            candidate_disease_names=candidate_disease_names,
            observed_features=list(session.observed_features),
            enable_symptom_candidate_recall=self.enable_symptom_candidate_recall,
            symptom_candidate_top_k=self.symptom_candidate_top_k,
            symptom_candidate_min_features=self.symptom_candidate_min_features,
            disease_scope_names=list(self.disease_scope),
        )
        retrieved_kg_context = [self._hit_to_prompt_payload(item) for item in query_result.hits]
        retrieved_kg_candidate_diseases = [
            self._candidate_disease_to_prompt_payload(item)
            for item in query_result.candidate_diseases
        ]
        retrieval_modes = self._dedupe_values(
            [item.retrieval_mode for item in query_result.hits]
            + [item.retrieval_mode for item in query_result.candidate_diseases]
        )
        return {
            "retrieval_query": retrieval_query,
            "retrieval_top_k": self.retrieval_top_k,
            "retrieved_kg_context": retrieved_kg_context,
            "retrieved_kg_candidate_diseases": retrieved_kg_candidate_diseases,
            "retrieved_kg_candidate_disease_total": query_result.candidate_disease_total,
            "retrieved_kg_candidate_disease_has_more": query_result.candidate_disease_has_more,
            "retrieved_kg_candidate_disease_notice": query_result.candidate_disease_notice,
            "retrieved_node_ids": [item.node_id for item in query_result.hits],
            "retrieved_disease_names": self._dedupe_values(item.disease_name for item in query_result.hits),
            "retrieved_candidate_disease_names_from_symptoms": self._dedupe_values(
                item.disease_name for item in query_result.candidate_diseases
            ),
            "retrieved_candidate_disease_count_from_symptoms": len(query_result.candidate_diseases),
            "retrieved_candidate_disease_total_from_symptoms": query_result.candidate_disease_total,
            "retrieved_candidate_disease_has_more_from_symptoms": query_result.candidate_disease_has_more,
            "retrieved_candidate_disease_notice_from_symptoms": query_result.candidate_disease_notice,
            "retrieval_mode": retrieval_modes[0] if len(retrieval_modes) == 1 else "mixed",
        }

    def _build_retrieval_query(self, session: BaselineSessionState) -> str:
        recent_turn_texts = [turn.text.strip() for turn in session.dialogue_history[-4:] if len(turn.text.strip()) > 0]
        top3_names = [item.name for item in session.last_model_top3[:3] if len(item.name.strip()) > 0]
        if top3_names:
            recent_turn_texts.append(f"当前候选：{' / '.join(top3_names)}")
        return " ".join(recent_turn_texts).strip()

    def _hit_to_prompt_payload(self, hit: KgRagHit) -> dict[str, Any]:
        return {
            "node_id": hit.node_id,
            "name": hit.name,
            "label": hit.label,
            "disease_name": hit.disease_name,
            "relation_type": hit.relation_type,
            "question_type_hint": hit.question_type_hint,
            "evidence_cost": hit.evidence_cost,
            "acquisition_mode": hit.acquisition_mode,
            "priority": hit.priority,
            "retrieval_mode": hit.retrieval_mode,
        }

    def _candidate_disease_to_prompt_payload(self, candidate: KgRagCandidateDisease) -> dict[str, Any]:
        return {
            "disease_name": candidate.disease_name,
            "score": candidate.score,
            "matched_features": list(candidate.matched_features),
            "evidence_names": list(candidate.evidence_names),
            "retrieval_mode": candidate.retrieval_mode,
            "already_in_top3": candidate.already_in_top3,
        }

    def _get_retrieval_context(self, session: BaselineSessionState) -> dict[str, Any]:
        return self._latest_retrieval_context_by_session.get(session.session_id, self._empty_retrieval_context())

    def _empty_retrieval_context(self) -> dict[str, Any]:
        return {
            "retrieval_query": "",
            "retrieval_top_k": self.retrieval_top_k,
            "retrieved_kg_context": [],
            "retrieved_kg_candidate_diseases": [],
            "retrieved_kg_candidate_disease_total": 0,
            "retrieved_kg_candidate_disease_has_more": False,
            "retrieved_kg_candidate_disease_notice": "",
            "retrieved_node_ids": [],
            "retrieved_disease_names": [],
            "retrieved_candidate_disease_names_from_symptoms": [],
            "retrieved_candidate_disease_count_from_symptoms": 0,
            "retrieved_candidate_disease_total_from_symptoms": 0,
            "retrieved_candidate_disease_has_more_from_symptoms": False,
            "retrieved_candidate_disease_notice_from_symptoms": "",
            "retrieval_mode": "",
        }

    def _dedupe_values(self, values) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for item in values:
            value = str(item or "").strip()
            normalized_value = self._normalize_text(value)
            if len(normalized_value) == 0 or normalized_value in seen:
                continue
            seen.add(normalized_value)
            deduped.append(value)
        return deduped

    def _classify_direct_reply(self, patient_text: str) -> str:
        normalized_text = str(patient_text or "").strip().rstrip("。！？!?；;，,")
        if len(normalized_text) == 0:
            return ""
        if normalized_text in _POSITIVE_DIRECT_REPLIES:
            return "present"
        if normalized_text in _UNCLEAR_DIRECT_REPLIES:
            return "unclear"
        if len(normalized_text) > 20:
            return ""
        if normalized_text.startswith(("有", "是", "对", "会", "存在")):
            return "present"
        if normalized_text.startswith(("不确定", "不太清楚", "不清楚", "说不上来", "没注意")):
            return "unclear"
        return ""

    def _build_direct_reply_feature(
        self,
        session: BaselineSessionState,
        patient_text: str,
    ) -> BaselineObservedFeature | None:
        target_name = str(session.pending_target_name or "").strip()
        question_group = str(session.pending_question_group or "").strip()
        if len(target_name) == 0 or question_group not in _TRACKED_OBSERVED_FEATURE_GROUPS:
            return None

        return BaselineObservedFeature(
            normalized_name=target_name,
            mention_state="present",
            canonical_name=target_name,
            source_turn=session.turn_index,
            source_kind="direct_reply_confirmed",
            metadata={
                "question_group": question_group,
                "question_text": session.pending_question_text,
                "target_name": target_name,
                "evidence_text": str(patient_text or "").strip(),
            },
        )

    def _should_extract_observed_features_from_free_text(
        self,
        session: BaselineSessionState,
        patient_text: str,
    ) -> bool:
        if self.feature_extractor is None:
            return False

        normalized_text = str(patient_text or "").strip()
        return session.turn_index == 1 or len(normalized_text) >= 12

    def _extract_observed_features_from_free_text(
        self,
        session: BaselineSessionState,
        patient_text: str,
    ) -> list[BaselineObservedFeature]:
        if self.feature_extractor is None:
            return []

        try:
            patient_context = self.feature_extractor.extract_patient_context(patient_text)
        except Exception:
            return []

        tracked_features = [
            item
            for item in patient_context.clinical_features
            if item.mention_state == "present" and item.category in _TRACKED_OBSERVED_FEATURE_GROUPS
        ]
        if len(tracked_features) == 0:
            return []

        if self.entity_linker is None:
            return [
                BaselineObservedFeature(
                    normalized_name=item.normalized_name,
                    mention_state="present",
                    canonical_name=item.name or item.normalized_name,
                    source_turn=session.turn_index,
                    source_kind="free_text_extracted",
                    metadata={
                        "category": item.category,
                        "evidence_text": item.evidence_text,
                    },
                )
                for item in tracked_features
            ]

        linked_entities = self.entity_linker.link_clinical_features(tracked_features)
        observed_features: list[BaselineObservedFeature] = []
        for feature, linked_entity in zip(tracked_features, linked_entities):
            if not linked_entity.is_trusted:
                continue
            observed_features.append(
                BaselineObservedFeature(
                    normalized_name=feature.normalized_name,
                    mention_state="present",
                    canonical_name=str(linked_entity.canonical_name or feature.name or feature.normalized_name),
                    node_id=str(linked_entity.node_id or ""),
                    label=str(linked_entity.label or ""),
                    similarity=float(linked_entity.similarity or 0.0),
                    source_turn=session.turn_index,
                    source_kind="free_text_extracted",
                    metadata={
                        "category": feature.category,
                        "evidence_text": feature.evidence_text,
                        "link_is_trusted": True,
                    },
                )
            )
        return observed_features

    def _merge_observed_features(
        self,
        session: BaselineSessionState,
        observed_features: list[BaselineObservedFeature],
    ) -> None:
        if len(observed_features) == 0:
            return

        existing_by_key = {
            self._observed_feature_key(item): item
            for item in session.observed_features
            if len(self._observed_feature_key(item)) > 0
        }
        for feature in observed_features:
            feature_key = self._observed_feature_key(feature)
            if len(feature_key) == 0:
                continue

            existing = existing_by_key.get(feature_key)
            if existing is None:
                session.observed_features.append(feature)
                existing_by_key[feature_key] = feature
                continue

            if len(existing.canonical_name) == 0 and len(feature.canonical_name) > 0:
                existing.canonical_name = feature.canonical_name
            if len(existing.node_id) == 0 and len(feature.node_id) > 0:
                existing.node_id = feature.node_id
            if len(existing.label) == 0 and len(feature.label) > 0:
                existing.label = feature.label
            if feature.similarity > existing.similarity:
                existing.similarity = feature.similarity
            if feature.source_turn >= existing.source_turn:
                existing.source_turn = feature.source_turn
                existing.source_kind = feature.source_kind or existing.source_kind
            existing.metadata.update(feature.metadata)

    def _observed_feature_key(self, feature: BaselineObservedFeature) -> str:
        return self._normalize_text(feature.canonical_name or feature.normalized_name)