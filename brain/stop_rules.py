"""负责判断问诊是否可以停止，或是否需要继续补充证据。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable

from .types import FinalAnswerScore, HypothesisScore, SessionState, StopDecision


ANCHOR_CONTROLLED_PROFILES = {"anchor_controlled", "anchor-controlled", "anchor"}
ACCEPTABLE_OBSERVED_ANCHOR_TIERS = {"strong_anchor", "definition_anchor", "provisional_anchor"}
STRONG_OBSERVED_ANCHOR_TIERS = {"strong_anchor", "definition_anchor"}
HARD_VERIFIER_REJECT_REASONS = {
    "hard_negative_key_evidence",
    "strong_alternative_not_ruled_out",
    "anchored_alternative_exists",
    "clear_negative_definition_evidence",
    "major_contradiction",
    "contradiction",
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
    min_strong_anchor_trajectory_count_before_accept: int = 1
    require_verifier_accept_flag: bool = True
    acceptance_profile: str = "baseline"
    enable_evidence_profile_acceptance: bool = False
    min_low_cost_profile_families: int = 2
    min_low_cost_profile_present_clear_count: int = 2
    min_low_cost_profile_stable_top_count: int = 2
    allow_soft_verifier_reject_with_evidence_profile: bool = True


class StopRuleEngine:
    """根据候选假设分数与真实锚点状态做终止判断。"""

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

        acceptance_profile = self._acceptance_profile()
        self._record_answer_candidate(session_state, answer_score)
        self._record_verifier_accept_candidate(session_state, answer_score)
        answer_score.metadata["acceptance_profile"] = acceptance_profile

        if session_state is not None and session_state.turn_index < self.config.min_turn_index_before_final_answer:
            return StopDecision(False, "turn_index_too_low", float(session_state.turn_index))

        anchor_decision = self._check_anchor_controlled_acceptance(answer_score, session_state, acceptance_profile)

        verifier_mode = str(answer_score.metadata.get("verifier_mode", ""))
        if (
            self.config.require_verifier_accept_flag
            and verifier_mode == "llm_verifier"
            and not bool(answer_score.metadata.get("verifier_should_accept", False))
        ):
            if not self._can_override_soft_verifier_reject(answer_score, anchor_decision):
                return StopDecision(False, "verifier_rejected_stop", answer_score.agent_evaluation)
            answer_score.metadata["verifier_soft_reject_overridden_by_evidence_profile"] = True

        if anchor_decision is not None and not anchor_decision.should_stop:
            return anchor_decision

        trajectory_count = int(answer_score.metadata.get("trajectory_count", 0))
        required_trajectory_count = self._required_trajectory_count(answer_score)
        if trajectory_count < required_trajectory_count:
            return StopDecision(False, "trajectory_count_too_low", float(trajectory_count))

        if anchor_decision is not None and anchor_decision.should_stop:
            # anchor gate 只负责通用结构约束；真正接受仍要通过基础分数阈值。
            pass

        if answer_score.consistency < self.config.min_answer_consistency:
            return StopDecision(False, "consistency_too_low", answer_score.consistency)

        if answer_score.agent_evaluation < self.config.min_agent_eval_score:
            return StopDecision(False, "agent_eval_too_low", answer_score.agent_evaluation)

        if answer_score.final_score < self.config.min_final_score:
            return StopDecision(False, "final_score_too_low", answer_score.final_score)

        return StopDecision(True, "final_answer_accepted", answer_score.final_score)

    # 读取结构化 stop gate 的 acceptance profile；verifier prompt 另走 TRAJECTORY_* 配置。
    def _acceptance_profile(self) -> str:
        configured_profile = (self.config.acceptance_profile or "").strip().lower()

        if configured_profile and configured_profile != "baseline":
            return configured_profile

        return (os.getenv("BRAIN_ACCEPTANCE_PROFILE") or configured_profile or "baseline").strip().lower()

    # 强 observed anchor 可以使用更低 trajectory 门槛；背景证据不能放宽。
    def _required_trajectory_count(self, answer_score: FinalAnswerScore) -> int:
        anchor_tier = str(answer_score.metadata.get("anchor_tier") or "")
        if anchor_tier in STRONG_OBSERVED_ANCHOR_TIERS:
            return max(0, int(self.config.min_strong_anchor_trajectory_count_before_accept))
        return max(0, int(self.config.min_trajectory_count_before_accept))

    # anchor_controlled profile 只做通用锚点约束，不再编码疾病证据合同。
    def _check_anchor_controlled_acceptance(
        self,
        answer_score: FinalAnswerScore,
        session_state: SessionState | None,
        acceptance_profile: str,
    ) -> StopDecision | None:
        if acceptance_profile not in ANCHOR_CONTROLLED_PROFILES:
            return None

        if session_state is None:
            return None

        answer_features = self._anchor_features_for_answer(answer_score, session_state)
        answer_score.metadata.update(
            {
                "anchor_controlled_applied": True,
                "observed_anchor_score": answer_features.get("observed_anchor_score", 0.0),
                "anchor_tier": answer_features.get("anchor_tier", "speculative"),
                "anchor_supporting_evidence": answer_features.get("anchor_supporting_evidence", []),
                "provisional_anchor_evidence": answer_features.get("provisional_anchor_evidence", []),
                "background_support_score": answer_features.get("background_support_score", 0.0),
                "anchor_negative_evidence": answer_features.get("anchor_negative_evidence", []),
                "missing_evidence_roles": answer_features.get("missing_evidence_roles", []),
                "low_cost_supporting_evidence": answer_features.get("low_cost_supporting_evidence", []),
                "low_cost_support_families": answer_features.get("low_cost_support_families", []),
                "low_cost_core_family_count": answer_features.get("low_cost_core_family_count", 0),
                "low_cost_present_clear_count": answer_features.get("low_cost_present_clear_count", 0),
                "low_cost_profile_satisfied": answer_features.get("low_cost_profile_satisfied", False),
                "evidence_profile_acceptance_candidate": answer_features.get(
                    "evidence_profile_acceptance_candidate", False
                ),
                "evidence_profile_acceptance_reason": answer_features.get("evidence_profile_acceptance_reason", ""),
            }
        )

        stronger_alternatives = self._stronger_anchor_alternatives(answer_score, session_state, answer_features)
        block_reason = self._select_anchor_controlled_block_reason(answer_features, stronger_alternatives)
        if str(answer_features.get("evidence_profile_acceptance_reason") or ""):
            answer_score.metadata["evidence_profile_acceptance_reason"] = answer_features[
                "evidence_profile_acceptance_reason"
            ]

        if len(block_reason) == 0:
            session_state.metadata["last_anchor_controlled_decision"] = {
                "should_accept": True,
                "reason": "",
                "answer_id": answer_score.answer_id,
                "answer_name": answer_score.answer_name,
                "turn_index": session_state.turn_index,
                "anchor_answer_features": answer_features,
            }
            return StopDecision(True, "anchor_controlled_ok", answer_score.agent_evaluation)

        repair_reason = self._map_anchor_block_to_repair_reason(block_reason)
        metadata = {
            "acceptance_profile": acceptance_profile,
            "anchor_controlled_block_reason": block_reason,
            "path_control_reason": repair_reason,
            "repair_reject_reason": repair_reason,
            "anchor_answer_features": answer_features,
            "anchor_stronger_alternative_candidates": stronger_alternatives,
            "observed_anchor_index": session_state.metadata.get("observed_anchor_index", {}),
        }
        answer_score.metadata["anchor_controlled_block_reason"] = block_reason
        session_state.metadata["last_anchor_controlled_decision"] = {
            "should_accept": False,
            "reason": block_reason,
            "answer_id": answer_score.answer_id,
            "answer_name": answer_score.answer_name,
            "turn_index": session_state.turn_index,
            **metadata,
        }
        return StopDecision(False, "anchor_controlled_rejected", answer_score.agent_evaluation, metadata)

    def _anchor_features_for_answer(
        self,
        answer_score: FinalAnswerScore,
        session_state: SessionState,
    ) -> dict:
        hypothesis = self._find_hypothesis(session_state.candidate_hypotheses, answer_score.answer_id)
        metadata = dict(hypothesis.metadata) if hypothesis is not None else {}

        if len(metadata) == 0:
            metadata = dict(answer_score.metadata)

        return {
            "answer_id": answer_score.answer_id,
            "answer_name": answer_score.answer_name,
            "anchor_tier": str(metadata.get("anchor_tier") or "speculative"),
            "observed_anchor_score": float(metadata.get("observed_anchor_score", 0.0) or 0.0),
            "strong_anchor_score": float(metadata.get("strong_anchor_score", 0.0) or 0.0),
            "provisional_anchor_score": float(metadata.get("provisional_anchor_score", 0.0) or 0.0),
            "background_support_score": float(metadata.get("background_support_score", 0.0) or 0.0),
            "anchor_supporting_evidence": self._as_list(metadata.get("anchor_supporting_evidence", [])),
            "provisional_anchor_evidence": self._as_list(metadata.get("provisional_anchor_evidence", [])),
            "background_supporting_evidence": self._as_list(metadata.get("background_supporting_evidence", [])),
            "anchor_negative_evidence": self._as_list(metadata.get("anchor_negative_evidence", [])),
            "missing_evidence_roles": self._as_list(metadata.get("missing_evidence_roles", [])),
            "low_cost_supporting_evidence": self._as_list(metadata.get("low_cost_supporting_evidence", [])),
            "low_cost_support_families": self._as_list(metadata.get("low_cost_support_families", [])),
            "low_cost_core_family_count": int(metadata.get("low_cost_core_family_count", 0) or 0),
            "low_cost_present_clear_count": int(metadata.get("low_cost_present_clear_count", 0) or 0),
            "low_cost_profile_satisfied": bool(metadata.get("low_cost_profile_satisfied", False)),
            "evidence_profile_acceptance_candidate": bool(
                metadata.get("evidence_profile_acceptance_candidate", False)
            ),
            "answer_stability_count": self._answer_stability_count(session_state, answer_score),
        }

    def _stronger_anchor_alternatives(
        self,
        answer_score: FinalAnswerScore,
        session_state: SessionState,
        answer_features: dict,
    ) -> list[dict]:
        answer_anchor_score = float(answer_features.get("observed_anchor_score", 0.0) or 0.0)
        values: list[dict] = []

        for hypothesis in session_state.candidate_hypotheses:
            if hypothesis.node_id == answer_score.answer_id:
                continue

            metadata = dict(hypothesis.metadata)
            tier = str(metadata.get("anchor_tier") or "")
            observed_anchor_score = float(metadata.get("observed_anchor_score", 0.0) or 0.0)

            if tier not in STRONG_OBSERVED_ANCHOR_TIERS or observed_anchor_score <= answer_anchor_score + 1e-6:
                continue

            values.append(
                {
                    "answer_id": hypothesis.node_id,
                    "answer_name": hypothesis.name,
                    "reason": "真实会话中存在更强 observed anchor。",
                    "strength": "strong",
                    "is_unresolved_strong": True,
                    "observed_anchor_score": observed_anchor_score,
                    "anchor_tier": tier,
                    "anchor_supporting_evidence": metadata.get("anchor_supporting_evidence", []),
                }
            )

        return sorted(values, key=lambda item: (-float(item.get("observed_anchor_score", 0.0)), str(item.get("answer_name"))))

    def _select_anchor_controlled_block_reason(
        self,
        answer_features: dict,
        stronger_alternatives: list[dict],
    ) -> str:
        if len(answer_features.get("anchor_negative_evidence", []) or []) > 0:
            return "clear_negative_definition_evidence"

        if len(stronger_alternatives) > 0:
            return "anchored_alternative_exists"

        anchor_tier = str(answer_features.get("anchor_tier") or "")
        observed_anchor_score = float(answer_features.get("observed_anchor_score", 0.0) or 0.0)
        if anchor_tier not in ACCEPTABLE_OBSERVED_ANCHOR_TIERS or observed_anchor_score <= 0.0:
            if self._evidence_profile_acceptance_ok(answer_features):
                answer_features["evidence_profile_acceptance_reason"] = "low_cost_multifamily_stable_top"
                return ""
            return "missing_required_anchor"

        return ""

    def _evidence_profile_acceptance_ok(self, answer_features: dict) -> bool:
        if not bool(self.config.enable_evidence_profile_acceptance):
            return False

        if not bool(answer_features.get("evidence_profile_acceptance_candidate", False)):
            return False

        if not bool(answer_features.get("low_cost_profile_satisfied", False)):
            return False

        if int(answer_features.get("low_cost_core_family_count", 0) or 0) < int(
            self.config.min_low_cost_profile_families
        ):
            return False

        if int(answer_features.get("low_cost_present_clear_count", 0) or 0) < int(
            self.config.min_low_cost_profile_present_clear_count
        ):
            return False

        if int(answer_features.get("answer_stability_count", 0) or 0) < int(
            self.config.min_low_cost_profile_stable_top_count
        ):
            return False

        return True

    def _can_override_soft_verifier_reject(
        self,
        answer_score: FinalAnswerScore,
        anchor_decision: StopDecision | None,
    ) -> bool:
        if not bool(self.config.allow_soft_verifier_reject_with_evidence_profile):
            return False

        reject_reason = str(answer_score.metadata.get("verifier_reject_reason") or "").strip()
        if reject_reason in HARD_VERIFIER_REJECT_REASONS:
            return False

        return anchor_decision is not None and anchor_decision.should_stop

    def _map_anchor_block_to_repair_reason(self, block_reason: str) -> str:
        if block_reason == "anchored_alternative_exists":
            return "anchored_alternative_exists"

        if block_reason == "clear_negative_definition_evidence":
            return "hard_negative_key_evidence"

        return "missing_required_anchor"

    # 记录每轮最佳答案候选，用于识别 hypothesis / final answer 是否稳定。
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

    # 记录 verifier 曾经愿意接受的候选，供复盘和 repair 观察。
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

    def _answer_stability_count(self, session_state: SessionState, answer_score: FinalAnswerScore) -> int:
        history = self._get_history(session_state, "answer_candidate_history")
        count = 0

        for item in reversed(history):
            if str(item.get("answer_id") or "") != answer_score.answer_id:
                break
            count += 1

        return count

    def _find_hypothesis(
        self,
        hypotheses: Iterable[HypothesisScore],
        answer_id: str,
    ) -> HypothesisScore | None:
        for hypothesis in hypotheses:
            if hypothesis.node_id == answer_id:
                return hypothesis
        return None

    def _get_history(self, session_state: SessionState, key: str) -> list[dict]:
        value = session_state.metadata.get(key)
        return list(value) if isinstance(value, list) else []

    def _as_list(self, value: object) -> list:
        return value if isinstance(value, list) else []
