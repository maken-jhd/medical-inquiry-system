"""定义虚拟病人病例结构和槽位真值结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


BehaviorStyle = Literal["cooperative", "guarded", "vague", "concealing"]


@dataclass
class SlotTruth:
    """表示单个槽位在虚拟病人中的真实答案。"""

    node_id: str
    value: Any
    mention_style: str = "direct"
    reveal_only_if_asked: bool = True
    aliases: List[str] = field(default_factory=list)


@dataclass
class VirtualPatientCase:
    """表示一条完整的虚拟病人病例。"""

    case_id: str
    title: str
    true_disease_phase: Optional[str] = None
    true_conditions: List[str] = field(default_factory=list)
    chief_complaint: str = ""
    behavior_style: BehaviorStyle = "cooperative"
    slot_truth_map: Dict[str, SlotTruth] = field(default_factory=dict)
    hidden_slots: List[str] = field(default_factory=list)
    red_flags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
