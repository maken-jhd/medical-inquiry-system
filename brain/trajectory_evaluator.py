"""实现轨迹聚合、多维评分与最终答案选择。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

from .llm_client import LlmClient
from .types import FinalAnswerScore, PatientContext, ReasoningTrajectory


ALLOWED_REJECT_REASONS = {
    "missing_key_support",
    "strong_alternative_not_ruled_out",
    "trajectory_insufficient",
}


ALLOWED_ACCEPT_REASONS = {
    "key_support_sufficient",
    "alternatives_reasonably_ruled_out",
    "trajectory_stable",
}


@dataclass
class TrajectoryEvaluatorConfig:
    """保存轨迹聚合评分阶段的权重配置。"""

    consistency_weight: float = 0.3
    diversity_weight: float = 0.4
    agent_eval_weight: float = 0.3
    agent_eval_mode: str = "fallback"
    llm_verifier_min_turn_index: int = 0
    llm_verifier_min_trajectory_count: int = 1


class TrajectoryEvaluator:
    """按照最终答案对轨迹聚类并输出聚合评分。"""

    # 初始化轨迹评估器配置。
    def __init__(
        self,
        config: TrajectoryEvaluatorConfig | None = None,
        llm_client: LlmClient | None = None,
    ) -> None:
        self.config = config or TrajectoryEvaluatorConfig()
        self.llm_client = llm_client

    # 按最终答案对轨迹进行分组。
    def group_by_answer(self, trajectories: Iterable[ReasoningTrajectory]) -> Dict[Tuple[str, str], List[ReasoningTrajectory]]:
        grouped: Dict[Tuple[str, str], List[ReasoningTrajectory]] = defaultdict(list)

        for trajectory in trajectories:
            key = (
                trajectory.final_answer_id or "UNKNOWN",
                trajectory.final_answer_name or "UNKNOWN",
            )
            grouped[key].append(trajectory)

        return dict(grouped)

    # 对每个答案分组计算一致性、多样性和代理评分。
    def score_groups(
        self,
        grouped: Dict[Tuple[str, str], List[ReasoningTrajectory]],
        patient_context: PatientContext | None = None,
        session_turn_index: int | None = None,
    ) -> List[FinalAnswerScore]:
        total_trajectories = sum(len(items) for items in grouped.values())

        # answer_candidates 会传给 verifier，帮助它知道当前不是“单答案判断”，
        # 而是在比较一组互相竞争的最终答案。
        answer_candidates = [
            {
                "answer_id": answer_id,
                "answer_name": answer_name,
                "trajectory_count": len(trajectories),
            }
            for (answer_id, answer_name), trajectories in grouped.items()
        ]
        scores: List[FinalAnswerScore] = []

        for (answer_id, answer_name), trajectories in grouped.items():
            # consistency 看这个答案占了多少 rollout，
            # diversity 看同一答案内部路径是否过于单一，
            # agent_evaluation 再补一个更偏“临床可信度”的视角。
            consistency = len(trajectories) / total_trajectories if total_trajectories > 0 else 0.0
            diversity = self._compute_diversity(trajectories)
            agent_evaluation, agent_metadata = self._compute_agent_evaluation(
                trajectories,
                answer_id=answer_id,
                answer_name=answer_name,
                patient_context=patient_context,
                answer_candidates=answer_candidates,
                session_turn_index=session_turn_index,
            )
            final_score = (
                consistency * self.config.consistency_weight
                + diversity * self.config.diversity_weight
                + agent_evaluation * self.config.agent_eval_weight
            )
            scores.append(
                FinalAnswerScore(
                    answer_id=answer_id,
                    answer_name=answer_name,
                    consistency=consistency,
                    diversity=diversity,
                    agent_evaluation=agent_evaluation,
                    final_score=final_score,
                    metadata={"trajectory_count": len(trajectories), **agent_metadata},
                )
            )

        # 最终还是统一按 final_score 排序，把“答案组聚合后的 top1”交给 stop/verifier/repair。
        return sorted(scores, key=lambda item: (-item.final_score, item.answer_name))

    # 从已评分的答案分组中选出最终答案。
    def select_best_answer(self, scores: Iterable[FinalAnswerScore]) -> FinalAnswerScore | None:
        ranked = sorted(scores, key=lambda item: (-item.final_score, item.answer_name))

        if len(ranked) == 0:
            return None

        return ranked[0]

    # 估计同一答案下轨迹的多样性。
    def _compute_diversity(self, trajectories: List[ReasoningTrajectory]) -> float:
        if len(trajectories) <= 1:
            return 0.0

        pairwise_scores: list[float] = []

        for index, left in enumerate(trajectories):
            for right in trajectories[index + 1 :]:
                pairwise_scores.append(1.0 - self._trajectory_similarity(left, right))

        if len(pairwise_scores) == 0:
            return 0.0

        return max(min(sum(pairwise_scores) / len(pairwise_scores), 1.0), 0.0)

    # 估计代理级整体评分，当前先使用轨迹平均得分。
    def _compute_agent_evaluation(
        self,
        trajectories: List[ReasoningTrajectory],
        answer_id: str,
        answer_name: str,
        patient_context: PatientContext | None = None,
        answer_candidates: list[dict] | None = None,
        session_turn_index: int | None = None,
    ) -> tuple[float, dict]:
        if len(trajectories) == 0:
            return 0.0, {"verifier_mode": "empty", "verifier_called": False}

        # llm_verifier 模式下，先看当前是否满足“值得调用 verifier”的时间窗口；
        # 不满足就先退回启发式，避免每轮都调用高成本评审。
        if self.config.agent_eval_mode == "llm_verifier":
            deferred_reason = self._llm_verifier_deferred_reason(
                trajectory_count=len(trajectories),
                session_turn_index=session_turn_index,
            )
            if deferred_reason is not None:
                fallback_score, fallback_metadata = self._compute_fallback_agent_evaluation(trajectories)
                return fallback_score, {
                    **fallback_metadata,
                    "verifier_mode": "llm_verifier_deferred",
                    "verifier_called": False,
                    "verifier_deferred_reason": deferred_reason,
                    "verifier_deferred_turn_index": session_turn_index,
                    "verifier_deferred_trajectory_count": len(trajectories),
                }

            llm_result = self._compute_llm_agent_evaluation(
                trajectories,
                answer_id=answer_id,
                answer_name=answer_name,
                patient_context=patient_context,
                answer_candidates=answer_candidates,
            )

            if llm_result is not None:
                # verifier 返回的 should_accept / reject_reason / accept_reason
                # 会直接成为 stop_rules 和 repair 的控制信号。
                return llm_result["score"], {
                    "verifier_mode": "llm_verifier",
                    "verifier_called": True,
                    "verifier_should_accept": llm_result["should_accept_stop"],
                    "verifier_reject_reason": llm_result["reject_reason"],
                    "verifier_reasoning": llm_result["reasoning"],
                    "verifier_missing_evidence": llm_result["missing_evidence"],
                    "verifier_risk_flags": llm_result["risk_flags"],
                    "verifier_recommended_next_evidence": llm_result["recommended_next_evidence"],
                    "verifier_alternative_candidates": llm_result["alternative_candidates"],
                    "verifier_reject_reason_source": llm_result["reject_reason_source"],
                    "verifier_schema_valid": llm_result["schema_valid"],
                    "verifier_accept_reason": llm_result["accept_reason"],
                    "verifier_accept_reason_source": llm_result["accept_reason_source"],
                    "verifier_accept_schema_valid": llm_result["accept_schema_valid"],
                }

        if self.config.agent_eval_mode != "fallback":
            # 其余模式暂时退回简单的轨迹均值，不引入额外验证语义。
            total_score = sum(item.score for item in trajectories)
            normalized = total_score / len(trajectories)
            return max(min(normalized, 1.0), 0.0), {
                "verifier_mode": self.config.agent_eval_mode,
                "verifier_called": False,
            }

        return self._compute_fallback_agent_evaluation(trajectories)

    # 对尚未达到“可终止观察窗口”的轮次延后 LLM verifier，避免每轮都支付高成本评审。
    def _llm_verifier_deferred_reason(
        self,
        *,
        trajectory_count: int,
        session_turn_index: int | None,
    ) -> str | None:
        min_turn_index = max(int(self.config.llm_verifier_min_turn_index), 0)
        min_trajectory_count = max(int(self.config.llm_verifier_min_trajectory_count), 1)

        if session_turn_index is not None and session_turn_index < min_turn_index:
            return "turn_index_too_low"

        if trajectory_count < min_trajectory_count:
            return "trajectory_count_too_low"

        return None

    # 使用原有启发式聚合分数作为 verifier 未出场时的轻量替代。
    def _compute_fallback_agent_evaluation(self, trajectories: List[ReasoningTrajectory]) -> tuple[float, dict]:
        total_score = sum(item.score for item in trajectories)
        best_score = max(item.score for item in trajectories)
        terminal_ratio = (
            sum(1 for item in trajectories if bool(item.metadata.get("path_terminal", False))) / len(trajectories)
        )
        normalized = total_score / len(trajectories)
        normalized = normalized * 0.55 + best_score * 0.3 + terminal_ratio * 0.15
        return max(min(normalized, 1.0), 0.0), {"verifier_mode": "fallback", "verifier_called": False}

    # 使用可选的 LLM verifier 对某个答案组做一次代理级评审。
    def _compute_llm_agent_evaluation(
        self,
        trajectories: List[ReasoningTrajectory],
        answer_id: str,
        answer_name: str,
        patient_context: PatientContext | None = None,
        answer_candidates: list[dict] | None = None,
    ) -> dict | None:
        if self.llm_client is None or not self.llm_client.is_available() or patient_context is None:
            return None

        # 只把该答案组里得分最高的最佳轨迹送给 verifier，
        # 避免 prompt 过重，同时仍保留 trajectory_count 和 answer_candidates 作为群体背景。
        best_trajectory = sorted(trajectories, key=lambda item: (-item.score, item.trajectory_id))[0]

        try:
            payload = self.llm_client.run_structured_prompt(
                "trajectory_agent_verifier",
                {
                    "patient_context": patient_context,
                    "answer_id": answer_id,
                    "answer_name": answer_name,
                    "best_trajectory": best_trajectory,
                    "trajectory_count": len(trajectories),
                    "answer_candidates": answer_candidates or [],
                },
                dict,
            )
        except Exception:
            return None

        if not isinstance(payload, dict):
            return None

        # verifier 输出必须被二次标准化：
        # schema 里漏填字段、布尔值文本化、reject_reason 不合规等情况都在这里兜住。
        try:
            score = float(payload.get("score", 0.0))
        except Exception:
            return None

        should_accept_stop = payload.get("should_accept_stop")

        if should_accept_stop is None:
            should_accept_stop = payload.get("should_accept")

        should_accept_stop_value = self._coerce_bool(should_accept_stop, default=score >= 0.75)
        missing_evidence = self._normalize_string_list(payload.get("missing_evidence", []))
        recommended_next_evidence = self._normalize_string_list(payload.get("recommended_next_evidence", []))
        alternative_candidates = self._normalize_alternative_candidates(payload.get("alternative_candidates", []))
        reject_reason, reject_reason_source, schema_valid = self._normalize_reject_reason(
            payload,
            trajectory_count=len(trajectories),
            alternative_candidates=alternative_candidates,
            missing_evidence=missing_evidence,
        )
        accept_reason, accept_reason_source, accept_schema_valid = self._normalize_accept_reason(
            payload,
            should_accept_stop=should_accept_stop_value,
            score=score,
            reject_reason=reject_reason,
        )

        return {
            "score": max(min(score, 1.0), 0.0),
            "should_accept_stop": should_accept_stop_value,
            "reject_reason": reject_reason,
            "accept_reason": accept_reason,
            "reasoning": str(payload.get("reasoning", "")),
            "missing_evidence": missing_evidence,
            "risk_flags": self._normalize_string_list(payload.get("risk_flags", [])),
            "recommended_next_evidence": recommended_next_evidence,
            "alternative_candidates": alternative_candidates,
            "reject_reason_source": reject_reason_source,
            "schema_valid": schema_valid,
            "accept_reason_source": accept_reason_source,
            "accept_schema_valid": accept_schema_valid,
        }

    # 将 accepted 路径的原因结构化，避免只知道“能停”却不知道为什么能停。
    def _normalize_accept_reason(
        self,
        payload: dict,
        should_accept_stop: bool,
        score: float,
        reject_reason: str,
    ) -> tuple[str, str, bool]:
        raw_reason = str(payload.get("accept_reason", "")).strip()

        # accept_reason 是 stop 后解释和实验统计的重要字段；
        # 若 LLM 漏填，就用低风险启发式补齐，并显式标记不是 schema 原值。
        if raw_reason in ALLOWED_ACCEPT_REASONS:
            return raw_reason, "llm_schema", True

        inferred_reason = self._infer_accept_reason(
            should_accept_stop=should_accept_stop,
            score=score,
            reject_reason=reject_reason,
        )
        return inferred_reason, "fallback_inferred", False

    # 当模型漏填 accept_reason 时，用低风险启发式补齐，并显式标记 fallback。
    def _infer_accept_reason(
        self,
        should_accept_stop: bool,
        score: float,
        reject_reason: str,
    ) -> str:
        if should_accept_stop and score >= 0.9:
            return "key_support_sufficient"

        if should_accept_stop and reject_reason == "strong_alternative_not_ruled_out":
            return "alternatives_reasonably_ruled_out"

        return "trajectory_stable"

    # verifier 是 repair policy 的控制信号，因此优先消费显式枚举，只有异常时才退回启发式推断。
    def _normalize_reject_reason(
        self,
        payload: dict,
        trajectory_count: int,
        alternative_candidates: list[dict],
        missing_evidence: list[str],
    ) -> tuple[str, str, bool]:
        raw_reason = str(payload.get("reject_reason", "")).strip()

        # reject_reason 是 repair 分流的关键控制信号，必须收敛到三类固定枚举。
        if raw_reason in ALLOWED_REJECT_REASONS:
            return raw_reason, "llm_schema", True

        inferred_reason = self._infer_reject_reason(
            payload,
            trajectory_count=trajectory_count,
            alternative_candidates=alternative_candidates,
            missing_evidence=missing_evidence,
        )
        return inferred_reason, "fallback_inferred", False

    # 对 verifier 输出中的候选替代诊断做标准化，统一为 dict 列表。
    def _normalize_alternative_candidates(self, payload: object) -> list[dict]:
        if not isinstance(payload, list):
            return []

        normalized: list[dict] = []

        for item in payload:
            if isinstance(item, dict):
                answer_name = str(item.get("answer_name") or item.get("name") or "").strip()
                answer_id = str(item.get("answer_id") or item.get("node_id") or "").strip()

                if len(answer_name) == 0 and len(answer_id) == 0:
                    continue

                normalized.append(
                    {
                        "answer_id": answer_id or None,
                        "answer_name": answer_name or answer_id,
                        "reason": str(item.get("reason", "")).strip(),
                    }
                )
                continue

            text = str(item).strip()

            if len(text) == 0:
                continue

            normalized.append({"answer_id": None, "answer_name": text, "reason": ""})

        return normalized

    # 将 verifier 返回的任意列表字段压平成字符串列表。
    def _normalize_string_list(self, payload: object) -> list[str]:
        if not isinstance(payload, list):
            return []

        values: list[str] = []

        for item in payload:
            text = str(item).strip()

            if len(text) == 0 or text in values:
                continue

            values.append(text)

        return values

    # 将模型可能返回的布尔文本标准化，避免 "false" 被 Python bool() 当成 True。
    def _coerce_bool(self, payload: object, default: bool) -> bool:
        if isinstance(payload, bool):
            return payload

        if isinstance(payload, (int, float)):
            return bool(payload)

        text = str(payload).strip().lower()

        if text in {"true", "1", "yes", "y", "是", "接受", "accept"}:
            return True

        if text in {"false", "0", "no", "n", "否", "拒绝", "reject"}:
            return False

        return default

    # 当 verifier 未显式返回 reject_reason 时，根据缺口特征做保守推断。
    def _infer_reject_reason(
        self,
        payload: dict,
        trajectory_count: int,
        alternative_candidates: list[dict],
        missing_evidence: list[str],
    ) -> str:
        raw_reason = str(payload.get("reject_reason", "")).strip()
        if raw_reason in ALLOWED_REJECT_REASONS:
            return raw_reason

        if len(alternative_candidates) > 0:
            return "strong_alternative_not_ruled_out"

        reasoning_text = " ".join(
            [
                str(payload.get("reasoning", "")),
                " ".join(self._normalize_string_list(payload.get("risk_flags", []))),
            ]
        ).lower()

        if any(keyword in reasoning_text for keyword in ["鉴别", "alternative", "替代", "未排除", "排除"]):
            return "strong_alternative_not_ruled_out"

        if trajectory_count <= 1 or any(keyword in reasoning_text for keyword in ["稳定", "路径", "不足", "不稳"]):
            return "trajectory_insufficient"

        if len(missing_evidence) > 0:
            return "missing_key_support"

        return "missing_key_support"

    # 使用动作序列 Jaccard 估计两条轨迹的相似度。
    def _trajectory_similarity(self, left: ReasoningTrajectory, right: ReasoningTrajectory) -> float:
        left_actions = self._extract_action_sequence(left)
        right_actions = self._extract_action_sequence(right)

        if len(left_actions) == 0 and len(right_actions) == 0:
            return 1.0

        left_set = set(left_actions)
        right_set = set(right_actions)
        union_size = len(left_set | right_set)

        if union_size == 0:
            return 0.0

        return len(left_set & right_set) / union_size

    # 提取轨迹里的动作名序列，忽略纯路由类步骤。
    def _extract_action_sequence(self, trajectory: ReasoningTrajectory) -> list[str]:
        action_names: list[str] = []

        for step in trajectory.steps:
            name = str(step.get("action_name", step.get("target_node_name", ""))).strip()

            if len(name) == 0:
                continue

            action_names.append(name)

        return action_names
