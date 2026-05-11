"""负责为 MCTS rollout 估计回答分支转移概率。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .types import HypothesisCandidate, HypothesisScore, MctsAction, PatientContext, SessionState


@dataclass
class TransitionBranch:
    """描述一个候选回答分支及其条件概率。"""

    branch_name: str
    probability: float
    polarity: str
    resolution: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResponseTransitionModelConfig:
    """保存启发式 transition model 的基础超参数。"""

    model_type: str = "heuristic"
    base_positive_probability: float = 0.6
    base_doubtful_probability: float = 0.15
    red_flag_positive_bonus: float = 0.1
    asked_before_positive_penalty: float = 0.15
    detail_positive_penalty: float = 0.1
    strong_relation_positive_bonus: float = 0.05


class ResponseTransitionModel:
    """定义 rollout 中条件化回答分支分布的统一接口。"""

    def predict_branches(
        self,
        action: MctsAction,
        session_state: SessionState,
        *,
        primary_hypothesis: HypothesisCandidate | HypothesisScore | None = None,
        candidate_hypotheses: Sequence[HypothesisScore] | None = None,
        patient_context: PatientContext | None = None,
    ) -> list[TransitionBranch]:
        raise NotImplementedError


class HeuristicResponseTransitionModel(ResponseTransitionModel):
    """复用当前启发式规则生成回答分支概率分布。"""

    def __init__(self, config: ResponseTransitionModelConfig | None = None) -> None:
        self.config = config or ResponseTransitionModelConfig()

    # 基于动作元数据、历史追问痕迹和关系类型估计三分支概率。
    def predict_branches(
        self,
        action: MctsAction,
        session_state: SessionState,
        *,
        primary_hypothesis: HypothesisCandidate | HypothesisScore | None = None,
        candidate_hypotheses: Sequence[HypothesisScore] | None = None,
        patient_context: PatientContext | None = None,
    ) -> list[TransitionBranch]:
        _ = primary_hypothesis, candidate_hypotheses, patient_context
        positive_probability = self._estimate_positive_probability(action, session_state)
        doubtful_probability = min(
            self.config.base_doubtful_probability,
            max(0.0, 1.0 - positive_probability),
        )
        negative_probability = max(0.05, 1.0 - positive_probability - doubtful_probability)

        branches = [
            TransitionBranch(
                branch_name="positive",
                probability=positive_probability,
                polarity="present",
                resolution="clear",
                metadata={"confidence_mode": "supportive"},
            ),
            TransitionBranch(
                branch_name="negative",
                probability=negative_probability,
                polarity="absent",
                resolution="clear",
                metadata={"confidence_mode": "contradictory"},
            ),
            TransitionBranch(
                branch_name="doubtful",
                probability=doubtful_probability,
                polarity="unclear",
                resolution="hedged",
                metadata={"confidence_mode": "uncertain"},
            ),
        ]
        return self._normalize_probabilities(branches)

    # 当前默认启发式仍保留“红旗更可能阳性、重复问法更不容易拿到新阳性”的判断。
    def _estimate_positive_probability(
        self,
        action: MctsAction,
        session_state: SessionState,
    ) -> float:
        probability = self.config.base_positive_probability

        if bool(action.metadata.get("is_red_flag", False)):
            probability += self.config.red_flag_positive_bonus

        if action.target_node_id in session_state.asked_node_ids:
            probability -= self.config.asked_before_positive_penalty

        relation_type = str(action.metadata.get("relation_type", ""))

        if relation_type == "REQUIRES_DETAIL":
            probability -= self.config.detail_positive_penalty
        elif relation_type in {"HAS_LAB_FINDING", "DIAGNOSED_BY"}:
            probability += self.config.strong_relation_positive_bonus

        return min(max(probability, 0.1), 0.9)

    # 统一归一化，保证外部拿到的是合法概率分布。
    def _normalize_probabilities(self, branches: Sequence[TransitionBranch]) -> list[TransitionBranch]:
        total_probability = sum(max(float(item.probability), 0.0) for item in branches)

        if total_probability <= 0.0:
            uniform_probability = 1.0 / max(len(branches), 1)
            return [
                TransitionBranch(
                    branch_name=item.branch_name,
                    probability=uniform_probability,
                    polarity=item.polarity,
                    resolution=item.resolution,
                    metadata=dict(item.metadata),
                )
                for item in branches
            ]

        return [
            TransitionBranch(
                branch_name=item.branch_name,
                probability=max(float(item.probability), 0.0) / total_probability,
                polarity=item.polarity,
                resolution=item.resolution,
                metadata=dict(item.metadata),
            )
            for item in branches
        ]
