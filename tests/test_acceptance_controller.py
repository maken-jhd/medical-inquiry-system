"""测试 verifier-only 最终接受控制器。"""

from brain.acceptance_controller import VerifierAcceptanceController
from brain.types import FinalAnswerScore, SessionState


def _answer(metadata: dict) -> FinalAnswerScore:
    return FinalAnswerScore(
        answer_id="d1",
        answer_name="候选诊断",
        consistency=0.1,
        diversity=0.1,
        agent_evaluation=0.2,
        final_score=0.3,
        metadata=dict(metadata),
    )


# 验证最终接受不再受 turn / trajectory / score 等 stop rule 阈值影响。
def test_acceptance_controller_accepts_when_verifier_accepts() -> None:
    controller = VerifierAcceptanceController()
    state = SessionState(session_id="s1", turn_index=0)

    decision = controller.should_accept_final_answer(
        _answer(
            {
                "verifier_mode": "llm_verifier",
                "verifier_should_accept": True,
            }
        ),
        state,
    )

    assert decision.should_stop is True
    assert decision.reason == "final_answer_accepted"
    assert decision.metadata["acceptance_mode"] == "verifier_only"


# 验证 verifier 拒绝仍会保留 repair 所需的拒绝原因。
def test_acceptance_controller_keeps_verifier_repair_signal() -> None:
    controller = VerifierAcceptanceController()

    decision = controller.should_accept_final_answer(
        _answer(
            {
                "verifier_mode": "llm_verifier",
                "verifier_should_accept": False,
                "verifier_reject_reason": "missing_key_support",
            }
        )
    )

    assert decision.should_stop is False
    assert decision.reason == "verifier_rejected_stop"
    assert decision.metadata["repair_reject_reason"] == "missing_key_support"
    assert decision.metadata["path_control_reason"] == "missing_key_support"


# 验证 candidate_state_fallback 产生的 observed evidence final evaluator 可直接参与接受控制。
def test_acceptance_controller_accepts_observed_final_evaluator() -> None:
    controller = VerifierAcceptanceController()

    decision = controller.should_accept_final_answer(
        _answer(
            {
                "verifier_mode": "observed_evidence_final_evaluator",
                "verifier_should_accept": True,
            }
        )
    )

    assert decision.should_stop is True
    assert decision.reason == "final_answer_accepted"


# 验证 verifier 尚未调用或被延后时，不生成最终报告，也不触发 repair。
def test_acceptance_controller_waits_when_verifier_not_ready() -> None:
    controller = VerifierAcceptanceController()

    decision = controller.should_accept_final_answer(
        _answer(
            {
                "verifier_mode": "llm_verifier_deferred",
                "verifier_called": False,
            }
        )
    )

    assert decision.should_stop is False
    assert decision.reason == "verifier_not_ready"
