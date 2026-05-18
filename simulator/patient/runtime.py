"""编排虚拟病人的开场、槽位匹配与回答渲染。"""

from __future__ import annotations

from brain.integrations import LlmClient

from ..cases.schema import VirtualPatientCase
from .matching import render_exam_context_reply, resolve_truth_with_fallback
from .opening import collect_opening_truths, render_opening
from .replies import render_hidden_reply, render_truth, render_unknown_reply
from .types import PatientOpening, PatientReply


class VirtualPatientRuntime:
    """阶段化封装虚拟病人真实运行逻辑。"""

    # 初始化虚拟病人运行时，并按需装配 LLM client。
    def __init__(
        self,
        *,
        llm_client: LlmClient | None = None,
        use_llm: bool = False,
    ) -> None:
        self.use_llm = use_llm
        self.llm_client = llm_client or (LlmClient() if use_llm else None)

    # 根据病例骨架生成首轮 opening。
    def open_case(self, case: VirtualPatientCase) -> PatientOpening:
        opening_truths = collect_opening_truths(case)
        if opening_truths:
            opening_text = render_opening(
                opening_truths,
                case,
                use_llm=self.use_llm,
                llm_client=self.llm_client,
            )
            return PatientOpening(
                opening_text=opening_text,
                revealed_slot_ids=[truth.node_id for truth in opening_truths],
            )

        chief_text = case.chief_complaint.strip()
        if chief_text:
            return PatientOpening(opening_text=chief_text, revealed_slot_ids=[])

        return PatientOpening(opening_text="最近想来咨询一下身体情况。", revealed_slot_ids=[])

    # 根据问题和病例真值生成病人回答。
    def answer_question(
        self,
        question_node_id: str,
        question_text: str,
        case: VirtualPatientCase,
    ) -> PatientReply:
        # 检查上下文问题先走专门回答分支，避免把“做过哪些检查”误落到普通 truth matching。
        exam_reply = render_exam_context_reply(
            question_node_id=question_node_id,
            question_text=question_text,
            case=case,
            render_unknown_reply=lambda text, patient_case: render_unknown_reply(
                text,
                patient_case,
                use_llm=self.use_llm,
                llm_client=self.llm_client,
            ),
        )
        if exam_reply is not None:
            return exam_reply

        truth, no_match_answer = resolve_truth_with_fallback(
            question_node_id=question_node_id,
            question_text=question_text,
            case=case,
            use_llm=self.use_llm,
            llm_client=self.llm_client,
        )

        # guarded / concealing 病例对隐藏槽位仍然按原行为优先回避。
        if (
            truth is not None
            and truth.node_id in case.hidden_slots
            and case.behavior_style in {"guarded", "concealing"}
        ):
            return render_hidden_reply(
                question_text=question_text,
                truth=truth,
                case=case,
                use_llm=self.use_llm,
                llm_client=self.llm_client,
            )

        if truth is None:
            if no_match_answer:
                return PatientReply(no_match_answer, confidence=0.5)
            return render_unknown_reply(
                question_text,
                case,
                use_llm=self.use_llm,
                llm_client=self.llm_client,
            )

        return render_truth(
            question_text=question_text,
            truth=truth,
            behavior_style=case.behavior_style,
            use_llm=self.use_llm,
            llm_client=self.llm_client,
        )
