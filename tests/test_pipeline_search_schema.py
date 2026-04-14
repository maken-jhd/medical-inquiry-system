"""测试搜索专用知识图谱抽取 schema。"""

import pytest

from knowledge_graph.pipeline import (
    ALLOWED_ACQUISITION_MODES,
    ALLOWED_EDGE_TYPES,
    ALLOWED_EVIDENCE_COSTS,
    ALLOWED_LABELS,
    Chunk,
    ExtractionValidationError,
    repair_acquisition_metadata,
    validate_extraction_result,
)


def _chunk() -> Chunk:
    return Chunk(
        chunk_id="chunk_test",
        relative_path="test.md",
        document_title="test",
        heading_path=["test"],
        line_start=1,
        line_end=1,
        text="",
        char_count=0,
    )


# 验证 pipeline 本体已经收缩为问诊搜索树消费的标签，而不是全指南图谱标签。
def test_pipeline_schema_uses_search_only_labels() -> None:
    assert "RiskBehavior" in ALLOWED_LABELS
    assert "ImagingFinding" in ALLOWED_LABELS
    assert "Recommendation" not in ALLOWED_LABELS
    assert "Medication" not in ALLOWED_LABELS
    assert "GuidelineDocument" not in ALLOWED_LABELS
    assert "ExposureScenario" not in ALLOWED_LABELS


# 验证 pipeline 关系只保留 R1/R2/A3/A4 需要的诊断问诊关系。
def test_pipeline_schema_uses_search_only_relations() -> None:
    assert "HAS_IMAGING_FINDING" in ALLOWED_EDGE_TYPES
    assert "HAS_PATHOGEN" in ALLOWED_EDGE_TYPES
    assert "RISK_FACTOR_FOR" in ALLOWED_EDGE_TYPES
    assert "RECOMMENDS" not in ALLOWED_EDGE_TYPES
    assert "TREATED_WITH" not in ALLOWED_EDGE_TYPES
    assert "SUPPORTED_BY" not in ALLOWED_EDGE_TYPES


# 验证抽取端为后续低成本/高成本问诊排序预留了节点级证据获取元数据。
def test_pipeline_schema_allows_acquisition_metadata() -> None:
    assert "direct_ask" in ALLOWED_ACQUISITION_MODES
    assert "needs_lab_test" in ALLOWED_ACQUISITION_MODES
    assert "needs_imaging" in ALLOWED_ACQUISITION_MODES
    assert "needs_pathogen_test" in ALLOWED_ACQUISITION_MODES
    assert "low" in ALLOWED_EVIDENCE_COSTS
    assert "high" in ALLOWED_EVIDENCE_COSTS

    payload = {
        "nodes": [
            {
                "id": "symptom_fever",
                "label": "Symptom",
                "name": "发热",
                "weight": 0.8,
                "detail_required": "minimal",
                "acquisition_mode": "direct_ask",
                "evidence_cost": "low",
            }
        ],
        "edges": [],
    }

    validate_extraction_result(payload, _chunk())


# 验证缺失 acquisition metadata 时，repair 会按证据标签补齐简单默认值。
def test_acquisition_metadata_repair_adds_label_defaults() -> None:
    payload = {
        "nodes": [
            {
                "id": "symptom_cough",
                "label": "Symptom",
                "name": "干咳",
                "weight": 0.7,
                "detail_required": "minimal",
            },
            {
                "id": "imaging_ground_glass",
                "label": "ImagingFinding",
                "name": "胸部 CT 磨玻璃影",
                "weight": 0.9,
                "detail_required": "standard",
            },
            {
                "id": "lab_cd4_low",
                "label": "LabFinding",
                "name": "CD4+ T 淋巴细胞计数 < 200/μL",
                "weight": 0.9,
                "detail_required": "standard",
                "attributes": {
                    "test_id": "LabTest_CD4_count",
                    "operator": "<",
                    "value": 200,
                    "unit": "cells/μL",
                },
            },
        ],
        "edges": [],
    }

    repaired = repair_acquisition_metadata(payload, _chunk())
    nodes_by_id = {node["id"]: node for node in repaired["nodes"]}

    assert nodes_by_id["symptom_cough"]["acquisition_mode"] == "direct_ask"
    assert nodes_by_id["symptom_cough"]["evidence_cost"] == "low"
    assert nodes_by_id["imaging_ground_glass"]["acquisition_mode"] == "needs_imaging"
    assert nodes_by_id["imaging_ground_glass"]["evidence_cost"] == "high"
    assert nodes_by_id["lab_cd4_low"]["acquisition_mode"] == "needs_lab_test"
    assert nodes_by_id["lab_cd4_low"]["evidence_cost"] == "high"
    validate_extraction_result(repaired, _chunk())


# 验证模型如果把 acquisition metadata 放入 attributes，repair 会提升到节点顶层保留。
def test_acquisition_metadata_repair_promotes_attribute_values() -> None:
    payload = {
        "nodes": [
            {
                "id": "lab_pcr_positive",
                "label": "LabFinding",
                "name": "BAL 肺孢子菌 PCR 阳性",
                "weight": 0.9,
                "detail_required": "standard",
                "attributes": {
                    "test_id": "LabTest_PJP_PCR",
                    "operator": "positive",
                    "value_text": "阳性",
                    "acquisition_mode": "病原学",
                    "evidence_cost": "高成本",
                },
            }
        ],
        "edges": [],
    }

    repaired = repair_acquisition_metadata(payload, _chunk())
    node = repaired["nodes"][0]

    assert node["acquisition_mode"] == "needs_pathogen_test"
    assert node["evidence_cost"] == "high"
    assert "acquisition_mode" not in node["attributes"]
    assert "evidence_cost" not in node["attributes"]
    validate_extraction_result(repaired, _chunk())


# 验证 ImagingFinding 成为一等搜索证据，可通过 HAS_IMAGING_FINDING 连接到疾病。
def test_pipeline_accepts_imaging_finding_edges() -> None:
    payload = {
        "nodes": [
            {
                "id": "disease_pcp",
                "label": "Disease",
                "name": "肺孢子菌肺炎 (PCP)",
                "weight": 0.95,
                "detail_required": "standard",
            },
            {
                "id": "imaging_ground_glass",
                "label": "ImagingFinding",
                "name": "双肺弥漫磨玻璃影",
                "weight": 0.9,
                "detail_required": "standard",
            },
        ],
        "edges": [
            {
                "id": "edge_pcp_imaging",
                "type": "HAS_IMAGING_FINDING",
                "source_id": "disease_pcp",
                "target_id": "imaging_ground_glass",
                "weight": 0.9,
                "detail_required": "standard",
            }
        ],
    }

    validate_extraction_result(payload, _chunk())


# 验证旧的全指南型 Recommendation 会被 schema 拒绝。
def test_pipeline_rejects_recommendation_nodes() -> None:
    payload = {
        "nodes": [
            {
                "id": "rec_1",
                "label": "Recommendation",
                "name": "推荐意见 1",
                "weight": 0.5,
                "detail_required": "minimal",
            }
        ],
        "edges": [],
    }

    with pytest.raises(ExtractionValidationError):
        validate_extraction_result(payload, _chunk())
