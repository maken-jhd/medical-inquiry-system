"""测试 A2 假设管理中的竞争性重排、极性计分与 Top-3 候选保留。"""

from brain.hypothesis_manager import HypothesisManager, HypothesisManagerConfig
from brain.types import EvidenceState, HypothesisCandidate, HypothesisScore, PatientContext


class FakeLlmClient:
    """返回固定 A2 结构化结果，验证 metadata 是否被回写到主假设。"""

    def is_available(self) -> bool:
        return True

    def run_structured_prompt(self, prompt_name: str, variables: dict, schema: type) -> dict:
        _ = variables
        _ = schema
        assert prompt_name == "a2_hypothesis_generation"
        return {
            "primary_hypothesis": {
                "node_id": "d1",
                "name": "肺孢子菌肺炎 (PCP)",
                "label": "Disease",
            },
            "alternatives": [
                {
                    "node_id": "d2",
                    "name": "活动性结核病",
                    "label": "Disease",
                }
            ],
            "reasoning": "PCP 与当前症状组合更一致。",
            "supporting_features": ["发热", "干咳"],
            "conflicting_features": ["无明显盗汗"],
            "why_primary_beats_alternatives": "PCP 更能同时解释发热和干咳。",
            "recommended_next_evidence": ["低氧血症"],
        }


# 验证 LLM A2 结果会把竞争性 metadata 写回主假设和备选假设。
def test_hypothesis_manager_attaches_llm_competition_metadata() -> None:
    manager = HypothesisManager(FakeLlmClient())  # type: ignore[arg-type]
    candidates = [
        HypothesisCandidate(
            node_id="d1",
            name="肺孢子菌肺炎 (PCP)",
            label="Disease",
            score=0.8,
            metadata={"evidence_names": ["发热", "干咳"], "feature_coverage": 0.9, "semantic_score": 0.82},
        ),
        HypothesisCandidate(
            node_id="d2",
            name="活动性结核病",
            label="Disease",
            score=0.75,
            metadata={"evidence_names": ["发热"], "feature_coverage": 0.5, "semantic_score": 0.66},
        ),
    ]

    result = manager.run_a2_hypothesis_generation(PatientContext(raw_text="发热伴干咳"), candidates)

    assert result.primary_hypothesis is not None
    assert result.primary_hypothesis.metadata["recommended_next_evidence"] == ["低氧血症"]
    assert result.primary_hypothesis.metadata["competition_role"] == "primary"
    assert result.alternatives[0].metadata["competition_role"] == "alternative"


# 验证 verifier 指出强替代假设未排除时，A2 会显式重排 hypothesis 分数。
def test_hypothesis_manager_applies_verifier_reshuffle() -> None:
    manager = HypothesisManager()
    hypotheses = [
        HypothesisScore(node_id="phase_acute", label="Disease", name="急性期", score=1.0, metadata={}),
        HypothesisScore(node_id="disease_pcp", label="Disease", name="肺孢子菌肺炎 (PCP)", score=0.82, metadata={}),
    ]

    reranked = manager.apply_verifier_repair(
        hypotheses,
        current_answer_id="phase_acute",
        reject_reason="strong_alternative_not_ruled_out",
        recommended_next_evidence=["低氧血症"],
        alternative_candidates=[{"answer_id": "disease_pcp", "answer_name": "肺孢子菌肺炎 (PCP)", "reason": "红旗证据尚未排除"}],
    )

    assert reranked[0].node_id == "disease_pcp"
    assert reranked[0].metadata["verifier_alternative_reason"] == "红旗证据尚未排除"
    assert reranked[0].metadata["recommended_next_evidence"] == ["低氧血症"]
    assert reranked[0].metadata["hypothesis_recommended_next_evidence"] == []
    assert reranked[0].metadata["verifier_recommended_next_evidence"] == ["低氧血症"]


