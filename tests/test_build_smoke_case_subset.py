"""测试 balanced smoke 子集抽样脚本。"""

from simulator.case_schema import VirtualPatientCase
from scripts.build_smoke_case_subset import build_balanced_case_subset, build_sample_summary


def _case(case_id: str, case_type: str) -> VirtualPatientCase:
    return VirtualPatientCase(
        case_id=case_id,
        title=case_id,
        metadata={"case_type": case_type},
    )


def test_build_balanced_case_subset_samples_equal_cases_per_type() -> None:
    cases = []

    for case_type in ("ordinary", "low_cost", "exam_driven", "competitive"):
        for index in range(20):
            cases.append(_case(f"{case_type}_{index:02d}", case_type))

    subset = build_balanced_case_subset(
        cases,
        case_types=["ordinary", "low_cost", "exam_driven", "competitive"],
        per_case_type=15,
        seed=20260505,
    )

    assert len(subset) == 60
    counts = {}
    for case in subset:
        case_type = str(case.metadata.get("case_type") or "")
        counts[case_type] = counts.get(case_type, 0) + 1

    assert counts == {
        "ordinary": 15,
        "low_cost": 15,
        "exam_driven": 15,
        "competitive": 15,
    }


def test_build_sample_summary_records_seed_and_case_ids() -> None:
    sampled = [
        _case("ordinary_01", "ordinary"),
        _case("low_cost_01", "low_cost"),
    ]

    summary = build_sample_summary(
        sampled,
        cases_file="cases.jsonl",
        per_case_type=15,
        seed=20260505,
    )

    assert summary["case_count"] == 2
    assert summary["seed"] == 20260505
    assert summary["sampled_case_ids"] == ["ordinary_01", "low_cost_01"]
