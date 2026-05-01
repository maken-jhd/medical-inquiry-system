"""将当前会话状态整理为可输出的结构化报告。"""

from __future__ import annotations

from dataclasses import asdict
from numbers import Number
from typing import Any, Dict, List

from .types import FinalAnswerScore, ReasoningTrajectory, SearchResult, SessionState, StopDecision


class ReportBuilder:
    """负责汇总会话中的已确认信息与候选假设。"""

    # 根据当前状态和终止决策构造最终结构化报告。
    def build_final_report(self, session_state: SessionState, stop_decision: StopDecision) -> Dict[str, Any]:
        confirmed_slots: List[Dict[str, Any]] = []

        # 最终报告只保留已经形成明确 true/false/uncertain 结论的槽位，
        # unknown 槽位不写进去，避免把“尚未验证”误读成有效证据。
        for slot in session_state.slots.values():
            if slot.status == "unknown":
                continue

            confirmed_slots.append(
                {
                    "node_id": slot.node_id,
                    "status": slot.status,
                    "polarity": slot.effective_polarity(),
                    "resolution": slot.resolution,
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

        # metadata 会先做一次轻量化裁剪，避免把搜索树、完整 SearchResult 等重对象直接带进最终输出。
        return {
            "session_id": session_state.session_id,
            "turn_index": session_state.turn_index,
            "stop_reason": stop_decision.reason,
            "stop_confidence": stop_decision.confidence,
            "confirmed_slots": confirmed_slots,
            "mention_context": self._build_public_mention_context(session_state),
            "candidate_hypotheses": hypotheses,
            "active_topics": list(session_state.active_topics),
            "trajectory_count": len(session_state.trajectories),
            "metadata": self._build_public_metadata(session_state.metadata),
        }

    # 构造搜索阶段的中间报告，便于调试 rollout 与动作选择。
    def build_search_report(
        self,
        session_state: SessionState,
        search_result: SearchResult,
    ) -> Dict[str, Any]:
        return {
            "session_id": session_state.session_id,
            "turn_index": session_state.turn_index,
            "selected_action": asdict(search_result.selected_action) if search_result.selected_action is not None else None,
            "root_best_action": asdict(search_result.root_best_action) if search_result.root_best_action is not None else None,
            "repair_selected_action": (
                asdict(search_result.repair_selected_action)
                if search_result.repair_selected_action is not None
                else None
            ),
            "repair_context": dict(search_result.verifier_repair_context),
            "verifier_repair_context": dict(search_result.verifier_repair_context),
            "best_answer_id": search_result.best_answer_id,
            "best_answer_name": search_result.best_answer_name,
            "trajectory_count": len(search_result.trajectories),
            "final_answer_scores": [asdict(item) for item in search_result.final_answer_scores],
        }

    # 构造最终诊断推理报告，包含最佳轨迹和备选答案评分。
    def build_final_reasoning_report(
        self,
        session_state: SessionState,
        stop_decision: StopDecision,
        search_result: SearchResult | None = None,
    ) -> Dict[str, Any]:
        final_report = self.build_final_report(session_state, stop_decision)

        if search_result is None:
            return final_report

        # reasoning report 会在基础 final_report 上补：
        # 最佳最终答案、最佳轨迹、为什么它赢、以及与 alternatives 的差距。
        best_trajectory = self._select_best_trajectory(search_result.trajectories, search_result.best_answer_id)
        evidence_for_best_answer = self._summarize_trajectory_evidence(best_trajectory)
        why_this_answer_wins = self._build_why_this_answer_wins(search_result)
        final_report.update(
            {
                "best_final_answer": {
                    "answer_id": search_result.best_answer_id,
                    "answer_name": search_result.best_answer_name,
                },
                "selected_action": asdict(search_result.selected_action) if search_result.selected_action is not None else None,
                "root_best_action": asdict(search_result.root_best_action) if search_result.root_best_action is not None else None,
                "repair_selected_action": (
                    asdict(search_result.repair_selected_action)
                    if search_result.repair_selected_action is not None
                    else None
                ),
                "repair_context": dict(search_result.verifier_repair_context),
                "verifier_repair_context": dict(search_result.verifier_repair_context),
                "answer_group_scores": [asdict(item) for item in search_result.final_answer_scores],
                "best_trajectory": asdict(best_trajectory) if best_trajectory is not None else None,
                "trajectory_summary": self._summarize_trajectory(best_trajectory),
                "why_this_answer_wins": why_this_answer_wins,
                "evidence_for_best_answer": evidence_for_best_answer,
                "evidence_against_top_alternatives": self._summarize_alternative_gaps(search_result),
                "alternative_trajectories": [
                    asdict(item) for item in search_result.trajectories if best_trajectory is None or item.trajectory_id != best_trajectory.trajectory_id
                ][:3],
            }
        )
        return final_report

    # 选出与最终答案一致且分数最高的最佳轨迹。
    def _select_best_trajectory(
        self,
        trajectories: List[ReasoningTrajectory],
        answer_id: str | None,
    ) -> ReasoningTrajectory | None:
        matched = [item for item in trajectories if answer_id is None or item.final_answer_id == answer_id]

        if len(matched) == 0:
            return None

        return sorted(matched, key=lambda item: (-item.score, item.trajectory_id))[0]

    # 用简短自然语言总结最佳轨迹的关键路径。
    def _summarize_trajectory(self, trajectory: ReasoningTrajectory | None) -> str:
        if trajectory is None:
            return ""

        step_names = [
            str(step.get("action_name", step.get("target_node_name", ""))).strip()
            for step in trajectory.steps
            if len(str(step.get("action_name", step.get("target_node_name", ""))).strip()) > 0
        ]

        if len(step_names) == 0:
            return "当前没有形成可解释的关键验证路径。"

        return f"最佳路径围绕 {', '.join(step_names[:4])} 展开，并最终收敛到当前最佳答案。"

    # 汇总最佳轨迹中的支持证据名称。
    def _summarize_trajectory_evidence(self, trajectory: ReasoningTrajectory | None) -> list[str]:
        if trajectory is None:
            return []

        evidence_names: list[str] = []

        for step in trajectory.steps:
            name = str(step.get("action_name", step.get("target_node_name", ""))).strip()

            if len(name) == 0 or name in evidence_names:
                continue

            evidence_names.append(name)

        return evidence_names[:6]

    # 解释为什么当前答案在评分上胜过其他答案。
    def _build_why_this_answer_wins(self, search_result: SearchResult) -> str:
        if len(search_result.final_answer_scores) == 0:
            return ""

        ranked = sorted(search_result.final_answer_scores, key=lambda item: (-item.final_score, item.answer_name))
        best = ranked[0]

        if len(ranked) == 1:
            return (
                f"当前只有答案“{best.answer_name}”形成了稳定路径，"
                f"其 consistency={best.consistency:.2f}、diversity={best.diversity:.2f}、agent_evaluation={best.agent_evaluation:.2f}。"
            )

        runner_up = ranked[1]
        return (
            f"答案“{best.answer_name}”的综合得分高于“{runner_up.answer_name}”，"
            f"主要因为它在 consistency、diversity 与 agent_evaluation 的组合上更优。"
        )

    # 提炼当前最佳答案相对于次优答案的关键差异。
    def _summarize_alternative_gaps(self, search_result: SearchResult) -> list[dict]:
        ranked = sorted(search_result.final_answer_scores, key=lambda item: (-item.final_score, item.answer_name))

        if len(ranked) <= 1:
            return []

        best = ranked[0]
        alternatives: list[dict] = []

        for item in ranked[1:3]:
            alternatives.append(
                {
                    "answer_id": item.answer_id,
                    "answer_name": item.answer_name,
                    "score_gap": round(best.final_score - item.final_score, 4),
                    "consistency_gap": round(best.consistency - item.consistency, 4),
                    "agent_evaluation_gap": round(best.agent_evaluation - item.agent_evaluation, 4),
                }
            )

        return alternatives

    # 过滤最终报告中的重量级运行态对象，避免 replay 批量落盘时占用过多内存。
    def _build_public_metadata(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        public_metadata: Dict[str, Any] = {}

        for key, value in metadata.items():
            if key == "search_tree":
                summary = self._summarize_search_tree_metadata(value)
                if summary is not None:
                    public_metadata["search_tree_summary"] = summary
                continue

            if key == "last_search_result":
                summary = self._summarize_search_result_metadata(value)
                if summary is not None:
                    public_metadata["last_search_result_summary"] = summary
                continue

            sanitized = self._sanitize_lightweight_metadata_value(value)
            if sanitized is not None:
                public_metadata[key] = sanitized

        return public_metadata

    def _build_public_mention_context(self, session_state: SessionState) -> list[dict]:
        values: list[dict] = []

        for item in session_state.mention_context.values():
            values.append(
                {
                    "normalized_name": item.normalized_name,
                    "display_name": item.display_name or item.normalized_name,
                    "node_id": item.node_id,
                    "polarity": item.polarity,
                    "evidence": list(item.evidence),
                    "source_turns": list(item.source_turns),
                }
            )

        return sorted(values, key=lambda item: (str(item.get("normalized_name") or ""), str(item.get("display_name") or "")))

    # 将轻量 metadata 递归裁剪为 JSON 友好结构，避免把运行时对象原样带入最终结果。
    def _sanitize_lightweight_metadata_value(
        self,
        value: Any,
        *,
        depth: int = 0,
    ) -> Any | None:
        # 标量值直接保留；复杂对象则递归向下裁剪。
        if value is None or isinstance(value, (str, bool)):
            return value

        if isinstance(value, Number):
            return value

        # 深度限制用于阻止 metadata 里再嵌 metadata，最终导致大对象层层展开。
        if depth >= 2:
            return None

        if isinstance(value, list):
            # 列表和元组都只截取前若干个元素，满足“能解释”即可，不追求完整镜像运行态。
            sanitized_items = [
                sanitized
                for item in value[:12]
                if (sanitized := self._sanitize_lightweight_metadata_value(item, depth=depth + 1)) is not None
            ]
            return sanitized_items

        if isinstance(value, tuple):
            sanitized_items = [
                sanitized
                for item in value[:12]
                if (sanitized := self._sanitize_lightweight_metadata_value(item, depth=depth + 1)) is not None
            ]
            return sanitized_items

        if isinstance(value, dict):
            sanitized_dict: Dict[str, Any] = {}

            # dict 也会限制键数量，并仅保留字符串键，确保最终 JSON 足够轻量且稳定。
            for index, (item_key, item_value) in enumerate(value.items()):
                if index >= 20:
                    break

                if not isinstance(item_key, str):
                    continue

                sanitized_value = self._sanitize_lightweight_metadata_value(item_value, depth=depth + 1)
                if sanitized_value is not None:
                    sanitized_dict[item_key] = sanitized_value

            return sanitized_dict

        return None

    # 为搜索树生成轻量摘要，避免在最终报告中保留整棵树对象。
    def _summarize_search_tree_metadata(self, tree_value: Any) -> Dict[str, Any] | None:
        root_id = getattr(tree_value, "root_id", None)
        nodes = getattr(tree_value, "nodes", None)

        if isinstance(tree_value, dict):
            root_id = tree_value.get("root_id")
            nodes = tree_value.get("nodes")

        node_count = len(nodes) if isinstance(nodes, dict) else 0
        if root_id is None and node_count == 0:
            return None

        return {
            "root_id": root_id,
            "node_count": node_count,
        }

    # 为最近一次搜索结果生成轻量摘要，避免把完整轨迹和评分对象直接挂进最终报告。
    def _summarize_search_result_metadata(self, result_value: Any) -> Dict[str, Any] | None:
        best_answer_id = getattr(result_value, "best_answer_id", None)
        best_answer_name = getattr(result_value, "best_answer_name", None)
        trajectories = getattr(result_value, "trajectories", None)
        final_answer_scores = getattr(result_value, "final_answer_scores", None)

        if isinstance(result_value, dict):
            best_answer_id = result_value.get("best_answer_id")
            best_answer_name = result_value.get("best_answer_name")
            trajectories = result_value.get("trajectories")
            final_answer_scores = result_value.get("final_answer_scores")

        trajectory_count = len(trajectories) if isinstance(trajectories, list) else 0
        answer_group_score_count = len(final_answer_scores) if isinstance(final_answer_scores, list) else 0

        if best_answer_id is None and best_answer_name is None and trajectory_count == 0 and answer_group_score_count == 0:
            return None

        return {
            "best_answer_id": best_answer_id,
            "best_answer_name": best_answer_name,
            "trajectory_count": trajectory_count,
            "answer_group_score_count": answer_group_score_count,
        }