# 验证 guarded 的细粒度 repair reason 会被 hypothesis 重排识别。
def test_hypothesis_manager_handles_guarded_repair_reasons() -> None:
    manager = HypothesisManager()
    hypotheses = [
        HypothesisScore(node_id="current", label="Disease", name="当前答案", score=1.0, metadata={}),
        HypothesisScore(node_id="alt", label="Disease", name="强备选", score=0.8, metadata={}),
    ]

    hard_negative = manager.apply_verifier_repair(
        hypotheses,
        current_answer_id="current",
        reject_reason="hard_negative_key_evidence",
    )
    strong_alternative = manager.apply_verifier_repair(
        hypotheses,
        current_answer_id="current",
        reject_reason="strong_unresolved_alternative_candidates",
        alternative_candidates=[{"answer_id": "alt", "reason": "guarded strong alternative"}],
    )

    current_after_hard_negative = next(item for item in hard_negative if item.node_id == "current")
    assert current_after_hard_negative.metadata["verifier_reject_reason"] == "hard_negative_key_evidence"
    assert current_after_hard_negative.score < 1.0
    assert strong_alternative[0].node_id == "alt"
    assert strong_alternative[0].metadata["verifier_alternative_reason"] == "guarded strong alternative"


# 验证连续缺少关键支持时，repair feedback 会被转成更强的当前答案降权。
def test_hypothesis_manager_uses_repeated_repair_feedback_count() -> None:
    manager = HypothesisManager()
    hypotheses = [
        HypothesisScore(node_id="current", label="Disease", name="当前答案", score=1.0, metadata={}),
        HypothesisScore(
            node_id="alt",
            label="Disease",
            name="备选答案",
            score=0.88,
            metadata={"exact_scope_anchor_score": 0.8},
        ),
    ]

    reranked = manager.apply_verifier_repair(
        hypotheses,
        current_answer_id="current",
        reject_reason="missing_required_anchor",
        alternative_candidates=[{"answer_id": "alt", "answer_name": "备选答案", "reason": "已有真实锚点支持"}],
        repair_feedback_counts={"current": {"missing_required_anchor": 3}},
    )
    current = next(item for item in reranked if item.node_id == "current")
    alt = next(item for item in reranked if item.node_id == "alt")

    assert current.metadata["repair_feedback_count"] == 3
    assert current.score < 0.7
    assert alt.metadata["verifier_observed_anchor_alt_bonus"] > 0.0


# 验证 evidence_state 即使还保留旧 existence 字段，也会优先按 polarity 做分数调整。
def test_hypothesis_manager_scores_unclear_and_absent_by_polarity() -> None:
    manager = HypothesisManager()
    hypotheses = [HypothesisScore(node_id="d1", label="Disease", name="PCP", score=1.0, metadata={})]

    unclear_updated = manager.apply_evidence_feedback(
        hypotheses,
        EvidenceState(
            node_id="symptom_fatigue",
            polarity="unclear",
            existence="unknown",
            resolution="hedged",
            metadata={"relation_type": "MANIFESTS_AS"},
        ),
        ["d1"],
    )
    absent_updated = manager.apply_evidence_feedback(
        hypotheses,
        EvidenceState(
            node_id="lab_po2",
            polarity="absent",
            existence="unknown",
            resolution="clear",
            metadata={"relation_type": "HAS_LAB_FINDING"},
        ),
        ["d1"],
    )

    assert unclear_updated[0].score < hypotheses[0].score
    assert absent_updated[0].score < unclear_updated[0].score


