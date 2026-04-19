"""疾病级知识图谱审计工具，服务后续自动病例骨架生成。"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Sequence

from brain.neo4j_client import Neo4jClient
from brain.retriever import GraphRetriever
from brain.types import HypothesisScore, SessionState


DISEASE_LABELS = (
    "Disease",
    "DiseasePhase",
    "OpportunisticInfection",
    "Comorbidity",
    "SyndromeOrComplication",
    "Tumor",
)

EVIDENCE_LABELS = (
    "Pathogen",
    "Symptom",
    "Sign",
    "ClinicalAttribute",
    "LabTest",
    "LabFinding",
    "ImagingFinding",
    "RiskFactor",
    "RiskBehavior",
    "PopulationGroup",
)

CORE_RELATION_TYPES = (
    "MANIFESTS_AS",
    "HAS_LAB_FINDING",
    "HAS_IMAGING_FINDING",
    "HAS_PATHOGEN",
    "DIAGNOSED_BY",
    "REQUIRES_DETAIL",
    "RISK_FACTOR_FOR",
    "COMPLICATED_BY",
    "APPLIES_TO",
)

STANDARD_GROUPS = ("symptom", "risk", "lab", "imaging", "pathogen", "detail")
VALID_ACQUISITION_MODES = (
    "direct_ask",
    "history_known",
    "needs_lab_test",
    "needs_imaging",
    "needs_pathogen_test",
    "needs_clinician_assessment",
)
VALID_EVIDENCE_COSTS = ("low", "medium", "high")
GENERIC_RELATION_TYPES = ("REQUIRES_DETAIL", "APPLIES_TO")

GROUP_LABELS = {
    "symptom": "症状 / 体征",
    "risk": "风险背景",
    "lab": "化验",
    "imaging": "影像",
    "pathogen": "病原学",
    "detail": "其他细节",
}

LLM_AUDIT_PROMPT_TEMPLATE = """你是一名医学知识图谱审计助手。下面是一份疾病局部子图或疾病对差异证据报告。

请基于医学常识和问诊决策用途进行审计，不要泛泛复述报告。请重点回答：

1. 这个疾病的核心证据是否医学上合理，哪些证据最关键？
2. 哪些证据明显可疑、过于泛化、方向可能错误，或不适合作为病例生成依据？
3. 哪些关键证据类别可能缺失，例如风险背景、化验、影像、病原学、体征等？
4. 如果报告包含 shared / target_only / competitor_only，当前划分是否能支持鉴别诊断？
5. 哪些节点建议合并、重命名、降权、删除或补边？请给出具体节点名和理由。

请按以下 JSON 结构回答：

{
  "overall_judgement": "usable | needs_minor_fix | needs_major_fix | unusable",
  "core_evidence_reasonable": true,
  "suspicious_evidence": [{"name": "...", "reason": "...", "suggestion": "..."}],
  "missing_evidence_categories": ["..."],
  "differential_quality": "good | weak | not_applicable",
  "merge_or_rename_suggestions": [{"from": "...", "to": "...", "reason": "..."}],
  "priority_fixes": ["..."]
}

报告如下：

