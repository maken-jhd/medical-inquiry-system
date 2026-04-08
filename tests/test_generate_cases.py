"""测试虚拟病人种子病例生成逻辑。"""

from simulator.generate_cases import build_seed_cases


# 验证内置种子病例的数量和关键字段是否符合预期。
def test_build_seed_cases_returns_rich_case_set() -> None:
    cases = build_seed_cases()

    assert len(cases) >= 10
    assert len({case.case_id for case in cases}) == len(cases)
    assert any("肺孢子菌肺炎 (PCP)" in case.true_conditions for case in cases)
    assert any(case.behavior_style == "concealing" for case in cases)
    assert any(case.behavior_style == "vague" for case in cases)
