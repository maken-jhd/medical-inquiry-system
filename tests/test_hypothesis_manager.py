"""测试 A2 假设管理中的竞争性重排、极性计分与 LLM metadata 回写。"""

from brain.hypothesis_manager import HypothesisManager
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
