"""测试轨迹评估器的分组评分与最佳答案选择。"""

from brain.trajectory_evaluator import TrajectoryEvaluator, TrajectoryEvaluatorConfig
from brain.types import PatientContext, ReasoningTrajectory


# 验证轨迹评估器会优先选择轨迹数量更多且得分更高的答案。
def test_trajectory_evaluator_prefers_more_consistent_answer_group() -> None:
    evaluator = TrajectoryEvaluator()
    trajectories = [
        ReasoningTrajectory(
            trajectory_id="t1",
            final_answer_id="d1",
            final_answer_name="肺孢子菌肺炎",
            steps=[{"action_name": "发热"}],
            score=0.8,
        ),
        ReasoningTrajectory(
            trajectory_id="t2",
            final_answer_id="d1",
            final_answer_name="肺孢子菌肺炎",
            steps=[{"action_name": "低氧血症"}],
            score=0.9,
        ),
        ReasoningTrajectory(
            trajectory_id="t3",
            final_answer_id="d2",
            final_answer_name="结核病",
            steps=[{"action_name": "盗汗"}],
            score=0.4,
        ),
    ]

    grouped = evaluator.group_by_answer(trajectories)
    scores = evaluator.score_groups(grouped)
    best = evaluator.select_best_answer(scores)

    assert best is not None
    assert best.answer_id == "d1"


class FakeVerifierClient:
    """模拟 trajectory agent verifier 的结构化输出。"""

    def is_available(self) -> bool:
        return True

    def run_structured_prompt(self, prompt_name: str, variables: dict, schema: type) -> dict:
        assert "answer_candidates" in variables
        _ = schema
        assert prompt_name == "trajectory_agent_verifier"
        return {
            "score": 0.88,
            "should_accept_stop": False,
            "reject_reason": "missing_key_support",
            "reasoning": "最佳轨迹与患者上下文一致，但缺少关键支持证据。",
            "missing_evidence": ["低氧血症"],
            "risk_flags": ["支持证据不足"],
            "recommended_next_evidence": ["低氧血症"],
            "alternative_candidates": [{"answer_name": "结核病", "reason": "尚未完成鉴别"}],
        }


class InvalidReasonVerifierClient:
    """模拟 verifier 未遵守 reject_reason 枚举时的兼容兜底。"""

    def is_available(self) -> bool:
        return True

    def run_structured_prompt(self, prompt_name: str, variables: dict, schema: type) -> dict:
        _ = prompt_name, variables, schema
        return {
            "score": 0.42,
            "should_accept_stop": "false",
            "reject_reason": "needs_more_work",
            "reasoning": "强替代诊断尚未排除，需要继续鉴别。",
            "missing_evidence": [],
            "risk_flags": ["替代诊断未排除"],
            "recommended_next_evidence": ["核酸检测"],
            "alternative_candidates": [{"answer_name": "新型冠状病毒感染", "reason": "需要排除"}],
        }


# 验证当启用 llm_verifier 模式时，agent evaluation 会消费 verifier 分数。
def test_trajectory_evaluator_supports_llm_verifier_mode() -> None:
    evaluator = TrajectoryEvaluator(
        TrajectoryEvaluatorConfig(agent_eval_mode="llm_verifier"),
        llm_client=FakeVerifierClient(),  # type: ignore[arg-type]
    )
    trajectories = [
        ReasoningTrajectory(
            trajectory_id="t1",
            final_answer_id="d1",
            final_answer_name="肺孢子菌肺炎",
            steps=[{"action_name": "发热"}, {"action_name": "低氧血症"}],
            score=0.8,
            metadata={"path_terminal": True},
        )
    ]

    grouped = evaluator.group_by_answer(trajectories)
    scores = evaluator.score_groups(grouped, patient_context=PatientContext(raw_text="发热伴呼吸困难"))

    assert len(scores) == 1
    assert scores[0].agent_evaluation == 0.88
    assert scores[0].metadata["verifier_reject_reason"] == "missing_key_support"
    assert scores[0].metadata["verifier_recommended_next_evidence"] == ["低氧血症"]
    assert scores[0].metadata["verifier_alternative_candidates"][0]["answer_name"] == "结核病"
    assert scores[0].metadata["verifier_reject_reason_source"] == "llm_schema"
    assert scores[0].metadata["verifier_schema_valid"] is True


# 验证 verifier schema 不合规时会记录 fallback 来源，并正确解析字符串布尔值。
def test_trajectory_evaluator_marks_invalid_verifier_reject_reason_schema() -> None:
    evaluator = TrajectoryEvaluator(
        TrajectoryEvaluatorConfig(agent_eval_mode="llm_verifier"),
        llm_client=InvalidReasonVerifierClient(),  # type: ignore[arg-type]
    )
    trajectories = [
        ReasoningTrajectory(
            trajectory_id="t1",
            final_answer_id="d1",
            final_answer_name="肺孢子菌肺炎",
            steps=[{"action_name": "发热"}],
            score=0.5,
        )
    ]

    grouped = evaluator.group_by_answer(trajectories)
    scores = evaluator.score_groups(grouped, patient_context=PatientContext(raw_text="发热"))

    assert scores[0].metadata["verifier_should_accept"] is False
    assert scores[0].metadata["verifier_reject_reason"] == "strong_alternative_not_ruled_out"
    assert scores[0].metadata["verifier_reject_reason_source"] == "fallback_inferred"
    assert scores[0].metadata["verifier_schema_valid"] is False
