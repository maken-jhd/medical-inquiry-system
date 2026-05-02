"""测试症状证据族分类与疾病最低证据组建议。"""

from __future__ import annotations

from simulator.evidence_family_catalog import (
    build_disease_evidence_catalog,
    build_disease_symptom_catalog,
    classify_evidence_families,
    classify_symptom_families,
    infer_evidence_group,
    suggest_minimum_evidence_groups,
)


# 验证常见症状可以被归入器官系统或病程线索证据族。
def test_classify_symptom_families_maps_common_clinical_findings() -> None:
    assert classify_symptom_families({"symptom_name": "活动后气促"}) == ["respiratory_symptom"]
    assert classify_symptom_families({"symptom_name": "抽搐伴意识障碍"}) == ["neurologic_symptom"]
    assert classify_symptom_families({"symptom_name": "症状持续加重"}) == ["worsening"]
    assert classify_symptom_families({"symptom_name": "不典型表现"}) == ["general_symptom"]


# 验证疾病最低症状证据组会优先选择更特异的 family，而不是只按数量堆叠全身症状。
def test_suggest_minimum_evidence_groups_prefers_specific_families() -> None:
    groups = suggest_minimum_evidence_groups(
        {
            "constitutional_symptom": 5,
            "respiratory_symptom": 1,
            "immune_status": 1,
            "general_symptom": 3,
        },
        max_groups_per_disease=2,
    )

    assert groups == [["immune_status"], ["respiratory_symptom"]]


# 验证 catalog 会同时输出症状节点归类和疾病级 minimum_evidence_groups。
def test_build_disease_symptom_catalog_adds_groups_to_each_disease() -> None:
    diseases = [
        {"disease_id": "d1", "disease_name": "肺孢子菌肺炎", "disease_label": "Disease"},
        {"disease_id": "d2", "disease_name": "中枢神经感染", "disease_label": "Disease"},
    ]
    edges = [
        {"disease_id": "d1", "symptom_id": "s1", "symptom_name": "干咳", "symptom_label": "ClinicalFinding"},
        {"disease_id": "d1", "symptom_id": "s2", "symptom_name": "发热", "symptom_label": "ClinicalFinding"},
        {"disease_id": "d2", "symptom_id": "s3", "symptom_name": "头痛", "symptom_label": "ClinicalFinding"},
        {"disease_id": "d2", "symptom_id": "s4", "symptom_name": "病情加重", "symptom_label": "ClinicalFinding"},
    ]

    catalog = build_disease_symptom_catalog(diseases, edges)
    disease_by_name = {item["disease_name"]: item for item in catalog["diseases"]}
    symptom_by_name = {item["symptom_name"]: item for item in catalog["symptom_nodes"]}

    assert symptom_by_name["干咳"]["families"] == ["respiratory_symptom"]
    assert disease_by_name["肺孢子菌肺炎"]["minimum_evidence_groups"][0] == ["respiratory_symptom"]
    assert disease_by_name["中枢神经感染"]["minimum_evidence_groups"][:2] == [
        ["worsening"],
        ["neurologic_symptom"],
    ]


# 验证 lab / imaging / pathogen / risk / detail 都能进入统一 evidence family 分类。
def test_classify_evidence_families_covers_non_symptom_groups() -> None:
    assert infer_evidence_group({"evidence_label": "LabFinding"}) == "lab"
    assert "immune_status" in classify_evidence_families(
        {"evidence_name": "CD4+ T淋巴细胞计数 < 200/μL", "evidence_label": "LabFinding"}
    )
    imaging_families = classify_evidence_families(
        {"evidence_name": "双肺弥漫性磨玻璃影", "evidence_label": "ImagingFinding"}
    )
    assert {"imaging", "pulmonary_imaging"}.issubset(set(imaging_families))
    pathogen_families = classify_evidence_families(
        {"evidence_name": "肺孢子菌", "evidence_label": "Pathogen"}
    )
    assert {"pathogen", "fungal_pathogen"}.issubset(set(pathogen_families))
    assert "art_or_reconstitution" in classify_evidence_families(
        {"evidence_name": "近期启动抗逆转录病毒治疗", "evidence_label": "RiskFactor"}
    )
    assert "onset_timing" in classify_evidence_families(
        {"evidence_name": "症状持续 2 周", "evidence_label": "ClinicalAttribute"}
    )


# 验证 full catalog 会按证据大组聚合，并给每个疾病生成全证据最低组建议。
def test_build_disease_evidence_catalog_adds_full_minimum_groups() -> None:
    diseases = [
        {"disease_id": "d1", "disease_name": "肺孢子菌肺炎", "disease_label": "Disease"},
    ]
    edges = [
        {
            "disease_id": "d1",
            "evidence_id": "s1",
            "evidence_name": "干咳",
            "evidence_label": "ClinicalFinding",
            "relation_type": "MANIFESTS_AS",
        },
        {
            "disease_id": "d1",
            "evidence_id": "l1",
            "evidence_name": "CD4+ T淋巴细胞计数 < 200/μL",
            "evidence_label": "LabFinding",
            "relation_type": "HAS_LAB_FINDING",
        },
        {
            "disease_id": "d1",
            "evidence_id": "l2",
            "evidence_name": "血清1,3-β-D葡聚糖 > 80 pg/mL",
            "evidence_label": "LabFinding",
            "relation_type": "HAS_LAB_FINDING",
        },
        {
            "disease_id": "d1",
            "evidence_id": "i1",
            "evidence_name": "胸部CT双肺弥漫性磨玻璃影",
            "evidence_label": "ImagingFinding",
            "relation_type": "HAS_IMAGING_FINDING",
        },
        {
            "disease_id": "d1",
            "evidence_id": "p1",
            "evidence_name": "肺孢子菌",
            "evidence_label": "Pathogen",
            "relation_type": "HAS_PATHOGEN",
        },
        {
            "disease_id": "d1",
            "evidence_id": "r1",
            "evidence_name": "HIV感染",
            "evidence_label": "RiskFactor",
            "relation_type": "RISK_FACTOR_FOR",
        },
        {
            "disease_id": "d1",
            "evidence_id": "d1_attr",
            "evidence_name": "症状持续加重",
            "evidence_label": "ClinicalAttribute",
            "relation_type": "REQUIRES_DETAIL",
        },
    ]

    catalog = build_disease_evidence_catalog(diseases, edges)
    disease = catalog["diseases"][0]

    assert catalog["evidence_node_count_by_group"]["lab"] == 2
    assert catalog["evidence_node_count_by_group"]["imaging"] == 1
    assert catalog["evidence_node_count_by_group"]["pathogen"] == 1
    assert disease["evidence_counts_by_group"] == {
        "detail": 1,
        "imaging": 1,
        "lab": 2,
        "pathogen": 1,
        "risk": 1,
        "symptom": 1,
    }
    assert "fungal_pathogen" in disease["evidence_family_coverage"]
    assert {tuple(group) for group in disease["minimum_evidence_groups_by_evidence_group"]["lab"]} == {
        ("immune_status",),
        ("fungal_marker",),
    }
    assert disease["minimum_evidence_groups_by_evidence_group"]["imaging"] == [["pulmonary_imaging"], ["imaging"]]
    assert disease["minimum_evidence_groups_by_evidence_group"]["pathogen"] == [["fungal_pathogen"], ["pathogen"]]
