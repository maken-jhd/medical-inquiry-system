"""测试 MedExtractor 能否将患者原话拆成一般信息与临床特征。"""

from brain.med_extractor import MedExtractor


# 验证规则版 MedExtractor 能提取年龄、性别与核心症状。
def test_med_extractor_extracts_general_info_and_features() -> None:
    extractor = MedExtractor(llm_client=None)

    context = extractor.extract_patient_context("32岁男性，最近发热、干咳，而且有过无保护性行为。")

    assert context.general_info.age == 32
    assert context.general_info.sex == "男"
    feature_names = {item.normalized_name for item in context.clinical_features}
    assert "发热" in feature_names
    assert "干咳" in feature_names
    assert "高危性行为" in feature_names
