"""负责将 R2 检索结果构造成可供 UCT 选择的候选动作。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from .types import A3VerificationResult, HypothesisScore, MctsAction, QuestionCandidate, SessionState


@dataclass
class ActionBuilderConfig:
    """保存动作构造阶段的默认参数。"""

    default_action_type: str = "verify_evidence"
    exam_context_action_type: str = "collect_exam_context"
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
        session_state: SessionState | None = None,
    ) -> List[MctsAction]:
        actions: List[MctsAction] = []
        exam_context_actions: dict[str, MctsAction] = {}
        alternatives = list(competing_hypotheses or [])
        preferred_evidence = self._collect_preferred_evidence(current_hypothesis, "recommended_next_evidence")
        verifier_preferred_evidence = self._collect_preferred_evidence(current_hypothesis, "verifier_recommended_next_evidence")
        hypothesis_preferred_evidence = self._collect_preferred_evidence(
            current_hypothesis,
            "hypothesis_recommended_next_evidence",
        )

        for row in rows:
            priority = float(row.get("priority", 0.0))
            contradiction_priority = float(row.get("contradiction_priority", 0.0))
            relation_weight = float(row.get("relation_weight", 0.0))
            question_type_hint = str(row.get("question_type_hint", "symptom"))
            acquisition_mode, evidence_cost = self._resolve_acquisition_info(row, question_type_hint)
            exam_kind = self._infer_exam_kind(acquisition_mode, question_type_hint, str(row.get("label", "")))
            alternative_overlap = self._estimate_alternative_overlap(row, alternatives)
            recommended_bonus, recommended_match_score, evidence_tags = self._estimate_recommended_bonus(
                row,
                preferred_evidence,
            )
            _, verifier_recommended_match_score, _ = self._estimate_recommended_bonus(
                row,
                verifier_preferred_evidence,
            )
            _, hypothesis_recommended_match_score, _ = self._estimate_recommended_bonus(
                row,
                hypothesis_preferred_evidence,
            )
            joint_recommended_match_score = self._estimate_joint_recommended_match(
                verifier_recommended_match_score,
                hypothesis_recommended_match_score,
            )

            if bool(row.get("is_red_flag", False)):
                priority += self.config.red_flag_bonus

            priority += recommended_bonus
            accessibility_bias = self._compute_accessibility_bias(acquisition_mode, evidence_cost)
            priority += accessibility_bias
            patient_burden = self._estimate_patient_burden(acquisition_mode, evidence_cost, question_type_hint)

            if exam_kind is not None and session_state is not None and self._is_high_cost_exam_mode(acquisition_mode):
                availability = self._get_exam_availability(session_state, exam_kind)

                if availability == "not_done":
                    continue

                if availability == "unknown":
                    self._accumulate_exam_context_action(
                        exam_context_actions,
                        exam_kind=exam_kind,
                        row=row,
                        hypothesis_id=hypothesis_id,
                        topic_id=topic_id,
                        priority=priority,
                        evidence_tags=evidence_tags,
                        acquisition_mode=acquisition_mode,
                        evidence_cost=evidence_cost,
                    )
                    continue

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
                        "question_type_hint": question_type_hint,
                        "acquisition_mode": acquisition_mode,
                        "evidence_cost": evidence_cost,
                        "accessibility_bias": accessibility_bias,
                        "discriminative_gain": max(
                            0.0,
                            contradiction_priority * (1.0 - alternative_overlap * 0.45)
                            + recommended_match_score * 0.35
                            + joint_recommended_match_score * 0.25,
                        ),
                        "novelty_score": max(
                            0.0,
                            1.0 - relation_weight * 0.7 - alternative_overlap * 0.2 + recommended_match_score * 0.15,
                        ),
                        "patient_burden": patient_burden,
                        "is_red_flag": bool(row.get("is_red_flag", False)),
                        "competing_hypothesis_count": len(alternatives),
                        "alternative_overlap": alternative_overlap,
                        "recommended_evidence_bonus": recommended_bonus,
                        "recommended_match_score": recommended_match_score,
                        "verifier_recommended_match_score": verifier_recommended_match_score,
                        "hypothesis_recommended_match_score": hypothesis_recommended_match_score,
                        "joint_recommended_match_score": joint_recommended_match_score,
                        "evidence_tags": sorted(evidence_tags),
                    },
                )
            )

        actions.extend(exam_context_actions.values())
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
        custom_question = str(action.metadata.get("question_text") or "").strip()

        if len(custom_question) > 0:
            return custom_question

        question_type_hint = str(action.metadata.get("question_type_hint", "symptom"))
        target_name = action.target_node_name

        if action.action_type == self.config.exam_context_action_type:
            return self._render_exam_context_question(action)

        if question_type_hint == "lab":
            return f"如果你手头记得检查结果，我想确认一下和“{target_name}”相关的结果是否提示异常？"

        if question_type_hint == "imaging":
            return f"如果最近做过胸部影像或 CT，报告里有没有提到“{target_name}”？"

        if question_type_hint == "pathogen":
            return f"如果做过痰检、PCR 或支气管肺泡灌洗等病原学检查，结果里有没有提示“{target_name}”？"

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

    # 对高成本证据构造一次性检查上下文采集动作。
    def _accumulate_exam_context_action(
        self,
        actions_by_kind: dict[str, MctsAction],
        *,
        exam_kind: str,
        row: dict,
        hypothesis_id: str,
        topic_id: Optional[str],
        priority: float,
        evidence_tags: set[str],
        acquisition_mode: str,
        evidence_cost: str,
    ) -> None:
        action = actions_by_kind.get(exam_kind)
        candidate_payload = {
            "node_id": row["node_id"],
            "label": row.get("label", "Unknown"),
            "name": row.get("name", row["node_id"]),
            "relation_type": row.get("relation_type"),
            "question_type_hint": row.get("question_type_hint", exam_kind),
            "acquisition_mode": acquisition_mode,
            "evidence_cost": evidence_cost,
            "priority": float(row.get("priority", priority)),
            "contradiction_priority": float(row.get("contradiction_priority", 0.0)),
        }

        if action is None:
            action = MctsAction(
                action_id=f"collect_exam::{hypothesis_id}::{exam_kind}",
                action_type=self.config.exam_context_action_type,
                target_node_id=f"__exam_context__::{exam_kind}",
                target_node_label="ExamContext",
                target_node_name=self._exam_context_display_name(exam_kind),
                hypothesis_id=hypothesis_id,
                topic_id=topic_id or str(row.get("topic_id") or exam_kind),
                prior_score=max(priority - 0.05, 0.0),
                metadata={
                    "exam_kind": exam_kind,
                    "question_type_hint": exam_kind,
                    "acquisition_mode": acquisition_mode,
                    "evidence_cost": evidence_cost,
                    "patient_burden": 0.35,
                    "accessibility_bias": -0.05,
                    "exam_candidate_evidence": [candidate_payload],
                    "exam_examples": [candidate_payload["name"]],
                    "evidence_tags": sorted(evidence_tags | {f"type:{exam_kind}", "exam_context"}),
                    "is_red_flag": bool(row.get("is_red_flag", False)),
                    "relation_type": row.get("relation_type"),
                    "collects_exam_availability": True,
                },
            )
            actions_by_kind[exam_kind] = action
            return

        action.prior_score = max(action.prior_score, priority - 0.05)
        action.metadata["is_red_flag"] = bool(action.metadata.get("is_red_flag", False)) or bool(
            row.get("is_red_flag", False)
        )
        action.metadata.setdefault("exam_candidate_evidence", []).append(candidate_payload)

        examples = action.metadata.setdefault("exam_examples", [])
        if candidate_payload["name"] not in examples:
            examples.append(candidate_payload["name"])

        merged_tags = set(action.metadata.get("evidence_tags", [])) | evidence_tags | {f"type:{exam_kind}", "exam_context"}
        action.metadata["evidence_tags"] = sorted(merged_tags)

    # 根据图谱提供的 acquisition / cost 字段做缺省修补。
    def _resolve_acquisition_info(self, row: dict, question_type_hint: str) -> tuple[str, str]:
        acquisition_mode = str(row.get("acquisition_mode") or "").strip()
        evidence_cost = str(row.get("evidence_cost") or "").strip()
        label = str(row.get("label") or "")

        if len(acquisition_mode) == 0:
            if label in {"Symptom", "Sign", "RiskFactor", "RiskBehavior", "ClinicalAttribute"}:
                acquisition_mode = "direct_ask"
            elif label == "PopulationGroup":
                acquisition_mode = "history_known"
            elif label in {"LabFinding", "LabTest"} or question_type_hint == "lab":
                acquisition_mode = "needs_lab_test"
            elif label == "ImagingFinding" or question_type_hint == "imaging":
                acquisition_mode = "needs_imaging"
            elif label == "Pathogen" or question_type_hint == "pathogen":
                acquisition_mode = "needs_pathogen_test"

        if len(evidence_cost) == 0:
            if acquisition_mode in {"direct_ask", "history_known"}:
                evidence_cost = "low"
            elif acquisition_mode == "needs_clinician_assessment":
                evidence_cost = "medium"
            elif acquisition_mode in {"needs_lab_test", "needs_imaging", "needs_pathogen_test"}:
                evidence_cost = "high"

        return acquisition_mode, evidence_cost

    # 将检查获取方式压缩到三类检查上下文。
    def _infer_exam_kind(self, acquisition_mode: str, question_type_hint: str, label: str) -> str | None:
        if acquisition_mode == "needs_lab_test" or question_type_hint == "lab" or label in {"LabFinding", "LabTest"}:
            return "lab"

        if acquisition_mode == "needs_imaging" or question_type_hint == "imaging" or label == "ImagingFinding":
            return "imaging"

        if acquisition_mode == "needs_pathogen_test" or question_type_hint == "pathogen" or label == "Pathogen":
            return "pathogen"

        return None

    # 判断当前证据是否属于需要检查上下文门控的高成本证据。
    def _is_high_cost_exam_mode(self, acquisition_mode: str) -> bool:
        return acquisition_mode in {"needs_lab_test", "needs_imaging", "needs_pathogen_test"}

    # 从会话状态读取某类检查是否做过。
    def _get_exam_availability(self, session_state: SessionState | None, exam_kind: str) -> str:
        if session_state is None:
            return "unknown"

        context = session_state.exam_context.get(exam_kind)

        if context is None:
            return "unknown"

        return context.availability

    # 使用 acquisition_mode / evidence_cost 估算患者负担。
    def _estimate_patient_burden(self, acquisition_mode: str, evidence_cost: str, question_type_hint: str) -> float:
        if evidence_cost == "low" or acquisition_mode in {"direct_ask", "history_known"}:
            return 0.2

        if evidence_cost == "medium" or acquisition_mode == "needs_clinician_assessment":
            return 0.4

        if evidence_cost == "high" or acquisition_mode in {"needs_lab_test", "needs_imaging", "needs_pathogen_test"}:
            return 0.65

        return 0.55 if question_type_hint in {"lab", "imaging", "pathogen"} else 0.25

    # 轻量成本偏置：只在信息量接近时影响排序，不阻止高价值检查证据。
    def _compute_accessibility_bias(self, acquisition_mode: str, evidence_cost: str) -> float:
        if acquisition_mode in {"direct_ask", "history_known"} or evidence_cost == "low":
            return 0.18

        if acquisition_mode == "needs_clinician_assessment" or evidence_cost == "medium":
            return 0.04

        if acquisition_mode in {"needs_lab_test", "needs_imaging", "needs_pathogen_test"} or evidence_cost == "high":
            return -0.08

        return 0.0

    # 检查上下文动作的展示名。
    def _exam_context_display_name(self, exam_kind: str) -> str:
        return {
            "lab": "化验检查情况",
            "imaging": "胸部影像检查情况",
            "pathogen": "病原学检查情况",
        }.get(exam_kind, "检查情况")

    # 渲染一次性采集“是否做过 / 检查名称 / 结果”的自然问法。
    def _render_exam_context_question(self, action: MctsAction) -> str:
        exam_kind = str(action.metadata.get("exam_kind") or "lab")
        examples = [
            str(item).strip()
            for item in action.metadata.get("exam_examples", [])
            if len(str(item).strip()) > 0
        ][:3]
        example_text = "、".join(examples)

        if exam_kind == "imaging":
            return "最近有没有做过胸部 CT 或胸片？如果做过，也可以顺便说一下报告里有没有提到明显异常。"

        if exam_kind == "pathogen":
            return "最近有没有做过痰检、PCR 或支气管肺泡灌洗之类的病原学检查？如果做过，也可以顺便说一下有没有阳性结果。"

        if len(example_text) == 0:
            example_text = "CD4、HIV RNA、β-D 葡聚糖"

        return f"最近有没有做过和免疫状态或感染相关的化验，比如 {example_text}？如果做过，也可以顺便说一下你记得的结果。"

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
    def _collect_preferred_evidence(self, current_hypothesis: HypothesisScore | None, metadata_key: str) -> list[str]:
        if current_hypothesis is None:
            return []

        preferred = current_hypothesis.metadata.get(metadata_key, [])

        if not isinstance(preferred, list):
            return []

        values: list[str] = []

        for item in preferred:
            text = str(item).strip()

            if len(text) == 0 or text in values:
                continue

            values.append(text)

        return values

    # 判断当前动作是否命中了主假设推荐的区分性证据。
    def _estimate_recommended_bonus(self, row: dict, preferred_evidence: Sequence[str]) -> tuple[float, float, set[str]]:
        if len(preferred_evidence) == 0:
            evidence_tags = self._infer_evidence_tags(
                str(row.get("name", row.get("node_id", ""))),
                str(row.get("question_type_hint", "symptom")),
            )
            return 0.0, 0.0, evidence_tags

        target_name = str(row.get("name", row.get("node_id", ""))).strip()
        question_type_hint = str(row.get("question_type_hint", "symptom"))
        evidence_tags = self._infer_evidence_tags(target_name, question_type_hint)
        normalized_target = self._normalize_evidence_text(target_name)
        best_match_score = 0.0

        for preferred in preferred_evidence:
            normalized_preferred = self._normalize_evidence_text(preferred)
            preferred_tags = self._infer_evidence_tags(preferred)
            match_score = 0.0

            if normalized_target == normalized_preferred:
                match_score = 1.0
            elif len(normalized_target) > 0 and len(normalized_preferred) > 0:
                if normalized_target in normalized_preferred or normalized_preferred in normalized_target:
                    match_score = 0.85

            if match_score < 0.85 and len(evidence_tags) > 0 and len(preferred_tags) > 0:
                overlap = len(evidence_tags & preferred_tags)

                if overlap > 0:
                    union = len(evidence_tags | preferred_tags)
                    match_score = max(match_score, 0.55 + overlap / max(union, 1) * 0.35)

            if match_score < 0.65:
                token_overlap = self._estimate_token_overlap(normalized_target, normalized_preferred)
                match_score = max(match_score, token_overlap * 0.65)

            best_match_score = max(best_match_score, match_score)

        return best_match_score * 0.45, best_match_score, evidence_tags

    # 同时命中 verifier 缺口与当前 hypothesis 推荐证据时，额外视为高价值修补动作。
    def _estimate_joint_recommended_match(self, verifier_match_score: float, hypothesis_match_score: float) -> float:
        if verifier_match_score > 0.0 and hypothesis_match_score > 0.0:
            return min(verifier_match_score, hypothesis_match_score)

        return max(verifier_match_score, hypothesis_match_score)

    # 轻量估计推荐证据文本与候选节点之间的词片段重叠。
    def _estimate_token_overlap(self, normalized_target: str, normalized_preferred: str) -> float:
        if len(normalized_target) == 0 or len(normalized_preferred) == 0:
            return 0.0

        target_tokens = self._evidence_tokens(normalized_target)
        preferred_tokens = self._evidence_tokens(normalized_preferred)

        if len(target_tokens) == 0 or len(preferred_tokens) == 0:
            return 0.0

        overlap = len(target_tokens & preferred_tokens)
        return overlap / max(min(len(target_tokens), len(preferred_tokens)), 1)

    # 从短医学文本中抽取可复用的轻量 token，增强“CT 结果”与“胸部CT磨玻璃影”这类匹配。
    def _evidence_tokens(self, normalized_text: str) -> set[str]:
        token_rules = {
            "ct": ("ct", "胸部ct", "影像", "磨玻璃"),
            "xray": ("x线", "胸片"),
            "oxygen": ("低氧", "血氧", "氧分压", "pao2", "氧合"),
            "hiv": ("hiv", "艾滋", "cd4", "病毒载量"),
            "pcr": ("核酸", "pcr"),
            "pcp": ("肺孢子", "pcp", "βd葡聚糖", "bdg"),
            "tb": ("结核", "盗汗", "抗酸", "分枝杆菌"),
            "risk": ("高危", "性行为", "暴露", "接触史"),
            "respiratory": ("咳嗽", "干咳", "呼吸困难", "气促"),
        }
        tokens: set[str] = set()

        for token, keywords in token_rules.items():
            if any(keyword in normalized_text for keyword in keywords):
                tokens.add(token)

        return tokens

    # 归一化医学证据文本，便于不同表述之间做轻量匹配。
    def _normalize_evidence_text(self, text: str) -> str:
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

    # 根据医学关键词把证据压缩成可比较的语义标签。
    def _infer_evidence_tags(self, text: str, question_type_hint: str | None = None) -> set[str]:
        normalized = self._normalize_evidence_text(text)
        tags: set[str] = set()
        tag_rules = {
            "immune_status": ("hiv", "cd4", "t淋巴", "免疫", "艾滋", "机会性感染", "免疫抑制"),
            "imaging": ("ct", "影像", "x线", "胸片", "磨玻璃", "双肺"),
            "oxygenation": ("低氧", "血氧", "pao2", "氧分压", "肺泡", "氧合"),
            "respiratory": ("发热", "干咳", "咳嗽", "呼吸困难", "气促"),
            "pathogen": ("βd葡聚糖", "bdg", "病原", "痰", "balf", "病原学"),
            "pcp_specific": ("肺孢子", "pneumocystis", "pcp", "βd葡聚糖", "bdg"),
            "viral": ("核酸", "pcr", "新冠", "covid", "病毒载量", "hivrna"),
            "tuberculosis": ("结核", "盗汗", "抗酸", "分枝杆菌", "tb"),
            "systemic": ("皮疹", "咽痛", "关节疼痛", "腹泻", "淋巴结"),
            "risk": ("高危", "性行为", "接触史", "暴露"),
        }

        for tag, keywords in tag_rules.items():
            if any(keyword in normalized for keyword in keywords):
                tags.add(tag)

        if question_type_hint is not None and len(question_type_hint.strip()) > 0:
            tags.add(f"type:{question_type_hint.strip()}")

        return tags
