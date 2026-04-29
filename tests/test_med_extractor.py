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


def test_med_extractor_rule_fallback_covers_competitive_symptoms() -> None:
    extractor = MedExtractor(llm_client=None)

    context = extractor.extract_patient_context("最近主要是嗜睡、精神错乱，还有畏光和视力下降。")

    feature_names = {item.normalized_name for item in context.clinical_features}
    assert "嗜睡" in feature_names
    assert "精神错乱" in feature_names
    assert "畏光" in feature_names
    assert "视力下降" in feature_names


def test_med_extractor_coerces_llm_string_clinical_features() -> None:
    class FakeLlmClient:
        def is_available(self) -> bool:
            return True

        def run_structured_prompt(self, prompt_name: str, variables: dict, schema):
            _ = prompt_name, variables, schema
            return {
                "general_info": "",
                "clinical_features": "嗜睡、精神错乱、痴呆",
            }

    extractor = MedExtractor(llm_client=FakeLlmClient())
    context = extractor.extract_patient_context("最近主要是嗜睡、精神错乱，还有痴呆。")

    assert context.metadata["source"] == "llm"
    feature_names = {item.normalized_name for item in context.clinical_features}
    assert "嗜睡" in feature_names
    assert "精神错乱" in feature_names
    assert "认知异常" in feature_names


def test_med_extractor_skips_llm_for_direct_reply() -> None:
    class FakeLlmClient:
        def __init__(self) -> None:
            self.called = False

        def is_available(self) -> bool:
            return True

        def run_structured_prompt(self, prompt_name: str, variables: dict, schema):
            _ = prompt_name, variables, schema
            self.called = True
            return {"clinical_features": []}

    client = FakeLlmClient()
    extractor = MedExtractor(llm_client=client)

    context = extractor.extract_patient_context("没有步态异常。")

    assert client.called is False
    assert context.metadata["source"] == "rules"
