"""测试真实 verifier 和早停门槛如何抑制过早终止。"""

from brain.stop_rules import StopRuleConfig, StopRuleEngine
from brain.types import FinalAnswerScore, SessionState


# 验证 turn 太早时，即使分数够高也不会直接接受最终答案。
def test_stop_rules_reject_early_final_answer_by_turn_index() -> None:
    engine = StopRuleEngine(StopRuleConfig(min_turn_index_before_final_answer=2))
    answer_score = FinalAnswerScore(
        answer_id="d1",
        answer_name="PCP",
        consistency=0.9,
        diversity=0.4,
        agent_evaluation=0.9,
        final_score=0.8,
        metadata={"trajectory_count": 3, "verifier_mode": "llm_verifier", "verifier_should_accept": True},
    )

    decision = engine.should_accept_final_answer(answer_score, SessionState(session_id="s1", turn_index=1))

    assert decision.should_stop is False
    assert decision.reason == "turn_index_too_low"


# 验证真实 verifier 明确拒绝 stop 时，系统不会接受最终答案。
def test_stop_rules_respect_verifier_rejection() -> None:
    engine = StopRuleEngine()
    answer_score = FinalAnswerScore(
        answer_id="d1",
        answer_name="PCP",
        consistency=0.9,
        diversity=0.4,
        agent_evaluation=0.9,
        final_score=0.8,
        metadata={"trajectory_count": 3, "verifier_mode": "llm_verifier", "verifier_should_accept": False},
    )

    decision = engine.should_accept_final_answer(answer_score, SessionState(session_id="s2", turn_index=3))

    assert decision.should_stop is False
    assert decision.reason == "verifier_rejected_stop"
