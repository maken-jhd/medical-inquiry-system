"""定义纯 LLM baseline 问诊医生使用的数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# 这组问题分组用于让 baseline 与现有 replay 分析口径对齐，
# 方便后续直接复用 question_count_by_group / truth_hit_by_group 统计。
BASELINE_QUESTION_GROUPS = (
    "symptom",
    "risk",
    "detail",
    "lab",
    "imaging",
    "pathogen",
    "exam_context",
)


# baseline 问题的成本先只保留 low / high / unknown 三档，
# 保持与当前 replay 汇总的 cost bucket 一致。
BASELINE_EVIDENCE_COSTS = ("low", "high", "unknown")


@dataclass
class BaselineHypothesisCandidate:
    """表示 baseline 当前维护的一条候选诊断。"""

    name: str = ""
    confidence: float = 0.0


@dataclass
class BaselineTurnDecisionDraft:
    """表示模型单轮 ask/final 决策的原始结构化输出。"""

    decision: str = ""
    question_text: str = ""
    question_group: str = ""
    target_name: str = ""
    evidence_cost: str = ""
    confidence: float = 0.0
    top3: list[dict[str, Any]] = field(default_factory=list)
    reasoning: str = ""
    compiled: bool = False
    final_answer: str = ""


@dataclass
class BaselineAskDecision:
    """表示规范化后的 ask 决策。"""

    question_text: str = ""
    question_group: str = "unknown"
    target_name: str = ""
    evidence_cost: str = "unknown"
    confidence: float = 0.0
    top3: list[BaselineHypothesisCandidate] = field(default_factory=list)
    reasoning: str = ""


@dataclass
class BaselineFinalDecision:
    """表示规范化后的 final 决策。"""

    final_answer: str = ""
    confidence: float = 0.0
    top3: list[BaselineHypothesisCandidate] = field(default_factory=list)
    reasoning: str = ""
    compiled: bool = False


@dataclass
class BaselineDialogueTurn:
    """表示 baseline 会话中的一条对话记录。"""

    role: str = ""
    text: str = ""


@dataclass
class BaselineObservedFeature:
    """表示 baseline 会话中已确认、可用于检索的轻量特征记录。"""

    normalized_name: str = ""
    mention_state: str = "present"
    canonical_name: str = ""
    node_id: str = ""
    label: str = ""
    similarity: float = 0.0
    source_turn: int = 0
    source_kind: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BaselineSessionState:
    """保存单病例纯 LLM 问诊会话状态。"""

    session_id: str
    dialogue_history: list[BaselineDialogueTurn] = field(default_factory=list)
    turn_index: int = 0
    asked_questions: list[str] = field(default_factory=list)
    last_model_top3: list[BaselineHypothesisCandidate] = field(default_factory=list)
    finalized: bool = False
    last_final_report: dict[str, Any] = field(default_factory=dict)
    pending_question_text: str = ""
    pending_target_name: str = ""
    pending_question_group: str = "unknown"
    observed_features: list[BaselineObservedFeature] = field(default_factory=list)