```markdown
{{REPORT_MARKDOWN}}
```
"""


@dataclass
class DiseaseNode:
    """疾病节点基础信息。"""

    disease_id: str
    disease_name: str
    disease_label: str
    aliases: list[str] = field(default_factory=list)
    node_weight: float = 0.0


@dataclass
class AuditIssue:
    """程序化规则发现的图谱疑点。"""

    severity: str
    code: str
    message: str
    node_id: str = ""
    node_name: str = ""
    group: str = ""
    relation_type: str = ""


@dataclass
class DiseaseAuditReport:
    """单疾病局部子图审计报告。"""

    disease: DiseaseNode
    evidence: list[dict[str, Any]]
    group_summary: dict[str, dict[str, Any]]
    issues: list[AuditIssue]
    summary: dict[str, Any]


@dataclass
class DifferentialAuditReport:
    """疾病对差异证据审计报告。"""

    target: DiseaseNode
    competitor: DiseaseNode
    shared_evidence: list[dict[str, Any]]
    target_only_evidence: list[dict[str, Any]]
    competitor_only_evidence: list[dict[str, Any]]
    exam_pool: list[dict[str, Any]]
    issues: list[AuditIssue]
    summary: dict[str, Any]


class DiseaseGraphAuditor:
    """按疾病导出局部证据画像并执行结构化审计。"""

    def __init__(self, client: Neo4jClient, retriever: GraphRetriever | None = None) -> None:
        self.client = client
        self.retriever = retriever or GraphRetriever(client)

    # 按名称、ID 或批量模式解析疾病节点。
    def find_diseases(
        self,
        *,
        disease_names: Sequence[str] | None = None,
        disease_ids: Sequence[str] | None = None,
        all_candidates: bool = False,
        labels: Sequence[str] = DISEASE_LABELS,
        limit: int = 200,
    ) -> list[DiseaseNode]:
        names = [item.strip() for item in disease_names or [] if item.strip()]
        ids = [item.strip() for item in disease_ids or [] if item.strip()]

        if all_candidates:
            rows = self.client.run_query(
                """
                MATCH (n)
                WHERE any(label IN labels(n) WHERE label IN $labels)
                RETURN n.id AS disease_id,
                       labels(n)[0] AS disease_label,
                       coalesce(n.canonical_name, n.name) AS disease_name,
                       coalesce(n.aliases, []) AS aliases,
                       coalesce(n.weight, 0.0) AS node_weight
                ORDER BY disease_label, disease_name
                LIMIT $limit
                """,
                {"labels": list(labels), "limit": limit},
            )
            return _dedupe_diseases([_row_to_disease_node(row) for row in rows])

        if not names and not ids:
            raise ValueError("请提供 disease_names、disease_ids，或设置 all_candidates=True。")

        rows = self.client.run_query(
            """
            MATCH (n)
            WHERE any(label IN labels(n) WHERE label IN $labels)
              AND (
                   n.id IN $ids
                OR coalesce(n.name, '') IN $names
                OR coalesce(n.canonical_name, '') IN $names
                OR any(alias IN coalesce(n.aliases, []) WHERE alias IN $names)
              )
            RETURN n.id AS disease_id,
                   labels(n)[0] AS disease_label,
                   coalesce(n.canonical_name, n.name) AS disease_name,
                   coalesce(n.aliases, []) AS aliases,
                   coalesce(n.weight, 0.0) AS node_weight
            ORDER BY disease_label, disease_name
            LIMIT $limit
            """,
            {"labels": list(labels), "names": names, "ids": ids, "limit": limit},
        )
        return _dedupe_diseases([_row_to_disease_node(row) for row in rows])

    # 生成单疾病局部子图审计报告。
    def audit_disease(self, disease: DiseaseNode, *, top_k: int = 80) -> DiseaseAuditReport:
        hypothesis = HypothesisScore(
            node_id=disease.disease_id,
            label=disease.disease_label,
            name=disease.disease_name,
            score=1.0,
        )
        old_profile_limit = self.retriever.config.evidence_profile_limit
        old_group_limit = self.retriever.config.evidence_profile_group_limit
        self.retriever.config.evidence_profile_limit = max(top_k, old_profile_limit)
        self.retriever.config.evidence_profile_group_limit = max(top_k, old_group_limit)

        try:
            evidence = self.retriever.retrieve_candidate_evidence_profile(
                hypothesis,
                SessionState(session_id=f"graph_audit::{disease.disease_id}"),
                top_k=top_k,
            )
        finally:
            self.retriever.config.evidence_profile_limit = old_profile_limit
            self.retriever.config.evidence_profile_group_limit = old_group_limit

        normalized_evidence = [_normalize_evidence_item(item) for item in evidence]
        group_summary = build_group_summary(normalized_evidence)
        issues = audit_evidence_rules(disease, normalized_evidence, group_summary)
        summary = build_disease_summary(normalized_evidence, group_summary, issues)
        return DiseaseAuditReport(
            disease=disease,
            evidence=normalized_evidence,
            group_summary=group_summary,
            issues=issues,
            summary=summary,
        )

    # 生成疾病对差异证据报告。
    def audit_differential_pair(
        self,
        target: DiseaseNode,
        competitor: DiseaseNode,
        *,
        top_k: int = 80,
    ) -> DifferentialAuditReport:
        target_report = self.audit_disease(target, top_k=top_k)
        competitor_report = self.audit_disease(competitor, top_k=top_k)
        shared, target_only, competitor_only = split_differential_evidence(
            target_report.evidence,
            competitor_report.evidence,
        )
        exam_pool = sort_evidence_items(
            [
                {**item, "bucket": bucket}
                for bucket, items in (
                    ("shared", shared),
                    ("target_only", target_only),
                    ("competitor_only", competitor_only),
                )
                for item in items
                if item.get("group") in {"lab", "imaging", "pathogen"}
            ]
        )
        issues = audit_differential_rules(shared, target_only, competitor_only, exam_pool)
        summary = build_differential_summary(shared, target_only, competitor_only, exam_pool, issues)
        return DifferentialAuditReport(
            target=target,
            competitor=competitor,
            shared_evidence=shared,
            target_only_evidence=target_only,
            competitor_only_evidence=competitor_only,
            exam_pool=exam_pool,
            issues=issues,
            summary=summary,
        )


def build_group_summary(evidence: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按标准 evidence group 汇总数量、平均优先级和高成本占比。"""

    summary = {
        group: {
            "count": 0,
            "avg_priority": 0.0,
            "avg_relation_specificity": 0.0,
            "high_cost_count": 0,
            "top_evidence_names": [],
        }
        for group in STANDARD_GROUPS
    }

    for group in STANDARD_GROUPS:
        items = [item for item in evidence if item.get("group") == group]
        count = len(items)

        if count == 0:
            continue

        summary[group] = {
            "count": count,
            "avg_priority": round(sum(float(item.get("priority", 0.0)) for item in items) / count, 4),
            "avg_relation_specificity": round(
                sum(float(item.get("relation_specificity", 0.0)) for item in items) / count,
                4,
            ),
            "high_cost_count": sum(1 for item in items if item.get("evidence_cost") == "high"),
            "top_evidence_names": [str(item.get("target_name") or "") for item in sort_evidence_items(items)[:5]],
        }

    return summary


