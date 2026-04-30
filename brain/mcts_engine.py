"""负责基于 UCT 在候选动作和搜索树节点中执行选择与回传。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from math import log, sqrt
from typing import Iterable, Optional, Sequence

from .search_tree import SearchTree
from .types import MctsAction, SessionState, SimulationOutcome, TreeNode


@dataclass
class MctsConfig:
    """保存 UCT 选择阶段的核心超参数。"""

    exploration_constant: float = 2.0
    prior_weight: float = 0.35
    simulation_weight: float = 0.45
    unvisited_bonus: float = 0.2
    num_rollouts: int = 8
    max_depth: int = 6
    max_child_nodes: int = 4
    discount_factor: float = 1.0
    max_kg_triplets: int = 15


class MctsEngine:
    """根据历史统计和 simulation 结果选择下一步动作。"""

    # 初始化 UCT 选择器配置。
    def __init__(self, config: MctsConfig | None = None) -> None:
        self.config = config or MctsConfig()

    # 构造当前状态的稳定签名，供访问统计与缓存复用。
    def build_state_signature(
        self,
        session_state: SessionState,
        hypothesis_id: Optional[str] = None,
    ) -> str:
        positive_slots = sorted(
            f"{slot.node_id}:{slot.resolution}" for slot in session_state.slots.values() if slot.status == "true"
        )
        negative_slots = sorted(
            f"{slot.node_id}:{slot.resolution}" for slot in session_state.slots.values() if slot.status == "false"
        )
        active_topics = sorted(session_state.active_topics)
        payload = "|".join(
            [
                f"H={hypothesis_id or 'NONE'}",
                f"P={';'.join(positive_slots)}",
                f"N={';'.join(negative_slots)}",
                f"T={';'.join(active_topics)}",
                f"Q={session_state.metadata.get('pending_action_id', '')}",
            ]
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    # 按照 UCT 对候选动作打分并返回当前最优动作。
    def select_action(
        self,
        actions: Iterable[MctsAction],
        session_state: SessionState,
        simulation_outcomes: Iterable[SimulationOutcome] | None = None,
        state_signature: Optional[str] = None,
    ) -> Optional[MctsAction]:
        action_list = list(actions)

        if len(action_list) == 0:
            return None

        parent_signature = state_signature or self.build_state_signature(session_state)
        parent_visits = session_state.state_visit_stats.get(parent_signature)
        parent_visit_count = parent_visits.visit_count if parent_visits is not None else 0
        simulation_map = {
            outcome.action_id: outcome for outcome in (simulation_outcomes or [])
        }

        ranked = sorted(
            action_list,
            key=lambda action: (
                -self.score_action(action, session_state, parent_visit_count, simulation_map.get(action.action_id)),
                action.target_node_name,
            ),
        )
        return ranked[0]

    # 在搜索树中选择当前最值得继续向下扩展的叶子节点。
    def select_leaf(self, tree: SearchTree) -> Optional[TreeNode]:
        if tree.root_id is None:
            return None

        current = tree.get_node(tree.root_id)

        while True:
            # 当前节点自己已经终止时，说明这条分支不再值得继续扩展。
            if current.terminal:
                return None

            # 没有 child 就是真正可扩展的叶子，直接返回给 expand + rollout 使用。
            if len(current.children_ids) == 0:
                return current

            # tree policy 只在“仍可继续”的孩子里挑选；
            # 已 terminal 的 child 不再参与 UCT 打分。
            children = [
                tree.get_node(child_id)
                for child_id in current.children_ids
                if not tree.get_node(child_id).terminal
            ]

            if len(children) == 0:
                # 如果所有孩子都已经终止，把当前节点也标记为 terminal，避免后续反复访问空分支。
                tree.mark_terminal(current.node_id, {"terminal_reason": "all_children_terminal"})
                return None

            parent_visit_count = max(current.visit_count, 1)
            current = sorted(
                children,
                key=lambda item: (
                    -self.score_tree_node(item, parent_visit_count),
                    item.depth,
                    item.node_id,
                ),
            )[0]

            # 一旦遇到未访问过的节点，或它本身还是叶子，就交给 rollout 阶段处理。
            if current.visit_count == 0 or len(current.children_ids) == 0:
                return current

    # 将候选动作扩展为搜索树中的子节点。
    def expand_node(
        self,
        tree: SearchTree,
        parent_node_id: str,
        actions: Iterable[MctsAction],
    ) -> list[TreeNode]:
        parent = tree.get_node(parent_node_id)
        created: list[TreeNode] = []

        for index, action in enumerate(actions):
            # 扩展宽度由 max_child_nodes 控制，避免单个叶子分叉过多拖慢 rollout。
            if index >= self.config.max_child_nodes:
                break

            child_id = f"{parent.node_id}::{action.action_id}"

            if child_id in tree.nodes:
                # 同一动作已经扩过时直接复用，避免重复创建节点打乱 visit/value 统计。
                created.append(tree.get_node(child_id))
                continue

            # child 节点只保存继续搜索所需的最小元数据：
            # 动作本体、目标节点、prior 分数和当前 hypothesis 绑定关系。
            child = TreeNode(
                node_id=child_id,
                state_signature=child_id,
                parent_id=parent.node_id,
                action_from_parent=action.action_id,
                stage="A3",
                depth=parent.depth + 1,
                metadata={
                    "action": action,
                    "hypothesis_id": action.hypothesis_id,
                    "topic_id": action.topic_id,
                    "target_node_id": action.target_node_id,
                    "target_node_name": action.target_node_name,
                    "prior_score": action.prior_score,
                },
            )
            tree.add_node(child)
            tree.add_edge(parent.node_id, child.node_id)
            created.append(child)

        return created

    # 将奖励值从叶子节点沿父链回传到根节点。
    def backpropagate(self, tree: SearchTree, node_id: str, reward: float) -> None:
        tree.backpropagate(node_id, reward)

    # 计算单个动作的 UCT 分数。
    def score_action(
        self,
        action: MctsAction,
        session_state: SessionState,
        parent_visit_count: int,
        simulation_outcome: SimulationOutcome | None = None,
    ) -> float:
        stats = session_state.action_stats.get(action.action_id)
        q_value = stats.average_value if stats is not None else 0.0
        visit_count = stats.visit_count if stats is not None else 0
        simulation_reward = simulation_outcome.expected_reward if simulation_outcome is not None else 0.0
        prior_score = action.prior_score * self.config.prior_weight

        blended_value = q_value + simulation_reward * self.config.simulation_weight + prior_score

        if visit_count == 0:
            exploration = self.config.exploration_constant * sqrt(log(parent_visit_count + 2))
            return blended_value + exploration + self.config.unvisited_bonus

        exploration = self.config.exploration_constant * sqrt(
            log(parent_visit_count + 2) / visit_count
        )
        return blended_value + exploration

    # 按树节点访问统计计算用于 tree policy 的 UCT 分数。
    def score_tree_node(self, node: TreeNode, parent_visit_count: int) -> float:
        prior_score = float(node.metadata.get("prior_score", 0.0)) * self.config.prior_weight

        if node.visit_count == 0:
            exploration = self.config.exploration_constant * sqrt(log(parent_visit_count + 2))
            return prior_score + exploration + self.config.unvisited_bonus

        exploration = self.config.exploration_constant * sqrt(
            log(parent_visit_count + 2) / node.visit_count
        )
        return node.average_value + prior_score + exploration

    # 从根节点的子节点中选择当前平均价值最高的动作。
    def select_root_action(
        self,
        tree: SearchTree,
        excluded_target_node_ids: Sequence[str] | None = None,
    ) -> Optional[MctsAction]:
        if tree.root_id is None:
            return None

        root = tree.get_node(tree.root_id)
        excluded_ids = set(excluded_target_node_ids or [])
        children = []

        for child_id in root.children_ids:
            child = tree.get_node(child_id)
            action = child.metadata.get("action")

            # 根节点选真实下一问时，显式排除已经问过的 target，减少重复追问。
            if isinstance(action, MctsAction) and action.target_node_id in excluded_ids:
                continue

            children.append(child)

        if len(children) == 0:
            return None

        # 根节点选择更偏 exploitation：
        # 先看 average_value，再看 visit_count，最后才看 prior_score。
        best_child = sorted(
            children,
            key=lambda item: (
                -item.average_value,
                -item.visit_count,
                -float(item.metadata.get("prior_score", 0.0)),
                item.node_id,
            ),
        )[0]
        action = best_child.metadata.get("action")
        return action if isinstance(action, MctsAction) else None
