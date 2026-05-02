"""编排患者上下文提取、统一提及推理、图谱检索与局部树搜索。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

import yaml

from .action_builder import ActionBuilder, ActionBuilderConfig
from .entity_linker import EntityLinker, EntityLinkerConfig
from .evidence_parser import EvidenceParser, EvidenceParserConfig
from .errors import LlmUnavailableError
from .hypothesis_manager import HypothesisManager, HypothesisManagerConfig
from .llm_client import LlmClient
from .mcts_engine import MctsConfig, MctsEngine
from .med_extractor import MedExtractor
from .neo4j_client import Neo4jClient
from .question_selector import QuestionSelector
from .report_builder import ReportBuilder
from .retriever import GraphRetriever, RetrievalConfig
from .router import ReasoningRouter, RouterConfig
from .search_tree import SearchTree
from .simulation_engine import SimulationConfig, SimulationEngine
from .state_tracker import StateTracker
from .stop_rules import (
    GUARDED_CONFIRMED_EVIDENCE_TAGS,
    GUARDED_DEFINITION_RELATION_TYPES,
    StopRuleConfig,
    StopRuleEngine,
)
from .trajectory_evaluator import TrajectoryEvaluator, TrajectoryEvaluatorConfig
from .types import (
    A1ExtractionResult,
    A2HypothesisResult,
    A3VerificationResult,
    ClinicalFeatureItem,
    ExamContextResult,
    ExamMentionedResult,
    EvidenceState,
    FinalAnswerScore,
    HypothesisCandidate,
    HypothesisScore,
    LinkedEntity,
    MctsAction,
    PatientContext,
    PendingActionDecision,
    PendingActionResult,
    ReasoningTrajectory,
    SearchResult,
    SessionState,
    SlotUpdate,
    StopDecision,
    TreeNode,
    RouteDecision,
    TurnInterpretationResult,
)


@dataclass
class BrainDependencies:
    """集中管理问诊大脑运行所需的核心组件。"""

    state_tracker: StateTracker
    retriever: GraphRetriever
    med_extractor: MedExtractor | None = None
    entity_linker: EntityLinker | None = None
    question_selector: QuestionSelector | None = None
    stop_rule_engine: StopRuleEngine | None = None
    report_builder: ReportBuilder | None = None
    evidence_parser: EvidenceParser | None = None
    hypothesis_manager: HypothesisManager | None = None
    action_builder: ActionBuilder | None = None
    router: ReasoningRouter | None = None
    mcts_engine: MctsEngine | None = None
    simulation_engine: SimulationEngine | None = None
    trajectory_evaluator: TrajectoryEvaluator | None = None
    llm_client: LlmClient | None = None
    repair_policy: "RepairPolicyConfig" = field(default_factory=lambda: RepairPolicyConfig())


@dataclass
class RepairPolicyConfig:
    """控制 verifier repair 与 reroot 的开关，便于做小规模 ablation。"""

    enable_verifier_hypothesis_reshuffle: bool = True
    enable_best_repair_action: bool = True
    enable_tree_reroot: bool = True


class ConsultationBrain:
    """阶段二问诊大脑的高层编排入口。"""

    # 初始化问诊大脑所需的依赖组件，并补齐未显式传入的默认对象。
    def __init__(self, deps: BrainDependencies) -> None:
        if deps.llm_client is None:
            deps.llm_client = LlmClient()
        if deps.med_extractor is None:
            deps.med_extractor = MedExtractor(deps.llm_client)
        if deps.entity_linker is None:
            deps.entity_linker = EntityLinker(deps.retriever.client)
        if deps.question_selector is None:
            deps.question_selector = QuestionSelector()
        if deps.stop_rule_engine is None:
            deps.stop_rule_engine = StopRuleEngine()
        if deps.report_builder is None:
            deps.report_builder = ReportBuilder()
        if deps.evidence_parser is None:
            deps.evidence_parser = EvidenceParser(deps.llm_client)
        if deps.hypothesis_manager is None:
            deps.hypothesis_manager = HypothesisManager(deps.llm_client)
        if deps.action_builder is None:
            deps.action_builder = ActionBuilder()
        if deps.router is None:
            deps.router = ReasoningRouter()
        if deps.mcts_engine is None:
            deps.mcts_engine = MctsEngine()
        if deps.simulation_engine is None:
            deps.simulation_engine = SimulationEngine()
        if deps.trajectory_evaluator is None:
            deps.trajectory_evaluator = TrajectoryEvaluator()
        self.deps = deps

    # 创建一条新的问诊会话并返回初始状态。
    def start_session(self, session_id: str) -> SessionState:
        return self.deps.state_tracker.create_session(session_id)

    # 兼容旧接口：批量应用槽位更新并刷新候选假设。
    def apply_updates(self, session_id: str, updates: Iterable[SlotUpdate]) -> SessionState:
        state = self.deps.state_tracker.apply_slot_updates(session_id, updates)
        hypotheses = self.deps.retriever.get_forward_hypotheses(state)
        self.deps.state_tracker.set_candidate_hypotheses(session_id, hypotheses)
        return self.deps.state_tracker.get_session(session_id)

    # 兼容旧接口：优先从最近一次搜索结果中返回下一问。
    def get_next_question(self, session_id: str) -> Optional[str]:
        state = self.deps.state_tracker.get_session(session_id)
        search_result = state.metadata.get("last_search_result")

        if isinstance(search_result, SearchResult) and search_result.selected_action is not None:
            return self.deps.action_builder.render_question_text(search_result.selected_action)

        candidate = self.deps.question_selector.select_next_question(
            self.deps.retriever.get_cold_start_questions(),
            state,
        )

        if candidate is None:
            return None

        return f"我想先了解一下：是否存在“{candidate.name}”相关情况？"

    # 兼容旧接口：根据当前状态输出最终报告。
    def finalize(self, session_id: str) -> dict:
        state = self.deps.state_tracker.get_session(session_id)
        search_result = state.metadata.get("last_search_result")

        if isinstance(search_result, SearchResult):
            best_answer_score = self.deps.trajectory_evaluator.select_best_answer(search_result.final_answer_scores)
            accept_decision = self.deps.stop_rule_engine.should_accept_final_answer(best_answer_score, state)
            return self.deps.report_builder.build_final_reasoning_report(state, accept_decision, search_result)

        stop_decision = self.deps.stop_rule_engine.check_sufficiency(state, state.candidate_hypotheses)
        return self.deps.report_builder.build_final_report(state, stop_decision)

    # 将患者原话抽成论文中的结构化上下文 P/C。
    def ingest_patient_turn(self, session_id: str, patient_text: str) -> PatientContext:
        _ = session_id
        return self.deps.med_extractor.extract_patient_context(patient_text)

    def _prepare_turn_mentions(
        self,
        turn_result: TurnInterpretationResult,
        pending_action: MctsAction | None = None,
    ) -> list[LinkedEntity]:
        if hasattr(self.deps.entity_linker, "link_mention_items"):
            linked_results = self.deps.entity_linker.link_mention_items(turn_result.mentions)
        else:
            linked_results = self.deps.entity_linker.link_clinical_features(turn_result.mentions)
        positive_links: list[LinkedEntity] = []

        for mention, linked in zip(turn_result.mentions, linked_results):
            original_normalized_name = str(mention.normalized_name or "")
            original_display_name = str(mention.name or "")
            mention.metadata.update(
                {
                    "linked_canonical_name": linked.canonical_name,
                    "linked_similarity": linked.similarity,
                    "linked_label": linked.label,
                    "linked_is_trusted": linked.is_trusted,
                    "linked_metadata": dict(linked.metadata),
                }
            )

            if linked.node_id is not None and linked.is_trusted:
                mention.node_id = str(linked.node_id)
                if linked.canonical_name is not None and len(str(linked.canonical_name).strip()) > 0:
                    mention.metadata.update(
                        {
                            "original_name": original_display_name,
                            "original_normalized_name": original_normalized_name,
                            "graph_grounded_node_id": str(linked.node_id),
                            "graph_grounded_canonical_name": str(linked.canonical_name),
                            "graph_grounded_label": linked.label,
                            "graph_grounded_source": linked.metadata.get("link_source"),
                        }
                    )
                    mention.normalized_name = str(linked.canonical_name)

            if pending_action is not None and mention.node_id is None:
                matched = self.deps.evidence_parser.find_target_mention([mention], pending_action)
                if matched is not None:
                    mention.node_id = pending_action.target_node_id

            if mention.polarity == "present":
                positive_links.append(linked)

        return positive_links

    def _build_slot_updates_from_mentions(
        self,
        mentions: Iterable[ClinicalFeatureItem],
        *,
        turn_index: int,
    ) -> list[SlotUpdate]:
        updates: list[SlotUpdate] = []

        for mention in mentions:
            node_id = str(mention.node_id or mention.normalized_name).strip()
            if len(node_id) == 0:
                continue

            updates.append(
                SlotUpdate(
                    node_id=node_id,
                    status=self._polarity_to_slot_status(mention.polarity),
                    polarity=mention.polarity,
                    resolution=self._polarity_to_resolution_compat(mention.polarity),
                    evidence=mention.evidence_text,
                    turn_index=turn_index,
                    metadata={
                        "source_stage": "TURN_INTERPRETER",
                        "normalized_name": mention.normalized_name,
                        "display_name": mention.name,
                        **dict(mention.metadata),
                    },
                )
            )

        return updates

    def _apply_generic_evidence_states_from_mentions(
        self,
        session_id: str,
        mentions: Iterable[ClinicalFeatureItem],
        *,
        turn_index: int,
    ) -> None:
        for mention in mentions:
            node_id = str(mention.node_id or "").strip()
            if len(node_id) == 0:
                continue

            self.deps.state_tracker.set_evidence_state(
                session_id,
                EvidenceState(
                    node_id=node_id,
                    polarity=mention.polarity,
                    existence=self._polarity_to_existence_compat(mention.polarity),
                    resolution=self._polarity_to_resolution_compat(mention.polarity),
                    reasoning=str(mention.metadata.get("reasoning") or ""),
                    source_turns=[turn_index],
                    metadata={
                        "target_node_name": mention.name,
                        "normalized_name": mention.normalized_name,
                        "source_stage": "TURN_INTERPRETER",
                        **dict(mention.metadata),
                    },
                ),
            )

    # 病原体阳性、影像/化验阳性等强证据进入后，强制下一轮重跑 A2 并刷新 search tree。
    def _mark_a2_refresh_if_strong_updates(
        self,
        session_id: str,
        updates: Iterable[SlotUpdate],
        *,
        source: str,
    ) -> None:
        strong_names: list[str] = []

        for update in updates:
            if not self._is_strong_positive_evidence_update(update):
                continue
            strong_names.append(str(update.metadata.get("normalized_name") or update.node_id))

        if len(strong_names) == 0:
            return

        state = self.deps.state_tracker.get_session(session_id)
        state.metadata["force_a2_refresh"] = True
        state.metadata["force_tree_refresh"] = True
        state.metadata["force_a2_refresh_reason"] = "strong_graph_evidence_observed"
        state.metadata["force_a2_refresh_source"] = source
        state.metadata["force_a2_refresh_evidence"] = strong_names[:6]

    # 普通 verify_evidence 动作确认强证据时，也触发 A2 重排。
    def _mark_a2_refresh_if_strong_evidence_state(
        self,
        session_id: str,
        evidence_state: EvidenceState,
        action: MctsAction,
    ) -> None:
        if evidence_state.effective_polarity() != "present":
            return

        label = str(action.target_node_label or "")
        question_type = str(action.metadata.get("question_type_hint") or "")
        acquisition_mode = str(action.metadata.get("acquisition_mode") or "")
        relation_type = str(action.metadata.get("relation_type") or "")

        if (
            label in {"LabFinding", "LabTest", "ImagingFinding", "Pathogen"}
            or question_type in {"lab", "imaging", "pathogen"}
            or acquisition_mode in {"needs_lab_test", "needs_imaging", "needs_pathogen_test"}
            or relation_type in {"DIAGNOSED_BY", "HAS_PATHOGEN", "HAS_LAB_FINDING", "HAS_IMAGING_FINDING"}
        ):
            state = self.deps.state_tracker.get_session(session_id)
            state.metadata["force_a2_refresh"] = True
            state.metadata["force_tree_refresh"] = True
            state.metadata["force_a2_refresh_reason"] = "strong_action_evidence_confirmed"
            state.metadata["force_a2_refresh_source"] = "pending_action"
            state.metadata["force_a2_refresh_evidence"] = [action.target_node_name]

    def _is_strong_positive_evidence_update(self, update: SlotUpdate) -> bool:
        if update.status != "true" and update.polarity != "present":
            return False

        metadata = dict(update.metadata)
        label = str(
            metadata.get("target_node_label")
            or metadata.get("linked_label")
            or metadata.get("graph_grounded_label")
            or ""
        )
        source_exam_kind = str(metadata.get("source_exam_kind") or "")
        source_stage = str(metadata.get("source_stage") or "")
        normalized_name = str(metadata.get("normalized_name") or update.node_id)

        if label in {"LabFinding", "LabTest", "ImagingFinding", "Pathogen"}:
            return True

        if source_exam_kind in {"lab", "imaging", "pathogen"}:
            return True

        if source_stage in {"A4_EXAM_CONTEXT_GENERIC_LINK", "PENDING_ACTION_EXAM_CONTEXT_GENERIC_LINK"}:
            return True

        normalized = self._normalize_match_text(normalized_name)
        return any(keyword in normalized for keyword in ("hivrna", "病毒载量", "cd4", "病原", "pcr", "阳性", "检出"))

    def _polarity_to_slot_status(self, polarity: str) -> str:
        if polarity == "present":
            return "true"
        if polarity == "absent":
            return "false"
        return "unknown"

    def _polarity_to_existence_compat(self, polarity: str) -> str:
        if polarity == "present":
            return "exist"
        if polarity == "absent":
            return "non_exist"
        return "unknown"

    def _polarity_to_resolution_compat(self, polarity: str) -> str:
        if polarity in {"present", "absent"}:
            return "clear"
        return "hedged"

    # 根据上一轮待验证动作更新证据状态、槽位状态和路由决策。
    def update_from_pending_action(
        self,
        session_id: str,
        patient_context: PatientContext,
        patient_text: str,
        turn_index: int,
        turn_result: TurnInterpretationResult | None = None,
    ) -> tuple[PendingActionResult | None, PendingActionDecision | None, RouteDecision | None, list[SlotUpdate]]:
        tracker = self.deps.state_tracker
        pending_action = tracker.get_pending_action(session_id)

        # 没有 pending_action 说明这句患者输入不是在回答上一轮问题，
        # 而是新的自由描述；本轮前半段就不做“上一轮动作消化”。
        if pending_action is None:
            return None, None, None, []

        # collect_chief_complaint 是一个纯 intake 动作：
        # 这轮只需要清掉 pending，并把路由重新拉回 A1。
        if pending_action.action_type == "collect_chief_complaint":
            tracker.get_session(session_id).metadata["last_answered_action"] = pending_action
            tracker.clear_pending_action(session_id)
            return (
                None,
                None,
                RouteDecision(
                    stage="A1",
                    reason="患者已补充主诉信息，重新进入 A1 关键线索提取。",
                    metadata={"source": "chief_complaint_intake"},
                ),
                [],
            )

        if pending_action.action_type in {"collect_exam_context", "collect_general_exam_context"}:
            # 检查上下文动作和普通 verify 动作的解析逻辑不同，单独走专门分支。
            return self._update_from_exam_context_action(
                session_id,
                pending_action,
                patient_text,
                turn_index,
            )

        # 普通 verify 动作直接从统一 mentions 中解释出目标提及项；
        # slot / evidence_state 已在前半段统一写入，这里只做目标节点富化、反馈与路由。
        pending_action_result = (
            self.deps.evidence_parser.derive_pending_action_result(turn_result, pending_action, patient_text)
            if turn_result is not None
            else self.deps.evidence_parser.derive_pending_action_result_from_text(patient_text, pending_action)
        )
        evidence_tags = self._infer_action_evidence_tags(pending_action)
        confirmed_family_candidate = self._is_confirmed_family_candidate(
            pending_action,
            pending_action_result,
            evidence_tags,
        )
        provisional_family_candidate = self._is_provisional_family_candidate(pending_action_result, evidence_tags)
        evidence_state = self._get_or_build_pending_action_evidence_state(
            session_id,
            pending_action,
            pending_action_result,
            turn_index,
        )
        self._enrich_pending_action_evidence_state(
            evidence_state,
            pending_action,
            pending_action_result,
            evidence_tags,
            patient_text,
            confirmed_family_candidate,
            provisional_family_candidate,
        )
        tracker.set_evidence_state(session_id, evidence_state)
        self._record_pending_action_audit(
            session_id,
            pending_action,
            evidence_state,
            pending_action_result,
            patient_text,
            turn_index,
        )

        # 目标节点一旦被统一 mentions 写入并富化完成，就立即反馈到 hypothesis 排名和 action_stats，
        # 这样本轮后续 search 会基于最新诊断竞争态继续推进。
        self._apply_hypothesis_feedback(session_id, pending_action, evidence_state)
        self._mark_a2_refresh_if_strong_evidence_state(session_id, evidence_state, pending_action)
        self._record_action_reward(session_id, pending_action, pending_action_result)
        tracker.get_session(session_id).metadata["last_answered_action"] = pending_action
        tracker.clear_pending_action(session_id)
        updated_state = tracker.get_session(session_id)

        # 最后再基于“已更新后的 hypothesis 排名”做路由，
        # 避免阶段切换还停留在旧的诊断排序上。
        pending_action_decision = self.deps.router.build_pending_action_decision(
            pending_action_result,
            pending_action,
            updated_state,
        )
        route_after_pending_action = self.deps.router.decide_next_stage(
            pending_action_decision,
            updated_state,
        )
        return pending_action_result, pending_action_decision, route_after_pending_action, []

    # 处理 collect_exam_context：更新检查上下文，必要时把结果映射到具体证据节点。
    def _update_from_exam_context_action(
        self,
        session_id: str,
        pending_action: MctsAction,
        patient_text: str,
        turn_index: int,
    ) -> tuple[PendingActionResult | None, PendingActionDecision | None, RouteDecision | None, list[SlotUpdate]]:
        tracker = self.deps.state_tracker

        # exam_context 解析会一次性提取：
        # 是否做过、做了哪些检查、是否说出了结果、是否还需要 follow-up。
        exam_result = self.deps.evidence_parser.interpret_exam_context_answer(patient_text, pending_action)
        previous_exam_signature = self._exam_context_signature(
            tracker.get_session(session_id),
            exam_result.exam_kind,
        )
        exam_updates = self.deps.evidence_parser.build_slot_updates_from_exam_context(
            pending_action,
            exam_result,
            patient_text,
            turn_index=turn_index,
        )
        generic_exam_updates = self._build_graph_linked_exam_context_updates(
            pending_action,
            exam_result,
            patient_text,
            turn_index=turn_index,
            existing_updates=exam_updates,
        )
        all_exam_updates = self._merge_exam_context_updates(exam_updates, generic_exam_updates)

        # 无论是否命中具体证据节点，都先把“检查上下文”写回 session，
        # 因为后续高成本动作是否可继续展开要依赖这份状态。
        tracker.update_exam_context(
            session_id,
            exam_result.exam_kind,
            availability=exam_result.availability,
            mentioned_exam_names=exam_result.mentioned_tests,
            mentioned_exam_results=exam_result.mentioned_results,
            turn_index=turn_index,
            metadata={
                "last_reasoning": exam_result.reasoning,
                "last_followup_reason": exam_result.followup_reason,
            },
        )
        self._sync_general_exam_context_to_specific_kinds(session_id, exam_result, turn_index)

        # 如果这句回答已经给出了具体检查结果，就进一步把它映射成 slot/evidence/hypothesis feedback。
        if len(all_exam_updates) > 0:
            tracker.apply_slot_updates(session_id, all_exam_updates)
        if len(exam_updates) > 0:
            self._apply_exam_context_evidence_feedback(session_id, pending_action, exam_result, exam_updates, turn_index)
        if len(generic_exam_updates) > 0:
            self._apply_generic_exam_context_evidence_feedback(session_id, pending_action, exam_result, generic_exam_updates, turn_index)
        self._mark_a2_refresh_if_strong_updates(
            session_id,
            all_exam_updates,
            source="exam_context",
        )

        # 做过检查但没说清结果时，挂一个专门 follow-up action，下一轮优先追问结果本身。
        tracker.get_session(session_id).metadata.pop("exam_context_followup_action", None)
        if exam_result.needs_followup and exam_result.availability == "done":
            followup_action = self._build_exam_context_followup_action(
                pending_action,
                exam_result,
            )
            if self._should_use_exam_context_followup_action(
                session_id,
                pending_action,
                exam_result,
                followup_action,
                all_exam_updates,
                previous_exam_signature,
            ):
                tracker.get_session(session_id).metadata["exam_context_followup_action"] = followup_action

        # exam_context 也会构造一个轻量 pending_action_result，便于统一复盘“这一轮发生了什么”。
        pending_action_result = self._build_pending_action_result_from_exam_context(pending_action, exam_result)
        tracker.get_session(session_id).metadata["last_exam_context_result"] = asdict(exam_result)
        tracker.get_session(session_id).metadata["last_answered_action"] = pending_action
        tracker.clear_pending_action(session_id)
        route_after_pending_action = RouteDecision(
            stage="A3",
            reason="已解析检查上下文回答，继续根据现有证据执行搜索或澄清。",
            next_topic_id=pending_action.topic_id,
            next_hypothesis_id=pending_action.hypothesis_id,
            metadata={
                "source": "exam_context",
                "exam_kind": exam_result.exam_kind,
                "availability": exam_result.availability,
                "needs_followup": exam_result.needs_followup,
                "followup_reason": exam_result.followup_reason,
            },
        )
        return pending_action_result, None, route_after_pending_action, all_exam_updates

    # 将检查上下文中映射到具体证据节点的结果写入 evidence_states 并反馈 hypothesis。
    def _sync_general_exam_context_to_specific_kinds(
        self,
        session_id: str,
        exam_result: ExamContextResult,
        turn_index: int,
    ) -> None:
        if exam_result.exam_kind != "general":
            return

        tracker = self.deps.state_tracker

        if exam_result.availability == "not_done":
            for exam_kind in ("lab", "imaging", "pathogen"):
                tracker.update_exam_context(
                    session_id,
                    exam_kind,
                    availability="not_done",
                    turn_index=turn_index,
                    metadata={
                        "source": "general_exam_context",
                        "last_reasoning": exam_result.reasoning,
                    },
                )
            return

        if exam_result.availability != "done":
            return

        mentioned_kinds = self._mentioned_exam_kinds_from_result(exam_result)

        for exam_kind in mentioned_kinds:
            tracker.update_exam_context(
                session_id,
                exam_kind,
                availability="done",
                mentioned_exam_names=exam_result.mentioned_tests,
                mentioned_exam_results=exam_result.mentioned_results,
                turn_index=turn_index,
                metadata={
                    "source": "general_exam_context",
                    "last_reasoning": exam_result.reasoning,
                },
            )

    # 读取解析器写入的内部类别映射；缺失时按关键词兜底。
    def _mentioned_exam_kinds_from_result(self, exam_result: ExamContextResult) -> list[str]:
        metadata_kinds = exam_result.metadata.get("mentioned_exam_kinds", [])

        if isinstance(metadata_kinds, list):
            values = [str(item) for item in metadata_kinds if str(item) in {"lab", "imaging", "pathogen"}]

            if len(values) > 0:
                return values

        kinds: set[str] = set()

        for text in list(exam_result.mentioned_tests) + [item.test_name for item in exam_result.mentioned_results] + [
            item.raw_text for item in exam_result.mentioned_results
        ]:
            normalized = self._normalize_match_text(text)

            if any(keyword in normalized for keyword in ("cd4", "hivrna", "病毒载量", "βd葡聚糖", "bdg", "葡聚糖", "pao2", "spo2", "血氧")):
                kinds.add("lab")

            if any(keyword in normalized for keyword in ("ct", "胸片", "x线", "x光", "影像", "磨玻璃")):
                kinds.add("imaging")

            if any(keyword in normalized for keyword in ("pcr", "核酸", "痰", "支气管肺泡", "肺泡灌洗", "bal", "balf", "抗酸", "xpert")):
                kinds.add("pathogen")

        return sorted(kinds)

    # 对检查回答里提到的 test/result 原文再做一次通用实体链接，补上非当前候选里的证据节点。
    def _build_graph_linked_exam_context_updates(
        self,
        pending_action: MctsAction,
        exam_result: ExamContextResult,
        patient_text: str,
        *,
        turn_index: int,
        existing_updates: list[SlotUpdate],
    ) -> list[SlotUpdate]:
        linker = self.deps.entity_linker
        if not hasattr(linker, "link_mentions"):
            return []

        existing_node_ids = {item.node_id for item in existing_updates}
        payloads = self._collect_exam_context_link_payloads(exam_result)
        if len(payloads) == 0:
            return []

        linked_results = linker.link_mentions([item["mention"] for item in payloads])
        updates: list[SlotUpdate] = []
        seen_node_ids: set[str] = set(existing_node_ids)

        for payload, linked in zip(payloads, linked_results):
            if not linked.is_trusted or linked.node_id is None:
                continue
            if linked.label not in {"LabFinding", "ImagingFinding", "Pathogen"}:
                continue

            node_id = str(linked.node_id)
            if node_id in seen_node_ids:
                continue

            status, polarity, resolution = self._exam_link_status_from_payload(payload, linked)
            evidence_text = str(payload.get("evidence") or patient_text)
            update = SlotUpdate(
                node_id=node_id,
                status=status,  # type: ignore[arg-type]
                polarity=polarity,  # type: ignore[arg-type]
                resolution=resolution,  # type: ignore[arg-type]
                value=payload.get("value") or linked.canonical_name,
                evidence=evidence_text,
                turn_index=turn_index,
                metadata={
                    "source_stage": "A4_EXAM_CONTEXT_GENERIC_LINK",
                    "normalized_name": linked.canonical_name or payload["mention"],
                    "display_name": payload["mention"],
                    "source_exam_kind": exam_result.exam_kind,
                    "target_node_label": linked.label,
                    "matched_from_exam_context": True,
                    "generic_exam_context_link": True,
                    "raw_mention": payload["mention"],
                    "source_field": payload.get("source_field"),
                    "link_metadata": dict(linked.metadata),
                    "action_id": pending_action.action_id,
                    "hypothesis_id": pending_action.hypothesis_id,
                },
            )
            updates.append(update)
            seen_node_ids.add(node_id)

        return updates

    # 收集检查回答中的检查名和结果原文，供实体链接器做候选内锚定。
    def _collect_exam_context_link_payloads(self, exam_result: ExamContextResult) -> list[dict]:
        payloads: list[dict] = []
        seen_mentions: set[str] = set()

        def add(mention: str, *, source_field: str, result: ExamMentionedResult | None = None) -> None:
            text = str(mention or "").strip()
            if len(text) == 0 or text in seen_mentions:
                return

            payloads.append(
                {
                    "mention": text,
                    "source_field": source_field,
                    "result": result,
                    "value": result.raw_text if result is not None and result.raw_text else text,
                    "evidence": result.raw_text if result is not None and result.raw_text else text,
                }
            )
            seen_mentions.add(text)

        for test_name in exam_result.mentioned_tests:
            add(test_name, source_field="mentioned_tests")

        for result in exam_result.mentioned_results:
            add(result.test_name, source_field="mentioned_results.test_name", result=result)
            add(result.raw_text, source_field="mentioned_results.raw_text", result=result)

        return payloads

    # 根据检查结果的 positive/negative/high/low 等归一化结果决定 slot 极性。
    def _exam_link_status_from_payload(self, payload: dict, linked: LinkedEntity) -> tuple[str, str, str]:
        result = payload.get("result")
        normalized_result = ""
        raw_text = str(payload.get("evidence") or payload.get("mention") or "")

        if isinstance(result, ExamMentionedResult):
            normalized_result = str(result.normalized_result or "").strip().lower()
            raw_text = f"{result.test_name} {result.raw_text}".strip()

        normalized_raw = self._normalize_match_text(raw_text)
        negative_markers = {"negative", "normal", "not_detected", "not detected", "absent", "阴性", "未检出", "正常"}

        if normalized_result in negative_markers or any(marker in normalized_raw for marker in ("阴性", "未检出", "没有提示")):
            return "false", "absent", "clear"

        positive_markers = {
            "positive",
            "high",
            "low",
            "elevated",
            "detected",
            "abnormal",
            "阳性",
            "升高",
            "降低",
            "偏低",
            "偏高",
        }

        if normalized_result in positive_markers or any(
            marker in normalized_raw
            for marker in ("阳性", "检出", "升高", "降低", "偏低", "偏高", "异常", "低于", "增高")
        ):
            return "true", "present", "clear"

        if linked.label == "Pathogen":
            return "true", "present", "hedged"

        return "true", "present", "hedged"

    # 去重合并候选映射更新和通用实体链接更新。
    def _merge_exam_context_updates(
        self,
        exam_updates: list[SlotUpdate],
        generic_exam_updates: list[SlotUpdate],
    ) -> list[SlotUpdate]:
        merged: list[SlotUpdate] = []
        seen_node_ids: set[str] = set()

        for update in [*exam_updates, *generic_exam_updates]:
            if update.node_id in seen_node_ids:
                continue
            merged.append(update)
            seen_node_ids.add(update.node_id)

        return merged

    # 将检查上下文中映射到具体证据节点的结果写入 evidence_states 并反馈 hypothesis。
    def _apply_exam_context_evidence_feedback(
        self,
        session_id: str,
        pending_action: MctsAction,
        exam_result: ExamContextResult,
        updates: list[SlotUpdate],
        turn_index: int,
    ) -> None:
        state = self.deps.state_tracker.get_session(session_id)

        for update in updates:
            existence = "unknown"
            resolution = "unknown"

            if update.status == "true":
                existence = "exist"
            elif update.status == "false":
                existence = "non_exist"

            if update.resolution == "clear":
                resolution = "clear"
            elif update.resolution == "hedged":
                resolution = "hedged"

            evidence_state = EvidenceState(
                node_id=update.node_id,
                existence=existence,  # type: ignore[arg-type]
                resolution=resolution,  # type: ignore[arg-type]
                reasoning=f"由检查上下文回答映射得到：{update.value or update.evidence or ''}",
                source_turns=[turn_index],
                metadata={
                    "source_stage": "PENDING_ACTION_EXAM_CONTEXT",
                    "action_id": pending_action.action_id,
                    "hypothesis_id": pending_action.hypothesis_id,
                    "exam_kind": exam_result.exam_kind,
                    "exam_availability": exam_result.availability,
                    "target_node_name": update.metadata.get("normalized_name", update.node_id),
                    "patient_answer": update.evidence,
                },
            )
            self.deps.state_tracker.set_evidence_state(session_id, evidence_state)
            related_ids = [pending_action.hypothesis_id] if pending_action.hypothesis_id is not None else None
            state.candidate_hypotheses = self.deps.hypothesis_manager.apply_evidence_feedback(
                state.candidate_hypotheses,
                evidence_state,
                related_ids,
            )

    # 通用 exam-result 链接不绑定某个 R2 候选，但仍写入 evidence_states 供后续检索与复盘消费。
    def _apply_generic_exam_context_evidence_feedback(
        self,
        session_id: str,
        pending_action: MctsAction,
        exam_result: ExamContextResult,
        updates: list[SlotUpdate],
        turn_index: int,
    ) -> None:
        for update in updates:
            existence = "unknown"
            resolution = "unknown"

            if update.status == "true":
                existence = "exist"
            elif update.status == "false":
                existence = "non_exist"

            if update.resolution == "clear":
                resolution = "clear"
            elif update.resolution == "hedged":
                resolution = "hedged"

            evidence_state = EvidenceState(
                node_id=update.node_id,
                polarity=update.polarity,
                existence=existence,  # type: ignore[arg-type]
                resolution=resolution,  # type: ignore[arg-type]
                reasoning=f"由检查上下文原文链接得到：{update.value or update.evidence or ''}",
                source_turns=[turn_index],
                metadata={
                    "source_stage": "PENDING_ACTION_EXAM_CONTEXT_GENERIC_LINK",
                    "action_id": pending_action.action_id,
                    "hypothesis_id": pending_action.hypothesis_id,
                    "exam_kind": exam_result.exam_kind,
                    "exam_availability": exam_result.availability,
                    "target_node_name": update.metadata.get("normalized_name", update.node_id),
                    "patient_answer": update.evidence,
                    **dict(update.metadata),
                },
            )
            self.deps.state_tracker.set_evidence_state(session_id, evidence_state)

    # 生成 follow-up 前做防循环判定，避免同一个检查入口被泛化追问多轮。
    def _should_use_exam_context_followup_action(
        self,
        session_id: str,
        pending_action: MctsAction,
        exam_result: ExamContextResult,
        followup_action: MctsAction,
        applied_updates: list[SlotUpdate],
        previous_signature: dict,
    ) -> bool:
        if not exam_result.needs_followup or exam_result.availability != "done":
            return False

        if len(applied_updates) > 0:
            return False

        state = self.deps.state_tracker.get_session(session_id)
        target_node_id = followup_action.target_node_id
        followup_mode = str(followup_action.metadata.get("exam_followup_mode") or "")

        if followup_mode == "specific_result":
            return target_node_id not in state.asked_node_ids

        if target_node_id == "__exam_context__::general" and target_node_id in state.asked_node_ids:
            return False

        if self._exam_result_has_test_or_result(exam_result):
            return False

        if not self._exam_context_result_has_new_information(previous_signature, exam_result):
            return False

        counter_key = f"exam_context_followup_count::{target_node_id}"
        count = int(state.metadata.get(counter_key, 0) or 0)
        if count >= 1:
            return False

        state.metadata[counter_key] = count + 1
        if target_node_id == "__exam_context__::general":
            state.metadata["exam_context_general_followup_count"] = int(
                state.metadata.get("exam_context_general_followup_count", 0) or 0
            ) + 1
        state.metadata.setdefault("exam_context_followup_history", []).append(
            {
                "source_action_id": pending_action.action_id,
                "target_node_id": target_node_id,
                "exam_kind": exam_result.exam_kind,
                "followup_mode": followup_mode or "generic",
                "followup_reason": exam_result.followup_reason,
            }
        )
        return True

    # 当前上下文里已经掌握的检查名/结果签名，用于判断下一轮回答是否真的新增了信息。
    def _exam_context_signature(self, state: SessionState, exam_kind: str) -> dict:
        context = state.exam_context.get(exam_kind)

        if context is None:
            return {
                "availability": "unknown",
                "tests": set(),
                "results": set(),
            }

        return {
            "availability": context.availability,
            "tests": {self._normalize_match_text(item) for item in context.mentioned_exam_names},
            "results": {
                (
                    self._normalize_match_text(item.test_name),
                    self._normalize_match_text(item.raw_text),
                    self._normalize_match_text(item.normalized_result),
                )
                for item in context.mentioned_exam_results
            },
        }

    # 检查本轮解析结果是否相对上一轮新增了检查状态、检查名或结果。
    def _exam_context_result_has_new_information(
        self,
        previous_signature: dict,
        exam_result: ExamContextResult,
    ) -> bool:
        if previous_signature.get("availability") != exam_result.availability:
            return True

        previous_tests = set(previous_signature.get("tests") or set())
        current_tests = {self._normalize_match_text(item) for item in exam_result.mentioned_tests}
        if len(current_tests - previous_tests) > 0:
            return True

        previous_results = set(previous_signature.get("results") or set())
        current_results = {
            (
                self._normalize_match_text(item.test_name),
                self._normalize_match_text(item.raw_text),
                self._normalize_match_text(item.normalized_result),
            )
            for item in exam_result.mentioned_results
        }
        return len(current_results - previous_results) > 0

    # 只要患者已经提到检查名、结果或病原体名，就不再对同一个 general 入口做泛化追问。
    def _exam_result_has_test_or_result(self, exam_result: ExamContextResult) -> bool:
        if any(len(str(item).strip()) > 0 for item in exam_result.mentioned_tests):
            return True

        return any(
            len(str(item.test_name).strip()) > 0 or len(str(item.raw_text).strip()) > 0
            for item in exam_result.mentioned_results
        )

    # 优先复用本轮统一 mentions 已写入的 target evidence_state；若异常缺失，再从同一份解释结果补一份。
    def _get_or_build_pending_action_evidence_state(
        self,
        session_id: str,
        action: MctsAction,
        pending_action_result: PendingActionResult,
        turn_index: int,
    ) -> EvidenceState:
        state = self.deps.state_tracker.get_session(session_id)
        existing = state.evidence_states.get(action.target_node_id)

        if existing is not None:
            if turn_index not in existing.source_turns:
                existing.source_turns.append(turn_index)
            return existing

        return EvidenceState(
            node_id=action.target_node_id,
            polarity=pending_action_result.polarity,
            existence=self._polarity_to_existence_compat(pending_action_result.polarity),
            resolution=pending_action_result.resolution,
            reasoning=pending_action_result.reasoning,
            source_turns=[turn_index],
            metadata={
                "source_stage": "TURN_INTERPRETER",
                "recovered_for_pending_action": True,
                "target_node_name": action.target_node_name,
            },
        )

    # 在不重建 evidence_state 的前提下，把 pending_action 相关的解释信息补充到目标节点上。
    def _enrich_pending_action_evidence_state(
        self,
        evidence_state: EvidenceState,
        action: MctsAction,
        pending_action_result: PendingActionResult,
        evidence_tags: set[str],
        patient_text: str,
        confirmed_family_candidate: bool,
        provisional_family_candidate: bool,
    ) -> None:
        evidence_state.polarity = pending_action_result.polarity
        evidence_state.existence = self._polarity_to_existence_compat(pending_action_result.polarity)
        evidence_state.resolution = pending_action_result.resolution
        evidence_state.reasoning = pending_action_result.reasoning
        evidence_state.metadata.update(
            {
                "action_id": action.action_id,
                "hypothesis_id": action.hypothesis_id,
                "relation_type": action.metadata.get("relation_type"),
                "target_node_name": action.target_node_name,
                "target_node_label": action.target_node_label,
                "evidence_tags": sorted(evidence_tags),
                "supporting_span": pending_action_result.supporting_span,
                "negation_span": pending_action_result.negation_span,
                "uncertain_span": pending_action_result.uncertain_span,
                "confirmed_family_candidate": confirmed_family_candidate,
                "confirmed_family_candidates": sorted(evidence_tags & GUARDED_CONFIRMED_EVIDENCE_TAGS),
                "provisional_family_candidate": provisional_family_candidate,
                "provisional_family_candidates": sorted(
                    evidence_tags & {"imaging", "oxygenation", "pathogen", "immune_status", "pcp_specific"}
                ),
                "patient_answer": patient_text,
                **dict(pending_action_result.metadata),
            }
        )

    # 将检查上下文解析结果转换成兼容前端和报告的 pending_action_result。
    def _build_pending_action_result_from_exam_context(
        self,
        action: MctsAction,
        exam_result: ExamContextResult,
    ) -> PendingActionResult:
        polarity = "unclear"
        resolution = "hedged" if exam_result.availability == "unknown" else "clear"

        if exam_result.availability == "done":
            polarity = "present"
        elif exam_result.availability == "not_done":
            polarity = "absent"

        return PendingActionResult(
            action_type=action.action_type,
            target_node_id=action.target_node_id,
            target_node_name=action.target_node_name,
            polarity=polarity,  # type: ignore[arg-type]
            resolution=resolution,  # type: ignore[arg-type]
            reasoning=exam_result.reasoning,
            supporting_span="；".join(item.raw_text for item in exam_result.mentioned_results),
            negation_span="未做相关检查" if exam_result.availability == "not_done" else "",
            uncertain_span="需要继续澄清检查名称或结果" if exam_result.needs_followup else "",
            metadata={
                "action_id": action.action_id,
                "action_type": action.action_type,
                "target_node_id": action.target_node_id,
                "target_node_name": action.target_node_name,
                "exam_context_result": asdict(exam_result),
            },
        )

    # 检查上下文已做但缺结果时，构造一次澄清追问。
    def _build_exam_context_followup_action(
        self,
        action: MctsAction,
        exam_result: ExamContextResult,
    ) -> MctsAction:
        candidate = self._select_exam_result_followup_candidate(action, exam_result)

        if candidate is not None:
            return self._build_specific_exam_result_action(action, exam_result, candidate)

        mentioned_tests = [item for item in exam_result.mentioned_tests if len(item.strip()) > 0]

        if len(mentioned_tests) > 0:
            test_text = "、".join(mentioned_tests[:4])
            question_text = (
                f"你刚才提到做过 {test_text}。能回忆一下大概结果吗？"
                "比如偏低、升高、阳性、阴性，或者报告里写了什么异常都可以。"
            )
        else:
            question_text = (
                "能回忆一下具体做过哪些检查，或者报告里提到过哪些异常吗？"
                "哪怕只记得“偏低、升高、阳性、阴性、磨玻璃影”也可以。"
            )

        return MctsAction(
            action_id=f"{action.action_id}::followup",
            action_type="collect_exam_context",
            target_node_id=action.target_node_id,
            target_node_label=action.target_node_label,
            target_node_name=action.target_node_name,
            hypothesis_id=action.hypothesis_id,
            topic_id=action.topic_id,
            prior_score=action.prior_score,
            metadata={
                **dict(action.metadata),
                "question_text": question_text,
                "exam_context_followup": True,
                "previous_followup_reason": exam_result.followup_reason,
                "mentioned_tests": mentioned_tests,
            },
        )

    # 如果患者已经说出检查名称，则优先追问与该检查最匹配、价值最高的具体结果节点。
    def _select_exam_result_followup_candidate(
        self,
        action: MctsAction,
        exam_result: ExamContextResult,
    ) -> dict | None:
        candidates = action.metadata.get("exam_candidate_evidence", [])

        if not isinstance(candidates, list) or len(exam_result.mentioned_tests) == 0:
            return None

        ranked: list[tuple[float, dict]] = []

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue

            name_match = self._exam_candidate_name_match_score(candidate, exam_result)

            if name_match <= 0.0:
                continue

            score = (
                name_match * 3.0
                + float(candidate.get("priority", 0.0)) * 0.45
                + float(candidate.get("contradiction_priority", 0.0)) * 0.55
                + float(candidate.get("discriminative_gain", 0.0)) * 0.8
                + float(candidate.get("recommended_match_score", 0.0)) * 1.1
                + float(candidate.get("joint_recommended_match_score", 0.0)) * 1.0
                + float(candidate.get("recommended_evidence_bonus", 0.0)) * 0.7
            )
            ranked.append((score, candidate))

        if len(ranked) == 0:
            return None

        return sorted(ranked, key=lambda item: (-item[0], str(item[1].get("name", ""))))[0][1]

    # 估计患者提到的检查名和候选证据节点是否匹配。
    def _exam_candidate_name_match_score(self, candidate: dict, exam_result: ExamContextResult) -> float:
        candidate_name = self._normalize_match_text(str(candidate.get("name") or ""))
        candidate_label = str(candidate.get("label") or "")
        exam_kind = str(candidate.get("exam_kind") or exam_result.exam_kind)
        if exam_kind == "general":
            exam_kind = self._candidate_exam_kind(candidate)
        best = 0.0

        for test_name in exam_result.mentioned_tests:
            test_text = self._normalize_match_text(test_name)

            if len(test_text) == 0 or len(candidate_name) == 0:
                continue

            if test_text in candidate_name or candidate_name in test_text:
                best = max(best, 1.0)

            family_rules = {
                "lab": (
                    (("cd4", "t淋巴"), ("cd4", "t淋巴", "免疫")),
                    (("βd葡聚糖", "bdg", "g试验", "葡聚糖"), ("βd葡聚糖", "bdg", "葡聚糖", "g试验")),
                    (("hivrna", "病毒载量"), ("hivrna", "病毒载量")),
                    (("血氧", "动脉血气", "pao2", "spo2"), ("血氧", "氧分压", "pao2", "spo2", "低氧")),
                ),
                "imaging": (
                    (("胸部ct", "ct", "胸片", "x线"), ("ct", "影像", "磨玻璃", "胸片", "x线")),
                ),
                "pathogen": (
                    (("pcr", "核酸"), ("pcr", "核酸", "阳性", "检出")),
                    (("痰检", "痰培养", "痰涂片"), ("痰", "培养", "涂片", "病原")),
                    (("支气管肺泡", "肺泡灌洗", "bal", "balf"), ("支气管肺泡", "肺泡灌洗", "bal", "balf")),
                ),
            }

            for test_keywords, candidate_keywords in family_rules.get(exam_kind, ()):
                if any(keyword in test_text for keyword in test_keywords) and any(
                    keyword in candidate_name for keyword in candidate_keywords
                ):
                    best = max(best, 0.85)

            if exam_kind == "imaging" and candidate_label == "ImagingFinding" and best == 0.0:
                best = max(best, 0.55)

            if exam_kind == "pathogen" and candidate_label in {"Pathogen", "LabFinding", "LabTest"} and best == 0.0:
                best = max(best, 0.45)

        return best

    # 构造针对某个具体检查结果节点的 follow-up 动作。
    def _build_specific_exam_result_action(
        self,
        source_action: MctsAction,
        exam_result: ExamContextResult,
        candidate: dict,
    ) -> MctsAction:
        node_id = str(candidate.get("node_id") or "").strip()
        target_name = str(candidate.get("name") or node_id).strip()
        label = str(candidate.get("label") or "Unknown")
        exam_kind = self._candidate_exam_kind(candidate) if exam_result.exam_kind == "general" else exam_result.exam_kind
        question_type_hint = str(candidate.get("question_type_hint") or exam_kind)
        mentioned_tests = [item for item in exam_result.mentioned_tests if len(item.strip()) > 0]
        question_text = self._render_specific_exam_result_question(exam_kind, mentioned_tests, target_name)

        return MctsAction(
            action_id=f"verify::{source_action.hypothesis_id or 'unknown'}::{node_id}",
            action_type="verify_evidence",
            target_node_id=node_id,
            target_node_label=label,
            target_node_name=target_name,
            hypothesis_id=source_action.hypothesis_id,
            topic_id=source_action.topic_id,
            prior_score=max(source_action.prior_score, float(candidate.get("priority", 0.0))),
            metadata={
                "relation_type": candidate.get("relation_type"),
                "question_type_hint": question_type_hint,
                "acquisition_mode": candidate.get("acquisition_mode", source_action.metadata.get("acquisition_mode", "")),
                "evidence_cost": candidate.get("evidence_cost", source_action.metadata.get("evidence_cost", "")),
                "patient_burden": source_action.metadata.get("patient_burden", 0.35),
                "contradiction_priority": float(candidate.get("contradiction_priority", 0.0)),
                "discriminative_gain": float(candidate.get("discriminative_gain", 0.0)),
                "recommended_match_score": float(candidate.get("recommended_match_score", 0.0)),
                "joint_recommended_match_score": float(candidate.get("joint_recommended_match_score", 0.0)),
                "question_text": question_text,
                "exam_context_followup": True,
                "exam_followup_mode": "specific_result",
                "exam_kind": exam_kind,
                "source_exam_kind": exam_result.exam_kind,
                "mentioned_tests": mentioned_tests,
                "source_exam_context_action_id": source_action.action_id,
                "evidence_tags": self._normalize_string_list(source_action.metadata.get("evidence_tags", [])),
            },
        )

    # 为具体检查结果 follow-up 渲染更自然的问题。
    def _render_specific_exam_result_question(
        self,
        exam_kind: str,
        mentioned_tests: list[str],
        target_name: str,
    ) -> str:
        test_text = "、".join(mentioned_tests[:3]) if len(mentioned_tests) > 0 else "这个检查"
        target_text = self.deps.action_builder.patient_friendly_target_name(target_name)

        if exam_kind == "imaging":
            return f"你刚才提到做过 {test_text}。报告里有没有提到{target_text}，或者类似的明显异常？"

        if exam_kind == "pathogen":
            return f"你刚才提到做过 {test_text}。结果有没有提示{target_text}，比如阳性、检出或阴性？"

        return f"你刚才提到做过 {test_text}。这个结果大概是否提示{target_text}，比如偏低、升高、阳性或阴性？"

    # 从候选证据 metadata / 标签中恢复内部检查类别。
    def _candidate_exam_kind(self, candidate: dict) -> str:
        exam_kind = str(candidate.get("exam_kind") or "").strip()

        if exam_kind in {"lab", "imaging", "pathogen"}:
            return exam_kind

        question_type_hint = str(candidate.get("question_type_hint") or "").strip()

        if question_type_hint in {"lab", "imaging", "pathogen"}:
            return question_type_hint

        label = str(candidate.get("label") or "")

        if label in {"LabFinding", "LabTest"}:
            return "lab"

        if label == "ImagingFinding":
            return "imaging"

        if label == "Pathogen":
            return "pathogen"

        return "lab"

    # 取出检查上下文澄清动作，避免重复使用。
    def _pop_exam_context_followup_action(self, session_id: str) -> MctsAction | None:
        state = self.deps.state_tracker.get_session(session_id)
        action = state.metadata.pop("exam_context_followup_action", None)

        if isinstance(action, MctsAction):
            return action

        if isinstance(action, dict):
            return MctsAction(**action)

        return None

    # 最终发问前再做一次可问性过滤，兜住 search/repair/follow-up 产生的重复动作。
    def _filter_selected_action_for_repeat(
        self,
        session_id: str,
        selected_action: MctsAction | None,
        search_result: SearchResult,
    ) -> MctsAction | None:
        if selected_action is None or self._selected_action_is_askable(session_id, selected_action):
            return selected_action

        search_result.metadata["filtered_repeated_action"] = asdict(selected_action)
        search_result.metadata["filtered_repeated_action_reason"] = "target_already_asked_or_exam_context_resolved"
        return None

    # 统一判断一个动作当前是否还能问：general exam context 不允许重复，普通节点也不重复问。
    def _selected_action_is_askable(self, session_id: str, action: MctsAction | None) -> bool:
        if action is None:
            return True

        state = self.deps.state_tracker.get_session(session_id)
        target_node_id = str(action.target_node_id or "")
        followup_mode = str(action.metadata.get("exam_followup_mode") or "")

        if target_node_id == "__exam_context__::general" and target_node_id in state.asked_node_ids:
            return False

        if target_node_id in state.asked_node_ids:
            return False

        if action.action_type == "collect_general_exam_context":
            return self._exam_context_availability_for_action(state, "general") == "unknown"

        if action.action_type == "collect_exam_context" and followup_mode != "specific_result":
            exam_kind = str(action.metadata.get("exam_kind") or "").strip()
            if exam_kind in {"general", "lab", "imaging", "pathogen"}:
                return self._exam_context_availability_for_action(state, exam_kind) == "unknown"

        return True

    def _exam_context_availability_for_action(self, state: SessionState, exam_kind: str) -> str:
        context = state.exam_context.get(exam_kind)

        if context is None:
            return "unknown"

        return context.availability

    # 运行 R1 + A2，生成主假设和备选假设并写回当前会话状态。
    def _run_a2(
        self,
        session_id: str,
        patient_context: PatientContext,
        a1_result: A1ExtractionResult,
        linked_entities: Sequence[LinkedEntity],
    ) -> A2HypothesisResult:
        tracker = self.deps.state_tracker
        state = tracker.get_session(session_id)
        force_reason = str(state.metadata.pop("force_a2_refresh_reason", "") or "")
        force_source = str(state.metadata.pop("force_a2_refresh_source", "") or "")
        force_evidence = state.metadata.pop("force_a2_refresh_evidence", [])
        state.metadata.pop("force_a2_refresh", None)
        candidates = self.deps.retriever.retrieve_r1_candidates(
            list(linked_entities) + list(a1_result.key_features),
            patient_context,
            state,
        )
        a2_result = self.deps.hypothesis_manager.run_a2_hypothesis_generation(patient_context, candidates)
        if len(force_reason) > 0:
            a2_result.metadata.update(
                {
                    "force_a2_refresh_reason": force_reason,
                    "force_a2_refresh_source": force_source,
                    "force_a2_refresh_evidence": force_evidence,
                }
            )
        score_candidates = []

        if a2_result.primary_hypothesis is not None:
            score_candidates.append(a2_result.primary_hypothesis)

        score_candidates.extend(a2_result.alternatives)
        tracker.set_candidate_hypotheses(
            session_id,
            self.deps.hypothesis_manager.build_hypothesis_scores(score_candidates),
        )
        return a2_result

    # A3 常规追问轮次优先复用上一轮的 hypothesis 排名，只有需要重建假设时才重新执行 A2。
    def _should_refresh_a2(
        self,
        state: SessionState,
        *,
        effective_stage: str | None,
        should_run_a1: bool,
        a1_result: A1ExtractionResult,
    ) -> bool:
        if len(state.candidate_hypotheses) == 0:
            return True

        if effective_stage == "A2":
            return True

        if bool(state.metadata.get("force_a2_refresh", False)):
            return True

        return should_run_a1 and len(a1_result.key_features) > 0

    # 当本轮跳过 A2 重算时，回填当前 hypothesis 排名，避免上层把 a2 结果展示为空。
    def _build_cached_a2_result(self, state: SessionState) -> A2HypothesisResult:
        if len(state.candidate_hypotheses) == 0:
            return A2HypothesisResult()

        ranked = sorted(state.candidate_hypotheses, key=lambda item: (-item.score, item.name))
        primary = self._score_to_candidate(ranked[0])
        alternatives = [
            self._score_to_candidate(item)
            for item in ranked[1 : self.deps.hypothesis_manager.config.expand_top_k_hypotheses]
        ]
        return A2HypothesisResult(
            primary_hypothesis=primary,
            alternatives=alternatives,
            reasoning="沿用上一轮已生成的候选假设排序，当前轮次不重复执行 A2。",
            metadata={"source": "cached"},
        )

    # 将内部 hypothesis score 转成 A2 展示使用的候选对象。
    def _score_to_candidate(self, hypothesis: HypothesisScore) -> HypothesisCandidate:
        return HypothesisCandidate(
            node_id=hypothesis.node_id,
            name=hypothesis.name,
            label=hypothesis.label,
            score=hypothesis.score,
            metadata=dict(hypothesis.metadata),
        )

    # 构建 A2 展示专用的候选诊断证据画像，帮助前端解释排序依据。
    def _build_a2_evidence_profiles(self, session_id: str, limit: int = 5) -> list[dict]:
        state = self.deps.state_tracker.get_session(session_id)
        ranked_hypotheses = sorted(state.candidate_hypotheses, key=lambda item: (-item.score, item.name))[:limit]
        profiles: list[dict] = []

        for index, hypothesis in enumerate(ranked_hypotheses):
            evidence_items = self._retrieve_display_evidence_profile(hypothesis, state)
            evidence_groups = self._group_display_evidence_items(evidence_items)
            status_counts = self._count_evidence_profile_statuses(evidence_items)
            profiles.append(
                {
                    "candidate_id": hypothesis.node_id,
                    "candidate_name": hypothesis.name,
                    "candidate_label": hypothesis.label,
                    "is_primary": index == 0,
                    "score": hypothesis.score,
                    "score_text": f"{hypothesis.score:.2f}",
                    "evidence_groups": evidence_groups,
                    "matched_count": status_counts["matched"],
                    "negative_count": status_counts["negative"],
                    "unknown_count": status_counts["unknown"],
                    "score_breakdown": self._build_evidence_profile_score_breakdown(status_counts, evidence_groups),
                    "reasoning": str(hypothesis.metadata.get("reasoning") or ""),
                }
            )

        return profiles

    # 读取展示用证据画像；当 retriever 不支持时优雅降级为空证据组。
    def _retrieve_display_evidence_profile(self, hypothesis: HypothesisScore, state: SessionState) -> list[dict]:
        retriever = self.deps.retriever

        if not hasattr(retriever, "retrieve_candidate_evidence_profile"):
            return []

        try:
            rows = retriever.retrieve_candidate_evidence_profile(hypothesis, state)
        except Exception as exc:
            state.metadata["last_a2_evidence_profile_error"] = str(exc)
            return []

        return [dict(item) for item in rows if isinstance(item, dict)]

    # 将证据画像按老师更容易理解的临床证据类别分组。
    def _group_display_evidence_items(self, evidence_items: list[dict]) -> dict[str, list[dict]]:
        groups: dict[str, list[dict]] = {
            "symptom": [],
            "risk": [],
            "lab": [],
            "imaging": [],
            "pathogen": [],
            "detail": [],
        }

        for item in evidence_items:
            group_key = self._normalize_display_evidence_group(item)
            groups.setdefault(group_key, []).append(
                {
                    "node_id": item.get("node_id"),
                    "name": item.get("name", "未命名证据"),
                    "label": item.get("label", ""),
                    "relation_type": item.get("relation_type", ""),
                    "question_type": item.get("question_type_hint", group_key),
                    "status": item.get("status", "unknown"),
                    "status_label": item.get("status_label", "待验证"),
                    "resolution": item.get("resolution", "unknown"),
                    "evidence_text": item.get("evidence_text", ""),
                    "acquisition_mode": item.get("acquisition_mode", ""),
                    "evidence_cost": item.get("evidence_cost", ""),
                }
            )

        return {key: value for key, value in groups.items() if len(value) > 0}

    # 兼容 retriever 旧字段或测试桩未提供 group 的情况。
    def _normalize_display_evidence_group(self, item: dict) -> str:
        group = str(item.get("group") or item.get("question_type_hint") or "").strip()

        if group in {"symptom", "risk", "lab", "imaging", "pathogen", "detail"}:
            return group

        label = str(item.get("label") or "")
        relation_type = str(item.get("relation_type") or "")

        if label == "ClinicalFinding":
            return "symptom"

        if label in {"RiskFactor", "PopulationGroup"} or relation_type == "RISK_FACTOR_FOR":
            return "risk"

        if label in {"LabFinding", "LabTest"} or relation_type == "HAS_LAB_FINDING":
            return "lab"

        if label == "ImagingFinding" or relation_type == "HAS_IMAGING_FINDING":
            return "imaging"

        if label == "Pathogen" or relation_type == "HAS_PATHOGEN":
            return "pathogen"

        return "detail"

    # 统计证据画像里的已命中 / 已否定 / 待验证数量。
    def _count_evidence_profile_statuses(self, evidence_items: list[dict]) -> dict[str, int]:
        counts = {"matched": 0, "negative": 0, "unknown": 0}

        for item in evidence_items:
            status = str(item.get("status") or "unknown")

            if status not in counts:
                status = "unknown"

            counts[status] += 1

        return counts

    # 生成一句面向展示的分数解释，不做复杂数学拆解。
    def _build_evidence_profile_score_breakdown(
        self,
        status_counts: dict[str, int],
        evidence_groups: dict[str, list[dict]],
    ) -> str:
        group_labels = {
            "symptom": "症状 / 体征",
            "risk": "风险背景",
            "lab": "化验",
            "imaging": "影像",
            "pathogen": "病原学",
            "detail": "关键细节",
        }
        active_groups = [group_labels.get(key, key) for key, items in evidence_groups.items() if len(items) > 0]
        base = (
            f"当前已有 {status_counts.get('matched', 0)} 条支持证据、"
            f"{status_counts.get('negative', 0)} 条反向证据、"
            f"{status_counts.get('unknown', 0)} 条待验证证据。"
        )

        if len(active_groups) == 0:
            return base + "分数主要来自 A2 候选排序与已有患者线索。"

        return base + "分数主要结合 " + "、".join(active_groups[:4]) + " 与当前患者线索共同形成。"

    # 运行局部树搜索，生成下一问动作、候选轨迹和最终答案评分。
    def run_reasoning_search(
        self,
        session_id: str,
        patient_context: PatientContext,
    ) -> SearchResult:
        tracker = self.deps.state_tracker

        # 先拿到当前会话状态；这份 state 是“真实会话态”，后续 rollout 使用的则是从它派生出来的轻量分支快照。
        state = tracker.get_session(session_id)

        # 搜索树按 session 复用：
        # - 若状态签名和当前 top hypothesis 没变，则继续沿用旧树
        # - 若 verifier repair 或 hypothesis 排名发生明显变化，则在 _ensure_search_tree() 内部 reroot / 重建
        tree = self._ensure_search_tree(session_id, state)

        # `trajectories` 收集本轮 search 里所有 rollout 产出的路径；
        # 它们既用于 root action 选择后的解释，也用于最终答案分组评分。
        trajectories: list[ReasoningTrajectory] = []

        # 不是每次 for-loop 都一定成功展开：
        # - 有可能 select_leaf 拿不到可扩展叶子
        # - 也可能叶子没有可扩展动作
        # 所以单独统计真正完成了 expand + rollout 的次数。
        rollout_executed = 0

        # MCTS 外层 rollout 循环：每一轮都尝试“选一个叶子 -> 为它扩动作 -> 从每个子节点做前瞻推演”。
        for rollout_idx in range(self.deps.mcts_engine.config.num_rollouts):
            # tree policy 负责从当前树里挑出最值得继续扩展的叶子；
            # 它已经综合了 visit_count、average_value、prior_score 和 exploration bonus。
            leaf = self.deps.mcts_engine.select_leaf(tree)

            if leaf is None:
                # 没有叶子可扩通常意味着：
                # - 树为空
                # - 当前 root/children 都已 terminal
                # - 或 all children terminal 后 tree policy 主动停下
                # 这时整轮 search 直接结束，不再硬凑 rollout。
                break

            # 叶子节点里保存的是某条搜索分支的轻量 rollout_state；
            # 这里把它恢复成“当前分支上下文”，包括：
            # - 当前假设
            # - competing hypotheses
            # - 这条分支下已经累计的 slots/evidence/exam_context
            rollout_context = self._build_rollout_context_from_leaf(session_id, leaf)

            # 记录状态签名访问次数，后续 UCT 的 parent visit 统计和调试信息都依赖这里。
            # metadata 里额外带上本次叶子和 hypothesis，便于 replay/排障时追踪“第几个 rollout 访问了哪个分支”。
            tracker.increment_state_visit(
                session_id,
                leaf.state_signature,
                {
                    "leaf_node_id": leaf.node_id,
                    "hypothesis_id": getattr(rollout_context["current_hypothesis"], "node_id", None),
                    "rollout_idx": rollout_idx,
                },
            )

            # 扩展动作的入口在 R2：
            # 当前分支的主假设会反向取回一批“最值得继续验证的证据节点”，
            # 再由 ActionBuilder 转成真正可执行的动作（verify_evidence / collect_exam_context 等）。
            actions = self._expand_actions_for_leaf(leaf, rollout_context)

            if len(actions) == 0:
                # 如果这个叶子已经没有可扩展动作，就把它标成 terminal；
                # 这样后续 select_leaf() 不会再反复回到这个死分支。
                tree.mark_terminal(leaf.node_id, {"terminal_reason": "no_expandable_actions"})
                continue

            # expand_node 只负责把候选动作挂成树上的 child node；
            # 真正的收益估计和路径前瞻发生在下面的 rollout_from_tree_node()。
            child_nodes = self.deps.mcts_engine.expand_node(tree, leaf.node_id, actions)

            if len(child_nodes) == 0:
                # 理论上很少发生，但如果 expand 失败，也要把叶子标 terminal，避免后续空转。
                tree.mark_terminal(leaf.node_id, {"terminal_reason": "expand_failed"})
                continue

            # 能走到这里说明这轮 rollout 至少成功完成了“选叶 + 扩动作”。
            rollout_executed += 1

            # 一个叶子可能扩出多个 child；这里不是只挑一个 child 推演，
            # 而是对每个 child 都做一次独立 rollout，尽量多收集候选路径。
            for child in child_nodes:
                # rollout_from_tree_node 会模拟：
                # A3 动作 -> 假想回答分支 -> route -> 下一步动作 ...
                # 直到达到 max_depth、路径停止，或没有后续动作。
                trajectory = self.deps.simulation_engine.rollout_from_tree_node(
                    child,
                    rollout_context["state"],
                    patient_context,
                    router=self.deps.router,
                    hypothesis_manager=self.deps.hypothesis_manager,
                    retriever=self.deps.retriever,
                    action_builder=self.deps.action_builder,
                    max_depth=self.deps.mcts_engine.config.max_depth,
                    current_hypothesis=rollout_context["current_hypothesis"],
                    competing_hypotheses=rollout_context["alternatives"],
                )

                # rollout 内部会临时把更新后的分支状态塞在 metadata["_rollout_state"] 里返回；
                # 这里取出来后再裁剪成轻量 snapshot，作为 child 的“继续向下扩展起点”缓存起来。
                rollout_state = trajectory.metadata.pop("_rollout_state", None)
                if isinstance(rollout_state, SessionState):
                    child.metadata["rollout_state"] = tracker.build_rollout_session_snapshot(rollout_state)

                # 下面这些 metadata 是后续 reroot、repair、调试和 search_report 解释最常用的几个摘要字段。
                child.metadata["rollout_depth"] = trajectory.metadata.get("rollout_depth", 0)
                child.metadata["last_stage"] = trajectory.metadata.get("last_stage")
                child.metadata["final_answer_id"] = trajectory.final_answer_id
                child.metadata["final_answer_name"] = trajectory.final_answer_name

                if bool(trajectory.metadata.get("path_terminal", False)):
                    # 如果 rollout 已经在这条 child 路径上走到 STOP，
                    # 就把 child 直接视作 terminal，后续不用再从它继续展开。
                    tree.mark_terminal(
                        child.node_id,
                        {"terminal_reason": "rollout_stop", "final_answer_id": trajectory.final_answer_id},
                    )

                # trajectory 会同时写到：
                # - 本轮局部列表 `trajectories`
                # - session 的长期轨迹缓存 `state.trajectories`
                # 前者服务当前 search 聚合，后者服务最终报告与 replay 复盘。
                trajectories.append(trajectory)
                tracker.save_trajectory(session_id, trajectory)

                # backpropagate 会把这条轨迹得分沿 child -> parent -> root 逐层回传，
                # 让后续 select_leaf / select_root_action 感知到这条路径的历史价值。
                self.deps.mcts_engine.backpropagate(tree, child.node_id, trajectory.score)

        # rollout 全部完成后，下一步不再看“单条轨迹”，而是按最终答案把轨迹分组。
        grouped = self.deps.trajectory_evaluator.group_by_answer(trajectories)

        # verifier 不能只看“最新一句患者回复”，否则会忽略历史上已确认的关键证据；
        # 因此这里会把累计 slots / evidence_states / candidate_hypotheses 整理进 verifier 上下文。
        verifier_patient_context = self._build_verifier_patient_context(session_id, patient_context)

        # score_groups 会给每个答案组打出：
        # - consistency
        # - diversity
        # - agent_evaluation（可能是 fallback，也可能是 llm_verifier）
        # 最终得到 final_answer_scores，供 accept/repair 使用。
        final_scores = self.deps.trajectory_evaluator.score_groups(
            grouped,
            patient_context=verifier_patient_context,
            session_turn_index=state.turn_index,
        )
        if self._needs_candidate_state_answer_fallback(final_scores) and len(state.candidate_hypotheses) > 0:
            final_scores = self.deps.trajectory_evaluator.score_candidate_hypotheses_without_trajectories(
                state.candidate_hypotheses,
                patient_context=verifier_patient_context,
            )

        # best_answer 代表“从答案聚合视角看，当前最优的最终诊断候选是谁”。
        best_answer = self.deps.trajectory_evaluator.select_best_answer(final_scores)

        # 但 search 还需要一个“下一问动作”。
        # 这里回到 root 层，从 root 的 child 里选出当前最值得真正发问的一条 action。
        # 同时排除已经问过的 target_node_id，避免重复追问同一节点。
        selected_action = self.deps.mcts_engine.select_root_action(
            tree,
            excluded_target_node_ids=state.asked_node_ids,
        )

        # SearchResult 是 process_turn() 后半段 stop/verifier/repair 的输入；
        # 它同时保留：
        # - root 级下一问
        # - 所有 rollout 轨迹
        # - 最终答案分组评分
        # - 本轮搜索规模统计
        search_result = SearchResult(
            selected_action=selected_action,
            root_best_action=selected_action,
            trajectories=trajectories,
            final_answer_scores=final_scores,
            best_answer_id=best_answer.answer_id if best_answer is not None else None,
            best_answer_name=best_answer.answer_name if best_answer is not None else None,
            metadata={
                "rollouts_requested": self.deps.mcts_engine.config.num_rollouts,
                "rollouts_executed": rollout_executed,
                "tree_node_count": len(tree.nodes),
                "tree_refresh": dict(state.metadata.get("last_tree_refresh", {})),
            },
        )

        # 把最近一次 search 结果挂回真实 session metadata；
        # 后续 finalize()、前端 search_report、以及调试复盘都会读取这里。
        state.metadata["last_search_result"] = search_result
        return search_result

    # rollout 可能只产出 UNKNOWN/空答案组；此时用当前 A2 候选态补一组保守 answer score。
    def _needs_candidate_state_answer_fallback(self, scores: Sequence[FinalAnswerScore]) -> bool:
        if len(scores) == 0:
            return True

        for score in scores:
            answer_id = str(score.answer_id or "").strip().upper()
            answer_name = str(score.answer_name or "").strip().upper()

            if answer_id not in {"", "UNKNOWN"} and answer_name not in {"", "UNKNOWN"}:
                return False

        return True

    # verifier 判断是否可以停止时需要看到累计会话证据，而不是只看当前 turn 的患者回复。
    def _build_verifier_patient_context(self, session_id: str, latest_context: PatientContext) -> PatientContext:
        state = self.deps.state_tracker.get_session(session_id)
        raw_sections: list[str] = []
        observed_session_evidence: list[dict] = []
        latest_text = latest_context.raw_text.strip()

        if len(latest_text) > 0:
            raw_sections.append(f"最新患者回答：{latest_text}")

        if len(state.slots) > 0:
            raw_sections.append("累计已确认槽位：")

            for slot in state.slots.values():
                if slot.status == "unknown" and slot.effective_polarity() == "unclear":
                    continue

                evidence_text = "；".join(str(item) for item in slot.evidence if len(str(item).strip()) > 0)
                raw_sections.append(
                    f"- {slot.node_id}: polarity={slot.effective_polarity()}, status={slot.status}, resolution={slot.resolution}, evidence={evidence_text}"
                )
                observed_session_evidence.append(
                    {
                        "source": "observed_slot",
                        "node_id": slot.node_id,
                        "name": str(slot.metadata.get("normalized_name") or slot.metadata.get("target_node_name") or slot.node_id),
                        "polarity": slot.effective_polarity(),
                        "status": slot.status,
                        "existence": "exist" if slot.effective_polarity() == "present" else "non_exist" if slot.effective_polarity() == "absent" else "unknown",
                        "resolution": slot.resolution,
                        "hypothesis_id": str(slot.metadata.get("hypothesis_id") or ""),
                        "relation_type": str(slot.metadata.get("relation_type") or ""),
                        "evidence_tags": self._normalize_string_list(slot.metadata.get("evidence_tags", [])),
                        "source_turns": list(slot.source_turns),
                        "evidence": evidence_text,
                    }
                )

        if len(state.mention_context) > 0:
            raw_sections.append("累计提及项上下文：")

            for mention in state.mention_context.values():
                evidence_text = "；".join(str(item) for item in mention.evidence if len(str(item).strip()) > 0)
                raw_sections.append(
                    f"- {mention.display_name or mention.normalized_name}: polarity={mention.polarity}, evidence={evidence_text}"
                )

        if len(state.evidence_states) > 0:
            raw_sections.append("累计上一轮动作证据判断：")

            for evidence in state.evidence_states.values():
                evidence_name = str(
                    evidence.metadata.get("target_node_name")
                    or evidence.metadata.get("normalized_name")
                    or evidence.node_id
                )
                raw_sections.append(
                    f"- {evidence.node_id}: name={evidence_name}, polarity={evidence.effective_polarity()}, existence={evidence.existence}, resolution={evidence.resolution}, reasoning={evidence.reasoning}"
                )
                observed_session_evidence.append(
                    {
                        "source": "observed_evidence_state",
                        "node_id": evidence.node_id,
                        "name": evidence_name,
                        "polarity": evidence.effective_polarity(),
                        "existence": evidence.existence,
                        "resolution": evidence.resolution,
                        "hypothesis_id": str(evidence.metadata.get("hypothesis_id") or ""),
                        "relation_type": str(evidence.metadata.get("relation_type") or ""),
                        "evidence_tags": self._normalize_string_list(evidence.metadata.get("evidence_tags", [])),
                        "source_stage": str(evidence.metadata.get("source_stage") or ""),
                        "source_turns": list(evidence.source_turns),
                        "reasoning": evidence.reasoning,
                    }
                )

        if len(state.candidate_hypotheses) > 0:
            raw_sections.append("当前候选假设：")

            for hypothesis in state.candidate_hypotheses[:5]:
                raw_sections.append(f"- {hypothesis.name}: score={hypothesis.score:.4f}, node_id={hypothesis.node_id}")

        raw_text = "\n".join(raw_sections).strip() or latest_context.raw_text
        return PatientContext(
            general_info=latest_context.general_info,
            clinical_features=list(latest_context.clinical_features),
            raw_text=raw_text,
            metadata={
                **dict(latest_context.metadata),
                "context_scope": "cumulative_session_for_verifier",
                "observed_session_evidence": observed_session_evidence,
            },
        )

    # 将搜索结果转成一条可直接用于提问的动作。
    def choose_next_question_from_search(self, session_id: str, search_result: SearchResult) -> MctsAction | None:
        if search_result.selected_action is not None:
            return search_result.selected_action

        state = self.deps.state_tracker.get_session(session_id)
        cold_candidate = self.deps.question_selector.select_next_question(
            self.deps.retriever.get_cold_start_questions(),
            state,
        )

        if cold_candidate is None:
            return None

        return self.deps.action_builder.build_probe_action_from_question_candidate(cold_candidate)

    # 根据搜索结果和终止规则生成最终可展示的推理报告。
    def finalize_from_search(
        self,
        session_id: str,
        search_result: SearchResult,
    ) -> dict:
        state = self.deps.state_tracker.get_session(session_id)
        best_answer_score = self.deps.trajectory_evaluator.select_best_answer(search_result.final_answer_scores)
        accept_decision = self.deps.stop_rule_engine.should_accept_final_answer(best_answer_score, state)
        return self.deps.report_builder.build_final_reasoning_report(state, accept_decision, search_result)

    # 处理单轮患者输入，并输出当前下一问或最终报告。
    def process_turn(self, session_id: str, patient_text: str) -> dict:
        tracker = self.deps.state_tracker
        # 单轮入口先推进轮次，再统一执行一次 turn_interpreter。
        # 之后所有分支都只消费这一份 mentions 结果，避免重复解释同一回答。
        turn_index = tracker.increment_turn(session_id)
        pending_action = tracker.get_pending_action(session_id)
        known_feature_names = self._collect_known_feature_names(session_id)
        turn_result = self.deps.evidence_parser.interpret_turn(patient_text, pending_action=pending_action)
        linked_entities = self._prepare_turn_mentions(turn_result, pending_action)
        patient_context = self.deps.evidence_parser.build_patient_context_from_turn(turn_result, patient_text)
        a1_result = self.deps.evidence_parser.run_a1_key_symptom_extraction(
            patient_context,
            known_feature_names=known_feature_names,
        )
        generic_updates = self._build_slot_updates_from_mentions(turn_result.mentions, turn_index=turn_index)

        # 统一提及项先合并进会话上下文，再写入通用 slots/evidence_states，
        # 让 present / unclear / absent 都能被后续检索、重排与冲突分析复用。
        tracker.merge_mention_items(session_id, turn_result.mentions, turn_index=turn_index)
        if len(generic_updates) > 0:
            tracker.apply_slot_updates(session_id, generic_updates)
        self._apply_generic_evidence_states_from_mentions(session_id, turn_result.mentions, turn_index=turn_index)
        self._mark_a2_refresh_if_strong_updates(
            session_id,
            generic_updates,
            source="turn_interpreter",
        )

        # 如果上一轮已经发出问题，这里会先把本轮回答解释成：
        # - 上一轮动作对应的目标证据判断
        # - exam context 更新
        # - slot / evidence state 更新
        # - route 决策
        # 也就是说，真正的状态刷新发生在 A1/A2/A3 之前。
        pending_action_result, pending_action_decision, route_after_pending_action, pending_action_updates = self.update_from_pending_action(
            session_id,
            patient_context,
            patient_text,
            turn_index,
            turn_result=turn_result,
        )

        # pending_action 即使给出局部 STOP 倾向，也要先降级成继续搜索，由 verifier + guarded gate 再做一次全局确认。
        route_after_pending_action = self._gate_pending_action_route(route_after_pending_action)

        # `applied_updates` 用来累计本轮所有真正写入状态的更新，最终统一返回给前端 / replay 结果。
        # 统一提及写入是本轮的主状态来源，exam_context 等动作产生的补充更新叠加在后面。
        applied_updates: list[SlotUpdate] = list(generic_updates) + list(pending_action_updates)

        # 读取一次已经过 pending_action 刷新的最新会话状态；后续阶段判断都基于这个状态而不是旧状态。
        state = tracker.get_session(session_id)
        stage_after_pending_action = getattr(route_after_pending_action, "stage", None)

        # 下面这些对象是本轮统一返回结构的占位结果；无论走哪条分支，都尽量返回同一套字段。
        a2_result = A2HypothesisResult()
        search_result = SearchResult()
        selected_action: MctsAction | None = None

        # 如果上一轮只是确认“检查做过没”，但没说清结果，这里会优先拿出 follow-up 动作继续追问具体结果。
        exam_followup_action = self._pop_exam_context_followup_action(session_id)

        # `default_search_action` 保存 search 原本选中的动作；
        # 若 verifier 后续没有触发 repair，就直接使用这条动作继续提问。
        default_search_action: MctsAction | None = None

        # 某些特殊停止（例如主诉反复澄清后仍完全无信号）不依赖 search 结果，
        # 会先构造成强制 stop，稍后覆盖常规 stop decision。
        forced_stop_decision: StopDecision | None = None

        # 这是基于“当前 session_state 里有没有槽位 / hypothesis”得到的朴素阶段判断，
        # 后面还会与 pending_action 的 route 决策合并，形成真正的 `effective_stage`。
        route_after_slot_update = self.deps.router.route_after_slot_update(state)

        # A1 现在只是同一份 mentions 的“首轮检索视图”，因此每轮都可稳定派生，
        # 不再需要再做一次独立的抽取或写槽位。
        should_run_a1 = True

        # pending_action 写回状态后，再根据最新槽位与 hypothesis 重新判断本轮主阶段。
        route_after_slot_update = self.deps.router.route_after_slot_update(tracker.get_session(session_id))

        # `effective_stage` 的优先级是：
        # - 若 pending_action 没给出更强约束，沿用基于 state 的 route_after_slot_update
        # - 若 pending_action 明确要求转去 A2/A3/FALLBACK，则优先听从这条路由结果
        # - 只有 pending_action=None 或 pending_action->A1 时，才允许 route_after_slot_update 接管
        effective_stage = (
            route_after_slot_update.stage
            if stage_after_pending_action in {None, "A1"}
            else stage_after_pending_action
        )

        # 先处理无需正式 search 的快捷分支：检查 follow-up、主诉澄清、重复 intake 停止、fallback。
        if exam_followup_action is not None:
            # 患者已经明确说“做过检查”，但还没给结果时，优先把这条链追完，
            # 避免 search 立刻跳去问别的证据，导致检查结果信息丢失。
            selected_action = exam_followup_action
            search_result.selected_action = selected_action
            search_result.root_best_action = selected_action
            search_result.metadata["fallback_reason"] = "exam_context_needs_followup"
        elif self._should_stop_after_repeated_chief_complaint(
            session_id,
            patient_context,
            a1_result,
            pending_action_result,
        ):
            # 连续两轮主诉澄清仍然没有任何临床信号时，不再机械重复 intake，直接阶段性停止。
            forced_stop_decision = self._build_repeated_chief_complaint_stop_decision(session_id)
            search_result.metadata["fallback_reason"] = "repeated_chief_complaint_without_signal"
        elif self._should_collect_chief_complaint(patient_context, a1_result, pending_action_result):
            # 当前输入几乎没有可推理的症状/病史/检查信息，先回到“请描述主诉”的 intake 动作。
            selected_action = self._build_chief_complaint_intake_action(patient_text)
            search_result.selected_action = selected_action
            search_result.root_best_action = selected_action
            search_result.metadata["fallback_reason"] = "no_clinical_information_in_patient_text"
        elif effective_stage == "FALLBACK":
            # fail_count 或 route 已要求降级时，不再依赖 hypothesis/search，
            # 直接用全局冷启动问题做一轮兜底探测。
            fallback_candidate = self.deps.question_selector.select_next_question(
                self.deps.retriever.get_cold_start_questions(),
                tracker.get_session(session_id),
            )
            selected_action = (
                self.deps.action_builder.build_probe_action_from_question_candidate(fallback_candidate)
                if fallback_candidate is not None
                else None
            )
        else:
            # 常规主路径：需要时刷新 A2；若已有候选诊断，则进入 A3 的局部树搜索。
            should_run_a2 = self._should_refresh_a2(
                tracker.get_session(session_id),
                effective_stage=effective_stage,
                should_run_a1=should_run_a1,
                a1_result=a1_result,
            )

            if should_run_a2:
                # 只有在 hypothesis 尚未建立、pending_action 明确要求回 A2、或 A1 抽出了新线索时，才重跑 R1 + A2。
                a2_result = self._run_a2(session_id, patient_context, a1_result, linked_entities)
            else:
                # 大多数常规 A3 追问轮次会复用上一轮的 hypothesis 排名，避免每轮重复重算 A2。
                a2_result = self._build_cached_a2_result(tracker.get_session(session_id))

            if effective_stage in {"A2", "A3"} and len(tracker.get_session(session_id).candidate_hypotheses) > 0:
                # 只有已经形成候选诊断时，A3 才有意义；
                # `run_reasoning_search()` 会完成 R2 检索、树扩展、rollout、trajectory 聚合与 root action 选择。
                search_result = self.run_reasoning_search(session_id, patient_context)
                default_search_action = self.choose_next_question_from_search(session_id, search_result)

        # search 结束后不直接发问，先经过 stop rule、trajectory 聚合、verifier 与 repair。
        # `check_sufficiency()` 更像传统启发式 stop：只看 hypothesis 分数和 margin，成本低但不够严格。
        stop_decision = self.deps.stop_rule_engine.check_sufficiency(
            tracker.get_session(session_id),
            tracker.get_session(session_id).candidate_hypotheses,
        )
        if forced_stop_decision is not None:
            # 特殊 stop（如 repeated chief complaint）优先级更高，显式覆盖常规 sufficiency stop。
            stop_decision = forced_stop_decision

        # `best_answer_score` 来自 trajectory 分组聚合，是 search 路径层面的“当前最优答案”。
        best_answer_score = self.deps.trajectory_evaluator.select_best_answer(search_result.final_answer_scores)

        # `should_accept_final_answer()` 才是真正的“全局能不能停”闸门；
        # 它会综合 turn_index、trajectory_count、verifier、guarded gate 等条件。
        accept_decision = self.deps.stop_rule_engine.should_accept_final_answer(best_answer_score, tracker.get_session(session_id))

        # 如果 verifier/guarded gate 认为“现在还不能停”，这里会提炼出 repair 原因与下一步补证据信号。
        repair_context = self._build_verifier_repair_context(
            session_id,
            search_result,
            best_answer_score,
            accept_decision,
        )

        if repair_context is not None:
            # repair 的本质不是推翻 search，而是把“为什么不能停”写回 hypothesis 排名与 tree refresh 理由，
            # 然后专门选一条更能补关键缺口的下一问。
            self._apply_verifier_repair_strategy(session_id, repair_context)
            if bool(self.deps.repair_policy.enable_best_repair_action):
                selected_action = self._choose_repair_action(session_id, search_result, repair_context)
                search_result.repair_selected_action = selected_action
            else:
                # ablation 关闭 repair action 时，仍退回 search 原本给出的 root action。
                selected_action = default_search_action
        elif default_search_action is not None:
            # verifier 没有拦截时，沿用 search 默认动作即可。
            selected_action = default_search_action

        # 若 search 没选出动作，再尝试阶段性停止；仍不能停时，最后退回冷启动探针问题。
        if selected_action is None and not accept_decision.should_stop:
            # 这一层主要处理“高成本检查没做，但当前也没有可继续追问的低成本证据”。
            stage_stop_decision = self._build_exam_limited_stage_stop_decision(session_id)

            if stage_stop_decision is not None:
                stop_decision = stage_stop_decision
                search_result.metadata["fallback_reason"] = "no_exam_and_no_low_cost_questions"

        if selected_action is None and not accept_decision.should_stop and not stop_decision.should_stop:
            # 连 search + repair 都给不出动作时，最后再退回一次冷启动探针，避免会话空转。
            selected_action = self._choose_cold_start_probe_action(session_id)

            if selected_action is not None and not self._has_search_signal(search_result):
                # 如果前面根本没有形成有效 search 结果，就把这条冷启动问题当作本轮的显式 selected action。
                search_result.selected_action = selected_action
                search_result.root_best_action = selected_action
                search_result.metadata["fallback_reason"] = "no_a2_a3_action_available"

        selected_action = self._filter_selected_action_for_repeat(session_id, selected_action, search_result)
        if selected_action is None and not accept_decision.should_stop and not stop_decision.should_stop:
            selected_action = self._choose_cold_start_probe_action(session_id)
            if selected_action is not None and self._selected_action_is_askable(session_id, selected_action):
                search_result.selected_action = selected_action
                search_result.root_best_action = selected_action
                search_result.metadata["fallback_reason"] = "repeat_action_filtered_to_cold_start"
            else:
                selected_action = None

        if self._has_search_signal(search_result):
            # 这里把 repair 前后的动作、reroot 情况和 reject reason 整理成便于前端/复盘读取的观测结构。
            search_result.selected_action = selected_action
            search_result.verifier_repair_context = self._build_observable_repair_context(
                search_result,
                repair_context,
                selected_action,
            )

        # 无论最终是否继续提问，都尽量补出当前 top hypotheses 的证据画像，方便 UI 解释“为什么是这些候选”。
        a2_evidence_profiles = self._build_a2_evidence_profiles(session_id)

        # 本轮真正结束时返回 final_report；否则继续登记下一问，让外层在下一轮把回答送回来。
        if self._should_emit_final_report(search_result, selected_action, stop_decision, accept_decision):
            if stop_decision.reason in {
                "no_exam_and_no_low_cost_questions",
                "exam_not_done_and_no_low_cost_questions",
                "insufficient_observable_evidence",
            }:
                # 这类停止更像“当前可观察证据已经到头”，直接返回阶段性报告，不强行包装成最终 reasoning answer。
                final_report = self.deps.report_builder.build_final_report(tracker.get_session(session_id), stop_decision)
            else:
                # 正常接受停止时，优先用 search + trajectory + verifier 的完整 reasoning report；
                # 若本轮几乎没有 search 信号，才退回普通 final report。
                final_report = (
                    self.finalize_from_search(session_id, search_result)
                    if self._has_search_signal(search_result)
                    else self.deps.report_builder.build_final_report(tracker.get_session(session_id), stop_decision)
                )
            return {
                "session_id": session_id,
                "turn_index": turn_index,
                "patient_text": patient_text,
                "patient_context": asdict(patient_context),
                "linked_entities": [asdict(item) for item in linked_entities],
                "a1": asdict(a1_result),
                "a2": asdict(a2_result),
                "a2_evidence_profiles": a2_evidence_profiles,
                "a3": asdict(A3VerificationResult()),
                "pending_action_result": (
                    asdict(pending_action_result) if pending_action_result is not None else None
                ),
                "pending_action_decision": (
                    asdict(pending_action_decision) if pending_action_decision is not None else None
                ),
                "route_after_pending_action": (
                    asdict(route_after_pending_action) if route_after_pending_action is not None else None
                ),
                "route_after_slot_update": asdict(route_after_slot_update),
                "updates": [asdict(item) for item in applied_updates],
                "pending_action_audit": tracker.get_session(session_id).metadata.get("last_pending_action_audit"),
                "search_report": (
                    self.deps.report_builder.build_search_report(tracker.get_session(session_id), search_result)
                    if len(search_result.trajectories) > 0 or search_result.selected_action is not None
                    else None
                ),
                "next_question": None,
                "pending_action": None,
                "final_report": final_report,
            }

        # 走到这里说明本轮还要继续问；A3 result 负责把 selected_action 渲染成面向患者的自然语言问题。
        a3_rationale = (
            "当前输入尚未包含明确症状、风险因素或检查结果，系统先进行主诉澄清。"
            if selected_action is not None and selected_action.action_type == "collect_chief_complaint"
            else "已结合 R2 检索、UCT 评分与局部 rollout 选择当前动作。"
        )
        a3_result = self.deps.action_builder.build_a3_verification_result(selected_action, rationale=a3_rationale)

        if selected_action is not None:
            if len(a3_result.question_text.strip()) > 0:
                selected_action.metadata = {
                    **dict(selected_action.metadata),
                    "question_text": str(selected_action.metadata.get("question_text") or a3_result.question_text),
                }
            # pending_action 是多轮闭环的关键：下一轮系统要靠它判断患者这句话在回答什么。
            # 同时还要记录“已经问过这个节点”，避免后续 R2/search 再次把同一问题当作高优先级动作选出来。
            tracker.mark_question_asked(session_id, selected_action.target_node_id)
            tracker.get_session(session_id).metadata["last_selected_action"] = selected_action
            tracker.set_pending_action(session_id, selected_action)

            if selected_action.topic_id is not None:
                # topic 只在继续追问时激活，用于表示当前问诊仍围绕哪个 hypothesis / 主题展开。
                tracker.activate_topic(session_id, selected_action.topic_id)

        return {
            "session_id": session_id,
            "turn_index": turn_index,
            "patient_text": patient_text,
            "patient_context": asdict(patient_context),
            "linked_entities": [asdict(item) for item in linked_entities],
            "a1": asdict(a1_result),
            "a2": asdict(a2_result),
            "a2_evidence_profiles": a2_evidence_profiles,
            "a3": asdict(a3_result),
            "pending_action_result": asdict(pending_action_result) if pending_action_result is not None else None,
            "pending_action_decision": (
                asdict(pending_action_decision) if pending_action_decision is not None else None
            ),
            "route_after_pending_action": (
                asdict(route_after_pending_action) if route_after_pending_action is not None else None
            ),
            "route_after_slot_update": asdict(route_after_slot_update),
            "updates": [asdict(item) for item in applied_updates],
            "pending_action_audit": tracker.get_session(session_id).metadata.get("last_pending_action_audit"),
            "search_report": (
                self.deps.report_builder.build_search_report(tracker.get_session(session_id), search_result)
                if len(search_result.trajectories) > 0 or search_result.selected_action is not None
                else None
            ),
            "next_question": a3_result.question_text,
            "pending_action": asdict(selected_action) if selected_action is not None else None,
            "final_report": None,
        }

    # 将上一轮动作解释写成逐轮审计记录，用来定位“问到了但没有进入 confirmed family”的断点。
    def _record_pending_action_audit(
        self,
        session_id: str,
        action: MctsAction,
        evidence_state: EvidenceState,
        pending_action_result: PendingActionResult,
        patient_text: str,
        turn_index: int,
    ) -> None:
        state = self.deps.state_tracker.get_session(session_id)
        evidence_tags = self._infer_action_evidence_tags(action)
        semantic_families = sorted(tag for tag in evidence_tags if not tag.startswith("type:"))
        confirmed_family_candidate = bool(evidence_state.metadata.get("confirmed_family_candidate", False))
        provisional_family_candidate = bool(evidence_state.metadata.get("provisional_family_candidate", False))
        entry = {
            "turn_index": turn_index,
            "action_id": action.action_id,
            "action_type": action.action_type,
            "target_node_id": action.target_node_id,
            "target_node_name": action.target_node_name,
            "target_node_label": action.target_node_label,
            "hypothesis_id": action.hypothesis_id,
            "topic_id": action.topic_id,
            "patient_answer": patient_text,
            "polarity": pending_action_result.polarity,
            "resolution": pending_action_result.resolution,
            "reasoning": pending_action_result.reasoning,
            "supporting_span": pending_action_result.supporting_span,
            "negation_span": pending_action_result.negation_span,
            "uncertain_span": pending_action_result.uncertain_span,
            "relation_type": str(action.metadata.get("relation_type") or ""),
            "question_type_hint": str(action.metadata.get("question_type_hint") or ""),
            "evidence_tags": sorted(evidence_tags),
            "evidence_families": semantic_families,
            "confirmed_family_candidate": confirmed_family_candidate,
            "confirmed_family_candidates": sorted(evidence_tags & GUARDED_CONFIRMED_EVIDENCE_TAGS),
            "provisional_family_candidate": provisional_family_candidate,
            "provisional_family_candidates": sorted(
                evidence_tags & {"imaging", "oxygenation", "pathogen", "immune_status", "pcp_specific"}
            ),
            "entered_confirmed_family": confirmed_family_candidate,
        }
        history = state.metadata.get("pending_action_audit_history", [])

        if not isinstance(history, list):
            history = []

        history.append(entry)
        state.metadata["last_pending_action_audit"] = entry
        state.metadata["pending_action_audit_history"] = history[-48:]

    # 待处理动作的证据标签必须比动作 metadata 更鲁棒；节点名可兜底识别 CD4、β-D、PCR、CT 等锚点。
    def _infer_action_evidence_tags(self, action: MctsAction) -> set[str]:
        tags = {
            item
            for item in self._normalize_string_list(action.metadata.get("evidence_tags", []))
            if len(item) > 0
        }
        text = self._normalize_match_text(
            " ".join(
                [
                    action.target_node_id,
                    action.target_node_name,
                    action.target_node_label,
                    str(action.metadata.get("relation_type") or ""),
                    str(action.metadata.get("question_type_hint") or ""),
                ]
            )
        )
        tag_rules = {
            "immune_status": ("hiv", "cd4", "t淋巴", "免疫", "艾滋", "机会性感染", "免疫抑制"),
            "imaging": ("ct", "影像", "x线", "胸片", "磨玻璃", "双肺"),
            "oxygenation": ("低氧", "血氧", "pao2", "氧分压", "氧合", "呼吸衰竭"),
            "respiratory": ("发热", "干咳", "咳嗽", "呼吸困难", "气促"),
            "pathogen": ("βd葡聚糖", "bdg", "葡聚糖", "病原", "痰", "balf", "pcr", "核酸", "支气管肺泡"),
            "pcp_specific": ("肺孢子", "pcp", "pneumocystis", "βd葡聚糖", "bdg", "葡聚糖", "支气管肺泡"),
            "tuberculosis": ("结核", "盗汗", "抗酸", "分枝杆菌", "tb", "tspot", "tspot.tb", "xpert"),
            "systemic": ("皮疹", "咽痛", "关节", "腹泻", "淋巴结"),
            "risk": ("高危", "性行为", "接触史", "暴露"),
        }

        for tag, keywords in tag_rules.items():
            if any(keyword in text for keyword in keywords):
                tags.add(tag)

        anchor_families = self._anchor_action_families_from_text(text)

        if len(anchor_families) > 0:
            semantic_family_tags = {
                "imaging",
                "oxygenation",
                "pathogen",
                "immune_status",
                "pcp_specific",
                "tuberculosis",
                "respiratory",
                "risk",
                "systemic",
                "viral",
            }
            type_tags = {tag for tag in tags if tag.startswith("type:")}
            non_family_tags = {
                tag
                for tag in tags
                if not tag.startswith("type:") and tag not in semantic_family_tags
            }
            tags = type_tags | non_family_tags | anchor_families

        question_type_hint = str(action.metadata.get("question_type_hint") or "").strip()

        if len(question_type_hint) > 0:
            tags.add(f"type:{question_type_hint}")

        return tags

    # 与 stop rule 的 promotion allowlist 保持一致，避免 CD4 这类节点继承错误 family。
    def _anchor_action_families_from_text(self, normalized_text: str) -> set[str]:
        anchor_rules: list[tuple[set[str], tuple[str, ...]]] = [
            ({"immune_status"}, ("cd4", "t淋巴", "hiv感染", "艾滋", "免疫抑制")),
            ({"imaging"}, ("胸部ct", "ct检查", "ct磨玻璃", "磨玻璃", "胸片", "影像")),
            ({"oxygenation"}, ("pao2", "spo2", "氧分压", "低氧", "氧合", "呼吸衰竭")),
            ({"pathogen", "pcp_specific"}, ("βd葡聚糖", "bdg", "葡聚糖", "g试验")),
            ({"pathogen", "pcp_specific"}, ("肺孢子pcr", "pcppcr", "肺孢子核酸", "支气管肺泡", "balf", "bal")),
            ({"tuberculosis"}, ("tspot", "tspot.tb", "t-spot", "xpert", "mtb/rif", "抗酸", "分枝杆菌")),
        ]

        for families, keywords in anchor_rules:
            if any(keyword in normalized_text for keyword in keywords):
                return set(families)

        return set()

    # 判断当前上一轮动作结果是否具备被 guarded gate 计入 confirmed family 的基础条件。
    def _is_confirmed_family_candidate(
        self,
        action: MctsAction,
        pending_action_result: PendingActionResult,
        evidence_tags: set[str],
    ) -> bool:
        if pending_action_result.polarity != "present" or pending_action_result.resolution != "clear":
            return False

        relation_type = str(action.metadata.get("relation_type") or "")
        return relation_type in GUARDED_DEFINITION_RELATION_TYPES or bool(
            evidence_tags & GUARDED_CONFIRMED_EVIDENCE_TAGS
        )

    # 高价值 anchor 的 present + hedged 可以进入 provisional family，但仍不等同 confirmed。
    def _is_provisional_family_candidate(
        self,
        pending_action_result: PendingActionResult,
        evidence_tags: set[str],
    ) -> bool:
        if pending_action_result.polarity != "present" or pending_action_result.resolution != "hedged":
            return False

        return bool(evidence_tags & {"imaging", "oxygenation", "pathogen", "immune_status", "pcp_specific"})

    # 根据上一轮动作结果将 reward 反馈给 MCTS 动作统计。
    def _record_action_reward(
        self,
        session_id: str,
        action: MctsAction,
        pending_action_result: PendingActionResult,
    ) -> None:
        reward = 0.0

        if pending_action_result.polarity == "present" and pending_action_result.resolution == "clear":
            reward = 1.0
        elif pending_action_result.polarity == "present" and pending_action_result.resolution == "hedged":
            reward = 0.5
        elif pending_action_result.polarity == "absent" and pending_action_result.resolution == "clear":
            reward = -0.4
        elif pending_action_result.polarity == "absent" and pending_action_result.resolution == "hedged":
            reward = -0.1

        self.deps.state_tracker.record_action_feedback(
            session_id,
            action.action_id,
            reward,
            {"hypothesis_id": action.hypothesis_id},
        )

    # 将 evidence_state 反馈回当前假设分数。
    def _apply_hypothesis_feedback(
        self,
        session_id: str,
        action: MctsAction,
        evidence_state: EvidenceState,
    ) -> None:
        state = self.deps.state_tracker.get_session(session_id)

        if len(state.candidate_hypotheses) == 0:
            return

        related_ids = [action.hypothesis_id] if action.hypothesis_id is not None else None
        updated = self.deps.hypothesis_manager.apply_evidence_feedback(
            state.candidate_hypotheses,
            evidence_state,
            related_ids,
        )
        self.deps.state_tracker.set_candidate_hypotheses(session_id, updated)

    # 从当前槽位状态中收集已知特征名称，辅助 A1 进行更保守的抽取。
    def _collect_known_feature_names(self, session_id: str) -> list[str]:
        state = self.deps.state_tracker.get_session(session_id)
        names: list[str] = []

        for mention in state.mention_context.values():
            if mention.normalized_name not in names:
                names.append(mention.normalized_name)

            display_name = str(mention.display_name).strip()
            if len(display_name) > 0 and display_name not in names:
                names.append(display_name)

        for slot in state.slots.values():
            if slot.node_id not in names:
                names.append(slot.node_id)

            normalized_name = slot.metadata.get("normalized_name")

            if isinstance(normalized_name, str) and normalized_name not in names:
                names.append(normalized_name)

        return names

    # 确保当前会话绑定了一棵搜索树，并在首次搜索时创建根节点。
    def _ensure_search_tree(self, session_id: str, state: SessionState) -> SearchTree:
        tracker = self.deps.state_tracker
        tree = tracker.get_bound_search_tree(session_id)
        top_hypothesis_id = self._get_top_hypothesis_id(state)
        root_signature = self.deps.mcts_engine.build_state_signature(state, top_hypothesis_id)
        force_tree_refresh = bool(state.metadata.pop("force_tree_refresh", False))
        enable_tree_reroot = bool(self.deps.repair_policy.enable_tree_reroot)
        rerooted = False
        reroot_reason = ""

        # 若已有树，优先判断这棵树是否还能复用：
        # 只有状态签名和当前 top hypothesis 都一致时，才直接沿用旧树。
        if tree is not None and tree.root_id is not None:
            root = tree.get_node(tree.root_id)
            root_top_hypothesis_id = str(root.metadata.get("top_hypothesis_id") or "") or None

            if not force_tree_refresh and root.state_signature == root_signature and root_top_hypothesis_id == top_hypothesis_id:
                state.metadata["last_tree_refresh"] = {
                    "rerooted": False,
                    "reason": "",
                    "root_signature": root_signature,
                    "top_hypothesis_id": top_hypothesis_id,
                }
                return tree

            # repair 或 hypothesis 排名变化后若禁用了 reroot，则保留旧树但显式记录原因。
            if not enable_tree_reroot:
                state.metadata["last_tree_refresh"] = {
                    "rerooted": False,
                    "reason": "reroot_disabled",
                    "root_signature": root.state_signature,
                    "top_hypothesis_id": root_top_hypothesis_id,
                }
                return tree

        # 只要强制 refresh、状态签名变化或 top hypothesis 改了，就重建 root。
        if force_tree_refresh:
            rerooted = True
            reroot_reason = state.metadata.get("tree_refresh_reason", "forced_refresh")
            state.metadata["tree_refresh_reason"] = reroot_reason
        elif tree is not None:
            rerooted = True
            reroot_reason = state.metadata.get("tree_refresh_reason", "state_signature_changed")
            state.metadata["tree_refresh_reason"] = reroot_reason

        # 新 root 会缓存一份 rollout_session_copy，
        # 后续每个叶子展开都从它的轻量快照恢复分支上下文。
        tree = SearchTree()
        tree.add_node(
            TreeNode(
                node_id=f"root::{root_signature}",
                state_signature=root_signature,
                parent_id=None,
                action_from_parent=None,
                stage="A2",
                depth=0,
                metadata={
                    "session_id": session_id,
                    "rollout_state": tracker.get_rollout_session_copy(session_id),
                    "top_hypothesis_id": top_hypothesis_id,
                },
            )
        )
        tracker.bind_search_tree(session_id, tree)
        state.metadata["last_tree_refresh"] = {
            "rerooted": rerooted,
            "reason": reroot_reason,
            "root_signature": root_signature,
            "top_hypothesis_id": top_hypothesis_id,
        }
        return tree

    # 从当前状态中读取 top1 hypothesis id，辅助搜索树决定是否需要换根。
    def _get_top_hypothesis_id(self, state: SessionState) -> str | None:
        if len(state.candidate_hypotheses) == 0:
            return None

        ranked = sorted(state.candidate_hypotheses, key=lambda item: (-item.score, item.name))
        return ranked[0].node_id

    # 从叶子节点恢复 rollout 上下文，包括当前分支状态与主备选假设。
    def _build_rollout_context_from_leaf(self, session_id: str, leaf: TreeNode) -> dict:
        tracker = self.deps.state_tracker
        base_state = leaf.metadata.get("rollout_state")

        if isinstance(base_state, SessionState):
            rollout_state = deepcopy(base_state)
        else:
            rollout_state = tracker.get_rollout_session_copy(session_id)

        hypothesis_id = str(leaf.metadata.get("final_answer_id") or leaf.metadata.get("hypothesis_id") or "")
        current_hypothesis = self._find_hypothesis_by_id(rollout_state.candidate_hypotheses, hypothesis_id)

        if current_hypothesis is None and len(rollout_state.candidate_hypotheses) > 0:
            current_hypothesis = sorted(
                rollout_state.candidate_hypotheses,
                key=lambda item: (-item.score, item.name),
            )[0]

        alternatives = [
            item
            for item in rollout_state.candidate_hypotheses
            if current_hypothesis is None or item.node_id != current_hypothesis.node_id
        ]
        return {
            "state": rollout_state,
            "current_hypothesis": current_hypothesis,
            "alternatives": alternatives,
        }

    # 根据当前叶子所处路径扩展下一批 A3 验证动作。
    def _expand_actions_for_leaf(self, leaf: TreeNode, rollout_context: dict) -> list[MctsAction]:
        _ = leaf
        rollout_state: SessionState = rollout_context["state"]
        current_hypothesis = rollout_context["current_hypothesis"]
        alternatives = rollout_context["alternatives"]

        if current_hypothesis is None:
            return []

        rows = self.deps.retriever.retrieve_r2_expected_evidence(current_hypothesis, rollout_state)
        actions = self.deps.action_builder.build_verification_actions(
            rows,
            hypothesis_id=current_hypothesis.node_id,
            topic_id=current_hypothesis.label,
            competing_hypotheses=alternatives,
            current_hypothesis=current_hypothesis,
            session_state=rollout_state,
        )
        return actions[: self.deps.mcts_engine.config.max_child_nodes]

    # 根据节点 id 从当前候选假设中找到对应对象。
    def _find_hypothesis_by_id(
        self,
        hypotheses: Sequence[object],
        hypothesis_id: str | None,
    ) -> object | None:
        if hypothesis_id is None:
            return None

        for hypothesis in hypotheses:
            if getattr(hypothesis, "node_id", None) == hypothesis_id:
                return hypothesis

        return None

    # 将 pending_action 的直接 STOP 先降级为继续搜索，由 verifier 再决定是否真正终止。
    def _gate_pending_action_route(self, route: RouteDecision | None) -> RouteDecision | None:
        if route is None or route.stage != "STOP":
            return route

        return RouteDecision(
            stage="A3",
            reason="上一轮动作解释给出终止倾向，但系统会先经过 search + verifier 二次确认后再真正停止。",
            next_topic_id=route.next_topic_id,
            next_hypothesis_id=route.next_hypothesis_id,
            metadata={
                **dict(route.metadata),
                "proposed_stage": "STOP",
                "gated_by_verifier": True,
            },
        )

    # 根据 verifier 的拒停信息构造 repair 分流上下文。
    def _build_verifier_repair_context(
        self,
        session_id: str,
        search_result: SearchResult,
        best_answer_score: FinalAnswerScore | None,
        accept_decision: StopDecision,
    ) -> dict | None:
        # 已经允许停止，或压根没有 best answer 时，就不需要 repair。
        if best_answer_score is None or accept_decision.should_stop:
            return None

        metadata = dict(best_answer_score.metadata)
        guarded_blocked = accept_decision.reason == "guarded_acceptance_rejected"

        # 只有 llm_verifier 明确拒停，或 guarded gate 挡下 verifier 的“可停”建议时，才进入 repair。
        if metadata.get("verifier_mode") != "llm_verifier":
            return None

        if bool(metadata.get("verifier_should_accept", True)) and not guarded_blocked:
            return None

        state = self.deps.state_tracker.get_session(session_id)
        reject_reason = (
            str(accept_decision.metadata.get("repair_reject_reason", "")).strip()
            or str(metadata.get("verifier_reject_reason", "")).strip()
            or "missing_key_support"
        )
        current_hypothesis = self._find_hypothesis_by_id(state.candidate_hypotheses, search_result.best_answer_id)
        recommended_next_evidence = self._normalize_string_list(metadata.get("verifier_recommended_next_evidence", []))
        guarded_block_reason = str(
            accept_decision.metadata.get("guarded_acceptance_block_reason")
            or metadata.get("guarded_acceptance_block_reason")
            or ""
        )
        guarded_features = {
            key: value
            for key, value in metadata.items()
            if key.startswith("guarded_")
        }
        guarded_missing_families = self._normalize_string_list(
            guarded_features.get("guarded_missing_evidence_families", [])
        )
        guarded_family_recommendations = self._recommended_evidence_for_guarded_families(
            guarded_missing_families,
            current_answer_name=best_answer_score.answer_name,
        )

        # 当前 hypothesis 自己可能已经带有 recommended_next_evidence；
        # 这里和 verifier 推荐缺口合并，避免 repair 丢掉任何一侧信号。
        if current_hypothesis is not None:
            recommended_next_evidence = self._merge_unique_strings(
                recommended_next_evidence,
                self._normalize_string_list(current_hypothesis.metadata.get("recommended_next_evidence", [])),
            )

        alternative_candidates = self._normalize_alternative_candidates(metadata.get("verifier_alternative_candidates", []))
        guarded_strong_alternatives = self._normalize_alternative_candidates(
            guarded_features.get("guarded_strong_alternative_candidates", [])
        )

        # guarded gate 若明确指出是强备选未排除，就优先用 guarded 解析出的强竞争者覆盖普通 alternatives。
        if guarded_block_reason == "strong_unresolved_alternative_candidates" and len(guarded_strong_alternatives) > 0:
            alternative_candidates = guarded_strong_alternatives

        # 若 verifier 没给出具体 alternatives，但拒停理由就是 strong alternative，
        # 则从当前 hypothesis 排名里兜底构造几个强备选，保证 repair 仍有目标可追。
        if self._is_alternative_repair_reason(reject_reason) and len(alternative_candidates) == 0:
            alternative_candidates = [
                {
                    "answer_id": item.node_id,
                    "answer_name": item.name,
                    "reason": "来自当前 hypothesis 排名中的强备选候选。",
                }
                for item in state.candidate_hypotheses[1:3]
            ]

        if reject_reason in {"missing_key_support", "hard_negative_key_evidence"} and len(recommended_next_evidence) == 0:
            recommended_next_evidence = self._normalize_string_list(metadata.get("verifier_missing_evidence", []))[:3]

        # guarded gate 给出的 family 缺口优先级很高，必要时把它们前置合并进推荐证据。
        if guarded_block_reason in {"pcp_combo_insufficient", "missing_confirmed_key_evidence"}:
            recommended_next_evidence = self._merge_unique_strings(
                guarded_family_recommendations,
                recommended_next_evidence,
            )

        return {
            "reject_reason": reject_reason,
            "recommended_next_evidence": recommended_next_evidence,
            "alternative_candidates": alternative_candidates,
            "verifier_reasoning": str(metadata.get("verifier_reasoning", "")),
            "verifier_reject_reason_source": str(metadata.get("verifier_reject_reason_source", "")),
            "verifier_schema_valid": bool(metadata.get("verifier_schema_valid", False)),
            "guarded_acceptance_blocked": guarded_blocked,
            "guarded_acceptance_block_reason": guarded_block_reason,
            "guarded_missing_evidence_families": guarded_missing_families,
            "guarded_family_recommendations": guarded_family_recommendations,
            "guarded_confirmed_evidence_families": self._normalize_string_list(
                guarded_features.get("guarded_confirmed_key_evidence_families", [])
            ),
            "guarded_hard_negative_key_evidence": guarded_features.get("guarded_hard_negative_key_evidence", []),
            "guarded_strong_alternative_candidates": guarded_strong_alternatives,
            "guarded_acceptance_features": guarded_features,
            "force_tree_refresh": True,
            "repair_stage": self._map_reject_reason_to_stage(reject_reason),
            "current_answer_id": best_answer_score.answer_id,
            "current_answer_name": best_answer_score.answer_name,
        }

    # 将 verifier 拒停结果显式写回 hypothesis 排序与会话元数据。
    def _apply_verifier_repair_strategy(self, session_id: str, repair_context: dict) -> None:
        state = self.deps.state_tracker.get_session(session_id)
        current_top_hypothesis_id = self._get_top_hypothesis_id(state)
        reject_reason = str(repair_context.get("reject_reason", "missing_key_support"))

        if bool(self.deps.repair_policy.enable_verifier_hypothesis_reshuffle) and len(state.candidate_hypotheses) > 0:
            updated = self.deps.hypothesis_manager.apply_verifier_repair(
                state.candidate_hypotheses,
                current_answer_id=repair_context.get("current_answer_id"),
                reject_reason=reject_reason,
                recommended_next_evidence=self._normalize_string_list(repair_context.get("recommended_next_evidence", [])),
                alternative_candidates=self._normalize_alternative_candidates(repair_context.get("alternative_candidates", [])),
            )
            self.deps.state_tracker.set_candidate_hypotheses(session_id, updated)

        refreshed_state = self.deps.state_tracker.get_session(session_id)
        new_top_hypothesis_id = self._get_top_hypothesis_id(refreshed_state)
        refreshed_state.metadata["verifier_repair_context"] = dict(repair_context)
        refreshed_state.metadata["force_tree_refresh"] = bool(
            self.deps.repair_policy.enable_tree_reroot and repair_context.get("force_tree_refresh", True)
        )

        if current_top_hypothesis_id != new_top_hypothesis_id:
            refreshed_state.metadata["tree_refresh_reason"] = "top_hypothesis_changed_after_verifier"
        else:
            refreshed_state.metadata["tree_refresh_reason"] = f"verifier_reject::{reject_reason}"

    # 在 verifier 拒停之后，显式选择修补证据缺口能力更强的下一问。
    def _choose_repair_action(
        self,
        session_id: str,
        search_result: SearchResult,
        repair_context: dict,
    ) -> MctsAction | None:
        state = self.deps.state_tracker.get_session(session_id)
        reject_reason = str(repair_context.get("reject_reason", "missing_key_support"))
        current_hypothesis = self._select_current_repair_hypothesis(state, repair_context)

        # 如果连当前 repair hypothesis 都选不出来，就退回 search 原本的 root action。
        if current_hypothesis is None:
            return self.choose_next_question_from_search(session_id, search_result)

        # repair 有时只围绕 current answer 补证据，
        # 有时要把 verifier 指出的强 alternatives 也纳入候选动作池。
        repair_hypotheses = self._select_repair_hypotheses(
            state,
            current_hypothesis=current_hypothesis,
            repair_context=repair_context,
        )
        actions: list[MctsAction] = []
        seen_action_keys: set[tuple[str, str]] = set()

        for hypothesis in repair_hypotheses:
            action_hypothesis = self._attach_repair_recommendations_to_hypothesis(hypothesis, repair_context)
            alternatives = [
                item
                for item in state.candidate_hypotheses
                if item.node_id != action_hypothesis.node_id
            ][: self.deps.hypothesis_manager.config.expand_top_k_hypotheses]
            rows = self.deps.retriever.retrieve_r2_expected_evidence(action_hypothesis, state)

            # 每个 hypothesis 都重新跑一轮 R2 + action_builder，
            # 这样 repair 得到的动作仍然遵守正常搜索链路的排序与 exam_context 门控。
            for action in self.deps.action_builder.build_verification_actions(
                rows,
                hypothesis_id=action_hypothesis.node_id,
                topic_id=action_hypothesis.label,
                competing_hypotheses=alternatives,
                current_hypothesis=action_hypothesis,
                session_state=state,
            ):
                action_key = (action.hypothesis_id or "", action.target_node_id)

                if action_key in seen_action_keys:
                    continue

                seen_action_keys.add(action_key)
                actions.append(action)

        # repair 只看尚未真正问过的节点，避免 verifier 一直把会话推回同一个问题。
        available_actions = [
            item
            for item in actions
            if item.target_node_id not in state.asked_node_ids
        ]

        if len(available_actions) == 0:
            return self.choose_next_question_from_search(session_id, search_result)

        recent_question_type = self._get_recent_question_type(state)
        recent_evidence_tags = self._get_recent_evidence_tags(state)
        ranked = sorted(
            available_actions,
            key=lambda item: (
                -self._score_repair_action(
                    item,
                    repair_context,
                    recent_question_type=recent_question_type,
                    recent_evidence_tags=recent_evidence_tags,
                ),
                -item.prior_score,
                item.target_node_name,
            ),
        )
        return ranked[0]

    # 将 verifier repair_context 中的推荐证据临时注入 A3 动作构造，避免局部 repair 选择丢失缺口信号。
    def _attach_repair_recommendations_to_hypothesis(
        self,
        hypothesis: HypothesisScore,
        repair_context: dict,
    ) -> HypothesisScore:
        verifier_evidence = self._normalize_string_list(repair_context.get("recommended_next_evidence", []))

        if len(verifier_evidence) == 0:
            return hypothesis

        metadata = dict(hypothesis.metadata)
        hypothesis_evidence = self._normalize_string_list(metadata.get("hypothesis_recommended_next_evidence", []))

        if len(hypothesis_evidence) == 0:
            hypothesis_evidence = self._normalize_string_list(metadata.get("recommended_next_evidence", []))

        metadata.update(
            {
                "hypothesis_recommended_next_evidence": hypothesis_evidence,
                "verifier_recommended_next_evidence": verifier_evidence,
                "recommended_next_evidence": self._merge_unique_strings(hypothesis_evidence, verifier_evidence),
            }
        )
        return HypothesisScore(
            node_id=hypothesis.node_id,
            label=hypothesis.label,
            name=hypothesis.name,
            score=hypothesis.score,
            evidence_node_ids=list(hypothesis.evidence_node_ids),
            metadata=metadata,
        )

    # guarded gate 指明当前答案缺关键 family 时，repair 优先修当前答案，避免被 reshuffle 后的 top1 带跑。
    def _select_current_repair_hypothesis(
        self,
        state: SessionState,
        repair_context: dict,
    ) -> HypothesisScore | None:
        if len(state.candidate_hypotheses) == 0:
            return None

        guarded_block_reason = str(repair_context.get("guarded_acceptance_block_reason") or "")

        if guarded_block_reason in {
            "pcp_combo_insufficient",
            "missing_confirmed_key_evidence",
            "hard_negative_key_evidence",
        } or str(repair_context.get("reject_reason") or "") == "hard_negative_key_evidence":
            current_answer_id = str(repair_context.get("current_answer_id") or "")
            current_answer = self._find_hypothesis_by_id(state.candidate_hypotheses, current_answer_id)

            if current_answer is not None:
                return current_answer

        return sorted(state.candidate_hypotheses, key=lambda item: (-item.score, item.name))[0]

    # 在 repair 阶段决定当前要从哪些 hypothesis 上取下一批候选动作。
    def _select_repair_hypotheses(
        self,
        state: SessionState,
        current_hypothesis: HypothesisScore,
        repair_context: dict,
    ) -> list[HypothesisScore]:
        reject_reason = str(repair_context.get("reject_reason", "missing_key_support"))
        ranked = sorted(state.candidate_hypotheses, key=lambda item: (-item.score, item.name))
        selected: list[HypothesisScore] = [current_hypothesis]

        if not self._is_alternative_repair_reason(reject_reason):
            return selected

        alternative_candidates = self._normalize_alternative_candidates(repair_context.get("alternative_candidates", []))

        for hypothesis in ranked:
            if hypothesis.node_id == current_hypothesis.node_id:
                continue

            if self._matches_repair_alternative(hypothesis, alternative_candidates):
                selected.append(hypothesis)

        if len(selected) == 1:
            for hypothesis in ranked:
                if hypothesis.node_id == current_hypothesis.node_id:
                    continue

                selected.append(hypothesis)

                if len(selected) >= 3:
                    break

        return selected[:3]

    # 判断某个 hypothesis 是否命中了 verifier 指出的强备选候选。
    def _matches_repair_alternative(
        self,
        hypothesis: HypothesisScore,
        alternative_candidates: list[dict],
    ) -> bool:
        normalized_name = self._normalize_match_text(hypothesis.name)

        for item in alternative_candidates:
            answer_id = str(item.get("answer_id") or "").strip()
            answer_name = self._normalize_match_text(str(item.get("answer_name") or ""))

            if len(answer_id) > 0 and answer_id == hypothesis.node_id:
                return True

            if len(answer_name) == 0:
                continue

            if answer_name == normalized_name or answer_name in normalized_name or normalized_name in answer_name:
                return True

        return False

    # 按 verifier 揭示的缺口类型计算 repair score。
    def _score_repair_action(
        self,
        action: MctsAction,
        repair_context: dict,
        recent_question_type: str | None,
        recent_evidence_tags: list[str],
    ) -> float:
        reject_reason = str(repair_context.get("reject_reason", "missing_key_support"))

        # repair score 会同时读取：
        # - action_builder 已算好的区分度/新颖度/推荐命中度
        # - guarded gate 给出的 family 缺口
        # - 最近几轮提问类型和证据 family，避免 repair 自己陷入重复追问
        discriminative_gain = float(action.metadata.get("discriminative_gain", 0.0))
        novelty_score = float(action.metadata.get("novelty_score", 0.0))
        recommended_bonus = float(action.metadata.get("recommended_evidence_bonus", 0.0))
        recommended_match_score = float(action.metadata.get("recommended_match_score", 0.0))
        verifier_recommended_match_score = float(action.metadata.get("verifier_recommended_match_score", 0.0))
        hypothesis_recommended_match_score = float(action.metadata.get("hypothesis_recommended_match_score", 0.0))
        joint_recommended_match_score = float(action.metadata.get("joint_recommended_match_score", 0.0))
        alternative_overlap = float(action.metadata.get("alternative_overlap", 0.0))
        patient_burden = float(action.metadata.get("patient_burden", 0.0))
        repair_cost_bias = self._repair_cost_bias(action)
        question_type_hint = str(action.metadata.get("question_type_hint", "symptom"))
        evidence_tags = self._normalize_string_list(action.metadata.get("evidence_tags", []))
        semantic_evidence_tags = {item for item in evidence_tags if not item.startswith("type:")}
        recent_semantic_evidence_tags = {item for item in recent_evidence_tags if not item.startswith("type:")}
        guarded_missing_families = {
            item
            for item in self._normalize_string_list(repair_context.get("guarded_missing_evidence_families", []))
            if len(item) > 0
        }
        guarded_confirmed_families = {
            item
            for item in self._normalize_string_list(repair_context.get("guarded_confirmed_evidence_families", []))
            if len(item) > 0
        }
        guarded_block_reason = str(repair_context.get("guarded_acceptance_block_reason") or "")
        guarded_family_match = len(semantic_evidence_tags & guarded_missing_families) > 0
        guarded_family_match_score = (
            len(semantic_evidence_tags & guarded_missing_families) / max(len(guarded_missing_families), 1)
            if len(guarded_missing_families) > 0
            else 0.0
        )
        pcp_combo_priority_bonus = 0.0
        missing_family_priority_bonus = 0.0
        non_missing_family_penalty = 0.0
        combo_anchor_bonus = self._combo_anchor_bonus(action, semantic_evidence_tags, guarded_missing_families)
        has_core_combo_gap = len(guarded_missing_families & {"immune_status", "pathogen", "pcp_specific"}) > 0
        repeats_already_confirmed_resp_family = len(
            semantic_evidence_tags & guarded_confirmed_families & {"imaging", "oxygenation", "respiratory"}
        ) > 0
        is_resp_or_oxygen_action = len(semantic_evidence_tags & {"respiratory", "oxygenation"}) > 0

        # 对 PCP combo / missing family 这类 guarded 缺口，优先把真正能补核心 family 的动作大幅前推。
        if guarded_block_reason == "pcp_combo_insufficient" and guarded_family_match:
            pcp_combo_priority_bonus = 3.0

            if len(semantic_evidence_tags & {"immune_status", "pathogen", "pcp_specific"}) > 0:
                pcp_combo_priority_bonus += 4.25

        if guarded_block_reason in {"pcp_combo_insufficient", "missing_confirmed_key_evidence"}:
            if guarded_family_match:
                missing_family_priority_bonus = 4.5 + guarded_family_match_score * 4.0

            if has_core_combo_gap and is_resp_or_oxygen_action and not guarded_family_match:
                non_missing_family_penalty += 4.75

            if guarded_block_reason == "pcp_combo_insufficient" and repeats_already_confirmed_resp_family:
                non_missing_family_penalty += 3.25

            if combo_anchor_bonus == 0.0 and has_core_combo_gap and not guarded_family_match:
                non_missing_family_penalty += 1.75

        score = action.prior_score
        type_diversity_bonus = 0.35 if recent_question_type is not None and question_type_hint != recent_question_type else 0.0
        same_type_penalty = 0.2 if recent_question_type is not None and question_type_hint == recent_question_type else 0.0
        shared_evidence_family = len(recent_semantic_evidence_tags & semantic_evidence_tags) > 0
        family_diversity_bonus = 0.35 if len(recent_semantic_evidence_tags) > 0 and not shared_evidence_family else 0.0
        family_repeat_penalty = 0.3 if shared_evidence_family else 0.0

        # 不同 reject_reason 分别有不同偏好：
        # - missing_key_support：优先补 verifier / hypothesis 推荐缺口
        # - hard_negative_key_evidence：优先围绕当前答案补能化解硬反证的推荐锚点
        # - strong alternative：优先找能拉开竞争差异的动作
        # - trajectory_insufficient：优先选新颖、少重复的动作稳定路径
        if reject_reason in {"missing_key_support", "hard_negative_key_evidence"}:
            recommended_gap_score = max(
                recommended_match_score,
                joint_recommended_match_score,
                verifier_recommended_match_score * 0.9,
                hypothesis_recommended_match_score * 0.75,
            )
            hard_recommended_bonus = 0.0

            if recommended_gap_score >= 0.9:
                hard_recommended_bonus = 4.0
            elif recommended_gap_score >= 0.65:
                hard_recommended_bonus = 2.5
            elif recommended_gap_score >= 0.35:
                hard_recommended_bonus = 1.2

            if reject_reason == "hard_negative_key_evidence" and recommended_gap_score > 0.0:
                hard_recommended_bonus += 1.25

            return (
                score
                + recommended_gap_score * 5.2
                + hard_recommended_bonus
                + joint_recommended_match_score * 2.8
                + verifier_recommended_match_score * 2.1
                + hypothesis_recommended_match_score * 1.0
                + recommended_bonus * 2.4
                + guarded_family_match_score * 2.2
                + pcp_combo_priority_bonus
                + missing_family_priority_bonus
                + combo_anchor_bonus
                + repair_cost_bias
                + discriminative_gain * 0.45
                + type_diversity_bonus * 0.55
                + family_diversity_bonus * 0.85
                - same_type_penalty * 0.5
                - family_repeat_penalty * 0.85
                - non_missing_family_penalty
                - patient_burden * 0.15
            )

        if self._is_alternative_repair_reason(reject_reason):
            current_answer_id = str(repair_context.get("current_answer_id") or "")
            alternative_hypothesis_bonus = 1.05 if len(current_answer_id) > 0 and action.hypothesis_id != current_answer_id else 0.0
            competition_family_bonus = (
                0.75
                if len(
                    semantic_evidence_tags
                    & {"respiratory", "imaging", "oxygenation", "pathogen", "immune_status", "tuberculosis", "systemic"}
                )
                > 0
                else 0.0
            )
            unclassified_lab_penalty = (
                1.35
                if question_type_hint == "lab" and len(semantic_evidence_tags) == 0
                else 0.0
            )
            return (
                score
                + discriminative_gain * 2.45
                + (1.0 - alternative_overlap) * 1.05
                + recommended_match_score * 1.1
                + joint_recommended_match_score * 0.65
                + alternative_hypothesis_bonus
                + competition_family_bonus
                + repair_cost_bias
                + family_diversity_bonus * 0.75
                - same_type_penalty * 0.35
                - family_repeat_penalty * 1.75
                - unclassified_lab_penalty
                - patient_burden * 0.1
            )

        return (
            score
            + novelty_score * 2.15
            + type_diversity_bonus * 2.1
            + family_diversity_bonus * 1.75
            + repair_cost_bias
            - same_type_penalty * 1.25
            - family_repeat_penalty * 1.9
            - patient_burden * 0.08
        )

    # repair 阶段的成本偏置保持很轻：低成本略优先，高成本关键证据仍可胜出。
    def _repair_cost_bias(self, action: MctsAction) -> float:
        acquisition_mode = str(action.metadata.get("acquisition_mode") or "")
        evidence_cost = str(action.metadata.get("evidence_cost") or "")

        if action.action_type == "collect_exam_context":
            return 0.08

        if acquisition_mode in {"direct_ask", "history_known"} or evidence_cost == "low":
            return 0.18

        if acquisition_mode == "needs_clinician_assessment" or evidence_cost == "medium":
            return 0.04

        if acquisition_mode in {"needs_lab_test", "needs_imaging", "needs_pathogen_test"} or evidence_cost == "high":
            return -0.10

        return 0.0

    # PCP combo repair anchors 是能直接补齐 immune/pathogen/PCP-specific 缺口的高价值证据。
    def _combo_anchor_bonus(
        self,
        action: MctsAction,
        semantic_evidence_tags: set[str],
        missing_families: set[str],
    ) -> float:
        if len(missing_families) == 0:
            return 0.0

        normalized_name = self._normalize_match_text(action.target_node_name)
        anchor_rules = {
            "immune_status": ("cd4", "hiv", "免疫", "艾滋", "t淋巴"),
            "pathogen": ("βd葡聚糖", "bdg", "葡聚糖", "pcr", "核酸", "bal", "balf", "支气管肺泡", "病原"),
            "pcp_specific": ("肺孢子", "pcp", "pneumocystis", "βd葡聚糖", "bdg", "葡聚糖", "支气管肺泡"),
        }
        bonus = 0.0

        for family, keywords in anchor_rules.items():
            if family not in missing_families:
                continue

            tag_match = family in semantic_evidence_tags
            text_match = any(keyword in normalized_name for keyword in keywords)

            if tag_match or text_match:
                bonus += 5.5

        if len(semantic_evidence_tags & {"immune_status", "pathogen", "pcp_specific"} & missing_families) > 0:
            bonus += 1.75

        return bonus

    # 返回最近一次真实追问的 question type，用于 trajectory_insufficient 下切换问法。
    def _get_recent_question_type(self, state: SessionState) -> str | None:
        for key in ("last_answered_action", "last_selected_action"):
            action = state.metadata.get(key)

            if isinstance(action, MctsAction):
                return str(action.metadata.get("question_type_hint", "symptom"))

            if isinstance(action, dict):
                metadata = action.get("metadata", {})

                if isinstance(metadata, dict):
                    return str(metadata.get("question_type_hint", "symptom"))

        return None

    # 返回最近一次追问的证据类别标签，用于 repair-aware A3 避免围绕同一家族打转。
    def _get_recent_evidence_tags(self, state: SessionState) -> list[str]:
        for key in ("last_answered_action", "last_selected_action"):
            action = state.metadata.get(key)

            if isinstance(action, MctsAction):
                return self._normalize_string_list(action.metadata.get("evidence_tags", []))

            if isinstance(action, dict):
                metadata = action.get("metadata", {})

                if isinstance(metadata, dict):
                    return self._normalize_string_list(metadata.get("evidence_tags", []))

        return []

    # 将 verifier 拒停原因映射为更明确的 repair 阶段语义。
    def _map_reject_reason_to_stage(self, reject_reason: str) -> str:
        if self._is_alternative_repair_reason(reject_reason):
            return "A2"

        return "A3"

    # verifier 和 guarded gate 的历史枚举略有不同，这里统一判断“竞争诊断未排除”类 repair。
    def _is_alternative_repair_reason(self, reject_reason: str) -> bool:
        return reject_reason in {"strong_alternative_not_ruled_out", "strong_unresolved_alternative_candidates"}

    # 将 guarded gate 暴露出的缺失证据家族翻译成 A3 可匹配的推荐证据文本。
    def _recommended_evidence_for_guarded_families(
        self,
        missing_families: list[str],
        *,
        current_answer_name: str,
    ) -> list[str]:
        answer_hint = self._normalize_match_text(current_answer_name)
        pcp_like = any(keyword in answer_hint for keyword in ("肺孢子", "pcp", "pneumocystis"))
        family_recommendations = {
            "immune_status": [
                "CD4+ T淋巴细胞计数 < 200/μL",
                "HIV感染或免疫抑制背景",
                "近期机会性感染或免疫功能低下",
            ],
            "pathogen": [
                "血清或BAL β-D-葡聚糖",
                "诱导痰或BAL病原学检查",
                "病原学 PCR / 核酸检测",
            ],
            "pcp_specific": [
                "诱导痰或BAL 肺孢子菌 PCR",
                "肺孢子菌病原学证据",
                "β-D-葡聚糖升高",
            ],
            "oxygenation": [
                "动脉血氧分压 (PaO2) < 70 mmHg",
                "低氧血症",
                "SpO2下降或氧合异常",
            ],
            "imaging": [
                "胸部CT磨玻璃影",
                "双肺弥漫性磨玻璃影",
                "胸部影像学异常",
            ],
            "respiratory": [
                "干咳",
                "进行性呼吸困难",
                "发热伴气促",
            ],
        }
        values: list[str] = []

        for family in missing_families:
            for evidence in family_recommendations.get(family, []):
                if evidence not in values:
                    values.append(evidence)

        if pcp_like:
            for evidence in ["CD4+ T淋巴细胞计数 < 200/μL", "诱导痰或BAL 肺孢子菌 PCR", "血清或BAL β-D-葡聚糖"]:
                if evidence not in values and any(
                    family in missing_families for family in ("immune_status", "pathogen", "pcp_specific")
                ):
                    values.append(evidence)

        return values[:8]

    # 把任意列表清洗为唯一字符串列表。
    def _normalize_string_list(self, payload: object) -> list[str]:
        if not isinstance(payload, list):
            return []

        values: list[str] = []

        for item in payload:
            text = str(item).strip()

            if len(text) == 0 or text in values:
                continue

            values.append(text)

        return values

    # 对名称做轻量归一化，用于 hypothesis / verifier 候选名匹配。
    def _normalize_match_text(self, text: str) -> str:
        return (
            text.strip()
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

    # 把 verifier 的替代诊断列表标准化为 dict 列表。
    def _normalize_alternative_candidates(self, payload: object) -> list[dict]:
        if not isinstance(payload, list):
            return []

        normalized: list[dict] = []

        for item in payload:
            if isinstance(item, dict):
                answer_name = str(item.get("answer_name") or item.get("name") or "").strip()
                answer_id = str(item.get("answer_id") or item.get("node_id") or "").strip()

                if len(answer_name) == 0 and len(answer_id) == 0:
                    continue

                normalized.append(
                    {
                        "answer_id": answer_id or None,
                        "answer_name": answer_name or answer_id,
                        "reason": str(item.get("reason", "")).strip(),
                        **self._classify_alternative_candidate(item),
                    }
                )
                continue

            text = str(item).strip()

            if len(text) == 0:
                continue

            normalized.append(
                {
                    "answer_id": None,
                    "answer_name": text,
                    "reason": "",
                    "strength": "strong",
                    "is_unresolved_strong": True,
                    "strength_reason": "text_candidate_without_reason",
                }
            )

        return normalized

    # 与 guarded gate 使用同一套轻量语义规则，避免 weak alternatives 继续驱动 hypothesis repair。
    def _classify_alternative_candidate(self, item: dict) -> dict:
        raw_strength = str(
            item.get("strength")
            or item.get("confidence")
            or item.get("competition_strength")
            or item.get("risk_level")
            or ""
        ).strip().lower()
        reason = str(item.get("reason", "")).strip()
        normalized_reason = self._normalize_match_text(reason)

        if raw_strength in {"strong", "high", "unresolved", "强", "高", "强竞争"}:
            return {
                "strength": "strong",
                "is_unresolved_strong": True,
                "strength_reason": "explicit_strong",
            }

        if raw_strength in {"weak", "low", "ruled_down", "ruled-out", "弱", "低", "已排除"}:
            return {
                "strength": "weak",
                "is_unresolved_strong": False,
                "strength_reason": "explicit_weak",
            }

        strong_markers = (
            "未排除",
            "不能排除",
            "尚未排除",
            "需要排除",
            "强支持",
            "证据支持",
            "同样支持",
            "更符合",
            "高度符合",
            "主要竞争",
            "强竞争",
        )
        weak_markers = (
            "缺乏",
            "证据不足",
            "不支持",
            "不如",
            "可能性低",
            "可能性较低",
            "较不符合",
            "较弱",
            "未见",
            "没有关键证据",
            "无法解释",
            "不典型",
            "仅作为",
            "一般候选",
            "低于当前诊断",
        )

        if any(marker in normalized_reason for marker in strong_markers):
            return {
                "strength": "strong",
                "is_unresolved_strong": True,
                "strength_reason": "reason_contains_strong_unresolved_signal",
            }

        if any(marker in normalized_reason for marker in weak_markers):
            return {
                "strength": "weak",
                "is_unresolved_strong": False,
                "strength_reason": "reason_indicates_ruled_down_or_weak_candidate",
            }

        if len(normalized_reason) == 0:
            return {
                "strength": "strong",
                "is_unresolved_strong": True,
                "strength_reason": "missing_reason_treated_as_unresolved",
            }

        return {
            "strength": "medium",
            "is_unresolved_strong": False,
            "strength_reason": "no_strong_unresolved_signal",
        }

    # 合并两组字符串列表，同时保持原有顺序和唯一性。
    def _merge_unique_strings(self, left: list[str], right: list[str]) -> list[str]:
        merged = list(left)

        for item in right:
            if item not in merged:
                merged.append(item)

        return merged

    # 把 verifier repair 相关信息整理成统一、便于复盘的观测结构。
    def _build_observable_repair_context(
        self,
        search_result: SearchResult,
        repair_context: dict | None,
        selected_action: MctsAction | None,
    ) -> dict:
        tree_refresh = dict(search_result.metadata.get("tree_refresh", {}))
        root_action = search_result.root_best_action
        current_context = dict(repair_context or {})
        previous_action = root_action

        if previous_action is None and search_result.selected_action is not None and search_result.repair_selected_action is None:
            previous_action = search_result.selected_action

        if len(current_context) == 0:
            return {
                "repair_mode": "none",
                "rerooted": bool(tree_refresh.get("rerooted", False)),
                "reroot_reason": tree_refresh.get("reason", ""),
                "previous_selected_action": None,
                "new_selected_action": None,
                "reject_reason": "",
                "recommended_next_evidence": [],
                "alternative_candidates": [],
            }

        reject_reason = str(current_context.get("reject_reason", "")).strip()
        repair_mode = {
            "missing_key_support": "repair_supporting_evidence",
            "strong_alternative_not_ruled_out": "repair_hypothesis_competition",
            "strong_unresolved_alternative_candidates": "repair_hypothesis_competition",
            "hard_negative_key_evidence": "repair_hard_negative_resolution",
            "trajectory_insufficient": "repair_path_diversification",
        }.get(reject_reason, "repair_generic")

        return {
            **current_context,
            "repair_mode": repair_mode,
            "rerooted": bool(tree_refresh.get("rerooted", False)),
            "reroot_reason": tree_refresh.get("reason", ""),
            "previous_selected_action": asdict(previous_action) if previous_action is not None else None,
            "new_selected_action": asdict(selected_action) if selected_action is not None else None,
        }

    # 当输入线索过少导致 A2/A3 暂无候选动作时，退回全局冷启动问题，避免会话空转。
    def _choose_cold_start_probe_action(self, session_id: str) -> MctsAction | None:
        state = self.deps.state_tracker.get_session(session_id)
        fallback_candidate = self.deps.question_selector.select_next_question(
            self.deps.retriever.get_cold_start_questions(),
            state,
        )

        if fallback_candidate is None:
            return None

        return self.deps.action_builder.build_probe_action_from_question_candidate(fallback_candidate)

    # 当相关高成本检查明确未做、且当前已无可观测低成本问题时，输出阶段性判断而不是空转。
    def _build_exam_limited_stage_stop_decision(self, session_id: str) -> StopDecision | None:
        state = self.deps.state_tracker.get_session(session_id)
        not_done_exam_kinds = [
            kind
            for kind, context in state.exam_context.items()
            if context.availability == "not_done"
        ]

        if len(not_done_exam_kinds) == 0 or len(state.candidate_hypotheses) == 0:
            return None

        low_cost_candidates = self._collect_remaining_low_cost_r2_candidates(state)

        if len(low_cost_candidates) > 0:
            return None

        return StopDecision(
            True,
            "no_exam_and_no_low_cost_questions",
            0.0,
            {
                "stage_end_reason": "insufficient_observable_evidence",
                "not_done_exam_kinds": not_done_exam_kinds,
                "candidate_hypothesis_ids": [item.node_id for item in state.candidate_hypotheses[:3]],
                "message": "相关检查尚未完成，且当前没有高价值、低成本的可继续追问证据，输出阶段性判断。",
            },
        )

    # 查询当前候选诊断下是否仍有未问过的低成本 R2 证据。
    def _collect_remaining_low_cost_r2_candidates(self, state: SessionState) -> list[dict]:
        if not hasattr(self.deps.retriever, "retrieve_r2_expected_evidence"):
            return []

        remaining: list[dict] = []
        ranked_hypotheses = sorted(state.candidate_hypotheses, key=lambda item: (-item.score, item.name))[:3]

        for hypothesis in ranked_hypotheses:
            try:
                rows = self.deps.retriever.retrieve_r2_expected_evidence(hypothesis, state, top_k=8)
            except TypeError:
                rows = self.deps.retriever.retrieve_r2_expected_evidence(hypothesis, state)
            except Exception:
                continue

            for row in rows:
                if not isinstance(row, dict):
                    continue

                node_id = str(row.get("node_id") or "").strip()

                if len(node_id) == 0:
                    continue

                if node_id in state.asked_node_ids or node_id in state.slots or node_id in state.evidence_states:
                    continue

                if self._is_low_cost_observable_row(row):
                    remaining.append(row)

        return remaining[:12]

    # 判断一条 R2 候选是否属于患者可直接回答或已知病史型低成本证据。
    def _is_low_cost_observable_row(self, row: dict) -> bool:
        acquisition_mode = str(row.get("acquisition_mode") or "").strip()
        evidence_cost = str(row.get("evidence_cost") or "").strip()
        label = str(row.get("label") or "").strip()

        if acquisition_mode in {"direct_ask", "history_known"} or evidence_cost == "low":
            return True

        if acquisition_mode in {"needs_lab_test", "needs_imaging", "needs_pathogen_test"} or evidence_cost == "high":
            return False

        return label in {
            "ClinicalFinding",
            "RiskFactor",
            "ClinicalAttribute",
            "PopulationGroup",
        }

    # 判断当前输入是否只是问候或闲聊，没有足够临床信息可进入 A2/A3。
    def _should_collect_chief_complaint(
        self,
        patient_context: PatientContext,
        a1_result: A1ExtractionResult,
        pending_action_result: PendingActionResult | None,
    ) -> bool:
        if pending_action_result is not None:
            return False

        if len(a1_result.key_features) > 0:
            return False

        if len(patient_context.clinical_features) > 0:
            return False

        general_info = patient_context.general_info
        if general_info.age is not None or general_info.sex is not None or general_info.pregnancy_status is not None:
            return False

        if len(general_info.past_history) > 0 or len(general_info.epidemiology) > 0:
            return False

        return True

    # 如果已经追问过一次主诉，但本轮仍无任何有效线索，则停止重复 intake，避免空转。
    def _should_stop_after_repeated_chief_complaint(
        self,
        session_id: str,
        patient_context: PatientContext,
        a1_result: A1ExtractionResult,
        pending_action_result: PendingActionResult | None,
    ) -> bool:
        if not self._should_collect_chief_complaint(patient_context, a1_result, pending_action_result):
            return False

        state = self.deps.state_tracker.get_session(session_id)
        last_answered_action = state.metadata.get("last_answered_action")

        if not isinstance(last_answered_action, MctsAction):
            return False

        return last_answered_action.action_type == "collect_chief_complaint"

    # 构造“主诉已追问但仍无有效线索”的停止决策，避免重复问同一句。
    def _build_repeated_chief_complaint_stop_decision(self, session_id: str) -> StopDecision:
        state = self.deps.state_tracker.get_session(session_id)

        return StopDecision(
            True,
            "repeated_chief_complaint_without_signal",
            0.0,
            {
                "stage_end_reason": "insufficient_chief_complaint_signal",
                "message": "已经进行过一次主诉澄清，但患者回答仍未提供任何可用于诊断推理的症状、病史或检查线索，停止重复 intake。",
                "asked_node_ids": list(state.asked_node_ids),
            },
        )

    # 构造主诉采集动作；优先用 LLM 生成自然回应，失败时回退固定话术。
    def _build_chief_complaint_intake_action(self, patient_text: str) -> MctsAction:
        payload = self._build_chief_complaint_prompt_payload(patient_text)
        acknowledgement = str(payload.get("acknowledgement") or "你好，我在。").strip()
        question = str(
            payload.get("question")
            or "请先告诉我这次主要哪里不舒服、持续了多久，以及你最担心的问题是什么？"
        ).strip()
        reasoning = str(payload.get("reasoning") or "当前输入缺少可推理的临床线索，需要先采集主诉。").strip()
        question_text = f"{acknowledgement}\n\n{question}"

        return MctsAction(
            action_id="intake::chief_complaint",
            action_type="collect_chief_complaint",
            target_node_id="__chief_complaint__",
            target_node_label="Intake",
            target_node_name="主要不适 / 就诊原因",
            topic_id="A1",
            prior_score=1.0,
            metadata={
                "question_type_hint": "detail",
                "question_text": question_text,
                "intake_reasoning": reasoning,
                "evidence_tags": ["intake", "type:detail"],
            },
        )

    # 生成主诉采集话术；LLM 不可用时返回安全固定话术。
    def _build_chief_complaint_prompt_payload(self, patient_text: str) -> dict:
        default_payload = {
            "acknowledgement": "你好，我在。",
            "question": "请先告诉我这次主要哪里不舒服、持续了多久，以及你最担心的问题是什么？",
            "reasoning": "当前输入缺少可推理的临床线索，需要先采集主诉。",
        }

        llm_client = self.deps.llm_client
        is_available = getattr(llm_client, "is_available", lambda: False)
        if llm_client is None or not is_available():
            return default_payload

        try:
            payload = llm_client.run_structured_prompt(
                "intake_opening_response",
                {"patient_text": patient_text},
                dict,
            )
        except Exception:
            return default_payload

        if not isinstance(payload, dict):
            return default_payload

        return {**default_payload, **payload}

    # 判断当前搜索是否已经产生了可供 verifier 或下一问消费的有效信号。
    def _has_search_signal(self, search_result: SearchResult) -> bool:
        return (
            search_result.selected_action is not None
            or len(search_result.trajectories) > 0
            or len(search_result.final_answer_scores) > 0
        )

    # 只有在答案被明确接受时，或者完全没有搜索信号且也无可继续动作时，才直接输出最终报告。
    def _should_emit_final_report(
        self,
        search_result: SearchResult,
        selected_action: MctsAction | None,
        stop_decision: StopDecision,
        accept_decision: StopDecision,
    ) -> bool:
        if accept_decision.should_stop:
            return True

        if (
            selected_action is None
            and stop_decision.should_stop
            and stop_decision.reason
            in {
                "no_exam_and_no_low_cost_questions",
                "exam_not_done_and_no_low_cost_questions",
                "insufficient_observable_evidence",
            }
        ):
            return True

        if self._has_search_signal(search_result):
            return False

        return stop_decision.should_stop and selected_action is None


# 将运行期覆盖配置递归合并到默认配置中。
def _merge_brain_config(base: dict, overrides: dict | None) -> dict:
    if overrides is None:
        return dict(base)

    merged = deepcopy(base)

    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_brain_config(merged[key], value)
        else:
            merged[key] = value

    return merged


# 基于现有依赖的默认实现，快速构造一个可运行的问诊大脑。
def build_default_brain(client: Neo4jClient, config_overrides: dict | None = None) -> ConsultationBrain:
    config = _merge_brain_config(load_brain_config(), config_overrides)
    search_config = dict(config.get("search", {}))
    kg_config = dict(config.get("kg", {}))
    path_eval_config = dict(config.get("path_evaluation", {}))
    llm_config = dict(config.get("llm", {}))
    a1_config = dict(config.get("a1", {}))
    a2_config = dict(config.get("a2", {}))
    fallback_config = dict(config.get("fallback", {}))
    stop_config = dict(config.get("stop", {}))
    repair_config = dict(config.get("repair", {}))
    llm_client = LlmClient(
        structured_retry_count=int(llm_config.get("structured_retry_count", 1)),
    )
    if not llm_client.is_available():
        raise LlmUnavailableError(
            stage="brain_startup",
            prompt_name="brain_startup",
            message="当前配置要求走 LLM-first 主链路，但未检测到可用的大模型客户端。",
        )
    deps = BrainDependencies(
        state_tracker=StateTracker(),
        retriever=GraphRetriever(
            client,
            RetrievalConfig(
                kg_similarity_threshold=float(kg_config.get("entity_link_threshold", 0.72)),
                disable_kg_below_threshold=bool(kg_config.get("disable_kg_below_threshold", True)),
                r2_limit=int(config.get("a3", {}).get("validation_limit", 10)),
            ),
        ),
        med_extractor=MedExtractor(llm_client),
        entity_linker=EntityLinker(
            client,
            EntityLinkerConfig(
                entity_link_threshold=float(kg_config.get("entity_link_threshold", 0.72)),
                top_k_entity_matches=int(kg_config.get("top_k_entity_matches", 5)),
                disable_kg_below_threshold=bool(kg_config.get("disable_kg_below_threshold", True)),
            ),
        ),
        question_selector=QuestionSelector(),
        stop_rule_engine=StopRuleEngine(
            StopRuleConfig(
                max_fail_count=int(fallback_config.get("max_fail_count", 2)),
                max_rollouts=int(search_config.get("num_rollouts", 8)),
                max_tree_depth=int(search_config.get("max_depth", 6)),
                min_turn_index_before_final_answer=int(stop_config.get("min_turn_index_before_final_answer", 2)),
                min_trajectory_count_before_accept=int(stop_config.get("min_trajectory_count_before_accept", 2)),
                min_answer_consistency=float(stop_config.get("min_answer_consistency", 0.45)),
                min_agent_eval_score=float(stop_config.get("min_agent_eval_score", 0.65)),
                min_final_score=float(stop_config.get("min_final_score", 0.55)),
                require_verifier_accept_flag=bool(stop_config.get("require_verifier_accept_flag", True)),
                acceptance_profile=str(stop_config.get("acceptance_profile", "baseline")),
                guarded_lenient_early_turn_index=int(stop_config.get("guarded_lenient_early_turn_index", 2)),
            )
        ),
        report_builder=ReportBuilder(),
        evidence_parser=EvidenceParser(
            llm_client,
            EvidenceParserConfig(
                use_llm_extractor=bool(a1_config.get("use_llm_extractor", True)),
            ),
        ),
        hypothesis_manager=HypothesisManager(
            llm_client,
            HypothesisManagerConfig(
                expand_top_k_hypotheses=int(a2_config.get("expand_top_k_hypotheses", 3)),
            ),
        ),
        action_builder=ActionBuilder(ActionBuilderConfig()),
        router=ReasoningRouter(
            RouterConfig(
                fallback_fail_count=int(fallback_config.get("max_fail_count", 2)),
            )
        ),
        mcts_engine=MctsEngine(
            MctsConfig(
                num_rollouts=int(search_config.get("num_rollouts", 8)),
                max_depth=int(search_config.get("max_depth", 6)),
                max_child_nodes=int(search_config.get("max_child_nodes", 4)),
                exploration_constant=float(search_config.get("exploration_weight", 2.0)),
                discount_factor=float(search_config.get("discount_factor", 1.0)),
                max_kg_triplets=int(search_config.get("max_kg_triplets", 15)),
            )
        ),
        simulation_engine=SimulationEngine(
            SimulationConfig(
                rollout_max_depth=int(search_config.get("max_depth", 6)),
                rollout_discount=float(search_config.get("discount_factor", 0.9)),
            )
        ),
        trajectory_evaluator=TrajectoryEvaluator(
            TrajectoryEvaluatorConfig(
                consistency_weight=float(path_eval_config.get("consistency_weight", 0.3)),
                diversity_weight=float(path_eval_config.get("diversity_weight", 0.4)),
                agent_eval_weight=float(path_eval_config.get("agent_eval_weight", 0.3)),
                agent_eval_mode=str(path_eval_config.get("agent_eval_mode", "fallback")),
                llm_verifier_min_turn_index=int(
                    path_eval_config.get(
                        "llm_verifier_min_turn_index",
                        stop_config.get("min_turn_index_before_final_answer", 2),
                    )
                ),
                llm_verifier_min_trajectory_count=int(
                    path_eval_config.get(
                        "llm_verifier_min_trajectory_count",
                        stop_config.get("min_trajectory_count_before_accept", 2),
                    )
                ),
            ),
            llm_client=llm_client,
        ),
        llm_client=llm_client,
        repair_policy=RepairPolicyConfig(
            enable_verifier_hypothesis_reshuffle=bool(
                repair_config.get("enable_verifier_hypothesis_reshuffle", True)
            ),
            enable_best_repair_action=bool(repair_config.get("enable_best_repair_action", True)),
            enable_tree_reroot=bool(repair_config.get("enable_tree_reroot", True)),
        ),
    )
    return ConsultationBrain(deps)


# 从环境变量读取 Neo4j 配置，并构造一个默认问诊大脑。
def build_default_brain_from_env(config_overrides: dict | None = None) -> ConsultationBrain:
    client = Neo4jClient.from_env()
    return build_default_brain(client, config_overrides=config_overrides)


# 读取第二阶段默认配置文件。
def load_brain_config(config_path: str | Path | None = None) -> dict:
    path = Path(config_path) if config_path is not None else Path(__file__).resolve().parents[1] / "configs" / "brain.yaml"

    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}

    return payload if isinstance(payload, dict) else {}
