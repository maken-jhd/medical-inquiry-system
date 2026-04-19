"""测试疾病级图谱审计的规则检查与差异证据拆分。"""

from simulator.graph_audit import (
    DiseaseNode,
    audit_differential_rules,
    audit_evidence_rules,
    build_group_summary,
    render_llm_prompt,
    split_differential_evidence,
)


def _item(
    node_id: str,
    name: str,
    label: str,
    group: str,
    relation_type: str,
    *,
    priority: float = 1.0,
    relation_specificity: float = 0.85,
    acquisition_mode: str = "direct_ask",
    evidence_cost: str = "low",
) -> dict:
    return {
        "target_node_id": node_id,
        "target_name": name,
        "target_label": label,
        "group": group,
        "relation_type": relation_type,
        "priority": priority,
        "relation_specificity": relation_specificity,
        "relation_weight": 0.8,
        "acquisition_mode": acquisition_mode,
        "evidence_cost": evidence_cost,
    }


# 验证 LabFinding / ImagingFinding 的 acquisition/cost 异常会被程序规则抓出。
def test_graph_audit_flags_acquisition_and_cost_mismatch() -> None:
    disease = DiseaseNode("d1", "肺孢子菌肺炎", "Disease")
    evidence = [
        _item(
            "lab1",
            "CD4 低",
            "LabFinding",
            "lab",
            "HAS_LAB_FINDING",
            acquisition_mode="direct_ask",
            evidence_cost="low",
        ),
        _item(
            "img1",
            "磨玻璃影",
            "ImagingFinding",
            "imaging",
            "HAS_IMAGING_FINDING",
            acquisition_mode="needs_lab_test",
            evidence_cost="low",
        ),
    ]

    issues = audit_evidence_rules(disease, evidence, build_group_summary(evidence))
    codes = {item.code for item in issues}

    assert "lab_direct_ask_mismatch" in codes
    assert "imaging_acquisition_mismatch" in codes
    assert "imaging_cost_mismatch" in codes


# 验证同名不同 ID 能进入 shared evidence，避免差异报告把 alias 漏判成独有证据。
def test_graph_audit_splits_shared_by_normalized_name() -> None:
    target = [
        _item("t1", "发热", "Symptom", "symptom", "MANIFESTS_AS"),
        _item(
            "t2",
            "胸部CT磨玻璃影",
            "ImagingFinding",
            "imaging",
            "HAS_IMAGING_FINDING",
            acquisition_mode="needs_imaging",
            evidence_cost="high",
        ),
    ]
    competitor = [
        _item("c1", "发热", "Symptom", "symptom", "MANIFESTS_AS"),
        _item(
            "c2",
            "抗酸染色阳性",
            "LabFinding",
            "lab",
            "DIAGNOSED_BY",
            acquisition_mode="needs_lab_test",
            evidence_cost="high",
        ),
    ]

    shared, target_only, competitor_only = split_differential_evidence(target, competitor)

    assert [item["target_name"] for item in shared] == ["发热"]
    assert [item["target_name"] for item in target_only] == ["胸部CT磨玻璃影"]
    assert [item["target_name"] for item in competitor_only] == ["抗酸染色阳性"]


# 验证疾病对审计会提示缺少检查池和缺少主诊断独有证据。
def test_graph_audit_pair_rules_flag_empty_exam_pool_and_target_only() -> None:
    issues = audit_differential_rules(
        shared=[_item("s1", "发热", "Symptom", "symptom", "MANIFESTS_AS")],
        target_only=[],
        competitor_only=[_item("c1", "盗汗", "Symptom", "symptom", "MANIFESTS_AS")],
        exam_pool=[],
    )
    codes = {item.code for item in issues}

    assert "missing_target_only_evidence" in codes
    assert "empty_exam_pool" in codes


# 验证 LLM prompt 模板会嵌入报告并要求结构化输出。
def test_graph_audit_llm_prompt_contains_report_and_schema() -> None:
    prompt = render_llm_prompt("# 测试报告\n\n| 证据 |")

    assert "# 测试报告" in prompt
    assert "overall_judgement" in prompt
    assert "suspicious_evidence" in prompt

