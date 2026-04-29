"""负责对候选动作执行浅层 simulation 预演。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Iterable, List, Optional

from .action_builder import ActionBuilder
from .hypothesis_manager import HypothesisManager
from .retriever import GraphRetriever
from .router import ReasoningRouter
from .types import (
    A4DeductiveResult,
    EvidenceState,
    HypothesisCandidate,
    HypothesisScore,
    MctsAction,
    PatientContext,
    ReasoningTrajectory,
    SessionState,
    SimulationOutcome,
    SlotState,
    TreeNode,
)


@dataclass
class SimulationConfig:
    """保存局部 rollout 的基础参数。"""

    positive_branch_probability: float = 0.6
    positive_reward_multiplier: float = 1.0
    negative_reward_multiplier: float = 0.45
    doubtful_reward_multiplier: float = 0.3
    rollout_max_depth: int = 3
    rollout_discount: float = 0.9
    doubtful_branch_probability: float = 0.15
    relation_bonus_map: dict[str, float] | None = None

    # 初始化默认的关系收益加成表。
    def __post_init__(self) -> None:
        if self.relation_bonus_map is None:
            self.relation_bonus_map = {
                "MANIFESTS_AS": 1.0,
                "HAS_LAB_FINDING": 1.15,
                "HAS_IMAGING_FINDING": 1.15,
                "HAS_PATHOGEN": 1.12,
                "DIAGNOSED_BY": 1.2,
                "REQUIRES_DETAIL": 0.8,
                "ASSOCIATED_WITH": 0.7,
            }


class SimulationEngine:
    """根据局部动作和当前假设做浅层前瞻预演。"""

    # 初始化 simulation 参数。
    def __init__(self, config: SimulationConfig | None = None) -> None:
        self.config = config or SimulationConfig()

    # 对一组候选动作做批量浅层预演。
    def simulate_actions(
        self,
        actions: Iterable[MctsAction],
        session_state: SessionState,
        primary_hypothesis: HypothesisCandidate | HypothesisScore | None = None,
    ) -> List[SimulationOutcome]:
        return [
            self.simulate_action(action, session_state, primary_hypothesis)
            for action in actions
        ]

    # 对单个候选动作做正反两分支的浅层收益估计。
    def simulate_action(
        self,
        action: MctsAction,
        session_state: SessionState,
        primary_hypothesis: HypothesisCandidate | HypothesisScore | None = None,
    ) -> SimulationOutcome:
        # simulation 不真的调用 LLM 或患者代理，
        # 而是用动作 prior、关系类型和当前 hypothesis 分数做一个浅层期望收益估计。
        relation_type = str(action.metadata.get("relation_type", ""))
        relation_bonus = float(self.config.relation_bonus_map.get(relation_type, 0.75))
        hypothesis_score = float(primary_hypothesis.score) if primary_hypothesis is not None else 0.0
        positive_probability = self._estimate_positive_probability(action, session_state)
        doubtful_probability = min(self.config.doubtful_branch_probability, max(0.0, 1.0 - positive_probability))
        negative_probability = max(0.05, 1.0 - positive_probability - doubtful_probability)
        contradiction_priority = float(action.metadata.get("contradiction_priority", 0.0))

        # 三条分支分别代表：
        # - positive：目标证据被确认
        # - negative：目标证据被否定
        # - doubtful：患者回答模糊，价值最低但仍可能提供轻微信号
        positive_reward = (
            action.prior_score * relation_bonus * self.config.positive_reward_multiplier
            + hypothesis_score * 0.35
        )
        negative_reward = (
            action.prior_score * (0.25 + contradiction_priority * 0.35) * self.config.negative_reward_multiplier
            + hypothesis_score * 0.10
        )
        doubtful_reward = (
            action.prior_score * 0.20 * self.config.doubtful_reward_multiplier
            + contradiction_priority * 0.10
            + hypothesis_score * 0.05
        )
        expected_reward = (
            positive_probability * positive_reward
            + negative_probability * negative_reward
            + doubtful_probability * doubtful_reward
        )

        return SimulationOutcome(
            action_id=action.action_id,
            expected_reward=expected_reward,
            positive_branch_reward=positive_reward,
            negative_branch_reward=negative_reward,
            depth=2,
            metadata={
                "positive_probability": positive_probability,
                "negative_probability": negative_probability,
                "doubtful_probability": doubtful_probability,
                "doubtful_branch_reward": doubtful_reward,
                "relation_type": relation_type,
            },
        )

    # 从单个动作出发构造一条轻量 rollout 轨迹。
    def rollout_from_action(
        self,
        action: MctsAction,
        state: SessionState,
        patient_context: object | None,
        max_depth: int | None = None,
        primary_hypothesis: HypothesisCandidate | HypothesisScore | None = None,
    ) -> ReasoningTrajectory:
        depth = max_depth or self.config.rollout_max_depth
        outcome = self.simulate_action(action, state, primary_hypothesis)
        hypothesis_id = action.hypothesis_id or "UNKNOWN"
        hypothesis_name = primary_hypothesis.name if primary_hypothesis is not None else "UNKNOWN"
        branch_payloads = self._build_branch_payloads(action, outcome)

        # 单动作 rollout 直接选择期望收益最高的回答分支，构成最轻量的一条前瞻轨迹。
        selected_branch = sorted(branch_payloads, key=lambda item: (-item["weighted_reward"], item["branch"]))[0]

        steps = [
            {
                "stage": "A3",
                "action_id": action.action_id,
                "action_name": action.target_node_name,
                "target_node_id": action.target_node_id,
                "target_node_name": action.target_node_name,
                "question_type_hint": action.metadata.get("question_type_hint", "symptom"),
            },
            {
                "stage": "A4",
                "branch_answer": selected_branch["branch"],
                "expected_reward": outcome.expected_reward,
                "depth": min(depth, self.config.rollout_max_depth),
            },
        ]

        return ReasoningTrajectory(
            trajectory_id=f"trajectory::{action.action_id}",
            final_answer_id=hypothesis_id,
            final_answer_name=hypothesis_name,
            steps=steps,
            score=outcome.expected_reward,
            metadata={"simulation_outcome": outcome.metadata, "branch_evaluations": branch_payloads},
        )

    # 从搜索树节点出发执行多步 rollout，模拟 A3 -> A4 -> route -> A2/A3 的前瞻过程。
    def rollout_from_tree_node(
        self,
        node: TreeNode,
        state: SessionState,
        patient_context: PatientContext,
        router: ReasoningRouter,
        hypothesis_manager: HypothesisManager,
        retriever: GraphRetriever,
        action_builder: ActionBuilder,
        max_depth: int,
        current_hypothesis: HypothesisCandidate | HypothesisScore | None = None,
        competing_hypotheses: list[HypothesisScore] | None = None,
    ) -> ReasoningTrajectory:
        # rollout 只在临时副本上推进，绝不直接污染真实 session_state。
        rollout_state = deepcopy(state)
        hypothesis = self._resolve_hypothesis(node, rollout_state, current_hypothesis)
        alternatives = list(competing_hypotheses or [])
        action = self._extract_action(node)
        total_reward = 0.0
        step_depth = 0
        visited_action_ids: set[str] = set()
        steps: list[dict] = []
        last_stage = "A3"

        while action is not None and step_depth < max_depth:
            step_depth += 1
            visited_action_ids.add(action.action_id)

            # 每一层都做“动作预演 -> 选择最优回答分支 -> route -> 回写临时状态 -> 取下一问”。
            outcome = self.simulate_action(action, rollout_state, hypothesis)
            branch_payloads = self._build_branch_payloads(action, outcome)
            selected_branch = sorted(
                branch_payloads,
                key=lambda item: (-item["weighted_reward"], item["branch"]),
            )[0]
            branch_result: A4DeductiveResult = selected_branch["deductive_result"]
            decision = router.build_deductive_decision(branch_result, action, rollout_state)
            step_reward = selected_branch["reward"] * (self.config.rollout_discount ** (step_depth - 1))
            context_bonus = self._estimate_context_bonus(action, patient_context)
            total_reward += step_reward + context_bonus
            last_stage = decision.next_stage

            # 一旦选中了某条模拟回答，就把它像真实 A4 一样写回 rollout_state，
            # 这样后续 R2/action selection 才能基于“已知新证据”继续展开。
            self._apply_rollout_state_update(
                rollout_state,
                action,
                branch_result,
                step_depth,
                hypothesis_manager,
            )

            if action.target_node_id not in rollout_state.asked_node_ids:
                rollout_state.asked_node_ids.append(action.target_node_id)

            steps.extend(
                [
                    {
                        "stage": "A3",
                        "action_id": action.action_id,
                        "action_name": action.target_node_name,
                        "target_node_id": action.target_node_id,
                        "target_node_name": action.target_node_name,
                        "hypothesis_id": hypothesis.node_id if hypothesis is not None else action.hypothesis_id,
                        "question_type_hint": action.metadata.get("question_type_hint", "symptom"),
                    },
                    {
                        "stage": "A4",
                        "answer_branch": selected_branch["branch"],
                        "existence": branch_result.existence,
                        "certainty": branch_result.certainty,
                        "reasoning": branch_result.reasoning,
                    },
                    {
                        "stage": "ROUTE",
                        "decision_type": decision.decision_type,
                        "next_stage": decision.next_stage,
                        "path_terminal": decision.should_terminate_current_path,
                        "contradiction_explanation": decision.contradiction_explanation,
                    },
                ]
            )

            # 模拟 path 已经满足 STOP 或当前决策要求终止时，不再继续往下 rollout。
            if decision.next_stage == "STOP" or decision.should_terminate_current_path:
                break

            # route 可能让当前主假设保持不变，也可能切到 alternatives 中的下一个候选。
            hypothesis, alternatives = self._advance_hypothesis_after_route(
                hypothesis,
                rollout_state,
                alternatives,
                decision,
                hypothesis_manager,
            )

            if decision.next_stage not in {"A2", "A3"}:
                break

            # 继续为新的 hypothesis / rollout_state 选择下一条 follow-up action。
            action = self._select_follow_up_action(
                hypothesis,
                alternatives,
                rollout_state,
                retriever,
                action_builder,
                visited_action_ids,
            )

            if action is None:
                break

        # 最终轨迹保留 rollout_state 副本，供树节点缓存和后续 reroot/repair 使用。
        final_hypothesis = hypothesis or self._resolve_hypothesis(node, rollout_state, current_hypothesis)
        trajectory = ReasoningTrajectory(
            trajectory_id=f"trajectory::{node.node_id}",
            final_answer_id=final_hypothesis.node_id if final_hypothesis is not None else None,
            final_answer_name=final_hypothesis.name if final_hypothesis is not None else None,
            steps=steps,
            score=total_reward / max(step_depth, 1),
            metadata={
                "rollout_depth": step_depth,
                "last_stage": last_stage,
                "path_terminal": last_stage == "STOP",
                "starting_hypothesis_id": current_hypothesis.node_id if current_hypothesis is not None else None,
                "_rollout_state": rollout_state,
            },
        )
        return trajectory

    # 根据动作类型、红旗程度和历史提问情况估算阳性回答概率。
    def _estimate_positive_probability(
        self,
        action: MctsAction,
        session_state: SessionState,
    ) -> float:
        probability = self.config.positive_branch_probability

        if bool(action.metadata.get("is_red_flag", False)):
            probability += 0.1

        if action.target_node_id in session_state.asked_node_ids:
            probability -= 0.15

        relation_type = str(action.metadata.get("relation_type", ""))

        if relation_type == "REQUIRES_DETAIL":
            probability -= 0.1
        elif relation_type in {"HAS_LAB_FINDING", "DIAGNOSED_BY"}:
            probability += 0.05

        return min(max(probability, 0.1), 0.9)

    # 为单个动作构造 positive / negative / doubtful 三个回答分支。
    def _build_branch_payloads(
        self,
        action: MctsAction,
        outcome: SimulationOutcome,
    ) -> list[dict]:
        positive_probability = float(outcome.metadata.get("positive_probability", self.config.positive_branch_probability))
        negative_probability = float(outcome.metadata.get("negative_probability", 1.0 - positive_probability))
        doubtful_probability = float(outcome.metadata.get("doubtful_probability", self.config.doubtful_branch_probability))
        doubtful_reward = float(
            outcome.metadata.get("doubtful_branch_reward", outcome.expected_reward * self.config.doubtful_reward_multiplier)
        )

        # rollout 统一只看三种标准回答分支，保证 router / deductive judge 可复用同一套下游逻辑。
        return [
            {
                "branch": "positive",
                "probability": positive_probability,
                "reward": outcome.positive_branch_reward,
                "weighted_reward": positive_probability * outcome.positive_branch_reward,
                "deductive_result": A4DeductiveResult(
                    existence="exist",
                    certainty="confident",
                    reasoning=f"模拟回答明确支持“{action.target_node_name}”存在。",
                    supporting_span=f"模拟正向回答：存在 {action.target_node_name}",
                ),
            },
            {
                "branch": "negative",
                "probability": negative_probability,
                "reward": outcome.negative_branch_reward,
                "weighted_reward": negative_probability * outcome.negative_branch_reward,
                "deductive_result": A4DeductiveResult(
                    existence="non_exist",
                    certainty="confident",
                    reasoning=f"模拟回答明确否定“{action.target_node_name}”。",
                    negation_span=f"模拟反向回答：无 {action.target_node_name}",
                ),
            },
            {
                "branch": "doubtful",
                "probability": doubtful_probability,
                "reward": doubtful_reward,
                "weighted_reward": doubtful_probability * doubtful_reward,
                "deductive_result": A4DeductiveResult(
                    existence="exist",
                    certainty="doubt",
                    reasoning=f"模拟回答对“{action.target_node_name}”提供了模糊支持，仍需复核。",
                    supporting_span=f"模拟模糊回答：可能有 {action.target_node_name}",
                    uncertain_span=f"模拟模糊回答：不太确定 {action.target_node_name}",
                ),
            },
        ]

    # 从树节点元数据中提取当前动作。
    def _extract_action(self, node: TreeNode) -> MctsAction | None:
        action = node.metadata.get("action")
        return action if isinstance(action, MctsAction) else None

    # 根据节点元数据或当前状态解析 rollout 的当前假设。
    def _resolve_hypothesis(
        self,
        node: TreeNode,
        state: SessionState,
        current_hypothesis: HypothesisCandidate | HypothesisScore | None,
    ) -> HypothesisCandidate | HypothesisScore | None:
        if current_hypothesis is not None:
            return current_hypothesis

        hypothesis_id = str(node.metadata.get("hypothesis_id") or "")

        for hypothesis in state.candidate_hypotheses:
            if hypothesis.node_id == hypothesis_id:
                return hypothesis

        if len(state.candidate_hypotheses) > 0:
            return sorted(state.candidate_hypotheses, key=lambda item: (-item.score, item.name))[0]

        return None

    # 将 rollout 分支结果回写到临时状态中，供下一层动作扩展使用。
    def _apply_rollout_state_update(
        self,
        state: SessionState,
        action: MctsAction,
        deductive_result: A4DeductiveResult,
        turn_index: int,
        hypothesis_manager: HypothesisManager,
    ) -> None:
        slot_status = "unknown"
        slot_certainty = "unknown"

        if deductive_result.existence == "exist":
            slot_status = "true"
        elif deductive_result.existence == "non_exist":
            slot_status = "false"

        if deductive_result.certainty == "confident":
            slot_certainty = "certain"
        elif deductive_result.certainty == "doubt":
            slot_certainty = "uncertain"

        # rollout 里的模拟回答也会同步写槽位和 evidence_state，
        # 这样后续 hypothesis feedback 与 follow-up selection 才能读取到完整上下文。
        state.slots[action.target_node_id] = SlotState(
            node_id=action.target_node_id,
            status=slot_status,
            certainty=slot_certainty,
            evidence=[deductive_result.supporting_span or deductive_result.negation_span or deductive_result.reasoning],
            source_turns=[turn_index],
            metadata={
                "source_stage": "SIMULATION",
                "action_id": action.action_id,
                "normalized_name": action.target_node_name,
            },
        )
        state.evidence_states[action.target_node_id] = EvidenceState(
            node_id=action.target_node_id,
            existence=deductive_result.existence,
            certainty=deductive_result.certainty,
            reasoning=deductive_result.reasoning,
            source_turns=[turn_index],
            metadata={
                "action_id": action.action_id,
                "hypothesis_id": action.hypothesis_id,
                "relation_type": action.metadata.get("relation_type"),
            },
        )

        related_ids = [action.hypothesis_id] if action.hypothesis_id is not None else None

        # 最后把这条模拟证据反馈回 hypothesis 排名，供下一层 rollout 继续沿着“更新后的诊断竞争态”推进。
        state.candidate_hypotheses = hypothesis_manager.apply_evidence_feedback(
            state.candidate_hypotheses,
            state.evidence_states[action.target_node_id],
            related_ids,
        )

    # 根据路由结果推进主假设与备选假设。
    def _advance_hypothesis_after_route(
        self,
        current_hypothesis: HypothesisCandidate | HypothesisScore | None,
        state: SessionState,
        alternatives: list[HypothesisScore],
        decision: object,
        hypothesis_manager: HypothesisManager,
    ) -> tuple[HypothesisCandidate | HypothesisScore | None, list[HypothesisScore]]:
        ranked = hypothesis_manager.select_expandable_hypotheses(state.candidate_hypotheses, top_k=3)
        ranked_alternatives = [
            item
            for item in ranked
            if current_hypothesis is None or item.node_id != current_hypothesis.node_id
        ]

        next_stage = getattr(decision, "next_stage", "A3")

        if next_stage == "A2":
            next_hypothesis = ranked_alternatives[0] if len(ranked_alternatives) > 0 else current_hypothesis
            remaining = ranked_alternatives[1:] if len(ranked_alternatives) > 1 else []
            return next_hypothesis, remaining

        return current_hypothesis, ranked_alternatives or alternatives

    # 从当前假设出发选择下一条最值得继续验证的动作。
    def _select_follow_up_action(
        self,
        current_hypothesis: HypothesisCandidate | HypothesisScore | None,
        alternatives: list[HypothesisScore],
        state: SessionState,
        retriever: GraphRetriever,
        action_builder: ActionBuilder,
        visited_action_ids: set[str],
    ) -> MctsAction | None:
        if current_hypothesis is None:
            return None

        # follow-up selection 仍沿用正式链路里的 R2 + action_builder，
        # 只是额外过滤 rollout 里已经访问过的动作和已问过节点。
        rows = retriever.retrieve_r2_expected_evidence(current_hypothesis, state, top_k=4)
        actions = action_builder.build_verification_actions(
            rows,
            hypothesis_id=current_hypothesis.node_id,
            topic_id=current_hypothesis.label,
            competing_hypotheses=alternatives,
            current_hypothesis=current_hypothesis,
            session_state=state,
        )

        for action in actions:
            if action.action_id in visited_action_ids:
                continue

            if action.target_node_id in state.asked_node_ids:
                continue

            return action

        return None

    # 给 rollout 注入患者上下文信息，避免 patient_context 形参完全闲置。
    def _estimate_context_bonus(self, action: MctsAction, patient_context: PatientContext) -> float:
        raw_text = patient_context.raw_text
        normalized_feature_names = {item.normalized_name for item in patient_context.clinical_features}
        target_name = action.target_node_name
        question_type_hint = str(action.metadata.get("question_type_hint", "symptom"))

        # 如果目标证据已经在患者原话里出现过，rollout 会给一点轻量 bonus，
        # 反映“这条路径更贴近当前会话上下文”。
        if target_name in raw_text or target_name in normalized_feature_names:
            return 0.1

        if question_type_hint == "risk" and len(patient_context.general_info.epidemiology) > 0:
            return 0.05

        return 0.0
