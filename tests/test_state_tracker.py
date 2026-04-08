"""测试状态追踪器对槽位状态的维护能力。"""

from brain.state_tracker import StateTracker
from brain.types import SlotUpdate


# 验证状态追踪器能够正确写入并读取槽位状态。
def test_state_tracker_sets_and_reads_slot() -> None:
    tracker = StateTracker()
    tracker.create_session("s1")
    tracker.set_slot(
        "s1",
        SlotUpdate(
            node_id="symptom_fever",
            status="true",
            certainty="uncertain",
            evidence="好像有点发热",
            turn_index=1,
        ),
    )

    slot = tracker.get_slot("s1", "symptom_fever")

    assert slot is not None
    assert slot.status == "true"
    assert slot.certainty == "uncertain"
    assert slot.evidence == ["好像有点发热"]
    assert slot.source_turns == [1]
