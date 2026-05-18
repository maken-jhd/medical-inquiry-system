"""封装虚拟病人开场、回答和语义匹配的 LLM 适配逻辑。"""

from __future__ import annotations

from typing import Any

from brain.integrations import LlmClient, PatientSlotSemanticMatchDraft

from ..cases.schema import SlotTruth, VirtualPatientCase
from .helpers import display_name
from .types import PatientAnswerDraft, PatientOpeningDraft


def llm_available(*, use_llm: bool, llm_client: LlmClient | None) -> bool:
    """判断当前虚拟病人是否允许走 LLM 表达分支。"""

    return bool(use_llm and llm_client is not None and llm_client.is_available())


def try_resolve_truth_with_llm(
    *,
    question_node_id: str,
    question_text: str,
    case: VirtualPatientCase,
    use_llm: bool,
    llm_client: LlmClient | None,
) -> tuple[SlotTruth | None, str | None]:
    """在精确匹配失败时，只允许 LLM 在病例已有候选槽位内做语义等价匹配。"""

    if not llm_available(use_llm=use_llm, llm_client=llm_client) or len(case.slot_truth_map) == 0:
        return None, None

    candidate_slots = [
        {
            "node_id": truth.node_id,
            "name": display_name(truth),
            "group": truth.group,
            "node_label": truth.node_label,
            "aliases": list(truth.aliases),
            "value_type": type(truth.value).__name__,
        }
        for truth in case.slot_truth_map.values()
    ]

    try:
        draft = llm_client.run_structured_prompt(
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

    return None, no_match_answer or "这个我不太确定，没听医生提过。"


def try_generate_opening_with_llm(
    *,
    labels: list[str],
    truths: list[SlotTruth],
    case: VirtualPatientCase,
    use_llm: bool,
    llm_client: LlmClient | None,
) -> str | None:
    """尝试让 LLM 生成更自然的首轮 opening，但必须保留关键医学锚点。"""

    if not llm_available(use_llm=use_llm, llm_client=llm_client) or not labels:
        return None

    try:
        draft = llm_client.run_structured_prompt(
            "patient_opening_generation",
            {
                "behavior_style": case.behavior_style,
                "true_conditions": case.true_conditions,
                "opening_slots": [
                    {
                        "node_id": truth.node_id,
                        "name": display_name(truth),
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
    if not opening_preserves_critical_anchors(opening_text, truths):
        return None
    return opening_text or None


def opening_preserves_critical_anchors(opening_text: str, truths: list[SlotTruth]) -> bool:
    """确保开场语没有把关键检查/病原锚点压缩成过于模糊的描述。"""

    if len(opening_text.strip()) == 0:
        return True

    normalized_opening = normalize_anchor_text(opening_text)

    for truth in truths:
        anchor_groups = opening_anchor_groups(truth)

        for anchor_group in anchor_groups:
            if not any(anchor in normalized_opening for anchor in anchor_group):
                return False

    return True


def opening_anchor_groups(truth: SlotTruth) -> list[list[str]]:
    """为关键检查/病原槽位生成必须在开场文本中保留的一组等价锚点。"""

    if truth.group not in {"lab", "imaging", "pathogen"} and truth.node_label not in {
        "LabFinding",
        "LabTest",
        "ImagingFinding",
        "Pathogen",
    }:
        return []

    display = display_name(truth)
    normalized_name = normalize_anchor_text(display)
    anchor_groups: list[list[str]] = []

    if "cd4" in normalized_name or "t淋巴" in normalized_name:
        anchor_groups.append(["cd4", "t淋巴"])
        if "200" in normalized_name:
            anchor_groups.append(["200", "低于200", "<200"])
        return anchor_groups

    if "hivrna" in normalized_name or "病毒载量" in normalized_name or "病毒量" in normalized_name:
        anchor_groups.append(["hivrna", "病毒载量", "病毒量"])
        if any(marker in normalized_name for marker in ("阳性", "检出", "检测到")):
            anchor_groups.append(["阳性", "检出", "检测到", "还能检测"])
        return anchor_groups

    if truth.group == "pathogen" or truth.node_label == "Pathogen":
        pathogen_name = normalized_name.removesuffix("阳性").removesuffix("检出")
        if len(pathogen_name) > 0:
            anchor_groups.append([pathogen_name])
        return anchor_groups

    if truth.group == "imaging" or truth.node_label == "ImagingFinding":
        if "磨玻璃" in normalized_name:
            anchor_groups.append(["磨玻璃"])
        elif len(normalized_name) > 0:
            anchor_groups.append([normalized_name])
        return anchor_groups

    if any(keyword in normalized_name for keyword in ("βd葡聚糖", "bdg", "葡聚糖", "g试验")):
        anchor_groups.append(["βd葡聚糖", "bdg", "葡聚糖", "g试验"])

    result_markers = [marker for marker in ("阳性", "阴性", "升高", "降低", "偏低", "偏高") if marker in normalized_name]
    if len(result_markers) > 0:
        anchor_groups.append(result_markers)

    return anchor_groups


def normalize_anchor_text(value: str) -> str:
    """为开场锚点校验做轻量文本归一化。"""

    return str(value).strip().replace(" ", "").replace("-", "").replace("_", "").lower()


def try_generate_answer_with_llm(
    *,
    question_text: str,
    truth: SlotTruth | None,
    answer_mode: str,
    behavior_style: str,
    use_llm: bool,
    llm_client: LlmClient | None,
) -> str | None:
    """尝试让 LLM 生成更自然的病人回答。"""

    if not llm_available(use_llm=use_llm, llm_client=llm_client):
        return None

    slot_payload: dict[str, Any] | None = None
    if truth is not None:
        slot_payload = {
            "node_id": truth.node_id,
            "name": display_name(truth),
            "value": truth.value,
            "group": truth.group,
            "node_label": truth.node_label,
            "mention_style": truth.mention_style,
            "aliases": list(truth.aliases),
        }

    try:
        draft = llm_client.run_structured_prompt(
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
