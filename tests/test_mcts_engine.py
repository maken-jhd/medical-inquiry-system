"""测试 UCT 选择器的基础动作排序行为。"""

from brain.mcts_engine import MctsEngine
from brain.search_tree import SearchTree
from brain.types import ActionStats, MctsAction, SessionState, SimulationOutcome, StateVisitStats, TreeNode


# 验证 UCT 选择器会优先选择 simulation 收益更高的动作。
def test_mcts_engine_prefers_higher_simulation_reward() -> None:
    engine = MctsEngine()
    state = SessionState(
        session_id="s1",
        action_stats={
            "a1": ActionStats(action_id="a1", visit_count=2, total_value=1.0, average_value=0.5),
            "a2": ActionStats(action_id="a2", visit_count=2, total_value=1.0, average_value=0.5),
        },
        state_visit_stats={
            "sig": StateVisitStats(state_signature="sig", visit_count=4),
        },
    )
    a1 = MctsAction(
        action_id="a1",
        action_type="verify_evidence",
        target_node_id="n1",
        target_node_label="Symptom",
        target_node_name="发热",
        prior_score=1.0,
    )
    a2 = MctsAction(
        action_id="a2",
        action_type="verify_evidence",
        target_node_id="n2",
        target_node_label="LabFinding",
        target_node_name="低氧血症",
        prior_score=1.0,
    )
    outcomes = [
        SimulationOutcome(action_id="a1", expected_reward=0.2),
        SimulationOutcome(action_id="a2", expected_reward=0.9),
    ]

    selected = engine.select_action([a1, a2], state, outcomes, state_signature="sig")

    assert selected is not None
    assert selected.action_id == "a2"


# 验证 tree policy 会沿树向下选择高价值路径，而不是直接把所有叶子摊平排序。
def test_mcts_engine_select_leaf_descends_by_tree_policy() -> None:
    engine = MctsEngine()
    tree = SearchTree()
    root = TreeNode(node_id="root", state_signature="root", parent_id=None, action_from_parent=None, stage="A2", depth=0)
    child_a = TreeNode(
        node_id="root::a",
        state_signature="a",
        parent_id="root",
        action_from_parent="a",
        stage="A3",
        depth=1,
        visit_count=3,
        total_value=1.2,
        average_value=0.4,
        metadata={"prior_score": 0.1},
    )
    child_b = TreeNode(
        node_id="root::b",
        state_signature="b",
        parent_id="root",
        action_from_parent="b",
        stage="A3",
        depth=1,
        visit_count=2,
        total_value=1.8,
        average_value=0.9,
        metadata={"prior_score": 0.8},
    )
    grandchild = TreeNode(
        node_id="root::b::c",
        state_signature="c",
        parent_id="root::b",
        action_from_parent="c",
        stage="A3",
        depth=2,
        metadata={"prior_score": 0.6},
    )
    tree.add_node(root)
    tree.add_node(child_a)
    tree.add_node(child_b)
    tree.add_node(grandchild)
    tree.add_edge("root", "root::a")
    tree.add_edge("root", "root::b")
    tree.add_edge("root::b", "root::b::c")
    root.visit_count = 5

    selected_leaf = engine.select_leaf(tree)

    assert selected_leaf is not None
    assert selected_leaf.node_id == "root::b::c"


# 验证根动作选择会跳过当前会话中已经追问过的节点，避免重复追问。
def test_mcts_engine_select_root_action_skips_already_asked_targets() -> None:
    engine = MctsEngine()
    tree = SearchTree()
    root = TreeNode(node_id="root", state_signature="root", parent_id=None, action_from_parent=None, stage="A2", depth=0)
    repeated_child = TreeNode(
        node_id="root::repeat",
        state_signature="repeat",
        parent_id="root",
        action_from_parent="repeat",
        stage="A3",
        depth=1,
        visit_count=5,
        total_value=4.0,
        average_value=0.8,
        metadata={
            "prior_score": 1.0,
            "action": MctsAction(
                action_id="repeat",
                action_type="verify_evidence",
                target_node_id="lab_cd4",
                target_node_label="LabFinding",
                target_node_name="CD4+ T淋巴细胞计数 < 200/μL",
                prior_score=1.0,
            ),
        },
    )
    fresh_child = TreeNode(
        node_id="root::fresh",
        state_signature="fresh",
        parent_id="root",
        action_from_parent="fresh",
        stage="A3",
        depth=1,
        visit_count=2,
        total_value=1.2,
        average_value=0.6,
        metadata={
            "prior_score": 0.6,
            "action": MctsAction(
                action_id="fresh",
                action_type="verify_evidence",
                target_node_id="symptom_lymph",
                target_node_label="Sign",
                target_node_name="淋巴结肿大",
                prior_score=0.6,
            ),
        },
    )
    tree.add_node(root)
    tree.add_node(repeated_child)
    tree.add_node(fresh_child)
    tree.add_edge("root", "root::repeat")
    tree.add_edge("root", "root::fresh")

    selected = engine.select_root_action(tree, excluded_target_node_ids=["lab_cd4"])

    assert selected is not None
    assert selected.action_id == "fresh"
