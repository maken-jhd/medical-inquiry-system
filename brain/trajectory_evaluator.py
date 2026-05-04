"""实现轨迹聚合、多维评分与最终答案选择。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from .llm_client import LlmClient
from .types import FinalAnswerScore, HypothesisScore, PatientContext, ReasoningTrajectory


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
    observed_anchor_agent_bonus_cap: float = 0.28
    simulated_key_evidence_penalty_cap: float = 0.12
    observed_final_accept_threshold: float = 0.72
    observed_final_scope_mismatch_block_threshold: float = 0.28


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
            anchor_profile = self._anchor_profile_for_answer(patient_context, answer_id)
            anchor_bonus = self._anchor_alignment_bonus(anchor_profile)
            simulated_penalty, simulated_metadata = self._simulated_key_evidence_penalty(
                trajectories,
                anchor_bonus=anchor_bonus,
                anchor_profile=anchor_profile,
            )
            agent_evaluation = max(min(agent_evaluation + anchor_bonus - simulated_penalty, 1.0), 0.0)
            agent_metadata.update(
                {
                    **self._anchor_profile_metadata(anchor_profile),
                    "observed_anchor_agent_bonus": round(anchor_bonus, 4),
                    **simulated_metadata,
                }
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

    # 当 rollout 没有形成可聚合答案时，保守地从当前候选态生成 answer score，避免 best_answer 断层。
    def score_candidate_hypotheses_without_trajectories(
        self,
        hypotheses: Sequence[HypothesisScore],
        patient_context: PatientContext | None = None,
        *,
        limit: int = 3,
    ) -> List[FinalAnswerScore]:
        ranked = sorted(hypotheses, key=lambda item: (-item.score, item.name))[: max(limit, 0)]

        if len(ranked) == 0:
            return []

        max_score = max(float(item.score) for item in ranked) or 1.0
        observed_evidence = self._observed_session_evidence(patient_context)
        scores: list[FinalAnswerScore] = []

        for index, hypothesis in enumerate(ranked):
            normalized_score = max(min(float(hypothesis.score) / max_score, 1.0), 0.0)
            observed_support = self._observed_answer_specific_support(
                answer_id=hypothesis.node_id,
                answer_name=hypothesis.name,
                observed_evidence=observed_evidence,
            )
            observed_support_count = len(observed_support)
            anchor_profile = self._anchor_profile_for_answer(patient_context, hypothesis.node_id)
            if len(anchor_profile) == 0:
                anchor_profile = dict(hypothesis.metadata)
            anchor_bonus = self._anchor_alignment_bonus(anchor_profile)
            observed_final = self._evaluate_observed_final_candidate(
                hypothesis=hypothesis,
                anchor_profile=anchor_profile,
                observed_support_count=observed_support_count,
                normalized_score=normalized_score,
                candidate_rank=index + 1,
            )
            support_bonus = min(observed_support_count * 0.08, 0.18) + anchor_bonus
            rank_penalty = index * 0.04
            heuristic_agent_evaluation = max(min(0.22 + normalized_score * 0.28 + support_bonus - rank_penalty, 0.74), 0.0)
            agent_evaluation = max(
                heuristic_agent_evaluation,
                float(observed_final["observed_final_score"]) if bool(observed_final["should_accept_stop"]) else min(float(observed_final["observed_final_score"]), 0.68),
            )
            consistency = max(0.12 - index * 0.025, 0.04)
            diversity = 0.0
            final_score = max(
                consistency * self.config.consistency_weight
                + diversity * self.config.diversity_weight
                + agent_evaluation * self.config.agent_eval_weight,
                float(observed_final["observed_final_score"]) * 0.58,
            )
            scores.append(
                FinalAnswerScore(
                    answer_id=hypothesis.node_id,
                    answer_name=hypothesis.name,
                    consistency=consistency,
                    diversity=diversity,
                    agent_evaluation=agent_evaluation,
                    final_score=final_score,
                    metadata={
                        "trajectory_count": 0,
                        "verifier_mode": "observed_evidence_final_evaluator",
                        "verifier_called": True,
                        "verifier_should_accept": bool(observed_final["should_accept_stop"]),
                        "verifier_reject_reason": str(observed_final["reject_reason"]),
                        "verifier_reasoning": str(observed_final["reasoning"]),
                        "verifier_missing_evidence": list(observed_final["missing_evidence"]),
                        "verifier_recommended_next_evidence": list(observed_final["recommended_next_evidence"]),
                        "verifier_alternative_candidates": [],
                        "verifier_accept_reason": str(observed_final["accept_reason"]),
                        "verifier_reject_reason_source": "observed_evidence_final_evaluator",
                        "verifier_schema_valid": True,
                        "answer_score_source": "candidate_state_fallback",
                        "candidate_rank": index + 1,
                        "candidate_state_score": hypothesis.score,
                        "heuristic_agent_evaluation": round(heuristic_agent_evaluation, 4),
                        "observed_answer_specific_support_count": observed_support_count,
                        "observed_answer_specific_support": observed_support,
                        "observed_anchor_agent_bonus": round(anchor_bonus, 4),
                        **observed_final["metadata"],
                        **self._anchor_profile_metadata(anchor_profile),
                    },
                )
            )

        return sorted(scores, key=lambda item: (-item.final_score, item.answer_name))

    # 没有 rollout trajectory 时，用真实 observed evidence 做一次轻量最终评估。
    # 这相当于 deterministic verifier：只消费真实会话证据，不读取模拟路径里的阳性。
    def _evaluate_observed_final_candidate(
        self,
        *,
        hypothesis: HypothesisScore,
        anchor_profile: dict,
        observed_support_count: int,
        normalized_score: float,
        candidate_rank: int,
    ) -> dict:
        if not isinstance(anchor_profile, dict):
            anchor_profile = {}

        exact_score = float(anchor_profile.get("exact_scope_anchor_score", 0.0) or 0.0)
        family_score = float(anchor_profile.get("family_scope_anchor_score", 0.0) or 0.0)
        definition_score = float(anchor_profile.get("definition_anchor_score", 0.0) or 0.0)
        phenotype_score = float(anchor_profile.get("phenotype_support_score", 0.0) or 0.0)
        background_score = float(anchor_profile.get("background_support_score", 0.0) or 0.0)
        negative_score = float(anchor_profile.get("anchor_negative_score", 0.0) or 0.0)
        scope_mismatch_score = float(anchor_profile.get("scope_mismatch_score", 0.0) or 0.0)
        generic_scope_penalty = float(anchor_profile.get("generic_scope_penalty", 0.0) or 0.0)
        scope_requirement_missing_score = float(anchor_profile.get("scope_requirement_missing_score", 0.0) or 0.0)
        scope_specificity_score = float(anchor_profile.get("scope_specificity_score", 0.0) or 0.0)
        low_cost_present_clear_count = int(anchor_profile.get("low_cost_present_clear_count", 0) or 0)
        low_cost_core_family_count = int(anchor_profile.get("low_cost_core_family_count", 0) or 0)
        low_cost_profile_satisfied = bool(anchor_profile.get("low_cost_profile_satisfied", False))
        minimum_groups_available = bool(anchor_profile.get("minimum_evidence_groups_available", False))
        family_coverage_satisfied = bool(anchor_profile.get("minimum_evidence_family_coverage_satisfied", False))
        anchor_tier = str(anchor_profile.get("anchor_tier") or "speculative")
        answer_name = hypothesis.name or hypothesis.node_id

        scope_block_score = scope_mismatch_score + generic_scope_penalty + scope_requirement_missing_score
        has_exact_anchor = exact_score + definition_score >= 0.35 or anchor_tier in {"strong_anchor", "definition_anchor"}
        has_family_coverage = minimum_groups_available and family_coverage_satisfied and (exact_score + family_score + phenotype_score) > 0.0
        has_low_cost_profile = low_cost_profile_satisfied and low_cost_present_clear_count >= 2 and low_cost_core_family_count >= 2
        has_clear_conflict = negative_score > 0.0 or scope_block_score >= self.config.observed_final_scope_mismatch_block_threshold

        observed_final_score = (
            0.28
            + normalized_score * 0.16
            + min(exact_score * 0.28, 0.24)
            + min(definition_score * 0.24, 0.18)
            + min(family_score * 0.16, 0.12)
            + min(phenotype_score * 0.06, 0.08)
            + min(scope_specificity_score * 0.16, 0.12)
            + min(low_cost_present_clear_count * 0.055, 0.16)
            + min(low_cost_core_family_count * 0.045, 0.14)
            + min(observed_support_count * 0.035, 0.10)
            + min(background_score * 0.01, 0.025)
            - negative_score * 0.18
            - scope_block_score * 0.24
            - max(candidate_rank - 1, 0) * 0.035
        )
        observed_final_score = max(min(observed_final_score, 0.94), 0.0)

        should_accept = False
        accept_reason = ""
        if not has_clear_conflict and has_exact_anchor and observed_final_score >= 0.62:
            should_accept = True
            accept_reason = "observed_strong_anchor_sufficient"
        elif not has_clear_conflict and has_family_coverage and observed_final_score >= 0.66:
            should_accept = True
            accept_reason = "observed_family_coverage_sufficient"
        elif not has_clear_conflict and has_low_cost_profile and observed_final_score >= self.config.observed_final_accept_threshold:
            should_accept = True
            accept_reason = "observed_low_cost_profile_sufficient"

        missing_evidence = self._observed_final_missing_evidence(anchor_profile)
        recommended_next_evidence = list(missing_evidence[:4])
        reject_reason = "missing_key_support"
        if has_clear_conflict:
            reject_reason = "strong_alternative_not_ruled_out"
        elif not has_exact_anchor and not has_family_coverage and not has_low_cost_profile:
            reject_reason = "missing_key_support"

        reasoning = (
            f"candidate_state_fallback 使用真实会话证据评估“{answer_name}”："
            f" exact={exact_score:.3f}, family={family_score:.3f}, low_cost={low_cost_present_clear_count}/{low_cost_core_family_count},"
            f" scope_block={scope_block_score:.3f}。"
        )

        return {
            "should_accept_stop": should_accept,
            "accept_reason": accept_reason or "observed_evidence_not_sufficient",
            "reject_reason": "none" if should_accept else reject_reason,
            "reasoning": reasoning,
            "missing_evidence": [] if should_accept else missing_evidence,
            "recommended_next_evidence": [] if should_accept else recommended_next_evidence,
            "observed_final_score": round(observed_final_score, 4),
            "metadata": {
                "observed_final_evaluator_applied": True,
                "observed_final_score": round(observed_final_score, 4),
                "observed_final_accept_basis": accept_reason,
                "observed_final_scope_block_score": round(scope_block_score, 4),
                "observed_final_has_exact_anchor": has_exact_anchor,
                "observed_final_has_family_coverage": has_family_coverage,
                "observed_final_has_low_cost_profile": has_low_cost_profile,
                "observed_final_has_clear_conflict": has_clear_conflict,
            },
        }

    def _observed_final_missing_evidence(self, anchor_profile: dict) -> list[str]:
        values: list[str] = []

        for item in anchor_profile.get("anchor_missing_evidence_families", []):
            text = str(item).strip()
            if len(text) > 0:
                values.append(f"补齐证据族：{text}")

        for item in anchor_profile.get("missing_scope_facets", []):
            text = str(item).strip()
            if len(text) > 0:
                values.append(f"补齐疾病作用域：{text}")

        for item in anchor_profile.get("missing_evidence_roles", []):
            text = str(item).strip()
            if len(text) > 0:
                values.append(f"补齐证据角色：{text}")

        return self._unique_strings(values)

    # 从 cumulative patient_context 中取当前答案对应的 observed anchor 摘要。
    def _anchor_profile_for_answer(self, patient_context: PatientContext | None, answer_id: str) -> dict:
        if patient_context is None:
            return {}

        anchor_index = patient_context.metadata.get("observed_anchor_index", {})
        if not isinstance(anchor_index, dict):
            return {}

        summaries = anchor_index.get("candidate_anchor_summary", [])
        if not isinstance(summaries, list):
            return {}

        for item in summaries:
            if not isinstance(item, dict):
                continue

            if str(item.get("candidate_id") or "") == answer_id:
                return dict(item)

        return {}

    # 把 anchor scope 转成 answer score 的轻量加分；背景证据只保留很小贡献。
    def _anchor_alignment_bonus(self, profile: dict) -> float:
        if not isinstance(profile, dict) or len(profile) == 0:
            return 0.0

        exact_score = float(profile.get("exact_scope_anchor_score", 0.0) or 0.0)
        if exact_score <= 0.0:
            exact_score = float(profile.get("strong_anchor_score", 0.0) or 0.0) + float(
                profile.get("definition_anchor_score", 0.0) or 0.0
            )
        family_score = float(profile.get("family_scope_anchor_score", 0.0) or 0.0)
        if family_score <= 0.0:
            family_score = float(profile.get("family_anchor_score", 0.0) or 0.0)
        phenotype_score = float(profile.get("phenotype_support_score", 0.0) or 0.0)
        background_score = float(profile.get("background_support_score", 0.0) or 0.0)
        negative_score = float(profile.get("anchor_negative_score", 0.0) or 0.0)
        background_attractor_score = float(profile.get("background_attractor_score", 0.0) or 0.0)
        scope_mismatch_score = float(profile.get("scope_mismatch_score", 0.0) or 0.0)

        raw_bonus = (
            exact_score * 0.22
            + family_score * 0.16
            + phenotype_score * 0.05
            + min(background_score * 0.015, 0.025)
            - negative_score * 0.18
            - background_attractor_score * 0.06
            - scope_mismatch_score * 0.08
        )
        return max(min(raw_bonus, self.config.observed_anchor_agent_bonus_cap), 0.0)

    # rollout 里模拟出来的关键阳性如果没有真实 anchor 承接，只能作为路径探索成本，而不能抬高最终答案。
    def _simulated_key_evidence_penalty(
        self,
        trajectories: Sequence[ReasoningTrajectory],
        *,
        anchor_bonus: float,
        anchor_profile: dict,
    ) -> tuple[float, dict]:
        simulated_positive_key_evidence: list[dict] = []

        for trajectory in trajectories:
            for item in self._extract_simulated_trajectory_evidence(trajectory):
                if str(item.get("polarity") or "") == "present" and bool(item.get("is_key_evidence", False)):
                    simulated_positive_key_evidence.append(item)

        simulated_names = self._unique_strings(str(item.get("name") or "") for item in simulated_positive_key_evidence)
        simulated_score = min(len(simulated_names) * 0.04, self.config.simulated_key_evidence_penalty_cap)
        has_observed_anchor = anchor_bonus > 0.0 or float(anchor_profile.get("observed_anchor_score", 0.0) or 0.0) > 0.0
        penalty = 0.0 if has_observed_anchor else simulated_score

        return penalty, {
            "simulated_trajectory_score": round(simulated_score, 4),
            "simulated_key_evidence_penalty": round(penalty, 4),
            "simulated_positive_key_evidence_names": simulated_names,
        }

    # 把 anchor profile 中最常用字段压回 FinalAnswerScore.metadata，方便 report / stop / benchmark 查看。
    def _anchor_profile_metadata(self, profile: dict) -> dict:
        if not isinstance(profile, dict) or len(profile) == 0:
            return {}

        keys = (
            "anchor_tier",
            "anchor_scope",
            "observed_anchor_score",
            "exact_scope_anchor_score",
            "family_scope_anchor_score",
            "family_anchor_score",
            "background_support_score",
            "background_attractor_score",
            "scope_mismatch_score",
            "scope_specificity_score",
            "generic_scope_penalty",
            "scope_requirement_missing_score",
            "candidate_scope_facets",
            "observed_scope_facets",
            "missing_scope_facets",
            "scope_mismatch_reasons",
            "anchor_negative_score",
            "anchor_supporting_evidence",
            "family_anchor_evidence",
            "anchor_negative_evidence",
            "minimum_evidence_groups_available",
            "minimum_evidence_family_coverage_satisfied",
            "anchor_missing_evidence_families",
            "missing_evidence_roles",
            "low_cost_present_clear_count",
            "low_cost_core_family_count",
            "low_cost_profile_satisfied",
        )
        return {key: profile.get(key) for key in keys if key in profile}

    # LLM verifier guard 也消费 anchor analyzer 已经对齐好的 observed 支持，避免纯文本 overlap 漏召回。
    def _observed_support_from_anchor_profile(self, profile: dict) -> list[dict]:
        if not isinstance(profile, dict) or len(profile) == 0:
            return []

        values: list[dict] = []
        seen_keys: set[tuple[str, str]] = set()

        for key in ("anchor_supporting_evidence", "family_anchor_evidence", "provisional_anchor_evidence"):
            payload = profile.get(key, [])
            if not isinstance(payload, list):
                continue

            for item in payload:
                if not isinstance(item, dict):
                    continue

                normalized = dict(item)
                dedupe_key = (
                    str(normalized.get("node_id") or ""),
                    str(normalized.get("name") or normalized.get("observed_name") or ""),
                )

                if dedupe_key in seen_keys:
                    continue

                seen_keys.add(dedupe_key)
                values.append(normalized)

        return values

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
                # 会直接成为最终接受控制和 repair 的信号。
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
                    "observed_evidence_guard_applied": llm_result.get("observed_evidence_guard_applied", False),
                    "observed_answer_specific_support_count": llm_result.get(
                        "observed_answer_specific_support_count",
                        0,
                    ),
                    "observed_answer_specific_support": llm_result.get("observed_answer_specific_support", []),
                    "simulated_positive_key_evidence": llm_result.get("simulated_positive_key_evidence", []),
                    "simulated_positive_key_evidence_names": llm_result.get(
                        "simulated_positive_key_evidence_names",
                        [],
                    ),
                    "verifier_acceptance_blocked_by_observed_evidence_guard": llm_result.get(
                        "verifier_acceptance_blocked_by_observed_evidence_guard",
                        False,
                    ),
                    "observed_evidence_guard_reason": llm_result.get("observed_evidence_guard_reason", ""),
                    "scope_acceptance_guard_applied": llm_result.get("scope_acceptance_guard_applied", False),
                    "scope_acceptance_guard_blocked": llm_result.get("scope_acceptance_guard_blocked", False),
                    "scope_acceptance_guard_reason": llm_result.get("scope_acceptance_guard_reason", ""),
                    "scope_acceptance_guard_missing_scope_facets": llm_result.get(
                        "scope_acceptance_guard_missing_scope_facets",
                        [],
                    ),
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
        observed_session_evidence = self._observed_session_evidence(patient_context)
        simulated_trajectory_evidence = self._extract_simulated_trajectory_evidence(best_trajectory)
        observed_support = self._observed_answer_specific_support(
            answer_id=answer_id,
            answer_name=answer_name,
            observed_evidence=observed_session_evidence,
        )
        anchor_profile = self._anchor_profile_for_answer(patient_context, answer_id)
        if len(observed_support) == 0:
            observed_support = self._observed_support_from_anchor_profile(anchor_profile)

        try:
            payload = self.llm_client.run_structured_prompt(
                "trajectory_agent_verifier",
                {
                    "patient_context": patient_context,
                    "answer_id": answer_id,
                    "answer_name": answer_name,
                    "best_trajectory": best_trajectory,
                    "observed_session_evidence": observed_session_evidence,
                    "observed_answer_specific_support": observed_support,
                    "simulated_trajectory_evidence": simulated_trajectory_evidence,
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
        guard_metadata = self._apply_observed_evidence_acceptance_guard(
            answer_id=answer_id,
            answer_name=answer_name,
            should_accept_stop=should_accept_stop_value,
            score=score,
            reject_reason=reject_reason,
            missing_evidence=missing_evidence,
            recommended_next_evidence=recommended_next_evidence,
            observed_support=observed_support,
            simulated_trajectory_evidence=simulated_trajectory_evidence,
        )
        guard_metadata = self._apply_scope_acceptance_guard(
            answer_name=answer_name,
            anchor_profile=anchor_profile,
            guard_metadata=guard_metadata,
        )
        should_accept_stop_value = bool(guard_metadata["should_accept_stop"])
        score = float(guard_metadata["score"])
        reject_reason = str(guard_metadata["reject_reason"])
        missing_evidence = list(guard_metadata["missing_evidence"])
        recommended_next_evidence = list(guard_metadata["recommended_next_evidence"])
        if not should_accept_stop_value and bool(guard_metadata["metadata"].get("verifier_acceptance_blocked_by_observed_evidence_guard", False)):
            accept_reason = self._infer_accept_reason(
                should_accept_stop=False,
                score=score,
                reject_reason=reject_reason,
            )
            accept_reason_source = "guarded_observed_evidence_guard"
            accept_schema_valid = False
            reject_reason_source = "observed_evidence_guard"
            schema_valid = False
        elif not should_accept_stop_value and bool(guard_metadata["metadata"].get("scope_acceptance_guard_blocked", False)):
            accept_reason = self._infer_accept_reason(
                should_accept_stop=False,
                score=score,
                reject_reason=reject_reason,
            )
            accept_reason_source = "scope_acceptance_guard"
            accept_schema_valid = False
            reject_reason_source = "scope_acceptance_guard"
            schema_valid = False

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
            **guard_metadata["metadata"],
        }

    # LLM verifier 可能接受“同病原但粒度不对”的答案；
    # 这里用 observed anchor 的 scope 画像做一次确定性校验。
    def _apply_scope_acceptance_guard(
        self,
        *,
        answer_name: str,
        anchor_profile: dict,
        guard_metadata: dict,
    ) -> dict:
        if not bool(guard_metadata.get("should_accept_stop", False)):
            return guard_metadata

        if not isinstance(anchor_profile, dict) or len(anchor_profile) == 0:
            return guard_metadata

        scope_mismatch_score = float(anchor_profile.get("scope_mismatch_score", 0.0) or 0.0)
        generic_scope_penalty = float(anchor_profile.get("generic_scope_penalty", 0.0) or 0.0)
        scope_requirement_missing_score = float(anchor_profile.get("scope_requirement_missing_score", 0.0) or 0.0)
        missing_scope_facets = [
            str(item)
            for item in anchor_profile.get("missing_scope_facets", [])
            if len(str(item).strip()) > 0
        ]
        scope_block_score = scope_mismatch_score + generic_scope_penalty + scope_requirement_missing_score

        if scope_block_score < self.config.observed_final_scope_mismatch_block_threshold and len(missing_scope_facets) == 0:
            return guard_metadata

        recommended = self._merge_unique_strings(
            list(guard_metadata.get("recommended_next_evidence", [])),
            [f"补齐疾病作用域：{item}" for item in missing_scope_facets],
        )
        missing = self._merge_unique_strings(
            list(guard_metadata.get("missing_evidence", [])),
            [f"补齐疾病作用域：{item}" for item in missing_scope_facets],
        )
        metadata = dict(guard_metadata.get("metadata", {}))
        metadata.update(
            {
                "scope_acceptance_guard_applied": True,
                "scope_acceptance_guard_blocked": True,
                "scope_acceptance_guard_reason": (
                    f"候选答案“{answer_name}”的真实证据作用域不足，scope_block_score={scope_block_score:.3f}。"
                ),
                "scope_acceptance_guard_missing_scope_facets": missing_scope_facets,
            }
        )
        return {
            **guard_metadata,
            "should_accept_stop": False,
            "score": min(float(guard_metadata.get("score", 0.0) or 0.0), 0.64),
            "reject_reason": "strong_alternative_not_ruled_out",
            "missing_evidence": missing,
            "recommended_next_evidence": recommended,
            "metadata": metadata,
        }

    # 如果 verifier 想基于 rollout 模拟阳性停机，但真实会话没有当前答案的特异支持，则强制改为拒停。
    def _apply_observed_evidence_acceptance_guard(
        self,
        *,
        answer_id: str,
        answer_name: str,
        should_accept_stop: bool,
        score: float,
        reject_reason: str,
        missing_evidence: list[str],
        recommended_next_evidence: list[str],
        observed_support: list[dict],
        simulated_trajectory_evidence: list[dict],
    ) -> dict:
        simulated_positive_key_evidence = [
            item
            for item in simulated_trajectory_evidence
            if str(item.get("polarity") or "") == "present" and bool(item.get("is_key_evidence", False))
        ]
        observed_support_count = len(observed_support)
        blocked = bool(should_accept_stop) and observed_support_count == 0 and len(simulated_positive_key_evidence) > 0
        simulated_names = self._unique_strings(str(item.get("name") or "") for item in simulated_positive_key_evidence)
        metadata = {
            "observed_evidence_guard_applied": True,
            "observed_answer_specific_support_count": observed_support_count,
            "observed_answer_specific_support": observed_support,
            "simulated_positive_key_evidence": simulated_positive_key_evidence,
            "simulated_positive_key_evidence_names": simulated_names,
            "verifier_acceptance_blocked_by_observed_evidence_guard": blocked,
        }

        if not blocked:
            return {
                "should_accept_stop": should_accept_stop,
                "score": score,
                "reject_reason": reject_reason,
                "missing_evidence": missing_evidence,
                "recommended_next_evidence": recommended_next_evidence,
                "metadata": metadata,
            }

        recommended = self._merge_unique_strings(recommended_next_evidence, simulated_names)
        missing = self._merge_unique_strings(missing_evidence, simulated_names)
        metadata["observed_evidence_guard_reason"] = (
            f"候选答案“{answer_name or answer_id}”的关键支持只来自 rollout 模拟阳性，真实会话尚未确认。"
        )
        metadata["verifier_reject_reason_source"] = "observed_evidence_guard"
        return {
            "should_accept_stop": False,
            "score": min(score, 0.64),
            "reject_reason": "missing_key_support",
            "missing_evidence": missing,
            "recommended_next_evidence": recommended,
            "metadata": metadata,
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

    # 从 verifier patient_context metadata 中取出真实会话证据。
    def _observed_session_evidence(self, patient_context: PatientContext | None) -> list[dict]:
        if patient_context is None:
            return []

        payload = patient_context.metadata.get("observed_session_evidence", [])

        if not isinstance(payload, list):
            return []

        return [dict(item) for item in payload if isinstance(item, dict)]

    # 从最佳 rollout 轨迹中提取模拟出来的证据，供 verifier 区分“可验证建议”和“真实已确认事实”。
    def _extract_simulated_trajectory_evidence(self, trajectory: ReasoningTrajectory) -> list[dict]:
        values: list[dict] = []
        current_action: dict[str, Any] = {}

        for step in trajectory.steps:
            stage = str(step.get("stage") or "")

            if stage == "A3":
                current_action = {
                    "action_id": str(step.get("action_id") or ""),
                    "node_id": str(step.get("target_node_id") or ""),
                    "name": str(step.get("action_name") or step.get("target_node_name") or ""),
                    "hypothesis_id": str(step.get("hypothesis_id") or ""),
                    "question_type_hint": str(step.get("question_type_hint") or ""),
                }
                continue

            if stage != "PENDING_ACTION" or len(current_action) == 0:
                continue

            polarity = str(step.get("polarity") or "")
            resolution = str(step.get("resolution") or "")
            item = {
                **current_action,
                "polarity": polarity,
                "resolution": resolution,
                "answer_branch": str(step.get("answer_branch") or ""),
                "source": "rollout_simulated",
                "is_key_evidence": self._is_key_simulated_evidence(current_action),
            }
            values.append(item)

        return values

    # 判断 rollout 证据是否足以影响诊断接受边界。
    def _is_key_simulated_evidence(self, action: dict[str, Any]) -> bool:
        text = self._normalize_match_text(
            " ".join(
                [
                    str(action.get("name") or ""),
                    str(action.get("question_type_hint") or ""),
                ]
            )
        )
        key_markers = (
            "lab",
            "imaging",
            "pathogen",
            "培养",
            "抗酸",
            "pcr",
            "核酸",
            "抗体",
            "抗原",
            "ct",
            "mri",
            "影像",
            "病原",
            "阳性",
            "dna",
            "rna",
        )
        return any(marker in text for marker in key_markers)

    # 判断真实会话里是否已有当前答案的特异支持；HIV/CD4 等泛背景不单独算作具体机会感染/肿瘤的接受依据。
    def _observed_answer_specific_support(
        self,
        *,
        answer_id: str,
        answer_name: str,
        observed_evidence: list[dict],
    ) -> list[dict]:
        values: list[dict] = []
        answer_text = self._normalize_match_text(answer_name)

        for item in observed_evidence:
            if str(item.get("polarity") or item.get("effective_polarity") or "") != "present":
                continue

            if str(item.get("resolution") or "") not in {"clear", "hedged"}:
                continue

            evidence_name = str(item.get("name") or item.get("normalized_name") or item.get("node_id") or "")
            evidence_text = self._normalize_match_text(evidence_name)
            hypothesis_id = str(item.get("hypothesis_id") or "")
            relation_type = str(item.get("relation_type") or "")

            if hypothesis_id == answer_id and not self._is_generic_background_evidence(evidence_text):
                values.append(dict(item))
                continue

            if self._has_meaningful_answer_overlap(answer_text, evidence_text):
                values.append(dict(item))
                continue

            if relation_type in {"DIAGNOSED_BY", "HAS_PATHOGEN"} and not self._is_generic_background_evidence(evidence_text):
                values.append(dict(item))

        return values

    def _has_meaningful_answer_overlap(self, answer_text: str, evidence_text: str) -> bool:
        if len(answer_text) == 0 or len(evidence_text) == 0:
            return False

        if "hiv" in answer_text and "hiv" in evidence_text:
            return True

        if evidence_text in answer_text or answer_text in evidence_text:
            return not self._is_generic_background_evidence(evidence_text)

        tokens = [
            token
            for token in (
                "水痘带状疱疹",
                "巨细胞",
                "弓形虫",
                "隐球菌",
                "结核",
                "分枝杆菌",
                "肺孢子",
                "卡波西",
                "hhv8",
                "hiv",
                "血脂",
                "ldl",
                "甘油三酯",
                "肥胖",
            )
            if token in answer_text and token in evidence_text
        ]
        return len(tokens) > 0

    def _is_generic_background_evidence(self, evidence_text: str) -> bool:
        if len(evidence_text) == 0:
            return True

        generic_markers = (
            "hiv感染者",
            "hiv感染",
            "艾滋",
            "cd4",
            "t淋巴",
            "免疫功能低下",
            "免疫抑制",
            "年龄",
            "发热",
        )
        return any(marker in evidence_text for marker in generic_markers)

    def _normalize_match_text(self, text: str) -> str:
        return (
            str(text or "")
            .strip()
            .lower()
            .replace(" ", "")
            .replace("（", "(")
            .replace("）", ")")
            .replace("-", "")
            .replace("_", "")
            .replace("/", "")
            .replace("+", "")
        )

    def _unique_strings(self, values: Iterable[str]) -> list[str]:
        unique: list[str] = []

        for value in values:
            text = str(value).strip()
            if len(text) == 0 or text in unique:
                continue
            unique.append(text)

        return unique

    def _merge_unique_strings(self, left: Sequence[str], right: Sequence[str]) -> list[str]:
        return self._unique_strings([*left, *right])

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
