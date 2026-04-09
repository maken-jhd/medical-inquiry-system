"""负责将 R2 检索结果构造成可供 UCT 选择的候选动作。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from .types import A3VerificationResult, HypothesisScore, MctsAction, QuestionCandidate


@dataclass
class ActionBuilderConfig:
    """保存动作构造阶段的默认参数。"""

    default_action_type: str = "verify_evidence"
    red_flag_bonus: float = 1.5


class ActionBuilder:
    """把图谱返回的验证候选节点转为可搜索动作。"""

    # 初始化动作构造器配置。
    def __init__(self, config: ActionBuilderConfig | None = None) -> None:
        self.config = config or ActionBuilderConfig()

    # 根据 R2 返回的候选节点构造动作集合。
    def build_verification_actions(
        self,
        rows: Iterable[dict],
        hypothesis_id: str,
        topic_id: Optional[str] = None,
        competing_hypotheses: Sequence[HypothesisScore] | None = None,
        current_hypothesis: HypothesisScore | None = None,
    ) -> List[MctsAction]:
        actions: List[MctsAction] = []
        alternatives = list(competing_hypotheses or [])
        preferred_evidence = self._collect_preferred_evidence(current_hypothesis)

        for row in rows:
            priority = float(row.get("priority", 0.0))
            contradiction_priority = float(row.get("contradiction_priority", 0.0))
            relation_weight = float(row.get("relation_weight", 0.0))
            alternative_overlap = self._estimate_alternative_overlap(row, alternatives)
            recommended_bonus = self._estimate_recommended_bonus(row, preferred_evidence)

            if bool(row.get("is_red_flag", False)):
                priority += self.config.red_flag_bonus

            priority += recommended_bonus

            actions.append(
                MctsAction(
                    action_id=f"verify::{hypothesis_id}::{row['node_id']}",
                    action_type=self.config.default_action_type,
                    target_node_id=row["node_id"],
                    target_node_label=row.get("label", "Unknown"),
                    target_node_name=row.get("name", row["node_id"]),
                    hypothesis_id=hypothesis_id,
                    topic_id=topic_id or row.get("topic_id"),
                    prior_score=priority,
                    metadata={
                        "relation_type": row.get("relation_type"),
                        "relation_weight": relation_weight,
                        "node_weight": float(row.get("node_weight", 0.0)),
                        "similarity_confidence": float(row.get("similarity_confidence", 0.0)),
                        "contradiction_priority": contradiction_priority,
                        "question_type_hint": row.get("question_type_hint", "symptom"),
                        "discriminative_gain": max(
                            0.0,
                            contradiction_priority * (1.0 - alternative_overlap * 0.35) + recommended_bonus * 0.5,
                        ),
                        "novelty_score": max(0.0, 1.0 - relation_weight * 0.7 - alternative_overlap * 0.2 + recommended_bonus * 0.2),
                        "patient_burden": 0.6 if row.get("question_type_hint") == "lab" else 0.25,
                        "is_red_flag": bool(row.get("is_red_flag", False)),
                        "competing_hypothesis_count": len(alternatives),
                        "recommended_evidence_bonus": recommended_bonus,
                    },
                )
            )

        return sorted(actions, key=lambda item: (-item.prior_score, item.target_node_name))

    # 从动作集合中选出当前最适合作为 A3 输出的验证动作。
    def build_a3_verification_result(
        self,
        selected_action: MctsAction | None,
        rationale: str = "",
    ) -> A3VerificationResult:
        if selected_action is None:
            return A3VerificationResult(
                relevant_symptom=None,
                question_text="",
                reasoning="当前没有可用的 R2 验证动作，建议回到 A2 重新生成假设。",
            )

        question_text = self.render_question_text(selected_action)
        return A3VerificationResult(
            relevant_symptom=selected_action,
            question_text=question_text,
            reasoning=rationale or "已选择当前最值得验证的动作进入 A3 提问阶段。",
        )

    # 根据动作类型和目标标签生成更贴近临床语境的提问文本。
    def render_question_text(self, action: MctsAction, style: str = "clinical") -> str:
        question_type_hint = str(action.metadata.get("question_type_hint", "symptom"))
        target_name = action.target_node_name

        if question_type_hint == "lab":
            return f"我想确认一下，之前有没有做过和“{target_name}”相关的检查，结果是否提示异常？"

        if question_type_hint == "risk":
            return f"我需要再核实一下，近期是否存在“{target_name}”相关情况？"

        if question_type_hint == "detail":
            return f"关于“{target_name}”，能再具体描述一下吗？"

        return f"我想再确认一下：近期是否存在“{target_name}”相关表现？"

    # 在没有主假设时，将冷启动问题包装成可追踪动作。
    def build_probe_action_from_question_candidate(self, candidate: QuestionCandidate) -> MctsAction:
        return MctsAction(
            action_id=f"probe::{candidate.node_id}",
            action_type="probe_feature",
            target_node_id=candidate.node_id,
            target_node_label=candidate.label,
            target_node_name=candidate.name,
            topic_id=candidate.topic_id or candidate.label,
            prior_score=max(candidate.priority, candidate.graph_weight, candidate.information_gain, 0.0),
            metadata=dict(candidate.metadata),
        )

    # 估计某条证据与备选假设的重叠程度，越高表示越不具区分性。
    def _estimate_alternative_overlap(
        self,
        row: dict,
        competing_hypotheses: Sequence[HypothesisScore],
    ) -> float:
        if len(competing_hypotheses) == 0:
            return 0.0

        target_name = str(row.get("name", row.get("node_id", "")))
        overlap_count = 0

        for hypothesis in competing_hypotheses:
            evidence_names = hypothesis.metadata.get("evidence_names", [])

            if isinstance(evidence_names, list) and target_name in evidence_names:
                overlap_count += 1

        return min(overlap_count / len(competing_hypotheses), 1.0)

    # 从当前主假设的 metadata 中提取推荐优先验证的证据名称。
    def _collect_preferred_evidence(self, current_hypothesis: HypothesisScore | None) -> set[str]:
        if current_hypothesis is None:
            return set()

        preferred = current_hypothesis.metadata.get("recommended_next_evidence", [])

        if not isinstance(preferred, list):
            return set()

        return {str(item).strip() for item in preferred if len(str(item).strip()) > 0}

    # 判断当前动作是否命中了主假设推荐的区分性证据。
    def _estimate_recommended_bonus(self, row: dict, preferred_evidence: set[str]) -> float:
        if len(preferred_evidence) == 0:
            return 0.0

        target_name = str(row.get("name", row.get("node_id", ""))).strip()
        return 0.25 if target_name in preferred_evidence else 0.0
