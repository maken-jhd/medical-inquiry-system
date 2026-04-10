"""测试 repair-aware A3 动作构造 metadata。"""

from brain.action_builder import ActionBuilder
from brain.types import HypothesisScore


# 验证 A3 动作会显式区分 verifier 推荐证据、原 hypothesis 推荐证据和共同命中信号。
def test_action_builder_tracks_joint_recommended_evidence_match() -> None:
    builder = ActionBuilder()
    hypothesis = HypothesisScore(
        node_id="pcp",
        label="Disease",
        name="肺孢子菌肺炎 (PCP)",
        score=1.0,
        metadata={
            "recommended_next_evidence": ["获取胸部CT结果", "询问免疫状态"],
            "hypothesis_recommended_next_evidence": ["胸部CT结果"],
            "verifier_recommended_next_evidence": ["胸部CT或X线结果"],
        },
    )
    actions = builder.build_verification_actions(
        [
            {
                "node_id": "sign_ct",
                "label": "Sign",
                "name": "胸部CT磨玻璃影",
                "relation_type": "DIAGNOSED_BY",
                "relation_weight": 0.85,
                "node_weight": 1.0,
                "similarity_confidence": 1.0,
                "contradiction_priority": 0.9,
                "question_type_hint": "detail",
                "priority": 2.5,
                "is_red_flag": False,
                "topic_id": "Disease",
            }
        ],
        hypothesis_id="pcp",
        current_hypothesis=hypothesis,
    )

    assert len(actions) == 1
    metadata = actions[0].metadata
    assert metadata["verifier_recommended_match_score"] > 0.0
    assert metadata["hypothesis_recommended_match_score"] > 0.0
    assert metadata["joint_recommended_match_score"] > 0.0
    assert "imaging" in metadata["evidence_tags"]
