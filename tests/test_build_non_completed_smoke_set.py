"""测试从未完成 replay 结果抽取 smoke 病例集。"""

from pathlib import Path

from scripts.build_non_completed_smoke_set import (
    build_non_completed_smoke_payload,
    render_non_completed_smoke_markdown,
)
from simulator.case_schema import VirtualPatientCase


# 验证脚本会按 non_completed_cases.json 中的 case_id 精确抽取病例并保留分类信息。
def test_build_non_completed_smoke_payload_selects_cases_by_report() -> None:
    cases = [
        VirtualPatientCase(
            case_id="case1",
            title="case1",
            true_conditions=["疾病1"],
            metadata={"case_type": "ordinary"},
        ),
        VirtualPatientCase(
            case_id="case2",
            title="case2",
            true_conditions=["疾病2"],
            metadata={"case_type": "low_cost"},
        ),
        VirtualPatientCase(
            case_id="case3",
            title="case3",
            true_conditions=["疾病3"],
            metadata={"case_type": "exam_driven"},
        ),
    ]
    non_completed_payload = {
        "non_completed_count": 3,
        "cases": [
            {
                "case_id": "case1",
                "category": "max_turn_reached::top_exact_correct_but_rejected",
                "true_conditions": ["疾病1"],
                "final_answer_name": "疾病1",
                "stop_reason": "anchor_controlled_rejected",
            },
            {
                "case_id": "case2",
                "category": "max_turn_reached::true_candidate_missing",
                "true_conditions": ["疾病2"],
                "final_answer_name": "疾病X",
                "stop_reason": "verifier_rejected_stop",
            },
            {
                "case_id": "missing_case",
                "category": "max_turn_reached::no_final_answer",
                "true_conditions": ["疾病4"],
                "final_answer_name": "",
                "stop_reason": "no_hypothesis",
            },
        ],
    }

    selected_cases, manifest = build_non_completed_smoke_payload(
        cases=cases,
        non_completed_payload=non_completed_payload,
        source_cases_file=Path("cases.jsonl"),
        non_completed_file=Path("non_completed_cases.json"),
        output_root=Path("non_completed_smoke"),
        include_categories=(),
        limit=0,
    )

    assert [case.case_id for case in selected_cases] == ["case1", "case2"]
    assert manifest["missing_case_ids"] == ["missing_case"]
    assert manifest["selected_category_breakdown"] == {
        "max_turn_reached::top_exact_correct_but_rejected": 1,
        "max_turn_reached::true_candidate_missing": 1,
    }
    assert manifest["selected_case_type_breakdown"] == {"low_cost": 1, "ordinary": 1}


# 验证可按异常类别筛选并渲染 Markdown 摘要。
def test_build_non_completed_smoke_payload_filters_category_and_renders_markdown() -> None:
    cases = [
        VirtualPatientCase(
            case_id="case1",
            title="case1",
            true_conditions=["疾病1"],
            metadata={"case_type": "ordinary"},
        ),
        VirtualPatientCase(
            case_id="case2",
            title="case2",
            true_conditions=["疾病2"],
            metadata={"case_type": "low_cost"},
        ),
    ]
    non_completed_payload = {
        "non_completed_count": 2,
        "cases": [
            {
                "case_id": "case1",
                "category": "max_turn_reached::top_exact_correct_but_rejected",
                "true_conditions": ["疾病1"],
                "final_answer_name": "疾病1",
                "stop_reason": "anchor_controlled_rejected",
            },
            {
                "case_id": "case2",
                "category": "max_turn_reached::true_candidate_missing",
                "true_conditions": ["疾病2"],
                "final_answer_name": "疾病X",
                "stop_reason": "verifier_rejected_stop",
            },
        ],
    }

    selected_cases, manifest = build_non_completed_smoke_payload(
        cases=cases,
        non_completed_payload=non_completed_payload,
        source_cases_file=Path("cases.jsonl"),
        non_completed_file=Path("non_completed_cases.json"),
        output_root=Path("non_completed_smoke"),
        include_categories=("max_turn_reached::true_candidate_missing",),
        limit=0,
    )
    manifest["selected_cases"] = [
        {
            "case_id": case.case_id,
            "metadata": case.metadata,
        }
        for case in selected_cases
    ]

    markdown = render_non_completed_smoke_markdown(manifest)

    assert [case.case_id for case in selected_cases] == ["case2"]
    assert "max_turn_reached::true_candidate_missing" in markdown
    assert "| case2 | low_cost |" in markdown
