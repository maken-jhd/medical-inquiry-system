"""测试真实 verifier 和早停门槛如何抑制过早终止。"""

from brain.stop_rules import StopRuleConfig, StopRuleEngine
from brain.types import EvidenceState, FinalAnswerScore, SessionState


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


def _accepted_answer_score() -> FinalAnswerScore:
    return FinalAnswerScore(
        answer_id="pcp",
        answer_name="肺孢子菌肺炎 (PCP)",
        consistency=0.9,
        diversity=0.4,
        agent_evaluation=0.92,
        final_score=0.8,
        metadata={
            "trajectory_count": 3,
            "verifier_mode": "llm_verifier",
            "verifier_should_accept": True,
            "verifier_accept_reason": "key_support_sufficient",
        },
    )


def test_guarded_lenient_requires_confirmed_key_evidence_for_early_accept() -> None:
    engine = StopRuleEngine(StopRuleConfig(acceptance_profile="guarded_lenient"))
    answer_score = _accepted_answer_score()
    state = SessionState(session_id="guarded", turn_index=2)

    decision = engine.should_accept_final_answer(answer_score, state)

    assert decision.should_stop is False
    assert decision.reason == "guarded_acceptance_rejected"
    assert decision.metadata["guarded_acceptance_block_reason"] == "missing_confirmed_key_evidence"


def test_guarded_lenient_blocks_negative_key_evidence() -> None:
    engine = StopRuleEngine(StopRuleConfig(acceptance_profile="guarded_lenient"))
    answer_score = _accepted_answer_score()
    state = SessionState(session_id="guarded", turn_index=3)
    state.evidence_states["oxygen"] = EvidenceState(
        node_id="oxygen",
        existence="non_exist",
        certainty="confident",
        metadata={
            "hypothesis_id": "pcp",
            "relation_type": "HAS_LAB_FINDING",
            "target_node_name": "低氧血症",
        },
    )

    decision = engine.should_accept_final_answer(answer_score, state)

    assert decision.should_stop is False
    assert decision.reason == "guarded_acceptance_rejected"
    assert decision.metadata["guarded_acceptance_block_reason"] == "hard_negative_key_evidence"
    assert answer_score.metadata["guarded_hard_negative_key_evidence"][0]["negative_evidence_tier"] == "hard"


def test_guarded_lenient_delays_on_soft_negative_without_prior_stability() -> None:
    engine = StopRuleEngine(StopRuleConfig(acceptance_profile="guarded_lenient"))
    answer_score = _accepted_answer_score()
    state = SessionState(session_id="guarded", turn_index=3)
    state.evidence_states["ct"] = EvidenceState(
        node_id="ct",
        existence="exist",
        certainty="confident",
        metadata={
            "hypothesis_id": "pcp",
            "relation_type": "HAS_LAB_FINDING",
            "target_node_name": "胸部CT磨玻璃影",
            "evidence_tags": ["imaging"],
        },
    )
    state.evidence_states["cd4"] = EvidenceState(
        node_id="cd4",
        existence="exist",
        certainty="confident",
        metadata={
            "hypothesis_id": "pcp",
            "relation_type": "HAS_LAB_FINDING",
            "target_node_name": "CD4+ T淋巴细胞计数 < 200/μL",
            "evidence_tags": ["immune_status", "type:lab"],
        },
    )
    state.evidence_states["resp_failure"] = EvidenceState(
        node_id="resp_failure",
        existence="unknown",
        certainty="doubt",
        metadata={
            "hypothesis_id": "alternative_pneumonia",
            "relation_type": "MANIFESTS_AS",
            "target_node_name": "呼吸衰竭",
            "evidence_tags": ["oxygenation", "type:symptom"],
        },
    )

    decision = engine.should_accept_final_answer(answer_score, state)

    assert decision.should_stop is False
    assert decision.metadata["guarded_acceptance_block_reason"] == "soft_negative_needs_stability"
    assert answer_score.metadata["guarded_soft_negative_or_doubtful_key_evidence"][0]["negative_evidence_tier"] == "soft"