def audit_evidence_rules(
    disease: DiseaseNode,
    evidence: Sequence[dict[str, Any]],
    group_summary: dict[str, dict[str, Any]],
) -> list[AuditIssue]:
    """执行不依赖 LLM 的局部图谱结构审计。"""

    issues: list[AuditIssue] = []

    if not evidence:
        issues.append(
            AuditIssue(
                severity="error",
                code="no_evidence",
                message=f"{disease.disease_name} 没有可用于问诊的邻接证据。",
            )
        )
        return issues

    for item in evidence:
        issues.extend(_audit_single_evidence_item(item))

    issues.extend(_audit_duplicate_evidence(evidence))

    total = len(evidence)
    generic_count = sum(1 for item in evidence if item.get("relation_type") in GENERIC_RELATION_TYPES)

    if total > 0 and generic_count / total >= 0.6:
        issues.append(
            AuditIssue(
                severity="warning",
                code="generic_relation_ratio_high",
                message=f"泛化关系占比过高：{generic_count}/{total}，可能不利于生成有区分度的病例骨架。",
            )
        )

    if group_summary.get("symptom", {}).get("count", 0) == 0:
        issues.append(
            AuditIssue(
                severity="warning",
                code="missing_symptom_evidence",
                message="缺少 symptom 组证据，问诊开场和低成本线索可能不足。",
            )
        )

    if group_summary.get("risk", {}).get("count", 0) == 0:
        issues.append(
            AuditIssue(
                severity="warning",
                code="missing_risk_evidence",
                message="缺少 risk 组证据，风险背景或易感人群信息可能不完整。",
            )
        )

    exam_count = sum(group_summary.get(group, {}).get("count", 0) for group in ("lab", "imaging", "pathogen"))

    if exam_count == 0:
        issues.append(
            AuditIssue(
                severity="warning",
                code="missing_exam_evidence",
                message="缺少 lab / imaging / pathogen 检查证据，后续 exam_pool 可能为空。",
            )
        )

    detail_count = group_summary.get("detail", {}).get("count", 0)

    if total > 0 and detail_count / total >= 0.7:
        issues.append(
            AuditIssue(
                severity="warning",
                code="detail_group_dominant",
                message=f"detail 组占比过高：{detail_count}/{total}，可能说明证据过于泛化。",
            )
        )

    if total < 4:
        issues.append(
            AuditIssue(
                severity="warning",
                code="too_few_evidence",
                message=f"邻接证据数量较少：{total}，不适合直接自动生成病例骨架。",
            )
        )

    return issues


