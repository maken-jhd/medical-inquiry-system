"""测试最终推理报告会输出解释性字段。"""

from brain.report_builder import ReportBuilder
from brain.types import FinalAnswerScore, ReasoningTrajectory, SearchResult, SessionState, StopDecision


# 验证 build_final_reasoning_report 会包含答案胜出原因与路径摘要。
def test_report_builder_includes_reasoning_summary_fields() -> None:
    builder = ReportBuilder()
    state = SessionState(session_id="s1")
    search_result = SearchResult(
        best_answer_id="d1",
        best_answer_name="肺孢子菌肺炎",
        trajectories=[
            ReasoningTrajectory(
                trajectory_id="t1",
                final_answer_id="d1",
                final_answer_name="肺孢子菌肺炎",
                steps=[
                    {"action_name": "发热"},
                    {"action_name": "低氧血症"},
                ],
                score=0.9,
            )
        ],
        final_answer_scores=[
            FinalAnswerScore(
                answer_id="d1",
                answer_name="肺孢子菌肺炎",
                consistency=0.7,
                diversity=0.5,
                agent_evaluation=0.8,
                final_score=0.66,
            ),
            FinalAnswerScore(
                answer_id="d2",
                answer_name="肺结核",
                consistency=0.3,
                diversity=0.4,
                agent_evaluation=0.5,
                final_score=0.4,
            ),
        ],
    )

    report = builder.build_final_reasoning_report(
        state,
        StopDecision(should_stop=True, reason="final_answer_accepted", confidence=0.66),
        search_result,
    )

    assert report["best_final_answer"]["answer_id"] == "d1"
    assert "肺孢子菌肺炎" in report["why_this_answer_wins"]
    assert "发热" in report["trajectory_summary"]
    assert report["evidence_for_best_answer"] == ["发热", "低氧血症"]
