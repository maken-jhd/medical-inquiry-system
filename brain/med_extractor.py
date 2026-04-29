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
            "畏光": ["畏光", "怕光"],
            "视力下降": ["视力下降", "视力模糊", "看东西模糊"],
            "嗜睡": ["嗜睡", "老是想睡", "总想睡觉"],
            "精神错乱": ["精神错乱", "意识混乱", "神志不清"],
            "认知异常": ["认知异常", "记性差", "记忆力下降", "痴呆"],
            "吞咽困难": ["吞咽困难", "吞东西困难"],
            "胸痛": ["胸痛", "胸口痛"],
            "咯血": ["咯血", "咳血"],
            "步态异常": ["步态异常", "走路不稳"],
            "言语异常": ["言语异常", "说话不清", "说话含糊"],
            "HIV感染": ["HIV感染", "HIV感染者", "艾滋病", "艾滋病患者"],
            "免疫功能低下": ["免疫功能低下", "免疫力低", "免疫力比较低"],
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
        # 对明显的短答直接走规则：
        # 这类输入往往是在回答上一轮问题，不值得再为 extractor 支付一次 LLM 成本。
        if self._looks_like_direct_reply(patient_text):
            return self._extract_with_rules(patient_text)

        # 首轮主诉或信息量较大的自然描述优先尝试 LLM，
        # 这样可以同时抽出一般信息和更细的临床特征。
        if self.llm_client is not None and self.llm_client.is_available():
            try:
                llm_payload = self.llm_client.run_structured_prompt(
                    "med_extractor",
                    {"patient_text": patient_text},
                    dict,
                )
                context = self._coerce_llm_payload(patient_text, llm_payload)

                # 只有当 LLM 至少抽出了一些临床特征时才采用它的结果；
                # 否则回退到规则版，避免空 payload 直接吞掉已有显式线索。
                if len(context.clinical_features) > 0:
                    return context
            except Exception:
                pass

        # 任意一步失败都回到规则兜底，保证问诊主链路不会因模型不可用而中断。
        return self._extract_with_rules(patient_text)

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

    # 将大模型输出转成 PatientContext。
    def _coerce_llm_payload(self, patient_text: str, payload: dict) -> PatientContext:
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
        return PatientContext(
            general_info=general_info,
            clinical_features=clinical_features,
            raw_text=patient_text,
            metadata={"source": "llm"},
        )

    def _coerce_clinical_feature_payload(self, payload: object, patient_text: str) -> list[dict]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]

        if isinstance(payload, str):
            normalized_payload = payload.strip()
            if len(normalized_payload) == 0:
                return []

            # 有些模型会把多个特征挤成一个字符串；
            # 这里按常见中文分隔符拆开，并尽量补齐 normalized_name / category 等字段。
            feature_items: list[dict] = []
            for raw_name in re.split(r"[、,，；;]|以及|和", normalized_payload):
                cleaned_name = raw_name.strip()
                if len(cleaned_name) == 0:
                    continue

                normalized_name = self._normalize_feature_name(cleaned_name)
                category = "risk_factor" if normalized_name in {"高危性行为", "输血史", "HIV感染", "免疫功能低下"} else "symptom"
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

    # 使用规则兜底提取患者上下文。
    def _extract_with_rules(self, patient_text: str) -> PatientContext:
        # 一般信息与临床特征拆开提取：
        # 前者面向年龄/性别/流调，后者面向症状与风险因素。
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

        # 每个标准特征只保留一次命中，避免“发热/发烧”这类别名重复写入多个槽位。
        for normalized_name, aliases in self.config.feature_aliases.items():
            for alias in aliases:
                if alias not in patient_text:
                    continue

                if normalized_name in seen:
                    break

                category = "risk_factor" if normalized_name in {"高危性行为", "输血史", "HIV感染", "免疫功能低下"} else "symptom"
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

    def _normalize_feature_name(self, raw_name: str) -> str:
        for normalized_name, aliases in self.config.feature_aliases.items():
            if raw_name == normalized_name or raw_name in aliases:
                return normalized_name

        return raw_name
