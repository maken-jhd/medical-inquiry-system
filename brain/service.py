"""编排 A1-A4 推理循环、图谱检索与下一问生成。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Optional

from .action_builder import ActionBuilder
from .evidence_parser import EvidenceParser
from .hypothesis_manager import HypothesisManager
from .mcts_engine import MctsEngine
from .neo4j_client import Neo4jClient
from .question_selector import QuestionSelector
from .report_builder import ReportBuilder
from .retriever import GraphRetriever
from .router import ReasoningRouter
from .simulation_engine import SimulationEngine
from .state_tracker import StateTracker
from .stop_rules import StopRuleEngine
from .types import (
    A1ExtractionResult,
    A2HypothesisResult,
    A3VerificationResult,
    A4DeductiveResult,
    EvidenceState,
    HypothesisCandidate,
    MctsAction,
    QuestionCandidate,
    SessionState,
    SlotUpdate,
)


@dataclass
class BrainDependencies:
    """集中管理问诊大脑依赖的核心组件。"""

    state_tracker: StateTracker
    retriever: GraphRetriever
    question_selector: QuestionSelector | None = None
    stop_rule_engine: StopRuleEngine | None = None
    report_builder: ReportBuilder | None = None
    evidence_parser: EvidenceParser | None = None
    hypothesis_manager: HypothesisManager | None = None
    action_builder: ActionBuilder | None = None
    router: ReasoningRouter | None = None
    mcts_engine: MctsEngine | None = None
    simulation_engine: SimulationEngine | None = None


class ConsultationBrain:
    """阶段二问诊大脑的高层编排入口。"""

    # 初始化问诊大脑所需的依赖组件，并补齐未传入的默认对象。
    def __init__(self, deps: BrainDependencies) -> None:
        if deps.question_selector is None:
            deps.question_selector = QuestionSelector()
        if deps.stop_rule_engine is None:
            deps.stop_rule_engine = StopRuleEngine()
        if deps.report_builder is None:
            deps.report_builder = ReportBuilder()
        if deps.evidence_parser is None:
            deps.evidence_parser = EvidenceParser()
        if deps.hypothesis_manager is None:
            deps.hypothesis_manager = HypothesisManager()
        if deps.action_builder is None:
            deps.action_builder = ActionBuilder()
        if deps.router is None:
            deps.router = ReasoningRouter()
        if deps.mcts_engine is None:
            deps.mcts_engine = MctsEngine()
        if deps.simulation_engine is None:
            deps.simulation_engine = SimulationEngine()
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

    # 兼容旧接口：在当前状态下返回下一问。
    def get_next_question(self, session_id: str) -> Optional[QuestionCandidate]:
        state = self.deps.state_tracker.get_session(session_id)
        stop_decision = self.deps.stop_rule_engine.check_sufficiency(
            state,
            state.candidate_hypotheses,
        )

        if stop_decision.should_stop:
            return None

        if len(state.candidate_hypotheses) == 0:
            candidates = self.deps.retriever.get_cold_start_questions()
        else:
            candidates = self.deps.retriever.get_reverse_validation_questions(
                state.candidate_hypotheses,
                state,
            )

        next_question = self.deps.question_selector.select_next_question(candidates, state)

        if next_question is not None:
            self.deps.state_tracker.mark_question_asked(session_id, next_question.node_id)

        return next_question

    # 在会话结束时汇总最终报告。
    def finalize(self, session_id: str) -> dict:
        state = self.deps.state_tracker.get_session(session_id)
        stop_decision = self.deps.stop_rule_engine.check_sufficiency(
            state,
            state.candidate_hypotheses,
        )
        return self.deps.report_builder.build_final_report(state, stop_decision)

    # 处理单轮患者输入，并按 A1-A4 + UCT + Simulation 生成下一问。
    def process_turn(self, session_id: str, patient_text: str) -> dict:
        tracker = self.deps.state_tracker
        turn_index = tracker.increment_turn(session_id)
        a4_result: A4DeductiveResult | None = None
        route_after_a4 = None
        applied_updates: list[SlotUpdate] = []
        pending_action = tracker.get_pending_action(session_id)

        if pending_action is not None:
            a4_result = self.deps.evidence_parser.run_a4_deductive_analysis(patient_text, pending_action)
            a4_updates = self.deps.evidence_parser.build_slot_updates_from_a4(
                pending_action,
                a4_result,
                patient_text,
                turn_index=turn_index,
            )
            tracker.apply_slot_updates(session_id, a4_updates)
            applied_updates.extend(a4_updates)

            evidence_state = EvidenceState(
                node_id=pending_action.target_node_id,
                existence=a4_result.existence,
                certainty=a4_result.certainty,
                reasoning=a4_result.reasoning,
                source_turns=[turn_index],
                metadata={
                    "action_id": pending_action.action_id,
                    "hypothesis_id": pending_action.hypothesis_id,
                },
            )
            tracker.set_evidence_state(session_id, evidence_state)
            tracker.clear_pending_action(session_id)
            route_after_a4 = self.deps.router.route_after_question_answer(
                a4_result,
                pending_action,
                tracker.get_session(session_id),
            )
            self._apply_hypothesis_feedback(session_id, pending_action, evidence_state)
            self._record_action_reward(session_id, pending_action, a4_result)

        a1_result = self.deps.evidence_parser.run_a1_key_symptom_extraction(
            patient_text,
            known_feature_names=self._collect_known_feature_names(session_id),
        )
        a1_updates = self.deps.evidence_parser.build_slot_updates_from_a1(
            a1_result,
            turn_index=turn_index,
        )

        if len(a1_updates) > 0:
            tracker.apply_slot_updates(session_id, a1_updates)
            applied_updates.extend(a1_updates)

        state = tracker.get_session(session_id)
        route_after_slot_update = self.deps.router.route_after_slot_update(state)
        a2_result = self._run_a2(session_id, a1_result)
        state = tracker.get_session(session_id)

        stop_decision = self.deps.stop_rule_engine.check_sufficiency(
            state,
            state.candidate_hypotheses,
        )

        if stop_decision.should_stop:
            final_report = self.deps.report_builder.build_final_report(state, stop_decision)
            return {
                "session_id": session_id,
                "turn_index": turn_index,
                "patient_text": patient_text,
                "a1": asdict(a1_result),
                "a2": asdict(a2_result),
                "a3": asdict(A3VerificationResult()),
                "a4": asdict(a4_result) if a4_result is not None else None,
                "route_after_a4": asdict(route_after_a4) if route_after_a4 is not None else None,
                "route_after_slot_update": asdict(route_after_slot_update),
                "updates": [asdict(item) for item in applied_updates],
                "next_question": None,
                "final_report": final_report,
            }

        selected_action, a3_result, simulation_outcomes = self._run_a3(session_id, a2_result)

        if selected_action is not None:
            tracker.mark_question_asked(session_id, selected_action.target_node_id)
            tracker.set_pending_action(session_id, selected_action)

            if selected_action.topic_id is not None:
                tracker.activate_topic(session_id, selected_action.topic_id)

        return {
            "session_id": session_id,
            "turn_index": turn_index,
            "patient_text": patient_text,
            "a1": asdict(a1_result),
            "a2": asdict(a2_result),
            "a3": asdict(a3_result),
            "a4": asdict(a4_result) if a4_result is not None else None,
            "route_after_a4": asdict(route_after_a4) if route_after_a4 is not None else None,
            "route_after_slot_update": asdict(route_after_slot_update),
            "updates": [asdict(item) for item in applied_updates],
            "next_question": a3_result.question_text,
            "pending_action": asdict(selected_action) if selected_action is not None else None,
            "simulation_outcomes": [asdict(item) for item in simulation_outcomes],
            "final_report": None,
        }

    # 执行 A2：先跑 R1，再生成主假设和备选假设。
    def _run_a2(self, session_id: str, a1_result: A1ExtractionResult) -> A2HypothesisResult:
        tracker = self.deps.state_tracker
        state = tracker.get_session(session_id)
        candidates = self.deps.retriever.retrieve_r1_candidates(a1_result.key_features, state)
        a2_result = self.deps.hypothesis_manager.run_a2_hypothesis_generation(candidates)

        score_candidates: list[HypothesisCandidate] = []

        if a2_result.primary_hypothesis is not None:
            score_candidates.append(a2_result.primary_hypothesis)

        score_candidates.extend(a2_result.alternatives)
        tracker.set_candidate_hypotheses(
            session_id,
            self.deps.hypothesis_manager.build_hypothesis_scores(score_candidates),
        )
        return a2_result

    # 执行 A3：先做 R2，再结合 simulation 和 UCT 选择最优验证动作。
    def _run_a3(
        self,
        session_id: str,
        a2_result: A2HypothesisResult,
    ) -> tuple[Optional[MctsAction], A3VerificationResult, list]:
        tracker = self.deps.state_tracker
        state = tracker.get_session(session_id)
        primary_hypothesis = a2_result.primary_hypothesis

        if primary_hypothesis is None:
            return self._fallback_to_cold_start(session_id)

        r2_rows = self.deps.retriever.retrieve_r2_expected_evidence(primary_hypothesis, state)
        actions = self.deps.action_builder.build_verification_actions(
            r2_rows,
            hypothesis_id=primary_hypothesis.node_id,
            topic_id=primary_hypothesis.label,
        )

        if len(actions) == 0:
            return self._fallback_to_cold_start(session_id)

        state_signature = self.deps.mcts_engine.build_state_signature(state, primary_hypothesis.node_id)
        tracker.increment_state_visit(
            session_id,
            state_signature,
            {"hypothesis_id": primary_hypothesis.node_id},
        )
        simulation_outcomes = self.deps.simulation_engine.simulate_actions(actions, state, primary_hypothesis)
        selected_action = self.deps.mcts_engine.select_action(
            actions,
            tracker.get_session(session_id),
            simulation_outcomes,
            state_signature=state_signature,
        )

        if selected_action is None:
            return self._fallback_to_cold_start(session_id)

        a3_result = self.deps.action_builder.build_a3_verification_result([selected_action])
        return selected_action, a3_result, simulation_outcomes

    # 在没有稳定主假设或 R2 动作时，退回冷启动提问。
    def _fallback_to_cold_start(
        self,
        session_id: str,
    ) -> tuple[Optional[MctsAction], A3VerificationResult, list]:
        tracker = self.deps.state_tracker
        state = tracker.get_session(session_id)
        cold_candidates = self.deps.retriever.get_cold_start_questions()
        selected_question = self.deps.question_selector.select_next_question(cold_candidates, state)

        if selected_question is None:
            return None, A3VerificationResult(reasoning="当前没有可用的冷启动问题。"), []

        action = self.deps.action_builder.build_probe_action_from_question_candidate(selected_question)
        verification_result = A3VerificationResult(
            relevant_symptom=action,
            question_text=self.deps.action_builder.render_question_text(action),
            reasoning="当前缺少稳定主假设，退回冷启动问题。",
        )
        return action, verification_result, []

    # 根据 A4 结果将 reward 反馈给 MCTS 统计。
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

    # 将 A4 演绎证据反馈到当前假设分数上。
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

    # 从当前槽位状态中收集已知特征名称，辅助 A1 做更保守的线索匹配。
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
    deps = BrainDependencies(
        state_tracker=StateTracker(),
        retriever=GraphRetriever(client),
        question_selector=QuestionSelector(),
        stop_rule_engine=StopRuleEngine(),
        report_builder=ReportBuilder(),
        evidence_parser=EvidenceParser(),
        hypothesis_manager=HypothesisManager(),
        action_builder=ActionBuilder(),
        router=ReasoningRouter(),
        mcts_engine=MctsEngine(),
        simulation_engine=SimulationEngine(),
    )
    return ConsultationBrain(deps)


# 从环境变量读取 Neo4j 配置，并构造一个默认问诊大脑。
def build_default_brain_from_env() -> ConsultationBrain:
    client = Neo4jClient.from_env()
    return build_default_brain(client)
