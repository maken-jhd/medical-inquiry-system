"""测试 MedExtractor 在 LLM-first 模式下的上下文抽取行为。"""

from __future__ import annotations

import pytest

from brain.errors import LlmEmptyExtractionError, LlmUnavailableError
from brain.med_extractor import MedExtractor


def test_med_extractor_uses_llm_for_long_text() -> None:
    class FakeLlmClient:
        def is_available(self) -> bool:
            return True

        def run_structured_prompt(self, prompt_name: str, variables: dict, schema):
            _ = prompt_name, variables, schema
            return {
                "general_info": {"age": 32, "sex": "男"},
                "clinical_features": [
                    {"name": "发烧", "normalized_name": "发热", "category": "symptom", "status": "exist", "certainty": "confident"},
                    {"name": "咳嗽", "normalized_name": "咳嗽", "category": "symptom", "status": "exist", "certainty": "doubt"},
                    {"name": "艾滋病", "normalized_name": "艾滋病", "category": "risk_factor", "status": "exist", "certainty": "confident"},
                ],
            }

    extractor = MedExtractor(llm_client=FakeLlmClient())
    context = extractor.extract_patient_context("32岁男性，最近发热、干咳，而且有过无保护性行为。")

    assert context.metadata["source"] == "llm"
    assert context.general_info.age == 32
    assert context.general_info.sex == "男"
    feature_names = {item.normalized_name for item in context.clinical_features}
    assert "发热" in feature_names
    assert "干咳" in feature_names
    assert "HIV感染" in feature_names


def test_med_extractor_raises_when_llm_unavailable_for_long_text() -> None:
    extractor = MedExtractor(llm_client=None)

    with pytest.raises(LlmUnavailableError):
        extractor.extract_patient_context("最近主要是嗜睡、精神错乱，还有畏光和视力下降。")


def test_med_extractor_coerces_llm_string_clinical_features() -> None:
    class FakeLlmClient:
        def is_available(self) -> bool:
            return True

        def run_structured_prompt(self, prompt_name: str, variables: dict, schema):
            _ = prompt_name, variables, schema
            return {
                "general_info": {},
                "clinical_features": "嗜睡、精神错乱、痴呆",
            }

    extractor = MedExtractor(llm_client=FakeLlmClient())
    context = extractor.extract_patient_context("最近主要是嗜睡、精神错乱，还有痴呆。")

    assert context.metadata["source"] == "llm"
    feature_names = {item.normalized_name for item in context.clinical_features}
    assert "嗜睡" in feature_names
    assert "精神错乱" in feature_names
    assert "认知异常" in feature_names


def test_med_extractor_raises_when_llm_returns_empty_features() -> None:
    class FakeLlmClient:
        def is_available(self) -> bool:
            return True

        def run_structured_prompt(self, prompt_name: str, variables: dict, schema):
            _ = prompt_name, variables, schema
            return {"general_info": {}, "clinical_features": []}

    extractor = MedExtractor(llm_client=FakeLlmClient())

    with pytest.raises(LlmEmptyExtractionError):
        extractor.extract_patient_context("最近发热、咳嗽。")


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
    assert context.metadata["source"] == "direct_reply_rule"
    assert context.clinical_features == []
