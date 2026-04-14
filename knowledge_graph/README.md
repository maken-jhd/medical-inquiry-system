# Knowledge Graph Pipeline（搜索专用版）

本目录是当前激活的知识图谱抽取端。它已经从旧版“全量医学指南知识图谱”收缩为“问诊搜索专用图谱”，目标不是完整存档指南知识，而是直接服务第二阶段问诊搜索树。

旧版全量指南图谱已迁移到 [knowledge_graph_bak](/Users/loki/Workspace/GraduationDesign/knowledge_graph_bak)，仅作为历史备份和对照资料，不再作为当前推荐抽取链路。

## 当前定位

当前图谱只围绕问诊主链路建模：

- `A1`：患者原话线索抽取后，需要把症状、风险、检查等线索链接到图谱节点
- `A2`：根据症状、风险因素、影像、实验室或病原线索生成候选诊断
- `A3`：根据候选诊断反向检索关键待验证证据，并构造下一问
- `A4`：根据患者回答更新证据状态、假设分数和后续路由

因此当前抽取器优先支持：

- `R1`：症状 / 风险 / 检查 / 影像 / 病原线索 -> 候选疾病
- `R2`：候选疾病 -> 关键待验证证据
- 下一问构造：把临床证据转换成适合问诊的问题
- 安全接受闸门：为 `imaging`、`oxygenation`、`immune_status`、`pathogen`、`pcp_specific` 等 evidence family 提供更稳定的图谱来源
- 证据获取方式预留：为证据节点保留 `acquisition_mode` 和 `evidence_cost`，方便后续区分“可直接询问”和“依赖检查”的下一问

当前不再抽取或维护：

- 用药方案
- 治疗推荐
- 预防策略
- 指南条目编号
- 文档证据链
- recommendation / assertion 级别的存档图谱

## 当前推荐主流程

当前推荐把 [pipeline.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/pipeline.py) 作为搜索专用抽取主入口：

1. 清理原始 Markdown
   - 入口：[run_clean_markdown.sh](/Users/loki/Workspace/GraduationDesign/knowledge_graph/run_clean_markdown.sh)
   - 脚本：[clean_markdown.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/clean_markdown.py)

2. 抽取搜索专用 `nodes / edges`
   - 入口：[run_pipeline.sh](/Users/loki/Workspace/GraduationDesign/knowledge_graph/run_pipeline.sh)
   - 脚本：[pipeline.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/pipeline.py)

3. 提取待统一名称
   - 入口：[run_collect_normalization_candidates.sh](/Users/loki/Workspace/GraduationDesign/knowledge_graph/run_collect_normalization_candidates.sh)
   - 脚本：[collect_normalization_candidates.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/collect_normalization_candidates.py)

4. 人工维护 `aliases/`
   - 目录：[aliases](/Users/loki/Workspace/GraduationDesign/knowledge_graph/aliases)

5. 按 alias 合并图谱
   - 入口：[run_merge_nodes_by_aliases.sh](/Users/loki/Workspace/GraduationDesign/knowledge_graph/run_merge_nodes_by_aliases.sh)
   - 脚本：[merge_nodes_by_aliases.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/merge_nodes_by_aliases.py)

6. 导入 Neo4j
   - 入口：[run_import_merged_graph.sh](/Users/loki/Workspace/GraduationDesign/knowledge_graph/run_import_merged_graph.sh)
   - 脚本：[import_merged_graph.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/import_merged_graph.py)

说明：

- [repair_relations_with_llm.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/repair_relations_with_llm.py) 是可选的搜索专用孤立节点关系修补器，当前不作为默认必要步骤。
- 如果 Neo4j 校验或合并报告显示孤立节点偏高，可通过 [run_repair_relations_with_llm.sh](/Users/loki/Workspace/GraduationDesign/knowledge_graph/run_repair_relations_with_llm.sh) 对最近一次搜索图谱输出进行补边、重新提取候选名称并重新 alias 合并。
- 当前 repair 只允许补充搜索本体关系，不会生成旧的治疗、推荐或文档证据链关系。
- [scripts/neo4j_init.cypher](/Users/loki/Workspace/GraduationDesign/knowledge_graph/scripts/neo4j_init.cypher) 可用于初始化 Neo4j 约束和索引；历史遗留索引不代表当前抽取器会继续生成旧标签。