# 验证真实证据会同时反馈到多个共享该证据的候选，而不是只更新当前 hypothesis。
def test_hypothesis_manager_fans_out_feedback_to_multiple_related_hypotheses() -> None:
    manager = HypothesisManager()
    hypotheses = [
        HypothesisScore(
            node_id="generic_pneumonia",
            label="Disease",
            name="原发性肺部感染",
            score=1.0,
            metadata={
                "relation_types": ["HAS_LAB_FINDING"],
                "evidence_node_ids": ["lab_bdg"],
                "anchor_tier": "background_supported",
                "observed_anchor_score": 0.0,
            },
        ),
        HypothesisScore(
            node_id="pcp",
            label="Disease",
            name="肺孢子菌肺炎 (PCP)",
            score=0.88,
            metadata={
                "relation_types": ["HAS_LAB_FINDING"],
                "evidence_node_ids": ["lab_bdg"],
                "anchor_tier": "strong_anchor",
                "observed_anchor_score": 0.72,
                "exact_scope_anchor_score": 0.68,
            },
        ),
        HypothesisScore(
            node_id="obesity",
            label="Disease",
            name="肥胖",
            score=0.76,
            metadata={
                "relation_types": ["REQUIRES_DETAIL"],
                "evidence_node_ids": ["bmi_high"],
            },
        ),
    ]
    evidence_state = EvidenceState(
        node_id="lab_bdg",
        polarity="present",
        existence="exist",
        resolution="clear",
        metadata={"relation_type": "HAS_LAB_FINDING", "target_node_name": "β-D 葡聚糖升高"},
    )

    feedback_weights = manager.resolve_evidence_feedback_weights(
        hypotheses,
        evidence_state,
        related_hypothesis_ids=["generic_pneumonia"],
    )
    updated = manager.apply_evidence_feedback(
        hypotheses,
        evidence_state,
        ["generic_pneumonia"],
        feedback_weights=feedback_weights,
    )
    by_id = {item.node_id: item for item in updated}

    assert set(feedback_weights) == {"generic_pneumonia", "pcp"}
    assert feedback_weights["pcp"] > feedback_weights["generic_pneumonia"]
    assert by_id["generic_pneumonia"].score > 1.0
    assert by_id["pcp"].score > 0.88
    assert by_id["obesity"].score == 0.76


def _build_top3_rescue_manager() -> HypothesisManager:
    return HypothesisManager(
        config=HypothesisManagerConfig(
            enable_top3_candidate_rescue=True,
            top3_rescue_rank_window=8,
            top3_rescue_bonus=0.09,
            enable_candidate_rank_memory=True,
            candidate_rank_memory_bonus=0.06,
            candidate_rank_memory_decay=0.6,
            enable_evidence_supported_rerank=True,
            positive_evidence_support_weight=0.05,
            evidence_family_diversity_weight=0.035,
            contradiction_penalty_weight=0.055,
        )
    )


# 验证 rank 4~8 且已有真实支持的候选，会获得 Top-3 rescue 与 evidence-supported bonus。
def test_hypothesis_manager_rescues_supported_candidate_toward_top3() -> None:
    manager = _build_top3_rescue_manager()
    hypotheses = [
        HypothesisScore(node_id="top1", label="Disease", name="第一候选", score=1.12, metadata={}),
        HypothesisScore(node_id="top2", label="Disease", name="第二候选", score=1.04, metadata={}),
        HypothesisScore(node_id="top3", label="Disease", name="第三候选", score=0.97, metadata={}),
        HypothesisScore(
            node_id="gold",
            label="Disease",
            name="金标准候选",
            score=0.88,
            metadata={
                "positive_evidence_support_count": 2,
                "positive_evidence_support_score": 0.75,
                "positive_evidence_support_families": ["symptom", "pathogen"],
                "exact_scope_anchor_score": 0.55,
                "family_scope_anchor_score": 0.34,
                "best_rank_seen": 3,
                "consecutive_presence_count": 2,
            },
        ),
        HypothesisScore(node_id="tail", label="Disease", name="尾部候选", score=0.84, metadata={}),
    ]

    reranked = manager.refresh_candidate_ranking(hypotheses, reset_candidate_raw_score=True)
    gold = next(item for item in reranked if item.node_id == "gold")

    assert gold.metadata["positive_evidence_support_bonus"] > 0.0
    assert gold.metadata["top3_rescue_bonus"] > 0.0
    assert gold.metadata["rank_memory_bonus"] > 0.0
    assert gold.metadata["new_rank"] <= 3


