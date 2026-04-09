"""测试 A2 假设管理中的竞争性重排与 LLM metadata 回写。"""

from brain.hypothesis_manager import HypothesisManager
from brain.types import HypothesisCandidate, PatientContext


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
