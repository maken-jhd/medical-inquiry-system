"""定义问诊阶段输出、轨迹聚合与停止判定相关的数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from .runtime import (
    A1SelectionDecision,
    ExamAvailability,
    ExamKind,
    ExamMentionedResult,
    KeyFeature,
    MctsAction,
    MentionItem,
    MentionPolarity,
    ReasoningStage,
    Resolution,
)


@dataclass
class TurnInterpretationResult:
    """表示统一提及抽取器对单轮患者输入的解析结果。"""

    # 当前轮抽取出的统一提及项列表。
    mentions: List[MentionItem] = field(default_factory=list)
    # 对本轮解释过程的简短说明。
    reasoning: str = ""
    # 解释来源、规则命中和异常兜底信息。
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExamContextResult:
    """表示 collect_exam_context 动作对应回答的解析结果。"""

    # 这条回答对应的检查大类。
    exam_kind: ExamKind
    # 患者是否做过该类检查。
    availability: ExamAvailability = "unknown"
    # 回答中提到的检查名称。
    mentioned_tests: List[str] = field(default_factory=list)
    # 回答中提到的结构化检查结果。
    mentioned_results: List[ExamMentionedResult] = field(default_factory=list)
    # 当前是否还需要继续追问具体检查名或结果。
    needs_followup: bool = False
    # 需要继续追问的原因标签。
    followup_reason: str = ""
    # 对这次 exam context 解析的简短解释。
    reasoning: str = ""
    # 原始输入、候选证据数量、解析来源等附加信息。
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class A1ExtractionResult:
    """表示 A1 核心症状提取阶段的输出。"""

    # A1 认为值得进入首轮检索的关键线索列表。
    key_features: List[KeyFeature] = field(default_factory=list)
    # 本轮是否成功选出显著线索。
    selection_decision: A1SelectionDecision = "selected"
    # A1 选择这些线索的解释。
    reasoning: str = ""
    # A1 阶段附加元数据。
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class A2HypothesisResult:
    """表示 A2 假设生成阶段的输出。"""

    # 当前排名第一的主假设。
    primary_hypothesis: Optional["HypothesisCandidate"] = None
    # 主假设之外的强备选候选。
    alternatives: List["HypothesisCandidate"] = field(default_factory=list)
    # A2 排序或重排的解释。
    reasoning: str = ""
    # A2 阶段附加信息，如 observed anchor rerank 标记。
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class A3VerificationResult:
    """表示 A3 证据验证阶段的输出。"""

    # 当前选中的验证动作。
    relevant_symptom: Optional[MctsAction] = None
    # 面向患者展示的下一问文本。
    question_text: str = ""
    # 为什么选择这条动作继续问。
    reasoning: str = ""
    # A3 相关附加信息。
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReasoningTrajectory:
    """表示一次 rollout 或交互式诊断形成的轨迹。"""

    # 轨迹唯一 ID。
    trajectory_id: str
    # 该轨迹最终收敛到的答案 ID。
    final_answer_id: Optional[str] = None
    # 该轨迹最终收敛到的答案名称。
    final_answer_name: Optional[str] = None
    # 轨迹中每一步的动作/回答/路由记录。
    steps: List[Dict[str, Any]] = field(default_factory=list)
    # 该轨迹的总分。
    score: float = 0.0
    # 轨迹附加信息，如 rollout_depth、branch_seed、是否终止。
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FinalAnswerScore:
    """表示某个最终答案分组的聚合评分。"""

    # 最终答案组的 ID。
    answer_id: str
    # 最终答案组的名称。
    answer_name: str
    # 该答案组在所有轨迹中的一致性得分。
    consistency: float
    # 该答案组内部路径多样性得分。
    diversity: float
    # verifier / observed evidence 视角下的代理评分。
    agent_evaluation: float
    # 最终综合分数。
    final_score: float
    # 评分明细元数据。
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PendingActionResult:
    """表示系统对“上一轮待处理动作回答”的统一解释结果。"""

    # 上一轮待处理动作的类型。
    action_type: str = ""
    # 上一轮动作指向的目标节点 ID。
    target_node_id: str = ""
    # 上一轮动作指向的目标节点名称。
    target_node_name: str = ""
    # 当前回答对该目标的极性判断。
    polarity: MentionPolarity = "unclear"
    # 当前判断是否清晰明确。
    resolution: Resolution = "unknown"
    # 统一解释后的简短推理说明。
    reasoning: str = ""
    # 支持“存在”的证据片段。
    supporting_span: str = ""
    # 支持“否定”的证据片段。
    negation_span: str = ""
    # 支持“不确定”的证据片段。
    uncertain_span: str = ""
    # 解释附加信息，如 direct_reply 标记、来源 prompt。
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PendingActionDecision:
    """表示基于上一轮动作解释结果生成的代码级路由决策。"""

    # 对上一轮目标证据的最终极性判断。
    polarity: MentionPolarity = "unclear"
    # 判断清晰度。
    resolution: Resolution = "unknown"
    # 程序级决策类型，如确认主假设、排除主假设、继续复核。
    decision_type: Literal[
        "confirm_hypothesis",
        "exclude_hypothesis",
        "reverify_hypothesis",
        "switch_hypothesis",
        "need_more_information",
    ] = "need_more_information"
    # 若存在矛盾，这里保存给调试和 repair 用的矛盾解释。
    contradiction_explanation: str = ""
    # 这次路由的诊断学理由。
    diagnostic_rationale: str = ""
    # 下一步应进入的推理阶段。
    next_stage: ReasoningStage = "A3"
    # 当前路径是否应在局部意义上终止。
    should_terminate_current_path: bool = False
    # 是否应同时考虑生成其他备选假设。
    should_spawn_alternative_hypotheses: bool = False
    # 路由附加元数据，如 next_topic_id、contradicted_feature。
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RouteDecision:
    """表示当前状态下下一步应进入的推理阶段。"""

    # 下一步阶段名称。
    stage: ReasoningStage
    # 为什么切到这个阶段。
    reason: str
    # 下一步建议追问的 topic ID。
    next_topic_id: Optional[str] = None
    # 下一步主要围绕的 hypothesis ID。
    next_hypothesis_id: Optional[str] = None
    # 其他阶段切换附加信息。
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StopDecision:
    """表示问诊是否终止以及终止原因。"""

    # 当前是否允许停止问诊。
    should_stop: bool
    # 停止或拒停的原因标签。
    reason: str
    # 停止判断的置信度或代理分。
    confidence: float = 0.0
    # 终止判定附加信息，如 verifier mode、repair reason。
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    """表示一次局部搜索返回的动作与答案评分结果。"""

    # 本轮最终选中的下一问动作。
    selected_action: Optional[MctsAction] = None
    # search root policy 原本最看好的动作。
    root_best_action: Optional[MctsAction] = None
    # verifier repair 后额外挑出的动作。
    repair_selected_action: Optional[MctsAction] = None
    # 本轮 rollout 产出的全部轨迹。
    trajectories: List[ReasoningTrajectory] = field(default_factory=list)
    # 按答案组聚合后的最终评分结果。
    final_answer_scores: List[FinalAnswerScore] = field(default_factory=list)
    # 当前 best answer 的 ID。
    best_answer_id: Optional[str] = None
    # 当前 best answer 的名称。
    best_answer_name: Optional[str] = None
    # verifier 拒停后构造出的 repair 上下文。
    verifier_repair_context: Dict[str, Any] = field(default_factory=dict)
    # search 层附加元数据，如 fallback reason、selected_action_source。
    metadata: Dict[str, Any] = field(default_factory=dict)


from .runtime import HypothesisCandidate  # noqa: E402
