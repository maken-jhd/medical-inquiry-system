"""集中维护 LLM 输出到知识图谱查询之间的轻量归一化逻辑。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List


@dataclass
class NormalizationConfig:
    """保存名称归一化所需的别名与常见口语映射。"""

    feature_aliases: Dict[str, List[str]] = field(
        default_factory=lambda: {
            "发热": ["发热", "发烧", "体温高", "低热", "高热"],
            "干咳": ["干咳", "咳嗽"],
            "呼吸困难": ["呼吸困难", "气促", "喘不上气", "胸闷", "气短"],
            "腹泻": ["腹泻", "拉肚子", "稀便"],
            "皮疹": ["皮疹", "红疹", "起疹子"],
            "头痛": ["头痛", "持续头痛"],
            "咽痛": ["咽痛", "嗓子痛"],
            "体重下降": ["体重下降", "消瘦", "变瘦"],
            "畏光": ["畏光", "怕光"],
            "视力下降": ["视力下降", "视力模糊", "看东西模糊"],
            "嗜睡": ["嗜睡", "老是想睡", "总想睡觉"],
            "精神错乱": ["精神错乱", "意识混乱", "神志不清"],
            "认知异常": ["认知异常", "记性差", "记忆力下降", "痴呆"],
            "吞咽困难": ["吞咽困难", "吞东西困难"],
            "吞咽疼痛": ["吞咽疼痛", "吞咽痛", "吃东西疼"],
            "胸痛": ["胸痛", "胸口痛"],
            "咯血": ["咯血", "咳血"],
            "步态异常": ["步态异常", "走路不稳"],
            "言语异常": ["言语异常", "说话不清", "说话含糊"],
            "HIV感染": ["HIV感染", "HIV感染者", "HIV阳性", "艾滋病", "艾滋", "艾滋病患者"],
            "免疫功能低下": ["免疫功能低下", "免疫力低", "免疫力比较低", "免疫抑制"],
            "高危性行为": ["高危性行为", "无保护性行为", "不安全性行为", "高危行为"],
            "输血史": ["输血史", "输过血"],
            "口腔念珠菌感染": ["口腔念珠菌感染", "口咽念珠菌病", "口腔白斑", "白色东西"],
        }
    )
    exam_aliases: Dict[str, List[str]] = field(
        default_factory=lambda: {
            "CD4+ T淋巴细胞计数": ["CD4", "CD4计数", "T淋巴细胞计数", "CD4低"],
            "β-D-葡聚糖检测": ["β-D葡聚糖", "β-D-葡聚糖", "BDG", "G试验", "葡聚糖"],
            "HIV RNA": ["HIV RNA", "病毒载量", "HIV病毒载量"],
            "胸部CT": ["胸部CT", "CT", "胸片", "影像", "肺部CT"],
            "PCR": ["PCR", "核酸"],
            "T-SPOT.TB": ["T-SPOT", "TSPOT", "IGRA", "结核检测"],
            "Xpert MTB/RIF": ["Xpert", "MTB/RIF"],
        }
    )


class NameNormalizer:
    """只负责名称整理，不负责自由文本事实抽取。"""

    def __init__(self, config: NormalizationConfig | None = None) -> None:
        self.config = config or NormalizationConfig()
        self._feature_alias_to_canonical = self._build_alias_lookup(self.config.feature_aliases)
        self._exam_alias_to_canonical = self._build_alias_lookup(self.config.exam_aliases)

    def feature_aliases(self) -> Dict[str, List[str]]:
        return dict(self.config.feature_aliases)

    def normalize_feature_name(self, raw_name: str) -> str:
        cleaned_name = self._clean_name(raw_name)
        if len(cleaned_name) == 0:
            return ""
        normalized = self._feature_alias_to_canonical.get(self._normalize_key(cleaned_name))
        return normalized or cleaned_name

    def normalize_exam_name(self, raw_name: str) -> str:
        cleaned_name = self._clean_name(raw_name)
        if len(cleaned_name) == 0:
            return ""
        normalized = self._exam_alias_to_canonical.get(self._normalize_key(cleaned_name))
        return normalized or cleaned_name

    def normalize_graph_mention(self, raw_name: str) -> str:
        feature_name = self.normalize_feature_name(raw_name)
        if feature_name != self._clean_name(raw_name):
            return feature_name

        exam_name = self.normalize_exam_name(raw_name)
        if exam_name != self._clean_name(raw_name):
            return exam_name

        return self._clean_name(raw_name)

    def normalize_feature_category(self, normalized_name: str, fallback: str = "symptom") -> str:
        if normalized_name in {"高危性行为", "输血史", "HIV感染", "免疫功能低下"}:
            return "risk_factor"
        return fallback or "symptom"

    def normalize_exam_text(self, text: str) -> str:
        return (
            str(text)
            .strip()
            .lower()
            .replace(" ", "")
            .replace("（", "(")
            .replace("）", ")")
            .replace("，", ",")
            .replace("。", "")
            .replace("、", "")
            .replace("-", "")
            .replace("_", "")
            .replace("/", "")
        )

    def split_feature_string(self, payload: str) -> list[str]:
        return [item.strip() for item in re.split(r"[、,，；;]|以及|和", payload) if len(item.strip()) > 0]

    def candidate_feature_aliases(self, normalized_name: str) -> list[str]:
        aliases = self.config.feature_aliases.get(normalized_name, [])
        return [normalized_name, *aliases]

    def candidate_exam_aliases(self, normalized_name: str) -> list[str]:
        aliases = self.config.exam_aliases.get(normalized_name, [])
        return [normalized_name, *aliases]

    def _build_alias_lookup(self, mapping: Dict[str, List[str]]) -> dict[str, str]:
        values: dict[str, str] = {}
        for canonical_name, aliases in mapping.items():
            family = [canonical_name, *aliases]
            for item in family:
                normalized_key = self._normalize_key(item)
                if len(normalized_key) == 0:
                    continue
                values[normalized_key] = canonical_name
        return values

    def _normalize_key(self, value: str) -> str:
        return self.normalize_exam_text(value)

    def _clean_name(self, value: str) -> str:
        return str(value).strip()

