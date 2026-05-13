---
description: "Use when editing the active search-only knowledge graph pipeline under knowledge_graph or its related audit and import scripts. Covers ontology limits, alias merge workflow, and Neo4j validation expectations."
name: "Search KG Pipeline"
applyTo:
  - "knowledge_graph/**/*.py"
  - "knowledge_graph/**/*.sh"
  - "scripts/audit_*.py"
  - "scripts/run_retriever_smoke.py"
---

# Search KG Pipeline

- 当前活跃链路只有 `knowledge_graph/`；`knowledge_graph_bak/` 只作历史参考，不要把旧版全量指南图谱重新接回当前抽取、导入或 replay。
- 当前搜索本体默认使用 `Disease` 诊断节点，以及 `ClinicalFinding`、`ClinicalAttribute`、`LabTest`、`LabFinding`、`ImagingFinding`、`Pathogen`、`RiskFactor`、`PopulationGroup` 等证据或人群标签。
- 除非用户明确要求，不要重新引入 `Recommendation`、`Medication`、`TreatmentRegimen`、`GuidelineDocument`、`EvidenceSpan`、`Assertion` 等旧标签和配套关系。
- 证据节点可以保留 `acquisition_mode` 和 `evidence_cost`，但不要顺手把这两个预留字段扩展成 `brain/` 搜索排序变更。
- 复用当前入口脚本与目录约定：优先 `run_search_kg_pipeline.sh`；已有人工维护 `aliases/` 时优先 `SEARCH_KG_OUTPUT_ROOT=... SKIP_EXTRACTION=true ...`。
- 自动生成病例骨架前，优先使用 `scripts/audit_disease_ego_graphs.py` 与 `scripts/audit_differential_pairs.py` 做局部审计；不要先把全图直接喂给 LLM。
- 本体细节与运行说明优先链接 [knowledge_graph/README.md](../../knowledge_graph/README.md) 和 [docs/search_kg_label_guide.md](../../docs/search_kg_label_guide.md)。