"""测试图谱驱动虚拟病人生成器。"""

from __future__ import annotations

import json
from pathlib import Path

from simulator.case_schema import SlotTruth, VirtualPatientCase
from simulator.graph_case_generator import (
    DiseaseAuditRecord,
    GraphCaseGenerator,
    GraphCaseGeneratorConfig,
    build_case_type_sample_payload,
    load_disease_audit_reports,
    render_case_type_sample_markdown,
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
    target_label: str | None = None,
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
        "target_label": target_label or {
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


# 验证 competitive opening 会跳过 HIV / ART 背景项，并回退到目标病自己的症状线索。
def test_generator_competitive_opening_falls_back_to_target_symptoms() -> None:
    target = _make_record(
        "merged_node_comp_opening001",
        "目标病-症状回退",
        [
            _make_evidence("merged_node_shared_risk_001", "HIV感染", "risk", priority=1.9),
            _make_evidence("merged_node_shared_risk_002", "抗逆转录病毒治疗", "risk", priority=1.8),
            _make_evidence("merged_node_target_sym_001", "发热", "symptom", priority=1.7),
            _make_evidence("merged_node_target_sym_002", "头痛", "symptom", priority=1.6),
        ],
    )
    competitor = _make_record(
        "merged_node_comp_opening002",
        "竞争病-背景更像",
        [
            _make_evidence("merged_node_shared_risk_101", "HIV感染", "risk", priority=1.9),
            _make_evidence("merged_node_shared_risk_102", "抗逆转录病毒治疗", "risk", priority=1.8),
            _make_evidence("merged_node_comp_sym_001", "胸痛", "symptom", priority=1.5),
            _make_evidence("merged_node_comp_sym_002", "盗汗", "symptom", priority=1.4),
        ],
    )

    result = GraphCaseGenerator().generate_from_records([target, competitor])
    case = next(
        case
        for case in result.cases
        if case.metadata.get("case_type") == "competitive"
        and case.metadata.get("disease_id") == target.disease_id
    )

    opening_names = list(case.metadata.get("opening_slot_names") or [])
    assert "HIV感染" not in opening_names
    assert "抗逆转录病毒治疗" not in opening_names
    assert any(name in opening_names for name in ("发热", "头痛"))
    assert "HIV感染" not in case.chief_complaint


# 验证 competitive opening 若共享项和 target-only 都不适合自然开场，会回退到疾病名。
def test_generator_competitive_opening_falls_back_to_disease_name_when_no_natural_signal() -> None:
    target = _make_record(
        "merged_node_comp_opening003",
        "肥胖",
        [
            _make_evidence("merged_node_shared_risk_201", "HIV感染", "risk", priority=1.9),
            _make_evidence("merged_node_shared_risk_202", "抗逆转录病毒治疗", "risk", priority=1.8),
            _make_evidence("merged_node_target_det_001", "BMI>=28.0kg/m2", "detail", priority=1.7),
            _make_evidence("merged_node_target_det_002", "体脂含量女性>=30%", "detail", priority=1.6),
        ],
    )
    competitor = _make_record(
        "merged_node_comp_opening004",
        "血脂异常",
        [
            _make_evidence("merged_node_shared_risk_301", "HIV感染", "risk", priority=1.9),
            _make_evidence("merged_node_shared_risk_302", "抗逆转录病毒治疗", "risk", priority=1.8),
            _make_evidence("merged_node_comp_det_001", "LDL-C ≥ 3.0 mmol/L", "detail", priority=1.5),
            _make_evidence("merged_node_comp_det_002", "ASCVD风险等级", "detail", priority=1.4),
        ],
    )

    result = GraphCaseGenerator().generate_from_records([target, competitor])
    case = next(
        case
        for case in result.cases
        if case.metadata.get("case_type") == "competitive"
        and case.metadata.get("disease_id") == target.disease_id
    )

    assert list(case.metadata.get("opening_slot_names") or []) == []
    assert case.chief_complaint == "最近想咨询一下肥胖相关的情况。"


# 验证 PCP 竞争病例会优先覆盖可判定所需的呼吸、免疫、影像和真菌/病原证据族。
def test_generator_competitive_covers_pcp_required_evidence_families() -> None:
    target = _make_record(
        "merged_node_pcp0001",
        "肺孢子菌肺炎",
        [
            _make_evidence("merged_node_sym_pcp_001", "发热", "symptom", priority=1.9),
            _make_evidence("merged_node_sym_pcp_002", "咳嗽", "symptom", priority=1.8),
            _make_evidence("merged_node_risk_pcp_001", "HIV/AIDS", "risk", priority=1.7),
            _make_evidence("merged_node_lab_pcp_001", "CD4+ T淋巴细胞计数 < 200/μL", "lab", priority=2.0),
            _make_evidence("merged_node_img_pcp_001", "双肺弥漫性磨玻璃影", "imaging", priority=2.0),
            _make_evidence("merged_node_lab_pcp_003", "动脉血氧分压 <60 mmHg", "lab", priority=2.0),
            _make_evidence("merged_node_lab_pcp_002", "(1,3)-β-D-葡聚糖明显高于正常值", "lab", priority=2.0),
            _make_evidence("merged_node_path_pcp_001", "耶氏肺孢子菌", "pathogen", priority=1.95),
        ],
    )
    competitor = _make_record(
        "merged_node_tb0001",
        "结核病",
        [
            _make_evidence("merged_node_sym_tb_001", "发热", "symptom", priority=1.9),
            _make_evidence("merged_node_sym_tb_002", "咳嗽", "symptom", priority=1.8),
            _make_evidence("merged_node_risk_tb_001", "HIV/AIDS", "risk", priority=1.7),
            _make_evidence("merged_node_sym_tb_003", "盗汗", "symptom", priority=1.6),
            _make_evidence("merged_node_path_tb_001", "结核分枝杆菌", "pathogen", priority=2.0),
        ],
    )

    result = GraphCaseGenerator().generate_from_records([target, competitor])
    case = next(
        item
        for item in result.cases
        if item.metadata.get("case_type") == "competitive"
        and item.metadata.get("disease_id") == target.disease_id
    )

    positive_names = set(case.metadata.get("selected_positive_slots") or [])

    assert "咳嗽" in positive_names
    assert "CD4+ T淋巴细胞计数 < 200/μL" in positive_names
    assert "双肺弥漫性磨玻璃影" in positive_names
    assert "动脉血氧分压 <60 mmHg" in positive_names
    assert "(1,3)-β-D-葡聚糖明显高于正常值" in positive_names
    assert case.metadata["benchmark_qc_status"] == "eligible"
    assert case.metadata["benchmark_missing_family_groups"] == []
    assert {"respiratory_symptom", "immune_status", "imaging", "oxygenation", "fungal_marker"}.issubset(
        set(case.metadata["evidence_family_coverage"])
    )


# 验证生成器优先使用 full-evidence catalog 中的疾病最低证据组。
def test_generator_uses_catalog_minimum_evidence_groups(tmp_path: Path) -> None:
    catalog_file = tmp_path / "disease_minimum_evidence_groups.json"
    catalog_file.write_text(
        json.dumps(
            [
                {
                    "disease_id": "merged_node_catalog_required",
                    "disease_name": "目录约束病",
                    "minimum_evidence_groups": [["oxygenation"]],
                    "minimum_evidence_groups_by_evidence_group": {"lab": [["oxygenation"]]},
                    "evidence_family_counts": {"oxygenation": 1},
                }
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    record = _make_record(
        "merged_node_catalog_required",
        "目录约束病",
            [
                *[
                    _make_evidence(
                        f"merged_node_sym_catalog_{index:03d}",
                        symptom_name,
                        "symptom",
                        priority=2.0 - index * 0.01,
                    )
                    for index, symptom_name in enumerate(
                        ["咳嗽", "头痛", "腹痛", "皮疹", "胸闷", "乏力", "视物模糊", "关节痛", "尿痛"],
                        start=1,
                    )
                ],
                _make_evidence("merged_node_lab_catalog_001", "动脉血氧分压 <60 mmHg", "lab", priority=0.5),
            ],
        )

    result = GraphCaseGenerator(
        GraphCaseGeneratorConfig(minimum_evidence_groups_file=str(catalog_file))
    ).generate_from_records([record])
    ordinary_case = next(case for case in result.cases if case.metadata.get("case_type") == "ordinary")

    assert "动脉血氧分压 <60 mmHg" in ordinary_case.metadata["selected_positive_slots"]
    assert ordinary_case.metadata["benchmark_requirement_source"] == "catalog"
    assert ordinary_case.metadata["benchmark_required_family_groups"] == [["oxygenation"]]
    assert ordinary_case.metadata["benchmark_qc_status"] == "eligible"
    assert result.manifest["minimum_evidence_requirement_catalog"]["requirement_count_by_id"] == 1


# 验证病例 QC 不会把 CD4 / HIV / 年龄这类背景证据当成 benchmark 核心锚点。
def test_generator_case_qc_rejects_background_only_positive_slots() -> None:
    record = _make_record(
        "merged_node_background_only",
        "原发性肝细胞癌",
        [
            _make_evidence("merged_node_cd4_bg_001", "CD4+ T淋巴细胞计数 < 200/μL", "lab", priority=2.0),
            _make_evidence("merged_node_risk_bg_001", "HIV感染者", "risk", priority=1.8),
            _make_evidence("merged_node_age_bg_001", "年龄", "detail", priority=1.7),
            _make_evidence("merged_node_history_bg_001", "既往病史", "risk", priority=1.6),
        ],
    )

    result = GraphCaseGenerator().generate_from_records([record])
    case = next(item for item in result.cases if item.metadata.get("case_type") == "ordinary")

    assert case.metadata["case_qc_status"] == "not_benchmark_eligible"
    assert case.metadata["benchmark_qc_status"] == "ineligible"
    assert "missing_disease_specific_or_definition_anchor" in case.metadata["case_qc_reasons"]
    assert "missing_primary_diagnostic_path_core_evidence" in case.metadata["case_qc_reasons"]


# 验证感染类病例需要 pathogen / 特异实验室 / 特异影像中的核心证据，而不是只靠背景风险。
def test_generator_case_qc_accepts_infection_specific_anchor() -> None:
    record = _make_record(
        "merged_node_infection_anchor",
        "巨细胞病毒感染",
        [
            _make_evidence("merged_node_risk_cmv_001", "HIV感染者", "risk", priority=1.8),
            _make_evidence("merged_node_lab_cmv_001", "CMV DNA阳性", "lab", priority=2.0),
            _make_evidence("merged_node_path_cmv_001", "巨细胞病毒", "pathogen", priority=1.9),
            _make_evidence("merged_node_img_cmv_001", "胸部CT提示间质性浸润", "imaging", priority=1.7),
            _make_evidence("merged_node_sym_cmv_001", "发热", "symptom", priority=1.5),
        ],
    )

    result = GraphCaseGenerator().generate_from_records([record])
    case = next(item for item in result.cases if item.metadata.get("case_type") == "exam_driven")

    assert case.metadata["case_qc_status"] == "eligible"
    assert case.metadata["benchmark_qc_status"] == "eligible"
    assert case.metadata["case_qc_positive_role_counts"]["disease_specific_anchor"] >= 1


# 验证肿瘤类病例必须有影像、病理或肿瘤标志物，单独肿瘤分期不构成核心诊断路径。
def test_generator_case_qc_requires_tumor_core_evidence() -> None:
    weak_record = _make_record(
        "merged_node_tumor_weak",
        "肝癌",
        [
            _make_evidence("merged_node_risk_tumor_001", "HIV感染者", "risk", priority=1.8),
            _make_evidence("merged_node_det_tumor_001", "肿瘤分期", "detail", priority=1.7),
            _make_evidence("merged_node_sym_tumor_001", "腹胀", "symptom", priority=1.6),
            _make_evidence("merged_node_sym_tumor_002", "消瘦", "symptom", priority=1.5),
        ],
    )
    strong_record = _make_record(
        "merged_node_tumor_strong",
        "宫颈癌",
        [
            _make_evidence("merged_node_sym_tumor_101", "阴道出血", "symptom", priority=1.5),
            _make_evidence("merged_node_img_tumor_101", "盆腔MRI提示宫颈肿块", "imaging", priority=2.0),
            _make_evidence("merged_node_lab_tumor_101", "病理活检提示鳞癌", "lab", priority=1.9),
            _make_evidence("merged_node_lab_tumor_102", "肿瘤标志物升高", "lab", priority=1.8),
            _make_evidence("merged_node_det_tumor_101", "肿瘤分期", "detail", priority=1.4),
        ],
    )

    result = GraphCaseGenerator().generate_from_records([weak_record, strong_record])
    weak_case = next(
        item
        for item in result.cases
        if item.metadata.get("case_type") == "low_cost"
        and item.metadata.get("disease_id") == weak_record.disease_id
    )
    strong_case = next(
        item
        for item in result.cases
        if item.metadata.get("case_type") == "exam_driven"
        and item.metadata.get("disease_id") == strong_record.disease_id
    )

    assert weak_case.metadata["case_qc_status"] == "not_benchmark_eligible"
    assert "tumor_imaging_pathology_marker_or_definition" in weak_case.metadata["case_qc_missing_core_roles"]
    assert strong_case.metadata["case_qc_status"] == "eligible"


# 验证竞争负例不会把目标病名称或核心定义误写成阴性槽位。
def test_generator_filters_competitive_negative_conflicting_with_target_definition() -> None:
    target = _make_record(
        "merged_node_obesity_target",
        "肥胖",
        [
            _make_evidence("merged_node_shared_risk_ob_001", "HIV感染", "risk", priority=1.8),
            _make_evidence("merged_node_shared_risk_ob_002", "抗逆转录病毒治疗", "risk", priority=1.7),
            _make_evidence("merged_node_bmi_ob_001", "BMI>=28.0kg/m2", "risk", priority=2.0),
            _make_evidence("merged_node_sym_ob_001", "体重增加", "symptom", priority=1.9),
        ],
    )
    competitor = _make_record(
        "merged_node_lipid_comp",
        "血脂异常",
        [
            _make_evidence("merged_node_shared_risk_li_001", "HIV感染", "risk", priority=1.8),
            _make_evidence("merged_node_shared_risk_li_002", "抗逆转录病毒治疗", "risk", priority=1.7),
            _make_evidence("merged_node_sym_li_001", "肥胖", "symptom", priority=1.9),
            _make_evidence("merged_node_risk_li_001", "吸烟", "risk", priority=1.6),
            _make_evidence("merged_node_lab_li_001", "LDL-C ≥ 3.0 mmol/L", "lab", priority=2.0),
        ],
    )

    result = GraphCaseGenerator().generate_from_records([target, competitor])
    case = next(
        item
        for item in result.cases
        if item.metadata.get("case_type") == "competitive"
        and item.metadata.get("disease_id") == target.disease_id
    )

    negative_names = set(case.metadata.get("selected_negative_slots") or [])
    positive_names = set(case.metadata.get("selected_positive_slots") or [])

    assert "肥胖" not in negative_names
    assert "LDL-C ≥ 3.0 mmol/L" not in negative_names
    assert "吸烟" in negative_names
    assert "BMI>=28.0kg/m2" in positive_names
    assert case.metadata["benchmark_qc_status"] == "eligible"


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


# 验证生成器会去除同一 BMI 分层族中的互斥阳性证据，只保留优先级最高的一条。
def test_generator_filters_mutually_exclusive_bmi_positive_slots() -> None:
    record = _make_record(
        "merged_node_obesity0001",
        "肥胖",
        [
            _make_evidence("merged_node_bmi_001", "BMI>=37.5kg/m²", "detail", priority=2.0),
            _make_evidence("merged_node_bmi_002", "32.5<=BMI<37.5kg/m²", "detail", priority=1.8),
            _make_evidence("merged_node_bmi_003", "28.0<=BMI<32.5kg/m²", "detail", priority=1.6),
            _make_evidence("merged_node_sym_101", "体重增加", "symptom", priority=1.5),
            _make_evidence("merged_node_sym_102", "体重难以控制", "symptom", priority=1.4),
        ],
    )

    result = GraphCaseGenerator().generate_from_records([record])
    low_cost_case = next(case for case in result.cases if case.metadata.get("case_type") == "low_cost")

    positive_names = list(low_cost_case.metadata.get("selected_positive_slots") or [])
    bmi_names = [name for name in positive_names if "BMI" in name or "kg/m²" in name or "身体质量指数" in name]
    truth_names = [
        truth.aliases[0]
        for truth in low_cost_case.slot_truth_map.values()
        if truth.aliases and ("BMI" in truth.aliases[0] or "kg/m²" in truth.aliases[0] or "身体质量指数" in truth.aliases[0])
    ]

    assert bmi_names == ["BMI>=37.5kg/m²"]
    assert truth_names == ["BMI>=37.5kg/m²"]


# 验证生成器会把多个 CD4 阈值阳性压缩为一个最严重结果。
def test_generator_filters_mutually_exclusive_cd4_positive_slots() -> None:
    record = _make_record(
        "merged_node_cd40001",
        "CD4互斥病",
        [
            _make_evidence("merged_node_cd4_001", "CD4+ T淋巴细胞计数", "lab", priority=2.0),
            _make_evidence("merged_node_cd4_002", "CD4+ T淋巴细胞计数 < 300/μL", "lab", priority=1.7),
            _make_evidence("merged_node_cd4_003", "CD4+ T淋巴细胞计数 < 200/μL", "lab", priority=1.8),
            _make_evidence("merged_node_cd4_004", "CD4+ T淋巴细胞计数 < 50/μL", "lab", priority=1.6),
            _make_evidence("merged_node_lab_401", "CMV DNA阳性", "lab", priority=1.5),
            _make_evidence("merged_node_img_401", "胸部CT提示磨玻璃影", "imaging", priority=1.4),
        ],
    )

    result = GraphCaseGenerator().generate_from_records([record])
    exam_case = next(case for case in result.cases if case.metadata.get("case_type") == "exam_driven")

    positive_names = list(exam_case.metadata.get("selected_positive_slots") or [])
    cd4_names = [name for name in positive_names if "CD4" in name]
    truth_cd4_names = [
        truth.aliases[0]
        for truth in exam_case.slot_truth_map.values()
        if truth.aliases and "CD4" in truth.aliases[0]
    ]

    assert cd4_names == ["CD4+ T淋巴细胞计数 < 50/μL"]
    assert truth_cd4_names == ["CD4+ T淋巴细胞计数 < 50/μL"]
    assert "CMV DNA阳性" in positive_names
    assert "胸部CT提示磨玻璃影" in positive_names


# 验证 CD4 family 过滤不会误删其他检查结果。
def test_generator_keeps_non_cd4_exam_results_when_filtering_cd4() -> None:
    record = _make_record(
        "merged_node_cd40002",
        "CD4保留其他检查病",
        [
            _make_evidence("merged_node_cd4_101", "CD4+ T淋巴细胞计数 < 300/μL", "lab", priority=1.7),
            _make_evidence("merged_node_cd4_102", "CD4+ T淋巴细胞计数 < 200/μL", "lab", priority=1.8),
            _make_evidence("merged_node_cd4_103", "CD4+ T淋巴细胞计数 < 50/μL", "lab", priority=1.6),
            _make_evidence("merged_node_hivrna_101", "HIV RNA < 50 copies/mL", "lab", priority=1.5),
            _make_evidence("merged_node_lab_402", "β-D葡聚糖升高", "lab", priority=1.4),
            _make_evidence("merged_node_img_402", "胸部CT提示磨玻璃影", "imaging", priority=1.3),
        ],
    )

    result = GraphCaseGenerator().generate_from_records([record])
    exam_case = next(case for case in result.cases if case.metadata.get("case_type") == "exam_driven")
    positive_names = list(exam_case.metadata.get("selected_positive_slots") or [])

    assert sum(1 for name in positive_names if "CD4" in name) == 1
    assert "HIV RNA < 50 copies/mL" in positive_names
    assert "β-D葡聚糖升高" in positive_names
    assert "胸部CT提示磨玻璃影" in positive_names


# 验证 HIV RNA / 病毒载量 family 最多只保留一个阳性槽位。
def test_generator_filters_hiv_rna_family_to_one_positive_slot() -> None:
    record = _make_record(
        "merged_node_hivrna0001",
        "病毒载量互斥病",
        [
            _make_evidence("merged_node_hivrna_201", "HIV RNA < 50 copies/mL", "lab", priority=1.7),
            _make_evidence("merged_node_hivrna_202", "HIV RNA >1000 copies/mL", "lab", priority=1.6),
            _make_evidence("merged_node_hivrna_203", "HIV病毒载量未受抑制", "lab", priority=1.5),
            _make_evidence("merged_node_lab_403", "CMV DNA阳性", "lab", priority=1.4),
            _make_evidence("merged_node_img_403", "胸部CT提示磨玻璃影", "imaging", priority=1.3),
        ],
    )

    result = GraphCaseGenerator().generate_from_records([record])
    exam_case = next(case for case in result.cases if case.metadata.get("case_type") == "exam_driven")
    positive_names = list(exam_case.metadata.get("selected_positive_slots") or [])
    hiv_rna_names = [name for name in positive_names if ("HIV RNA" in name or "病毒载量" in name)]

    assert len(hiv_rna_names) == 1
    assert "CMV DNA阳性" in positive_names
    assert "胸部CT提示磨玻璃影" in positive_names


# 验证 LDL family 去重时不会误删 TG / 总胆固醇 / imaging 等其他 family。
def test_generator_filters_lipid_family_without_dropping_other_families() -> None:
    record = _make_record(
        "merged_node_lipid0001",
        "血脂互斥病",
        [
            _make_evidence("merged_node_ldl_001", "LDL-C ≥ 1.8 mmol/L", "lab", priority=1.6),
            _make_evidence("merged_node_ldl_002", "LDL-C ≥ 3.0 mmol/L", "lab", priority=1.5),
            _make_evidence("merged_node_tg_001", "甘油三酯 >= 1.7 mmol/L", "lab", priority=1.4),
            _make_evidence("merged_node_tc_001", "总胆固醇升高", "lab", priority=1.3),
            _make_evidence("merged_node_img_404", "胸部CT提示磨玻璃影", "imaging", priority=1.2),
        ],
    )

    result = GraphCaseGenerator().generate_from_records([record])
    exam_case = next(case for case in result.cases if case.metadata.get("case_type") == "exam_driven")
    positive_names = list(exam_case.metadata.get("selected_positive_slots") or [])
    ldl_names = [name for name in positive_names if "LDL" in name or "低密度脂蛋白胆固醇" in name]

    assert len(ldl_names) == 1
    assert "甘油三酯 >= 1.7 mmol/L" in positive_names
    assert "总胆固醇升高" in positive_names
    assert "胸部CT提示磨玻璃影" in positive_names


# 验证 eGFR family 会保留最严重的阈值，而不会误删其他检查证据。
def test_generator_filters_egfr_family_to_most_severe_threshold() -> None:
    record = _make_record(
        "merged_node_egfr0001",
        "肾功能互斥病",
        [
            _make_evidence("merged_node_egfr_001", "eGFR < 90 mL/min", "lab", priority=1.8),
            _make_evidence("merged_node_egfr_002", "eGFR < 60 mL·min⁻¹·1.73 m⁻²", "lab", priority=1.7),
            _make_evidence("merged_node_egfr_003", "eGFR < 30 mL·min⁻¹·1.73 m⁻²", "lab", priority=1.6),
            _make_evidence("merged_node_lab_404", "CMV DNA阳性", "lab", priority=1.5),
            _make_evidence("merged_node_img_405", "胸部CT提示磨玻璃影", "imaging", priority=1.4),
        ],
    )

    result = GraphCaseGenerator().generate_from_records([record])
    exam_case = next(case for case in result.cases if case.metadata.get("case_type") == "exam_driven")
    positive_names = list(exam_case.metadata.get("selected_positive_slots") or [])
    egfr_names = [name for name in positive_names if "eGFR" in name]

    assert egfr_names == ["eGFR < 30 mL·min⁻¹·1.73 m⁻²"]
    assert "CMV DNA阳性" in positive_names
    assert "胸部CT提示磨玻璃影" in positive_names


# 验证 opening 会过滤掉不适合患者主动暴露的 detail 槽位。
def test_generator_filters_non_patient_friendly_opening_slots() -> None:
    record = _make_record(
        "merged_node_opening0001",
        "骨代谢病",
        [
            _make_evidence("merged_node_det_101", "骨密度测量部位", "detail", priority=2.0),
            _make_evidence("merged_node_det_102", "减重持续时间", "detail", priority=1.9),
            _make_evidence("merged_node_sym_201", "发热", "symptom", priority=1.8),
            _make_evidence("merged_node_sym_202", "干咳", "symptom", priority=1.7),
        ],
    )

    result = GraphCaseGenerator().generate_from_records([record])
    low_cost_case = next(case for case in result.cases if case.metadata.get("case_type") == "low_cost")
    opening_names = list(low_cost_case.metadata.get("opening_slot_names") or [])

    assert "骨密度测量部位" not in opening_names
    assert "减重持续时间" not in opening_names
    assert any(name in opening_names for name in ("发热", "干咳"))


# 验证被 opening 过滤掉的证据若仍在正向证据中，会保留在 truth map 且保持按问再答。
def test_generator_keeps_filtered_opening_slots_in_truth_map() -> None:
    record = _make_record(
        "merged_node_exam0003",
        "检查项保留病",
        [
            _make_evidence("merged_node_lab_201", "CD4+ T淋巴细胞计数", "lab", priority=2.0, relation_specificity=0.9),
            _make_evidence("merged_node_lab_202", "CMV DNA阳性", "lab", priority=1.9, relation_specificity=0.95),
            _make_evidence("merged_node_img_201", "胸部CT提示磨玻璃影", "imaging", priority=1.8, relation_specificity=0.92),
        ],
    )

    result = GraphCaseGenerator().generate_from_records([record])
    exam_case = next(case for case in result.cases if case.metadata.get("case_type") == "exam_driven")
    opening_names = list(exam_case.metadata.get("opening_slot_names") or [])
    cd4_truth = exam_case.slot_truth_map["merged_node_lab_201"]

    assert "CD4+ T淋巴细胞计数" not in opening_names
    assert cd4_truth.aliases == ["CD4+ T淋巴细胞计数"]
    assert cd4_truth.reveal_only_if_asked is True


# 验证 opening 会排除 LabTest、Pathogen、ClinicalAttribute 等非病人自然开场槽位。
def test_generator_opening_excludes_labtest_pathogen_clinical_attribute() -> None:
    record = _make_record(
        "merged_node_opening0002",
        "opening标签过滤病",
        [
            _make_evidence(
                "merged_node_labtest_001",
                "CD4+ T淋巴细胞计数",
                "lab",
                priority=2.0,
                target_label="LabTest",
            ),
            _make_evidence("merged_node_path_302", "巨细胞病毒", "pathogen", priority=1.9),
            _make_evidence(
                "merged_node_attr_001",
                "骨密度测量部位",
                "detail",
                priority=1.8,
                target_label="ClinicalAttribute",
            ),
            _make_evidence("merged_node_sym_301", "发热", "symptom", priority=1.7),
            _make_evidence("merged_node_sym_302", "干咳", "symptom", priority=1.6),
        ],
    )

    result = GraphCaseGenerator().generate_from_records([record])
    ordinary_case = next(case for case in result.cases if case.metadata.get("case_type") == "ordinary")
    opening_names = list(ordinary_case.metadata.get("opening_slot_names") or [])

    assert "发热" in opening_names
    assert "CD4+ T淋巴细胞计数" not in opening_names
    assert "巨细胞病毒" not in opening_names
    assert "骨密度测量部位" not in opening_names
    assert ordinary_case.slot_truth_map["merged_node_labtest_001"].reveal_only_if_asked is True
    assert ordinary_case.slot_truth_map["merged_node_attr_001"].reveal_only_if_asked is True


# 验证预后/疗效/统计类 ClinicalFinding 不会进入 opening。
def test_generator_opening_excludes_prognosis_treatment_statistical_findings() -> None:
    record = _make_record(
        "merged_node_opening0003",
        "预后统计过滤病",
        [
            _make_evidence("merged_node_find_001", "AIDS相关病死率高", "symptom", priority=2.0),
            _make_evidence("merged_node_find_002", "临床症状无改善", "symptom", priority=1.9),
            _make_evidence("merged_node_find_003", "体征无改善", "symptom", priority=1.8),
            _make_evidence("merged_node_find_004", "咳嗽", "symptom", priority=1.7),
        ],
    )

    result = GraphCaseGenerator().generate_from_records([record])
    low_cost_case = next(case for case in result.cases if case.metadata.get("case_type") == "low_cost")
    opening_names = list(low_cost_case.metadata.get("opening_slot_names") or [])

    assert "咳嗽" in opening_names
    assert "AIDS相关病死率高" not in opening_names
    assert "临床症状无改善" not in opening_names
    assert "体征无改善" not in opening_names


# 验证检查驱动 opening 允许具体检查结果，但不会主动暴露纯病原体名。
def test_generator_uses_exam_result_as_opening_but_not_plain_pathogen() -> None:
    record = _make_record(
        "merged_node_exam0004",
        "CMV相关病",
        [
            _make_evidence("merged_node_lab_301", "CMV DNA阳性", "lab", priority=2.0, relation_specificity=0.95),
            _make_evidence("merged_node_path_301", "巨细胞病毒", "pathogen", priority=1.9, relation_specificity=0.9),
            _make_evidence("merged_node_img_301", "胸部CT提示磨玻璃影", "imaging", priority=1.8, relation_specificity=0.9),
        ],
    )

    result = GraphCaseGenerator().generate_from_records([record])
    exam_case = next(case for case in result.cases if case.metadata.get("case_type") == "exam_driven")
    opening_names = list(exam_case.metadata.get("opening_slot_names") or [])

    assert "CMV DNA阳性" in opening_names
    assert "巨细胞病毒" not in opening_names


# 验证可按四类病例各抽固定数量，供人工抽样检查。
def test_build_case_type_sample_payload_draws_fixed_count_per_type() -> None:
    cases = []
    for case_type in ("ordinary", "low_cost", "exam_driven", "competitive"):
        for index in range(6):
            cases.append(
                VirtualPatientCase(
                    case_id=f"{case_type}_{index:02d}",
                    title=f"{case_type}_{index}",
                    chief_complaint="测试主诉",
                    metadata={
                        "case_type": case_type,
                        "disease_id": f"merged_node_{case_type}_{index:02d}",
                        "disease_name": f"{case_type}_{index}",
                    },
                )
            )

    payload = build_case_type_sample_payload(cases, sample_size_per_type=5, seed=7)

    assert payload["sampled_case_count"] == 20
    assert payload["requested_case_types"] == ["ordinary", "low_cost", "exam_driven", "competitive"]
    for case_type in ("ordinary", "low_cost", "exam_driven", "competitive"):
        assert len(payload["sampled_cases_by_type"][case_type]) == 5


# 验证抽样结果可渲染成人工检查用 Markdown 摘要。
def test_render_case_type_sample_markdown_includes_core_fields() -> None:
    payload = build_case_type_sample_payload(
        [
            VirtualPatientCase(
                case_id="ordinary_001",
                title="普通病例 1",
                chief_complaint="测试主诉",
                behavior_style="cooperative",
                red_flags=["呼吸困难"],
                hidden_slots=["高危性行为"],
                metadata={
                    "case_type": "ordinary",
                    "disease_id": "merged_node_ordinary001",
                    "disease_name": "普通病",
                    "opening_slot_names": ["发热", "干咳"],
                    "selected_positive_slots": ["发热", "干咳"],
                    "selected_negative_slots": ["盗汗"],
                    "evidence_counts_by_group": {"symptom": 2, "risk": 1},
                },
                slot_truth_map={
                    "merged_node_slot1": SlotTruth(node_id="merged_node_slot1", value=True, aliases=["发热"]),
                    "merged_node_slot2": SlotTruth(node_id="merged_node_slot2", value=False, aliases=["盗汗"]),
                },
            ),
            VirtualPatientCase(
                case_id="low_cost_001",
                title="低成本病例 1",
                chief_complaint="测试主诉",
                metadata={"case_type": "low_cost", "disease_id": "merged_node_low001", "disease_name": "低成本病"},
            ),
            VirtualPatientCase(
                case_id="exam_driven_001",
                title="检查驱动病例 1",
                chief_complaint="测试主诉",
                metadata={
                    "case_type": "exam_driven",
                    "disease_id": "merged_node_exam001",
                    "disease_name": "检查病",
                },
            ),
            VirtualPatientCase(
                case_id="competitive_001",
                title="竞争病例 1",
                chief_complaint="测试主诉",
                metadata={
                    "case_type": "competitive",
                    "disease_id": "merged_node_comp001",
                    "disease_name": "竞争病",
                },
            ),
        ]
        * 5,
        sample_size_per_type=5,
        seed=11,
    )

    markdown = render_case_type_sample_markdown(payload)

    assert "# 图谱病例抽样检查摘要" in markdown
    assert "## ordinary" in markdown
    assert "opening_slot_names: 发热、干咳" in markdown
    assert "slot_truth_negative: 盗汗" in markdown
