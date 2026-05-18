"""提供虚拟病人开场、槽位匹配与回答渲染复用的小工具。"""

from __future__ import annotations

from ..cases.schema import SlotTruth


def display_name(truth: SlotTruth) -> str:
    """优先使用 alias 作为患者更自然的表述名。"""

    for alias in truth.aliases:
        alias_text = str(alias).strip()
        if alias_text:
            return alias_text
    return truth.node_id


def truth_exam_group(truth: SlotTruth) -> str:
    """兼容 group 缺失时根据节点标签反推检查类别。"""

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


def truth_is_positive(truth: SlotTruth) -> bool:
    """统一判断槽位真值在模拟时应被视为阳性还是阴性。"""

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
