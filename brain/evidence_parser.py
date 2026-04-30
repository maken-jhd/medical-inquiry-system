"""负责 A1 核心症状提取、答案解释和 A4 结果转槽位更新。"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable, List, Optional, Sequence

from .errors import LlmEmptyExtractionError, LlmOutputInvalidError, LlmUnavailableError
from .llm_client import LlmClient
from .normalization import NameNormalizer
from .types import (
    A1ExtractionResult,
    A4DeductiveResult,
    ClinicalFeatureItem,
    DeductiveDecision,
    ExamContextResult,
    ExamMentionedResult,
    HypothesisScore,
    KeyFeature,
    MctsAction,
    PatientContext,
    SlotUpdate,
)


@dataclass
class EvidenceParserConfig:
    """保存 A1 / A4 在 LLM-first 模式下的轻量配置。"""

    use_llm_extractor: bool = True
    use_llm_deductive_judge: bool = True


class EvidenceParser:
    """将患者自然语言回答转成核心线索和结构化更新。"""

    # 初始化证据解析器并加载基础规则词典。
    def __init__(self, llm_client: LlmClient | None = None, config: EvidenceParserConfig | None = None) -> None:
        self.llm_client = llm_client
        self.config = config or EvidenceParserConfig()
        self.normalizer = NameNormalizer()

    # 对外提供 A1 阶段入口；长文本只接受 LLM 结构化结果。
    def run_a1_key_symptom_extraction(
        self,
        patient_input: str | PatientContext,
        known_feature_names: Optional[Sequence[str]] = None,
    ) -> A1ExtractionResult:
        if self.llm_client is None or not self.llm_client.is_available() or not self.config.use_llm_extractor:
            raise LlmUnavailableError(stage="a1_key_symptom_extraction", prompt_name="a1_key_symptom_extraction")

        return self._run_a1_with_llm(patient_input, known_feature_names)

    # 将 A4 阶段的目标问题回答解释成目标感知的证据状态。
    def interpret_answer_for_target(
        self,
        patient_text: str,
        action: MctsAction,
    ) -> A4DeductiveResult:
        direct_reply = self._classify_direct_reply(patient_text)
        if direct_reply is not None:
            return self._build_direct_reply_interpretation(patient_text, action, direct_reply)

        if self.llm_client is None or not self.llm_client.is_available():
            raise LlmUnavailableError(
                stage="a4_target_answer_interpretation",
                prompt_name="a4_target_answer_interpretation",
            )

        payload = self.llm_client.run_structured_prompt(
            "a4_target_answer_interpretation",
            {
                "question_text": str(action.metadata.get("question_text") or ""),
                "target_node_name": action.target_node_name,
                "target_aliases": self.normalizer.candidate_feature_aliases(action.target_node_name),
                "question_type": str(action.metadata.get("question_type_hint") or action.action_type or ""),
                "patient_answer": patient_text,
            },
            dict,
        )
        return self._coerce_target_answer_payload(payload, action, patient_text)

    # collect_exam_context 动作专用解析：一次回答里同时识别是否做过、检查名称和结果。
    def interpret_exam_context_answer(
        self,
        patient_text: str,
        action: MctsAction,
    ) -> ExamContextResult:
        exam_kind = str(action.metadata.get("exam_kind") or "lab")

        if exam_kind not in {"general", "lab", "imaging", "pathogen"}:
            exam_kind = "general"

        direct_reply = self._classify_direct_reply(patient_text)
        if direct_reply in {"positive", "negative", "uncertain"}:
            return self._build_direct_reply_exam_context_result(
                patient_text=patient_text,
                action=action,
                exam_kind=exam_kind,
                direct_reply=direct_reply,
            )

        if self.llm_client is None or not self.llm_client.is_available():
            raise LlmUnavailableError(
                stage="exam_context_interpretation",
                prompt_name="exam_context_interpretation",
            )

        payload = self.llm_client.run_structured_prompt(
            "exam_context_interpretation",
            {
                "exam_kind": exam_kind,
                "question_text": str(action.metadata.get("question_text") or ""),
                "patient_answer": patient_text,
                "candidate_evidence_names": [
                    str(item.get("name", ""))
                    for item in action.metadata.get("exam_candidate_evidence", [])
                    if isinstance(item, dict)
                ][:8],
            },
            dict,
        )
        return self._coerce_exam_context_payload(payload, action, patient_text, exam_kind)

    # 对 verify_evidence 的短答使用确定性解释，避免每轮都为“有/没有/不清楚”支付一次 LLM 成本。
    def _build_direct_reply_interpretation(
        self,
        patient_text: str,
        action: MctsAction,
        direct_reply: str,
    ) -> A4DeductiveResult:
        if direct_reply == "positive":
            existence, certainty = "exist", "confident"
            reasoning = f"患者对“{action.target_node_name}”给出了直接肯定回答。"
        elif direct_reply == "negative":
            existence, certainty = "non_exist", "confident"
            reasoning = f"患者对“{action.target_node_name}”给出了直接否定回答。"
        else:
            existence, certainty = "unknown", "doubt"
            reasoning = f"患者对“{action.target_node_name}”给出了不确定回答。"

        return A4DeductiveResult(
            existence=existence,  # type: ignore[arg-type]
            certainty=certainty,  # type: ignore[arg-type]
            reasoning=reasoning,
            supporting_span=patient_text if direct_reply == "positive" else "",
            negation_span=patient_text if direct_reply == "negative" else "",
            uncertain_span=patient_text if direct_reply == "uncertain" else "",
            metadata={
                "action_id": action.action_id,
                "target_node_id": action.target_node_id,
                "target_node_name": action.target_node_name,
                "focused_spans": [patient_text.strip()] if len(patient_text.strip()) > 0 else [],
                "direct_reply": direct_reply,
                "has_contradiction": False,
                "interpretation_source": "direct_reply_rule",
            },
        )

    def _coerce_target_answer_payload(
        self,
        payload: dict,
        action: MctsAction,
        patient_text: str,
    ) -> A4DeductiveResult:
        if not isinstance(payload, dict):
            raise LlmOutputInvalidError(
                stage="a4_target_answer_interpretation",
                prompt_name="a4_target_answer_interpretation",
                attempts=1,
                message="A4 target answer interpretation 收到的 payload 不是 JSON object。",
            )

        existence = str(payload.get("existence", "")).strip()
        certainty = str(payload.get("certainty", "")).strip()
        if existence not in {"exist", "non_exist", "unknown"}:
            raise LlmOutputInvalidError(
                stage="a4_target_answer_interpretation",
                prompt_name="a4_target_answer_interpretation",
                attempts=1,
                message=f"A4 target answer interpretation 返回了非法 existence：{existence or '空'}",
            )
        if certainty not in {"confident", "doubt", "unknown"}:
            raise LlmOutputInvalidError(
                stage="a4_target_answer_interpretation",
                prompt_name="a4_target_answer_interpretation",
                attempts=1,
                message=f"A4 target answer interpretation 返回了非法 certainty：{certainty or '空'}",
            )

        supporting_span = str(payload.get("supporting_span", "") or "")
        negation_span = str(payload.get("negation_span", "") or "")
        uncertain_span = str(payload.get("uncertain_span", "") or "")
        contradiction_detected = bool(
            len(negation_span) > 0 and len(supporting_span) > 0 and negation_span != supporting_span
        )

        return A4DeductiveResult(
            existence=existence,  # type: ignore[arg-type]
            certainty=certainty,  # type: ignore[arg-type]
            reasoning=str(payload.get("reasoning", "") or "已由 LLM 完成目标回答解释。"),
            supporting_span=supporting_span,
            negation_span=negation_span,
            uncertain_span=uncertain_span,
            metadata={
                "action_id": action.action_id,
                "target_node_id": action.target_node_id,
                "target_node_name": action.target_node_name,
                "focused_spans": [patient_text.strip()] if len(patient_text.strip()) > 0 else [],
                "direct_reply": None,
                "has_contradiction": contradiction_detected,
                "interpretation_source": "llm",
            },
        )

    def _build_direct_reply_exam_context_result(
        self,
        *,
        patient_text: str,
        action: MctsAction,
        exam_kind: str,
        direct_reply: str,
    ) -> ExamContextResult:
        if direct_reply == "positive":
            availability = "done"
            needs_followup = True
            followup_reason = "done_without_test_name_or_result"
            reasoning = "患者直接表示做过相关检查，但尚未提供检查名称或结果。"
        elif direct_reply == "negative":
            availability = "not_done"
            needs_followup = False
            followup_reason = ""
            reasoning = "患者直接表示近期没有做过该类检查。"
        else:
            availability = "unknown"
            needs_followup = True
            followup_reason = "availability_unclear"
            reasoning = "患者对是否做过该类检查仍不确定，需要先澄清 availability。"

        return ExamContextResult(
            exam_kind=exam_kind,  # type: ignore[arg-type]
            availability=availability,  # type: ignore[arg-type]
            mentioned_tests=[],
            mentioned_results=[],
            needs_followup=needs_followup,
            followup_reason=followup_reason,
            reasoning=reasoning,
            metadata={
                "action_id": action.action_id,
                "raw_text": patient_text,
                "candidate_evidence_count": len(action.metadata.get("exam_candidate_evidence", [])),
                "mentioned_exam_kinds": [],
                "interpretation_source": "direct_reply_rule",
            },
        )

    def _coerce_exam_context_payload(
        self,
        payload: dict,
        action: MctsAction,
        patient_text: str,
        exam_kind: str,
    ) -> ExamContextResult:
        if not isinstance(payload, dict):
            raise LlmOutputInvalidError(
                stage="exam_context_interpretation",
                prompt_name="exam_context_interpretation",
                attempts=1,
                message="Exam context interpretation 收到的 payload 不是 JSON object。",
            )

        availability = str(payload.get("availability", "")).strip()
        if availability not in {"unknown", "done", "not_done"}:
            raise LlmOutputInvalidError(
                stage="exam_context_interpretation",
                prompt_name="exam_context_interpretation",
                attempts=1,
                message=f"Exam context interpretation 返回了非法 availability：{availability or '空'}",
            )

        mentioned_tests = [
            self.normalizer.normalize_exam_name(str(item))
            for item in payload.get("mentioned_tests", [])
            if len(str(item).strip()) > 0
        ]
        mentioned_results: list[ExamMentionedResult] = []
        for item in payload.get("mentioned_results", []):
            if not isinstance(item, dict):
                continue
            normalized_result = str(item.get("normalized_result", "unknown") or "unknown").strip()
            if normalized_result not in {"positive", "negative", "high", "low", "unknown"}:
                normalized_result = "unknown"
            test_name = self.normalizer.normalize_exam_name(str(item.get("test_name", "")))
            mentioned_results.append(
                ExamMentionedResult(
                    test_name=test_name,
                    raw_text=str(item.get("raw_text", "") or ""),
                    normalized_result=normalized_result,
                    metadata=dict(item.get("metadata", {})),
                )
            )

        needs_followup = bool(payload.get("needs_followup", False))
        followup_reason = str(payload.get("followup_reason", "") or "")
        reasoning = str(payload.get("reasoning", "") or "已由 LLM 解析检查上下文回答。")
        mentioned_exam_kinds = self._infer_exam_kinds_from_mentions(mentioned_tests, mentioned_results)

        return ExamContextResult(
            exam_kind=exam_kind,  # type: ignore[arg-type]
            availability=availability,  # type: ignore[arg-type]
            mentioned_tests=mentioned_tests,
            mentioned_results=mentioned_results,
            needs_followup=needs_followup,
            followup_reason=followup_reason,
            reasoning=reasoning,
            metadata={
                "action_id": action.action_id,
                "raw_text": patient_text,
                "candidate_evidence_count": len(action.metadata.get("exam_candidate_evidence", [])),
                "mentioned_exam_kinds": sorted(mentioned_exam_kinds),
                "interpretation_source": "llm",
            },
        )

    # 将检查上下文中提到的结果尽量映射回当前 R2 候选证据节点。
    def build_slot_updates_from_exam_context(
        self,
        action: MctsAction,
        exam_result: ExamContextResult,
        raw_evidence_text: str,
        turn_index: Optional[int] = None,
    ) -> List[SlotUpdate]:
        updates: List[SlotUpdate] = []

        # 只有“做过检查”且给出了可解析结果时，才尝试把回答映射回具体证据节点。
        if exam_result.availability != "done" or len(exam_result.mentioned_results) == 0:
            return updates

        candidates = action.metadata.get("exam_candidate_evidence", [])

        if not isinstance(candidates, list):
            return updates

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue

            # match 会把自由文本结果压成 true/false + certain/uncertain，
            # 让后续状态机能像普通 A4 一样消费它。
            match = self._match_exam_result_to_candidate(exam_result, candidate)

            if match is None:
                continue

            status, certainty, evidence_text = match
            node_id = str(candidate.get("node_id") or "").strip()
            name = str(candidate.get("name") or node_id).strip()

            if len(node_id) == 0:
                continue

            updates.append(
                SlotUpdate(
                    node_id=node_id,
                    status=status,
                    certainty=certainty,
                    value=evidence_text,
                    evidence=raw_evidence_text,
                    turn_index=turn_index,
                    metadata={
                        "source_stage": "A4_EXAM_CONTEXT",
                        "action_id": action.action_id,
                        "action_type": action.action_type,
                        "normalized_name": name,
                        "exam_kind": exam_result.exam_kind,
                        "exam_availability": exam_result.availability,
                        "matched_from_exam_context": True,
                    },
                )
            )

            if len(updates) >= 3:
                # 一次 exam_context 回答最多回填几个高置信候选，避免“一句话误命中太多节点”。
                break

        return updates

    # 基于回答解释结果与主备选假设，输出更贴近论文 A4 的演绎决策。
    def judge_deductive_result(
        self,
        patient_context: PatientContext,
        action: MctsAction,
        answer_interpretation: A4DeductiveResult,
        current_hypothesis: HypothesisScore | None,
        alternatives: list[HypothesisScore],
    ) -> DeductiveDecision:
        if self._should_skip_llm_deductive_judge(patient_context, action, answer_interpretation):
            return self._build_rule_based_deductive_decision(
                action,
                answer_interpretation,
                current_hypothesis,
                alternatives,
            )

        if self.llm_client is None or not self.llm_client.is_available() or not self.config.use_llm_deductive_judge:
            raise LlmUnavailableError(stage="a4_deductive_judge", prompt_name="a4_deductive_judge")

        payload = self.llm_client.run_structured_prompt(
            "a4_deductive_judge",
            {
                "patient_context": patient_context,
                "action": action,
                "answer_interpretation": answer_interpretation,
                "current_hypothesis": current_hypothesis,
                "alternatives": alternatives[:3],
            },
            dict,
        )
        return self._coerce_judge_payload(payload, action, answer_interpretation)

    # 明确短答的 targeted verification 通常不需要再走一次 LLM judge，规则决策即可支撑后续路由。
    def _should_skip_llm_deductive_judge(
        self,
        patient_context: PatientContext,
        action: MctsAction,
        answer_interpretation: A4DeductiveResult,
    ) -> bool:
        if action.action_type != "verify_evidence":
            return False

        if bool(answer_interpretation.metadata.get("has_contradiction", False)):
            return False

        direct_reply = str(answer_interpretation.metadata.get("direct_reply") or "").strip()
        if direct_reply in {"positive", "negative", "uncertain"}:
            return True

        return False

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
            raise LlmUnavailableError(stage="a1_key_symptom_extraction", prompt_name="a1_key_symptom_extraction")

        patient_context = self._ensure_patient_context(patient_input)
        payload = self.llm_client.run_structured_prompt(
            "a1_key_symptom_extraction",
            {
                "patient_context": patient_context,
                "known_feature_names": list(known_feature_names or []),
            },
            dict,
        )
        if not isinstance(payload, dict):
            raise LlmOutputInvalidError(
                stage="a1_key_symptom_extraction",
                prompt_name="a1_key_symptom_extraction",
                attempts=1,
                message="A1 key symptom extraction 收到的 payload 不是 JSON object。",
            )
        key_features: List[KeyFeature] = []
        normalized_names: set[str] = set()
        known_names = set(known_feature_names or [])

        for item in payload.get("key_features", []):
            if not isinstance(item, dict):
                continue
            normalized_name = self.normalizer.normalize_feature_name(
                str(item.get("normalized_name", item.get("name", "")) or item.get("name", ""))
            )
            if len(normalized_name) == 0 or normalized_name in normalized_names:
                continue
            key_features.append(
                KeyFeature(
                    name=str(item.get("name", normalized_name) or normalized_name),
                    normalized_name=normalized_name,
                    status=str(item.get("status", "exist") or "exist"),
                    certainty=str(item.get("certainty", "doubt") or "doubt"),
                    reasoning=str(item.get("reasoning", "由 LLM 提取。") or "由 LLM 提取。"),
                    metadata=dict(item.get("metadata", {})),
                )
            )
            normalized_names.add(normalized_name)

        if len(key_features) == 0:
            for feature_name in known_names:
                normalized_name = self.normalizer.normalize_feature_name(feature_name)
                if normalized_name in normalized_names:
                    continue
                if normalized_name and normalized_name in patient_context.raw_text:
                    key_features.append(
                        KeyFeature(
                            name=normalized_name,
                            normalized_name=normalized_name,
                            status="exist",
                            certainty="doubt",
                            reasoning=f"患者原话直接复述了已知特征“{normalized_name}”。",
                        )
                    )
                    normalized_names.add(normalized_name)

        if len(key_features) == 0:
            raise LlmEmptyExtractionError(
                stage="a1_key_symptom_extraction",
                prompt_name="a1_key_symptom_extraction",
                attempts=1,
                message="A1 未从当前患者上下文中抽取出任何关键特征。",
            )

        return A1ExtractionResult(
            key_features=key_features,
            reasoning=str(payload.get("reasoning_summary", "已由 LLM 提取核心线索。") or "已由 LLM 提取核心线索。"),
            metadata={"source": "llm"},
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

        # 先处理几类非常短、语义高度稳定的回答，减少后续模式判断的歧义。
        if stripped_text in {"有", "有的", "是的", "会", "存在"}:
            return "exist", "confident"

        if stripped_text in generic_negative_phrases:
            return "non_exist", "confident"

        if any(phrase in stripped_text for phrase in generic_unknown_phrases):
            return "unknown", "doubt"

        # 接着判断整句级否定，再判断命中词附近的局部否定/模糊语气，
        # 尽量把“没有发热，但有点咳嗽”这类句子识别得更稳。
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

    # 识别患者是否做过某类检查。
    def _infer_exam_availability(self, patient_text: str, exam_kind: str) -> str:
        normalized = self._normalize_exam_text(patient_text)
        not_done_patterns = (
            "没做",
            "没有做",
            "没查",
            "没有查",
            "没拍",
            "没有拍",
            "没验",
            "没有验",
            "还没做",
            "没做过",
            "没有做过",
        )
        done_patterns = (
            "做过",
            "查过",
            "拍过",
            "验过",
            "检查过",
            "报告",
            "结果",
            "阳性",
            "阴性",
            "升高",
            "降低",
            "偏高",
            "偏低",
            "异常",
            "正常",
        )

        # 明确没做过的表达优先级最高，避免“没做，但医生提到 CT”被误判为 done。
        if any(pattern in normalized for pattern in not_done_patterns):
            return "not_done"

        # 其次识别“做过 / 报告 / 阳性 / 异常”等 done 线索。
        if any(pattern in normalized for pattern in done_patterns):
            return "done"

        # 如果没明确说 done/not_done，但已经提到具体检查名，也大概率代表做过。
        if len(self._extract_mentioned_tests(patient_text, exam_kind)) > 0:
            return "done"

        return "unknown"

    # 从患者回答中提取检查名称，第一版只做高价值关键词。
    def _extract_mentioned_tests(self, patient_text: str, exam_kind: str) -> List[str]:
        normalized = self._normalize_exam_text(patient_text)
        test_patterns = {
            "lab": [
                ("CD4", ("cd4", "t淋巴")),
                ("HIV RNA", ("hivrna", "病毒载量")),
                ("β-D 葡聚糖", ("βd葡聚糖", "bdg", "g试验", "葡聚糖")),
                ("血氧 / 动脉血气", ("pao2", "spo2", "氧分压", "动脉血气", "血氧")),
                ("LDH", ("ldh", "乳酸脱氢酶")),
            ],
            "imaging": [
                ("胸部CT", ("胸部ct", "ct", "胸部影像")),
                ("胸片", ("胸片", "x线", "x光")),
            ],
            "pathogen": [
                ("PCR", ("pcr", "核酸")),
                ("痰检", ("痰检", "痰培养", "痰涂片")),
                ("支气管肺泡灌洗", ("支气管肺泡", "肺泡灌洗", "bal", "balf")),
                ("抗酸染色", ("抗酸", "抗酸染色")),
                ("T-SPOT / Xpert", ("tspot", "t-spot", "xpert")),
            ],
        }
        values: List[str] = []
        pattern_groups = (
            test_patterns.get(exam_kind, [])
            if exam_kind != "general"
            else test_patterns["lab"] + test_patterns["imaging"] + test_patterns["pathogen"]
        )

        for name, keywords in pattern_groups:
            if any(keyword in normalized for keyword in keywords) and name not in values:
                values.append(name)

        return values

    # 抽取检查结果分句，并给出粗粒度 positive/negative/abnormal/low/high 判断。
    def _extract_mentioned_exam_results(
        self,
        patient_text: str,
        mentioned_tests: Sequence[str],
        exam_kind: str,
    ) -> List[ExamMentionedResult]:
        # 先按自然分句切开，再逐句判断是否包含结果关键词或隐式数值结果。
        clauses = [
            clause.strip()
            for clause in re.split(r"[，。！？；;,.!\n]", patient_text)
            if len(clause.strip()) > 0
        ]
        results: List[ExamMentionedResult] = []
        result_keywords = (
            "阳性",
            "阴性",
            "升高",
            "降低",
            "偏高",
            "偏低",
            "很低",
            "异常",
            "正常",
            "磨玻璃",
            "低氧",
            "小于",
            "大于",
            "不到",
            "<",
            ">",
            "检出",
            "未检出",
            "没有异常",
        )

        for clause in clauses:
            normalized_clause = self._normalize_exam_text(clause)

            has_result_keyword = any(keyword in normalized_clause for keyword in result_keywords)
            has_numeric_result = self._contains_exam_numeric_result(clause, mentioned_tests, exam_kind)

            if not has_result_keyword and not has_numeric_result:
                continue

            # 每条结果分句都尽量绑定一个 test_name，并归一成 positive/negative/high/low。
            test_name = self._choose_result_test_name(clause, mentioned_tests, exam_kind)
            normalized_result = self._classify_exam_result_text(f"{test_name} {clause}")
            result = ExamMentionedResult(
                test_name=test_name,
                raw_text=clause,
                normalized_result=normalized_result,
                metadata={"exam_kind": exam_kind},
            )
            results.append(result)

        return results

    # 识别“CD4 150”“PaO2 68”“β-D 葡聚糖 200”等没有显式高低词的数值型结果。
    def _contains_exam_numeric_result(
        self,
        clause: str,
        mentioned_tests: Sequence[str],
        exam_kind: str,
    ) -> bool:
        normalized = self._normalize_exam_text(clause)

        if len(self._extract_numbers(normalized)) == 0:
            return False

        if len(mentioned_tests) > 0:
            for test_name in mentioned_tests:
                if self._normalize_exam_text(test_name) in normalized:
                    return True

            if len(mentioned_tests) == 1 or any(marker in normalized for marker in ("结果", "数值", "报告")):
                return True

        numeric_test_keywords = {
            "lab": (
                "cd4",
                "t淋巴",
                "pao2",
                "spo2",
                "氧分压",
                "血氧",
                "βd葡聚糖",
                "bdg",
                "葡聚糖",
                "g试验",
                "hivrna",
                "病毒载量",
            ),
            "imaging": ("ct", "胸片", "影像"),
            "pathogen": ("pcr", "核酸", "痰", "灌洗", "bal", "balf"),
        }
        if exam_kind == "general":
            keywords = (
                numeric_test_keywords["lab"]
                + numeric_test_keywords["imaging"]
                + numeric_test_keywords["pathogen"]
            )
            return any(keyword in normalized for keyword in keywords)

        return any(keyword in normalized for keyword in numeric_test_keywords.get(exam_kind, ()))

    # 给结果分句选择最可能对应的检查名。
    def _choose_result_test_name(self, clause: str, mentioned_tests: Sequence[str], exam_kind: str) -> str:
        normalized = self._normalize_exam_text(clause)

        for test_name in mentioned_tests:
            if self._normalize_exam_text(test_name) in normalized:
                return test_name

        if exam_kind in {"imaging", "general"} and any(
            keyword in normalized for keyword in ("ct", "胸片", "影像", "磨玻璃")
        ):
            return "胸部影像"

        if exam_kind in {"pathogen", "general"} and any(
            keyword in normalized for keyword in ("pcr", "核酸", "痰", "灌洗", "bal", "balf", "阳性")
        ):
            return "病原学检查"

        if exam_kind in {"lab", "general"} and any(
            keyword in normalized for keyword in ("cd4", "葡聚糖", "血氧", "病毒载量", "pao2", "spo2")
        ):
            return "化验"

        if len(mentioned_tests) > 0:
            return mentioned_tests[0]

        return "未指明检查"

    # 粗略分类检查结果方向。
    def _classify_exam_result_text(self, text: str) -> str:
        normalized = self._normalize_exam_text(text)

        if any(keyword in normalized for keyword in ("阴性", "未检出", "正常", "没有异常", "未见")):
            return "negative"

        numeric_direction = self._classify_numeric_exam_result(normalized)

        if numeric_direction != "unknown":
            return numeric_direction

        if any(keyword in normalized for keyword in ("阳性", "检出", "异常", "磨玻璃")):
            return "positive"

        if any(keyword in normalized for keyword in ("降低", "偏低", "很低", "低氧", "小于", "不到", "<")):
            return "low"

        if any(keyword in normalized for keyword in ("升高", "偏高", "大于", ">")):
            return "high"

        return "unknown"

    # 对常见检查阈值做轻量判断，避免真实报告式数值无法进入槽位。
    def _classify_numeric_exam_result(self, normalized_text: str) -> str:
        numbers = self._extract_numbers(normalized_text)

        if len(numbers) == 0:
            return "unknown"

        value = numbers[0]

        # 这里只做最常见、最临床相关的阈值粗分类，
        # 目标不是替代完整报告解析，而是让“CD4 150”“PaO2 68”能顺利进入槽位。
        if any(keyword in normalized_text for keyword in ("cd4", "t淋巴")):
            if value < 200:
                return "low"
            if value >= 500:
                return "negative"
            return "unknown"

        if any(keyword in normalized_text for keyword in ("pao2", "氧分压")):
            if value < 70:
                return "low"
            if value >= 80:
                return "negative"
            return "unknown"

        if any(keyword in normalized_text for keyword in ("spo2", "血氧")):
            if value < 94:
                return "low"
            if value >= 95:
                return "negative"
            return "unknown"

        if any(keyword in normalized_text for keyword in ("βd葡聚糖", "bdg", "葡聚糖", "g试验")):
            if value >= 80:
                return "high"
            if value < 60:
                return "negative"
            return "unknown"

        if any(keyword in normalized_text for keyword in ("hivrna", "病毒载量")):
            if value > 0:
                return "high"
            return "negative"

        return "unknown"

    # 从归一化文本中抽取浮点数。
    def _extract_numbers(self, normalized_text: str) -> list[float]:
        values: list[float] = []

        for match in re.findall(r"(?<![a-zA-Z])\d+(?:\.\d+)?(?![a-zA-Z])", normalized_text):
            try:
                values.append(float(match))
            except ValueError:
                continue

        return values

    # 将结果分句映射到当前 collect_exam_context 动作携带的候选证据节点。
    def _match_exam_result_to_candidate(
        self,
        exam_result: ExamContextResult,
        candidate: dict[str, Any],
    ) -> tuple[str, str, str] | None:
        candidate_name = str(candidate.get("name") or "").strip()
        candidate_label = str(candidate.get("label") or "").strip()
        normalized_candidate = self._normalize_exam_text(candidate_name)

        for result in exam_result.mentioned_results:
            normalized_text = self._normalize_exam_text(f"{result.test_name} {result.raw_text}")
            result_direction = result.normalized_result
            family_match = self._exam_result_family_matches(
                normalized_candidate,
                normalized_text,
                candidate_label,
                exam_result.exam_kind,
            )

            if not family_match:
                continue

            if result_direction == "negative":
                return "false", "certain", result.raw_text

            if result_direction in {"positive", "low", "high"}:
                return "true", "certain", result.raw_text

            return "true", "uncertain", result.raw_text

        return None

    # 用少量医学关键词判断“CD4 很低”是否能映射到 CD4 阈值节点等。
    def _exam_result_family_matches(
        self,
        normalized_candidate: str,
        normalized_text: str,
        candidate_label: str,
        exam_kind: str,
    ) -> bool:
        if len(normalized_candidate) > 0 and (
            normalized_candidate in normalized_text or normalized_text in normalized_candidate
        ):
            return True

        if exam_kind == "imaging" or candidate_label == "ImagingFinding":
            return any(keyword in normalized_candidate for keyword in ("ct", "影像", "磨玻璃", "胸片")) and any(
                keyword in normalized_text for keyword in ("ct", "影像", "磨玻璃", "胸片")
            )

        if exam_kind == "pathogen" or candidate_label == "Pathogen" or (
            exam_kind == "general"
            and any(keyword in normalized_candidate for keyword in ("pcr", "核酸", "病原", "肺孢子", "痰", "灌洗", "bal"))
        ):
            return any(keyword in normalized_candidate for keyword in ("pcr", "核酸", "病原", "肺孢子", "痰", "灌洗", "bal")) and any(
                keyword in normalized_text for keyword in ("pcr", "核酸", "病原", "肺孢子", "痰", "灌洗", "bal", "阳性", "检出")
            )

        if exam_kind in {"lab", "general"}:
            lab_family_rules = (
                (("cd4", "t淋巴"), ("cd4", "t淋巴", "很低", "偏低", "小于", "不到")),
                (("βd葡聚糖", "bdg", "葡聚糖", "g试验"), ("βd葡聚糖", "bdg", "葡聚糖", "g试验", "升高", "阳性")),
                (("pao2", "spo2", "氧分压", "低氧", "血氧"), ("pao2", "spo2", "氧分压", "低氧", "血氧")),
                (("hivrna", "病毒载量"), ("hivrna", "病毒载量")),
            )

            for candidate_keywords, text_keywords in lab_family_rules:
                if any(keyword in normalized_candidate for keyword in candidate_keywords) and any(
                    keyword in normalized_text for keyword in text_keywords
                ):
                    return True

        return False

    # 将患者提到的检查名 / 结果内部映射回 lab、imaging、pathogen，供状态更新和具体结果追问使用。
    def _infer_exam_kinds_from_mentions(
        self,
        mentioned_tests: Sequence[str],
        mentioned_results: Sequence[ExamMentionedResult],
    ) -> set[str]:
        values: set[str] = set()

        for text in list(mentioned_tests) + [item.test_name for item in mentioned_results] + [
            item.raw_text for item in mentioned_results
        ]:
            normalized = self._normalize_exam_text(text)

            if any(keyword in normalized for keyword in ("cd4", "hivrna", "病毒载量", "βd葡聚糖", "bdg", "葡聚糖", "g试验", "pao2", "spo2", "血氧", "氧分压", "ldh", "乳酸脱氢酶")):
                values.add("lab")

            if any(keyword in normalized for keyword in ("胸部ct", "ct", "胸片", "x线", "x光", "影像", "磨玻璃")):
                values.add("imaging")

            if any(keyword in normalized for keyword in ("pcr", "核酸", "痰检", "痰培养", "痰涂片", "支气管肺泡", "肺泡灌洗", "bal", "balf", "抗酸", "tspot", "xpert")):
                values.add("pathogen")

        return values

    # 检查上下文解析使用的统一归一化。
    def _normalize_exam_text(self, text: str) -> str:
        return self.normalizer.normalize_exam_text(text)

    # 将 LLM judge 的 JSON 负载转成 DeductiveDecision。
    def _coerce_judge_payload(
        self,
        payload: dict,
        action: MctsAction,
        answer_interpretation: A4DeductiveResult,
    ) -> DeductiveDecision:
        next_stage = str(payload.get("next_stage", "A3"))
        decision_type = str(payload.get("decision_type", "need_more_information"))

        # 先把 LLM 输出收敛到系统允许的枚举，避免上游判断分支被自由文本打穿。
        if decision_type not in {
            "confirm_hypothesis",
            "exclude_hypothesis",
            "reverify_hypothesis",
            "switch_hypothesis",
            "need_more_information",
        }:
            decision_type = "need_more_information"

        if next_stage not in {"A1", "A2", "A3", "A4", "STOP", "FALLBACK"}:
            next_stage = "A3"

        # supporting/negation/uncertain span 继续挂到 metadata，
        # 这样 router、audit 和前端都能追溯本轮判断是基于哪段患者原话。
        return DeductiveDecision(
            existence=str(payload.get("existence", answer_interpretation.existence)),
            certainty=str(payload.get("certainty", answer_interpretation.certainty)),
            decision_type=decision_type,  # type: ignore[arg-type]
            contradiction_explanation=str(payload.get("contradiction_explanation", "")),
            diagnostic_rationale=str(payload.get("diagnostic_rationale", payload.get("reasoning", answer_interpretation.reasoning))),
            next_stage=next_stage,  # type: ignore[arg-type]
            should_terminate_current_path=bool(payload.get("should_terminate_current_path", next_stage == "STOP")),
            should_spawn_alternative_hypotheses=bool(payload.get("should_spawn_alternative_hypotheses", False)),
            metadata={
                "next_topic_id": action.topic_id,
                "next_hypothesis_id": action.hypothesis_id,
                "supporting_span": answer_interpretation.supporting_span,
                "negation_span": answer_interpretation.negation_span,
                "uncertain_span": answer_interpretation.uncertain_span,
                "judge_source": "llm",
            },
        )

    # 在没有 LLM judge 时，基于回答解释与主备选假设做规则化演绎决策。
    def _build_rule_based_deductive_decision(
        self,
        action: MctsAction,
        answer_interpretation: A4DeductiveResult,
        current_hypothesis: HypothesisScore | None,
        alternatives: list[HypothesisScore],
    ) -> DeductiveDecision:
        margin = 0.0

        # margin 只在“当前主假设已存在且存在 alternatives”时才有意义，
        # 用来区分“已确认关键证据”后是直接 STOP 还是继续 A3。
        if current_hypothesis is not None and len(alternatives) > 0:
            margin = current_hypothesis.score - max(item.score for item in alternatives)

        # 明确阳性：支持当前路径；若主假设优势足够明显，可给出 STOP 倾向。
        if answer_interpretation.existence == "exist" and answer_interpretation.certainty == "confident":
            next_stage = "STOP" if current_hypothesis is not None and margin >= 1.0 else "A3"
            return DeductiveDecision(
                existence="exist",
                certainty="confident",
                decision_type="confirm_hypothesis",
                diagnostic_rationale="目标证据被明确确认，当前路径对主假设形成强支持。",
                next_stage=next_stage,
                should_terminate_current_path=next_stage == "STOP",
                should_spawn_alternative_hypotheses=False,
                metadata={
                    "next_topic_id": action.topic_id,
                    "next_hypothesis_id": action.hypothesis_id,
                    "supporting_span": answer_interpretation.supporting_span,
                    "judge_source": "rule",
                    "path_terminal": next_stage == "STOP",
                },
            )

        # 明确阴性：当前关键证据被反驳，优先退回 A2 重整 hypothesis 排名。
        if answer_interpretation.existence == "non_exist" and answer_interpretation.certainty == "confident":
            return DeductiveDecision(
                existence="non_exist",
                certainty="confident",
                decision_type="exclude_hypothesis",
                contradiction_explanation=f"关键证据“{action.target_node_name}”被明确否定，当前假设需要被下调或切换。",
                diagnostic_rationale="当前回答对主假设形成稳定反证。",
                next_stage="A2",
                should_terminate_current_path=False,
                should_spawn_alternative_hypotheses=len(alternatives) > 0,
                metadata={
                    "next_topic_id": action.topic_id,
                    "next_hypothesis_id": action.hypothesis_id,
                    "negation_span": answer_interpretation.negation_span,
                    "contradicted_feature": action.target_node_id,
                    "judge_source": "rule",
                },
            )

        # 模糊阳性：保留当前假设，但继续 A3 复核，不急着终止路径。
        if answer_interpretation.existence == "exist" and answer_interpretation.certainty == "doubt":
            return DeductiveDecision(
                existence="exist",
                certainty="doubt",
                decision_type="reverify_hypothesis",
                diagnostic_rationale="回答对目标证据提供了模糊支持，建议继续 A3 做更细的验证。",
                next_stage="A3",
                should_terminate_current_path=False,
                should_spawn_alternative_hypotheses=False,
                metadata={
                    "next_topic_id": action.topic_id,
                    "next_hypothesis_id": action.hypothesis_id,
                    "uncertain_span": answer_interpretation.uncertain_span,
                    "judge_source": "rule",
                },
            )

        # 模糊阴性：不直接排除，先把它当成“需要更多信息”的矛盾分析入口。
        if answer_interpretation.existence == "non_exist" and answer_interpretation.certainty == "doubt":
            return DeductiveDecision(
                existence="non_exist",
                certainty="doubt",
                decision_type="need_more_information",
                contradiction_explanation=(
                    f"“{action.target_node_name}”目前只表现出弱否定。"
                    "需要判断这是患者忽略、表述模糊还是当前假设确实不成立。"
                ),
                diagnostic_rationale="建议继续 A3 做矛盾分析，同时保留备选假设。",
                next_stage="A3",
                should_terminate_current_path=False,
                should_spawn_alternative_hypotheses=True,
                metadata={
                    "next_topic_id": action.topic_id,
                    "next_hypothesis_id": action.hypothesis_id,
                    "negation_span": answer_interpretation.negation_span,
                    "uncertain_span": answer_interpretation.uncertain_span,
                    "need_contradiction_analysis": True,
                    "judge_source": "rule",
                },
            )

        return DeductiveDecision(
            existence=answer_interpretation.existence,
            certainty=answer_interpretation.certainty,
            decision_type="switch_hypothesis",
            diagnostic_rationale="当前回答无法稳定支持现有路径，建议回到 A1/A2 重新整理线索。",
            next_stage="A1" if current_hypothesis is None else "A2",
            should_terminate_current_path=False,
            should_spawn_alternative_hypotheses=len(alternatives) > 0,
            metadata={
                "next_topic_id": action.topic_id,
                "next_hypothesis_id": action.hypothesis_id,
                "judge_source": "rule",
            },
        )

    # 将输入统一转换为 PatientContext，便于上游既可以传原文也可以传结构化上下文。
    def _ensure_patient_context(self, patient_input: str | PatientContext) -> PatientContext:
        if isinstance(patient_input, PatientContext):
            return patient_input

        return PatientContext(
            clinical_features=[],
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

    # 聚焦包含目标词的分句，减少整句级否定对目标判断的污染。
    def _collect_target_relevant_spans(self, patient_text: str, target_name: str) -> List[str]:
        clauses = [
            clause.strip()
            for clause in re.split(r"[，。！？；;,.!\n]", patient_text)
            if len(clause.strip()) > 0
        ]
        matched = [clause for clause in clauses if target_name in clause]

        if len(matched) > 0:
            return matched

        if len(patient_text.strip()) <= 8:
            return [patient_text.strip()]

        return []

    # 抽取回答是否是直接的“有/没有/不确定”短答。
    def _classify_direct_reply(self, patient_text: str) -> str | None:
        stripped_text = patient_text.strip().rstrip("。！？!?；;，,")

        if stripped_text in {"有", "有的", "是的", "会", "存在"}:
            return "positive"

        if stripped_text in {"没有", "没有的", "不是", "不会", "无"}:
            return "negative"

        if stripped_text in {"不确定", "不太清楚", "说不上来", "没有特别注意到"}:
            return "uncertain"

        if any(phrase in stripped_text for phrase in {"没有特别注意到", "不太清楚", "说不上来", "不确定"}):
            return "uncertain"

        if len(stripped_text) <= 20:
            if stripped_text.startswith(("有", "是", "会")):
                return "positive"
            if stripped_text.startswith(("没有", "不是", "不会", "无")):
                return "negative"
            if stripped_text.startswith(("不确定", "不太清楚", "说不上来", "没注意")):
                return "uncertain"

        return None

    # 从聚焦分句中提取首个包含指定模式的 span。
    def _extract_target_span(self, spans: Sequence[str], patterns: Sequence[str]) -> str:
        for span in spans:
            if any(re.search(pattern, span) is not None for pattern in patterns):
                return span

        return ""

    # 基于目标相关分句而不是整句做存在性与确定性判断。
    def _infer_target_aware_existence_and_certainty(
        self,
        patient_text: str,
        target_name: str,
        focused_spans: Sequence[str],
        direct_reply: str | None,
        negation_span: str,
        uncertain_span: str,
        supporting_span: str,
    ) -> tuple[str, str]:
        if direct_reply == "positive":
            return "exist", "confident"

        if direct_reply == "negative":
            return "non_exist", "confident"

        if direct_reply == "uncertain":
            return "unknown", "doubt"

        focused_text = "；".join(focused_spans)

        if len(focused_text) == 0:
            return self._infer_existence_and_certainty(patient_text, target_name)

        if len(uncertain_span) > 0 and len(negation_span) == 0:
            return "exist", "doubt"

        if len(negation_span) > 0 and len(uncertain_span) > 0:
            return "non_exist", "doubt"

        if len(negation_span) > 0:
            return "non_exist", "confident"

        if len(supporting_span) > 0:
            return "exist", "confident"

        return "unknown", "doubt"
