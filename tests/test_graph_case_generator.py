"""测试图谱驱动虚拟病人生成器。"""

from __future__ import annotations

import json
from pathlib import Path

from simulator.graph_case_generator import (
    DiseaseAuditRecord,
    GraphCaseGenerator,
    GraphCaseGeneratorConfig,
    load_disease_audit_reports,
)


STANDARD_GROUPS = ("symptom", "risk", "lab", "imaging", "pathogen", "detail")


def _make_evidence(
    node_id: str,
    name: str,
    group: str,
    *,
    priority: float = 1.0,
    relation_specificity: float = 0.85,
    relation_type: str | None = None,
    acquisition_mode: str | None = None,
    evidence_cost: str | None = None,
) -> dict:
    default_relation = {
        "symptom": "MANIFESTS_AS",
        "risk": "RISK_FACTOR_FOR",
        "detail": "REQUIRES_DETAIL",
        "lab": "HAS_LAB_FINDING",
        "imaging": "HAS_IMAGING_FINDING",
        "pathogen": "HAS_PATHOGEN",
    }
    default_acquisition = {
        "symptom": "direct_ask",
        "risk": "history_known",
        "detail": "direct_ask",
        "lab": "needs_lab_test",
        "imaging": "needs_imaging",
        "pathogen": "needs_pathogen_test",
    }
    default_cost = {
        "symptom": "low",
        "risk": "low",
        "detail": "low",
        "lab": "high",
        "imaging": "high",
        "pathogen": "high",
    }
    return {
        "target_node_id": node_id,
        "target_name": name,
        "target_label": {
            "symptom": "ClinicalFinding",
            "risk": "RiskFactor",
            "detail": "ClinicalAttribute",
            "lab": "LabFinding",
            "imaging": "ImagingFinding",
            "pathogen": "Pathogen",
        }[group],
        "group": group,
        "relation_type": relation_type or default_relation[group],
        "priority": priority,
        "relation_specificity": relation_specificity,
        "relation_weight": 1.0,
        "node_weight": 1.0,
        "acquisition_mode": acquisition_mode or default_acquisition[group],
        "evidence_cost": evidence_cost or default_cost[group],
        "question_type_hint": group,
        "status": "unknown",
        "status_label": "待验证",
    }


def _make_group_summary(evidence: list[dict]) -> dict[str, dict[str, object]]:
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
        items = [item for item in evidence if item["group"] == group]
        if not items:
            continue
        summary[group] = {
            "count": len(items),
            "avg_priority": round(sum(float(item["priority"]) for item in items) / len(items), 4),
            "avg_relation_specificity": round(
                sum(float(item["relation_specificity"]) for item in items) / len(items),
                4,
            ),
            "high_cost_count": sum(1 for item in items if item["evidence_cost"] == "high"),
            "top_evidence_names": [item["target_name"] for item in items[:5]],
        }
    return summary


def _make_record(disease_id: str, disease_name: str, evidence: list[dict]) -> DiseaseAuditRecord:
    return DiseaseAuditRecord(
        disease_id=disease_id,
        disease_name=disease_name,
        disease_label="Disease",
        evidence=evidence,
        group_summary=_make_group_summary(evidence),
        summary={"evidence_count": len(evidence)},
        source_file=Path(f"{disease_name}.json"),
    )


# 验证输入审计缺少关键字段时会被标记为 invalid，并阻止后续病例生成。
def test_load_disease_audit_reports_marks_missing_required_fields(tmp_path: Path) -> None:
    payload = {
        "disease": {
            "disease_id": "merged_node_invalid0001",
            "disease_name": "字段缺失病",
            "disease_label": "Disease",
        },
        "summary": {"evidence_count": 1},
        "group_summary": _make_group_summary([]),
        "evidence": [
            {
                "target_node_id": "merged_node_slot_001",
                "target_name": "发热",
                "group": "symptom",
                "relation_type": "MANIFESTS_AS",
                "relation_specificity": 0.85,
                "acquisition_mode": "direct_ask",
                "evidence_cost": "low",
            }
        ],
    }
    report_file = tmp_path / "invalid_report.json"
    report_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    valid, invalid = load_disease_audit_reports(tmp_path)

    assert valid == []
    assert len(invalid) == 1
    assert invalid[0]["disease_name"] == "字段缺失病"
    assert invalid[0]["missing_fields"] == ["priority"]


