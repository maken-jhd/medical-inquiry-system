"""根据病例真值表模拟虚拟病人的回答行为。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .case_schema import SlotTruth, VirtualPatientCase


@dataclass
class PatientReply:
    """表示虚拟病人对单个问题的回答结果。"""

    answer_text: str
    revealed_slot_id: Optional[str] = None
    confidence: float = 1.0


class VirtualPatientAgent:
    """根据病例设定生成符合行为风格的病人回答。"""

    # 根据当前问题、问题文本和病例真值表生成病人回答。
    def answer_question(
        self,
        question_node_id: str,
        question_text: str,
        case: VirtualPatientCase,
    ) -> PatientReply:
        truth = self._resolve_truth(question_node_id, question_text, case)

        if (
            truth is not None
            and truth.node_id in case.hidden_slots
            and case.behavior_style in {"guarded", "concealing"}
        ):
            return PatientReply("这个问题我不太想回答。", confidence=0.3)

        if truth is None:
            if case.behavior_style == "vague":
                return PatientReply("说不上来，感觉不太明显。", confidence=0.4)

            return PatientReply("没有特别注意到。", confidence=0.5)

        return self._render_truth(truth)

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

    # 将槽位真值渲染成自然语言形式的回答。
    def _render_truth(self, truth: SlotTruth) -> PatientReply:
        if isinstance(truth.value, bool):
            answer_text = "有。" if truth.value else "没有。"

            if truth.mention_style == "vague":
                answer_text = "好像有一点。" if truth.value else "感觉不像。"

            return PatientReply(answer_text, revealed_slot_id=truth.node_id)

        return PatientReply(str(truth.value), revealed_slot_id=truth.node_id)
