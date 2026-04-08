"""维护会话内存 DAG，用于保证追问过程按主题深入。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


DagNodeStatus = str


@dataclass
class SessionDagNode:
    """表示会话 DAG 中的一个追问节点。"""

    node_id: str
    topic_id: str
    status: DagNodeStatus = "open"
    parent_id: Optional[str] = None
    child_ids: List[str] = field(default_factory=list)
    depth: int = 0
    metadata: Dict[str, object] = field(default_factory=dict)


class SessionDag:
    """轻量级会话内存 DAG，用于维持 DFS 风格的连续追问。"""

    # 初始化 DAG 容器和主题根节点映射。
    def __init__(self) -> None:
        self.nodes: Dict[str, SessionDagNode] = {}
        self.topic_roots: Dict[str, str] = {}

    # 确保指定节点存在；若不存在则自动创建。
    def ensure_node(
        self,
        node_id: str,
        topic_id: str,
        parent_id: Optional[str] = None,
        metadata: Optional[Dict[str, object]] = None,
    ) -> SessionDagNode:
        if node_id not in self.nodes:
            depth = 0

            if parent_id is not None and parent_id in self.nodes:
                depth = self.nodes[parent_id].depth + 1

            self.nodes[node_id] = SessionDagNode(
                node_id=node_id,
                topic_id=topic_id,
                parent_id=parent_id,
                depth=depth,
                metadata=metadata or {},
            )

        return self.nodes[node_id]

    # 激活一个新的主题分支，并记录其根节点。
    def activate_topic(self, topic_id: str, root_node_id: str, metadata: Optional[Dict[str, object]] = None) -> SessionDagNode:
        root = self.ensure_node(root_node_id, topic_id=topic_id, metadata=metadata)
        self.topic_roots[topic_id] = root_node_id
        return root

    # 在指定父节点下新增一个子节点。
    def add_child(
        self,
        parent_id: str,
        child_node_id: str,
        metadata: Optional[Dict[str, object]] = None,
    ) -> SessionDagNode:
        if parent_id not in self.nodes:
            raise KeyError(f"Unknown parent node: {parent_id}")

        parent = self.nodes[parent_id]
        child = self.ensure_node(
            child_node_id,
            topic_id=parent.topic_id,
            parent_id=parent_id,
            metadata=metadata,
        )

        if child_node_id not in parent.child_ids:
            parent.child_ids.append(child_node_id)

        return child

    # 将节点标记为“已回答”。
    def mark_answered(self, node_id: str) -> None:
        self.nodes[node_id].status = "answered"

    # 将节点标记为“已关闭”。
    def mark_closed(self, node_id: str) -> None:
        self.nodes[node_id].status = "closed"

    # 将节点标记为“已跳过”。
    def mark_skipped(self, node_id: str) -> None:
        self.nodes[node_id].status = "skipped"

    # 检查某个主题下是否仍存在可继续追问的开放节点。
    def has_open_nodes(self, topic_id: str) -> bool:
        return any(node.topic_id == topic_id and node.status == "open" for node in self.nodes.values())

    # 在指定主题分支内按 DFS 顺序查找下一个开放节点。
    def next_open_node_in_branch(self, topic_id: str) -> Optional[SessionDagNode]:
        root_id = self.topic_roots.get(topic_id)

        if root_id is None:
            return None

        return self._dfs_find_open(root_id)

    # 在所有主题中查找下一个可追问节点。
    def next_open_node(self) -> Optional[SessionDagNode]:
        for topic_id in self.topic_roots:
            candidate = self.next_open_node_in_branch(topic_id)

            if candidate is not None:
                return candidate

        return None

    # 递归执行 DFS，找到当前分支里的第一个开放节点。
    def _dfs_find_open(self, node_id: str) -> Optional[SessionDagNode]:
        node = self.nodes[node_id]

        if node.status == "open":
            return node

        for child_id in node.child_ids:
            child = self._dfs_find_open(child_id)

            if child is not None:
                return child

        return None
