# knowledge_graph_bak（已废弃）

本目录是旧版“全量医学指南知识图谱”链路的历史备份。它曾经用于从 HIV/AIDS 指南、专家共识和专题资料中抽取更完整的指南型图谱，包括治疗建议、用药方案、推荐条目、文档证据链和 assertion 级别结构。

当前项目已经切换为问诊搜索树服务的搜索专用知识图谱抽取端，活跃目录是 [knowledge_graph](/Users/loki/Workspace/GraduationDesign/knowledge_graph)。本目录不再作为当前推荐运行、导入或联调入口。

## 当前状态

- 状态：已废弃，保留为历史备份
- 用途：对照旧本体、查阅早期实现、必要时恢复历史逻辑
- 不建议：用于当前 Neo4j 入库、实时问诊、replay、ablation 或中期演示
- 当前活跃链路：[knowledge_graph/README.md](/Users/loki/Workspace/GraduationDesign/knowledge_graph/README.md)

如果后续 agent 或人工维护者需要改知识图谱抽取逻辑，应优先修改 [knowledge_graph/pipeline.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/pipeline.py)，不要直接在本目录上继续开发。

## 为什么废弃

旧版全量指南图谱的目标是“尽可能完整地存档指南知识”，因此会抽取很多当前问诊搜索树并不消费的对象，例如：

- 治疗方案
- 药物类别
- 预防策略
- 管理建议
- 推荐编号
- 文档章节
- 证据片段
- assertion 主谓宾结构

这些结构适合做医学指南知识库或治疗建议系统，但会给当前问诊搜索树带来额外噪声。当前系统的主线是：

- `A1`：抽取患者线索
- `A2`：生成候选诊断
- `A3`：检索关键待验证证据并选择下一问
- `A4`：解释患者回答、更新证据和假设

因此新版本只保留诊断候选生成和下一问选择真正需要的节点、边和校验逻辑。

## 旧版本体范围

旧版链路曾经包含但当前活跃抽取端已移除的节点标签包括：

- `GuidelineDocument`
- `GuidelineSection`
- `EvidenceSpan`
- `Assertion`
- `Recommendation`
- `Medication`
- `DrugClass`
- `TreatmentRegimen`
- `PreventionStrategy`
- `TransmissionRoute`
- `ManagementAction`
- `ExposureScenario`

旧版链路曾经包含但当前活跃抽取端已移除的关系类型包括：

- `RECOMMENDS`
- `TREATED_WITH`
- `CONSISTS_OF`
- `BELONGS_TO_CLASS`
- `PREVENTED_BY`
- `MONITORED_BY`
- `SCREENED_BY`
- `TRANSMITTED_VIA`
- `INTERACTS_WITH`
- `INITIATED_AFTER`
- `CONTRAINDICATED_IN`
- `NOT_RECOMMENDED_FOR`
- `HAS_SECTION`
- `HAS_EVIDENCE`
- `SUBJECT`
- `OBJECT`
- `SUPPORTED_BY`

这些旧本体不应被重新加入当前搜索专用抽取器，除非未来项目方向明确切回“指南知识库 / 治疗推荐系统”。

## 目录内容

本目录保留了旧版链路的主要文件：

- [pipeline.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph_bak/pipeline.py)：旧版全量指南图谱抽取器
- [repair_relations_with_llm.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph_bak/repair_relations_with_llm.py)：旧版关系修补脚本
- [collect_normalization_candidates.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph_bak/collect_normalization_candidates.py)：旧版候选名称提取脚本
- [merge_nodes_by_aliases.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph_bak/merge_nodes_by_aliases.py)：旧版 alias 合并脚本
- [import_merged_graph.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph_bak/import_merged_graph.py)：旧版 Neo4j 导入脚本
- [aliases](/Users/loki/Workspace/GraduationDesign/knowledge_graph_bak/aliases)：旧版本体下的 alias 规则
- [build_retrospective.md](/Users/loki/Workspace/GraduationDesign/knowledge_graph_bak/build_retrospective.md)：旧版建设历程和阶段复盘

## 使用提醒

- 不要把本目录的 `run_*.sh` 当作当前默认图谱构建入口。
- 不要把本目录生成的全量指南图谱直接导入当前问诊系统使用的 Neo4j。
- 如果只是为了比较旧本体和新本体，可以阅读本目录文件，但应以 [knowledge_graph](/Users/loki/Workspace/GraduationDesign/knowledge_graph) 为当前实现准线。
- 如果确实需要恢复旧版图谱，请先复制到独立实验分支或独立输出目录，避免覆盖当前搜索专用图谱产物。