def _audit_single_evidence_item(item: dict[str, Any]) -> list[AuditIssue]:
    issues: list[AuditIssue] = []
    label = str(item.get("target_label") or "")
    group = str(item.get("group") or "")
    relation_type = str(item.get("relation_type") or "")
    acquisition_mode = str(item.get("acquisition_mode") or "")
    evidence_cost = str(item.get("evidence_cost") or "")

    def append_issue(severity: str, code: str, message: str) -> None:
        issues.append(
            AuditIssue(
                severity=severity,
                code=code,
                message=message,
                node_id=str(item.get("target_node_id") or ""),
                node_name=str(item.get("target_name") or ""),
                group=group,
                relation_type=relation_type,
            )
        )

    if label not in EVIDENCE_LABELS:
        append_issue("error", "unexpected_evidence_label", f"邻接证据标签不在搜索主流程集合内：{label}")

    if group not in STANDARD_GROUPS:
        append_issue("error", "invalid_group", f"证据分组为空或不合法：{group}")

    if relation_type not in CORE_RELATION_TYPES:
        append_issue("error", "unexpected_relation_type", f"关系类型不在核心集合内：{relation_type}")

    if acquisition_mode not in VALID_ACQUISITION_MODES:
        append_issue("warning", "invalid_acquisition_mode", f"acquisition_mode 缺失或非法：{acquisition_mode}")

    if evidence_cost not in VALID_EVIDENCE_COSTS:
        append_issue("warning", "invalid_evidence_cost", f"evidence_cost 缺失或非法：{evidence_cost}")

    if label in {"LabFinding", "LabTest"} and acquisition_mode == "direct_ask":
        append_issue("warning", "lab_direct_ask_mismatch", "LabFinding / LabTest 不应主要标记为 direct_ask。")

    if label == "LabFinding" and acquisition_mode not in {"needs_lab_test", "needs_pathogen_test"}:
        append_issue("warning", "labfinding_acquisition_mismatch", "LabFinding 的 acquisition_mode 与检查型证据不匹配。")

    if label == "LabTest" and acquisition_mode != "needs_lab_test":
        append_issue("warning", "labtest_acquisition_mismatch", "LabTest 通常应为 needs_lab_test。")

    if label == "ImagingFinding" and acquisition_mode != "needs_imaging":
        append_issue("warning", "imaging_acquisition_mismatch", "ImagingFinding 通常应为 needs_imaging。")

    if label == "Pathogen" and acquisition_mode != "needs_pathogen_test":
        append_issue("warning", "pathogen_acquisition_mismatch", "Pathogen 通常应为 needs_pathogen_test。")

    if label == "Symptom" and evidence_cost == "high":
        append_issue("warning", "symptom_high_cost_mismatch", "Symptom 不应是 high cost 证据。")

    if label in {"RiskFactor", "RiskBehavior"} and evidence_cost == "high":
        append_issue("warning", "risk_high_cost_mismatch", "RiskFactor / RiskBehavior 通常应是低成本可问证据。")

    if label == "ImagingFinding" and evidence_cost != "high":
        append_issue("warning", "imaging_cost_mismatch", "ImagingFinding 通常应是 high cost 证据。")

    return issues


def _audit_duplicate_evidence(evidence: Sequence[dict[str, Any]]) -> list[AuditIssue]:
    issues: list[AuditIssue] = []
    by_normalized_name: dict[str, list[dict[str, Any]]] = {}

    for item in evidence:
        key = normalize_name(str(item.get("target_name") or ""))

        if not key:
            continue

        by_normalized_name.setdefault(key, []).append(item)

    for items in by_normalized_name.values():
        node_ids = {str(item.get("target_node_id") or "") for item in items}

        if len(node_ids) <= 1:
            continue

        issues.append(
            AuditIssue(
                severity="warning",
                code="duplicate_evidence_name",
                message="存在同名证据节点，建议检查 alias 合并。",
                node_id=", ".join(sorted(node_ids)),
                node_name=str(items[0].get("target_name") or ""),
                group=str(items[0].get("group") or ""),
                relation_type=str(items[0].get("relation_type") or ""),
            )
        )

    sorted_items = list(evidence)

    for index, left in enumerate(sorted_items):
        left_name = str(left.get("target_name") or "")

        for right in sorted_items[index + 1 :]:
            right_name = str(right.get("target_name") or "")
            left_id = str(left.get("target_node_id") or "")
            right_id = str(right.get("target_node_id") or "")

            if left_id == right_id or not left_name or not right_name:
                continue

            similarity = SequenceMatcher(None, normalize_name(left_name), normalize_name(right_name)).ratio()

            if similarity < 0.92:
                continue

            issues.append(
                AuditIssue(
                    severity="info",
                    code="similar_evidence_names",
                    message=f"证据名称高度相似，可能需要 alias 合并：{left_name} / {right_name}。",
                    node_id=f"{left_id}, {right_id}",
                    node_name=f"{left_name} / {right_name}",
                )
            )

    return issues


