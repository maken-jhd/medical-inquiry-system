"""测试搜索树的节点扩展与回传统计。"""

from brain.search_tree import SearchTree
from brain.types import TreeNode


# 验证搜索树能创建父子关系并正确回传奖励。
def test_search_tree_backpropagates_reward() -> None:
    tree = SearchTree()
    root = TreeNode(
        node_id="root",
        state_signature="root",
        parent_id=None,
        action_from_parent=None,
        stage="A2",
        depth=0,
    )
    child = TreeNode(
        node_id="child",
        state_signature="child",
        parent_id="root",
        action_from_parent="a1",
        stage="A3",
        depth=1,
    )
    tree.add_node(root)
    tree.add_node(child)
    tree.add_edge("root", "child")

    tree.backpropagate("child", 0.8)

    assert tree.get_node("child").visit_count == 1
    assert tree.get_node("root").visit_count == 1
    assert tree.get_node("root").average_value == 0.8
