"""编排患者上下文提取、A1-A4 推理、图谱检索与局部树搜索。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

import yaml

from .action_builder import ActionBuilder, ActionBuilderConfig
from .entity_linker import EntityLinker, EntityLinkerConfig
from .evidence_parser import EvidenceParser, EvidenceParserConfig
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
    A4DeductiveResult,
    EvidenceState,
    FinalAnswerScore,
    HypothesisScore,
    LinkedEntity,
    MctsAction,
    PatientContext,
    ReasoningTrajectory,
    SearchResult,
    SessionState,
    SlotUpdate,
    StopDecision,
    TreeNode,
    RouteDecision,
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

    # 根据上一轮待验证动作更新证据状态、槽位状态和路由决策。
    def update_from_pending_action(
        self,
        session_id: str,
        patient_context: PatientContext,
        patient_text: str,
        turn_index: int,
    ) -> tuple[A4DeductiveResult | None, object | None, object | None, list[SlotUpdate]]:
        tracker = self.deps.state_tracker
        pending_action = tracker.get_pending_action(session_id)

        if pending_action is None:
            return None, None, None, []

        a4_result = self.deps.evidence_parser.interpret_answer_for_target(patient_text, pending_action)
        a4_updates = self.deps.evidence_parser.build_slot_updates_from_a4(
            pending_action,
            a4_result,
            patient_text,
            turn_index=turn_index,
        )
        tracker.apply_slot_updates(session_id, a4_updates)

        evidence_tags = self._infer_action_evidence_tags(pending_action)
        confirmed_family_candidate = self._is_confirmed_family_candidate(
            pending_action,
            a4_result,
            evidence_tags,
        )
        provisional_family_candidate = self._is_provisional_family_candidate(a4_result, evidence_tags)
        evidence_state = EvidenceState(
            node_id=pending_action.target_node_id,
            existence=a4_result.existence,
            certainty=a4_result.certainty,
            reasoning=a4_result.reasoning,
            source_turns=[turn_index],
            metadata={
                "action_id": pending_action.action_id,
                "hypothesis_id": pending_action.hypothesis_id,
                "relation_type": pending_action.metadata.get("relation_type"),
                "target_node_name": pending_action.target_node_name,
                "target_node_label": pending_action.target_node_label,
                "evidence_tags": sorted(evidence_tags),
                "a4_supporting_span": a4_result.supporting_span,
                "a4_negation_span": a4_result.negation_span,
                "a4_uncertain_span": a4_result.uncertain_span,
                "confirmed_family_candidate": confirmed_family_candidate,
                "confirmed_family_candidates": sorted(evidence_tags & GUARDED_CONFIRMED_EVIDENCE_TAGS),
                "provisional_family_candidate": provisional_family_candidate,
                "provisional_family_candidates": sorted(
                    evidence_tags & {"imaging", "oxygenation", "pathogen", "immune_status", "pcp_specific"}
                ),
                "patient_answer": patient_text,
            },
        )
        tracker.set_evidence_state(session_id, evidence_state)
        self._record_a4_evidence_audit(session_id, pending_action, evidence_state, a4_result, patient_text, turn_index)
        self._apply_hypothesis_feedback(session_id, pending_action, evidence_state)
        self._record_action_reward(session_id, pending_action, a4_result)
        tracker.get_session(session_id).metadata["last_answered_action"] = pending_action
        tracker.clear_pending_action(session_id)
        updated_state = tracker.get_session(session_id)
        current_hypothesis = self._find_hypothesis_by_id(
            updated_state.candidate_hypotheses,
            pending_action.hypothesis_id,
        )
        alternatives = [
            item
            for item in updated_state.candidate_hypotheses
            if current_hypothesis is None or item.node_id != current_hypothesis.node_id
        ]
        deductive_decision = self.deps.evidence_parser.judge_deductive_result(
            patient_context,
            pending_action,
            a4_result,
            current_hypothesis,
            alternatives,
        )
        route_after_a4 = self.deps.router.decide_next_stage(deductive_decision, updated_state)
        return a4_result, deductive_decision, route_after_a4, a4_updates

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
        candidates = self.deps.retriever.retrieve_r1_candidates(
            list(linked_entities) + list(a1_result.key_features),
            patient_context,
            state,
        )
        a2_result = self.deps.hypothesis_manager.run_a2_hypothesis_generation(patient_context, candidates)
        score_candidates = []

        if a2_result.primary_hypothesis is not None:
            score_candidates.append(a2_result.primary_hypothesis)

        score_candidates.extend(a2_result.alternatives)
        tracker.set_candidate_hypotheses(
            session_id,
            self.deps.hypothesis_manager.build_hypothesis_scores(score_candidates),
        )
        return a2_result

    # 运行局部树搜索，生成下一问动作、候选轨迹和最终答案评分。
    def run_reasoning_search(
        self,
        session_id: str,
        patient_context: PatientContext,
    ) -> SearchResult:
        tracker = self.deps.state_tracker
        state = tracker.get_session(session_id)
        tree = self._ensure_search_tree(session_id, state)
        trajectories: list[ReasoningTrajectory] = []
        rollout_executed = 0

        for rollout_idx in range(self.deps.mcts_engine.config.num_rollouts):
            leaf = self.deps.mcts_engine.select_leaf(tree)

            if leaf is None:
                break

            rollout_context = self._build_rollout_context_from_leaf(session_id, leaf)
            tracker.increment_state_visit(
                session_id,
                leaf.state_signature,
                {
                    "leaf_node_id": leaf.node_id,
                    "hypothesis_id": getattr(rollout_context["current_hypothesis"], "node_id", None),
                    "rollout_idx": rollout_idx,
                },
            )
            actions = self._expand_actions_for_leaf(leaf, rollout_context)

            if len(actions) == 0:
                tree.mark_terminal(leaf.node_id, {"terminal_reason": "no_expandable_actions"})
                continue

            child_nodes = self.deps.mcts_engine.expand_node(tree, leaf.node_id, actions)

            if len(child_nodes) == 0:
                tree.mark_terminal(leaf.node_id, {"terminal_reason": "expand_failed"})
                continue

            rollout_executed += 1

            for child in child_nodes:
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
                rollout_state = trajectory.metadata.pop("_rollout_state", None)
                if isinstance(rollout_state, SessionState):
                    child.metadata["rollout_state"] = rollout_state
                child.metadata["rollout_depth"] = trajectory.metadata.get("rollout_depth", 0)
                child.metadata["last_stage"] = trajectory.metadata.get("last_stage")
                child.metadata["final_answer_id"] = trajectory.final_answer_id
                child.metadata["final_answer_name"] = trajectory.final_answer_name

                if bool(trajectory.metadata.get("path_terminal", False)):
                    tree.mark_terminal(
                        child.node_id,
                        {"terminal_reason": "rollout_stop", "final_answer_id": trajectory.final_answer_id},
                    )

                trajectories.append(trajectory)
                tracker.save_trajectory(session_id, trajectory)
                self.deps.mcts_engine.backpropagate(tree, child.node_id, trajectory.score)

        grouped = self.deps.trajectory_evaluator.group_by_answer(trajectories)
        verifier_patient_context = self._build_verifier_patient_context(session_id, patient_context)
        final_scores = self.deps.trajectory_evaluator.score_groups(grouped, patient_context=verifier_patient_context)
        best_answer = self.deps.trajectory_evaluator.select_best_answer(final_scores)
        selected_action = self.deps.mcts_engine.select_root_action(
            tree,
            excluded_target_node_ids=state.asked_node_ids,
        )
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
        state.metadata["last_search_result"] = search_result
        return search_result

    # verifier 判断是否可以停止时需要看到累计会话证据，而不是只看当前 turn 的患者回复。
    def _build_verifier_patient_context(self, session_id: str, latest_context: PatientContext) -> PatientContext:
        state = self.deps.state_tracker.get_session(session_id)
        raw_sections: list[str] = []
        latest_text = latest_context.raw_text.strip()

        if len(latest_text) > 0:
            raw_sections.append(f"最新患者回答：{latest_text}")

        if len(state.slots) > 0:
            raw_sections.append("累计已确认槽位：")

            for slot in state.slots.values():
                if slot.status == "unknown":
                    continue

                evidence_text = "；".join(str(item) for item in slot.evidence if len(str(item).strip()) > 0)
                raw_sections.append(
                    f"- {slot.node_id}: status={slot.status}, certainty={slot.certainty}, evidence={evidence_text}"
                )

        if len(state.evidence_states) > 0:
            raw_sections.append("累计 A4 证据判断：")

            for evidence in state.evidence_states.values():
                raw_sections.append(
                    f"- {evidence.node_id}: existence={evidence.existence}, certainty={evidence.certainty}, reasoning={evidence.reasoning}"
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
            metadata={**dict(latest_context.metadata), "context_scope": "cumulative_session_for_verifier"},
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
        turn_index = tracker.increment_turn(session_id)
        patient_context = self.ingest_patient_turn(session_id, patient_text)
        a4_result, deductive_decision, route_after_a4, a4_updates = self.update_from_pending_action(
            session_id,
            patient_context,
            patient_text,
            turn_index,
        )
        route_after_a4 = self._gate_route_after_a4(route_after_a4)
        applied_updates: list[SlotUpdate] = list(a4_updates)
        state = tracker.get_session(session_id)
        stage_after_a4 = getattr(route_after_a4, "stage", None)

        a1_result = A1ExtractionResult()
        linked_entities: list[LinkedEntity] = []
        a2_result = A2HypothesisResult()
        search_result = SearchResult()
        selected_action: MctsAction | None = None
        default_search_action: MctsAction | None = None
        route_after_slot_update = self.deps.router.route_after_slot_update(state)

        should_run_a1 = tracker.get_session(session_id).turn_index == 1 or stage_after_a4 == "A1"

        if should_run_a1:
            a1_result = self.deps.evidence_parser.run_a1_key_symptom_extraction(
                patient_context,
                known_feature_names=self._collect_known_feature_names(session_id),
            )
            a1_updates = self.deps.evidence_parser.build_slot_updates_from_a1(a1_result, turn_index=turn_index)

            if len(a1_updates) > 0:
                tracker.apply_slot_updates(session_id, a1_updates)
                applied_updates.extend(a1_updates)

            linked_entities = self.deps.entity_linker.link_clinical_features(patient_context.clinical_features)
        else:
            linked_entities = self.deps.entity_linker.link_clinical_features(patient_context.clinical_features)

        route_after_slot_update = self.deps.router.route_after_slot_update(tracker.get_session(session_id))
        effective_stage = route_after_slot_update.stage if stage_after_a4 in {None, "A1"} else stage_after_a4

        if effective_stage == "FALLBACK":
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
            should_run_a2 = effective_stage in {"A2", "A3"} or len(tracker.get_session(session_id).candidate_hypotheses) == 0

            if should_run_a2:
                a2_result = self._run_a2(session_id, patient_context, a1_result, linked_entities)

            if effective_stage in {"A2", "A3"} and len(tracker.get_session(session_id).candidate_hypotheses) > 0:
                search_result = self.run_reasoning_search(session_id, patient_context)
                default_search_action = self.choose_next_question_from_search(session_id, search_result)

        stop_decision = self.deps.stop_rule_engine.check_sufficiency(
            tracker.get_session(session_id),
            tracker.get_session(session_id).candidate_hypotheses,
        )
        best_answer_score = self.deps.trajectory_evaluator.select_best_answer(search_result.final_answer_scores)
        accept_decision = self.deps.stop_rule_engine.should_accept_final_answer(best_answer_score, tracker.get_session(session_id))
        repair_context = self._build_verifier_repair_context(
            session_id,
            search_result,
            best_answer_score,
            accept_decision,
        )

        if repair_context is not None:
            self._apply_verifier_repair_strategy(session_id, repair_context)
            if bool(self.deps.repair_policy.enable_best_repair_action):
                selected_action = self._choose_repair_action(session_id, search_result, repair_context)
                search_result.repair_selected_action = selected_action
            else:
                selected_action = default_search_action
        elif default_search_action is not None:
            selected_action = default_search_action

        if self._has_search_signal(search_result):
            search_result.selected_action = selected_action
            search_result.verifier_repair_context = self._build_observable_repair_context(
                search_result,
                repair_context,
                selected_action,
            )

        if self._should_emit_final_report(search_result, selected_action, stop_decision, accept_decision):
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
                "a3": asdict(A3VerificationResult()),
                "a4": asdict(a4_result) if a4_result is not None else None,
                "deductive_decision": asdict(deductive_decision) if deductive_decision is not None else None,
                "route_after_a4": asdict(route_after_a4) if route_after_a4 is not None else None,
                "route_after_slot_update": asdict(route_after_slot_update),
                "updates": [asdict(item) for item in applied_updates],
                "evidence_audit": tracker.get_session(session_id).metadata.get("last_a4_evidence_audit"),
                "search_report": (
                    self.deps.report_builder.build_search_report(tracker.get_session(session_id), search_result)
                    if len(search_result.trajectories) > 0 or search_result.selected_action is not None
                    else None
                ),
                "next_question": None,
                "pending_action": None,
                "final_report": final_report,
            }

        a3_result = self.deps.action_builder.build_a3_verification_result(
            selected_action,
            rationale="已结合 R2 检索、UCT 评分与局部 rollout 选择当前动作。",
        )

        if selected_action is not None:
            tracker.mark_question_asked(session_id, selected_action.target_node_id)
            tracker.get_session(session_id).metadata["last_selected_action"] = selected_action
            tracker.set_pending_action(session_id, selected_action)

            if selected_action.topic_id is not None:
                tracker.activate_topic(session_id, selected_action.topic_id)

        return {
            "session_id": session_id,
            "turn_index": turn_index,
            "patient_text": patient_text,
            "patient_context": asdict(patient_context),
            "linked_entities": [asdict(item) for item in linked_entities],
            "a1": asdict(a1_result),
            "a2": asdict(a2_result),
            "a3": asdict(a3_result),
            "a4": asdict(a4_result) if a4_result is not None else None,
            "deductive_decision": asdict(deductive_decision) if deductive_decision is not None else None,
            "route_after_a4": asdict(route_after_a4) if route_after_a4 is not None else None,
            "route_after_slot_update": asdict(route_after_slot_update),
            "updates": [asdict(item) for item in applied_updates],
            "evidence_audit": tracker.get_session(session_id).metadata.get("last_a4_evidence_audit"),
            "search_report": (
                self.deps.report_builder.build_search_report(tracker.get_session(session_id), search_result)
                if len(search_result.trajectories) > 0 or search_result.selected_action is not None
                else None
            ),
            "next_question": a3_result.question_text,
            "pending_action": asdict(selected_action) if selected_action is not None else None,
            "final_report": None,
        }

    # 将 A4 问答解释写成逐轮审计记录，用来定位“问到了但没有进入 confirmed family”的断点。
    def _record_a4_evidence_audit(
        self,
        session_id: str,
        action: MctsAction,
        evidence_state: EvidenceState,
        a4_result: A4DeductiveResult,
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
            "existence": a4_result.existence,
            "certainty": a4_result.certainty,
            "reasoning": a4_result.reasoning,
            "supporting_span": a4_result.supporting_span,
            "negation_span": a4_result.negation_span,
            "uncertain_span": a4_result.uncertain_span,
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
        history = state.metadata.get("a4_evidence_audit_history", [])

        if not isinstance(history, list):
            history = []

        history.append(entry)
        state.metadata["last_a4_evidence_audit"] = entry
        state.metadata["a4_evidence_audit_history"] = history[-48:]

    # A4 证据标签必须比动作 metadata 更鲁棒；节点名可兜底识别 CD4、β-D、PCR、CT 等锚点。
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

    # 判断当前 A4 结果是否具备被 guarded gate 计入 confirmed family 的基础条件。
    def _is_confirmed_family_candidate(
        self,
        action: MctsAction,
        a4_result: A4DeductiveResult,
        evidence_tags: set[str],
    ) -> bool:
        if a4_result.existence != "exist" or a4_result.certainty != "confident":
            return False

        relation_type = str(action.metadata.get("relation_type") or "")
        return relation_type in GUARDED_DEFINITION_RELATION_TYPES or bool(
            evidence_tags & GUARDED_CONFIRMED_EVIDENCE_TAGS
        )

    # 高价值 anchor 的 exist + doubt 可以进入 provisional family，但仍不等同 confirmed。
    def _is_provisional_family_candidate(
        self,
        a4_result: A4DeductiveResult,
        evidence_tags: set[str],
    ) -> bool:
        if a4_result.existence != "exist" or a4_result.certainty != "doubt":
            return False

        return bool(evidence_tags & {"imaging", "oxygenation", "pathogen", "immune_status", "pcp_specific"})

    # 根据 A4 结果将 reward 反馈给 MCTS 动作统计。
    def _record_action_reward(
        self,
        session_id: str,
        action: MctsAction,
        a4_result: A4DeductiveResult,
    ) -> None:
        reward = 0.0

        if a4_result.existence == "exist" and a4_result.certainty == "confident":
            reward = 1.0
        elif a4_result.existence == "exist" and a4_result.certainty == "doubt":
            reward = 0.5
        elif a4_result.existence == "non_exist" and a4_result.certainty == "confident":
            reward = -0.4
        elif a4_result.existence == "non_exist" and a4_result.certainty == "doubt":
            reward = -0.1

        self.deps.state_tracker.record_action_feedback(
            session_id,
            action.action_id,
            reward,
            {"hypothesis_id": action.hypothesis_id},
        )

    # 将 A4 证据状态反馈回当前假设分数。
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

            if not enable_tree_reroot:
                state.metadata["last_tree_refresh"] = {
                    "rerooted": False,
                    "reason": "reroot_disabled",
                    "root_signature": root.state_signature,
                    "top_hypothesis_id": root_top_hypothesis_id,
                }
                return tree

        if force_tree_refresh:
            rerooted = True
            reroot_reason = state.metadata.get("tree_refresh_reason", "forced_refresh")
            state.metadata["tree_refresh_reason"] = reroot_reason
        elif tree is not None:
            rerooted = True
            reroot_reason = state.metadata.get("tree_refresh_reason", "state_signature_changed")
            state.metadata["tree_refresh_reason"] = reroot_reason

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
                    "rollout_state": deepcopy(state),
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
            rollout_state = tracker.get_session_copy(session_id)

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

    # 将 A4 的直接 STOP 先降级为继续搜索，由 verifier 再决定是否真正终止。
    def _gate_route_after_a4(self, route: RouteDecision | None) -> RouteDecision | None:
        if route is None or route.stage != "STOP":
            return route

        return RouteDecision(
            stage="A3",
            reason="A4 给出终止倾向，但系统会先经过 search + verifier 二次确认后再真正停止。",
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
        if best_answer_score is None or accept_decision.should_stop:
            return None

        metadata = dict(best_answer_score.metadata)
        guarded_blocked = accept_decision.reason == "guarded_acceptance_rejected"

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

        if current_hypothesis is not None:
            recommended_next_evidence = self._merge_unique_strings(
                recommended_next_evidence,
                self._normalize_string_list(current_hypothesis.metadata.get("recommended_next_evidence", [])),
            )

        alternative_candidates = self._normalize_alternative_candidates(metadata.get("verifier_alternative_candidates", []))
        guarded_strong_alternatives = self._normalize_alternative_candidates(
            guarded_features.get("guarded_strong_alternative_candidates", [])
        )

        if guarded_block_reason == "strong_unresolved_alternative_candidates" and len(guarded_strong_alternatives) > 0:
            alternative_candidates = guarded_strong_alternatives

        if reject_reason == "strong_alternative_not_ruled_out" and len(alternative_candidates) == 0:
            alternative_candidates = [
                {
                    "answer_id": item.node_id,
                    "answer_name": item.name,
                    "reason": "来自当前 hypothesis 排名中的强备选候选。",
                }
                for item in state.candidate_hypotheses[1:3]
            ]

        if reject_reason == "missing_key_support" and len(recommended_next_evidence) == 0:
            recommended_next_evidence = self._normalize_string_list(metadata.get("verifier_missing_evidence", []))[:3]

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

        if current_hypothesis is None:
            return self.choose_next_question_from_search(session_id, search_result)

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

            for action in self.deps.action_builder.build_verification_actions(
                rows,
                hypothesis_id=action_hypothesis.node_id,
                topic_id=action_hypothesis.label,
                competing_hypotheses=alternatives,
                current_hypothesis=action_hypothesis,
            ):
                action_key = (action.hypothesis_id or "", action.target_node_id)

                if action_key in seen_action_keys:
                    continue

                seen_action_keys.add(action_key)
                actions.append(action)

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

        if guarded_block_reason in {"pcp_combo_insufficient", "missing_confirmed_key_evidence"}:
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

        if reject_reason != "strong_alternative_not_ruled_out":
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
        discriminative_gain = float(action.metadata.get("discriminative_gain", 0.0))
        novelty_score = float(action.metadata.get("novelty_score", 0.0))
        recommended_bonus = float(action.metadata.get("recommended_evidence_bonus", 0.0))
        recommended_match_score = float(action.metadata.get("recommended_match_score", 0.0))
        verifier_recommended_match_score = float(action.metadata.get("verifier_recommended_match_score", 0.0))
        hypothesis_recommended_match_score = float(action.metadata.get("hypothesis_recommended_match_score", 0.0))
        joint_recommended_match_score = float(action.metadata.get("joint_recommended_match_score", 0.0))
        alternative_overlap = float(action.metadata.get("alternative_overlap", 0.0))
        patient_burden = float(action.metadata.get("patient_burden", 0.0))
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

        if reject_reason == "missing_key_support":
            recommended_gap_score = max(
                recommended_match_score,
                joint_recommended_match_score,
                verifier_recommended_match_score * 0.9,
                hypothesis_recommended_match_score * 0.75,
            )
            return (
                score
                + recommended_gap_score * 2.9
                + joint_recommended_match_score * 1.8
                + verifier_recommended_match_score * 1.25
                + hypothesis_recommended_match_score * 0.85
                + recommended_bonus * 1.35
                + guarded_family_match_score * 2.2
                + pcp_combo_priority_bonus
                + missing_family_priority_bonus
                + combo_anchor_bonus
                + discriminative_gain * 0.65
                + type_diversity_bonus * 0.55
                + family_diversity_bonus * 0.85
                - same_type_penalty * 0.5
                - family_repeat_penalty * 0.85
                - non_missing_family_penalty
                - patient_burden * 0.15
            )

        if reject_reason == "strong_alternative_not_ruled_out":
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
            - same_type_penalty * 1.25
            - family_repeat_penalty * 1.9
            - patient_burden * 0.08
        )

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
        if reject_reason == "strong_alternative_not_ruled_out":
            return "A2"

        return "A3"

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
    a1_config = dict(config.get("a1", {}))
    a2_config = dict(config.get("a2", {}))
    a4_config = dict(config.get("a4", {}))
    fallback_config = dict(config.get("fallback", {}))
    stop_config = dict(config.get("stop", {}))
    repair_config = dict(config.get("repair", {}))
    llm_client = LlmClient()
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
                fallback_to_rules=bool(a1_config.get("fallback_to_rules", True)),
                use_llm_deductive_judge=bool(a4_config.get("use_llm_deductive_judge", True)),
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
