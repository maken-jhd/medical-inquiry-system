"""定义虚拟病人开场、回答与 LLM 草稿相关的数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PatientReply:
    """表示虚拟病人对单个问题的回答结果。"""

    # 最终返回给问诊系统或前端的自然语言回答文本。
    answer_text: str
    # 这句回答主要揭示了哪个真实槽位；没有命中具体槽位时可为空。
    revealed_slot_id: Optional[str] = None
    # 当前回答和病例真值的匹配置信度，便于调试 fallback/模糊匹配。
    confidence: float = 1.0


@dataclass
class PatientOpening:
    """表示虚拟病人的首轮开场发言。"""

    # 病人开场第一句话。
    opening_text: str
    # 开场时已经主动暴露出来的槽位 ID 列表。
    revealed_slot_ids: list[str] = field(default_factory=list)


@dataclass
class PatientOpeningDraft:
    """表示 LLM 生成的患者开场语。"""

    # LLM 草拟出的开场文本。
    opening_text: str
    # LLM 生成这句开场时的简短解释，主要用于调试。
    reasoning: str = ""


@dataclass
class PatientAnswerDraft:
    """表示 LLM 生成的患者回答。"""

    # LLM 草拟出的患者回答文本。
    answer_text: str
    # LLM 生成该回答时的简短理由或风格说明。
    reasoning: str = ""