def test_guarded_lenient_accepts_soft_negative_after_prior_stability() -> None:
    engine = StopRuleEngine(StopRuleConfig(acceptance_profile="guarded_lenient"))
    answer_score = _accepted_answer_score()
    state = SessionState(session_id="guarded", turn_index=3)
    state.metadata["verifier_accept_history"] = [
        {"turn_index": 2, "answer_id": "pcp", "answer_name": "肺孢子菌肺炎 (PCP)"}
    ]
    state.evidence_states["ct"] = EvidenceState(
        node_id="ct",
        existence="exist",
        certainty="confident",
        metadata={
            "hypothesis_id": "pcp",
            "relation_type": "HAS_LAB_FINDING",
            "target_node_name": "胸部CT磨玻璃影",
            "evidence_tags": ["imaging"],
        },
    )
    state.evidence_states["cd4"] = EvidenceState(
        node_id="cd4",
        existence="exist",
        certainty="confident",
        metadata={
            "hypothesis_id": "pcp",
            "relation_type": "HAS_LAB_FINDING",
            "target_node_name": "CD4+ T淋巴细胞计数 < 200/μL",
            "evidence_tags": ["immune_status", "type:lab"],
        },
    )
    state.evidence_states["resp_failure"] = EvidenceState(
        node_id="resp_failure",
        existence="unknown",
        certainty="doubt",
        metadata={
            "hypothesis_id": "alternative_pneumonia",
            "relation_type": "MANIFESTS_AS",
            "target_node_name": "呼吸衰竭",
            "evidence_tags": ["oxygenation", "type:symptom"],
        },
    )

    decision = engine.should_accept_final_answer(answer_score, state)

    assert decision.should_stop is True
    assert answer_score.metadata["guarded_soft_negative_requires_stability"] is False


def test_guarded_lenient_accepts_after_confirmed_key_evidence() -> None:
    engine = StopRuleEngine(StopRuleConfig(acceptance_profile="guarded_lenient"))
    answer_score = _accepted_answer_score()
    state = SessionState(session_id="guarded", turn_index=2)
    state.evidence_states["ct"] = EvidenceState(
        node_id="ct",
        existence="exist",
        certainty="confident",
        metadata={
            "hypothesis_id": "pcp",
            "relation_type": "HAS_LAB_FINDING",
            "target_node_name": "胸部CT磨玻璃影",
        },
    )
    state.evidence_states["cd4"] = EvidenceState(
        node_id="cd4",
        existence="exist",
        certainty="confident",
        metadata={
            "hypothesis_id": "pcp",
            "relation_type": "HAS_LAB_FINDING",
            "target_node_name": "CD4+ T淋巴细胞计数 < 200/μL",
            "evidence_tags": ["immune_status", "type:lab"],
        },
    )

    decision = engine.should_accept_final_answer(answer_score, state)

    assert decision.should_stop is True
    assert decision.reason == "final_answer_accepted"
    assert answer_score.metadata["guarded_pcp_combo_variant"] == "imaging_immune_status_lab"


def test_guarded_lenient_counts_shareable_immune_evidence_for_pcp_combo() -> None:
    engine = StopRuleEngine(StopRuleConfig(acceptance_profile="guarded_lenient"))
    answer_score = _accepted_answer_score()
    state = SessionState(session_id="guarded", turn_index=3)
    state.evidence_states["ct"] = EvidenceState(
        node_id="ct",
        existence="exist",
        certainty="confident",
        metadata={
            "hypothesis_id": "pcp",
            "relation_type": "HAS_LAB_FINDING",
            "target_node_name": "胸部CT磨玻璃影",
            "evidence_tags": ["imaging"],
        },
    )
    state.evidence_states["cd4"] = EvidenceState(
        node_id="cd4",
        existence="exist",
        certainty="confident",
        metadata={
            "hypothesis_id": "alternative_pneumonia",
            "relation_type": "HAS_LAB_FINDING",
            "target_node_name": "CD4+ T淋巴细胞计数 < 200/μL",
            "evidence_tags": ["immune_status", "type:lab"],
        },
    )

    decision = engine.should_accept_final_answer(answer_score, state)

    assert decision.should_stop is True
    confirmed = answer_score.metadata["guarded_confirmed_key_evidence"]
    assert any(item["name"] == "CD4+ T淋巴细胞计数 < 200/μL" for item in confirmed)
    assert any(item["evidence_scope"] == "shared_clinical" for item in confirmed)