## 当前本体

### 节点标签

当前抽取器只保留问诊搜索树会消费的标签：

- `Disease`
- `DiseasePhase`
- `OpportunisticInfection`
- `Comorbidity`
- `SyndromeOrComplication`
- `Tumor`
- `Pathogen`
- `Symptom`
- `Sign`
- `ClinicalAttribute`
- `LabTest`
- `LabFinding`
- `ImagingFinding`
- `RiskFactor`
- `RiskBehavior`
- `PopulationGroup`

### 节点级证据获取元数据

当前抽取端为证据节点预留两个可选字段：

- `acquisition_mode`：证据通常如何获得
- `evidence_cost`：证据获取的相对成本

`acquisition_mode` 当前允许：

- `direct_ask`：患者可直接回答，例如发热、干咳、气促、盗汗、体重下降、高危性行为
- `history_known`：通常来自既往史或已知背景，例如 HIV 感染者、免疫抑制人群、孕产妇
- `needs_lab_test`：需要实验室检查，例如 CD4、HIV RNA、LDH、β-D 葡聚糖
- `needs_imaging`：需要影像检查，例如胸部 CT 磨玻璃影、空洞、粟粒样结节
- `needs_pathogen_test`：需要病原学检测，例如 BAL PCR、培养、抗原、核酸检测
- `needs_clinician_assessment`：需要医生查体或临床判断，例如听诊异常、体格检查发现

`evidence_cost` 当前允许：

- `low`
- `medium`
- `high`

默认倾向：

- `Symptom`、`Sign`、`RiskBehavior`、`RiskFactor`：通常为 `direct_ask / low`
- `PopulationGroup`：通常为 `history_known / low`
- `LabFinding`、大部分 `LabTest`：通常为 `needs_lab_test / high`
- `ImagingFinding`：通常为 `needs_imaging / high`
- `Pathogen` 或病原学阳性证据：通常为 `needs_pathogen_test / high`
- `ClinicalAttribute`：根据名称启发式判断，普通问诊细节偏 `direct_ask / low`，检查量化属性偏高成本检查型

这两个字段目前只作为抽取端和数据结构层面的预留，当前搜索算法不会直接消费它们。

### 关系类型

当前抽取器只保留或新增以下关系：

- `MANIFESTS_AS`：疾病 -> 症状 / 体征 / 临床表现
- `HAS_LAB_FINDING`：疾病 -> 实验室发现
- `HAS_IMAGING_FINDING`：疾病 -> 影像学发现
- `HAS_PATHOGEN`：疾病 -> 病原体或病原线索
- `DIAGNOSED_BY`：疾病 -> 关键检查 / 诊断依据
- `REQUIRES_DETAIL`：疾病或症状 -> 需要继续追问的临床细节
- `RISK_FACTOR_FOR`：风险因素 / 风险行为 -> 疾病
- `COMPLICATED_BY`：疾病 -> 并发症 / 综合征 / 肿瘤
- `APPLIES_TO`：疾病或证据 -> 适用人群

## 与问诊搜索树的对齐

当前图谱抽取端和第二阶段检索语义的对应关系如下：

