"""测试 belief state signature 的稳定性与区分度。"""

from brain.search import MctsConfig, MctsEngine
from brain.search.tree import SearchTree
from brain.state.signature import BeliefStateSignatureBuilder, StateSignatureConfig
from brain.state import MctsAction, SessionState, SlotState, TreeNode


def _build_state(session_id: str) -> SessionState:
    state = SessionState(
        session_id=session_id,
        active_topics=["Disease"],
        asked_node_ids=["node_history"],
    )
    state.slots["node_fever"] = SlotState(
        node_id="node_fever",
        status="true",
        polarity="present",
        resolution="clear",
    )
    state.slots["node_cough"] = SlotState(
        node_id="node_cough",
        status="false",
        polarity="absent",
        resolution="clear",
    )
    state.metadata["pending_action_id"] = "pending::node_history"
    return state


# 验证相同语义状态即使内部插入顺序不同，也会生成同一签名。
def test_state_signature_is_stable_for_same_semantic_state() -> None:
    builder = BeliefStateSignatureBuilder(
        StateSignatureConfig(
            include_exam_context=True,
            include_top_hypotheses=False,
        )
    )
    left = _build_state("s_left")
    right = SessionState(
        session_id="s_right",
        active_topics=["Disease"],
        asked_node_ids=["node_history"],
    )
    right.slots["node_cough"] = SlotState(
        node_id="node_cough",
        status="false",
        polarity="absent",
        resolution="clear",
    )
    right.slots["node_fever"] = SlotState(
        node_id="node_fever",
        status="true",
        polarity="present",
        resolution="clear",
    )
    right.metadata["pending_action_id"] = "pending::node_history"

    assert builder.build(left, hypothesis_id="d1") == builder.build(right, hypothesis_id="d1")


# 验证 evidence 状态变化会反映到 signature 中。
def test_state_signature_changes_when_evidence_changes() -> None:
    builder = BeliefStateSignatureBuilder()
    present_state = _build_state("s_present")
    absent_state = _build_state("s_absent")
    absent_state.slots["node_fever"] = SlotState(
        node_id="node_fever",
        status="false",
        polarity="absent",
        resolution="clear",
    )

    assert builder.build(present_state, hypothesis_id="d1") != builder.build(absent_state, hypothesis_id="d1")


# 验证 modular_v2 child node 的 state_signature 不再直接等于 action path id。
def test_mcts_engine_modular_child_uses_belief_state_signature() -> None:
    builder = BeliefStateSignatureBuilder()
    engine = MctsEngine(
        MctsConfig(search_impl="modular_v2"),
        state_signature_builder=builder,
    )
    tree = SearchTree()
    root = TreeNode(
        node_id="root",
        state_signature="root_sig",
        parent_id=None,
        action_from_parent=None,
        stage="A2",
        depth=0,
    )
    tree.add_node(root)
    state = _build_state("s_expand")
    action = MctsAction(
        action_id="verify::d1::node_lab",
        action_type="verify_evidence",
        target_node_id="node_lab",
        target_node_label="LabFinding",
        target_node_name="低氧血症",
        hypothesis_id="d1",
        topic_id="Disease",
        prior_score=1.2,
    )

    children = engine.expand_node(tree, "root", [action], session_state=state)

    assert len(children) == 1
    expected_signature = builder.build_post_action_signature(state, action)
    assert children[0].state_signature == expected_signature
    assert children[0].state_signature != children[0].node_id
