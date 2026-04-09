"""负责 A1 核心症状提取、答案解释和 A4 结果转槽位更新。"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Dict, Iterable, List, Optional, Sequence

from .llm_client import LlmClient
from .types import (
    A1ExtractionResult,
    A4DeductiveResult,
    ClinicalFeatureItem,
    KeyFeature,
    MctsAction,
    PatientContext,
    SlotUpdate,
)


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
    use_llm_extractor: bool = True
    fallback_to_rules: bool = True


class EvidenceParser:
    """将患者自然语言回答转成核心线索和结构化更新。"""

    # 初始化证据解析器并加载基础规则词典。
    def __init__(self, llm_client: LlmClient | None = None, config: EvidenceParserConfig | None = None) -> None:
        self.llm_client = llm_client
        self.config = config or EvidenceParserConfig()

    # 对外提供 A1 阶段入口，优先走 LLM，失败后回退到规则版。
    def run_a1_key_symptom_extraction(
        self,
        patient_input: str | PatientContext,
        known_feature_names: Optional[Sequence[str]] = None,
    ) -> A1ExtractionResult:
        if self.llm_client is not None and self.llm_client.is_available() and self.config.use_llm_extractor:
            try:
                return self._run_a1_with_llm(patient_input, known_feature_names)
            except Exception:
                if not self.config.fallback_to_rules:
                    raise

        return self._run_a1_with_rules(patient_input, known_feature_names)

    # 将 A4 阶段的目标问题回答解释成目标感知的证据状态。
    def interpret_answer_for_target(
        self,
        patient_text: str,
        action: MctsAction,
    ) -> A4DeductiveResult:
        target_name = action.target_node_name
        existence, certainty = self._infer_existence_and_certainty(patient_text, target_name)

        if existence == "non_exist":
            reasoning = f"患者回答中出现了针对“{target_name}”的否定表达。"
        elif existence == "exist" and certainty == "doubt":
            reasoning = f"患者回答与“{target_name}”相关，但表述仍然模糊。"
        elif existence == "exist":
            reasoning = f"患者回答明确支持“{target_name}”存在。"
        else:
            reasoning = f"当前回答不足以对“{target_name}”形成明确判断。"

        return A4DeductiveResult(
            existence=existence,
            certainty=certainty,
            reasoning=reasoning,
            supporting_span=patient_text,
            metadata={
                "action_id": action.action_id,
                "target_node_id": action.target_node_id,
                "target_node_name": target_name,
            },
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

    # 兼容旧入口：当前直接复用目标感知解释逻辑。
    def run_a4_deductive_analysis(
        self,
        patient_text: str,
        action: MctsAction,
    ) -> A4DeductiveResult:
        return self.interpret_answer_for_target(patient_text, action)

    # 使用 LLM 执行 A1 结构化关键特征抽取。
    def _run_a1_with_llm(
        self,
        patient_input: str | PatientContext,
        known_feature_names: Optional[Sequence[str]] = None,
    ) -> A1ExtractionResult:
        if self.llm_client is None:
            raise RuntimeError("当前未配置可用的 LLM 客户端。")

        patient_context = self._ensure_patient_context(patient_input)
        payload = self.llm_client.run_structured_prompt(
            "a1_key_symptom_extraction",
            {
                "patient_context": patient_context,
                "known_feature_names": list(known_feature_names or []),
            },
            dict,
        )
        key_features: List[KeyFeature] = []

        for item in payload.get("key_features", []):
            key_features.append(
                KeyFeature(
                    name=item.get("name", ""),
                    normalized_name=item.get("normalized_name", item.get("name", "")),
                    status=item.get("status", "exist"),
                    certainty=item.get("certainty", "doubt"),
                    reasoning=item.get("reasoning", "由 LLM 提取。"),
                    metadata=dict(item.get("metadata", {})),
                )
            )

        return A1ExtractionResult(
            key_features=key_features,
            reasoning=payload.get("reasoning_summary", "已由 LLM 提取核心线索。"),
            metadata={"source": "llm"},
        )

    # 使用规则兜底执行 A1 抽取。
    def _run_a1_with_rules(
        self,
        patient_input: str | PatientContext,
        known_feature_names: Optional[Sequence[str]] = None,
    ) -> A1ExtractionResult:
        patient_context = self._ensure_patient_context(patient_input)
        patient_text = patient_context.raw_text
        key_features: List[KeyFeature] = []
        normalized_names: set[str] = set()
        candidate_names = set(known_feature_names or [])

        # 优先消费 MedExtractor 已经结构化的临床特征。
        for feature in patient_context.clinical_features:
            if feature.normalized_name in normalized_names:
                continue

            if feature.status == "unknown":
                continue

            key_features.append(
                KeyFeature(
                    name=feature.name,
                    normalized_name=feature.normalized_name,
                    status=feature.status,  # type: ignore[arg-type]
                    certainty=feature.certainty,  # type: ignore[arg-type]
                    reasoning=f"MedExtractor 已识别出“{feature.normalized_name}”。",
                    metadata={"category": feature.category},
                )
            )
            normalized_names.add(feature.normalized_name)

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

    # 将输入统一转换为 PatientContext，便于上游既可以传原文也可以传结构化上下文。
    def _ensure_patient_context(self, patient_input: str | PatientContext) -> PatientContext:
        if isinstance(patient_input, PatientContext):
            return patient_input

        clinical_features: List[ClinicalFeatureItem] = []

        for normalized_name, aliases in self.config.feature_aliases.items():
            for alias in aliases:
                if alias not in patient_input:
                    continue

                clinical_features.append(
                    ClinicalFeatureItem(
                        name=alias,
                        normalized_name=normalized_name,
                        category="risk_factor" if normalized_name in {"高危性行为", "输血史"} else "symptom",
                        status="exist",
                        certainty="confident",
                        evidence_text=patient_input,
                    )
                )
                break

        return PatientContext(
            clinical_features=clinical_features,
            raw_text=patient_input,
            metadata={"source": "raw_text"},
        )

    # 在命中的线索附近查找否定词或模糊词，辅助判断证据状态。
    def _contains_nearby_pattern(self, text: str, matched_text: str, patterns: Iterable[str]) -> bool:
        match = re.search(re.escape(matched_text), text)

        if match is None:
            return False

        start = max(0, match.start() - 8)
        end = min(len(text), match.end() + 8)
        window = text[start:end]

        return any(re.search(pattern, window) is not None for pattern in patterns)
