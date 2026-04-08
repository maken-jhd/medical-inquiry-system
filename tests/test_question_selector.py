"""测试下一问选择器的基础排序逻辑。"""

from brain.question_selector import QuestionSelector
from brain.types import QuestionCandidate, SessionState


# 验证选择器会优先选择尚未问过的候选问题。
def test_question_selector_prefers_unasked_candidate() -> None:
    selector = QuestionSelector()
    state = SessionState(session_id="s1", asked_node_ids=["q1"])
    q1 = QuestionCandidate(node_id="q1", label="Symptom", name="发热", priority=5.0)
    q2 = QuestionCandidate(node_id="q2", label="Symptom", name="咳嗽", priority=4.0)

    selected = selector.select_next_question([q1, q2], state)

    assert selected is not None
    assert selected.node_id == "q2"
