"""实现论文中 MedExtractor 的患者上下文抽取层。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List

from .llm_client import LlmClient
from .types import ClinicalFeatureItem, PatientContext, PatientGeneralInfo


@dataclass
class MedExtractorConfig:
    """保存患者上下文抽取阶段的规则词典。"""

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
    feature_aliases: Dict[str, List[str]] = field(
        default_factory=lambda: {
            "发热": ["发热", "发烧", "低热", "高热"],
            "干咳": ["干咳", "咳嗽"],
            "呼吸困难": ["呼吸困难", "气促", "胸闷", "喘不上气"],
            "腹泻": ["腹泻", "拉肚子", "稀便"],
            "皮疹": ["皮疹", "起疹子", "红疹"],
            "头痛": ["头痛", "持续头痛"],
            "咽痛": ["咽痛", "嗓子痛"],
            "体重下降": ["体重下降", "消瘦", "变瘦"],
            "高危性行为": ["高危性行为", "无保护性行为", "不安全性行为", "高危行为"],
            "输血史": ["输血史", "输过血"],
        }
    )


class MedExtractor:
    """将患者原话拆解成一般信息 P 与临床特征 C。"""

    # 初始化 MedExtractor，可选接入大模型主通道。
    def __init__(self, llm_client: LlmClient | None = None, config: MedExtractorConfig | None = None) -> None:
        self.llm_client = llm_client
        self.config = config or MedExtractorConfig()

    # 提取结构化患者上下文。
    def extract_patient_context(self, patient_text: str) -> PatientContext:
        if self.llm_client is not None and self.llm_client.is_available():
            try:
                llm_payload = self.llm_client.run_structured_prompt(
                    "med_extractor",
                    {"patient_text": patient_text},
                    dict,
                )
                context = self._coerce_llm_payload(patient_text, llm_payload)
                if len(context.clinical_features) > 0:
                    return context
            except Exception:
                pass

        return self._extract_with_rules(patient_text)

    # 将大模型输出转成 PatientContext。
    def _coerce_llm_payload(self, patient_text: str, payload: dict) -> PatientContext:
        general_info_payload = payload.get("general_info", {})
        clinical_feature_payload = payload.get("clinical_features", [])
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
        return PatientContext(
            general_info=general_info,
            clinical_features=clinical_features,
            raw_text=patient_text,
            metadata={"source": "llm"},
        )

    # 使用规则兜底提取患者上下文。
    def _extract_with_rules(self, patient_text: str) -> PatientContext:
        general_info = PatientGeneralInfo(
            age=self._extract_age(patient_text),
            sex=self._extract_sex(patient_text),
            pregnancy_status=self._extract_pregnancy_status(patient_text),
            past_history=self._extract_keyword_hits(patient_text, self.config.past_history_keywords),
            epidemiology=self._extract_keyword_hits(patient_text, self.config.epidemiology_keywords),
        )
        clinical_features = self._extract_clinical_features(patient_text)
        return PatientContext(
            general_info=general_info,
            clinical_features=clinical_features,
            raw_text=patient_text,
            metadata={"source": "rules"},
        )

    # 提取年龄信息。
    def _extract_age(self, patient_text: str) -> int | None:
        match = re.search(r"(\d{1,3})\s*岁", patient_text)

        if match is None:
            return None

        return int(match.group(1))

    # 提取性别信息。
    def _extract_sex(self, patient_text: str) -> str | None:
        for sex, keywords in self.config.sex_keywords.items():
            if any(keyword in patient_text for keyword in keywords):
                return sex

        return None

    # 提取妊娠状态。
    def _extract_pregnancy_status(self, patient_text: str) -> str | None:
        if any(keyword in patient_text for keyword in self.config.pregnancy_keywords):
            return "pregnant"

        return None

    # 提取规则词典中的一般信息命中结果。
    def _extract_keyword_hits(self, patient_text: str, keywords: List[str]) -> List[str]:
        hits: List[str] = []

        for keyword in keywords:
            if keyword in patient_text and keyword not in hits:
                hits.append(keyword)

        return hits

    # 提取临床特征并输出为结构化项目。
    def _extract_clinical_features(self, patient_text: str) -> List[ClinicalFeatureItem]:
        features: List[ClinicalFeatureItem] = []
        seen: set[str] = set()

        for normalized_name, aliases in self.config.feature_aliases.items():
            for alias in aliases:
                if alias not in patient_text:
                    continue

                if normalized_name in seen:
                    break

                category = "risk_factor" if normalized_name in {"高危性行为", "输血史"} else "symptom"
                features.append(
                    ClinicalFeatureItem(
                        name=alias,
                        normalized_name=normalized_name,
                        category=category,
                        status="exist",
                        certainty="confident",
                        evidence_text=patient_text,
                    )
                )
                seen.add(normalized_name)
                break

        return features
