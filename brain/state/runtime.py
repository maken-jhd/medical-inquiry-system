"""定义问诊运行期会话、动作与图谱链接相关的数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


MentionState = Literal["present", "absent", "unclear"]
MentionPolarity = MentionState
SlotTruthValue = Literal["true", "false", "unknown"]
Resolution = Literal["clear", "hedged", "unknown"]
EvidenceExistence = Literal["exist", "non_exist", "unknown"]
ReasoningStage = Literal["A1", "A2", "A3", "STOP", "FALLBACK"]
ExamKind = Literal["general", "lab", "imaging", "pathogen"]
ExamAvailability = Literal["unknown", "done", "not_done"]
A1SelectionDecision = Literal["selected", "none_salient"]


@dataclass
class PatientGeneralInfo:
    """表示患者一般信息 P。"""

    # 年龄；未提及时为空。
    age: Optional[int] = None
    # 性别；通常来自病人自述或病例先验。
    sex: Optional[str] = None
    # 妊娠状态；仅在需要区分特殊人群时使用。
    pregnancy_status: Optional[str] = None
    # 既往史列表，如慢病、机会性感染史、长期治疗史。
    past_history: List[str] = field(default_factory=list)
    # 流行病学/暴露史列表，如接触史、旅行史、性行为风险等。
    epidemiology: List[str] = field(default_factory=list)


@dataclass
class ClinicalFeatureItem:
    """表示统一提及抽取器输出的一条患者提及项。"""

    # 患者原话中可展示的提及名称。
    name: str
    # 归一化后的标准名称；后续 entity linker、A1/A2 都优先用它。
    normalized_name: str
    # 特征类别，如 symptom / risk / lab / imaging / detail。
    category: str = ""
    # 患者对该特征的表述极性：存在 / 不存在 / 不确定。
    mention_state: MentionState = "present"
    # 支撑该提及项的原始证据片段。
    evidence_text: str = ""
    # 若已和图谱节点对齐，这里保存对应节点 ID。
    node_id: Optional[str] = None
    # 附加调试信息，如链接结果、抽取来源、规则命中原因。
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def polarity(self) -> MentionPolarity:
        return self.mention_state

    @polarity.setter
    def polarity(self, value: MentionPolarity) -> None:
        self.mention_state = value


MentionItem = ClinicalFeatureItem


@dataclass
class PatientContext:
    """表示当前轮患者输入的统一结构化上下文。"""

    # 患者一般信息视图。
    general_info: PatientGeneralInfo = field(default_factory=PatientGeneralInfo)
    # 当前轮从患者输入中解析出的提及项列表。
    clinical_features: List[ClinicalFeatureItem] = field(default_factory=list)
    # 当前轮患者原始输入文本。
    raw_text: str = ""
    # 当前轮上下文的附加元信息，如来源、累积证据摘要。
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def mentions(self) -> List[MentionItem]:
        return self.clinical_features


@dataclass
class LinkedEntity:
    """表示提及实体和图谱节点之间的链接结果。"""

    # 原始提及或用于链接的文本。
    mention: str
    # 命中的图谱节点 ID；未命中时为空。
    node_id: Optional[str] = None
    # 命中的图谱标准名。
    canonical_name: Optional[str] = None
    # 链接相似度分数。
    similarity: float = 0.0
    # 是否达到可信阈值，可直接视为 graph-grounded。
    is_trusted: bool = False
    # 图谱节点标签，如 Disease、ClinicalFinding。
    label: Optional[str] = None
    # 链接过程的附加信息，如来源、候选列表、阈值判断。
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SlotState:
    """表示单个槽位在当前会话中的状态。"""

    # 槽位对应的图谱节点 ID。
    node_id: str
    # 兼容布尔槽位的 truth 状态：true / false / unknown。
    status: SlotTruthValue = "unknown"
    # 与患者语言表述对齐的极性：present / absent / unclear。
    polarity: MentionPolarity = "unclear"
    # 当前判断是否清晰明确，还是带保留。
    resolution: Resolution = "unknown"
    # 槽位携带的值，可是文本、数值或结构化结果。
    value: Optional[Any] = None
    # 支撑当前槽位状态的证据文本列表。
    evidence: List[str] = field(default_factory=list)
    # 这些证据最早/再次出现于哪些轮次。
    source_turns: List[int] = field(default_factory=list)
    # 附加元数据，如来源阶段、目标节点标签、exam context 映射标记。
    metadata: Dict[str, Any] = field(default_factory=dict)

    def effective_polarity(self) -> MentionPolarity:
        if self.polarity in {"present", "absent"}:
            return self.polarity
        if self.status == "true":
            return "present"
        if self.status == "false":
            return "absent"
        return "unclear"


@dataclass
class EvidenceState:
    """表示单个证据节点在演绎分析后的状态。"""

    # 证据节点对应的图谱 node_id。
    node_id: str
    # 该证据当前被认为存在、不存在还是不确定。
    polarity: MentionPolarity = "unclear"
    # 兼容旧链路的 exist / non_exist / unknown 表示。
    existence: EvidenceExistence = "unknown"
    # 判断清晰度：clear / hedged / unknown。
    resolution: Resolution = "unknown"
    # 系统对该证据状态的简短推理说明。
    reasoning: str = ""
    # 该证据状态由哪些轮次的回答推动形成。
    source_turns: List[int] = field(default_factory=list)
    # 富化元数据，如 hypothesis_id、relation_type、evidence_tags。
    metadata: Dict[str, Any] = field(default_factory=dict)

    def effective_polarity(self) -> MentionPolarity:
        if self.polarity in {"present", "absent"}:
            return self.polarity
        if self.existence == "exist":
            return "present"
        if self.existence == "non_exist":
            return "absent"
        return "unclear"


@dataclass
class MentionContextItem:
    """表示会话级合并后的提及项上下文。"""

    # 合并后的标准提及名，作为会话级 key 使用。
    normalized_name: str
    # 若已链接，则对应图谱节点 ID。
    node_id: Optional[str] = None
    # 更适合展示给人看的名称。
    display_name: str = ""
    # 会话级别下该提及的综合极性。
    polarity: MentionPolarity = "unclear"
    # 支持这一提及的所有证据片段。
    evidence: List[str] = field(default_factory=list)
    # 该提及在会话中出现过的轮次。
    source_turns: List[int] = field(default_factory=list)
    # 其他合并过程中保留下来的附加信息。
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExamMentionedResult:
    """表示患者在检查上下文回答中提到的一条检查结果。"""

    # 提到的检查名称，如“胸部CT”“CD4”。
    test_name: str = ""
    # 患者原话中的结果片段。
    raw_text: str = ""
    # 归一化后的结果类别，如 positive / negative / high / low / unknown。
    normalized_result: str = "unknown"
    # 该结果的附加解释信息。
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExamContextState:
    """表示某一类检查信息在当前会话中是否已知。"""

    # 检查大类：general / lab / imaging / pathogen。
    exam_kind: ExamKind
    # 对这类检查是否做过的当前认知。
    availability: ExamAvailability = "unknown"
    # 患者明确提到做过的检查名称列表。
    mentioned_exam_names: List[str] = field(default_factory=list)
    # 患者明确提到的检查结果列表。
    mentioned_exam_results: List[ExamMentionedResult] = field(default_factory=list)
    # 这些检查信息出现于哪些轮次。
    source_turns: List[int] = field(default_factory=list)
    # follow-up 原因、解释来源等附加信息。
    metadata: Dict[str, Any] = field(default_factory=dict)


def default_exam_context() -> Dict[str, ExamContextState]:
    """为每个新会话初始化统一检查入口和内部三类检查上下文状态。"""

    return {
        "general": ExamContextState(exam_kind="general"),
        "lab": ExamContextState(exam_kind="lab"),
        "imaging": ExamContextState(exam_kind="imaging"),
        "pathogen": ExamContextState(exam_kind="pathogen"),
    }


@dataclass
class HypothesisScore:
    """表示某个候选疾病或阶段的当前得分。"""

    # 候选疾病/阶段的图谱节点 ID。
    node_id: str
    # 节点标签，如 Disease、DiseasePhase。
    label: str
    # 候选名称。
    name: str
    # 当前综合分数，供排序和 root action 选择使用。
    score: float
    # 当前候选已关联到的关键证据节点 ID 列表。
    evidence_node_ids: List[str] = field(default_factory=list)
    # 候选调试信息，如 raw_score、anchor、repair、recommended evidence。
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionStats:
    """表示某个搜索动作在 MCTS 中的访问统计。"""

    # 动作唯一 ID。
    action_id: str
    # 该动作在真实搜索/反馈中累计被访问的次数。
    visit_count: int = 0
    # 奖励累计值。
    total_value: float = 0.0
    # 平均奖励值。
    average_value: float = 0.0
    # 其他统计附加信息。
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StateVisitStats:
    """表示某个状态签名在搜索过程中的访问统计。"""

    # belief state 的签名字符串。
    state_signature: str
    # 该状态签名被访问的累计次数。
    visit_count: int = 0
    # 调试元数据，如叶子节点、rollout 序号。
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class KeyFeature:
    """表示 A1 阶段选出的首轮检索核心线索。"""

    # 核心线索的展示名。
    name: str
    # 归一化后的标准名称。
    normalized_name: str
    # 线索类别，通常是 symptom / risk / exam 等。
    category: str = "symptom"
    # 这条线索为何被选入 A1 的解释。
    reasoning: str = ""
    # 附加来源信息。
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HypothesisCandidate:
    """表示 A2 阶段产生的单个候选假设。"""

    # 候选图谱节点 ID。
    node_id: str
    # 候选名称。
    name: str
    # 候选标签，默认是 Disease。
    label: str = "Disease"
    # R1/R2/A2 阶段赋予它的当前得分。
    score: float = 0.0
    # 为什么把它作为候选召回的解释。
    reasoning: str = ""
    # 候选附加信息，如匹配证据、semantic_score、rescue 来源。
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class QuestionCandidate:
    """表示一个可被选作下一问的候选节点。"""

    # 候选问题目标节点 ID。
    node_id: str
    # 目标节点标签。
    label: str
    # 目标节点名称。
    name: str
    # 该问题隶属的主题或候选诊断 ID。
    topic_id: Optional[str] = None
    # 候选问题的整体优先级。
    priority: float = 0.0
    # 估计的信息增益。
    information_gain: float = 0.0
    # 节点本身在图谱中的权重。
    graph_weight: float = 0.0
    # 是否属于危险信号相关问题。
    red_flag_score: float = 0.0
    # 这个问题是否曾经问过。
    asked_before: bool = False
    # 其他问题构造元数据。
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MctsAction:
    """表示可供 UCT 选择的候选动作。"""

    # 动作唯一 ID，通常编码了 hypothesis 和 target node。
    action_id: str
    # 动作类型，如 verify_evidence、collect_exam_context。
    action_type: str
    # 动作指向的目标节点 ID。
    target_node_id: str
    # 目标节点标签。
    target_node_label: str
    # 目标节点名称，常用于生成问题文本。
    target_node_name: str
    # 该动作主要服务的候选假设 ID。
    hypothesis_id: Optional[str] = None
    # 当前动作绑定的主题 ID。
    topic_id: Optional[str] = None
    # 进入 root policy / UCT 前的先验分数。
    prior_score: float = 0.0
    # 动作附加信息，如 relation_type、question_type_hint、answerability。
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SimulationOutcome:
    """表示对某个候选动作进行局部 simulation 的结果。"""

    # 被模拟的动作 ID。
    action_id: str
    # 该动作在本地 rollout 中的期望奖励。
    expected_reward: float = 0.0
    # 假设得到正向回答时的分支奖励。
    positive_branch_reward: float = 0.0
    # 假设得到负向回答时的分支奖励。
    negative_branch_reward: float = 0.0
    # 本次 simulation 推进到的深度。
    depth: int = 0
    # 其他模拟过程信息。
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TreeNode:
    """表示搜索树中的单个节点。"""

    # 搜索树节点唯一 ID。
    node_id: str
    # 该节点对应的 belief state 签名，用于树复用与 reroot。
    state_signature: str
    # 父节点 ID；根节点为空。
    parent_id: Optional[str]
    # 从父节点走到当前节点所对应的动作 ID。
    action_from_parent: Optional[str]
    # 当前节点所处阶段，如 A3。
    stage: str
    # 当前节点在树中的深度。
    depth: int
    # 子节点 ID 列表。
    children_ids: List[str] = field(default_factory=list)
    # 累计访问次数。
    visit_count: int = 0
    # 累计回传价值。
    total_value: float = 0.0
    # 平均价值。
    average_value: float = 0.0
    # 该节点是否已被判定为 terminal。
    terminal: bool = False
    # 节点元数据，如动作对象、prior、rollout 摘要。
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionState:
    """表示一次完整问诊会话的全局状态。"""

    # 会话唯一 ID。
    session_id: str
    # 当前进行到第几轮患者输入。
    turn_index: int = 0
    # 当前仍在活跃追问的主题列表。
    active_topics: List[str] = field(default_factory=list)
    # 会话级提及项记忆，记录患者总体说过什么。
    mention_context: Dict[str, MentionContextItem] = field(default_factory=dict)
    # 当前槽位状态表。
    slots: Dict[str, SlotState] = field(default_factory=dict)
    # 当前图谱证据状态表。
    evidence_states: Dict[str, EvidenceState] = field(default_factory=dict)
    # 各类检查上下文状态。
    exam_context: Dict[str, ExamContextState] = field(default_factory=default_exam_context)
    # 当前候选假设排序结果。
    candidate_hypotheses: List[HypothesisScore] = field(default_factory=list)
    # 已经问过的 target node ID 列表，用于去重。
    asked_node_ids: List[str] = field(default_factory=list)
    # 真实会话中动作反馈统计。
    action_stats: Dict[str, ActionStats] = field(default_factory=dict)
    # 搜索状态访问统计。
    state_visit_stats: Dict[str, StateVisitStats] = field(default_factory=dict)
    # 已保存的 rollout / reasoning 轨迹。
    trajectories: List["ReasoningTrajectory"] = field(default_factory=list)
    # 连续失败/无效轮次计数，供 fallback 使用。
    fail_count: int = 0
    # 会话附加元数据，如 pending_action、search_tree、repair context。
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SlotUpdate:
    """表示一次用户回答触发的槽位更新。"""

    # 被更新的槽位节点 ID。
    node_id: str
    # 兼容布尔槽位的结果值。
    status: SlotTruthValue
    # 这次更新对应的语言极性。
    polarity: MentionPolarity = "unclear"
    # 这次更新的清晰度。
    resolution: Resolution = "unknown"
    # 槽位写入的具体值。
    value: Optional[Any] = None
    # 直接支撑这次更新的证据文本。
    evidence: Optional[str] = None
    # 这次更新发生在哪一轮。
    turn_index: Optional[int] = None
    # 来源阶段、目标标签等附加元数据。
    metadata: Dict[str, Any] = field(default_factory=dict)
