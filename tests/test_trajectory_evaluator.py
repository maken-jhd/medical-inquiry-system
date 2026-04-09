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
        _ = variables
        _ = schema
        assert prompt_name == "trajectory_agent_verifier"
        return {"score": 0.88, "reasoning": "最佳轨迹与患者上下文一致。"}


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
