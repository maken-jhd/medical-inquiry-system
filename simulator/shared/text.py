"""提供 simulator 侧复用的轻量文本归一化工具。"""

from __future__ import annotations


def normalize_name_match_text(value: str) -> str:
    """统一文本格式，便于在 replay/benchmark 中做宽松名称比较。"""

    return (
        value.strip()
        .lower()
        .replace(" ", "")
        .replace("（", "(")
        .replace("）", ")")
        .replace("，", ",")
        .replace("。", "")
        .replace("、", "")
        .replace("-", "")
        .replace("_", "")
        .replace("/", "")
    )