- `Symptom`、`Sign`、`ClinicalAttribute` 支持患者主诉、症状细节和 A3 追问
- `RiskFactor`、`RiskBehavior`、`PopulationGroup` 支持 HIV 风险史、免疫抑制背景和人群特征
- `LabTest`、`LabFinding` 支持 CD4、PaO2、β-D 葡聚糖、PCR 等实验室证据
- `ImagingFinding` 支持胸部 CT、磨玻璃影、空洞、粟粒样结节等影像证据
- `Pathogen` 支持 PCP、结核、真菌等病原学线索
- `Disease`、`OpportunisticInfection`、`Comorbidity`、`SyndromeOrComplication`、`Tumor` 支持候选诊断和鉴别诊断
- `REQUIRES_DETAIL` 用于生成“还需要问什么”的细节槽位
- `HAS_IMAGING_FINDING`、`HAS_PATHOGEN` 用于减少前端和 action builder 对关键词猜测的依赖

## 已移除的旧版本体

以下标签属于旧版全量指南图谱，当前搜索专用抽取端不再生成：

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

以下关系属于旧版治疗、推荐或证据链建模，当前不再使用：

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

如果新的抽取结果中再次大量出现这些旧标签或旧关系，通常说明跑错了旧链路、旧 prompt 或 [knowledge_graph_bak](/Users/loki/Workspace/GraduationDesign/knowledge_graph_bak) 里的历史脚本。

## 主要文件

- [clean_markdown.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/clean_markdown.py)：清理原始 Markdown，统一标题、空白、单位、符号和复杂表格表达
- [pipeline.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/pipeline.py)：当前搜索专用知识图谱抽取器，包含 schema constitution、抽取、校验、LabFinding repair、ImagingFinding / RiskBehavior 支持和 dangling edge 修复
- [collect_normalization_candidates.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/collect_normalization_candidates.py)：提取 `label -> name[]` 候选，供人工维护别名
- [merge_nodes_by_aliases.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/merge_nodes_by_aliases.py)：按 `aliases/` 规则合并节点并重写边
- [import_merged_graph.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/import_merged_graph.py)：将合并后的图谱导入 Neo4j
- [build_retrospective.md](/Users/loki/Workspace/GraduationDesign/knowledge_graph/build_retrospective.md)：第一阶段早期建设历程和旧链路复盘，阅读时需要注意其中部分内容对应旧版全量图谱

## 输入与输出

主要输入：

- 原始资料：[HIV](/Users/loki/Workspace/GraduationDesign/HIV)
- 清理后资料：[HIV_cleaned](/Users/loki/Workspace/GraduationDesign/HIV_cleaned)

主要输出：

- 原始抽取结果：[output_graph_test.jsonl](/Users/loki/Workspace/GraduationDesign/output_graph_test.jsonl)
- 抽取错误日志：[output_graph_test_errors.jsonl](/Users/loki/Workspace/GraduationDesign/output_graph_test_errors.jsonl)
- 待统一名称目录：[normalization_candidates](/Users/loki/Workspace/GraduationDesign/test_outputs/normalization_candidates)
- alias 合并输出目录：[alias_merge](/Users/loki/Workspace/GraduationDesign/test_outputs/alias_merge)
- 推荐入库源：[merged_graph_by_aliases.json](/Users/loki/Workspace/GraduationDesign/test_outputs/alias_merge/merged_graph_by_aliases.json)

## 运行方式

推荐先进入项目环境：

```bash
conda activate GraduationDesign
```

### 一键构建搜索专用图谱

当前最推荐的入口是：

```bash
./knowledge_graph/run_search_kg_pipeline.sh
```

这个脚本会按顺序执行：

1. 搜索专用图谱抽取
2. 候选名称提取
3. alias 合并

默认不会运行可选的 `repair_relations_with_llm.py` 孤立节点关系修补，也不会自动导入 Neo4j。

默认输出目录为：

```text
test_outputs/search_kg/search_kg_<timestamp>/
```

并会写入 latest 指针：

```text
test_outputs/search_kg/latest_output_dir.txt
```

主要输出包括：

- `output_graph.jsonl`
- `output_graph_errors.jsonl`
- `normalization_candidates/node_names_by_label.json`
- `alias_merge/merged_graph_by_aliases.json`
- `alias_merge/merged_graph_by_aliases_report.json`
- `run.log`
- `status.json`

