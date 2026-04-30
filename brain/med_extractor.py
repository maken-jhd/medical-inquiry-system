"""实现论文中 MedExtractor 的患者上下文抽取层。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List

from .errors import LlmEmptyExtractionError, LlmOutputInvalidError, LlmUnavailableError
from .llm_client import LlmClient
from .normalization import NameNormalizer
from .types import ClinicalFeatureItem, PatientContext, PatientGeneralInfo


@dataclass
class MedExtractorConfig:
    """保存患者上下文抽取阶段的轻量配置。"""

    sex_keywords: Dict[str, List[str]] = field(
        default_factory=lambda: {
            "男": ["男性", "男", "先生"],
            "女": ["女性", "女", "女士"],
        }
    )
    pregnancy_keywords: List[str] = field(default_factory=lambda: ["怀孕", "孕期", "妊娠", "孕妇"])
    past_history_keywords: List[str] = field(default_factory=lambda: ["既往", "既往史", "慢性肾病", "高血脂", "肥胖"])
    epidemiology_keywords: List[str] = field(
        default_factory=lambda: ["高危性行为", "输血史", "不安全性行为", "无保护性行为", "高危行为"]
    )
class MedExtractor:
    """将患者原话拆解成一般信息 P 与临床特征 C。"""

    # 初始化 MedExtractor，可选接入大模型主通道。
    def __init__(self, llm_client: LlmClient | None = None, config: MedExtractorConfig | None = None) -> None:
        self.llm_client = llm_client
        self.config = config or MedExtractorConfig()
        self.normalizer = NameNormalizer()

    # 提取结构化患者上下文。
    def extract_patient_context(self, patient_text: str) -> PatientContext:
        # 对明显的短答直接走规则：
        # 这类输入往往是在回答上一轮问题，不值得再为 extractor 支付一次 LLM 成本。
        if self._looks_like_direct_reply(patient_text):
            return self._build_direct_reply_context(patient_text)

        # 首轮主诉或信息量较大的自然描述优先尝试 LLM，
        # 这样可以同时抽出一般信息和更细的临床特征。
        if self.llm_client is None or not self.llm_client.is_available():
            raise LlmUnavailableError(stage="med_extractor", prompt_name="med_extractor")

        llm_payload = self.llm_client.run_structured_prompt(
            "med_extractor",
            {"patient_text": patient_text},
            dict,
        )
        return self._coerce_llm_payload(patient_text, llm_payload)

    # 对“有/没有/不太清楚”这类短答优先走规则，避免每轮问答都为 extractor 支付一次 LLM 成本。
    def _looks_like_direct_reply(self, patient_text: str) -> bool:
        normalized_text = patient_text.strip().rstrip("。！？!?；;，,")

        if len(normalized_text) == 0:
            return False

        if normalized_text in {"有", "有的", "是的", "会", "存在", "没有", "没有的", "不是", "不会", "无"}:
            return True

        if normalized_text in {"不确定", "不太清楚", "说不上来", "没有特别注意到"}:
            return True

        if len(normalized_text) > 20:
            return False

        direct_prefixes = (
            "有",
            "没有",
            "不是",
            "不会",
            "不确定",
            "不太清楚",
            "说不上来",
            "没注意",
        )
        return normalized_text.startswith(direct_prefixes)

    # 为短答构造最小上下文，不再顺手做自由文本症状抽取。
    def _build_direct_reply_context(self, patient_text: str) -> PatientContext:
        return PatientContext(
            general_info=PatientGeneralInfo(),
            clinical_features=[],
            raw_text=patient_text,
            metadata={"source": "direct_reply_rule"},
        )

    # 将大模型输出转成 PatientContext。
    def _coerce_llm_payload(self, patient_text: str, payload: dict) -> PatientContext:
        if not isinstance(payload, dict):
            raise LlmOutputInvalidError(
                stage="med_extractor",
                prompt_name="med_extractor",
                attempts=1,
                message="MedExtractor 收到的 LLM payload 不是 JSON object。",
            )

        general_info_payload = payload.get("general_info", {})
        if not isinstance(general_info_payload, dict):
            general_info_payload = {}

        # clinical_features 可能是标准对象数组，也可能退化成一个字符串；
        # 先统一整理成列表，再生成系统内部使用的结构化对象。
        clinical_feature_payload = self._coerce_clinical_feature_payload(payload.get("clinical_features", []), patient_text)
        general_info = PatientGeneralInfo(
            age=general_info_payload.get("age"),
            sex=general_info_payload.get("sex"),
            pregnancy_status=general_info_payload.get("pregnancy_status"),
            past_history=list(general_info_payload.get("past_history", [])),
            epidemiology=list(general_info_payload.get("epidemiology", [])),
        )
        clinical_features = [
            ClinicalFeatureItem(
                name=item.get("name", ""),
                normalized_name=item.get("normalized_name", item.get("name", "")),
                category=item.get("category", "symptom"),
                status=item.get("status", "unknown"),
                certainty=item.get("certainty", "unknown"),
                evidence_text=item.get("evidence_text", patient_text),
                metadata=dict(item.get("metadata", {})),
            )
            for item in clinical_feature_payload
            if len(str(item.get("name", ""))) > 0
        ]
        if len(clinical_features) == 0:
            raise LlmEmptyExtractionError(
                stage="med_extractor",
                prompt_name="med_extractor",
                attempts=1,
                message="MedExtractor 未从当前长文本中抽取出任何临床特征。",
            )
        return PatientContext(
            general_info=general_info,
            clinical_features=clinical_features,
            raw_text=patient_text,
            metadata={"source": "llm"},
        )

    def _coerce_clinical_feature_payload(self, payload: object, patient_text: str) -> list[dict]:
        if isinstance(payload, list):
            feature_items: list[dict] = []
            for item in payload:
                if not isinstance(item, dict):
                    continue
                normalized_name = self.normalizer.normalize_feature_name(
                    str(item.get("normalized_name", item.get("name", "")) or item.get("name", ""))
                )
                if len(normalized_name) == 0:
                    continue
                feature_items.append(
                    {
                        "name": str(item.get("name", normalized_name)),
                        "normalized_name": normalized_name,
                        "category": self.normalizer.normalize_feature_category(
                            normalized_name,
                            str(item.get("category", "symptom") or "symptom"),
                        ),
                        "status": str(item.get("status", "exist") or "exist"),
                        "certainty": str(item.get("certainty", "doubt") or "doubt"),
                        "evidence_text": str(item.get("evidence_text", patient_text) or patient_text),
                        "metadata": dict(item.get("metadata", {})),
                    }
                )
            return feature_items

        if isinstance(payload, str):
            normalized_payload = payload.strip()
            if len(normalized_payload) == 0:
                return []

            # 有些模型会把多个特征挤成一个字符串；
            # 这里按常见中文分隔符拆开，并尽量补齐 normalized_name / category 等字段。
            feature_items: list[dict] = []
            for raw_name in self.normalizer.split_feature_string(normalized_payload):
                cleaned_name = raw_name.strip()
                if len(cleaned_name) == 0:
                    continue

                normalized_name = self.normalizer.normalize_feature_name(cleaned_name)
                category = self.normalizer.normalize_feature_category(normalized_name)
                feature_items.append(
                    {
                        "name": cleaned_name,
                        "normalized_name": normalized_name,
                        "category": category,
                        "status": "exist",
                        "certainty": "doubt",
                        "evidence_text": patient_text,
                        "metadata": {"source": "llm_string_payload"},
                    }
                )
            return feature_items

        return []

    def _normalize_feature_name(self, raw_name: str) -> str:
        return self.normalizer.normalize_feature_name(raw_name)
