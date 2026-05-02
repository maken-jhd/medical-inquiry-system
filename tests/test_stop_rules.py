"""测试打薄后的通用 stop policy。"""

from brain.stop_rules import StopRuleConfig, StopRuleEngine
from brain.types import FinalAnswerScore, HypothesisScore, SessionState


def _answer_score(
    answer_id: str = "d1",
    answer_name: str = "候选疾病",
    *,
    trajectory_count: int = 3,
    verifier_should_accept: bool = True,
) -> FinalAnswerScore:
    return FinalAnswerScore(
        answer_id=answer_id,
        answer_name=answer_name,
        consistency=0.9,
        diversity=0.4,
        agent_evaluation=0.9,
        final_score=0.8,
        metadata={
            "trajectory_count": trajectory_count,
            "verifier_mode": "llm_verifier",
            "verifier_should_accept": verifier_should_accept,
            "verifier_accept_reason": "key_support_sufficient",
        },
    )


# 验证 turn 太早时，即使分数够高也不会直接接受最终答案。
def test_stop_rules_reject_early_final_answer_by_turn_index() -> None:
    engine = StopRuleEngine(StopRuleConfig(min_turn_index_before_final_answer=2))

    decision = engine.should_accept_final_answer(_answer_score(), SessionState(session_id="s1", turn_index=1))

    assert decision.should_stop is False
    assert decision.reason == "turn_index_too_low"


# 验证真实 verifier 明确拒绝 stop 时，系统不会接受最终答案。
def test_stop_rules_respect_verifier_rejection() -> None:
    engine = StopRuleEngine()

    decision = engine.should_accept_final_answer(
        _answer_score(verifier_should_accept=False),
        SessionState(session_id="s2", turn_index=3),
    )

    assert decision.should_stop is False
    assert decision.reason == "verifier_rejected_stop"


# 显式 profile 用于消融测试时，不应被外部环境变量污染。
def test_explicit_acceptance_profile_overrides_environment(monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_ACCEPTANCE_PROFILE", "anchor_controlled")
    engine = StopRuleEngine(StopRuleConfig(acceptance_profile="baseline"))

    assert engine._acceptance_profile() == "anchor_controlled"

    explicit_engine = StopRuleEngine(StopRuleConfig(acceptance_profile="anchor_controlled"))
    assert explicit_engine._acceptance_profile() == "anchor_controlled"


def test_anchor_controlled_accepts_real_strong_anchor(monkeypatch) -> None:
    monkeypatch.delenv("BRAIN_ACCEPTANCE_PROFILE", raising=False)
    engine = StopRuleEngine(StopRuleConfig(acceptance_profile="anchor_controlled"))
    state = SessionState(session_id="anchor_stop", turn_index=3)
    state.candidate_hypotheses = [
        HypothesisScore(
            node_id="vzv",
            label="Disease",
            name="水痘-带状疱疹病毒感染",
            score=1.0,
            metadata={
                "anchor_tier": "strong_anchor",
                "observed_anchor_score": 0.63,
                "anchor_supporting_evidence": [{"node_id": "path_vzv", "name": "水痘-带状疱疹病毒"}],
            },
        )
    ]

    decision = engine.should_accept_final_answer(_answer_score("vzv", "水痘-带状疱疹病毒感染"), state)

    assert decision.should_stop is True
    assert decision.reason == "final_answer_accepted"


def test_anchor_controlled_blocks_background_only_answer(monkeypatch) -> None:
    monkeypatch.delenv("BRAIN_ACCEPTANCE_PROFILE", raising=False)
    engine = StopRuleEngine(StopRuleConfig(acceptance_profile="anchor_controlled"))
    state = SessionState(session_id="anchor_stop", turn_index=3)
    state.candidate_hypotheses = [
        HypothesisScore(
            node_id="ks",
            label="Disease",
            name="卡波西肉瘤",
            score=1.2,
            metadata={"anchor_tier": "background_supported", "observed_anchor_score": 0.0},
        )
    ]

    decision = engine.should_accept_final_answer(_answer_score("ks", "卡波西肉瘤"), state)

    assert decision.should_stop is False
    assert decision.reason == "anchor_controlled_rejected"
    assert decision.metadata["repair_reject_reason"] == "missing_required_anchor"


def test_anchor_controlled_blocks_when_stronger_anchor_alternative_exists(monkeypatch) -> None:
    monkeypatch.delenv("BRAIN_ACCEPTANCE_PROFILE", raising=False)
    engine = StopRuleEngine(StopRuleConfig(acceptance_profile="anchor_controlled"))
    state = SessionState(session_id="anchor_stop", turn_index=3)
    state.candidate_hypotheses = [
        HypothesisScore(
            node_id="ks",
            label="Disease",
            name="卡波西肉瘤",
            score=1.2,
            metadata={"anchor_tier": "provisional_anchor", "observed_anchor_score": 0.2},
        ),
        HypothesisScore(
            node_id="cmv",
            label="Disease",
            name="巨细胞病毒感染",
            score=1.0,
            metadata={
                "anchor_tier": "strong_anchor",
                "observed_anchor_score": 0.7,
                "anchor_supporting_evidence": [{"node_id": "cmv_path", "name": "巨细胞病毒"}],
            },
        ),
    ]

    decision = engine.should_accept_final_answer(_answer_score("ks", "卡波西肉瘤"), state)

    assert decision.should_stop is False
    assert decision.metadata["repair_reject_reason"] == "anchored_alternative_exists"
    assert decision.metadata["anchor_stronger_alternative_candidates"][0]["answer_id"] == "cmv"


def test_strong_anchor_uses_lower_trajectory_threshold(monkeypatch) -> None:
    monkeypatch.delenv("BRAIN_ACCEPTANCE_PROFILE", raising=False)
    engine = StopRuleEngine(
        StopRuleConfig(
            acceptance_profile="anchor_controlled",
            min_trajectory_count_before_accept=3,
            min_strong_anchor_trajectory_count_before_accept=1,
        )
    )
    state = SessionState(session_id="anchor_stop", turn_index=3)
    state.candidate_hypotheses = [
        HypothesisScore(
            node_id="cmv",
            label="Disease",
            name="巨细胞病毒感染",
            score=1.0,
            metadata={"anchor_tier": "strong_anchor", "observed_anchor_score": 0.7},
        )
    ]

    decision = engine.should_accept_final_answer(
        _answer_score("cmv", "巨细胞病毒感染", trajectory_count=1),
        state,
    )

    assert decision.should_stop is True
    assert decision.reason == "final_answer_accepted"