如果确认合并图谱无误并希望导入 Neo4j，可以运行：

```bash
IMPORT_TO_NEO4J=true ./knowledge_graph/run_search_kg_pipeline.sh
```

也可以只导入最近一次构建结果：

```bash
./knowledge_graph/run_import_merged_graph.sh
```

如果需要先清空 Neo4j 旧图谱，再应用搜索专用 schema、导入新图谱并生成结构校验报告，使用：

```bash
./knowledge_graph/run_reload_search_kg_neo4j.sh
```

该脚本会执行：

1. 清空当前 Neo4j 数据库中的节点和关系
2. 应用 [neo4j_init.cypher](/Users/loki/Workspace/GraduationDesign/knowledge_graph/scripts/neo4j_init.cypher) 中的搜索专用约束和索引
3. 导入 `${SEARCH_KG_OUTPUT_ROOT}/alias_merge/merged_graph_by_aliases.json`
4. 输出 `${SEARCH_KG_OUTPUT_ROOT}/neo4j_validation_report.json`

如果已经有一轮抽取结果，只是人工整理了该轮目录下的 `aliases/`，可以不重跑 LLM，直接复用最新输出并重新合并：

```bash
SKIP_EXTRACTION=true ./knowledge_graph/run_search_kg_pipeline.sh
```

也可以指定某一次输出目录：

```bash
SEARCH_KG_OUTPUT_ROOT="/Users/loki/Workspace/GraduationDesign/test_outputs/search_kg/search_kg_20260413_231209" \
SKIP_EXTRACTION=true \
./knowledge_graph/run_search_kg_pipeline.sh
```

alias 读取优先级为：

1. 显式设置的 `ALIAS_DIR`
2. `${SEARCH_KG_OUTPUT_ROOT}/aliases`
3. [knowledge_graph/aliases](/Users/loki/Workspace/GraduationDesign/knowledge_graph/aliases)

因此你在 `test_outputs/search_kg/search_kg_20260413_231209/aliases/` 下人工维护的清单会优先被使用。

如需本地密钥或 Neo4j 密码，可新建 `configs/kg_pipeline.local.sh`，例如：

```bash
export DASHSCOPE_API_KEY="你的 key"
export OPENAI_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
export OPENAI_MODEL="qwen3-max"
export NEO4J_PASSWORD="你的 Neo4j 密码"
```

如果已经在 `configs/frontend.local.yaml` 中配置了 `llm.api_key`，`run_pipeline.sh` 也会尝试读取它作为 LLM key。

### 分步运行

按当前搜索专用链路运行：

```bash
./knowledge_graph/run_clean_markdown.sh
./knowledge_graph/run_pipeline.sh
./knowledge_graph/run_collect_normalization_candidates.sh
```

人工补充或检查 `aliases/` 后继续：

```bash
./knowledge_graph/run_merge_nodes_by_aliases.sh
./knowledge_graph/run_import_merged_graph.sh
```

抽取阶段支持按错误日志重试：

```bash
./knowledge_graph/run_pipeline.sh retry
```

## 维护原则

- 优先让图谱服务问诊搜索，而不是追求医学百科式完备。
- 新增标签和关系前，先确认 `brain/retriever.py`、`brain/action_builder.py` 或安全 gate 是否真的消费它们。
- 不要把治疗方案、用药推荐、指南证据链重新加回当前 schema。
- `RiskBehavior`、`ImagingFinding`、`Pathogen`、`ClinicalAttribute` 是当前搜索质量的关键标签，后续应优先保证抽取稳定性。
- `LabFinding` 仍然要求尽量结构化记录 `test_id`、`operator`、`value`、`value_text`、`unit` 和必要的 `reference_value_text`。
- 同义实体合并继续以人工维护 `aliases/` 为准，避免医学高风险实体被过度自动合并。
