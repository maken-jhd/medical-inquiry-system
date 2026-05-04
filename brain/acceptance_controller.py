"""根据 verifier / observed final evaluator 的结果决定是否接受最终答案。"""

from __future__ import annotations

from .types import FinalAnswerScore, SessionState, StopDecision


VERIFIER_ACCEPTANCE_MODES = {"llm_verifier", "observed_evidence_final_evaluator"}


class VerifierAcceptanceController:
    """只消费 verifier 信号，不再叠加结构化 stop rule 阈值。"""

    # 判断当前 best answer 是否已经被 verifier / observed evaluator 接受。
    def should_accept_final_answer(
        self,
        answer_score: FinalAnswerScore | None,
        session_state: SessionState | None = None,
    ) -> StopDecision:
        if answer_score is None:
            return StopDecision(False, "no_answer_score")

        self._record_answer_candidate(session_state, answer_score)
        self._record_verifier_accept_candidate(session_state, answer_score)

        verifier_mode = str(answer_score.metadata.get("verifier_mode") or "")
        metadata = {
            "acceptance_mode": "verifier_only",
            "verifier_mode": verifier_mode,
        }

        if verifier_mode not in VERIFIER_ACCEPTANCE_MODES:
            return StopDecision(False, "verifier_not_ready", answer_score.final_score, metadata)

        if bool(answer_score.metadata.get("verifier_should_accept", False)):
            return StopDecision(True, "final_answer_accepted", answer_score.final_score, metadata)

        reject_reason = str(answer_score.metadata.get("verifier_reject_reason") or "missing_key_support")
        return StopDecision(
            False,
            "verifier_rejected_stop",
            answer_score.agent_evaluation,
            {
                **metadata,
                "repair_reject_reason": reject_reason,
                "path_control_reason": reject_reason,
            },
        )

    # 记录每轮 best answer，用于后续复盘 top hypothesis 是否稳定。
    def _record_answer_candidate(
        self,
        session_state: SessionState | None,
        answer_score: FinalAnswerScore,
    ) -> None:
        if session_state is None:
            return

        history = self._get_history(session_state, "answer_candidate_history")
        entry = {
            "turn_index": session_state.turn_index,
            "answer_id": answer_score.answer_id,
            "answer_name": answer_score.answer_name,
        }

        if len(history) == 0 or history[-1] != entry:
            history.append(entry)

        session_state.metadata["answer_candidate_history"] = history[-12:]

    # 记录 verifier 曾经愿意接受的候选，便于 benchmark 后区分“答案对但未完成”。
    def _record_verifier_accept_candidate(
        self,
        session_state: SessionState | None,
        answer_score: FinalAnswerScore,
    ) -> None:
        if session_state is None:
            return

        if str(answer_score.metadata.get("verifier_mode") or "") != "llm_verifier":
            return

        if not bool(answer_score.metadata.get("verifier_should_accept", False)):
            return

        history = self._get_history(session_state, "verifier_accept_history")
        entry = {
            "turn_index": session_state.turn_index,
            "answer_id": answer_score.answer_id,
            "answer_name": answer_score.answer_name,
            "accept_reason": str(answer_score.metadata.get("verifier_accept_reason") or ""),
        }

        if len(history) == 0 or history[-1] != entry:
            history.append(entry)

        session_state.metadata["verifier_accept_history"] = history[-12:]

    def _get_history(self, session_state: SessionState, key: str) -> list[dict]:
        value = session_state.metadata.get(key)
        return list(value) if isinstance(value, list) else []