def test_guarded_lenient_allows_weak_alternative_after_combo_satisfied() -> None:
    engine = StopRuleEngine(StopRuleConfig(acceptance_profile="guarded_lenient"))
    answer_score = _accepted_answer_score()
    answer_score.metadata["verifier_alternative_candidates"] = [
        {
            "answer_id": "tb",
            "answer_name": "活动性结核病",
            "reason": "缺乏结核关键证据，当前不如 PCP。仅作为一般候选。",
        }
    ]
    state = SessionState(session_id="guarded", turn_index=3)
    state.evidence_states["ct"] = EvidenceState(
        node_id="ct",
        existence="exist",
        certainty="confident",
        metadata={
            "hypothesis_id": "pcp",
            "relation_type": "HAS_LAB_FINDING",
            "target_node_name": "胸部CT磨玻璃影",
            "evidence_tags": ["imaging"],
        },
    )
    state.evidence_states["cd4"] = EvidenceState(
        node_id="cd4",
        existence="exist",
        certainty="confident",
        metadata={
            "hypothesis_id": "pcp",
            "relation_type": "HAS_LAB_FINDING",
            "target_node_name": "CD4+ T淋巴细胞计数 < 200/μL",
            "evidence_tags": ["immune_status", "type:lab"],
        },
    )

    decision = engine.should_accept_final_answer(answer_score, state)

    assert decision.should_stop is True
    assert answer_score.metadata["guarded_nonempty_alternative_candidates"] is True
    assert answer_score.metadata["guarded_has_strong_unresolved_alternative"] is False
    assert answer_score.metadata["guarded_weak_or_ruled_down_alternative_candidates"][0]["strength"] == "weak"


def test_guarded_lenient_blocks_only_strong_unresolved_alternative() -> None:
    engine = StopRuleEngine(StopRuleConfig(acceptance_profile="guarded_lenient"))
    answer_score = _accepted_answer_score()
    answer_score.metadata["verifier_alternative_candidates"] = [
        {
            "answer_id": "tb",
            "answer_name": "活动性结核病",
            "reason": "结核仍未排除，盗汗和体重下降同样支持该诊断。",
        }
    ]
    state = SessionState(session_id="guarded", turn_index=3)
    state.evidence_states["ct"] = EvidenceState(
        node_id="ct",
        existence="exist",
        certainty="confident",
        metadata={
            "hypothesis_id": "pcp",
            "relation_type": "HAS_LAB_FINDING",
            "target_node_name": "胸部CT磨玻璃影",
            "evidence_tags": ["imaging"],
        },
    )
    state.evidence_states["cd4"] = EvidenceState(
        node_id="cd4",
        existence="exist",
        certainty="confident",
        metadata={
            "hypothesis_id": "pcp",
            "relation_type": "HAS_LAB_FINDING",
            "target_node_name": "CD4+ T淋巴细胞计数 < 200/μL",
            "evidence_tags": ["immune_status", "type:lab"],
        },
    )

    decision = engine.should_accept_final_answer(answer_score, state)

    assert decision.should_stop is False
    assert decision.metadata["guarded_acceptance_block_reason"] == "strong_unresolved_alternative_candidates"
    assert answer_score.metadata["guarded_strong_alternative_candidates"][0]["strength"] == "strong"


def test_guarded_lenient_uses_provisional_anchor_combo_for_vague_pcp_support() -> None:
    engine = StopRuleEngine(StopRuleConfig(acceptance_profile="guarded_lenient"))
    answer_score = _accepted_answer_score()
    state = SessionState(session_id="guarded", turn_index=3)
    state.evidence_states["ct"] = EvidenceState(
        node_id="ct",
        existence="exist",
        certainty="doubt",
        metadata={
            "hypothesis_id": "pcp",
            "relation_type": "HAS_LAB_FINDING",
            "target_node_name": "胸部CT磨玻璃影",
            "evidence_tags": ["imaging", "type:imaging"],
        },
    )
    state.evidence_states["cd4"] = EvidenceState(
        node_id="cd4",
        existence="exist",
        certainty="doubt",
        metadata={
            "hypothesis_id": "pcp",
            "relation_type": "HAS_LAB_FINDING",
            "target_node_name": "CD4+ T淋巴细胞计数 < 200/μL",
            "evidence_tags": ["immune_status", "type:lab"],
        },
    )

    decision = engine.should_accept_final_answer(answer_score, state)

    assert decision.should_stop is True
    assert answer_score.metadata["guarded_pcp_combo_uses_provisional"] is True
    assert answer_score.metadata["guarded_confirmed_key_evidence_families"] == []
    assert answer_score.metadata["guarded_provisional_key_evidence_families"] == ["imaging", "immune_status"]
    assert answer_score.metadata["guarded_pcp_combo_variant"] == "imaging_immune_status_lab"


