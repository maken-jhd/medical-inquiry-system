"""根据病例真值表模拟虚拟病人的回答行为。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from brain.llm_client import LlmClient, PatientSlotSemanticMatchDraft
from .case_schema import SlotTruth, VirtualPatientCase


@dataclass
class PatientReply:
    """表示虚拟病人对单个问题的回答结果。"""

    answer_text: str
    revealed_slot_id: Optional[str] = None
    confidence: float = 1.0


@dataclass
class PatientOpening:
    """表示虚拟病人的首轮开场发言。"""

    opening_text: str
    revealed_slot_ids: list[str] = field(default_factory=list)


@dataclass
class PatientOpeningDraft:
    """表示 LLM 生成的患者开场语。"""

    opening_text: str
    reasoning: str = ""


@dataclass
class PatientAnswerDraft:
    """表示 LLM 生成的患者回答。"""

    answer_text: str
    reasoning: str = ""


class VirtualPatientAgent:
    """根据病例设定生成符合行为风格的病人回答。"""

    def __init__(
        self,
        *,
        llm_client: LlmClient | None = None,
        use_llm: bool = False,
    ) -> None:
        self.use_llm = use_llm
        self.llm_client = llm_client or (LlmClient() if use_llm else None)

    # 根据病例骨架生成首轮开场发言。
    def open_case(self, case: VirtualPatientCase) -> PatientOpening:
        opening_truths = self._collect_opening_truths(case)
        if opening_truths:
            opening_text = self._render_opening(opening_truths, case)
            return PatientOpening(
                opening_text=opening_text,
                revealed_slot_ids=[truth.node_id for truth in opening_truths],
            )

        chief_text = case.chief_complaint.strip()
        if chief_text:
            return PatientOpening(opening_text=chief_text, revealed_slot_ids=[])

        return PatientOpening(opening_text="最近想来咨询一下身体情况。", revealed_slot_ids=[])

    # 根据当前问题、问题文本和病例真值表生成病人回答。
    def answer_question(
        self,
        question_node_id: str,
        question_text: str,
        case: VirtualPatientCase,
    ) -> PatientReply:
        exam_reply = self._render_exam_context_reply(question_node_id, question_text, case)

        if exam_reply is not None:
            return exam_reply

        truth = self._resolve_truth(question_node_id, question_text, case)
        no_match_answer: str | None = None

        if truth is None:
            truth, no_match_answer = self._try_resolve_truth_with_llm(
                question_node_id,
                question_text,
                case,
            )

        if (
            truth is not None
            and truth.node_id in case.hidden_slots
            and case.behavior_style in {"guarded", "concealing"}
        ):
            return self._render_hidden_reply(question_text, truth, case)

        if truth is None:
            if no_match_answer:
                return PatientReply(no_match_answer, confidence=0.5)
            return self._render_unknown_reply(question_text, case)

        return self._render_truth(question_text, truth, case.behavior_style)

    def _collect_opening_truths(self, case: VirtualPatientCase) -> list[SlotTruth]:
        opening_truths: list[SlotTruth] = []

        for truth in case.slot_truth_map.values():
            if truth.reveal_only_if_asked:
                continue
            if isinstance(truth.value, bool) and not truth.value:
                continue
            opening_truths.append(truth)

        if opening_truths:
            return opening_truths

        metadata_opening_ids = case.metadata.get("opening_slot_ids")
        if not isinstance(metadata_opening_ids, list):
            return []

        for slot_id in metadata_opening_ids:
            truth = case.slot_truth_map.get(str(slot_id))
            if truth is None:
                continue
            if isinstance(truth.value, bool) and not truth.value:
                continue
            opening_truths.append(truth)
        return opening_truths

    def _render_opening(self, truths: list[SlotTruth], case: VirtualPatientCase) -> str:
        labels = [self._display_name(truth) for truth in truths if self._display_name(truth)]
        llm_text = self._try_generate_opening_with_llm(labels, truths, case)
        if llm_text:
            return llm_text
        return self._render_opening_fallback(truths)

    # 根据问题节点和问题文本，从病例真值表中解析最匹配的槽位。
    def _resolve_truth(
        self,
        question_node_id: str,
        question_text: str,
        case: VirtualPatientCase,
    ) -> Optional[SlotTruth]:
        direct_truth = case.slot_truth_map.get(question_node_id)

        if direct_truth is not None:
            return direct_truth

        for truth in case.slot_truth_map.values():
            if truth.node_id == question_node_id:
                return truth

            if truth.node_id in question_text:
                return truth

            if any(alias in question_text or alias == question_node_id for alias in truth.aliases):
                return truth

        return None

    # 对检查上下文动作直接汇总病例里的检查真值，避免虚拟病人把已知检查答成“不记得”。
    def _render_exam_context_reply(
        self,
        question_node_id: str,
        question_text: str,
        case: VirtualPatientCase,
    ) -> PatientReply | None:
        prefix = "__exam_context__::"

        if not question_node_id.startswith(prefix):
            return None

        exam_kind = question_node_id.removeprefix(prefix).strip() or "general"
        truths = self._collect_exam_context_truths(exam_kind, case)

        if len(truths) == 0:
            return self._render_unknown_reply(question_text, case)

        positive_truths = [truth for truth in truths if self._truth_is_positive(truth)]
        negative_truths = [truth for truth in truths if not self._truth_is_positive(truth)]

        if len(positive_truths) > 0:
            selected = positive_truths[:3]
            names = "、".join(self._display_name(truth) for truth in selected)
            return PatientReply(
                answer_text=f"做过，结果提示{names}。",
                revealed_slot_id=selected[0].node_id,
            )

        selected = negative_truths[:3]
        names = "、".join(self._display_name(truth) for truth in selected)
        return PatientReply(
            answer_text=f"做过相关检查，没有提示{names}。",
            revealed_slot_id=selected[0].node_id,
        )

    # 按检查类型收集病例真值；general 会汇总所有检查、影像和病原学槽位。
    def _collect_exam_context_truths(self, exam_kind: str, case: VirtualPatientCase) -> list[SlotTruth]:
        allowed_groups_by_kind = {
            "general": {"lab", "imaging", "pathogen"},
            "lab": {"lab"},
            "imaging": {"imaging"},
            "pathogen": {"pathogen"},
        }
        allowed_groups = allowed_groups_by_kind.get(exam_kind, {exam_kind})
        values: list[SlotTruth] = []

        for truth in case.slot_truth_map.values():
            if (
                truth.node_id in case.hidden_slots
                and case.behavior_style in {"guarded", "concealing"}
            ):
                continue

            if self._truth_exam_group(truth) not in allowed_groups:
                continue

            values.append(truth)

        return values

    # 兼容病例骨架 group 缺失时用节点标签兜底识别检查类别。
    def _truth_exam_group(self, truth: SlotTruth) -> str:
        group = str(truth.group or "").strip()

        if group in {"lab", "imaging", "pathogen"}:
            return group

        if truth.node_label in {"LabFinding", "LabTest"}:
            return "lab"

        if truth.node_label == "ImagingFinding":
            return "imaging"

        if truth.node_label == "Pathogen":
            return "pathogen"

        return group

    def _truth_is_positive(self, truth: SlotTruth) -> bool:
        if isinstance(truth.value, bool):
            return truth.value

        value_text = str(truth.value).strip().lower()
        negative_values = {
            "",
            "false",
            "0",
            "none",
            "null",
            "negative",
            "absent",
            "阴性",
            "未见",
            "未检出",
            "无",
            "否",
            "正常",
        }
        return value_text not in negative_values

    # 精确匹配失败时，允许 LLM 在病例已有槽位候选内做一次语义匹配。
    def _try_resolve_truth_with_llm(
        self,
        question_node_id: str,
        question_text: str,
        case: VirtualPatientCase,
    ) -> tuple[SlotTruth | None, str | None]:
        if not self._llm_available() or len(case.slot_truth_map) == 0:
            return None, None

        candidate_slots = [
            {
                "node_id": truth.node_id,
                "name": self._display_name(truth),
                "group": truth.group,
                "node_label": truth.node_label,
                "aliases": list(truth.aliases),
                "value_type": type(truth.value).__name__,
            }
            for truth in case.slot_truth_map.values()
        ]

        try:
            draft = self.llm_client.run_structured_prompt(
                "patient_slot_semantic_match",
                {
                    "question_node_id": question_node_id,
                    "question_text": question_text,
                    "candidate_slots": candidate_slots,
                },
                PatientSlotSemanticMatchDraft,
            )
        except Exception:
            return None, None

        if isinstance(draft, dict):
            matched_node_id = str(draft.get("matched_node_id") or "").strip()
            no_match_answer = str(draft.get("no_match_answer") or "").strip()
        else:
            matched_node_id = str(getattr(draft, "matched_node_id", "") or "").strip()
            no_match_answer = str(getattr(draft, "no_match_answer", "") or "").strip()

        if len(matched_node_id) > 0 and matched_node_id in case.slot_truth_map:
            return case.slot_truth_map[matched_node_id], None

        return None, no_match_answer or "没有相关情况。"

    # 将槽位真值渲染成自然语言形式的回答。
    def _render_truth(self, question_text: str, truth: SlotTruth, behavior_style: str) -> PatientReply:
        llm_answer = self._try_generate_answer_with_llm(
            question_text=question_text,
            truth=truth,
            answer_mode="known",
            behavior_style=behavior_style,
        )
        if llm_answer:
            return PatientReply(llm_answer, revealed_slot_id=truth.node_id)

        if isinstance(truth.value, bool):
            answer_text = "有。" if truth.value else "没有。"

            if truth.mention_style == "vague":
                answer_text = "好像有一点。" if truth.value else "感觉不像。"

            return PatientReply(answer_text, revealed_slot_id=truth.node_id)

        return PatientReply(str(truth.value), revealed_slot_id=truth.node_id)

    def _render_hidden_reply(
        self,
        question_text: str,
        truth: SlotTruth,
        case: VirtualPatientCase,
    ) -> PatientReply:
        llm_answer = self._try_generate_answer_with_llm(
            question_text=question_text,
            truth=truth,
            answer_mode="hidden",
            behavior_style=case.behavior_style,
        )
        if llm_answer:
            return PatientReply(llm_answer, confidence=0.3)
        return PatientReply("这个问题我不太想回答。", confidence=0.3)

    def _render_unknown_reply(self, question_text: str, case: VirtualPatientCase) -> PatientReply:
        llm_answer = self._try_generate_answer_with_llm(
            question_text=question_text,
            truth=None,
            answer_mode="unknown",
            behavior_style=case.behavior_style,
        )
        if llm_answer:
            return PatientReply(llm_answer, confidence=0.4 if case.behavior_style == "vague" else 0.5)

        if case.behavior_style == "vague":
            return PatientReply("说不上来，不能确定有没有。", confidence=0.4)

        return PatientReply("这个我不太确定，没专门注意过。", confidence=0.5)

    def _try_generate_opening_with_llm(
        self,
        labels: list[str],
        truths: list[SlotTruth],
        case: VirtualPatientCase,
    ) -> str | None:
        if not self._llm_available() or not labels:
            return None

        try:
            draft = self.llm_client.run_structured_prompt(
                "patient_opening_generation",
                {
                    "behavior_style": case.behavior_style,
                    "true_conditions": case.true_conditions,
                    "opening_slots": [
                        {
                            "node_id": truth.node_id,
                            "name": self._display_name(truth),
                            "group": truth.group,
                            "node_label": truth.node_label,
                            "mention_style": truth.mention_style,
                        }
                        for truth in truths
                    ],
                    "patient_profile": {
                        "age": case.metadata.get("age"),
                        "sex": case.metadata.get("sex"),
                        "scenario_group": case.metadata.get("scenario_group"),
                    },
                },
                PatientOpeningDraft,
            )
        except Exception:
            return None

        opening_text = str(getattr(draft, "opening_text", "") or "").strip()
        return opening_text or None

    def _try_generate_answer_with_llm(
        self,
        *,
        question_text: str,
        truth: SlotTruth | None,
        answer_mode: str,
        behavior_style: str = "cooperative",
    ) -> str | None:
        if not self._llm_available():
            return None

        slot_payload: dict[str, Any] | None = None
        if truth is not None:
            slot_payload = {
                "node_id": truth.node_id,
                "name": self._display_name(truth),
                "value": truth.value,
                "group": truth.group,
                "node_label": truth.node_label,
                "mention_style": truth.mention_style,
                "aliases": list(truth.aliases),
            }

        try:
            draft = self.llm_client.run_structured_prompt(
                "patient_answer_generation",
                {
                    "question_text": question_text,
                    "answer_mode": answer_mode,
                    "behavior_style": behavior_style,
                    "matched_slot": slot_payload,
                },
                PatientAnswerDraft,
            )
        except Exception:
            return None

        answer_text = str(getattr(draft, "answer_text", "") or "").strip()
        return answer_text or None

    def _render_opening_fallback(self, truths: list[SlotTruth]) -> str:
        labels = [self._display_name(truth) for truth in truths if self._display_name(truth)]
        if not labels:
            return "最近想来咨询一下身体情况。"

        symptom_like = [truth for truth in truths if truth.group in {"symptom", "detail"}]
        exam_like = [truth for truth in truths if truth.group in {"lab", "imaging", "pathogen"}]
        risk_like = [truth for truth in truths if truth.group == "risk"]

        if symptom_like:
            symptom_names = [self._display_name(truth) for truth in symptom_like[:3]]
            if len(symptom_names) == 1:
                return f"最近主要是{symptom_names[0]}，想来看看是怎么回事。"
            if len(symptom_names) == 2:
                return f"最近主要是{symptom_names[0]}，还伴有{symptom_names[1]}。"
            return f"最近主要是{symptom_names[0]}、{symptom_names[1]}，还有{symptom_names[2]}。"

        if exam_like:
            exam_names = [self._display_name(truth) for truth in exam_like[:3]]
            if len(exam_names) == 1:
                return f"最近检查提示{exam_names[0]}，想进一步看看。"
            return f"最近检查提示{exam_names[0]}、{exam_names[1]}，想进一步看看。"

        if risk_like:
            risk_names = [self._display_name(truth) for truth in risk_like[:2]]
            if len(risk_names) == 1:
                return f"最近主要想咨询一下{risk_names[0]}相关的情况。"
            return f"最近主要想咨询一下{risk_names[0]}、{risk_names[1]}相关的情况。"

        if len(labels) == 1:
            return f"最近主要是{labels[0]}。"
        if len(labels) == 2:
            return f"最近主要是{labels[0]}，还伴有{labels[1]}。"
        return f"最近主要是{labels[0]}、{labels[1]}，还有{labels[2]}。"

    def _display_name(self, truth: SlotTruth) -> str:
        for alias in truth.aliases:
            alias_text = str(alias).strip()
            if alias_text:
                return alias_text
        return truth.node_id

    def _llm_available(self) -> bool:
        return bool(self.use_llm and self.llm_client is not None and self.llm_client.is_available())