def split_differential_evidence(
    target_evidence: Sequence[dict[str, Any]],
    competitor_evidence: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """基于 node_id 和归一化名称拆分 shared / target_only / competitor_only。"""

    competitor_ids = _evidence_id_set(competitor_evidence)
    competitor_names = _evidence_name_set(competitor_evidence)
    target_ids = _evidence_id_set(target_evidence)
    target_names = _evidence_name_set(target_evidence)

    shared = [
        {
            **item,
            "target_priority": float(item.get("priority", 0.0)),
            "competitor_priority": float(_find_matching_evidence(competitor_evidence, item).get("priority", 0.0)),
        }
        for item in target_evidence
        if _has_matching_evidence(item, competitor_ids, competitor_names)
    ]
    target_only = [
        item
        for item in target_evidence
        if not _has_matching_evidence(item, competitor_ids, competitor_names)
    ]
    competitor_only = [
        item
        for item in competitor_evidence
        if not _has_matching_evidence(item, target_ids, target_names)
    ]
    return sort_evidence_items(shared), sort_evidence_items(target_only), sort_evidence_items(competitor_only)


def audit_differential_rules(
    shared: Sequence[dict[str, Any]],
    target_only: Sequence[dict[str, Any]],
    competitor_only: Sequence[dict[str, Any]],
    exam_pool: Sequence[dict[str, Any]],
) -> list[AuditIssue]:
    """审计疾病对是否有足够可区分证据。"""

    issues: list[AuditIssue] = []
    total = len(shared) + len(target_only) + len(competitor_only)

    if total == 0:
        return [
            AuditIssue(
                severity="error",
                code="empty_pair_evidence",
                message="疾病对没有可比较证据。",
            )
        ]

    if len(target_only) == 0:
        issues.append(
            AuditIssue(
                severity="error",
                code="missing_target_only_evidence",
                message="主诊断没有 target_only 证据，不适合生成目标病例骨架。",
            )
        )

    if len(competitor_only) == 0:
        issues.append(
            AuditIssue(
                severity="warning",
                code="missing_competitor_only_evidence",
                message="竞争病没有 competitor_only 证据，差异病例可能难以构造。",
            )
        )

    if len(shared) / total >= 0.55:
        issues.append(
            AuditIssue(
                severity="warning",
                code="shared_evidence_ratio_high",
                message=f"shared_evidence 占比偏高：{len(shared)}/{total}，疾病对可能高度混淆。",
            )
        )

    if len(exam_pool) == 0:
        issues.append(
            AuditIssue(
                severity="warning",
                code="empty_exam_pool",
                message="差异证据中缺少 lab / imaging / pathogen 检查池。",
            )
        )

    high_value_target = [
        item
        for item in target_only
        if float(item.get("priority", 0.0)) >= 1.0 or float(item.get("relation_specificity", 0.0)) >= 0.85
    ]

    if len(high_value_target) == 0:
        issues.append(
            AuditIssue(
                severity="warning",
                code="weak_target_only_evidence",
                message="主诊断缺少高优先级或高特异性的独有证据。",
            )
        )

    return issues


def build_disease_summary(
    evidence: Sequence[dict[str, Any]],
    group_summary: dict[str, dict[str, Any]],
    issues: Sequence[AuditIssue],
) -> dict[str, Any]:
    return {
        "evidence_count": len(evidence),
        "issue_count": len(issues),
        "error_count": sum(1 for item in issues if item.severity == "error"),
        "warning_count": sum(1 for item in issues if item.severity == "warning"),
        "groups_present": [group for group, payload in group_summary.items() if payload.get("count", 0) > 0],
        "exam_evidence_count": sum(1 for item in evidence if item.get("group") in {"lab", "imaging", "pathogen"}),
        "direct_ask_count": sum(1 for item in evidence if item.get("acquisition_mode") in {"direct_ask", "history_known"}),
        "high_cost_count": sum(1 for item in evidence if item.get("evidence_cost") == "high"),
    }


def build_differential_summary(
    shared: Sequence[dict[str, Any]],
    target_only: Sequence[dict[str, Any]],
    competitor_only: Sequence[dict[str, Any]],
    exam_pool: Sequence[dict[str, Any]],
    issues: Sequence[AuditIssue],
) -> dict[str, Any]:
    return {
        "shared_count": len(shared),
        "target_only_count": len(target_only),
        "competitor_only_count": len(competitor_only),
        "exam_pool_count": len(exam_pool),
        "issue_count": len(issues),
        "error_count": sum(1 for item in issues if item.severity == "error"),
        "warning_count": sum(1 for item in issues if item.severity == "warning"),
        "top_differentiating_target_evidence": [
            str(item.get("target_name") or "") for item in sort_evidence_items(target_only)[:5]
        ],
        "top_differentiating_competitor_evidence": [
            str(item.get("target_name") or "") for item in sort_evidence_items(competitor_only)[:5]
        ],
    }


def render_disease_markdown(report: DiseaseAuditReport) -> str:
    """渲染适合人工审阅和 LLM 审计的单疾病 Markdown。"""

    disease = report.disease
    lines = [
        f"# 疾病级图谱审计：{disease.disease_name}",
        "",
        "## 1. 疾病基本信息",
        "",
        f"- disease_id: `{disease.disease_id}`",
        f"- disease_name: {disease.disease_name}",
        f"- disease_label: `{disease.disease_label}`",
        f"- aliases: {', '.join(disease.aliases) if disease.aliases else '无'}",
        f"- node_weight: {disease.node_weight:.4f}",
        "",
        "## 2. 分组统计",
        "",
        markdown_table(
            ["分组", "数量", "平均优先级", "平均特异性", "高成本数", "Top 证据"],
            [
                [
                    GROUP_LABELS.get(group, group),
                    payload.get("count", 0),
                    payload.get("avg_priority", 0.0),
                    payload.get("avg_relation_specificity", 0.0),
                    payload.get("high_cost_count", 0),
                    "；".join(payload.get("top_evidence_names", [])[:3]),
                ]
                for group, payload in report.group_summary.items()
            ],
        ),
        "",
        "## 3. 关键证据表",
        "",
        markdown_table(
            ["证据", "标签", "分组", "关系", "priority", "specificity", "acquisition", "cost"],
            [
                [
                    item.get("target_name", ""),
                    item.get("target_label", ""),
                    item.get("group", ""),
                    item.get("relation_type", ""),
                    f"{float(item.get('priority', 0.0)):.3f}",
                    f"{float(item.get('relation_specificity', 0.0)):.3f}",
                    item.get("acquisition_mode", ""),
                    item.get("evidence_cost", ""),
                ]
                for item in sort_evidence_items(report.evidence)
            ],
        ),
        "",
        "## 4. 可疑项",
        "",
        render_issues_markdown(report.issues),
        "",
        "## 5. 简短总结",
        "",
        f"- evidence_count: {report.summary['evidence_count']}",
        f"- exam_evidence_count: {report.summary['exam_evidence_count']}",
        f"- direct_ask_count: {report.summary['direct_ask_count']}",
        f"- high_cost_count: {report.summary['high_cost_count']}",
        f"- issue_count: {report.summary['issue_count']}，其中 error={report.summary['error_count']}，warning={report.summary['warning_count']}",
    ]
    return "\n".join(lines) + "\n"


def render_differential_markdown(report: DifferentialAuditReport) -> str:
    """渲染适合人工审阅和 LLM 审计的疾病对 Markdown。"""

    lines = [
        f"# 疾病对差异证据审计：{report.target.disease_name} vs {report.competitor.disease_name}",
        "",
        "## 1. 主诊断 / 竞争病",
        "",
        f"- 主诊断: {report.target.disease_name} (`{report.target.disease_id}`, {report.target.disease_label})",
        f"- 竞争病: {report.competitor.disease_name} (`{report.competitor.disease_id}`, {report.competitor.disease_label})",
        "",
        "## 2. shared_evidence",
        "",
        render_evidence_section(report.shared_evidence),
        "",
        "## 3. target_only_evidence",
        "",
        render_evidence_section(report.target_only_evidence),
        "",
        "## 4. competitor_only_evidence",
        "",
        render_evidence_section(report.competitor_only_evidence),
        "",
        "## 5. exam_pool",
        "",
        render_evidence_section(report.exam_pool, include_bucket=True),
        "",
        "## 6. 推荐的可区分证据总结",
        "",
        f"- shared_count: {report.summary['shared_count']}",
        f"- target_only_count: {report.summary['target_only_count']}",
        f"- competitor_only_count: {report.summary['competitor_only_count']}",
        f"- exam_pool_count: {report.summary['exam_pool_count']}",
        f"- 主诊断 Top 独有证据: {'；'.join(report.summary['top_differentiating_target_evidence']) or '无'}",
        f"- 竞争病 Top 独有证据: {'；'.join(report.summary['top_differentiating_competitor_evidence']) or '无'}",
        "",
        "## 7. 可疑项",
        "",
        render_issues_markdown(report.issues),
    ]
    return "\n".join(lines) + "\n"


def render_evidence_section(items: Sequence[dict[str, Any]], *, include_bucket: bool = False) -> str:
    if not items:
        return "无\n"

    headers = ["证据", "分组", "关系", "priority", "specificity", "acquisition", "cost"]

    if include_bucket:
        headers.insert(0, "来源")

    rows = []

    for item in sort_evidence_items(items):
        row = [
            item.get("target_name", ""),
            item.get("group", ""),
            item.get("relation_type", ""),
            f"{float(item.get('priority', 0.0)):.3f}",
            f"{float(item.get('relation_specificity', 0.0)):.3f}",
            item.get("acquisition_mode", ""),
            item.get("evidence_cost", ""),
        ]

        if include_bucket:
            row.insert(0, item.get("bucket", ""))

        rows.append(row)

    return markdown_table(headers, rows)


def render_issues_markdown(issues: Sequence[AuditIssue]) -> str:
    if not issues:
        return "未发现程序化规则疑点。\n"

    return markdown_table(
        ["级别", "代码", "说明", "节点", "分组", "关系"],
        [
            [
                item.severity,
                item.code,
                item.message,
                item.node_name or item.node_id,
                item.group,
                item.relation_type,
            ]
            for item in issues
        ],
    )


def write_disease_report(report: DiseaseAuditReport, output_dir: Path) -> dict[str, Path]:
    """写入单疾病 JSON / Markdown / LLM prompt。"""

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_filename(f"{report.disease.disease_name}_{report.disease.disease_id}")
    json_path = output_dir / f"{stem}.json"
    markdown_path = output_dir / f"{stem}.md"
    prompt_path = output_dir / f"{stem}.llm_prompt.md"
    markdown = render_disease_markdown(report)

    json_path.write_text(
        json.dumps(disease_report_to_dict(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(markdown, encoding="utf-8")
    prompt_path.write_text(render_llm_prompt(markdown), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path, "prompt": prompt_path}


def write_differential_report(report: DifferentialAuditReport, output_dir: Path) -> dict[str, Path]:
    """写入疾病对 JSON / Markdown / LLM prompt。"""

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_filename(f"{report.target.disease_name}_vs_{report.competitor.disease_name}")
    json_path = output_dir / f"{stem}.json"
    markdown_path = output_dir / f"{stem}.md"
    prompt_path = output_dir / f"{stem}.llm_prompt.md"
    markdown = render_differential_markdown(report)

    json_path.write_text(
        json.dumps(differential_report_to_dict(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(markdown, encoding="utf-8")
    prompt_path.write_text(render_llm_prompt(markdown), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path, "prompt": prompt_path}


def disease_report_to_dict(report: DiseaseAuditReport) -> dict[str, Any]:
    return {
        "disease": asdict(report.disease),
        "evidence": report.evidence,
        "group_summary": report.group_summary,
        "issues": [asdict(item) for item in report.issues],
        "summary": report.summary,
    }


def differential_report_to_dict(report: DifferentialAuditReport) -> dict[str, Any]:
    return {
        "target": asdict(report.target),
        "competitor": asdict(report.competitor),
        "shared_evidence": report.shared_evidence,
        "target_only_evidence": report.target_only_evidence,
        "competitor_only_evidence": report.competitor_only_evidence,
        "exam_pool": report.exam_pool,
        "issues": [asdict(item) for item in report.issues],
        "summary": report.summary,
    }


def render_llm_prompt(report_markdown: str) -> str:
    return LLM_AUDIT_PROMPT_TEMPLATE.replace("{{REPORT_MARKDOWN}}", report_markdown.strip())


def sort_evidence_items(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [dict(item) for item in items],
        key=lambda item: (
            -evidence_sort_score(item),
            str(item.get("group") or ""),
            str(item.get("target_name") or ""),
        ),
    )


def evidence_sort_score(item: dict[str, Any]) -> float:
    group_bonus = 0.2 if item.get("group") in {"lab", "imaging", "pathogen"} else 0.0
    return (
        float(item.get("priority", 0.0))
        + float(item.get("relation_specificity", 0.0)) * 0.55
        + float(item.get("relation_weight", 0.0)) * 0.2
        + group_bonus
    )


def normalize_name(value: str) -> str:
    return (
        value.strip()
        .lower()
        .replace("（", "(")
        .replace("）", ")")
        .replace("，", ",")
        .replace("。", "")
        .replace("、", "")
        .replace(" ", "")
        .replace("-", "")
        .replace("_", "")
        .replace("/", "")
    )


def safe_filename(value: str) -> str:
    normalized = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value.strip())
    normalized = normalized.strip("._")
    return normalized[:120] or "graph_audit_report"


def markdown_table(headers: Sequence[Any], rows: Sequence[Sequence[Any]]) -> str:
    header_row = "| " + " | ".join(_md_cell(item) for item in headers) + " |"
    separator = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(_md_cell(item) for item in row) + " |" for row in rows]
    return "\n".join([header_row, separator, *body]) + "\n"


def _md_cell(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("|", "\\|").replace("\n", "<br>")


def _row_to_disease_node(row: dict[str, Any]) -> DiseaseNode:
    return DiseaseNode(
        disease_id=str(row.get("disease_id") or ""),
        disease_name=str(row.get("disease_name") or ""),
        disease_label=str(row.get("disease_label") or ""),
        aliases=[str(item) for item in row.get("aliases") or [] if str(item).strip()],
        node_weight=float(row.get("node_weight") or 0.0),
    )


def _dedupe_diseases(diseases: Sequence[DiseaseNode]) -> list[DiseaseNode]:
    by_id: dict[str, DiseaseNode] = {}

    for disease in diseases:
        if not disease.disease_id:
            continue

        by_id.setdefault(disease.disease_id, disease)

    return list(by_id.values())


def _normalize_evidence_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "target_node_id": str(item.get("node_id") or item.get("target_node_id") or ""),
        "target_name": str(item.get("name") or item.get("target_name") or ""),
        "target_label": str(item.get("label") or item.get("target_label") or ""),
        "group": str(item.get("group") or item.get("question_type_hint") or ""),
        "relation_type": str(item.get("relation_type") or ""),
        "priority": float(item.get("priority") or 0.0),
        "relation_specificity": float(item.get("relation_specificity") or 0.0),
        "relation_weight": float(item.get("relation_weight") or 0.0),
        "node_weight": float(item.get("node_weight") or 0.0),
        "acquisition_mode": str(item.get("acquisition_mode") or ""),
        "evidence_cost": str(item.get("evidence_cost") or ""),
        "question_type_hint": str(item.get("question_type_hint") or item.get("group") or ""),
        "status": str(item.get("status") or "unknown"),
        "status_label": str(item.get("status_label") or "待验证"),
    }


def _evidence_id_set(items: Sequence[dict[str, Any]]) -> set[str]:
    return {str(item.get("target_node_id") or "").strip() for item in items if str(item.get("target_node_id") or "").strip()}


def _evidence_name_set(items: Sequence[dict[str, Any]]) -> set[str]:
    return {
        normalize_name(str(item.get("target_name") or ""))
        for item in items
        if normalize_name(str(item.get("target_name") or ""))
    }


def _has_matching_evidence(item: dict[str, Any], ids: set[str], names: set[str]) -> bool:
    node_id = str(item.get("target_node_id") or "").strip()
    normalized_name = normalize_name(str(item.get("target_name") or ""))
    return (len(node_id) > 0 and node_id in ids) or (len(normalized_name) > 0 and normalized_name in names)


def _find_matching_evidence(items: Sequence[dict[str, Any]], target: dict[str, Any]) -> dict[str, Any]:
    target_id = str(target.get("target_node_id") or "").strip()
    target_name = normalize_name(str(target.get("target_name") or ""))

    for item in items:
        item_id = str(item.get("target_node_id") or "").strip()

        if target_id and item_id == target_id:
            return item

    for item in items:
        item_name = normalize_name(str(item.get("target_name") or ""))

        if target_name and item_name == target_name:
            return item

    return {}
