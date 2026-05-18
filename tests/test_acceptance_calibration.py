"""测试 reward-side confidence proxy 的轻量 acceptance 校准。"""

from brain.acceptance import AcceptanceCalibrationConfig, VerifierAcceptanceController
from brain.state import FinalAnswerScore


def _answer(**metadata) -> FinalAnswerScore:
    return FinalAnswerScore(
        answer_id="pcp",
        answer_name="肺孢子菌肺炎",
        consistency=0.7,
        diversity=0.4,
        agent_evaluation=0.68,
        final_score=0.74,
        metadata=dict(metadata),
    )


# 当 verifier 想接受，但 reward-side proxy 显示 margin 仍弱且 acceptance risk 偏高时，应触发轻量拒停。
def test_acceptance_controller_blocks_high_risk_low_margin_acceptance() -> None:
    controller = VerifierAcceptanceController(
        AcceptanceCalibrationConfig(
            enable_reward_confidence_proxy=True,
            min_belief_margin_proxy=0.12,
            max_acceptance_risk_proxy=0.22,
            min_branch_support_quality=0.42,
        )
    )

    decision = controller.should_accept_final_answer(
        _answer(
            verifier_mode="llm_verifier",
            verifier_should_accept=True,
            reward_confidence_proxy_source="trajectory_reward_proxy",
            belief_margin_proxy=0.05,
            acceptance_risk_proxy=0.31,
            branch_support_quality=0.36,
        )
    )

    assert decision.should_stop is False
    assert decision.reason == "verifier_rejected_stop"
    assert decision.metadata["acceptance_calibration_blocked"] is True
    assert decision.metadata["repair_reject_reason"] == "strong_alternative_not_ruled_out"


# 当 margin 与 support quality 足够时，不应因为 proxy 校准把正常接受全拦掉。
def test_acceptance_controller_keeps_low_risk_acceptance() -> None:
    controller = VerifierAcceptanceController()

    decision = controller.should_accept_final_answer(
        _answer(
            verifier_mode="llm_verifier",
            verifier_should_accept=True,
            reward_confidence_proxy_source="trajectory_reward_proxy",
            belief_margin_proxy=0.19,
            acceptance_risk_proxy=0.14,
            branch_support_quality=0.63,
        )
    )

    assert decision.should_stop is True
    assert decision.reason == "final_answer_accepted"
    assert decision.metadata["acceptance_calibration_reason"] == "within_risk_limit"


# 当 risk 只是略高于阈值，但 support quality 很强且 margin 接近阈值时，应允许软放行。
def test_acceptance_controller_allows_high_support_borderline_case() -> None:
    controller = VerifierAcceptanceController()

    decision = controller.should_accept_final_answer(
        _answer(
            verifier_mode="llm_verifier",
            verifier_should_accept=True,
            reward_confidence_proxy_source="trajectory_reward_proxy",
            belief_margin_proxy=0.1,
            acceptance_risk_proxy=0.29,
            branch_support_quality=0.68,
        )
    )

    assert decision.should_stop is True
    assert decision.reason == "final_answer_accepted"
    assert decision.metadata["acceptance_calibration_reason"] == "high_support_quality_override"
    assert decision.metadata["acceptance_calibration_high_support_override"] is True


# 即使 support quality 很高，若 risk 明显越过 relaxed buffer，仍应继续阻止过早接受。
def test_acceptance_controller_still_blocks_very_high_risk_case() -> None:
    controller = VerifierAcceptanceController()

    decision = controller.should_accept_final_answer(
        _answer(
            verifier_mode="llm_verifier",
            verifier_should_accept=True,
            reward_confidence_proxy_source="trajectory_reward_proxy",
            belief_margin_proxy=0.1,
            acceptance_risk_proxy=0.36,
            branch_support_quality=0.68,
        )
    )

    assert decision.should_stop is False
    assert decision.reason == "verifier_rejected_stop"
    assert decision.metadata["acceptance_calibration_blocked"] is True
    assert decision.metadata["acceptance_calibration_reason"] == "reward_confidence_proxy_guard"
