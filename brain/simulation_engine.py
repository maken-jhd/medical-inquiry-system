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
    EvidenceState,
    HypothesisCandidate,
    HypothesisScore,
    MctsAction,
    PendingActionResult,
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
    enable_multi_branch_rollout: bool = True
    branch_budget_per_action: int = 2
    enable_anti_collapse_penalty: bool = True
    low_anchor_branch_penalty: float = 0.08
    low_anchor_threshold: float = 0.18

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
                "stage": "PENDING_ACTION",
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

    # 从搜索树节点出发执行多步 rollout，模拟 A3 -> 回答解释 -> route -> A2/A3 的前瞻过程。
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
        trajectories = self.rollout_trajectories_from_tree_node(
            node,
            state,
            patient_context,
            router,
            hypothesis_manager,
            retriever,
            action_builder,
            max_depth,
            current_hypothesis=current_hypothesis,
            competing_hypotheses=competing_hypotheses,
        )
        if len(trajectories) > 0:
            return trajectories[0]

        hypothesis = self._resolve_hypothesis(node, state, current_hypothesis)
        return ReasoningTrajectory(
            trajectory_id=f"trajectory::{node.node_id}",
            final_answer_id=hypothesis.node_id if hypothesis is not None else None,
            final_answer_name=hypothesis.name if hypothesis is not None else None,
            steps=[],
            score=0.0,
            metadata={"rollout_depth": 0, "last_stage": "A3", "path_terminal": False},
        )

    # 从搜索树节点出发返回一组 rollout 轨迹，避免每个 child 只复制单条最乐观路径。
    def rollout_trajectories_from_tree_node(
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
    ) -> list[ReasoningTrajectory]:
        action = self._extract_action(node)
        if action is None:
            return []

        hypothesis = self._resolve_hypothesis(node, state, current_hypothesis)
        initial_outcome = self.simulate_action(action, state, hypothesis)
        branch_payloads = self._build_branch_payloads(action, initial_outcome)
        branch_seeds = self._select_rollout_branch_seeds(branch_payloads)

        trajectories: list[ReasoningTrajectory] = []
        for seed in branch_seeds:
            trajectories.append(
                self._rollout_from_tree_node_with_seed(
                    node,
                    state,
                    patient_context,
                    router,
                    hypothesis_manager,
                    retriever,
                    action_builder,
                    max_depth,
                    branch_seed=seed,
                    branch_budget=len(branch_seeds),
                    current_hypothesis=current_hypothesis,
                    competing_hypotheses=competing_hypotheses,
                )
            )
        return trajectories

    # 真正执行单条 rollout；第一步可强制选择某个分支 seed，后续再回到贪心跟进。
    def _rollout_from_tree_node_with_seed(
        self,
        node: TreeNode,
        state: SessionState,
        patient_context: PatientContext,
        router: ReasoningRouter,
        hypothesis_manager: HypothesisManager,
        retriever: GraphRetriever,
        action_builder: ActionBuilder,
        max_depth: int,
        *,
        branch_seed: dict,
        branch_budget: int,
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
        branch_trace: list[str] = []
        anti_collapse_penalty_total = 0.0

        while action is not None and step_depth < max_depth:
            step_depth += 1
            visited_action_ids.add(action.action_id)

            # 每一层都做“动作预演 -> 选择最优回答分支 -> route -> 回写临时状态 -> 取下一问”。
            outcome = self.simulate_action(action, rollout_state, hypothesis)
            branch_payloads = self._build_branch_payloads(action, outcome)
            if step_depth == 1:
                selected_branch = self._find_branch_payload(branch_payloads, str(branch_seed.get("branch") or ""))
                selection_mode = "seeded"
            else:
                selected_branch = None
                selection_mode = "greedy"
            if selected_branch is None:
                selected_branch = self._select_best_branch_payload(branch_payloads)
            branch_result: PendingActionResult = selected_branch["pending_action_result"]
            decision = router.build_pending_action_decision(branch_result, action, rollout_state)
            anti_collapse_penalty, anti_collapse_reason = self._compute_anti_collapse_penalty(
                selected_branch=selected_branch,
                branch_payloads=branch_payloads,
                current_hypothesis=hypothesis,
                step_depth=step_depth,
            )
            anti_collapse_penalty_total += anti_collapse_penalty
            step_reward = max(selected_branch["reward"] - anti_collapse_penalty, 0.0) * (
                self.config.rollout_discount ** (step_depth - 1)
            )
            context_bonus = self._estimate_context_bonus(action, patient_context)
            total_reward += step_reward + context_bonus
            last_stage = decision.next_stage
            branch_trace.append(str(selected_branch["branch"]))

            # 一旦选中了某条模拟回答，就把它像真实 pending_action 一样写回 rollout_state，
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
                        "stage": "PENDING_ACTION",
                        "answer_branch": selected_branch["branch"],
                        "polarity": branch_result.polarity,
                        "resolution": branch_result.resolution,
                        "reasoning": branch_result.reasoning,
                        "branch_selection_mode": selection_mode,
                        "anti_collapse_penalty": round(anti_collapse_penalty, 4),
                        "anti_collapse_reason": anti_collapse_reason,
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
            trajectory_id=f"trajectory::{node.node_id}::{str(branch_seed.get('branch') or 'seed')}",
            final_answer_id=final_hypothesis.node_id if final_hypothesis is not None else None,
            final_answer_name=final_hypothesis.name if final_hypothesis is not None else None,
            steps=steps,
            score=total_reward / max(step_depth, 1),
            metadata={
                "rollout_depth": step_depth,
                "last_stage": last_stage,
                "path_terminal": last_stage == "STOP",
                "starting_hypothesis_id": current_hypothesis.node_id if current_hypothesis is not None else None,
                "starting_hypothesis_observed_anchor_score": (
                    float(getattr(current_hypothesis, "metadata", {}).get("observed_anchor_score", 0.0) or 0.0)
                    if current_hypothesis is not None
                    else 0.0
                ),
                "rollout_branch_mode": "multi_branch" if branch_budget > 1 else "single_branch",
                "branch_budget": branch_budget,
                "branch_seed": str(branch_seed.get("branch") or ""),
                "branch_trace": branch_trace,
                "anti_collapse_penalty_total": round(anti_collapse_penalty_total, 4),
                "anti_collapse_triggered": anti_collapse_penalty_total > 0.0,
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
                "pending_action_result": PendingActionResult(
                    action_type=action.action_type,
                    target_node_id=action.target_node_id,
                    target_node_name=action.target_node_name,
                    polarity="present",
                    resolution="clear",
                    reasoning=f"模拟回答明确支持“{action.target_node_name}”存在。",
                    supporting_span=f"模拟正向回答：存在 {action.target_node_name}",
                ),
            },
            {
                "branch": "negative",
                "probability": negative_probability,
                "reward": outcome.negative_branch_reward,
                "weighted_reward": negative_probability * outcome.negative_branch_reward,
                "pending_action_result": PendingActionResult(
                    action_type=action.action_type,
                    target_node_id=action.target_node_id,
                    target_node_name=action.target_node_name,
                    polarity="absent",
                    resolution="clear",
                    reasoning=f"模拟回答明确否定“{action.target_node_name}”。",
                    negation_span=f"模拟反向回答：无 {action.target_node_name}",
                ),
            },
            {
                "branch": "doubtful",
                "probability": doubtful_probability,
                "reward": doubtful_reward,
                "weighted_reward": doubtful_probability * doubtful_reward,
                "pending_action_result": PendingActionResult(
                    action_type=action.action_type,
                    target_node_id=action.target_node_id,
                    target_node_name=action.target_node_name,
                    polarity="unclear",
                    resolution="hedged",
                    reasoning=f"模拟回答对“{action.target_node_name}”提供了模糊支持，仍需复核。",
                    supporting_span=f"模拟模糊回答：可能有 {action.target_node_name}",
                    uncertain_span=f"模拟模糊回答：不太确定 {action.target_node_name}",
                ),
            },
        ]

    # rollout seed 至少保留正向与一个非正向分支，避免所有轨迹都复制同一条乐观路径。
    def _select_rollout_branch_seeds(self, branch_payloads: list[dict]) -> list[dict]:
        ranked = sorted(
            branch_payloads,
            key=lambda item: (-float(item["weighted_reward"]), item["branch"]),
        )
        if len(ranked) == 0:
            return []

        budget = max(int(self.config.branch_budget_per_action), 1)
        if not self.config.enable_multi_branch_rollout or budget <= 1 or len(ranked) == 1:
            return [ranked[0]]

        selected: list[dict] = []
        selected_branches: set[str] = set()

        def add_payload(payload: dict | None) -> None:
            if payload is None:
                return
            branch = str(payload.get("branch") or "")
            if len(branch) == 0 or branch in selected_branches:
                return
            selected.append(payload)
            selected_branches.add(branch)

        positive_branch = self._find_branch_payload(ranked, "positive")
        non_positive_candidates = [item for item in ranked if str(item.get("branch") or "") != "positive"]
        non_positive_branch = None
        if len(non_positive_candidates) > 0:
            non_positive_branch = sorted(
                non_positive_candidates,
                key=lambda item: (
                    str(item.get("branch") or "") != "negative",
                    -float(item.get("weighted_reward", 0.0) or 0.0),
                    -float(item.get("probability", 0.0) or 0.0),
                ),
            )[0]

        add_payload(ranked[0])
        add_payload(positive_branch)
        add_payload(non_positive_branch)

        for payload in ranked:
            if len(selected) >= budget:
                break
            add_payload(payload)

        return selected[:budget]

    def _find_branch_payload(self, branch_payloads: list[dict], branch_name: str) -> dict | None:
        for payload in branch_payloads:
            if str(payload.get("branch") or "") == branch_name:
                return payload
        return None

    def _select_best_branch_payload(self, branch_payloads: list[dict]) -> dict:
        return sorted(
            branch_payloads,
            key=lambda item: (-float(item["weighted_reward"]), item["branch"]),
        )[0]

    # 当真实 observed anchor 很弱时，限制 rollout 仅靠乐观正向分支把某个答案一路抬高。
    def _compute_anti_collapse_penalty(
        self,
        *,
        selected_branch: dict,
        branch_payloads: list[dict],
        current_hypothesis: HypothesisCandidate | HypothesisScore | None,
        step_depth: int,
    ) -> tuple[float, str]:
        if not self.config.enable_anti_collapse_penalty:
            return 0.0, ""

        if step_depth != 1 or str(selected_branch.get("branch") or "") != "positive":
            return 0.0, ""

        if current_hypothesis is None:
            return 0.0, ""

        metadata = getattr(current_hypothesis, "metadata", {})
        observed_anchor_score = float(metadata.get("observed_anchor_score", 0.0) or 0.0)
        exact_scope_anchor_score = float(metadata.get("exact_scope_anchor_score", 0.0) or 0.0)
        if (
            observed_anchor_score > self.config.low_anchor_threshold
            or exact_scope_anchor_score >= self.config.low_anchor_threshold * 0.75
        ):
            return 0.0, ""

        best_non_positive = max(
            (
                float(item.get("weighted_reward", 0.0) or 0.0)
                for item in branch_payloads
                if str(item.get("branch") or "") != "positive"
            ),
            default=0.0,
        )
        dominance_margin = max(float(selected_branch.get("weighted_reward", 0.0) or 0.0) - best_non_positive, 0.0)
        if dominance_margin <= 0.0:
            return 0.0, ""

        penalty = min(
            self.config.low_anchor_branch_penalty * (1.0 + dominance_margin),
            float(selected_branch.get("reward", 0.0) or 0.0) * 0.35,
        )
        return penalty, "low_observed_anchor_positive_branch_collapse_risk"

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
        pending_action_result: PendingActionResult,
        turn_index: int,
        hypothesis_manager: HypothesisManager,
    ) -> None:
        slot_status = "unknown"
        slot_resolution = "unknown"

        if pending_action_result.polarity == "present":
            slot_status = "true"
        elif pending_action_result.polarity == "absent":
            slot_status = "false"

        if pending_action_result.resolution == "clear":
            slot_resolution = "clear"
        elif pending_action_result.resolution == "hedged":
            slot_resolution = "hedged"

        # rollout 里的模拟回答也会同步写槽位和 evidence_state，
        # 这样后续 hypothesis feedback 与 follow-up selection 才能读取到完整上下文。
        state.slots[action.target_node_id] = SlotState(
            node_id=action.target_node_id,
            status=slot_status,
            polarity=pending_action_result.polarity,
            resolution=slot_resolution,
            evidence=[
                pending_action_result.supporting_span
                or pending_action_result.negation_span
                or pending_action_result.uncertain_span
                or pending_action_result.reasoning
            ],
            source_turns=[turn_index],
            metadata={
                "source_stage": "SIMULATION",
                "action_id": action.action_id,
                "normalized_name": action.target_node_name,
            },
        )
        state.evidence_states[action.target_node_id] = EvidenceState(
            node_id=action.target_node_id,
            polarity=pending_action_result.polarity,
            existence=(
                "exist"
                if pending_action_result.polarity == "present"
                else "non_exist"
                if pending_action_result.polarity == "absent"
                else "unknown"
            ),
            resolution=pending_action_result.resolution,
            reasoning=pending_action_result.reasoning,
            source_turns=[turn_index],
            metadata={
                "action_id": action.action_id,
                "hypothesis_id": action.hypothesis_id,
                "relation_type": action.metadata.get("relation_type"),
            },
        )

        related_ids = [action.hypothesis_id] if action.hypothesis_id is not None else None
        feedback_weights = hypothesis_manager.resolve_evidence_feedback_weights(
            state.candidate_hypotheses,
            state.evidence_states[action.target_node_id],
            related_hypothesis_ids=related_ids,
        )
        state.evidence_states[action.target_node_id].metadata["related_hypothesis_feedback_weights"] = dict(
            feedback_weights
        )

        # 最后把这条模拟证据反馈回 hypothesis 排名，供下一层 rollout 继续沿着“更新后的诊断竞争态”推进。
        state.candidate_hypotheses = hypothesis_manager.apply_evidence_feedback(
            state.candidate_hypotheses,
            state.evidence_states[action.target_node_id],
            related_ids,
            feedback_weights=feedback_weights,
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
