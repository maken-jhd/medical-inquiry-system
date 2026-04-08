"""测试批量回放指标汇总逻辑。"""

from simulator.benchmark import summarize_benchmark
from simulator.replay_engine import ReplayResult, ReplayTurn


# 验证评测汇总能够正确统计完成率、命中率和红旗覆盖率。
def test_summarize_benchmark_returns_expected_metrics() -> None:
    results = [
        ReplayResult(
            case_id="case1",
            case_title="case1",
            true_conditions=["肺孢子菌肺炎 (PCP)"],
            red_flags=["低氧血症"],
            turns=[
                ReplayTurn(
                    question_node_id="q1",
                    question_text="是否存在低氧血症？",
                    answer_text="有。",
                    turn_index=1,
                    revealed_slot_id="低氧血症",
                )
            ],
            final_report={
                "candidate_hypotheses": [
                    {"name": "肺孢子菌肺炎 (PCP)"},
                ],
                "confirmed_slots": [
                    {"node_id": "低氧血症", "status": "true"},
                ],
            },
            status="completed",
        ),
        ReplayResult(
            case_id="case2",
            case_title="case2",
            true_conditions=["活动性结核病"],
            red_flags=[],
            turns=[],
            final_report={
                "candidate_hypotheses": [
                    {"name": "肺孢子菌肺炎 (PCP)"},
                ],
                "confirmed_slots": [],
            },
            status="max_turn_reached",
        ),
    ]

    summary = summarize_benchmark(results)

    assert summary.case_count == 2
    assert summary.completed_count == 1
    assert summary.max_turn_reached_count == 1
    assert summary.hypothesis_hit_count == 1
    assert summary.red_flag_case_count == 1
    assert summary.red_flag_hit_count == 1
