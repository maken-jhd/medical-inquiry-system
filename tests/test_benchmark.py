"""测试批量回放指标汇总逻辑。"""

from simulator.benchmark import build_non_completed_case_report, summarize_benchmark
from simulator.replay_engine import ReplayResult, ReplayTurn


# 验证评测汇总能够正确统计完成率、候选命中率、最终答案准确率和红旗覆盖率。
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
                "best_final_answer": {"answer_name": "肺孢子菌肺炎 (PCP)"},
                "confirmed_slots": [
                    {"node_id": "低氧血症", "status": "true"},
                ],
                "stop_reason": "final_answer_accepted",
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
                    {"name": "活动性结核病"},
                ],
                "answer_group_scores": [
                    {"answer_name": "活动性结核病"},
                ],
                "confirmed_slots": [],
                "stop_reason": "anchor_controlled_rejected",
            },
            status="max_turn_reached",
        ),
        ReplayResult(
            case_id="case3",
            case_title="case3",
            true_conditions=["非结核分枝杆菌肺病"],
            red_flags=[],
            turns=[],
            final_report={
                "candidate_hypotheses": [
                    {"name": "非结核分枝杆菌病"},
                ],
                "answer_group_scores": [
                    {"answer_name": "非结核分枝杆菌病"},
                ],
                "confirmed_slots": [],
                "stop_reason": "final_answer_accepted",
            },
            status="completed",
        ),
        ReplayResult(
            case_id="case4",
            case_title="case4",
            true_conditions=["血脂异常"],
            red_flags=[],
            turns=[],
            final_report={
                "candidate_hypotheses": [
                    {"name": "肥胖"},
                ],
                "answer_group_scores": [
                    {"answer_name": "肥胖"},
                ],
                "confirmed_slots": [],
                "stop_reason": "final_answer_accepted",
            },
            status="completed",
        ),
    ]

    summary = summarize_benchmark(results)

    assert summary.case_count == 4
    assert summary.completed_count == 3
    assert summary.max_turn_reached_count == 1
    assert summary.hypothesis_hit_count == 3
    assert summary.top3_hypothesis_hit_count == 3
    assert summary.final_answer_count == 4
    assert summary.final_answer_exact_hit_count == 2
    assert summary.top1_final_answer_hit_count == 2
    assert summary.final_answer_family_hit_count == 3
    assert summary.accepted_final_answer_count == 3
    assert summary.accepted_exact_hit_count == 1
    assert summary.accepted_family_hit_count == 2
    assert summary.wrong_accepted_count == 2
    assert summary.family_wrong_accepted_count == 1
    assert summary.top_exact_correct_but_rejected_count == 1
    assert summary.top_family_correct_but_rejected_count == 1
    assert summary.red_flag_case_count == 1
    assert summary.red_flag_hit_count == 1


# 验证未完成病例报告会按 max-turn / failed 等原因分类，便于全量 benchmark 后快速复盘。
def test_build_non_completed_case_report_groups_abnormal_cases() -> None:
    results = [
        ReplayResult(
            case_id="case1",
            case_title="正确但未放行",
            true_conditions=["活动性结核病"],
            final_report={
                "candidate_hypotheses": [{"name": "活动性结核病"}],
                "answer_group_scores": [{"answer_name": "活动性结核病"}],
                "stop_reason": "anchor_controlled_rejected",
            },
            status="max_turn_reached",
        ),
        ReplayResult(
            case_id="case2",
            case_title="候选命中但最终错",
            true_conditions=["血脂异常"],
            final_report={
                "candidate_hypotheses": [{"name": "血脂异常"}, {"name": "肥胖"}],
                "answer_group_scores": [{"answer_name": "肥胖"}],
                "stop_reason": "max_turn_reached",
            },
            status="max_turn_reached",
        ),
        ReplayResult(
            case_id="case3",
            case_title="运行失败",
            true_conditions=["肺孢子菌肺炎"],
            error={"code": "unexpected_runtime_error", "stage": "batch_runner"},
            status="failed",
        ),
        ReplayResult(
            case_id="case4",
            case_title="已完成",
            true_conditions=["肺孢子菌肺炎"],
            final_report={"best_final_answer": {"answer_name": "肺孢子菌肺炎"}},
            status="completed",
        ),
    ]

    report = build_non_completed_case_report(results)

    assert report["case_count"] == 4
    assert report["non_completed_count"] == 3
    assert report["category_breakdown"] == {
        "failed::unexpected_runtime_error": 1,
        "max_turn_reached::top_exact_correct_but_rejected": 1,
        "max_turn_reached::true_candidate_but_final_wrong": 1,
    }
    assert [item["case_id"] for item in report["cases"]] == ["case1", "case2", "case3"]
    assert report["cases"][0]["final_answer_exact_hit"] is True
    assert report["cases"][0]["top1_final_answer_hit"] is True
    assert report["cases"][1]["hypothesis_hit"] is True
    assert report["cases"][1]["top3_hypothesis_hit"] is True
    assert report["cases"][2]["error"]["stage"] == "batch_runner"
