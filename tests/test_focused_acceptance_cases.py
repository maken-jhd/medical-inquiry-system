"""测试 focused acceptance 扩样本病例文件可被加载。"""

from pathlib import Path

from simulator.generate_cases import load_cases_jsonl


def test_focused_acceptance_cases_load() -> None:
    cases = load_cases_jsonl(Path("simulator/focused_acceptance_cases.jsonl"))
    case_ids = {case.case_id for case in cases}

    assert len(cases) == 10
    assert "pcp_typical_001" in case_ids
    assert "tb_vs_pcp_001" in case_ids
    assert "fungal_vs_pcp_001" in case_ids
    assert "systemic_non_pcp_001" in case_ids
