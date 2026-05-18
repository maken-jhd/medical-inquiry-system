"""实现接入文本稀疏检索的多轮问诊 baseline。"""

from __future__ import annotations

from typing import Any

from brain.integrations import LlmClient

from .llm_baseline_types import BaselineFinalDecision, BaselineSessionState
from .llm_consultation_brain import PureLlmConsultationBrain
from .text_rag_retriever import SparseTextRagRetriever, TextRagHit


class TextRagConsultationBrain(PureLlmConsultationBrain):
    """在纯 LLM baseline 上叠加文本稀疏检索上下文。"""

    def __init__(
        self,
        *,
        llm_client: LlmClient,
        retriever: SparseTextRagRetriever,
        max_turns: int = 8,
        retrieval_top_k: int = 4,
        backend_name: str = "llm_text_rag",
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
        self._latest_retrieval_context_by_session: dict[str, dict[str, Any]] = {}

    def start_session(self, session_id: str) -> BaselineSessionState:
        session = super().start_session(session_id)
        self._latest_retrieval_context_by_session[session_id] = self._empty_retrieval_context()
        return session

    # 每轮在 ask/final 前先做一次轻量文本检索，再把结果注入同一 prompt 契约。
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
            "retrieved_documents": list(retrieval_context.get("retrieved_documents", [])),
        }

    def _selected_action_source(self) -> str:
        return "baseline_llm_text_rag"

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
            "retrieved_doc_ids": list(retrieval_context.get("retrieved_doc_ids", [])),
            "retrieved_disease_names": list(retrieval_context.get("retrieved_disease_names", [])),
            "retrieved_documents": list(retrieval_context.get("retrieved_documents", [])),
            "retrieved_document_count": len(retrieval_context.get("retrieved_documents", [])),
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
            "retrieved_doc_ids": list(retrieval_context.get("retrieved_doc_ids", [])),
            "retrieved_disease_names": list(retrieval_context.get("retrieved_disease_names", [])),
            "retrieved_documents": list(retrieval_context.get("retrieved_documents", [])),
            "retrieved_document_count": len(retrieval_context.get("retrieved_documents", [])),
        }

    def _build_retrieval_context(self, session: BaselineSessionState) -> dict[str, Any]:
        retrieval_query = self._build_retrieval_query(session)
        if len(retrieval_query) == 0:
            return self._empty_retrieval_context()

        candidate_disease_names = [item.name for item in session.last_model_top3 if len(item.name) > 0]
        hits = self.retriever.query(
            retrieval_query,
            top_k=self.retrieval_top_k,
            candidate_disease_names=candidate_disease_names,
        )
        retrieved_documents = [self._hit_to_prompt_payload(item) for item in hits]
        return {
            "retrieval_query": retrieval_query,
            "retrieval_top_k": self.retrieval_top_k,
            "retrieved_documents": retrieved_documents,
            "retrieved_doc_ids": [item.doc_id for item in hits],
            "retrieved_disease_names": self._dedupe_names(item.disease_name for item in hits),
        }

    def _build_retrieval_query(self, session: BaselineSessionState) -> str:
        recent_turn_texts = [turn.text.strip() for turn in session.dialogue_history[-4:] if len(turn.text.strip()) > 0]
        top3_names = [item.name for item in session.last_model_top3[:3] if len(item.name.strip()) > 0]
        if top3_names:
            recent_turn_texts.append(f"当前候选：{' / '.join(top3_names)}")
        return " ".join(recent_turn_texts).strip()

    def _hit_to_prompt_payload(self, hit: TextRagHit) -> dict[str, Any]:
        return {
            "doc_id": hit.doc_id,
            "disease_name": hit.disease_name,
            "title": hit.title,
            "content": self._truncate_text(hit.content, max_chars=320),
            "score": hit.score,
            "tags": list(hit.tags),
            "matched_terms": list(hit.matched_terms),
        }

    def _get_retrieval_context(self, session: BaselineSessionState) -> dict[str, Any]:
        return self._latest_retrieval_context_by_session.get(session.session_id, self._empty_retrieval_context())

    def _empty_retrieval_context(self) -> dict[str, Any]:
        return {
            "retrieval_query": "",
            "retrieval_top_k": self.retrieval_top_k,
            "retrieved_documents": [],
            "retrieved_doc_ids": [],
            "retrieved_disease_names": [],
        }

    def _dedupe_names(self, values) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for item in values:
            name = str(item or "").strip()
            normalized_name = self._normalize_text(name)
            if len(normalized_name) == 0 or normalized_name in seen:
                continue
            seen.add(normalized_name)
            deduped.append(name)
        return deduped

    def _truncate_text(self, value: str, *, max_chars: int) -> str:
        text = str(value or "").strip()
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 1].rstrip() + "…"