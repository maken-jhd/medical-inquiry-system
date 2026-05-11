"""根据 verifier / observed final evaluator 的结果决定是否接受最终答案。"""

from __future__ import annotations

from dataclasses import dataclass

from .types import FinalAnswerScore, SessionState, StopDecision


VERIFIER_ACCEPTANCE_MODES = {"llm_verifier", "observed_evidence_final_evaluator"}


@dataclass
class AcceptanceCalibrationConfig:
    """保存最终接受前的轻量 proxy 校准阈值。"""

    enable_reward_confidence_proxy: bool = True
    min_belief_margin_proxy: float = 0.12
    max_acceptance_risk_proxy: float = 0.22
    min_branch_support_quality: float = 0.42


class VerifierAcceptanceController:
    """只消费 verifier 信号，不再叠加结构化 stop rule 阈值。"""

    def __init__(self, config: AcceptanceCalibrationConfig | None = None) -> None:
        self.config = config or AcceptanceCalibrationConfig()

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
            calibration_block = self._apply_acceptance_calibration(answer_score)
            if calibration_block is not None:
                return StopDecision(
                    False,
                    "verifier_rejected_stop",
                    answer_score.agent_evaluation,
                    {
                        **metadata,
                        "repair_reject_reason": "strong_alternative_not_ruled_out",
                        "path_control_reason": "strong_alternative_not_ruled_out",
                        **calibration_block,
                    },
                )
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

    # 当 verifier 已愿意接受，但 reward-side proxy 提示 margin 仍弱且风险偏高时，做一次轻量拒停。
    def _apply_acceptance_calibration(self, answer_score: FinalAnswerScore) -> dict | None:
        if not self.config.enable_reward_confidence_proxy:
            return None

        if str(answer_score.metadata.get("verifier_mode") or "") != "llm_verifier":
            return None

        margin_proxy = answer_score.metadata.get("belief_margin_proxy")
        acceptance_risk_proxy = answer_score.metadata.get("acceptance_risk_proxy")
        branch_support_quality = answer_score.metadata.get("branch_support_quality")

        if not all(isinstance(value, (int, float)) for value in (margin_proxy, acceptance_risk_proxy, branch_support_quality)):
            return None

        normalized_margin = float(margin_proxy)
        normalized_risk = float(acceptance_risk_proxy)
        normalized_quality = float(branch_support_quality)

        if normalized_risk <= self.config.max_acceptance_risk_proxy:
            return None

        if (
            normalized_margin >= self.config.min_belief_margin_proxy
            and normalized_quality >= self.config.min_branch_support_quality
        ):
            return None

        return {
            "acceptance_calibration_blocked": True,
            "acceptance_calibration_reason": "reward_confidence_proxy_guard",
            "belief_margin_proxy": round(normalized_margin, 4),
            "acceptance_risk_proxy": round(normalized_risk, 4),
            "branch_support_quality": round(normalized_quality, 4),
            "reward_confidence_proxy_source": str(answer_score.metadata.get("reward_confidence_proxy_source") or ""),
        }
