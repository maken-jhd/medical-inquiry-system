"""测试实体链接器能否将特征名称对齐到图谱节点。"""

from brain.entity_linker import EntityLinker
from brain.types import ClinicalFeatureItem


class FakeNeo4jClient:
    """使用固定查询结果模拟图谱节点候选。"""

    # 根据提及词返回一组固定候选节点。
    def run_query(self, query: str, params: dict | None = None) -> list[dict]:
        _ = query
        mention = (params or {}).get("mention", "")
        candidates = {
            "发热": {
                "node_id": "symptom_fever",
                "label": "ClinicalFinding",
                "canonical_name": "发热",
                "aliases": ["发烧", "高热"],
            },
            "免疫功能低下": {
                "node_id": "risk_immune_low",
                "label": "RiskFactor",
                "canonical_name": "免疫功能低下",
                "aliases": ["免疫力低下", "免疫力差"],
            },
            "血清 β-D 葡聚糖升高": {
                "node_id": "lab_bdg_high",
                "label": "LabFinding",
                "canonical_name": "血清 β-D 葡聚糖升高",
                "aliases": ["血清1,3-β-D葡聚糖升高", "BDG升高"],
            },
            "乙型肝炎病毒感染": {
                "node_id": "disease_hbv",
                "label": "Disease",
                "canonical_name": "乙型肝炎病毒感染",
                "aliases": ["乙肝病毒感染", "HBV感染"],
            },
            "空腹血糖>=6.1mmol/L": {
                "node_id": "lab_fpg_high",
                "label": "ClinicalAttribute",
                "canonical_name": "空腹血糖>=6.1mmol/L",
                "aliases": ["空腹血糖超过6.1"],
            },
            "甘油三酯 >= 1.7 mmol/L": {
                "node_id": "lab_tg_high",
                "label": "ClinicalAttribute",
                "canonical_name": "甘油三酯 >= 1.7 mmol/L",
                "aliases": ["甘油三酯大于1.7"],
            },
            "CD4+ T淋巴细胞计数 < 200/μL": {
                "node_id": "lab_cd4_low",
                "label": "LabFinding",
                "canonical_name": "CD4+ T淋巴细胞计数 < 200/μL",
                "aliases": ["CD4低", "CD4+T细胞计数偏低"],
            },
            "下肢麻木": {
                "node_id": "symptom_lower_limb_numbness",
                "label": "ClinicalFinding",
                "canonical_name": "下肢麻木",
                "aliases": ["下肢发麻"],
            },
            "双足麻木": {
                "node_id": "symptom_feet_numbness",
                "label": "ClinicalFinding",
                "canonical_name": "双足麻木",
                "aliases": ["双足发麻"],
            },
            "使用利奈唑胺": {
                "node_id": "drug_linezolid_use",
                "label": "ClinicalAttribute",
                "canonical_name": "使用利奈唑胺",
                "aliases": ["利奈唑胺使用"],
            },
        }

        if mention in candidates:
            return [candidates[mention]]

        return []


# 验证实体链接器会输出可信的最佳匹配结果。
def test_entity_linker_returns_trusted_match() -> None:
    linker = EntityLinker(FakeNeo4jClient())

    results = linker.link_mentions(["发热"])

    assert len(results) == 1
    assert results[0].node_id == "symptom_fever"
    assert results[0].canonical_name == "发热"
    assert results[0].is_trusted is True
    assert results[0].metadata["top_matches"][0]["canonical_name"] == "发热"


# 验证临床特征链接只消费 mention_state=present 的提及项。
def test_entity_linker_links_only_present_clinical_features() -> None:
    linker = EntityLinker(FakeNeo4jClient())

    results = linker.link_clinical_features(
        [
            ClinicalFeatureItem(name="发烧", normalized_name="发热", category="symptom", mention_state="present"),
            ClinicalFeatureItem(name="无发热", normalized_name="发热", category="symptom", mention_state="absent"),
        ]
    )

    assert len(results) == 1
    assert results[0].mention == "发热"


# 验证 CD4 口语化低值表达会扩展到低值 LabFinding，而不是停在模糊 CD4 检查名。
def test_entity_linker_expands_cd4_low_mention() -> None:
    linker = EntityLinker(FakeNeo4jClient())

    results = linker.link_mentions(["CD4+T细胞计数偏低"])

    assert len(results) == 1
    assert results[0].node_id == "lab_cd4_low"
    assert results[0].canonical_name == "CD4+ T淋巴细胞计数 < 200/μL"
    assert results[0].is_trusted is True
    assert "CD4+ T淋巴细胞计数 < 200/μL" in results[0].metadata["expanded_mentions"]
    assert results[0].metadata["raw_mention"] == "CD4+T细胞计数偏低"


# 验证患者自然表达能经模板扩展落到图谱规范节点，并保留扩展链路元数据。
def test_entity_linker_expands_patient_expression_variants() -> None:
    linker = EntityLinker(FakeNeo4jClient())

    results = linker.link_mentions(["下肢发麻", "双足发麻", "利奈唑胺使用"])

    assert [item.node_id for item in results] == [
        "symptom_lower_limb_numbness",
        "symptom_feet_numbness",
        "drug_linezolid_use",
    ]
    assert all(item.is_trusted for item in results)
    assert results[0].metadata["matched_mention"] == "下肢麻木"
    assert results[1].metadata["matched_mention"] == "双足麻木"
    assert results[2].metadata["matched_mention"] == "使用利奈唑胺"
    assert results[2].metadata["link_source"] == "template"


# 验证常见医学口语表达会泛化扩展到图谱中的可用证据节点。
def test_entity_linker_expands_common_medical_surface_forms() -> None:
    linker = EntityLinker(FakeNeo4jClient())

    results = linker.link_mentions(
        [
            "高烧不退",
            "免疫力比较差",
            "血清1,3-β-D葡聚糖超过80 pg/mL",
            "乙肝病毒感染阳性",
            "空腹血糖超过6.1",
            "甘油三酯大于1.7",
        ]
    )

    assert [item.node_id for item in results] == [
        "symptom_fever",
        "risk_immune_low",
        "lab_bdg_high",
        "disease_hbv",
        "lab_fpg_high",
        "lab_tg_high",
    ]
    assert all(item.is_trusted for item in results)
    assert results[0].metadata["expansion_rule"] in {"high_fever", "alias_normalization"}
    assert results[2].metadata["matched_mention"] == "血清 β-D 葡聚糖升高"
    assert "bdg_high" in results[2].metadata["expansion_rules"]
    assert results[3].metadata["expansion_rule"] in {"hbv_infection", "alias_normalization"}
