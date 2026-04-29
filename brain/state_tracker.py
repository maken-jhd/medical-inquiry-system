"""统一维护问诊会话中的可变状态。"""

from __future__ import annotations

from copy import deepcopy
from typing import Dict, Iterable, Optional

from .search_tree import SearchTree
from .types import (
    ActionStats,
    ExamAvailability,
    ExamContextState,
    ExamMentionedResult,
    EvidenceState,
    HypothesisScore,
    MctsAction,
    ReasoningTrajectory,
    SessionState,
    SlotState,
    SlotUpdate,
    StateVisitStats,
)


class StateTracker:
    """管理问诊会话、槽位状态和候选假设。"""

    # 初始化会话状态容器。
    def __init__(self) -> None:
        self._sessions: Dict[str, SessionState] = {}

    # 创建一个新的会话状态对象。
    def create_session(self, session_id: str) -> SessionState:
        state = SessionState(session_id=session_id)
        self._sessions[session_id] = state
        return state

    # 读取指定会话的当前状态；不存在时抛出异常。
    def get_session(self, session_id: str) -> SessionState:
        if session_id not in self._sessions:
            raise KeyError(f"Unknown session_id: {session_id}")

        return self._sessions[session_id]

    # 返回一份当前会话状态的深拷贝，避免外部直接修改原对象。
    def get_session_copy(self, session_id: str) -> SessionState:
        return deepcopy(self.get_session(session_id))

    # 返回 rollout / reroot 专用的轻量状态快照，避免把 search_tree 等运行时大对象一并 deepcopy。
    def get_rollout_session_copy(self, session_id: str) -> SessionState:
        return self.build_rollout_session_snapshot(self.get_session(session_id))

    # 克隆当前会话状态，供 rollout 或分支推演时使用。
    def clone_session_state(self, session_id: str) -> SessionState:
        return self.get_session_copy(session_id)

    # 只保留推演所需字段，切断 search_tree / last_search_result / trajectories 等重量级引用。
    @staticmethod
    def build_rollout_session_snapshot(state: SessionState) -> SessionState:
        return SessionState(
            session_id=state.session_id,
            turn_index=state.turn_index,
            active_topics=list(state.active_topics),
            slots=deepcopy(state.slots),
            evidence_states=deepcopy(state.evidence_states),
            exam_context=deepcopy(state.exam_context),
            candidate_hypotheses=deepcopy(state.candidate_hypotheses),
            asked_node_ids=list(state.asked_node_ids),
            action_stats={},
            state_visit_stats={},
            trajectories=[],
            fail_count=state.fail_count,
            metadata={},
        )

    # 确保指定槽位存在；若不存在则自动创建默认槽位。
    def ensure_slot(self, session_id: str, node_id: str) -> SlotState:
        state = self.get_session(session_id)

        if node_id not in state.slots:
            state.slots[node_id] = SlotState(node_id=node_id)

        return state.slots[node_id]

    # 获取指定槽位当前状态；若不存在则返回空值。
    def get_slot(self, session_id: str, node_id: str) -> Optional[SlotState]:
        state = self.get_session(session_id)
        return state.slots.get(node_id)

    # 用新的槽位更新对象覆盖当前槽位状态。
    def set_slot(self, session_id: str, update: SlotUpdate) -> SlotState:
        slot = self.ensure_slot(session_id, update.node_id)

        slot.status = update.status
        slot.certainty = update.certainty
        slot.value = update.value
        slot.metadata.update(update.metadata)

        if update.evidence is not None:
            self.append_evidence(session_id, update.node_id, update.evidence, update.turn_index)

        return slot

    # 为指定槽位追加证据文本和对应轮次。
    def append_evidence(
        self,
        session_id: str,
        node_id: str,
        evidence: str,
        turn_index: Optional[int] = None,
    ) -> None:
        slot = self.ensure_slot(session_id, node_id)

        if evidence not in slot.evidence:
            slot.evidence.append(evidence)

        if turn_index is not None and turn_index not in slot.source_turns:
            slot.source_turns.append(turn_index)

    # 批量应用多个槽位更新。
    def apply_slot_updates(self, session_id: str, updates: Iterable[SlotUpdate]) -> SessionState:
        for update in updates:
            self.set_slot(session_id, update)

        return self.get_session(session_id)

    # 确保指定检查类型的上下文状态存在。
    def ensure_exam_context(self, session_id: str, exam_kind: str) -> ExamContextState:
        state = self.get_session(session_id)

        if exam_kind not in {"general", "lab", "imaging", "pathogen"}:
            exam_kind = "general"

        if exam_kind not in state.exam_context:
            state.exam_context[exam_kind] = ExamContextState(exam_kind=exam_kind)  # type: ignore[arg-type]

        return state.exam_context[exam_kind]

    # 写入患者对某类检查是否做过、记得哪些检查和结果的回答。
    def update_exam_context(
        self,
        session_id: str,
        exam_kind: str,
        *,
        availability: ExamAvailability | None = None,
        mentioned_exam_names: Iterable[str] | None = None,
        mentioned_exam_results: Iterable[ExamMentionedResult] | None = None,
        turn_index: Optional[int] = None,
        metadata: Optional[Dict[str, object]] = None,
    ) -> ExamContextState:
        context = self.ensure_exam_context(session_id, exam_kind)

        # availability 是该类检查当前最核心的状态位，存在新结论时直接覆盖。
        if availability is not None:
            context.availability = availability

        # 检查名称按去重追加，保留患者跨轮次陆续补充的“做过哪些检查”信息。
        for name in mentioned_exam_names or []:
            cleaned_name = str(name).strip()

            if len(cleaned_name) == 0 or cleaned_name in context.mentioned_exam_names:
                continue

            context.mentioned_exam_names.append(cleaned_name)

        # 结果去重键同时看 test_name + raw_text，
        # 避免同一轮或多轮回答把同一句结果反复累计进去。
        existing_result_keys = {
            (item.test_name, item.raw_text)
            for item in context.mentioned_exam_results
        }

        for result in mentioned_exam_results or []:
            key = (result.test_name, result.raw_text)

            if key in existing_result_keys:
                continue

            context.mentioned_exam_results.append(result)
            existing_result_keys.add(key)

        # source_turns 记录这类检查信息是在哪些轮次被患者提到的，便于前端和 replay 追溯。
        if turn_index is not None and turn_index not in context.source_turns:
            context.source_turns.append(turn_index)

        # metadata 只做增量更新，保留上一次 followup / reasoning 等辅助解释信息。
        if metadata is not None:
            context.metadata.update(metadata)

        return context

    # 更新当前会话的候选假设列表。
    def set_candidate_hypotheses(
        self,
        session_id: str,
        hypotheses: Iterable[HypothesisScore],
    ) -> SessionState:
        state = self.get_session(session_id)
        state.candidate_hypotheses = list(hypotheses)
        return state

    # 保存一条推理轨迹，供最终答案聚合与解释性报告使用。
    def save_trajectory(self, session_id: str, trajectory: ReasoningTrajectory) -> None:
        state = self.get_session(session_id)
        state.trajectories.append(trajectory)

    # 返回当前会话已保存的所有推理轨迹。
    def list_trajectories(self, session_id: str) -> list[ReasoningTrajectory]:
        state = self.get_session(session_id)
        return list(state.trajectories)

    # 记录某个问题节点已经被问过，避免重复追问。
    def mark_question_asked(self, session_id: str, node_id: str) -> None:
        state = self.get_session(session_id)

        if node_id not in state.asked_node_ids:
            state.asked_node_ids.append(node_id)

    # 激活一个当前正在追问的主题。
    def activate_topic(self, session_id: str, topic_id: str) -> None:
        state = self.get_session(session_id)

        if topic_id not in state.active_topics:
            state.active_topics.append(topic_id)

    # 关闭一个已完成或已跳出的主题。
    def close_topic(self, session_id: str, topic_id: str) -> None:
        state = self.get_session(session_id)
        state.active_topics = [item for item in state.active_topics if item != topic_id]

    # 将当前会话轮次加一，并返回新的轮次编号。
    def increment_turn(self, session_id: str) -> int:
        state = self.get_session(session_id)
        state.turn_index += 1
        return state.turn_index

    # 增加一次失败计数，用于 fallback 判定。
    def increment_fail_count(self, session_id: str) -> int:
        state = self.get_session(session_id)
        state.fail_count += 1
        return state.fail_count

    # 将失败计数清零。
    def reset_fail_count(self, session_id: str) -> None:
        state = self.get_session(session_id)
        state.fail_count = 0

    # 写入或覆盖某条证据节点的演绎状态。
    def set_evidence_state(self, session_id: str, evidence_state: EvidenceState) -> EvidenceState:
        state = self.get_session(session_id)
        state.evidence_states[evidence_state.node_id] = evidence_state
        return evidence_state

    # 读取当前待验证动作；若不存在则返回空值。
    def get_pending_action(self, session_id: str) -> Optional[MctsAction]:
        state = self.get_session(session_id)
        pending_action = state.metadata.get("pending_action")

        if pending_action is None:
            return None

        if isinstance(pending_action, MctsAction):
            return pending_action

        return MctsAction(**pending_action)

    # 将当前待验证动作写入会话元数据。
    def set_pending_action(self, session_id: str, action: MctsAction) -> None:
        state = self.get_session(session_id)
        state.metadata["pending_action"] = action
        state.metadata["pending_action_id"] = action.action_id

    # 清空当前会话中的待验证动作。
    def clear_pending_action(self, session_id: str) -> None:
        state = self.get_session(session_id)
        state.metadata.pop("pending_action", None)
        state.metadata.pop("pending_action_id", None)

    # 将搜索树绑定到当前会话，便于多轮搜索复用同一棵树。
    def bind_search_tree(self, session_id: str, tree: SearchTree) -> None:
        state = self.get_session(session_id)
        state.metadata["search_tree"] = tree

    # 读取当前会话绑定的搜索树；若尚未绑定则返回空值。
    def get_bound_search_tree(self, session_id: str) -> Optional[SearchTree]:
        state = self.get_session(session_id)
        tree = state.metadata.get("search_tree")

        if tree is None:
            return None

        if isinstance(tree, SearchTree):
            return tree

        return None

    # 为指定动作累计一次 reward，用于后续 UCT 计算。
    def record_action_feedback(
        self,
        session_id: str,
        action_id: str,
        reward: float,
        metadata: Optional[Dict[str, object]] = None,
    ) -> ActionStats:
        state = self.get_session(session_id)
        stats = state.action_stats.get(action_id)

        if stats is None:
            stats = ActionStats(action_id=action_id)
            state.action_stats[action_id] = stats

        stats.visit_count += 1
        stats.total_value += reward
        stats.average_value = stats.total_value / stats.visit_count

        if metadata is not None:
            stats.metadata.update(metadata)

        return stats

    # 为状态签名累计访问次数，用于 UCT 的父节点访问统计。
    def increment_state_visit(
        self,
        session_id: str,
        state_signature: str,
        metadata: Optional[Dict[str, object]] = None,
    ) -> StateVisitStats:
        state = self.get_session(session_id)
        stats = state.state_visit_stats.get(state_signature)

        if stats is None:
            stats = StateVisitStats(state_signature=state_signature)
            state.state_visit_stats[state_signature] = stats

        stats.visit_count += 1

        if metadata is not None:
            stats.metadata.update(metadata)

        return stats