def test_guarded_lenient_still_blocks_single_provisional_anchor() -> None:
    engine = StopRuleEngine(StopRuleConfig(acceptance_profile="guarded_lenient"))
    answer_score = _accepted_answer_score()
    state = SessionState(session_id="guarded", turn_index=3)
    state.evidence_states["ct"] = EvidenceState(
        node_id="ct",
        existence="exist",
        certainty="doubt",
        metadata={
            "hypothesis_id": "pcp",
            "relation_type": "HAS_LAB_FINDING",
            "target_node_name": "胸部CT磨玻璃影",
            "evidence_tags": ["imaging", "type:imaging"],
        },
    )

    decision = engine.should_accept_final_answer(answer_score, state)

    assert decision.should_stop is False
    assert decision.metadata["guarded_acceptance_block_reason"] == "pcp_combo_insufficient"
    assert answer_score.metadata["guarded_pcp_combo_uses_provisional"] is False


def test_guarded_lenient_anchor_promotion_removes_cross_family_noise() -> None:
    engine = StopRuleEngine(StopRuleConfig(acceptance_profile="guarded_lenient"))
    answer_score = _accepted_answer_score()
    state = SessionState(session_id="guarded", turn_index=3)
    state.evidence_states["cd4"] = EvidenceState(
        node_id="cd4",
        existence="exist",
        certainty="confident",
        metadata={
            "hypothesis_id": "pcp",
            "relation_type": "HAS_LAB_FINDING",
            "target_node_name": "CD4+ T淋巴细胞计数 < 200/μL",
            "evidence_tags": ["imaging", "immune_status", "type:lab"],
        },
    )

    decision = engine.should_accept_final_answer(answer_score, state)

    assert decision.should_stop is False
    assert decision.metadata["guarded_acceptance_block_reason"] == "pcp_combo_insufficient"
    assert answer_score.metadata["guarded_confirmed_key_evidence_families"] == ["immune_status"]


def test_guarded_lenient_blocks_recent_hypothesis_switch() -> None:
    engine = StopRuleEngine(StopRuleConfig(acceptance_profile="guarded_lenient"))
    state = SessionState(session_id="guarded", turn_index=2)
    state.metadata["answer_candidate_history"] = [
        {"turn_index": 1, "answer_id": "acute", "answer_name": "急性期"},
    ]
    state.evidence_states["ct"] = EvidenceState(
        node_id="ct",
        existence="exist",
        certainty="confident",
        metadata={
            "hypothesis_id": "pcp",
            "relation_type": "HAS_LAB_FINDING",
            "evidence_tags": ["imaging"],
        },
    )
    state.evidence_states["cd4"] = EvidenceState(
        node_id="cd4",
        existence="exist",
        certainty="confident",
        metadata={
            "hypothesis_id": "pcp",
            "relation_type": "HAS_LAB_FINDING",
            "evidence_tags": ["immune_status"],
        },
    )

    decision = engine.should_accept_final_answer(_accepted_answer_score(), state)

    assert decision.should_stop is False
    assert decision.metadata["guarded_acceptance_block_reason"] == "recent_hypothesis_switch"


def test_guarded_lenient_requires_confirmed_key_evidence_for_high_risk_respiratory_answer() -> None:
    engine = StopRuleEngine(StopRuleConfig(acceptance_profile="guarded_lenient"))
    answer_score = _accepted_answer_score()
    state = SessionState(session_id="guarded", turn_index=4)

    decision = engine.should_accept_final_answer(answer_score, state)

    assert decision.should_stop is False
    assert decision.metadata["guarded_acceptance_block_reason"] == "missing_confirmed_key_evidence"


def test_guarded_lenient_blocks_pcp_acceptance_with_only_imaging_evidence() -> None:
    engine = StopRuleEngine(StopRuleConfig(acceptance_profile="guarded_lenient"))
    answer_score = _accepted_answer_score()
    state = SessionState(session_id="guarded", turn_index=3)
    state.evidence_states["ct"] = EvidenceState(
        node_id="ct",
        existence="exist",
        certainty="confident",
        metadata={
            "hypothesis_id": "pcp",
            "relation_type": "HAS_LAB_FINDING",
            "target_node_name": "胸部CT磨玻璃影",
            "evidence_tags": ["imaging", "type:imaging"],
        },
    )

    decision = engine.should_accept_final_answer(answer_score, state)

    assert decision.should_stop is False
    assert decision.metadata["guarded_acceptance_block_reason"] == "pcp_combo_insufficient"


