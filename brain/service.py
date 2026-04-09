"""编排患者上下文提取、A1-A4 推理、图谱检索与局部树搜索。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Optional, Sequence

from .action_builder import ActionBuilder
from .entity_linker import EntityLinker
from .evidence_parser import EvidenceParser
from .hypothesis_manager import HypothesisManager
from .llm_client import LlmClient
from .mcts_engine import MctsEngine
from .med_extractor import MedExtractor
from .neo4j_client import Neo4jClient
from .question_selector import QuestionSelector
from .report_builder import ReportBuilder
from .retriever import GraphRetriever
from .router import ReasoningRouter
from .search_tree import SearchTree
from .simulation_engine import SimulationEngine
from .state_tracker import StateTracker
from .stop_rules import StopRuleEngine
from .trajectory_evaluator import TrajectoryEvaluator
from .types import (
    A1ExtractionResult,
    A2HypothesisResult,
    A3VerificationResult,
    A4DeductiveResult,
    EvidenceState,
    FinalAnswerScore,
    LinkedEntity,
    MctsAction,
    PatientContext,
    ReasoningTrajectory,
    SearchResult,
    SessionState,
    SlotUpdate,
    StopDecision,
    TreeNode,
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
        stop_decision = self.deps.stop_rule_engine.check_sufficiency(state, state.candidate_hypotheses)
        search_result = state.metadata.get("last_search_result")

        if isinstance(search_result, SearchResult):
            return self.deps.report_builder.build_final_reasoning_report(state, stop_decision, search_result)

        return self.deps.report_builder.build_final_report(state, stop_decision)

    # 将患者原话抽成论文中的结构化上下文 P/C。
    def ingest_patient_turn(self, session_id: str, patient_text: str) -> PatientContext:
        _ = session_id
        return self.deps.med_extractor.extract_patient_context(patient_text)

    # 根据上一轮待验证动作更新证据状态、槽位状态和路由决策。
    def update_from_pending_action(
        self,
        session_id: str,
        patient_text: str,
        turn_index: int,
    ) -> tuple[A4DeductiveResult | None, object | None, list[SlotUpdate]]:
        tracker = self.deps.state_tracker
        pending_action = tracker.get_pending_action(session_id)

        if pending_action is None:
            return None, None, []

        a4_result = self.deps.evidence_parser.interpret_answer_for_target(patient_text, pending_action)
        a4_updates = self.deps.evidence_parser.build_slot_updates_from_a4(
            pending_action,
            a4_result,
            patient_text,
            turn_index=turn_index,
        )
        tracker.apply_slot_updates(session_id, a4_updates)

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
            },
        )
        tracker.set_evidence_state(session_id, evidence_state)
        self._apply_hypothesis_feedback(session_id, pending_action, evidence_state)
        self._record_action_reward(session_id, pending_action, a4_result)
        tracker.clear_pending_action(session_id)

        route_after_a4 = self.deps.router.route_after_question_answer(
            a4_result,
            pending_action,
            tracker.get_session(session_id),
        )
        return a4_result, route_after_a4, a4_updates

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
        tree = tracker.get_bound_search_tree(session_id)

        if tree is None:
            tree = SearchTree()
            root_signature = self.deps.mcts_engine.build_state_signature(state)
            tree.add_node(
                TreeNode(
                    node_id=f"root::{root_signature}",
                    state_signature=root_signature,
                    parent_id=None,
                    action_from_parent=None,
                    stage="A2",
                    depth=0,
                    metadata={"session_id": session_id},
                )
            )
            tracker.bind_search_tree(session_id, tree)

        root_id = tree.root_id or next(iter(tree.nodes))
        expandable_hypotheses = self.deps.hypothesis_manager.select_expandable_hypotheses(
            state.candidate_hypotheses,
            self.deps.mcts_engine.config.max_child_nodes,
        )

        selected_action: MctsAction | None = None
        best_score = float("-inf")
        trajectories: list[ReasoningTrajectory] = []

        for hypothesis in expandable_hypotheses:
            rows = self.deps.retriever.retrieve_r2_expected_evidence(hypothesis, state)
            actions = self.deps.action_builder.build_verification_actions(
                rows,
                hypothesis_id=hypothesis.node_id,
                topic_id=hypothesis.label,
            )

            if len(actions) == 0:
                continue

            state_signature = self.deps.mcts_engine.build_state_signature(state, hypothesis.node_id)
            tracker.increment_state_visit(
                session_id,
                state_signature,
                {"hypothesis_id": hypothesis.node_id},
            )
            simulation_outcomes = self.deps.simulation_engine.simulate_actions(actions, state, hypothesis)
            current_action = self.deps.mcts_engine.select_action(
                actions,
                state,
                simulation_outcomes,
                state_signature=state_signature,
            )

            if current_action is None:
                continue

            trajectory = self.deps.simulation_engine.rollout_from_action(
                current_action,
                state,
                patient_context,
                max_depth=self.deps.mcts_engine.config.max_depth,
                primary_hypothesis=hypothesis,
            )
            trajectories.append(trajectory)
            tracker.save_trajectory(session_id, trajectory)

            created_nodes = self.deps.mcts_engine.expand_node(tree, root_id, [current_action])

            if len(created_nodes) > 0:
                self.deps.mcts_engine.backpropagate(tree, created_nodes[0].node_id, trajectory.score)

            if trajectory.score > best_score:
                selected_action = current_action
                best_score = trajectory.score

        grouped = self.deps.trajectory_evaluator.group_by_answer(trajectories)
        final_scores = self.deps.trajectory_evaluator.score_groups(grouped)
        best_answer = self.deps.trajectory_evaluator.select_best_answer(final_scores)
        search_result = SearchResult(
            selected_action=selected_action,
            trajectories=trajectories,
            final_answer_scores=final_scores,
            best_answer_id=best_answer.answer_id if best_answer is not None else None,
            best_answer_name=best_answer.answer_name if best_answer is not None else None,
            metadata={"expandable_hypothesis_count": len(expandable_hypotheses)},
        )
        state.metadata["last_search_result"] = search_result
        return search_result

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
        accept_decision = self.deps.stop_rule_engine.should_accept_final_answer(best_answer_score)

        if accept_decision.should_stop:
            return self.deps.report_builder.build_final_reasoning_report(state, accept_decision, search_result)

        fallback_stop = self.deps.stop_rule_engine.check_sufficiency(state, state.candidate_hypotheses)
        return self.deps.report_builder.build_final_reasoning_report(state, fallback_stop, search_result)

    # 处理单轮患者输入，并输出当前下一问或最终报告。
    def process_turn(self, session_id: str, patient_text: str) -> dict:
        tracker = self.deps.state_tracker
        turn_index = tracker.increment_turn(session_id)
        patient_context = self.ingest_patient_turn(session_id, patient_text)
        a4_result, route_after_a4, a4_updates = self.update_from_pending_action(session_id, patient_text, turn_index)
        applied_updates: list[SlotUpdate] = list(a4_updates)
        should_run_a1 = (
            tracker.get_session(session_id).turn_index == 1
            or route_after_a4 is None
            or getattr(route_after_a4, "stage", None) in {"A1", "FALLBACK"}
        )

        a1_result = A1ExtractionResult()
        linked_entities: list[LinkedEntity] = []

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
        a2_result = self._run_a2(session_id, patient_context, a1_result, linked_entities)
        search_result = self.run_reasoning_search(session_id, patient_context)
        selected_action = self.choose_next_question_from_search(session_id, search_result)

        stop_decision = self.deps.stop_rule_engine.check_sufficiency(
            tracker.get_session(session_id),
            tracker.get_session(session_id).candidate_hypotheses,
        )
        best_answer_score = self.deps.trajectory_evaluator.select_best_answer(search_result.final_answer_scores)
        accept_decision = self.deps.stop_rule_engine.should_accept_final_answer(best_answer_score)

        if stop_decision.should_stop or accept_decision.should_stop:
            final_report = self.finalize_from_search(session_id, search_result)
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
                "route_after_a4": asdict(route_after_a4) if route_after_a4 is not None else None,
                "route_after_slot_update": asdict(route_after_slot_update),
                "updates": [asdict(item) for item in applied_updates],
                "search_report": self.deps.report_builder.build_search_report(tracker.get_session(session_id), search_result),
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
            "route_after_a4": asdict(route_after_a4) if route_after_a4 is not None else None,
            "route_after_slot_update": asdict(route_after_slot_update),
            "updates": [asdict(item) for item in applied_updates],
            "search_report": self.deps.report_builder.build_search_report(tracker.get_session(session_id), search_result),
            "next_question": a3_result.question_text,
            "pending_action": asdict(selected_action) if selected_action is not None else None,
            "final_report": None,
        }

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


# 基于现有依赖的默认实现，快速构造一个可运行的问诊大脑。
def build_default_brain(client: Neo4jClient) -> ConsultationBrain:
    llm_client = LlmClient()
    deps = BrainDependencies(
        state_tracker=StateTracker(),
        retriever=GraphRetriever(client),
        med_extractor=MedExtractor(llm_client),
        entity_linker=EntityLinker(client),
        question_selector=QuestionSelector(),
        stop_rule_engine=StopRuleEngine(),
        report_builder=ReportBuilder(),
        evidence_parser=EvidenceParser(llm_client),
        hypothesis_manager=HypothesisManager(llm_client),
        action_builder=ActionBuilder(),
        router=ReasoningRouter(),
        mcts_engine=MctsEngine(),
        simulation_engine=SimulationEngine(),
        trajectory_evaluator=TrajectoryEvaluator(),
        llm_client=llm_client,
    )
    return ConsultationBrain(deps)


# 从环境变量读取 Neo4j 配置，并构造一个默认问诊大脑。
def build_default_brain_from_env() -> ConsultationBrain:
    client = Neo4jClient.from_env()
    return build_default_brain(client)
