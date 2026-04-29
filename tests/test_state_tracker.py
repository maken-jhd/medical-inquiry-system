"""测试状态追踪器对槽位状态与轻量快照的维护能力。"""

from brain.state_tracker import StateTracker
from brain.search_tree import SearchTree
from brain.types import ReasoningTrajectory, SearchResult, SlotUpdate


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


# 验证 rollout 快照不会把 search_tree / last_search_result / trajectories 等重量级运行时对象一起拷贝。
def test_state_tracker_rollout_snapshot_excludes_runtime_heavy_fields() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s2")
    state.metadata["search_tree"] = SearchTree(root_id="root")
    state.metadata["last_search_result"] = SearchResult(best_answer_name="测试答案")
    state.metadata["custom_flag"] = "should_not_copy"
    state.trajectories.append(ReasoningTrajectory(trajectory_id="t1", score=0.6))

    snapshot = tracker.get_rollout_session_copy("s2")

    assert snapshot.session_id == "s2"
    assert snapshot.metadata == {}
    assert snapshot.trajectories == []
    assert "search_tree" not in snapshot.metadata
    assert "last_search_result" not in snapshot.metadata
