"""实现一个可接入 ReplayEngine 的纯 LLM 多轮问诊 baseline。"""

from __future__ import annotations

from dataclasses import asdict
import re
from typing import Any

from brain.errors import LlmUnavailableError
from brain.llm_client import LlmClient

from .llm_baseline_types import (
    BASELINE_EVIDENCE_COSTS,
    BASELINE_QUESTION_GROUPS,
    BaselineAskDecision,
    BaselineDialogueTurn,
    BaselineFinalDecision,
    BaselineHypothesisCandidate,
    BaselineSessionState,
    BaselineTurnDecisionDraft,
)


class PureLlmConsultationBrain:
    """纯 LLM 医生 baseline，直接基于对话历史做 ask/final 决策。"""

    def __init__(
        self,
        *,
        llm_client: LlmClient,
        max_turns: int = 8,
        backend_name: str = "pure_llm",
        disease_scope: list[str] | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.max_turns = max(int(max_turns), 1)
        self.backend_name = backend_name
        self.sessions: dict[str, BaselineSessionState] = {}
        self.disease_scope = self._dedupe_scope_names(disease_scope or [])
        self._disease_scope_by_normalized = {
            self._normalize_text(name): name for name in self.disease_scope if len(self._normalize_text(name)) > 0
        }

        if not getattr(self.llm_client, "is_available", lambda: False)():
            raise LlmUnavailableError(
                stage="baseline_consultation_turn",
                prompt_name="baseline_consultation_turn",
                message="纯 LLM baseline 需要可用的大模型客户端。",
            )

    # 创建一条 baseline 会话，兼容 ReplayEngine 的 start_session 接口。
    def start_session(self, session_id: str) -> BaselineSessionState:
        session = BaselineSessionState(session_id=session_id)
        self.sessions[session_id] = session
        return session

    # 处理一轮患者输入：要么继续追问，要么直接给出 final_report。
    def process_turn(self, session_id: str, patient_text: str) -> dict[str, Any]:
        session = self.sessions.get(session_id) or self.start_session(session_id)
        session.turn_index += 1
        session.dialogue_history.append(BaselineDialogueTurn(role="patient", text=str(patient_text).strip()))
        self._record_patient_observation(session, patient_text)

        decision_kind, decision = self._decide_next_step(session, must_finalize=False)
        if decision_kind == "ask":
            ask_decision = decision
            pending_action = self._build_pending_action(session, ask_decision)
            search_report = self._build_search_report(
                session,
                decision_kind="ask",
                top3=ask_decision.top3,
                selected_action=pending_action,
                confidence=ask_decision.confidence,
                reasoning=ask_decision.reasoning,
                question_group=ask_decision.question_group,
                evidence_cost=ask_decision.evidence_cost,
            )
            session.last_model_top3 = list(ask_decision.top3)
            session.asked_questions.append(ask_decision.question_text)
            session.pending_question_text = ask_decision.question_text
            session.pending_target_name = ask_decision.target_name
            session.pending_question_group = ask_decision.question_group
            session.dialogue_history.append(BaselineDialogueTurn(role="doctor", text=ask_decision.question_text))
            return {
                "session_id": session_id,
                "turn_index": session.turn_index,
                "next_question": ask_decision.question_text,
                "pending_action": pending_action,
                "search_report": search_report,
                "final_report": None,
            }

        final_decision = decision
        final_report = self._build_final_report(
            session,
            final_decision,
            stop_reason="final_answer_accepted",
            forced_finalize=False,
        )
        session.last_model_top3 = list(final_decision.top3)
        session.finalized = True
        session.last_final_report = dict(final_report)
        session.pending_question_text = ""
        session.pending_target_name = ""
        session.pending_question_group = "unknown"
        search_report = self._build_search_report(
            session,
            decision_kind="final",
            top3=final_decision.top3,
            selected_action=None,
            confidence=final_decision.confidence,
            reasoning=final_decision.reasoning,
            final_answer=final_decision.final_answer,
        )
        return {
            "session_id": session_id,
            "turn_index": session.turn_index,
            "next_question": None,
            "pending_action": None,
            "search_report": search_report,
            "final_report": final_report,
        }

    # 到达最大轮次后，强制让模型给出 top1 + top3 的最终判断。
    def finalize(self, session_id: str) -> dict[str, Any]:
        session = self.sessions.get(session_id) or self.start_session(session_id)
        if session.finalized and session.last_final_report:
            return dict(session.last_final_report)

        _, decision = self._decide_next_step(session, must_finalize=True)
        final_report = self._build_final_report(
            session,
            decision,
            stop_reason="baseline_turn_limit_finalize",
            forced_finalize=True,
        )
        session.last_model_top3 = list(decision.top3)
        session.finalized = True
        session.last_final_report = dict(final_report)
        session.pending_question_text = ""
        session.pending_target_name = ""
        session.pending_question_group = "unknown"
        return final_report

    # 将当前会话转成结构化 prompt 输入，并归一化 ask/final 决策。
    def _decide_next_step(
        self,
        session: BaselineSessionState,
        *,
        must_finalize: bool,
    ) -> tuple[str, BaselineAskDecision | BaselineFinalDecision]:
        payload = self.llm_client.run_structured_prompt(
            "baseline_consultation_turn",
            self._build_prompt_variables(session, must_finalize=must_finalize),
            BaselineTurnDecisionDraft,
        )
        return self._normalize_turn_decision(payload, session, must_finalize=must_finalize)

    # prompt 里显式说明字段含义；这里再把会话历史、预算和上一轮 top3 一起喂给模型。
    def _build_prompt_variables(self, session: BaselineSessionState, *, must_finalize: bool) -> dict[str, Any]:
        asked_count = len(session.asked_questions)
        remaining_budget = max(self.max_turns - asked_count, 0)
        variables = {
            "scene": "HIV/AIDS 场景下的多轮问诊 baseline",
            "max_turns": self.max_turns,
            "asked_question_count": asked_count,
            "remaining_question_budget": remaining_budget,
            "must_finalize": must_finalize,
            "dialogue_history": [asdict(item) for item in session.dialogue_history],
            "asked_questions": list(session.asked_questions),
            "last_model_top3": [asdict(item) for item in session.last_model_top3],
            "disease_scope_count": len(self.disease_scope),
            "disease_scope_names": list(self.disease_scope),
        }
        variables.update(self._build_additional_prompt_variables(session, must_finalize=must_finalize))
        return variables

    # 子类可以在这里补充额外 prompt 变量，例如检索结果或外部上下文。
    def _build_additional_prompt_variables(
        self,
        session: BaselineSessionState,
        *,
        must_finalize: bool,
    ) -> dict[str, Any]:
        _ = session, must_finalize
        return {}

    # 不同 baseline 可以覆盖动作来源，方便 replay 区分纯 LLM 和检索增强路线。
    def _selected_action_source(self) -> str:
        return "baseline_llm"

    # 子类可以在 search_report.search_metadata 里补充检索元信息。
    def _build_additional_search_metadata(
        self,
        session: BaselineSessionState,
        *,
        decision_kind: str,
        question_group: str,
        evidence_cost: str,
        final_answer: str,
    ) -> dict[str, Any]:
        _ = session, decision_kind, question_group, evidence_cost, final_answer
        return {}

    # 子类可以在 final_report.metadata 里补充本轮最终决策时使用的额外上下文。
    def _build_additional_final_metadata(
        self,
        session: BaselineSessionState,
        decision: BaselineFinalDecision,
        *,
        stop_reason: str,
        forced_finalize: bool,
    ) -> dict[str, Any]:
        _ = session, decision, stop_reason, forced_finalize
        return {}

    # 子类可在这里根据最新患者回复维护轻量会话状态，例如检索增强所需的已知特征集合。
    def _record_patient_observation(self, session: BaselineSessionState, patient_text: str) -> None:
        _ = session, patient_text

    # 将模型原始 JSON 统一收口为 ask / final 两种规范化决策。
    def _normalize_turn_decision(
        self,
        payload: Any,
        session: BaselineSessionState,
        *,
        must_finalize: bool,
    ) -> tuple[str, BaselineAskDecision | BaselineFinalDecision]:
        raw_payload = self._payload_to_dict(payload)
        decision = str(raw_payload.get("decision") or "").strip().lower()
        top3 = self._normalize_top3(raw_payload.get("top3"), fallback=session.last_model_top3)
        reasoning = str(raw_payload.get("reasoning") or "").strip()
        confidence = self._resolve_decision_confidence(
            raw_payload.get("confidence"),
            top3=top3,
            fallback=session.last_model_top3,
        )

        if must_finalize:
            decision = "final"

        if decision == "final":
            final_decision = self._build_final_decision(raw_payload, top3, confidence, reasoning, session)
            return "final", final_decision

        question_text = self._sanitize_question_text(raw_payload.get("question_text"))
        question_group = self._normalize_question_group(
            raw_payload.get("question_group"),
            question_text=question_text,
            target_name=str(raw_payload.get("target_name") or "").strip(),
        )
        target_name = self._normalize_target_name(
            raw_payload.get("target_name"),
            question_text=question_text,
            question_group=question_group,
        )
        evidence_cost = self._normalize_evidence_cost(raw_payload.get("evidence_cost"), question_group=question_group)

        if len(question_text) == 0:
            final_decision = self._build_final_decision(raw_payload, top3, confidence, reasoning, session)
            return "final", final_decision

        return "ask", BaselineAskDecision(
            question_text=question_text,
            question_group=question_group,
            target_name=target_name,
            evidence_cost=evidence_cost,
            confidence=confidence,
            top3=top3,
            reasoning=reasoning,
        )

    def _build_final_decision(
        self,
        raw_payload: dict[str, Any],
        top3: list[BaselineHypothesisCandidate],
        confidence: float,
        reasoning: str,
        session: BaselineSessionState,
    ) -> BaselineFinalDecision:
        final_answer = self._align_disease_name_to_scope(raw_payload.get("final_answer"))
        compiled = bool(raw_payload.get("compiled", False))

        if len(final_answer) == 0 and top3:
            final_answer = top3[0].name
        if len(final_answer) == 0 and session.last_model_top3:
            final_answer = session.last_model_top3[0].name
        if len(final_answer) == 0:
            final_answer = "待进一步评估"

        normalized_top3 = list(top3)
        scope_aligned_top3 = [item for item in normalized_top3 if self._is_disease_name_in_scope(item.name)]
        if scope_aligned_top3:
            normalized_top3 = scope_aligned_top3
            if not self._is_disease_name_in_scope(final_answer):
                final_answer = normalized_top3[0].name
        if not normalized_top3:
            normalized_top3 = [BaselineHypothesisCandidate(name=final_answer, confidence=confidence)]
        elif self._normalize_text(normalized_top3[0].name) != self._normalize_text(final_answer):
            normalized_top3 = [BaselineHypothesisCandidate(name=final_answer, confidence=confidence)] + [
                item
                for item in normalized_top3
                if self._normalize_text(item.name) != self._normalize_text(final_answer)
            ]
            normalized_top3 = normalized_top3[:3]

        return BaselineFinalDecision(
            final_answer=final_answer,
            confidence=confidence,
            top3=normalized_top3,
            reasoning=reasoning,
            compiled=compiled or True,
        )

    def _payload_to_dict(self, payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            return dict(payload)
        if hasattr(payload, "__dict__"):
            return dict(vars(payload))
        return {}

    # 模型可能输出多句或多问号，这里尽量压成一条清晰、单目标的患者问题。
    def _sanitize_question_text(self, value: Any) -> str:
        text = str(value or "").strip().replace("\n", " ")
        text = re.sub(r"\s+", " ", text)
        if len(text) == 0:
            return ""
        if "？" in text:
            text = text.split("？", 1)[0].strip() + "？"
        elif "?" in text:
            text = text.split("?", 1)[0].strip() + "？"
        elif not text.endswith(("？", "?", "。")):
            text = text + "？"
        return text

    # question_group 不再强制要求模型输出；这里优先兼容旧字段，再按问句内容做轻量推断。
    def _normalize_question_group(self, value: Any, *, question_text: str, target_name: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in BASELINE_QUESTION_GROUPS:
            return normalized

        anchor_text = self._normalize_text(f"{question_text} {target_name}")
        if any(keyword in anchor_text for keyword in ("做过", "检查", "ct", "mri", "影像", "化验", "检验")):
            if any(keyword in anchor_text for keyword in ("病原", "培养", "pcr", "抗原", "抗体")):
                return "pathogen"
            if any(keyword in anchor_text for keyword in ("ct", "mri", "影像", "片", "超声")):
                return "imaging" if "结果" in anchor_text or "提示" in anchor_text else "exam_context"
            if any(keyword in anchor_text for keyword in ("化验", "检验", "血常规", "cd4", "rna", "病毒载量")):
                return "lab" if "结果" in anchor_text or "多少" in anchor_text or "提示" in anchor_text else "exam_context"
            return "exam_context"
        if any(keyword in anchor_text for keyword in ("多久", "程度", "严重", "加重", "缓解", "什么情况")):
            return "detail"
        if any(keyword in anchor_text for keyword in ("接触", "暴露", "性行为", "吸毒", "免疫", "基础病", "既往")):
            return "risk"
        return "symptom"

    def _normalize_target_name(self, value: Any, *, question_text: str, question_group: str) -> str:
        target_name = str(value or "").strip()
        if len(target_name) > 0:
            return target_name

        if question_group == "exam_context":
            if any(keyword in question_text for keyword in ("实验室", "化验", "检验", "抽血")):
                return "实验室检查"
            if any(keyword in question_text for keyword in ("CT", "MRI", "影像", "片子", "超声")):
                return "影像检查"
            if any(keyword in question_text for keyword in ("病原", "培养", "PCR", "抗原", "抗体")):
                return "病原学检查"
            return "相关检查"

        patterns = [
            r"有没有(.+?)？?$",
            r"是否有(.+?)？?$",
            r"最近有没有(.+?)？?$",
            r"近期有没有(.+?)？?$",
            r"是否存在(.+?)？?$",
            r"有无(.+?)？?$",
        ]
        for pattern in patterns:
            matched = re.search(pattern, question_text)
            if matched:
                candidate = matched.group(1).strip("，。！？? ")
                if len(candidate) > 0:
                    return candidate
        return question_text.strip("，。！？? ")

    # evidence_cost 不再强制要求模型输出；这里优先兼容旧字段，再按问题类型兜底。
    def _normalize_evidence_cost(self, value: Any, *, question_group: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in BASELINE_EVIDENCE_COSTS:
            return normalized
        if question_group in {"symptom", "risk", "detail"}:
            return "low"
        if question_group in {"lab", "imaging", "pathogen", "exam_context"}:
            return "high"
        return "unknown"

    def _normalize_top3(
        self,
        raw_items: Any,
        *,
        fallback: list[BaselineHypothesisCandidate],
    ) -> list[BaselineHypothesisCandidate]:
        if not isinstance(raw_items, list):
            return list(fallback)

        normalized_items: list[BaselineHypothesisCandidate] = []
        seen_names: set[str] = set()
        for item in raw_items:
            if isinstance(item, dict):
                name = self._align_disease_name_to_scope(item.get("name") or item.get("answer_name") or "")
                score = item.get("confidence", item.get("score", item.get("final_score", 0.0)))
            else:
                name = self._align_disease_name_to_scope(item or "")
                score = 0.0

            normalized_name = self._normalize_text(name)
            if len(normalized_name) == 0 or normalized_name in seen_names:
                continue
            seen_names.add(normalized_name)
            normalized_items.append(
                BaselineHypothesisCandidate(name=name, confidence=self._normalize_confidence(score))
            )
            if len(normalized_items) >= 3:
                break

        if self.disease_scope:
            in_scope_items = [item for item in normalized_items if self._is_disease_name_in_scope(item.name)]
            if in_scope_items:
                return in_scope_items
        return normalized_items or list(fallback)

    def _normalize_confidence(self, value: Any) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            confidence = 0.0
        return max(0.0, min(confidence, 1.0))

    # decision_confidence 不再强制要求模型单独输出；优先复用旧字段，否则退回 top3 第一名分数。
    def _resolve_decision_confidence(
        self,
        value: Any,
        *,
        top3: list[BaselineHypothesisCandidate],
        fallback: list[BaselineHypothesisCandidate],
    ) -> float:
        normalized = self._normalize_confidence(value)
        if normalized > 0.0:
            return normalized
        if top3:
            return top3[0].confidence
        if fallback:
            return fallback[0].confidence
        return 0.0

    def _dedupe_scope_names(self, names: list[str]) -> list[str]:
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

    def _align_disease_name_to_scope(self, value: Any) -> str:
        name = str(value or "").strip()
        normalized_name = self._normalize_text(name)
        if len(normalized_name) == 0:
            return ""
        return self._disease_scope_by_normalized.get(normalized_name, name)

    def _is_disease_name_in_scope(self, value: str) -> bool:
        if not self.disease_scope:
            return True
        normalized_name = self._normalize_text(value)
        return normalized_name in self._disease_scope_by_normalized

    # 把 ask 决策包装成 ReplayEngine 可消费的 pending_action。
    def _build_pending_action(self, session: BaselineSessionState, decision: BaselineAskDecision) -> dict[str, Any]:
        target_label = self._target_node_label_for_group(decision.question_group)
        target_node_id = self._target_node_id_for_question(
            question_group=decision.question_group,
            target_name=decision.target_name,
            question_text=decision.question_text,
        )
        top_hypothesis = decision.top3[0].name if decision.top3 else ""
        return {
            "action_id": f"baseline::{session.turn_index}::{self._slugify(target_node_id or decision.target_name or 'ask')}",
            "action_type": "baseline_ask",
            "target_node_id": target_node_id,
            "target_node_name": decision.target_name,
            "target_node_label": target_label,
            "hypothesis_id": (
                f"baseline::{self._slugify(top_hypothesis)}"
                if len(self._slugify(top_hypothesis)) > 0
                else ""
            ),
            "metadata": {
                "question_type_hint": decision.question_group,
                "acquisition_mode": self._acquisition_mode_for_group(decision.question_group, target_node_id),
                "evidence_cost": decision.evidence_cost,
                "selected_action_source": self._selected_action_source(),
                "selected_action_source_priority_rank": 1,
                "decision_confidence": decision.confidence,
                "backend": self.backend_name,
                "question_text": decision.question_text,
            },
        }

    def _target_node_label_for_group(self, question_group: str) -> str:
        return {
            "symptom": "ClinicalFinding",
            "risk": "RiskFactor",
            "detail": "ClinicalAttribute",
            "lab": "LabFinding",
            "imaging": "ImagingFinding",
            "pathogen": "Pathogen",
            "exam_context": "ExamContext",
        }.get(question_group, "ClinicalFinding")

    # target_node_id 尽量优先复用稳定中文锚点，避免无意义 synthetic id 影响 patient matching。
    def _target_node_id_for_question(self, *, question_group: str, target_name: str, question_text: str) -> str:
        if question_group == "exam_context":
            exam_kind = self._infer_exam_context_kind(target_name=target_name, question_text=question_text)
            return f"__exam_context__::{exam_kind}"

        normalized_target_name = str(target_name).strip()
        if len(normalized_target_name) > 0:
            return normalized_target_name

        slug = self._slugify(question_text)
        return f"baseline::{slug}" if len(slug) > 0 else "baseline::unknown_target"

    def _infer_exam_context_kind(self, *, target_name: str, question_text: str) -> str:
        anchor_text = self._normalize_text(f"{target_name} {question_text}")
        if any(keyword in anchor_text for keyword in ("病原", "培养", "pcr", "抗原", "抗体")):
            return "pathogen"
        if any(keyword in anchor_text for keyword in ("ct", "mri", "影像", "片", "超声")):
            return "imaging"
        if any(keyword in anchor_text for keyword in ("实验室", "化验", "检验", "血常规", "cd4", "rna", "病毒载量")):
            return "lab"
        return "general"

    def _acquisition_mode_for_group(self, question_group: str, target_node_id: str) -> str:
        if question_group in {"symptom", "risk", "detail"}:
            return "direct_ask"
        if question_group == "lab":
            return "needs_lab_test"
        if question_group == "imaging":
            return "needs_imaging"
        if question_group == "pathogen":
            return "needs_pathogen_test"
        if question_group == "exam_context":
            if target_node_id.endswith("::lab"):
                return "history_known"
            if target_node_id.endswith("::imaging"):
                return "history_known"
            if target_node_id.endswith("::pathogen"):
                return "history_known"
            return "history_known"
        return "direct_ask"

    # search_report 尽量贴近现有 schema，方便 replay 直接复盘 baseline 每轮的 top3 与动作来源。
    def _build_search_report(
        self,
        session: BaselineSessionState,
        *,
        decision_kind: str,
        top3: list[BaselineHypothesisCandidate],
        selected_action: dict[str, Any] | None,
        confidence: float,
        reasoning: str,
        question_group: str = "",
        evidence_cost: str = "",
        final_answer: str = "",
    ) -> dict[str, Any]:
        top3_payload = [
            {
                "name": item.name,
                "score": item.confidence,
                "confidence": item.confidence,
            }
            for item in top3
        ]
        search_metadata = {
            "backend": self.backend_name,
            "decision": decision_kind,
            "decision_confidence": confidence,
            "selected_action_source": self._selected_action_source(),
            "question_group": question_group,
            "evidence_cost": evidence_cost,
            "final_answer": final_answer,
            "current_top3": top3_payload,
            "reasoning": reasoning,
        }
        search_metadata.update(
            self._build_additional_search_metadata(
                session,
                decision_kind=decision_kind,
                question_group=question_group,
                evidence_cost=evidence_cost,
                final_answer=final_answer,
            )
        )
        return {
            "session_id": session.session_id,
            "turn_index": session.turn_index,
            "selected_action": dict(selected_action) if selected_action is not None else None,
            "root_best_action": dict(selected_action) if selected_action is not None else None,
            "repair_selected_action": None,
            "best_answer_name": top3[0].name if top3 else final_answer,
            "trajectory_count": 0,
            "final_answer_scores": [
                {
                    "answer_name": item.name,
                    "final_score": item.confidence,
                    "confidence": item.confidence,
                }
                for item in top3
            ],
            "search_metadata": search_metadata,
        }

    # final_report 只保留 benchmark 真正会消费的字段，并补充少量 baseline 自身元信息。
    def _build_final_report(
        self,
        session: BaselineSessionState,
        decision: BaselineFinalDecision,
        *,
        stop_reason: str,
        forced_finalize: bool,
    ) -> dict[str, Any]:
        candidate_hypotheses = [
            {
                "name": item.name,
                "score": item.confidence,
            }
            for item in decision.top3
        ]
        answer_group_scores = [
            {
                "answer_name": item.name,
                "final_score": item.confidence,
                "confidence": item.confidence,
            }
            for item in decision.top3
        ]
        top_confidence = decision.top3[0].confidence if decision.top3 else decision.confidence
        final_metadata = {
            "backend": self.backend_name,
            "forced_finalize": forced_finalize,
            "decision_confidence": decision.confidence,
            "compiled": bool(decision.compiled),
            "asked_question_count": len(session.asked_questions),
            "disease_scope_enabled": bool(self.disease_scope),
            "disease_scope_count": len(self.disease_scope),
            "reasoning": decision.reasoning,
        }
        final_metadata.update(
            self._build_additional_final_metadata(
                session,
                decision,
                stop_reason=stop_reason,
                forced_finalize=forced_finalize,
            )
        )
        return {
            "session_id": session.session_id,
            "turn_index": session.turn_index,
            "stop_reason": stop_reason,
            "best_final_answer": {
                "answer_name": decision.final_answer,
                "confidence": top_confidence,
            },
            "candidate_hypotheses": candidate_hypotheses,
            "answer_group_scores": answer_group_scores,
            "metadata": final_metadata,
        }

    def _normalize_text(self, value: str) -> str:
        return str(value).strip().replace(" ", "").replace("-", "").replace("_", "").lower()

    def _slugify(self, value: str) -> str:
        slug = self._normalize_text(value)
        slug = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "_", slug)
        slug = slug.strip("_")
        return slug[:64]
