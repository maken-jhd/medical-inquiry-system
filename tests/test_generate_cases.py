"""测试虚拟病人种子病例生成逻辑。"""

import json
from pathlib import Path

from simulator.generate_cases import build_seed_cases, write_cases_json


# 验证内置种子病例的数量和关键字段是否符合预期。
def test_build_seed_cases_returns_rich_case_set() -> None:
    cases = build_seed_cases()

    assert len(cases) >= 10
    assert len({case.case_id for case in cases}) == len(cases)
    assert any("肺孢子菌肺炎 (PCP)" in case.true_conditions for case in cases)
    assert any(case.behavior_style == "concealing" for case in cases)
    assert any(case.behavior_style == "vague" for case in cases)


# 验证病例可导出为 JSON 数组，便于人工查看。
def test_write_cases_json_writes_array_payload(tmp_path: Path) -> None:
    cases = build_seed_cases()[:2]
    output_file = tmp_path / "cases.json"

    write_cases_json(cases, output_file)

    payload = json.loads(output_file.read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    assert len(payload) == 2
    assert payload[0]["case_id"] == cases[0].case_id
    assert "slot_truth_map" in payload[0]
