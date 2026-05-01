"""负责判断问诊是否可以停止，或是否需要降级处理。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable

from .types import EvidenceState, FinalAnswerScore, HypothesisScore, SessionState, StopDecision


GUARDED_LENIENT_PROFILES = {"guarded_lenient", "guarded-lenient", "guarded"}
GUARDED_DEFINITION_RELATION_TYPES = {
    "DIAGNOSED_BY",
    "HAS_LAB_FINDING",
    "HAS_IMAGING_FINDING",
    "HAS_PATHOGEN",
    "CONFIRMED_BY",
    "DETECTED_BY",
}
GUARDED_KEY_SUPPORT_RELATION_TYPES = GUARDED_DEFINITION_RELATION_TYPES | {
    "MANIFESTS_AS",
    "RISK_FACTOR_FOR",
    "REQUIRES_DETAIL",
}
GUARDED_CONFIRMED_EVIDENCE_TAGS = {
    "imaging",
    "oxygenation",
    "pathogen",
    "immune_status",
    "pcp_specific",
    "tuberculosis",
}
GUARDED_KEY_SUPPORT_EVIDENCE_TAGS = GUARDED_CONFIRMED_EVIDENCE_TAGS | {
    "respiratory",
    "risk",
}
GUARDED_EVIDENCE_FAMILY_TAGS = GUARDED_KEY_SUPPORT_EVIDENCE_TAGS | {
    "systemic",
    "viral",
}
GUARDED_HARD_NEGATIVE_EVIDENCE_TAGS = {
    "imaging",
    "pathogen",
    "pcp_specific",
    "immune_status",
    "tuberculosis",
}
GUARDED_SHAREABLE_RESPIRATORY_EVIDENCE_TAGS = {
    "imaging",
    "oxygenation",
    "immune_status",
    "respiratory",
    "risk",
}
GUARDED_PCP_PRIMARY_MISSING_FAMILY_ORDER = (
    "imaging",
    "immune_status",
    "pathogen",
    "pcp_specific",
    "oxygenation",
    "respiratory",
)
GUARDED_HIGH_RISK_RESPIRATORY_KEYWORDS = (
    "肺孢子",
    "pcp",
    "pneumocystis",
    "结核",
    "tb",
    "tuberculosis",
    "肺炎",
    "肺部感染",
    "呼吸道感染",
    "真菌",
    "组织胞浆",
)
GUARDED_PROVISIONAL_ANCHOR_FAMILIES = {
    "imaging",
    "oxygenation",
    "pathogen",
    "immune_status",
    "pcp_specific",
}


@dataclass
class StopRuleConfig:
    """保存终止条件与 fallback 条件相关阈值。"""

    min_top1_margin: float = 0.25
    min_top1_score: float = 1.0
    max_fail_count: int = 2
    min_candidate_count: int = 1
    max_rollouts: int = 8
    max_tree_depth: int = 6
    min_answer_consistency: float = 0.45
    min_agent_eval_score: float = 0.65
    min_final_score: float = 0.55
    min_turn_index_before_final_answer: int = 2
    min_trajectory_count_before_accept: int = 2
    require_verifier_accept_flag: bool = True
    acceptance_profile: str = "baseline"
    guarded_lenient_early_turn_index: int = 2


class StopRuleEngine:
    """根据候选假设分数与失败次数做终止判断。"""

    # 初始化终止规则配置。
    def __init__(self, config: StopRuleConfig | None = None) -> None:
        self.config = config or StopRuleConfig()

    # 判断当前证据是否足以结束问诊并输出结果。
    def check_sufficiency(
        self,
        session_state: SessionState,
        hypotheses: Iterable[HypothesisScore],
    ) -> StopDecision:
        ranked = list(hypotheses)

        if len(ranked) == 0:
            return StopDecision(False, "no_hypothesis")

        if len(ranked) == 1 and ranked[0].score >= self.config.min_top1_score:
            return StopDecision(True, "single_hypothesis_clear", ranked[0].score)

        if len(ranked) >= 2:
            margin = ranked[0].score - ranked[1].score

            if ranked[0].score >= self.config.min_top1_score and margin >= self.config.min_top1_margin:
                return StopDecision(
                    True,
                    "top1_margin_sufficient",
                    ranked[0].score,
                    {"margin": margin},
                )

        return StopDecision(False, "insufficient_evidence")

    # 判断当前会话是否应进入降级或兜底流程。
    def should_fallback(self, session_state: SessionState) -> StopDecision:
        if session_state.fail_count >= self.config.max_fail_count:
            return StopDecision(True, "fallback_due_to_fail_count")

        return StopDecision(False, "continue")

    # 判断单条 rollout 是否应停止继续向下扩展。
    def should_stop_rollout(self, current_depth: int, fail_count: int = 0) -> StopDecision:
        if current_depth >= self.config.max_tree_depth:
            return StopDecision(True, "max_tree_depth_reached")

        if fail_count >= self.config.max_fail_count:
            return StopDecision(True, "rollout_fail_threshold_reached")

        return StopDecision(False, "continue_rollout")

    # 判断当前搜索过程是否应整体停止。
    def should_stop_search(self, rollout_count: int, fail_count: int = 0) -> StopDecision:
        if rollout_count >= self.config.max_rollouts:
            return StopDecision(True, "max_rollouts_reached")

        if fail_count >= self.config.max_fail_count:
            return StopDecision(True, "search_fail_threshold_reached")

        return StopDecision(False, "continue_search")

    # 判断最终答案评分是否足以被接受为当前结果。
    def should_accept_final_answer(
        self,
        answer_score: FinalAnswerScore | None,
        session_state: SessionState | None = None,
    ) -> StopDecision:
        if answer_score is None:
            return StopDecision(False, "no_answer_score")

        # 每次都先把当前 top answer 和记一次 verifier 愿意接受的候选写入历史，
        # 这样 guarded gate 后续才能判断“答案是否刚切换”“之前 accept 过的是不是另一个答案”。
        acceptance_profile = self._acceptance_profile()
        self._record_answer_candidate(session_state, answer_score)
        self._record_verifier_accept_candidate(session_state, answer_score)
        answer_score.metadata["acceptance_profile"] = acceptance_profile

        # 先过基础的时间窗口和轨迹数量门槛，避免问诊过早停止。
        if session_state is not None and session_state.turn_index < self.config.min_turn_index_before_final_answer:
            return StopDecision(False, "turn_index_too_low", float(session_state.turn_index))

        trajectory_count = int(answer_score.metadata.get("trajectory_count", 0))

        if trajectory_count < self.config.min_trajectory_count_before_accept:
            return StopDecision(False, "trajectory_count_too_low", float(trajectory_count))

        verifier_mode = str(answer_score.metadata.get("verifier_mode", ""))

        if (
            self.config.require_verifier_accept_flag
            and verifier_mode == "llm_verifier"
            and not bool(answer_score.metadata.get("verifier_should_accept", False))
        ):
            return StopDecision(False, "verifier_rejected_stop", answer_score.agent_evaluation)

        # guarded_lenient profile 只放宽 verifier prompt，
        # 但最终是否允许停诊仍要经过结构化安全闸门二次确认。
        guarded_decision = self._check_guarded_lenient_acceptance(answer_score, session_state, acceptance_profile)

        if guarded_decision is not None:
            return guarded_decision

        # 若没触发 guarded gate，再退回常规数值阈值判断。
        if answer_score.consistency < self.config.min_answer_consistency:
            return StopDecision(False, "consistency_too_low", answer_score.consistency)

        if answer_score.agent_evaluation < self.config.min_agent_eval_score:
            return StopDecision(False, "agent_eval_too_low", answer_score.agent_evaluation)

        if answer_score.final_score < self.config.min_final_score:
            return StopDecision(False, "final_score_too_low", answer_score.final_score)

        return StopDecision(True, "final_answer_accepted", answer_score.final_score)

    # 读取 verifier acceptance profile；真实实验脚本通过环境变量控制该值。
    def _acceptance_profile(self) -> str:
        return (
            os.getenv("TRAJECTORY_VERIFIER_ACCEPTANCE_PROFILE")
            or self.config.acceptance_profile
            or "baseline"
        ).strip().lower()

    # guarded_lenient 只放宽 verifier prompt，不放宽安全闸门。
    def _check_guarded_lenient_acceptance(
        self,
        answer_score: FinalAnswerScore,
        session_state: SessionState | None,
        acceptance_profile: str,
    ) -> StopDecision | None:
        # 只有 guarded_lenient profile 才启用这道 gate；
        # baseline / strict 等模式仍使用普通阈值，不走额外安全校验。
        if acceptance_profile not in GUARDED_LENIENT_PROFILES:
            return None

        if session_state is None:
            return None

        verifier_mode = str(answer_score.metadata.get("verifier_mode", ""))

        if verifier_mode != "llm_verifier" or not bool(answer_score.metadata.get("verifier_should_accept", False)):
            return None

        # 先提炼出所有“是否能停”需要看的结构化证据特征，并挂到 metadata 里便于前端和复盘查看。
        guard_features = self._build_guarded_acceptance_features(answer_score, session_state)
        answer_score.metadata.update(
            {
                "guarded_acceptance_applied": True,
                **guard_features,
            }
        )
        session_state.metadata["last_guarded_acceptance_features"] = dict(guard_features)

        # 所有安全特征最后会被压缩成一个最主要的 block_reason，
        # 这样 repair 分流和实验统计都能稳定消费。
        block_reason = self._select_guarded_block_reason(guard_features)

        if len(block_reason) == 0:
            answer_score.metadata["guarded_acceptance_block_reason"] = ""
            session_state.metadata["last_guarded_acceptance_decision"] = {
                "should_accept": True,
                "reason": "",
                "answer_id": answer_score.answer_id,
                "answer_name": answer_score.answer_name,
                "turn_index": session_state.turn_index,
                **guard_features,
            }
            return None

        # 一旦 block，就把拒停原因、guard features 和审计条目全部写回，
        # 后续 repair 可以据此选更有针对性的补证据动作。
        metadata = {
            **guard_features,
            "acceptance_profile": acceptance_profile,
            "guarded_acceptance_block_reason": block_reason,
            "repair_reject_reason": self._map_guarded_block_to_repair_reason(block_reason),
        }
        audit_entry = self._build_guarded_gate_audit_entry(
            answer_score,
            session_state,
            block_reason=block_reason,
            guard_features=guard_features,
        )
        metadata["guarded_gate_audit"] = audit_entry
        answer_score.metadata["guarded_acceptance_block_reason"] = block_reason
        answer_score.metadata["guarded_gate_audit"] = audit_entry
        self._record_guarded_gate_audit(session_state, audit_entry)
        session_state.metadata["last_guarded_acceptance_decision"] = {
            "should_accept": False,
            "reason": block_reason,
            "answer_id": answer_score.answer_id,
            "answer_name": answer_score.answer_name,
            "turn_index": session_state.turn_index,
            **metadata,
        }
        return StopDecision(
            False,
            "guarded_acceptance_rejected",
            answer_score.agent_evaluation,
            metadata,
        )

    # 汇总 guarded_lenient 判断所需的安全特征。
    def _build_guarded_acceptance_features(
        self,
        answer_score: FinalAnswerScore,
        session_state: SessionState,
    ) -> dict:
        # 先拆出三类关键证据：
        # - confirmed：exist + clear 的关键支持
        # - provisional：exist + hedged 的高价值 anchor
        # - negative/doubtful：会削弱当前答案的反向证据
        confirmed_key_evidence = self._collect_confirmed_key_evidence(answer_score, session_state)
        provisional_key_evidence = self._collect_provisional_key_evidence(answer_score, session_state)
        negative_or_doubtful_key_evidence = self._collect_negative_or_doubtful_key_evidence(
            answer_score,
            session_state,
        )
        hard_negative_key_evidence = [
            item
            for item in negative_or_doubtful_key_evidence
            if str(item.get("negative_evidence_tier") or "") == "hard"
        ]
        soft_negative_or_doubtful_key_evidence = [
            item
            for item in negative_or_doubtful_key_evidence
            if str(item.get("negative_evidence_tier") or "") == "soft"
        ]
        alternative_candidates = self._normalize_alternative_candidates(
            answer_score.metadata.get("verifier_alternative_candidates", [])
        )
        strong_alternative_candidates = [
            item for item in alternative_candidates if bool(item.get("is_unresolved_strong", False))
        ]
        weak_or_ruled_down_alternative_candidates = [
            item for item in alternative_candidates if not bool(item.get("is_unresolved_strong", False))
        ]
        answer_changed_after_first_accept = self._answer_changed_after_first_accept(answer_score, session_state)
        recent_hypothesis_switch = self._has_recent_hypothesis_switch(answer_score, session_state)
        confirmed_key_evidence_families = self._collect_evidence_families(confirmed_key_evidence)
        provisional_key_evidence_families = self._collect_evidence_families(provisional_key_evidence)
        combined_key_evidence_families = confirmed_key_evidence_families | provisional_key_evidence_families
        high_risk_respiratory_answer = self._is_high_risk_respiratory_answer(answer_score)
        pcp_answer = self._is_pcp_answer(answer_score)
        confirmed_pcp_combo = self._evaluate_pcp_combo(confirmed_key_evidence_families)
        combined_pcp_combo = self._evaluate_pcp_combo(combined_key_evidence_families)
        pcp_combo_uses_provisional = (
            pcp_answer
            and not bool(confirmed_pcp_combo.get("satisfied", False))
            and bool(combined_pcp_combo.get("satisfied", False))
        )
        pcp_combo = combined_pcp_combo if pcp_combo_uses_provisional else confirmed_pcp_combo
        missing_evidence_families = self._select_guarded_missing_evidence_families(
            combined_key_evidence_families,
            pcp_combo=pcp_combo,
            pcp_answer=pcp_answer,
            high_risk_respiratory_answer=high_risk_respiratory_answer,
        )
        recent_key_evidence_states = self._collect_recent_key_evidence_states(answer_score, session_state)
        soft_negative_requires_stability = (
            len(soft_negative_or_doubtful_key_evidence) > 0
            and not self._has_prior_verifier_accept_for_answer(answer_score, session_state)
        )

        # 返回的 feature dict 会同时服务：
        # guarded gate 判停、repair 推荐证据、前端调试展示和实验审计。
        return {
            "guarded_early_accept_window": session_state.turn_index <= self.config.guarded_lenient_early_turn_index,
            "guarded_high_risk_respiratory_answer": high_risk_respiratory_answer,
            "guarded_pcp_answer": pcp_answer,
            "guarded_has_confirmed_key_evidence": len(confirmed_key_evidence) > 0,
            "guarded_confirmed_key_evidence": confirmed_key_evidence,
            "guarded_confirmed_key_evidence_families": sorted(confirmed_key_evidence_families),
            "guarded_has_provisional_key_evidence": len(provisional_key_evidence) > 0,
            "guarded_provisional_key_evidence": provisional_key_evidence,
            "guarded_provisional_key_evidence_families": sorted(provisional_key_evidence_families),
            "guarded_has_confirmed_or_provisional_key_evidence": len(combined_key_evidence_families) > 0,
            "guarded_combined_key_evidence_families": sorted(combined_key_evidence_families),
            "guarded_missing_evidence_families": missing_evidence_families,
            "guarded_pcp_combo_satisfied": bool(pcp_combo.get("satisfied", False)),
            "guarded_confirmed_pcp_combo_satisfied": bool(confirmed_pcp_combo.get("satisfied", False)),
            "guarded_pcp_combo_uses_provisional": pcp_combo_uses_provisional,
            "guarded_pcp_combo_variant": str(pcp_combo.get("variant", "")),
            "guarded_pcp_combo_missing_family_options": pcp_combo.get("missing_family_options", []),
            "guarded_negative_or_doubtful_key_evidence": negative_or_doubtful_key_evidence,
            "guarded_has_negative_or_doubtful_key_evidence": len(negative_or_doubtful_key_evidence) > 0,
            "guarded_hard_negative_key_evidence": hard_negative_key_evidence,
            "guarded_has_hard_negative_key_evidence": len(hard_negative_key_evidence) > 0,
            "guarded_soft_negative_or_doubtful_key_evidence": soft_negative_or_doubtful_key_evidence,
            "guarded_has_soft_negative_or_doubtful_key_evidence": len(soft_negative_or_doubtful_key_evidence) > 0,
            "guarded_soft_negative_requires_stability": soft_negative_requires_stability,
            "guarded_recent_key_evidence_states": recent_key_evidence_states,
            "guarded_recent_hypothesis_switch": recent_hypothesis_switch,
            "guarded_answer_changed_after_first_accept": answer_changed_after_first_accept,
            "guarded_nonempty_alternative_candidates": len(alternative_candidates) > 0,
            "guarded_alternative_candidate_count": len(alternative_candidates),
            "guarded_alternative_candidates": alternative_candidates,
            "guarded_strong_alternative_candidates": strong_alternative_candidates,
            "guarded_weak_or_ruled_down_alternative_candidates": weak_or_ruled_down_alternative_candidates,
            "guarded_has_strong_unresolved_alternative": len(strong_alternative_candidates) > 0,
            "guarded_strong_alternative_candidate_count": len(strong_alternative_candidates),
        }

    # 选择一个最主要的 guarded block reason，保持可解释且便于统计。
    def _select_guarded_block_reason(self, guard_features: dict) -> str:
        # block reason 有明确优先级：
        # 先看硬反证，再看关键支持缺失，再看强备选和稳定性问题。
        if bool(guard_features.get("guarded_has_hard_negative_key_evidence", False)):
            return "hard_negative_key_evidence"

        if bool(guard_features.get("guarded_early_accept_window", False)) and not bool(
            guard_features.get("guarded_has_confirmed_or_provisional_key_evidence", False)
        ):
            return "missing_confirmed_key_evidence"

        if bool(guard_features.get("guarded_high_risk_respiratory_answer", False)) and not bool(
            guard_features.get("guarded_has_confirmed_or_provisional_key_evidence", False)
        ):
            return "missing_confirmed_key_evidence"

        if bool(guard_features.get("guarded_pcp_answer", False)) and not bool(
            guard_features.get("guarded_pcp_combo_satisfied", False)
        ):
            return "pcp_combo_insufficient"

        if bool(guard_features.get("guarded_soft_negative_requires_stability", False)):
            return "soft_negative_needs_stability"

        if bool(guard_features.get("guarded_has_strong_unresolved_alternative", False)):
            return "strong_unresolved_alternative_candidates"

        if bool(guard_features.get("guarded_recent_hypothesis_switch", False)):
            return "recent_hypothesis_switch"

        if bool(guard_features.get("guarded_answer_changed_after_first_accept", False)):
            return "answer_changed_after_first_accept"

        return ""

    # 将 guarded block reason 映射回 repair 分流可理解的三类拒停原因。
    def _map_guarded_block_to_repair_reason(self, block_reason: str) -> str:
        if block_reason in {"hard_negative_key_evidence", "strong_unresolved_alternative_candidates"}:
            return "strong_alternative_not_ruled_out"

        if block_reason in {
            "soft_negative_needs_stability",
            "recent_hypothesis_switch",
            "answer_changed_after_first_accept",
        }:
            return "trajectory_insufficient"

        return "missing_key_support"

    # 记录每轮最佳答案候选，用于识别最近一轮 hypothesis / final answer 是否发生切换。
    def _record_answer_candidate(
        self,
        session_state: SessionState | None,
        answer_score: FinalAnswerScore,
    ) -> None:
        if session_state is None:
            return

        history = self._get_history(session_state, "answer_candidate_history")
        entry = {
            "turn_index": session_state.turn_index,
            "answer_id": answer_score.answer_id,
            "answer_name": answer_score.answer_name,
        }

        if len(history) == 0 or history[-1] != entry:
            history.append(entry)

        session_state.metadata["answer_candidate_history"] = history[-12:]

    # 记录 verifier 曾经愿意接受的候选，即便该候选后来被 stop rule 或 guarded gate 拦下。
    def _record_verifier_accept_candidate(
        self,
        session_state: SessionState | None,
        answer_score: FinalAnswerScore,
    ) -> None:
        if session_state is None:
            return

        if str(answer_score.metadata.get("verifier_mode", "")) != "llm_verifier":
            return

        if not bool(answer_score.metadata.get("verifier_should_accept", False)):
            return

        history = self._get_history(session_state, "verifier_accept_history")
        entry = {
            "turn_index": session_state.turn_index,
            "answer_id": answer_score.answer_id,
            "answer_name": answer_score.answer_name,
            "accept_reason": str(answer_score.metadata.get("verifier_accept_reason", "")),
        }

        if len(history) == 0 or history[-1] != entry:
            history.append(entry)

        session_state.metadata["verifier_accept_history"] = history[-12:]

    # 判断最近一轮最佳答案是否发生切换。
    def _has_recent_hypothesis_switch(self, answer_score: FinalAnswerScore, session_state: SessionState) -> bool:
        previous = self._last_history_before_turn(
            session_state,
            "answer_candidate_history",
            current_turn=session_state.turn_index,
        )

        if previous is None:
            return False

        previous_answer_id = str(previous.get("answer_id") or "")
        return len(previous_answer_id) > 0 and previous_answer_id != answer_score.answer_id

    # 如果 verifier 首次愿意接受的是另一个答案，则要求当前答案至少先稳定通过一轮 verifier。
    def _answer_changed_after_first_accept(
        self,
        answer_score: FinalAnswerScore,
        session_state: SessionState,
    ) -> bool:
        history = self._get_history(session_state, "verifier_accept_history")
        previous_accepts = [
            item
            for item in history
            if int(item.get("turn_index", 0)) < session_state.turn_index
        ]

        if len(previous_accepts) == 0:
            return False

        first_accept = previous_accepts[0]
        first_answer_id = str(first_accept.get("answer_id") or "")

        if len(first_answer_id) == 0 or first_answer_id == answer_score.answer_id:
            return False

        return not any(str(item.get("answer_id") or "") == answer_score.answer_id for item in previous_accepts)

    def _has_prior_verifier_accept_for_answer(
        self,
        answer_score: FinalAnswerScore,
        session_state: SessionState,
    ) -> bool:
        return any(
            str(item.get("answer_id") or "") == answer_score.answer_id
            for item in self._get_history(session_state, "verifier_accept_history")
            if int(item.get("turn_index", 0)) < session_state.turn_index
        )

    # 收集当前答案下已经被 A4 确认为 exist + clear 的定义性关键证据。
    def _collect_confirmed_key_evidence(
        self,
        answer_score: FinalAnswerScore,
        session_state: SessionState,
    ) -> list[dict]:
        values: list[dict] = []

        for evidence in self._iter_guarded_evidence(answer_score, session_state):
            relation_type = str(evidence.metadata.get("relation_type") or "")
            evidence_tags = self._infer_evidence_tags(evidence)

            if relation_type not in GUARDED_DEFINITION_RELATION_TYPES and not (
                evidence_tags & GUARDED_CONFIRMED_EVIDENCE_TAGS
            ):
                continue

            if self._evidence_polarity(evidence) != "present" or evidence.resolution != "clear":
                continue

            values.append(self._compact_evidence(evidence, answer_score=answer_score))

        return values

    # 收集高价值 anchor 的 exist + hedged 证据，作为 guarded 的 provisional family。
    def _collect_provisional_key_evidence(
        self,
        answer_score: FinalAnswerScore,
        session_state: SessionState,
    ) -> list[dict]:
        values: list[dict] = []

        for evidence in self._iter_guarded_evidence(answer_score, session_state):
            if self._evidence_polarity(evidence) != "present" or evidence.resolution != "hedged":
                continue

            if not self._is_provisional_anchor_evidence(evidence):
                continue

            compact = self._compact_evidence(evidence, answer_score=answer_score)
            compact["provisional_reason"] = "exist_hedged_high_value_anchor"
            values.append(compact)

        return values

    # 收集会直接削弱当前答案的 negative / doubtful 关键支持证据。
    def _collect_negative_or_doubtful_key_evidence(
        self,
        answer_score: FinalAnswerScore,
        session_state: SessionState,
    ) -> list[dict]:
        values: list[dict] = []

        for evidence in self._iter_guarded_evidence(answer_score, session_state):
            relation_type = str(evidence.metadata.get("relation_type") or "")
            evidence_tags = self._infer_evidence_tags(evidence)

            if relation_type not in GUARDED_KEY_SUPPORT_RELATION_TYPES and not (
                evidence_tags & GUARDED_KEY_SUPPORT_EVIDENCE_TAGS
            ):
                continue

            polarity = self._evidence_polarity(evidence)

            if polarity == "absent" or polarity == "unclear" or evidence.resolution == "hedged":
                if polarity == "present" and evidence.resolution == "hedged" and self._is_provisional_anchor_evidence(evidence):
                    continue

                compact = self._compact_evidence(evidence, answer_score=answer_score)
                compact.update(self._classify_negative_or_doubtful_evidence(evidence, compact))
                values.append(compact)

        return values

    def _iter_guarded_evidence(
        self,
        answer_score: FinalAnswerScore,
        session_state: SessionState,
    ) -> Iterable[EvidenceState]:
        high_risk_respiratory_answer = self._is_high_risk_respiratory_answer(answer_score)

        for evidence in session_state.evidence_states.values():
            hypothesis_id = str(evidence.metadata.get("hypothesis_id") or "")

            if len(hypothesis_id) == 0 or hypothesis_id == answer_score.answer_id:
                yield evidence
                continue

            if high_risk_respiratory_answer and self._is_shareable_guarded_evidence(evidence, answer_score):
                yield evidence

    def _is_shareable_guarded_evidence(
        self,
        evidence: EvidenceState,
        answer_score: FinalAnswerScore,
    ) -> bool:
        evidence_tags = self._infer_evidence_tags(evidence)

        if len(evidence_tags & GUARDED_SHAREABLE_RESPIRATORY_EVIDENCE_TAGS) > 0:
            return True

        return self._is_pcp_answer(answer_score) and "pcp_specific" in evidence_tags

    def _compact_evidence(self, evidence: EvidenceState, answer_score: FinalAnswerScore | None = None) -> dict:
        hypothesis_id = str(evidence.metadata.get("hypothesis_id") or "")
        evidence_scope = "unscoped"

        if answer_score is None:
            evidence_scope = "unscoped" if len(hypothesis_id) == 0 else "hypothesis_scoped"
        elif len(hypothesis_id) == 0:
            evidence_scope = "unscoped"
        elif hypothesis_id == answer_score.answer_id:
            evidence_scope = "answer_scoped"
        else:
            evidence_scope = "shared_clinical"

        return {
            "node_id": evidence.node_id,
            "name": str(evidence.metadata.get("target_node_name") or evidence.node_id),
            "polarity": self._evidence_polarity(evidence),
            "existence": evidence.existence,
            "resolution": evidence.resolution,
            "relation_type": str(evidence.metadata.get("relation_type") or ""),
            "evidence_tags": sorted(self._infer_evidence_tags(evidence)),
            "evidence_families": sorted(
                tag for tag in self._infer_evidence_tags(evidence) if not tag.startswith("type:")
            ),
            "hypothesis_id": hypothesis_id,
            "evidence_scope": evidence_scope,
            "source_turns": list(evidence.source_turns),
        }

    def _classify_negative_or_doubtful_evidence(self, evidence: EvidenceState, compact: dict) -> dict:
        evidence_families = {
            str(item)
            for item in compact.get("evidence_families", [])
            if len(str(item)) > 0
        }
        evidence_scope = str(compact.get("evidence_scope") or "")
        relation_type = str(compact.get("relation_type") or "")
        is_clear_absence = self._evidence_polarity(evidence) == "absent" and evidence.resolution == "clear"
        is_definition_like = relation_type in GUARDED_DEFINITION_RELATION_TYPES or len(
            evidence_families & GUARDED_HARD_NEGATIVE_EVIDENCE_TAGS
        ) > 0
        is_answer_scoped = evidence_scope in {"answer_scoped", "unscoped"}

        # “当前答案自己的定义性证据被明确否定”才算 hard negative；
        # 其余共享证据、模糊证据都按 soft 处理，要求更多稳定性但不直接一票否决。
        if is_clear_absence and is_definition_like and is_answer_scoped:
            return {
                "negative_evidence_tier": "hard",
                "negative_evidence_reason": "clear_absence_of_answer_scoped_definition_evidence",
            }

        return {
            "negative_evidence_tier": "soft",
            "negative_evidence_reason": "uncertain_or_shared_or_non_core_evidence",
        }

    def _collect_recent_key_evidence_states(
        self,
        answer_score: FinalAnswerScore,
        session_state: SessionState,
        limit: int = 8,
    ) -> list[dict]:
        values: list[dict] = []

        for evidence in self._iter_guarded_evidence(answer_score, session_state):
            relation_type = str(evidence.metadata.get("relation_type") or "")
            evidence_tags = self._infer_evidence_tags(evidence)

            if relation_type not in GUARDED_KEY_SUPPORT_RELATION_TYPES and not (
                evidence_tags & GUARDED_KEY_SUPPORT_EVIDENCE_TAGS
            ):
                continue

            values.append(self._compact_evidence(evidence, answer_score=answer_score))

        return sorted(
            values,
            key=lambda item: (
                -max([int(turn) for turn in item.get("source_turns", [])] or [0]),
                str(item.get("name") or ""),
            ),
        )[:limit]

    def _evidence_polarity(self, evidence: EvidenceState) -> str:
        return evidence.effective_polarity()

    def _collect_evidence_families(self, compact_evidence: list[dict]) -> set[str]:
        families: set[str] = set()

        for item in compact_evidence:
            tags = item.get("evidence_tags", [])

            if not isinstance(tags, list):
                continue

            families.update(str(tag) for tag in tags if not str(tag).startswith("type:"))

        return families

    def _is_high_risk_respiratory_answer(self, answer_score: FinalAnswerScore) -> bool:
        normalized = self._normalize_text(f"{answer_score.answer_name} {answer_score.answer_id}")
        return any(keyword in normalized for keyword in GUARDED_HIGH_RISK_RESPIRATORY_KEYWORDS)

    def _is_pcp_answer(self, answer_score: FinalAnswerScore) -> bool:
        normalized = self._normalize_text(f"{answer_score.answer_name} {answer_score.answer_id}")
        return any(keyword in normalized for keyword in ("肺孢子", "pcp", "pneumocystis"))

    def _is_pcp_combo_satisfied(self, evidence_families: set[str]) -> bool:
        return bool(self._evaluate_pcp_combo(evidence_families).get("satisfied", False))

    def _evaluate_pcp_combo(self, evidence_families: set[str]) -> dict:
        # PCP 等高风险呼吸道答案不靠单一 family 放行，
        # 而是要求若干关键 family 组合之一被满足。
        combo_variants = [
            ("imaging_immune_status_lab", [{"imaging"}, {"immune_status"}]),
            ("imaging_pathogen_or_pcp_specific", [{"imaging"}, {"pathogen", "pcp_specific"}]),
            ("imaging_oxygenation_immune_status", [{"imaging"}, {"oxygenation"}, {"immune_status"}]),
            ("imaging_respiratory_immune_status", [{"imaging"}, {"respiratory"}, {"immune_status"}]),
        ]
        missing_options: list[list[str]] = []

        for variant_name, required_groups in combo_variants:
            missing_for_variant: list[str] = []

            for group in required_groups:
                if len(evidence_families & group) == 0:
                    missing_for_variant.extend(sorted(group))

            if len(missing_for_variant) == 0:
                return {
                    "satisfied": True,
                    "variant": variant_name,
                    "missing_family_options": [],
                    "preferred_missing_families": [],
                }

            missing_options.append(self._sort_missing_families(missing_for_variant))

        return {
            "satisfied": False,
            "variant": "",
            "missing_family_options": missing_options,
            "preferred_missing_families": self._preferred_missing_families(missing_options),
        }

    def _select_guarded_missing_evidence_families(
        self,
        evidence_families: set[str],
        *,
        pcp_combo: dict,
        pcp_answer: bool,
        high_risk_respiratory_answer: bool,
    ) -> list[str]:
        if pcp_answer and not bool(pcp_combo.get("satisfied", False)):
            return self._sort_missing_families(pcp_combo.get("preferred_missing_families", []))

        if high_risk_respiratory_answer and len(evidence_families) == 0:
            return ["imaging", "oxygenation", "pathogen", "immune_status"]

        return []

    def _preferred_missing_families(self, missing_options: list[list[str]]) -> list[str]:
        single_step_families: list[str] = []

        for option in missing_options:
            if len(option) != 1 and set(option) != {"pathogen", "pcp_specific"}:
                continue

            for family in option:
                if family not in single_step_families:
                    single_step_families.append(family)

        if len(single_step_families) > 0:
            return self._sort_missing_families(single_step_families)

        flattened: list[str] = []

        for option in missing_options:
            for family in option:
                if family not in flattened:
                    flattened.append(family)

        return self._sort_missing_families(flattened)

    def _sort_missing_families(self, families: Iterable[str]) -> list[str]:
        unique = {str(item) for item in families if len(str(item)) > 0}
        order = {family: index for index, family in enumerate(GUARDED_PCP_PRIMARY_MISSING_FAMILY_ORDER)}
        return sorted(unique, key=lambda item: (order.get(item, len(order)), item))

    def _build_guarded_gate_audit_entry(
        self,
        answer_score: FinalAnswerScore,
        session_state: SessionState,
        *,
        block_reason: str,
        guard_features: dict,
    ) -> dict:
        # audit entry 只保留“为什么被挡下”的核心证据，不复制整个 session_state。
        return {
            "turn_index": session_state.turn_index,
            "block_reason": block_reason,
            "current_answer_id": answer_score.answer_id,
            "current_answer_name": answer_score.answer_name,
            "confirmed_evidence_families": guard_features.get("guarded_confirmed_key_evidence_families", []),
            "provisional_evidence_families": guard_features.get("guarded_provisional_key_evidence_families", []),
            "combined_evidence_families": guard_features.get("guarded_combined_key_evidence_families", []),
            "missing_families": guard_features.get("guarded_missing_evidence_families", []),
            "pcp_combo_uses_provisional": guard_features.get("guarded_pcp_combo_uses_provisional", False),
            "pcp_combo_variant": guard_features.get("guarded_pcp_combo_variant", ""),
            "pcp_combo_missing_family_options": guard_features.get(
                "guarded_pcp_combo_missing_family_options",
                [],
            ),
            "hard_negative_key_evidence": guard_features.get("guarded_hard_negative_key_evidence", []),
            "soft_negative_or_doubtful_key_evidence": guard_features.get(
                "guarded_soft_negative_or_doubtful_key_evidence",
                [],
            ),
            "soft_negative_requires_stability": guard_features.get(
                "guarded_soft_negative_requires_stability",
                False,
            ),
            "alternative_candidates": self._normalize_alternative_candidates(
                answer_score.metadata.get("verifier_alternative_candidates", [])
            ),
            "strong_alternative_candidates": guard_features.get("guarded_strong_alternative_candidates", []),
            "weak_or_ruled_down_alternative_candidates": guard_features.get(
                "guarded_weak_or_ruled_down_alternative_candidates",
                [],
            ),
            "recent_key_evidence_states": guard_features.get("guarded_recent_key_evidence_states", []),
        }

    def _record_guarded_gate_audit(self, session_state: SessionState, audit_entry: dict) -> None:
        history = self._get_history(session_state, "guarded_gate_audit_history")
        history.append(dict(audit_entry))
        session_state.metadata["guarded_gate_audit_history"] = history[-24:]

    def _is_provisional_anchor_evidence(self, evidence: EvidenceState) -> bool:
        families = self._anchor_evidence_families(evidence)

        if len(families & GUARDED_PROVISIONAL_ANCHOR_FAMILIES) == 0:
            return False

        evidence_tags = self._infer_evidence_tags(evidence)
        return len(evidence_tags & GUARDED_PROVISIONAL_ANCHOR_FAMILIES) > 0

    def _infer_evidence_tags(self, evidence: EvidenceState) -> set[str]:
        raw_tags = evidence.metadata.get("evidence_tags", [])
        tags: set[str] = set()

        # 先复用 action_builder / service 已写入的 evidence_tags，
        # 再从 node_id / name / label / relation_type 做一次文本侧 family 补推断。
        if isinstance(raw_tags, list):
            tags.update(str(tag).strip() for tag in raw_tags if len(str(tag).strip()) > 0)

        text = self._normalize_text(
            " ".join(
                [
                    evidence.node_id,
                    str(evidence.metadata.get("target_node_name") or ""),
                    str(evidence.metadata.get("target_node_label") or ""),
                    str(evidence.metadata.get("relation_type") or ""),
                ]
            )
        )
        tag_rules = {
            "immune_status": ("hiv", "cd4", "t淋巴", "免疫", "艾滋", "机会性感染", "免疫抑制"),
            "imaging": ("ct", "影像", "x线", "胸片", "磨玻璃", "双肺"),
            "oxygenation": ("低氧", "血氧", "pao2", "氧分压", "肺泡", "氧合", "呼吸衰竭"),
            "respiratory": ("发热", "干咳", "咳嗽", "呼吸困难", "气促"),
            "pathogen": ("βd葡聚糖", "bdg", "病原", "痰", "balf", "病原学", "pcr", "核酸"),
            "pcp_specific": ("肺孢子", "pneumocystis", "pcp", "βd葡聚糖", "bdg"),
            "tuberculosis": ("结核", "盗汗", "抗酸", "分枝杆菌", "tb", "tspot", "t-spot"),
            "risk": ("高危", "性行为", "接触史", "暴露"),
        }

        for tag, keywords in tag_rules.items():
            if any(keyword in text for keyword in keywords):
                tags.add(tag)

        # 对少数高价值 anchor，再用 allowlist 强制提升到更稳定的 family，
        # 同时清掉可能串线的旧 family 标签。
        anchor_families = self._anchor_families_from_text(text)

        if len(anchor_families) > 0:
            type_tags = {tag for tag in tags if tag.startswith("type:")}
            non_family_tags = {
                tag
                for tag in tags
                if not tag.startswith("type:") and tag not in GUARDED_EVIDENCE_FAMILY_TAGS
            }
            tags = type_tags | non_family_tags | anchor_families

        return tags

    # 高价值医学 anchor 使用 allowlist 做 family promotion，并清掉明显串线的旧 family 标签。
    def _anchor_evidence_families(self, evidence: EvidenceState) -> set[str]:
        text = self._normalize_text(
            " ".join(
                [
                    evidence.node_id,
                    str(evidence.metadata.get("target_node_name") or ""),
                    str(evidence.metadata.get("target_node_label") or ""),
                ]
            )
        )
        return self._anchor_families_from_text(text)

    def _anchor_families_from_text(self, normalized_text: str) -> set[str]:
        anchor_rules: list[tuple[set[str], tuple[str, ...]]] = [
            ({"immune_status"}, ("cd4", "t淋巴", "hiv感染", "艾滋", "免疫抑制")),
            ({"imaging"}, ("胸部ct", "ct检查", "ct磨玻璃", "磨玻璃", "胸片", "影像")),
            ({"oxygenation"}, ("pao2", "spo2", "氧分压", "低氧", "氧合", "呼吸衰竭")),
            ({"pathogen", "pcp_specific"}, ("βd葡聚糖", "bdg", "葡聚糖", "g试验")),
            ({"pathogen", "pcp_specific"}, ("肺孢子pcr", "pcppcr", "肺孢子核酸", "支气管肺泡", "balf", "bal")),
            ({"tuberculosis"}, ("tspot", "tspot.tb", "t-spot", "xpert", "mtb/rif", "抗酸", "分枝杆菌")),
        ]

        for families, keywords in anchor_rules:
            if any(keyword in normalized_text for keyword in keywords):
                return set(families)

        return set()

    def _normalize_text(self, text: str) -> str:
        return (
            text.strip()
            .lower()
            .replace(" ", "")
            .replace("（", "(")
            .replace("）", ")")
            .replace("，", ",")
            .replace("。", "")
            .replace("、", "")
            .replace("-", "")
            .replace("_", "")
            .replace("/", "")
        )

    def _normalize_alternative_candidates(self, payload: object) -> list[dict]:
        if not isinstance(payload, list):
            return []

        normalized: list[dict] = []

        # verifier 可能返回结构化 dict，也可能只给字符串；
        # 这里统一收敛成 answer_id / answer_name / reason 结构。
        for item in payload:
            if isinstance(item, dict):
                answer_id = str(item.get("answer_id") or item.get("node_id") or "").strip()
                answer_name = str(item.get("answer_name") or item.get("name") or "").strip()

                if len(answer_id) == 0 and len(answer_name) == 0:
                    continue

                normalized.append(
                    {
                        "answer_id": answer_id or None,
                        "answer_name": answer_name or answer_id,
                        "reason": str(item.get("reason", "")).strip(),
                        **self._classify_alternative_candidate(item),
                    }
                )

        return normalized

    # 将 verifier 给出的备选诊断从“非空列表”拆成强竞争者与已弱化候选。
    def _classify_alternative_candidate(self, item: dict) -> dict:
        raw_strength = str(
            item.get("strength")
            or item.get("confidence")
            or item.get("competition_strength")
            or item.get("risk_level")
            or ""
        ).strip().lower()
        reason = str(item.get("reason", "")).strip()
        normalized_reason = self._normalize_text(reason)

        # 优先吃显式 strength 字段；只有字段缺失时才从 reason 里推断强弱。
        if raw_strength in {"strong", "high", "unresolved", "强", "高", "强竞争"}:
            return {
                "strength": "strong",
                "is_unresolved_strong": True,
                "strength_reason": "explicit_strong",
            }

        if raw_strength in {"weak", "low", "ruled_down", "ruled-out", "弱", "低", "已排除"}:
            return {
                "strength": "weak",
                "is_unresolved_strong": False,
                "strength_reason": "explicit_weak",
            }

        strong_markers = (
            "未排除",
            "不能排除",
            "尚未排除",
            "需要排除",
            "强支持",
            "证据支持",
            "同样支持",
            "更符合",
            "高度符合",
            "主要竞争",
            "强竞争",
            "关键证据支持",
            "高度怀疑",
        )
        weak_markers = (
            "缺乏",
            "证据不足",
            "不支持",
            "不如",
            "可能性低",
            "可能性较低",
            "较不符合",
            "较弱",
            "未见",
            "没有关键证据",
            "无法解释",
            "不典型",
            "仅作为",
            "一般候选",
            "低于当前诊断",
        )

        if any(marker in normalized_reason for marker in strong_markers):
            return {
                "strength": "strong",
                "is_unresolved_strong": True,
                "strength_reason": "reason_contains_strong_unresolved_signal",
            }

        if any(marker in normalized_reason for marker in weak_markers):
            return {
                "strength": "weak",
                "is_unresolved_strong": False,
                "strength_reason": "reason_indicates_ruled_down_or_weak_candidate",
            }

        if len(normalized_reason) == 0:
            # 没有 reason 时采取保守策略，宁可把它当未排除强竞争者。
            return {
                "strength": "strong",
                "is_unresolved_strong": True,
                "strength_reason": "missing_reason_treated_as_unresolved",
            }

        return {
            "strength": "medium",
            "is_unresolved_strong": False,
            "strength_reason": "no_strong_unresolved_signal",
        }

    def _get_history(self, session_state: SessionState, key: str) -> list[dict]:
        history = session_state.metadata.get(key, [])

        if not isinstance(history, list):
            return []

        return [item for item in history if isinstance(item, dict)]

    def _last_history_before_turn(
        self,
        session_state: SessionState,
        key: str,
        current_turn: int,
    ) -> dict | None:
        history = [
            item
            for item in self._get_history(session_state, key)
            if int(item.get("turn_index", 0)) < current_turn
        ]

        if len(history) == 0:
            return None

        return history[-1]
