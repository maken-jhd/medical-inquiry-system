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
    margin_relaxation_buffer: float = 0.025
    risk_relaxation_buffer: float = 0.045
    high_support_quality_override: float = 0.62
    high_support_quality_risk_discount: float = 0.05


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
            calibration_assessment = self._evaluate_acceptance_calibration(answer_score)
            if calibration_assessment is not None and calibration_assessment.get("acceptance_calibration_blocked", False):
                return StopDecision(
                    False,
                    "verifier_rejected_stop",
                    answer_score.agent_evaluation,
                    {
                        **metadata,
                        "repair_reject_reason": "strong_alternative_not_ruled_out",
                        "path_control_reason": "strong_alternative_not_ruled_out",
                        **calibration_assessment,
                    },
                )
            accept_metadata = dict(metadata)
            if calibration_assessment is not None:
                accept_metadata.update(calibration_assessment)
            return StopDecision(True, "final_answer_accepted", answer_score.final_score, accept_metadata)

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

    # 当 verifier 已愿意接受时，再用 reward-side proxy 做一次轻量校准：
    # - 极高风险仍然拦住
    # - 接近阈值的边界值 case 给一点 buffer，避免过度错杀
    # - support quality 很高时，允许抵消一小部分 risk proxy
    def _evaluate_acceptance_calibration(self, answer_score: FinalAnswerScore) -> dict | None:
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
        margin_floor = self.config.min_belief_margin_proxy
        quality_floor = self.config.min_branch_support_quality
        risk_ceiling = self.config.max_acceptance_risk_proxy
        relaxed_margin_floor = max(0.0, margin_floor - self.config.margin_relaxation_buffer)
        relaxed_risk_ceiling = risk_ceiling + self.config.risk_relaxation_buffer
        high_support_override_active = normalized_quality >= self.config.high_support_quality_override
        effective_risk = max(
            0.0,
            normalized_risk
            - (
                self.config.high_support_quality_risk_discount
                if high_support_override_active
                else 0.0
            ),
        )

        assessment = {
            "acceptance_calibration_applied": True,
            "acceptance_calibration_blocked": False,
            "belief_margin_proxy": round(normalized_margin, 4),
            "acceptance_risk_proxy": round(normalized_risk, 4),
            "effective_acceptance_risk_proxy": round(effective_risk, 4),
            "branch_support_quality": round(normalized_quality, 4),
            "acceptance_calibration_margin_floor": round(margin_floor, 4),
            "acceptance_calibration_relaxed_margin_floor": round(relaxed_margin_floor, 4),
            "acceptance_calibration_risk_ceiling": round(risk_ceiling, 4),
            "acceptance_calibration_relaxed_risk_ceiling": round(relaxed_risk_ceiling, 4),
            "acceptance_calibration_high_support_override": high_support_override_active,
            "reward_confidence_proxy_source": str(answer_score.metadata.get("reward_confidence_proxy_source") or ""),
        }

        if effective_risk <= risk_ceiling:
            return {
                **assessment,
                "acceptance_calibration_reason": "within_risk_limit",
            }

        if (
            normalized_margin >= margin_floor
            and normalized_quality >= quality_floor
        ):
            return {
                **assessment,
                "acceptance_calibration_reason": "baseline_proxy_pass",
            }

        # 如果 branch support 很强，则允许它抵消一小段 risk overshoot，
        # 但仍要求 margin 至少接近阈值，避免彻底放开高风险 case。
        if (
            high_support_override_active
            and effective_risk <= relaxed_risk_ceiling
            and normalized_margin >= relaxed_margin_floor
        ):
            return {
                **assessment,
                "acceptance_calibration_reason": "high_support_quality_override",
            }

        # 风险只是略高于主阈值、support 也够强时，不要因为 margin 略低于线就一刀切拒停。
        if (
            effective_risk <= relaxed_risk_ceiling
            and normalized_quality >= quality_floor
            and normalized_margin >= relaxed_margin_floor
        ):
            return {
                **assessment,
                "acceptance_calibration_reason": "buffered_margin_pass",
            }

        return {
            **assessment,
            "acceptance_calibration_blocked": True,
            "acceptance_calibration_reason": "reward_confidence_proxy_guard",
        }
