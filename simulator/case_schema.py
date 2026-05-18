"""定义虚拟病人病例结构和槽位真值结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


BehaviorStyle = Literal["cooperative", "guarded", "vague", "concealing"]


@dataclass
class SlotTruth:
    """表示单个槽位在虚拟病人中的真实答案。"""

    # 图谱节点唯一 ID；虚拟病人靠它和 brain 追问的 target_node_id 对齐。
    node_id: str
    # 该槽位的真实值；可以是 bool、文本、数值或更复杂的结构。
    value: Any
    # 槽位所属的证据分组，如 symptom / lab / imaging / pathogen。
    group: str = ""
    # 该节点在图谱中的标签类型，如 ClinicalFinding、LabFinding。
    node_label: str = ""
    # 虚拟病人默认如何表达该事实，如 direct / indirect / vague。
    mention_style: str = "direct"
    # 是否只在系统明确问到该槽位时才透露，控制首轮开场和主动暴露行为。
    reveal_only_if_asked: bool = True
    # 该槽位可被问题文本命中的别名集合，帮助 patient agent 做模糊匹配。
    aliases: List[str] = field(default_factory=list)


@dataclass
class VirtualPatientCase:
    """表示一条完整的虚拟病人病例。"""

    # 病例唯一 ID；用于回放、benchmark 和产物命名。
    case_id: str
    # 病例标题或摘要名，主要给人读和调试定位使用。
    title: str
    # 该病例对应的真实疾病阶段，可为空；常用于 phase benchmark。
    true_disease_phase: Optional[str] = None
    # 该病例真实存在的疾病/并发症列表，可包含主病和竞争诊断。
    true_conditions: List[str] = field(default_factory=list)
    # 病人首轮主诉文本；若没有自动 opening，就优先用它开场。
    chief_complaint: str = ""
    # 病人的回答风格，如配合、保守、含糊或刻意隐瞒。
    behavior_style: BehaviorStyle = "cooperative"
    # 病例全部槽位真值表；key 通常是槽位 ID，value 是具体 SlotTruth。
    slot_truth_map: Dict[str, SlotTruth] = field(default_factory=dict)
    # 即使有真值也默认隐藏的槽位 ID，常用于 guarded / concealing 行为测试。
    hidden_slots: List[str] = field(default_factory=list)
    # 需要系统尽快识别或重点关注的危险信号列表。
    red_flags: List[str] = field(default_factory=list)
    # 其他病例附加信息，如生成来源、opening_slot_ids、benchmark 标签等。
    metadata: Dict[str, Any] = field(default_factory=dict)
