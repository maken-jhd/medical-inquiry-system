"""负责将 R2 检索结果构造成可供 UCT 选择的候选动作。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional

from .types import A3VerificationResult, MctsAction, QuestionCandidate


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
    ) -> List[MctsAction]:
        actions: List[MctsAction] = []

        for row in rows:
            priority = float(row.get("priority", 0.0))

            if bool(row.get("is_red_flag", False)):
                priority += self.config.red_flag_bonus

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
                        "is_red_flag": bool(row.get("is_red_flag", False)),
                    },
                )
            )

        return sorted(actions, key=lambda item: (-item.prior_score, item.target_node_name))

    # 从动作集合中选出当前最适合作为 A3 输出的验证动作。
    def build_a3_verification_result(
        self,
        actions: Iterable[MctsAction],
    ) -> A3VerificationResult:
        ranked = sorted(actions, key=lambda item: (-item.prior_score, item.target_node_name))

        if len(ranked) == 0:
            return A3VerificationResult(
                relevant_symptom=None,
                question_text="",
                reasoning="当前没有可用的 R2 验证动作，建议回到 A2 重新生成假设。",
            )

        action = ranked[0]
        question_text = self.render_question_text(action)
        return A3VerificationResult(
            relevant_symptom=action,
            question_text=question_text,
            reasoning="已选择当前优先级最高的验证动作进入 A3 提问阶段。",
        )

    # 根据目标节点名称生成一个基础版提问文本。
    def render_question_text(self, action: MctsAction) -> str:
        return f"我想再确认一下：近期是否存在“{action.target_node_name}”相关表现？"

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
