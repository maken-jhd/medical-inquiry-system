"""负责构造更稳定的 belief state signature。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Sequence

from .runtime import MctsAction, SessionState


@dataclass
class StateSignatureConfig:
    """控制状态签名包含哪些可稳定复用的 belief 特征。"""

    include_asked_node_ids: bool = True
    include_active_topics: bool = True
    include_exam_context: bool = True
    include_top_hypotheses: bool = True
    include_pending_context: bool = True
    max_top_hypotheses: int = 3


class BeliefStateSignatureBuilder:
    """把会话状态压缩成稳定的哈希签名，供搜索树复用。"""

    def __init__(self, config: StateSignatureConfig | None = None) -> None:
        self.config = config or StateSignatureConfig()

    # 为当前 belief state 构造签名；可按需覆盖 asked/topic/pending 上下文。
    def build(
        self,
        session_state: SessionState,
        *,
        hypothesis_id: str | None = None,
        focus_target_id: str | None = None,
        pending_action_id: str | None = None,
        asked_node_ids: Sequence[str] | None = None,
        active_topics: Sequence[str] | None = None,
    ) -> str:
        payload = self.build_payload(
            session_state,
            hypothesis_id=hypothesis_id,
            focus_target_id=focus_target_id,
            pending_action_id=pending_action_id,
            asked_node_ids=asked_node_ids,
            active_topics=active_topics,
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    # 为“当前动作刚被选中、但还没观察到回答”的近似 post-action belief state 构造签名。
    def build_post_action_signature(
        self,
        session_state: SessionState,
        action: MctsAction,
    ) -> str:
        asked_ids = list(session_state.asked_node_ids)
        if action.target_node_id not in asked_ids:
            asked_ids.append(action.target_node_id)

        active_topics = list(session_state.active_topics)
        if action.topic_id is not None and action.topic_id not in active_topics:
            active_topics.append(action.topic_id)

        return self.build(
            session_state,
            hypothesis_id=action.hypothesis_id,
            focus_target_id=action.target_node_id,
            pending_action_id=action.action_id,
            asked_node_ids=asked_ids,
            active_topics=active_topics,
        )

    # 暴露可读 payload，便于测试和调试定位 signature 漂移来源。
    def build_payload(
        self,
        session_state: SessionState,
        *,
        hypothesis_id: str | None = None,
        focus_target_id: str | None = None,
        pending_action_id: str | None = None,
        asked_node_ids: Sequence[str] | None = None,
        active_topics: Sequence[str] | None = None,
    ) -> str:
        positive_slots = self._collect_slot_entries(session_state, "present")
        negative_slots = self._collect_slot_entries(session_state, "absent")
        unclear_slots = self._collect_slot_entries(session_state, "unclear")
        payload_parts = [
            f"H={str(hypothesis_id or 'NONE').strip() or 'NONE'}",
            f"P={';'.join(positive_slots)}",
            f"N={';'.join(negative_slots)}",
            f"U={';'.join(unclear_slots)}",
        ]

        if self.config.include_asked_node_ids:
            asked_source = asked_node_ids if asked_node_ids is not None else session_state.asked_node_ids
            asked_ids = sorted(str(item).strip() for item in asked_source if len(str(item).strip()) > 0)
            payload_parts.append(f"A={';'.join(asked_ids)}")

        if self.config.include_active_topics:
            topic_source = active_topics if active_topics is not None else session_state.active_topics
            topics = sorted(str(item).strip() for item in topic_source if len(str(item).strip()) > 0)
            payload_parts.append(f"T={';'.join(topics)}")

        if self.config.include_exam_context:
            exam_items = sorted(
                f"{exam_kind}:{context.availability}"
                for exam_kind, context in session_state.exam_context.items()
            )
            payload_parts.append(f"E={';'.join(exam_items)}")

        if self.config.include_top_hypotheses:
            payload_parts.append(f"C={';'.join(self._collect_top_hypothesis_entries(session_state))}")

        if self.config.include_pending_context:
            pending_id = str(pending_action_id or session_state.metadata.get('pending_action_id') or "").strip()
            payload_parts.append(f"Q={pending_id}")
            payload_parts.append(f"F={str(focus_target_id or '').strip()}")

        return "|".join(payload_parts)

    # 收集指定 polarity 下的 slot 概览，避免把整个对象塞进 signature。
    def _collect_slot_entries(self, session_state: SessionState, polarity: str) -> list[str]:
        return sorted(
            f"{slot.node_id}:{slot.effective_polarity()}:{slot.resolution}"
            for slot in session_state.slots.values()
            if slot.effective_polarity() == polarity
        )

    # 只收录 top hypotheses 的简化摘要，避免分数微抖动导致树缓存完全失效。
    def _collect_top_hypothesis_entries(self, session_state: SessionState) -> list[str]:
        if not self.config.include_top_hypotheses:
            return []

        ranked = sorted(
            session_state.candidate_hypotheses,
            key=lambda item: (-float(item.score), item.name),
        )[: max(int(self.config.max_top_hypotheses), 0)]
        return [
            f"{item.node_id}:{round(float(item.score), 2)}"
            for item in ranked
        ]