# 验证强 negative / contradiction 会压制 rescue，不会把脆弱候选硬拉回 Top-3。
def test_hypothesis_manager_contradiction_blocks_top3_rescue() -> None:
    manager = _build_top3_rescue_manager()
    hypotheses = [
        HypothesisScore(node_id="top1", label="Disease", name="第一候选", score=1.12, metadata={}),
        HypothesisScore(node_id="top2", label="Disease", name="第二候选", score=1.03, metadata={}),
        HypothesisScore(node_id="top3", label="Disease", name="第三候选", score=0.96, metadata={}),
        HypothesisScore(
            node_id="fragile",
            label="Disease",
            name="脆弱候选",
            score=0.9,
            metadata={
                "positive_evidence_support_count": 2,
                "positive_evidence_support_score": 0.72,
                "positive_evidence_support_families": ["symptom", "pathogen"],
                "exact_scope_anchor_score": 0.46,
                "negative_evidence_support_count": 2,
                "negative_evidence_support_score": 1.05,
                "anchor_negative_score": 0.82,
                "scope_mismatch_score": 0.42,
                "best_rank_seen": 3,
                "consecutive_presence_count": 2,
            },
        ),
        HypothesisScore(node_id="tail", label="Disease", name="尾部候选", score=0.86, metadata={}),
    ]

    reranked = manager.refresh_candidate_ranking(hypotheses, reset_candidate_raw_score=True)
    fragile = next(item for item in reranked if item.node_id == "fragile")

    assert fragile.metadata["contradiction_penalty"] > 0.0
    assert fragile.metadata["top3_rescue_bonus"] == 0.0
    assert fragile.metadata["new_rank"] > 3


# 验证 rank memory 不是无条件兜底：若已无正支持且存在明显冲突，不会把旧候选直接推回 Top-3。
def test_hypothesis_manager_rank_memory_needs_current_support() -> None:
    manager = _build_top3_rescue_manager()
    hypotheses = [
        HypothesisScore(node_id="top1", label="Disease", name="第一候选", score=1.08, metadata={}),
        HypothesisScore(node_id="top2", label="Disease", name="第二候选", score=1.0, metadata={}),
        HypothesisScore(node_id="top3", label="Disease", name="第三候选", score=0.96, metadata={}),
        HypothesisScore(node_id="top4", label="Disease", name="第四候选", score=0.94, metadata={}),
        HypothesisScore(node_id="top5", label="Disease", name="第五候选", score=0.91, metadata={}),
        HypothesisScore(
            node_id="stale",
            label="Disease",
            name="旧候选",
            score=0.87,
            metadata={
                "best_rank_seen": 2,
                "consecutive_presence_count": 4,
                "anchor_negative_score": 0.75,
                "scope_mismatch_score": 0.38,
            },
        ),
    ]

    reranked = manager.refresh_candidate_ranking(hypotheses, reset_candidate_raw_score=True)
    stale = next(item for item in reranked if item.node_id == "stale")

    assert stale.metadata["rank_memory_bonus"] == 0.0
    assert stale.metadata["new_rank"] > 3


# 验证弱 rescue 候选不会轻易压过明显更强的 Top-1 候选，避免把当前 Top-1 稳定性打坏。
def test_hypothesis_manager_does_not_let_weak_rescue_overtake_strong_top1() -> None:
    manager = _build_top3_rescue_manager()
    hypotheses = [
        HypothesisScore(
            node_id="leader",
            label="Disease",
            name="强 Top1",
            score=1.3,
            metadata={
                "positive_evidence_support_count": 2,
                "positive_evidence_support_score": 0.62,
                "positive_evidence_support_families": ["symptom"],
            },
        ),
        HypothesisScore(node_id="top2", label="Disease", name="第二候选", score=1.02, metadata={}),
        HypothesisScore(node_id="top3", label="Disease", name="第三候选", score=0.97, metadata={}),
        HypothesisScore(
            node_id="rescued",
            label="Disease",
            name="可救回候选",
            score=0.94,
            metadata={
                "positive_evidence_support_count": 1,
                "positive_evidence_support_score": 0.55,
                "positive_evidence_support_families": ["pathogen", "symptom"],
                "exact_scope_anchor_score": 0.42,
                "best_rank_seen": 3,
                "consecutive_presence_count": 2,
            },
        ),
    ]

    reranked = manager.refresh_candidate_ranking(hypotheses, reset_candidate_raw_score=True)

    assert reranked[0].node_id == "leader"
    rescued = next(item for item in reranked if item.node_id == "rescued")
    assert rescued.metadata["top3_rescue_bonus"] > 0.0
