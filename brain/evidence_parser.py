"""负责 A1 核心症状提取与 A4 结果转槽位更新。"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Dict, Iterable, List, Optional, Sequence

from .types import A1ExtractionResult, A4DeductiveResult, KeyFeature, MctsAction, SlotUpdate


@dataclass
class EvidenceParserConfig:
    """保存 A1 规则抽取阶段使用的基础词典。"""

    feature_aliases: Dict[str, List[str]] = field(
        default_factory=lambda: {
            "发热": ["发热", "发烧", "体温高", "低热", "高热"],
            "干咳": ["干咳", "咳嗽"],
            "呼吸困难": ["呼吸困难", "气促", "喘不上气", "胸闷"],
            "腹泻": ["腹泻", "拉肚子", "稀便"],
            "皮疹": ["皮疹", "红疹", "起疹子"],
            "体重下降": ["体重下降", "消瘦", "变瘦"],
            "高危性行为": ["高危性行为", "无保护性行为", "不安全性行为"],
            "输血史": ["输血史", "输过血"],
        }
    )


class EvidenceParser:
    """将患者自然语言回答转成核心线索和结构化更新。"""

    # 初始化证据解析器并加载基础规则词典。
    def __init__(self, config: EvidenceParserConfig | None = None) -> None:
        self.config = config or EvidenceParserConfig()

    # 对外提供 A1 阶段入口，从患者原话中提取核心线索。
    def run_a1_key_symptom_extraction(
        self,
        patient_text: str,
        known_feature_names: Optional[Sequence[str]] = None,
    ) -> A1ExtractionResult:
        key_features: List[KeyFeature] = []
        normalized_names: set[str] = set()
        candidate_names = set(known_feature_names or [])

        for normalized_name, aliases in self.config.feature_aliases.items():
            for alias in aliases:
                if alias in patient_text:
                    if normalized_name in normalized_names:
                        break

                    status, certainty = self._infer_existence_and_certainty(patient_text, alias)
                    key_features.append(
                        KeyFeature(
                            name=alias,
                            normalized_name=normalized_name,
                            status=status,
                            certainty=certainty,
                            reasoning=f"患者表述中命中了“{alias}”，归一为“{normalized_name}”。",
                        )
                    )
                    normalized_names.add(normalized_name)
                    break

        for feature_name in candidate_names:
            if feature_name in normalized_names:
                continue

            if feature_name in patient_text:
                status, certainty = self._infer_existence_and_certainty(patient_text, feature_name)
                key_features.append(
                    KeyFeature(
                        name=feature_name,
                        normalized_name=feature_name,
                        status=status,
                        certainty=certainty,
                        reasoning=f"患者原话直接出现了候选特征“{feature_name}”。",
                    )
                )
                normalized_names.add(feature_name)

        reasoning = "已根据规则词典从患者原话中提取核心线索。"

        if len(key_features) == 0:
            reasoning = "未命中规则词典中的明显核心线索，建议进入更保守的澄清提问。"

        return A1ExtractionResult(
            key_features=key_features,
            reasoning=reasoning,
            metadata={"source_text": patient_text},
        )

    # 将 A4 演绎分析结果转换为可写入状态机的槽位更新。
    def build_slot_updates_from_a4(
        self,
        action: MctsAction,
        deductive_result: A4DeductiveResult,
        raw_evidence_text: str,
        turn_index: Optional[int] = None,
    ) -> List[SlotUpdate]:
        status = "unknown"
        certainty = "unknown"

        if deductive_result.existence == "exist":
            status = "true"
        elif deductive_result.existence == "non_exist":
            status = "false"

        if deductive_result.certainty == "confident":
            certainty = "certain"
        elif deductive_result.certainty == "doubt":
            certainty = "uncertain"

        return [
            SlotUpdate(
                node_id=action.target_node_id,
                status=status,
                certainty=certainty,
                evidence=raw_evidence_text,
                turn_index=turn_index,
                metadata={
                    "action_id": action.action_id,
                    "deductive_reasoning": deductive_result.reasoning,
                    "action_type": action.action_type,
                },
            )
        ]

    # 将 A1 阶段提取出的核心线索转换为可写入状态机的槽位更新。
    def build_slot_updates_from_a1(
        self,
        extraction_result: A1ExtractionResult,
        turn_index: Optional[int] = None,
    ) -> List[SlotUpdate]:
        updates: List[SlotUpdate] = []

        for feature in extraction_result.key_features:
            status = "unknown"
            certainty = "unknown"

            if feature.status == "exist":
                status = "true"
            elif feature.status == "non_exist":
                status = "false"

            if feature.certainty == "confident":
                certainty = "certain"
            elif feature.certainty == "doubt":
                certainty = "uncertain"

            updates.append(
                SlotUpdate(
                    node_id=feature.normalized_name,
                    status=status,
                    certainty=certainty,
                    evidence=feature.name,
                    turn_index=turn_index,
                    metadata={
                        "source_stage": "A1",
                        "reasoning": feature.reasoning,
                        "normalized_name": feature.normalized_name,
                    },
                )
            )

        return updates

    # 根据患者对验证问题的回答，生成 A4 阶段的存在性与确定性判断。
    def run_a4_deductive_analysis(
        self,
        patient_text: str,
        action: MctsAction,
    ) -> A4DeductiveResult:
        target_name = action.target_node_name
        existence, certainty = self._infer_existence_and_certainty(patient_text, target_name)

        if existence == "non_exist":
            reasoning = f"患者回答中出现了与“{target_name}”相关的否定表达。"
        elif existence == "exist" and certainty == "doubt":
            reasoning = f"患者回答中提到了“{target_name}”，但语气偏模糊，判定为存在但存疑。"
        elif existence == "exist":
            reasoning = f"患者回答中明确支持“{target_name}”存在。"
        else:
            reasoning = f"患者回答中没有形成足够明确的“{target_name}”判断，建议继续验证。"

        return A4DeductiveResult(
            existence=existence,
            certainty=certainty,
            reasoning=reasoning,
            metadata={
                "action_id": action.action_id,
                "target_node_id": action.target_node_id,
                "target_node_name": target_name,
            },
        )

    # 根据原话中的语气和否定词，粗略判断存在性和确定性。
    def _infer_existence_and_certainty(self, patient_text: str, matched_text: str) -> tuple[str, str]:
        negation_patterns = [r"没有", r"并未", r"否认", r"无"]
        doubt_patterns = [r"好像", r"可能", r"大概", r"有点", r"不太确定", r"说不上来"]
        positive_patterns = [r"有", r"是", r"会", r"存在", r"出现", r"明显"]
        generic_unknown_phrases = [
            "没有特别注意到",
            "不太清楚",
            "说不上来",
            "感觉不太明显",
            "不确定",
        ]
        generic_negative_phrases = [
            "没有",
            "没有的",
            "不是",
            "不会",
            "无",
        ]

        stripped_text = patient_text.strip()

        if stripped_text in {"有", "有的", "是的", "会", "存在"}:
            return "exist", "confident"

        if stripped_text in generic_negative_phrases:
            return "non_exist", "confident"

        if any(phrase in stripped_text for phrase in generic_unknown_phrases):
            return "unknown", "doubt"

        if any(re.search(pattern, stripped_text) is not None for pattern in negation_patterns):
            return "non_exist", "confident"

        if self._contains_nearby_pattern(patient_text, matched_text, negation_patterns):
            return "non_exist", "confident"

        if self._contains_nearby_pattern(patient_text, matched_text, doubt_patterns):
            return "exist", "doubt"

        if matched_text in patient_text or any(re.search(pattern, patient_text) is not None for pattern in positive_patterns):
            return "exist", "confident"

        if any(re.search(pattern, patient_text) is not None for pattern in doubt_patterns):
            return "unknown", "doubt"

        return "unknown", "unknown"

    # 在命中的线索附近查找否定词或模糊词，辅助判断证据状态。
    def _contains_nearby_pattern(self, text: str, matched_text: str, patterns: Iterable[str]) -> bool:
        match = re.search(re.escape(matched_text), text)

        if match is None:
            return False

        start = max(0, match.start() - 8)
        end = min(len(text), match.end() + 8)
        window = text[start:end]

        return any(re.search(pattern, window) is not None for pattern in patterns)