# 验证 ordinary 即使总证据数够多，只要 chief-friendly 不足仍然跳过。
def test_generator_requires_chief_friendly_pool_for_ordinary() -> None:
    exam_only_record = _make_record(
        "merged_node_exam0001",
        "检查型疾病",
        [
            _make_evidence("merged_node_lab_001", "血清学异常A", "lab", priority=2.0, relation_specificity=1.0),
            _make_evidence("merged_node_lab_002", "血清学异常B", "lab", priority=1.9, relation_specificity=1.0),
            _make_evidence("merged_node_img_001", "影像异常A", "imaging", priority=1.8, relation_specificity=1.0),
            _make_evidence("merged_node_path_001", "病原阳性A", "pathogen", priority=1.7, relation_specificity=1.0),
        ],
    )

    result = GraphCaseGenerator().generate_from_records([exam_only_record])

    entry = result.manifest["diseases"][0]
    skipped_reasons = {item["reason"] for item in entry["skipped"]}
    generated_types = set(entry["generated_case_types"])

    assert "ordinary" not in generated_types
    assert "insufficient_chief_complaint_friendly_pool" in skipped_reasons
    assert "exam_driven" in generated_types


# 验证 low_cost 不会把 needs_clinician_assessment 计入低成本池。
def test_generator_excludes_clinician_assessment_from_low_cost_pool() -> None:
    record = _make_record(
        "merged_node_lowcost0001",
        "低成本不足病",
        [
            _make_evidence("merged_node_sym_001", "发热", "symptom", priority=1.8),
            _make_evidence("merged_node_sym_002", "干咳", "symptom", priority=1.7),
            _make_evidence("merged_node_risk_001", "HIV感染", "risk", priority=1.6),
            _make_evidence(
                "merged_node_det_001",
                "皮损压痛",
                "detail",
                priority=1.5,
                acquisition_mode="needs_clinician_assessment",
            ),
        ],
    )

    result = GraphCaseGenerator().generate_from_records([record])

    entry = result.manifest["diseases"][0]
    skipped = next(item for item in entry["skipped"] if item["case_type"] == "low_cost")
    assert skipped["reason"] == "insufficient_low_cost_pool"
    assert skipped["available_pool_size"] == 3


# 验证 exam_driven 使用 3 个检查证据 + 2 个高价值证据的双阈值。
def test_generator_accepts_exam_driven_with_three_exam_and_two_high_value() -> None:
    record = _make_record(
        "merged_node_exam0002",
        "典型检查驱动病",
        [
            _make_evidence("merged_node_sym_010", "发热", "symptom", priority=1.0),
            _make_evidence("merged_node_lab_010", "血清隐球菌抗原阳性", "lab", priority=2.0, relation_specificity=1.0),
            _make_evidence("merged_node_lab_011", "墨汁染色阳性", "lab", priority=1.9, relation_specificity=1.0),
            _make_evidence("merged_node_path_010", "新生隐球菌", "pathogen", priority=1.2, relation_specificity=0.4),
        ],
    )

    result = GraphCaseGenerator().generate_from_records([record])

    entry = result.manifest["diseases"][0]
    assert "exam_driven" in entry["generated_case_types"]
    assert entry["pool_counts"]["exam_pool_total"] == 3
    assert entry["pool_counts"]["exam_pool_high_value"] == 3


# 验证 competitive 使用综合竞争分数挑选竞争病，而不是只看 symptom overlap。
def test_generator_competitive_uses_combined_competition_score() -> None:
    target = _make_record(
        "merged_node_target0001",
        "目标病",
        [
            _make_evidence("merged_node_sym_t001", "发热", "symptom", priority=1.8),
            _make_evidence("merged_node_sym_t002", "干咳", "symptom", priority=1.7),
            _make_evidence("merged_node_risk_t001", "HIV感染", "risk", priority=1.6),
            _make_evidence("merged_node_det_t001", "活动后气促", "detail", priority=1.55),
            _make_evidence("merged_node_lab_t001", "目标病特异抗原阳性", "lab", priority=2.0, relation_specificity=1.0),
            _make_evidence("merged_node_lab_t002", "目标病PCR阳性", "lab", priority=1.95, relation_specificity=1.0),
        ],
    )
    symptom_only = _make_record(
        "merged_node_comp10001",
        "竞争病-症状更像",
        [
            _make_evidence("merged_node_sym_c101", "发热", "symptom", priority=1.8),
            _make_evidence("merged_node_sym_c102", "干咳", "symptom", priority=1.7),
            _make_evidence("merged_node_sym_c103", "胸痛", "symptom", priority=1.6),
            _make_evidence("merged_node_lab_c101", "竞争病甲检查阳性", "lab", priority=1.3, relation_specificity=0.4),
        ],
    )
    combined = _make_record(
        "merged_node_comp20001",
        "竞争病-综合重叠",
        [
            _make_evidence("merged_node_sym_x001", "发热", "symptom", priority=1.8),
            _make_evidence("merged_node_risk_x001", "HIV感染", "risk", priority=1.6),
            _make_evidence("merged_node_det_x001", "活动后气促", "detail", priority=1.55),
            _make_evidence("merged_node_lab_x001", "目标病特异抗原阳性", "lab", priority=2.0, relation_specificity=1.0),
            _make_evidence("merged_node_sym_x002", "盗汗", "symptom", priority=1.4),
        ],
    )

    result = GraphCaseGenerator().generate_from_records([target, symptom_only, combined])
    target_cases = [
        case
        for case in result.cases
        if case.metadata.get("case_type") == "competitive"
        and case.metadata.get("disease_id") == target.disease_id
    ]

    assert len(target_cases) == 1
    assert target_cases[0].metadata["competitor_disease_name"] == "竞争病-综合重叠"


