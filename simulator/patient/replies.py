"""封装虚拟病人对已知、隐藏和未知槽位的回答渲染逻辑。"""

from __future__ import annotations

from ..cases.schema import SlotTruth, VirtualPatientCase
from .llm import try_generate_answer_with_llm
from .types import PatientReply


def render_truth(
    *,
    question_text: str,
    truth: SlotTruth,
    behavior_style: str,
    use_llm: bool,
    llm_client,
) -> PatientReply:
    """将命中的真实槽位渲染成病人回答。"""

    llm_answer = try_generate_answer_with_llm(
        question_text=question_text,
        truth=truth,
        answer_mode="known",
        behavior_style=behavior_style,
        use_llm=use_llm,
        llm_client=llm_client,
    )
    if llm_answer:
        return PatientReply(llm_answer, revealed_slot_id=truth.node_id)

    if isinstance(truth.value, bool):
        answer_text = "有。" if truth.value else "没有。"

        if truth.mention_style == "vague":
            answer_text = "好像有一点。" if truth.value else "感觉不像。"

        return PatientReply(answer_text, revealed_slot_id=truth.node_id)

    return PatientReply(str(truth.value), revealed_slot_id=truth.node_id)


def render_hidden_reply(
    *,
    question_text: str,
    truth: SlotTruth,
    case: VirtualPatientCase,
    use_llm: bool,
    llm_client,
) -> PatientReply:
    """对 guarded / concealing 病例中的隐藏槽位给出回避式回答。"""

    llm_answer = try_generate_answer_with_llm(
        question_text=question_text,
        truth=truth,
        answer_mode="hidden",
        behavior_style=case.behavior_style,
        use_llm=use_llm,
        llm_client=llm_client,
    )
    if llm_answer:
        return PatientReply(llm_answer, confidence=0.3)
    return PatientReply("这个问题我不太想回答。", confidence=0.3)


def render_unknown_reply(
    question_text: str,
    case: VirtualPatientCase,
    *,
    use_llm: bool,
    llm_client,
) -> PatientReply:
    """对病例里没有对应槽位的问题给出不确定回答。"""

    llm_answer = try_generate_answer_with_llm(
        question_text=question_text,
        truth=None,
        answer_mode="unknown",
        behavior_style=case.behavior_style,
        use_llm=use_llm,
        llm_client=llm_client,
    )
    if llm_answer:
        return PatientReply(llm_answer, confidence=0.4 if case.behavior_style == "vague" else 0.5)

    if case.behavior_style == "vague":
        return PatientReply("说不上来，不能确定有没有。", confidence=0.4)

    return PatientReply("这个我不太确定，没专门注意过。", confidence=0.5)
