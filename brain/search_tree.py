"""实现 MCTS 搜索过程中使用的显式树结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .types import TreeNode


@dataclass
class SearchTree:
    """管理搜索树节点、父子关系与回传统计。"""

    nodes: Dict[str, TreeNode] = field(default_factory=dict)
    root_id: Optional[str] = None

    # 向搜索树中添加一个节点。
    def add_node(self, node: TreeNode) -> TreeNode:
        self.nodes[node.node_id] = node

        if node.parent_id is None and self.root_id is None:
            self.root_id = node.node_id

        return node

    # 创建父子关系，并自动写入双方字段。
    def add_edge(self, parent_id: str, child_id: str) -> None:
        parent = self.nodes[parent_id]
        child = self.nodes[child_id]
        child.parent_id = parent_id

        if child_id not in parent.children_ids:
            parent.children_ids.append(child_id)

    # 获取指定节点；不存在时抛出异常。
    def get_node(self, node_id: str) -> TreeNode:
        return self.nodes[node_id]

    # 获取当前所有叶子节点。
    def get_leaf_nodes(self) -> List[TreeNode]:
        return [node for node in self.nodes.values() if len(node.children_ids) == 0]

    # 将节点标记为终局节点。
    def mark_terminal(self, node_id: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        node = self.nodes[node_id]
        node.terminal = True

        if metadata is not None:
            node.metadata.update(metadata)

    # 从某个节点开始向上回传 reward。
    def backpropagate(self, node_id: str, reward: float) -> None:
        current_id: Optional[str] = node_id

        while current_id is not None:
            node = self.nodes[current_id]
            node.visit_count += 1
            node.total_value += reward
            node.average_value = node.total_value / node.visit_count
            current_id = node.parent_id
