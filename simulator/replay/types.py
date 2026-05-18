"""定义自动回放主链共享的结果类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ReplayTurn:
    """表示自动对战中的单轮问答记录。"""

    # 本轮追问对应的目标节点 ID，通常来自 brain 的 pending_action.target_node_id。
    question_node_id: str
    # 问诊系统本轮向虚拟病人提出的自然语言问题。
    question_text: str
    # 虚拟病人返回给问诊系统的自然语言回答。
    answer_text: str
    # 这是自动回放中的第几轮正式问答，从 1 开始。
    turn_index: int
    # 本轮回答最终揭示的真实槽位 ID；未命中时为空。
    revealed_slot_id: Optional[str] = None
    # 当前记录默认归属哪个 brain 阶段，主要用于调试和旧报表兼容。
    stage: str = "A3"
    # brain 在处理这轮回答后返回的 search_report 快照。
    search_report: dict = field(default_factory=dict)
    # 从 search_report 中展开出的 search_metadata，便于直接检索。
    search_metadata: dict = field(default_factory=dict)
    # 这轮真正被问出去的 action_id。
    asked_action_id: str = ""
    # 该 action 的类型，如 verify_evidence / collect_exam_context。
    asked_action_type: str = ""
    # 被询问目标节点的图谱标签，如 ClinicalFinding / LabFinding。
    asked_target_node_label: str = ""
    # 被询问目标节点的人类可读名称。
    asked_target_node_name: str = ""
    # 该 action 归属的假设 ID，便于回看是哪条候选推动了这次提问。
    asked_action_hypothesis_id: str = ""
    # 将 action 归一化后的问题组别，如 symptom / lab / exam_context。
    asked_action_group: str = "unknown"
    # brain 构造问题时附带的问题类型提示。
    asked_action_question_type_hint: str = ""
    # 获取该证据需要的方式，如 direct_ask / needs_lab_test。
    asked_action_acquisition_mode: str = ""
    # 该证据的成本标签，如 low / high / unknown。
    asked_action_evidence_cost: str = "unknown"
    # root action 最终来自哪个候选来源，如 default_search_action / repair。
    asked_action_selected_source: str = ""
    # action source 在优先级表中的排名，便于调试被谁覆盖。
    asked_action_selected_source_priority_rank: int = 0
    # 这轮回答是否命中了病例中的真实槽位。
    truth_hit: bool = False
    # 命中槽位所属的证据组别。
    revealed_slot_group: str = ""
    # 命中槽位的图谱标签。
    revealed_slot_label: str = ""
    # 命中槽位的人类可读名称。
    revealed_slot_name: str = ""
    # 命中槽位的真实值。
    revealed_slot_value: Any = None
    # 命中槽位是否为阳性/存在性证据；无法判断时为空。
    revealed_slot_positive: Optional[bool] = None
    # 命中槽位被归类到的 evidence family。
    revealed_slot_families: list[str] = field(default_factory=list)
    # 虚拟病人回答这一问耗费的秒数。
    patient_answer_seconds: float = 0.0
    # brain 消化这一轮回答并产出下一步耗费的秒数。
    brain_turn_seconds: float = 0.0
    # 本轮病人回答 + brain 处理的总耗时。
    total_seconds: float = 0.0


@dataclass
class ReplayResult:
    """表示单个病例自动对战完成后的回放结果。"""

    # 病例唯一 ID。
    case_id: str
    # 病例标题或摘要名称。
    case_title: str = ""
    # 病例类型，如 ordinary / competitive。
    case_type: str = ""
    # 病例自身的 QC 状态。
    case_qc_status: str = ""
    # benchmark 视角下的可评测状态。
    benchmark_qc_status: str = ""
    # 病例 QC 失败或降级的原因列表。
    case_qc_reasons: list[str] = field(default_factory=list)
    # 虚拟病人的首轮 opening 文本。
    opening_text: str = ""
    # opening 已主动暴露的槽位 ID 列表。
    opening_revealed_slot_ids: list[str] = field(default_factory=list)
    # 病例真实疾病/并发症列表。
    true_conditions: list[str] = field(default_factory=list)
    # 病例真实疾病阶段，可为空。
    true_disease_phase: Optional[str] = None
    # 病例标注的红旗线索。
    red_flags: list[str] = field(default_factory=list)
    # 整个自动回放中的逐轮问答明细。
    turns: list[ReplayTurn] = field(default_factory=list)
    # brain 最终给出的 final_report。
    final_report: dict = field(default_factory=dict)
    # opening 输入后的首轮 brain 输出快照。
    initial_output: dict = field(default_factory=dict)
    # 单病例分析结果，供 benchmark 和 debug 复用。
    analysis: dict = field(default_factory=dict)
    # 回放状态，如 pending / completed / max_turn_reached / failed。
    status: str = "pending"
    # 病例级 timing 摘要。
    timing: dict = field(default_factory=dict)
    # 失败病例的结构化错误信息。
    error: dict = field(default_factory=dict)


@dataclass
class ReplayConfig:
    """保存自动回放的基础参数。"""

    # 单病例最多允许多少轮正式问答，超过后会触发 finalize。
    max_turns: int = 8
