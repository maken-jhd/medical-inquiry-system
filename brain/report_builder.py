"""将当前会话状态整理为可输出的结构化报告。"""

from __future__ import annotations

from typing import Any, Dict, List

from .types import SessionState, StopDecision


class ReportBuilder:
    """负责汇总会话中的已确认信息与候选假设。"""

    # 根据当前状态和终止决策构造最终结构化报告。
    def build_final_report(self, session_state: SessionState, stop_decision: StopDecision) -> Dict[str, Any]:
        confirmed_slots: List[Dict[str, Any]] = []

        for slot in session_state.slots.values():
            if slot.status == "unknown":
                continue

            confirmed_slots.append(
                {
                    "node_id": slot.node_id,
                    "status": slot.status,
                    "certainty": slot.certainty,
                    "value": slot.value,
                    "evidence": slot.evidence,
                }
            )

        hypotheses = [
            {
                "node_id": hypothesis.node_id,
                "label": hypothesis.label,
                "name": hypothesis.name,
                "score": hypothesis.score,
            }
            for hypothesis in session_state.candidate_hypotheses
        ]

        return {
            "session_id": session_state.session_id,
            "turn_index": session_state.turn_index,
            "stop_reason": stop_decision.reason,
            "stop_confidence": stop_decision.confidence,
            "confirmed_slots": confirmed_slots,
            "candidate_hypotheses": hypotheses,
            "active_topics": list(session_state.active_topics),
            "metadata": dict(session_state.metadata),
        }