# 验证 max_competitors_per_disease 可限制竞争病例个数，并允许后续扩展到多个。
def test_generator_respects_max_competitors_per_disease() -> None:
    target = _make_record(
        "merged_node_target0002",
        "目标病-双竞争",
        [
            _make_evidence("merged_node_sym_tt01", "发热", "symptom", priority=1.8),
            _make_evidence("merged_node_sym_tt02", "干咳", "symptom", priority=1.7),
            _make_evidence("merged_node_risk_tt01", "HIV感染", "risk", priority=1.6),
            _make_evidence("merged_node_det_tt01", "夜间加重", "detail", priority=1.55),
            _make_evidence("merged_node_lab_tt01", "目标病检查A阳性", "lab", priority=2.0, relation_specificity=1.0),
            _make_evidence("merged_node_lab_tt02", "目标病检查B阳性", "lab", priority=1.95, relation_specificity=1.0),
        ],
    )
    competitor_a = _make_record(
        "merged_node_compa0001",
        "竞争病A",
        [
            _make_evidence("merged_node_sym_ca01", "发热", "symptom", priority=1.8),
            _make_evidence("merged_node_risk_ca01", "HIV感染", "risk", priority=1.6),
            _make_evidence("merged_node_det_ca01", "夜间加重", "detail", priority=1.55),
            _make_evidence("merged_node_lab_ca01", "目标病检查A阳性", "lab", priority=2.0, relation_specificity=1.0),
            _make_evidence("merged_node_sym_ca02", "盗汗", "symptom", priority=1.4),
        ],
    )
    competitor_b = _make_record(
        "merged_node_compb0001",
        "竞争病B",
        [
            _make_evidence("merged_node_sym_cb01", "发热", "symptom", priority=1.8),
            _make_evidence("merged_node_risk_cb01", "HIV感染", "risk", priority=1.6),
            _make_evidence("merged_node_det_cb01", "夜间加重", "detail", priority=1.55),
            _make_evidence("merged_node_lab_cb01", "目标病检查B阳性", "lab", priority=2.0, relation_specificity=1.0),
            _make_evidence("merged_node_sym_cb02", "胸闷", "symptom", priority=1.4),
        ],
    )

    one_competitor = GraphCaseGenerator().generate_from_records([target, competitor_a, competitor_b])
    two_competitors = GraphCaseGenerator(
        GraphCaseGeneratorConfig(max_competitors_per_disease=2)
    ).generate_from_records([target, competitor_a, competitor_b])

    one_cases = [
        case
        for case in one_competitor.cases
        if case.metadata.get("case_type") == "competitive"
        and case.metadata.get("disease_id") == target.disease_id
    ]
    two_cases = [
        case
        for case in two_competitors.cases
        if case.metadata.get("case_type") == "competitive"
        and case.metadata.get("disease_id") == target.disease_id
    ]

    assert len(one_cases) == 1
    assert len(two_cases) == 2


# 验证生成的 slot_truth_map key 和 SlotTruth.node_id 都使用真实 target_node_id。
def test_generator_uses_real_target_node_id_for_slot_truth_map() -> None:
    record = _make_record(
        "merged_node_realid0001",
        "真实ID病",
        [
            _make_evidence("merged_node_slot001", "发热", "symptom", priority=1.8),
            _make_evidence("merged_node_slot002", "干咳", "symptom", priority=1.7),
            _make_evidence("merged_node_slot003", "HIV感染", "risk", priority=1.6),
            _make_evidence("merged_node_slot004", "夜间加重", "detail", priority=1.55),
        ],
    )

    result = GraphCaseGenerator().generate_from_records([record])
    low_cost_case = next(case for case in result.cases if case.metadata.get("case_type") == "low_cost")

    assert set(low_cost_case.slot_truth_map.keys()) == {
        "merged_node_slot001",
        "merged_node_slot002",
        "merged_node_slot003",
        "merged_node_slot004",
    }
    assert {truth.node_id for truth in low_cost_case.slot_truth_map.values()} == set(low_cost_case.slot_truth_map.keys())
    assert any(not truth.reveal_only_if_asked for truth in low_cost_case.slot_truth_map.values())
