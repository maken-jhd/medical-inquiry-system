"""封装虚拟病人对问题的槽位匹配与检查上下文匹配逻辑。"""

from __future__ import annotations

from ..cases.schema import SlotTruth, VirtualPatientCase
from .helpers import display_name, truth_exam_group, truth_is_positive
from .llm import try_resolve_truth_with_llm
from .types import PatientReply


def resolve_truth(
    *,
    question_node_id: str,
    question_text: str,
    case: VirtualPatientCase,
) -> SlotTruth | None:
    """按 node_id、问题文本和 aliases 从病例真值表中解析最匹配的槽位。"""

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


def resolve_truth_with_fallback(
    *,
    question_node_id: str,
    question_text: str,
    case: VirtualPatientCase,
    use_llm: bool,
    llm_client,
) -> tuple[SlotTruth | None, str | None]:
    """先做规则匹配，失败时再用 LLM 在候选槽位内做语义等价匹配。"""

    truth = resolve_truth(question_node_id=question_node_id, question_text=question_text, case=case)
    if truth is not None:
        return truth, None

    return try_resolve_truth_with_llm(
        question_node_id=question_node_id,
        question_text=question_text,
        case=case,
        use_llm=use_llm,
        llm_client=llm_client,
    )


def render_exam_context_reply(
    *,
    question_node_id: str,
    question_text: str,
    case: VirtualPatientCase,
    render_unknown_reply,
) -> PatientReply | None:
    """对检查上下文问题直接汇总病例里的检查真值回答。"""

    prefix = "__exam_context__::"
    if not question_node_id.startswith(prefix):
        return None

    exam_kind = question_node_id.removeprefix(prefix).strip() or "general"
    truths = collect_exam_context_truths(exam_kind=exam_kind, case=case)

    if len(truths) == 0:
        return render_unknown_reply(question_text, case)

    positive_truths = [truth for truth in truths if truth_is_positive(truth)]
    negative_truths = [truth for truth in truths if not truth_is_positive(truth)]

    if len(positive_truths) > 0:
        selected = positive_truths[:3]
        names = "、".join(display_name(truth) for truth in selected)
        return PatientReply(
            answer_text=f"做过，结果提示{names}。",
            revealed_slot_id=selected[0].node_id,
        )

    selected = negative_truths[:3]
    names = "、".join(display_name(truth) for truth in selected)
    return PatientReply(
        answer_text=f"做过相关检查，没有提示{names}。",
        revealed_slot_id=selected[0].node_id,
    )


def collect_exam_context_truths(*, exam_kind: str, case: VirtualPatientCase) -> list[SlotTruth]:
    """按检查类型收集可用于 exam_context 回答的槽位真值。"""

    allowed_groups_by_kind = {
        "general": {"lab", "imaging", "pathogen"},
        "lab": {"lab"},
        "imaging": {"imaging"},
        "pathogen": {"pathogen"},
    }
    allowed_groups = allowed_groups_by_kind.get(exam_kind, {exam_kind})
    values: list[SlotTruth] = []

    for truth in case.slot_truth_map.values():
        if truth.node_id in case.hidden_slots and case.behavior_style in {"guarded", "concealing"}:
            continue

        if truth_exam_group(truth) not in allowed_groups:
            continue

        values.append(truth)

    return values
