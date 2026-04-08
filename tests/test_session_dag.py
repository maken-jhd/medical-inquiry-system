"""测试会话 DAG 的深度优先追问行为。"""

from brain.session_dag import SessionDag


# 验证会话 DAG 会按 DFS 顺序返回当前分支的下一个开放节点。
def test_session_dag_depth_first_next_open_node() -> None:
    dag = SessionDag()
    dag.activate_topic("topic_risk", "root_risk")
    dag.add_child("root_risk", "risk_time")
    dag.add_child("risk_time", "risk_protection")
    dag.mark_answered("root_risk")
    dag.mark_answered("risk_time")

    next_node = dag.next_open_node_in_branch("topic_risk")

    assert next_node is not None
    assert next_node.node_id == "risk_protection"
