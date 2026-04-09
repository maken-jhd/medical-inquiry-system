"""测试轨迹评估器的分组评分与最佳答案选择。"""

from brain.trajectory_evaluator import TrajectoryEvaluator
from brain.types import ReasoningTrajectory


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