def test_guarded_lenient_blocks_pcp_acceptance_with_only_immune_evidence() -> None:
    engine = StopRuleEngine(StopRuleConfig(acceptance_profile="guarded_lenient"))
    answer_score = _accepted_answer_score()
    state = SessionState(session_id="guarded", turn_index=3)
    state.evidence_states["cd4"] = EvidenceState(
        node_id="cd4",
        existence="exist",
        certainty="confident",
        metadata={
            "hypothesis_id": "pcp",
            "relation_type": "HAS_LAB_FINDING",
            "target_node_name": "CD4+ T淋巴细胞计数 < 200/μL",
            "evidence_tags": ["immune_status", "type:lab"],
        },
    )

    decision = engine.should_accept_final_answer(answer_score, state)

    assert decision.should_stop is False
    assert decision.metadata["guarded_acceptance_block_reason"] == "pcp_combo_insufficient"


def test_guarded_lenient_blocks_pcp_acceptance_with_imaging_and_oxygenation_only() -> None:
    engine = StopRuleEngine(StopRuleConfig(acceptance_profile="guarded_lenient"))
    answer_score = _accepted_answer_score()
    state = SessionState(session_id="guarded", turn_index=3)
    state.evidence_states["ct"] = EvidenceState(
        node_id="ct",
        existence="exist",
        certainty="confident",
        metadata={
            "hypothesis_id": "pcp",
            "relation_type": "HAS_LAB_FINDING",
            "target_node_name": "胸部CT磨玻璃影",
            "evidence_tags": ["imaging"],
        },
    )
    state.evidence_states["oxygen"] = EvidenceState(
        node_id="oxygen",
        existence="exist",
        certainty="confident",
        metadata={
            "hypothesis_id": "alternative_pneumonia",
            "relation_type": "HAS_LAB_FINDING",
            "target_node_name": "低氧血症",
            "evidence_tags": ["oxygenation"],
        },
    )

    decision = engine.should_accept_final_answer(answer_score, state)

    assert decision.should_stop is False
    assert decision.metadata["guarded_acceptance_block_reason"] == "pcp_combo_insufficient"
    assert answer_score.metadata["guarded_missing_evidence_families"] == [
        "immune_status",
        "pathogen",
        "pcp_specific",
    ]


def test_guarded_lenient_writes_gate_audit_for_blocked_accept_candidate() -> None:
    engine = StopRuleEngine(StopRuleConfig(acceptance_profile="guarded_lenient"))
    answer_score = _accepted_answer_score()
    state = SessionState(session_id="guarded", turn_index=3)
    state.evidence_states["ct"] = EvidenceState(
        node_id="ct",
        existence="exist",
        certainty="confident",
        source_turns=[2],
        metadata={
            "hypothesis_id": "pcp",
            "relation_type": "HAS_LAB_FINDING",
            "target_node_name": "胸部CT磨玻璃影",
            "evidence_tags": ["imaging"],
        },
    )

    decision = engine.should_accept_final_answer(answer_score, state)

    assert decision.should_stop is False
    audit = answer_score.metadata["guarded_gate_audit"]
    assert audit["block_reason"] == "pcp_combo_insufficient"
    assert audit["confirmed_evidence_families"] == ["imaging"]
    assert "immune_status" in audit["missing_families"]
    assert len(audit["recent_key_evidence_states"]) == 1
    assert state.metadata["guarded_gate_audit_history"][0]["block_reason"] == "pcp_combo_insufficient"


def test_guarded_lenient_uses_evidence_tags_for_confirmed_key_evidence() -> None:
    engine = StopRuleEngine(StopRuleConfig(acceptance_profile="guarded_lenient"))
    answer_score = FinalAnswerScore(
        answer_id="tb",
        answer_name="活动性结核病",
        consistency=0.9,
        diversity=0.4,
        agent_evaluation=0.92,
        final_score=0.8,
        metadata={
            "trajectory_count": 3,
            "verifier_mode": "llm_verifier",
            "verifier_should_accept": True,
            "verifier_accept_reason": "key_support_sufficient",
        },
    )
    state = SessionState(session_id="guarded", turn_index=3)
    state.evidence_states["tspot"] = EvidenceState(
        node_id="tspot",
        existence="exist",
        certainty="confident",
        metadata={
            "hypothesis_id": "tb",
            "relation_type": "ASSOCIATED_WITH",
            "target_node_name": "T-SPOT.TB 阳性",
            "evidence_tags": ["tuberculosis", "pathogen", "type:lab"],
        },
    )

    decision = engine.should_accept_final_answer(answer_score, state)

    assert decision.should_stop is True
    assert decision.reason == "final_answer_accepted"
