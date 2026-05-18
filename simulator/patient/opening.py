"""封装虚拟病人首轮开场生成逻辑。"""

from __future__ import annotations

from ..cases.schema import SlotTruth, VirtualPatientCase
from .helpers import display_name, truth_is_positive
from .llm import try_generate_opening_with_llm


def collect_opening_truths(case: VirtualPatientCase) -> list[SlotTruth]:
    """收集首轮可以主动暴露的真实槽位。"""

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
        if not truth_is_positive(truth):
            continue
        opening_truths.append(truth)
    return opening_truths


def render_opening(
    truths: list[SlotTruth],
    case: VirtualPatientCase,
    *,
    use_llm: bool,
    llm_client,
) -> str:
    """优先用 LLM 生成自然 opening，失败时退回规则模板。"""

    labels = [display_name(truth) for truth in truths if display_name(truth)]
    llm_text = try_generate_opening_with_llm(
        labels=labels,
        truths=truths,
        case=case,
        use_llm=use_llm,
        llm_client=llm_client,
    )
    if llm_text:
        return llm_text
    return render_opening_fallback(truths)


def render_opening_fallback(truths: list[SlotTruth]) -> str:
    """使用确定性模板生成首轮开场。"""

    labels = [display_name(truth) for truth in truths if display_name(truth)]
    if not labels:
        return "最近想来咨询一下身体情况。"

    symptom_like = [truth for truth in truths if truth.group in {"symptom", "detail"}]
    exam_like = [truth for truth in truths if truth.group in {"lab", "imaging", "pathogen"}]
    risk_like = [truth for truth in truths if truth.group == "risk"]

    if symptom_like:
        symptom_names = [display_name(truth) for truth in symptom_like[:3]]
        if len(symptom_names) == 1:
            return f"最近主要是{symptom_names[0]}，想来看看是怎么回事。"
        if len(symptom_names) == 2:
            return f"最近主要是{symptom_names[0]}，还伴有{symptom_names[1]}。"
        return f"最近主要是{symptom_names[0]}、{symptom_names[1]}，还有{symptom_names[2]}。"

    if exam_like:
        exam_names = [display_name(truth) for truth in exam_like[:3]]
        if len(exam_names) == 1:
            return f"最近检查提示{exam_names[0]}，想进一步看看。"
        return f"最近检查提示{exam_names[0]}、{exam_names[1]}，想进一步看看。"

    if risk_like:
        risk_names = [display_name(truth) for truth in risk_like[:2]]
        if len(risk_names) == 1:
            return f"最近主要想咨询一下{risk_names[0]}相关的情况。"
        return f"最近主要想咨询一下{risk_names[0]}、{risk_names[1]}相关的情况。"

    if len(labels) == 1:
        return f"最近主要是{labels[0]}。"
    if len(labels) == 2:
        return f"最近主要是{labels[0]}，还伴有{labels[1]}。"
    return f"最近主要是{labels[0]}、{labels[1]}，还有{labels[2]}。"
