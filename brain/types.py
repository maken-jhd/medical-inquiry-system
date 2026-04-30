"""定义问诊大脑阶段二使用的核心数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


MentionState = Literal["present", "absent", "unclear"]
SlotTruthValue = Literal["true", "false", "unknown"]
Resolution = Literal["clear", "hedged", "unknown"]
EvidenceExistence = Literal["exist", "non_exist", "unknown"]
ReasoningStage = Literal["A1", "A2", "A3", "A4", "STOP", "FALLBACK"]
ExamKind = Literal["general", "lab", "imaging", "pathogen"]
ExamAvailability = Literal["unknown", "done", "not_done"]
A1SelectionDecision = Literal["selected", "none_salient"]


def default_exam_context() -> Dict[str, "ExamContextState"]:
    """为每个新会话初始化统一检查入口和内部三类检查上下文状态。"""

    return {
        "general": ExamContextState(exam_kind="general"),
        "lab": ExamContextState(exam_kind="lab"),
        "imaging": ExamContextState(exam_kind="imaging"),
        "pathogen": ExamContextState(exam_kind="pathogen"),
    }


@dataclass
class PatientGeneralInfo:
    """表示患者一般信息 P。"""

    age: Optional[int] = None
    sex: Optional[str] = None
    pregnancy_status: Optional[str] = None
    past_history: List[str] = field(default_factory=list)
    epidemiology: List[str] = field(default_factory=list)


@dataclass
class ClinicalFeatureItem:
    """表示 MedExtractor 输出的一条患者提及项。"""

    name: str
    normalized_name: str
    category: str
    mention_state: MentionState = "present"
    evidence_text: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PatientContext:
    """表示 MedExtractor 输出的结构化患者上下文。"""

    general_info: PatientGeneralInfo = field(default_factory=PatientGeneralInfo)
    clinical_features: List[ClinicalFeatureItem] = field(default_factory=list)
    raw_text: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LinkedEntity:
    """表示提及实体和图谱节点之间的链接结果。"""

    mention: str
    node_id: Optional[str] = None
    canonical_name: Optional[str] = None
    similarity: float = 0.0
    is_trusted: bool = False
    label: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SlotState:
    """表示单个槽位在当前会话中的状态。"""

    node_id: str
    status: SlotTruthValue = "unknown"
    resolution: Resolution = "unknown"
    value: Optional[Any] = None
    evidence: List[str] = field(default_factory=list)
    source_turns: List[int] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidenceState:
    """表示单个证据节点在演绎分析后的状态。"""

    node_id: str
    existence: EvidenceExistence = "unknown"
    resolution: Resolution = "unknown"
    reasoning: str = ""
    source_turns: List[int] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExamMentionedResult:
    """表示患者在检查上下文回答中提到的一条检查结果。"""

    test_name: str = ""
    raw_text: str = ""
    normalized_result: str = "unknown"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExamContextState:
    """表示某一类检查信息在当前会话中是否已知。"""

    exam_kind: ExamKind
    availability: ExamAvailability = "unknown"
    mentioned_exam_names: List[str] = field(default_factory=list)
    mentioned_exam_results: List[ExamMentionedResult] = field(default_factory=list)
    source_turns: List[int] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExamContextResult:
    """表示 collect_exam_context 动作对应回答的解析结果。"""

    exam_kind: ExamKind
    availability: ExamAvailability = "unknown"
    mentioned_tests: List[str] = field(default_factory=list)
    mentioned_results: List[ExamMentionedResult] = field(default_factory=list)
    needs_followup: bool = False
    followup_reason: str = ""
    reasoning: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HypothesisScore:
    """表示某个候选疾病或阶段的当前得分。"""

    node_id: str
    label: str
    name: str
    score: float
    evidence_node_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionStats:
    """表示某个搜索动作在 MCTS 中的访问统计。"""

    action_id: str
    visit_count: int = 0
    total_value: float = 0.0
    average_value: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StateVisitStats:
    """表示某个状态签名在搜索过程中的访问统计。"""

    state_signature: str
    visit_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class KeyFeature:
    """表示 A1 阶段选出的首轮检索核心线索。"""

    name: str
    normalized_name: str
    category: str = "symptom"
    reasoning: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class A1ExtractionResult:
    """表示 A1 核心症状提取阶段的输出。"""

    key_features: List[KeyFeature] = field(default_factory=list)
    selection_decision: A1SelectionDecision = "selected"
    reasoning: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HypothesisCandidate:
    """表示 A2 阶段产生的单个候选假设。"""

    node_id: str
    name: str
    label: str = "Disease"
    score: float = 0.0
    reasoning: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class A2HypothesisResult:
    """表示 A2 假设生成阶段的输出。"""

    primary_hypothesis: Optional[HypothesisCandidate] = None
    alternatives: List[HypothesisCandidate] = field(default_factory=list)
    reasoning: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class QuestionCandidate:
    """表示一个可被选作下一问的候选节点。"""

    node_id: str
    label: str
    name: str
    topic_id: Optional[str] = None
    priority: float = 0.0
    information_gain: float = 0.0
    graph_weight: float = 0.0
    red_flag_score: float = 0.0
    asked_before: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MctsAction:
    """表示可供 UCT 选择的候选动作。"""

    action_id: str
    action_type: str
    target_node_id: str
    target_node_label: str
    target_node_name: str
    hypothesis_id: Optional[str] = None
    topic_id: Optional[str] = None
    prior_score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class A3VerificationResult:
    """表示 A3 证据验证阶段的输出。"""

    relevant_symptom: Optional[MctsAction] = None
    question_text: str = ""
    reasoning: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SimulationOutcome:
    """表示对某个候选动作进行局部 simulation 的结果。"""

    action_id: str
    expected_reward: float = 0.0
    positive_branch_reward: float = 0.0
    negative_branch_reward: float = 0.0
    depth: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TreeNode:
    """表示搜索树中的单个节点。"""

    node_id: str
    state_signature: str
    parent_id: Optional[str]
    action_from_parent: Optional[str]
    stage: str
    depth: int
    children_ids: List[str] = field(default_factory=list)
    visit_count: int = 0
    total_value: float = 0.0
    average_value: float = 0.0
    terminal: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReasoningTrajectory:
    """表示一次 rollout 或交互式诊断形成的轨迹。"""

    trajectory_id: str
    final_answer_id: Optional[str] = None
    final_answer_name: Optional[str] = None
    steps: List[Dict[str, Any]] = field(default_factory=list)
    score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FinalAnswerScore:
    """表示某个最终答案分组的聚合评分。"""

    answer_id: str
    answer_name: str
    consistency: float
    diversity: float
    agent_evaluation: float
    final_score: float
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionState:
    """表示一次完整问诊会话的全局状态。"""

    session_id: str
    turn_index: int = 0
    active_topics: List[str] = field(default_factory=list)
    slots: Dict[str, SlotState] = field(default_factory=dict)
    evidence_states: Dict[str, EvidenceState] = field(default_factory=dict)
    exam_context: Dict[str, ExamContextState] = field(default_factory=default_exam_context)
    candidate_hypotheses: List[HypothesisScore] = field(default_factory=list)
    asked_node_ids: List[str] = field(default_factory=list)
    action_stats: Dict[str, ActionStats] = field(default_factory=dict)
    state_visit_stats: Dict[str, StateVisitStats] = field(default_factory=dict)
    trajectories: List[ReasoningTrajectory] = field(default_factory=list)
    fail_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SlotUpdate:
    """表示一次用户回答触发的槽位更新。"""

    node_id: str
    status: SlotTruthValue
    resolution: Resolution = "unknown"
    value: Optional[Any] = None
    evidence: Optional[str] = None
    turn_index: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class A4DeductiveResult:
    """表示 A4 演绎分析阶段的结构化判断结果。"""

    existence: EvidenceExistence = "unknown"
    resolution: Resolution = "unknown"
    reasoning: str = ""
    supporting_span: str = ""
    negation_span: str = ""
    uncertain_span: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DeductiveDecision:
    """表示 A4 后用于驱动代码路由的诊断决策。"""

    existence: EvidenceExistence = "unknown"
    resolution: Resolution = "unknown"
    decision_type: Literal[
        "confirm_hypothesis",
        "exclude_hypothesis",
        "reverify_hypothesis",
        "switch_hypothesis",
        "need_more_information",
    ] = "need_more_information"
    contradiction_explanation: str = ""
    diagnostic_rationale: str = ""
    next_stage: ReasoningStage = "A3"
    should_terminate_current_path: bool = False
    should_spawn_alternative_hypotheses: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RouteDecision:
    """表示当前状态下下一步应进入的推理阶段。"""

    stage: ReasoningStage
    reason: str
    next_topic_id: Optional[str] = None
    next_hypothesis_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StopDecision:
    """表示问诊是否终止以及终止原因。"""

    should_stop: bool
    reason: str
    confidence: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    """表示一次局部搜索返回的动作与答案评分结果。"""

    selected_action: Optional[MctsAction] = None
    root_best_action: Optional[MctsAction] = None
    repair_selected_action: Optional[MctsAction] = None
    trajectories: List[ReasoningTrajectory] = field(default_factory=list)
    final_answer_scores: List[FinalAnswerScore] = field(default_factory=list)
    best_answer_id: Optional[str] = None
    best_answer_name: Optional[str] = None
    verifier_repair_context: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
