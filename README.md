# GraduationDesign

本项目面向 HIV/AIDS 场景的智能问诊系统建设，当前已完成第一阶段知识图谱链路从“全量指南图谱”到“问诊搜索专用图谱”的收缩，并已经把第二阶段“问诊大脑”和“虚拟病人”推进到可做真实端到端联调的阶段。

## 当前阶段

当前工作可以分成三条活跃主线，并保留一个历史备份目录：

- `knowledge_graph/`：当前激活的第一阶段链路，负责医学资料清理、搜索专用图谱抽取、别名合并与 Neo4j 入库
- `knowledge_graph_bak/`：旧版全量指南知识图谱备份，已经废弃，仅用于历史对照
- `brain/`、`simulator/`：第二阶段，负责 Med-MCTS 风格问诊、虚拟病人生成与离线评测
- `frontend/`：中期检查演示层，负责 Streamlit 前端、回放模式与实时问诊模式展示

一句话概括当前状态：

- 第一阶段：当前活跃版本已切换为服务 `R1 / R2 / A3 / A4` 的搜索专用图谱；旧版治疗、推荐、证据链图谱已移至 `knowledge_graph_bak/`
- 第二阶段：已经进入“select -> expand -> simulate -> backpropagate 多次 rollout 可跑”的阶段，并已切换到 `LLM-first + 显式错误传播 + 集中 normalization` 的抽取 / 解释链路；当前 intake / A4 统一使用 `mention_state + resolution` 语义，不再把自述症状表述成“医学 certainty”
- 前端演示：已支持中文 Streamlit 页面，可展示多轮问诊、A1/A2/A3/A4、候选诊断、下一问、搜索摘要与安全机制

更详细的局部说明可分别查看：

- 第一阶段搜索专用知识图谱处理链：[knowledge_graph/README.md](/Users/loki/Workspace/GraduationDesign/knowledge_graph/README.md)
- 旧版全量指南图谱备份：[knowledge_graph_bak/README.md](/Users/loki/Workspace/GraduationDesign/knowledge_graph_bak/README.md)
- 第二阶段问诊大脑：[brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)
- 第二阶段问诊大脑详细运行链路指南：[brain_runtime_call_chain_guide.md](/Users/loki/Workspace/GraduationDesign/docs/brain_runtime_call_chain_guide.md)
- 虚拟病人与离线回放：[simulator/README.md](/Users/loki/Workspace/GraduationDesign/simulator/README.md)
- 图谱驱动虚拟病人详细方案：[virtual_patient_generation_scheme.md](/Users/loki/Workspace/GraduationDesign/docs/virtual_patient_generation_scheme.md)
- 前端演示界面：[frontend/README.md](/Users/loki/Workspace/GraduationDesign/frontend/README.md)
- 第二阶段测试：[tests/README.md](/Users/loki/Workspace/GraduationDesign/tests/README.md)

## 项目结构

```text
GraduationDesign/
├── HIV/                          # 原始医学资料
├── HIV_cleaned/                  # 清理后的资料输出
├── knowledge_graph/              # 当前激活的搜索专用知识图谱处理链
│   ├── aliases/
│   ├── scripts/neo4j_init.cypher
│   ├── clean_markdown.py
│   ├── pipeline.py
│   ├── repair_relations_with_llm.py       # 搜索专用孤立节点关系修补工具，非默认必要步骤
│   ├── collect_normalization_candidates.py
│   ├── merge_nodes_by_aliases.py
│   ├── import_merged_graph.py
│   ├── run_clean_markdown.sh
│   ├── run_pipeline.sh
│   ├── run_repair_relations_with_llm.sh
│   ├── run_collect_normalization_candidates.sh
│   ├── run_merge_nodes_by_aliases.sh
│   ├── run_import_merged_graph.sh
│   └── README.md
├── knowledge_graph_bak/          # 已废弃的旧版全量指南图谱备份
├── brain/                        # 第二阶段问诊大脑脚手架
├── simulator/                    # 虚拟病人、离线评测与图谱审计脚手架
│   ├── graph_audit.py            # 疾病级局部子图与疾病对差异证据审计
│   └── ...
├── frontend/                     # Streamlit 中期检查演示界面
│   ├── app.py                    # 前端入口
│   ├── ui_adapter.py             # 后端结果到 UI 展示字段的轻量适配层
│   ├── output_browser.py         # 历史实验输出浏览适配层
│   ├── config_loader.py          # 前端实时模式配置加载
│   ├── demo_cases.py             # 内置回放病例索引
│   ├── demo_replays/             # 可离线展示的示例问诊记录
│   └── README.md
├── configs/                      # 第二阶段配置
├── .streamlit/                   # Streamlit 本地运行配置
├── docs/                         # 设计与执行清单
├── scripts/                      # 第二阶段演示、图谱审计与工具脚本
│   ├── audit_disease_ego_graphs.py
│   ├── audit_differential_pairs.py
│   └── ...
├── tests/                        # 第二阶段单元测试脚手架
├── test/                         # 小范围试跑输入
├── test_outputs/                 # 中间产物与实验输出
├── output_graph_test.jsonl       # 当前抽取主结果
├── output_graph_test_errors.jsonl
└── README.md
```

## 第一阶段：搜索专用知识图谱处理链

第一阶段的当前活跃脚本整理在 [knowledge_graph](/Users/loki/Workspace/GraduationDesign/knowledge_graph)。这一版已经不再维护“全量医学指南图谱”，而是为问诊搜索树量身定制：支持 `R1` 候选诊断生成、`R2` 关键证据检索、`A3` 下一问构造和 `A4` 证据更新。

旧版全量指南图谱已经移到 [knowledge_graph_bak](/Users/loki/Workspace/GraduationDesign/knowledge_graph_bak)，该目录只作历史备份，不建议用于当前 Neo4j 入库、replay 或实时问诊。

主流程如下：

推荐一键入口：

```bash
./knowledge_graph/run_search_kg_pipeline.sh
```

该脚本默认读取 [HIV_cleaned](/Users/loki/Workspace/GraduationDesign/HIV_cleaned)，并把本轮结果写入：

```text
test_outputs/search_kg/search_kg_<timestamp>/
```

它会自动完成：

1. 大模型抽取 `nodes / edges`
2. 提取待统一名称
3. 按 alias 合并图谱

默认不会运行可选的 `repair_relations_with_llm.py` 孤立节点关系修补，也不会自动导入 Neo4j。确认合并图谱无误后，如需导入可执行：

```bash
IMPORT_TO_NEO4J=true ./knowledge_graph/run_search_kg_pipeline.sh
```

或导入最近一次构建结果：

```bash
./knowledge_graph/run_import_merged_graph.sh
```

如果要重建 Neo4j 中的当前图谱，可使用清库、导入、校验一体入口：

```bash
./knowledge_graph/run_reload_search_kg_neo4j.sh
```

校验报告会写到当前搜索图谱输出目录下的 `neo4j_validation_report.json`。

如果只是人工更新了某次输出目录下的 `aliases/`，无需重跑 LLM，可直接复用该轮抽取结果重新合并：

```bash
SEARCH_KG_OUTPUT_ROOT="/Users/loki/Workspace/GraduationDesign/test_outputs/search_kg/search_kg_20260413_231209" \
SKIP_EXTRACTION=true \
./knowledge_graph/run_search_kg_pipeline.sh
```

当前线上问诊搜索专用本体重点保留：

- 诊断候选：`Disease`，统一承载普通疾病、机会性感染、肿瘤、共病、综合征、并发症和可独立作为候选诊断的临床型
- 待验证证据：`ClinicalFinding`、`ClinicalAttribute`、`LabTest`、`LabFinding`、`ImagingFinding`、`Pathogen`
- 风险与人群：`RiskFactor`、`PopulationGroup`
- 搜索关系：`MANIFESTS_AS`、`HAS_LAB_FINDING`、`HAS_IMAGING_FINDING`、`HAS_PATHOGEN`、`DIAGNOSED_BY`、`REQUIRES_DETAIL`、`RISK_FACTOR_FOR`、`COMPLICATED_BY`、`APPLIES_TO`
- 证据获取元数据：证据节点可预留 `acquisition_mode` 和 `evidence_cost`，用于区分直接问诊证据与依赖化验、影像、病原检测的高成本证据

以下旧版全量指南图谱内容已经从当前抽取端移除：

- `Recommendation`、`Medication`、`DrugClass`、`TreatmentRegimen`、`PreventionStrategy`、`ManagementAction`
- `GuidelineDocument`、`GuidelineSection`、`EvidenceSpan`、`Assertion`
- `RECOMMENDS`、`TREATED_WITH`、`SUPPORTED_BY`、`HAS_EVIDENCE`、`SUBJECT`、`OBJECT` 等治疗/推荐/证据链关系

[repair_relations_with_llm.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/repair_relations_with_llm.py) 当前已改为搜索专用孤立节点关系修补器，仍然不作为默认必要步骤。若 Neo4j 校验或合并报告显示孤立节点偏高，可运行：

```bash
./knowledge_graph/run_repair_relations_with_llm.sh
```

该脚本默认读取最近一次 `test_outputs/search_kg/search_kg_<timestamp>/output_graph.jsonl`，只允许补充当前搜索本体中的诊断-证据关系，并会继续生成修补后的候选名称和 alias 合并图谱。修补结果默认写入当前搜索图谱目录下的 `relation_repair/`。

当前最新的已清理图谱版本是 `search_kg_20260419_125328` 的 `evidence_count <= 3` 剪枝、节点重分类、`aliases_le3/` 二次合并和 `disease_aliases.json` 单文件疾病节点修订版。该版本已删除旧审计中邻接证据数量小于等于 3 的疾病节点，按人工重分类清单修正节点标签，在全量 `aliases_le3/` 合并基础上仅按 `disease_aliases.json` 对 `Disease` 节点做删除、重命名和同名合并，并同步清理由此产生的非疾病孤立节点。

当前推荐的最终入库源是：

- [merged_graph_by_aliases_pruned_le3_reclassified_aliases_le3_disease_aliases_only_no_isolates.json](/Users/loki/Workspace/GraduationDesign/test_outputs/search_kg/search_kg_20260419_125328/relation_repair/alias_merge/merged_graph_by_aliases_pruned_le3_reclassified_aliases_le3_disease_aliases_only_no_isolates.json)

对应处理报告：

- [pruned_le3_no_isolates_report.json](/Users/loki/Workspace/GraduationDesign/test_outputs/search_kg/search_kg_20260419_125328/relation_repair/alias_merge/pruned_le3_no_isolates_report.json)
- [merged_graph_by_aliases_pruned_le3_no_isolates_reclassified_report.json](/Users/loki/Workspace/GraduationDesign/test_outputs/search_kg/search_kg_20260419_125328/relation_repair/alias_merge/merged_graph_by_aliases_pruned_le3_no_isolates_reclassified_report.json)
- [merged_graph_by_aliases_pruned_le3_reclassified_aliases_le3_no_isolates_report.json](/Users/loki/Workspace/GraduationDesign/test_outputs/search_kg/search_kg_20260419_125328/relation_repair/alias_merge/merged_graph_by_aliases_pruned_le3_reclassified_aliases_le3_no_isolates_report.json)
- [merged_graph_by_aliases_pruned_le3_reclassified_aliases_le3_disease_aliases_only_no_isolates_report.json](/Users/loki/Workspace/GraduationDesign/test_outputs/search_kg/search_kg_20260419_125328/relation_repair/alias_merge/merged_graph_by_aliases_pruned_le3_reclassified_aliases_le3_disease_aliases_only_no_isolates_report.json)

对应 Neo4j 校验报告：

- [neo4j_validation_report_pruned_le3_reclassified_aliases_le3_disease_aliases_only_no_isolates.json](/Users/loki/Workspace/GraduationDesign/test_outputs/search_kg/search_kg_20260419_125328/relation_repair/neo4j_validation_report_pruned_le3_reclassified_aliases_le3_disease_aliases_only_no_isolates.json)

该版本导入 Neo4j 后的预期计数为：

- `Disease = 80`
- `nodes = 1012`
- `relationships = 1725`
- `isolated_nodes = 0`

对应 Neo4j Desktop dump 尚未重新导出；下面这些是上一版 `pruned_le3_verified` 的历史 dump，不对应当前 `disease_aliases.json` 最新入库版：

- 推荐导入文件：[neo4j.dump](/Users/loki/Workspace/GraduationDesign/test_outputs/neo4j_dumps/search_kg_20260419_125328_pruned_le3_verified/neo4j.dump)
- 命名副本：[search_kg_20260419_125328_pruned_le3_verified.dump](/Users/loki/Workspace/GraduationDesign/test_outputs/neo4j_dumps/search_kg_20260419_125328_pruned_le3_verified/search_kg_20260419_125328_pruned_le3_verified.dump)
- dump manifest：[manifest.json](/Users/loki/Workspace/GraduationDesign/test_outputs/neo4j_dumps/search_kg_20260419_125328_pruned_le3_verified/manifest.json)
- 导入说明：[README_IMPORT.md](/Users/loki/Workspace/GraduationDesign/test_outputs/neo4j_dumps/search_kg_20260419_125328_pruned_le3_verified/README_IMPORT.md)

Neo4j 初始化脚本位于：

- [neo4j_init.cypher](/Users/loki/Workspace/GraduationDesign/knowledge_graph/scripts/neo4j_init.cypher)

## 疾病级知识图谱审计

在基于图谱自动生成结构化病例骨架之前，建议先做疾病级图谱审计。审计工具会按疾病导出 1-hop 邻接证据，并对疾病对生成 `shared / target_only / competitor_only / exam_pool` 差异证据报告。它的目标是先发现图谱里的缺边、错边、证据失衡和难以区分的疾病对，避免后续虚拟病例生成把图谱问题放大。

核心实现：

- [simulator/graph_audit.py](/Users/loki/Workspace/GraduationDesign/simulator/graph_audit.py)：复用 `GraphRetriever.retrieve_candidate_evidence_profile()`，执行局部证据导出、程序化规则审计、差异证据拆分、Markdown/JSON/LLM prompt 渲染
- [scripts/audit_disease_ego_graphs.py](/Users/loki/Workspace/GraduationDesign/scripts/audit_disease_ego_graphs.py)：单疾病或批量疾病局部子图审计入口
- [scripts/audit_differential_pairs.py](/Users/loki/Workspace/GraduationDesign/scripts/audit_differential_pairs.py)：主诊断 vs 竞争病差异证据审计入口

单疾病审计示例：

```bash
NEO4J_PASSWORD=你的密码 conda run -n GraduationDesign python scripts/audit_disease_ego_graphs.py \
  --disease-name 肺孢子菌肺炎 \
  --top-k 80
```

如果同名疾病节点不止一个，优先使用 node id 精确审计：

```bash
NEO4J_PASSWORD=你的密码 conda run -n GraduationDesign python scripts/audit_disease_ego_graphs.py \
  --disease-id merged_node_3597acb1ddfc \
  --top-k 80
```

批量审计所有候选疾病标签：

```bash
NEO4J_PASSWORD=你的密码 conda run -n GraduationDesign python scripts/audit_disease_ego_graphs.py \
  --all \
  --limit 200 \
  --top-k 80
```

疾病对差异审计示例：

```bash
NEO4J_PASSWORD=你的密码 conda run -n GraduationDesign python scripts/audit_differential_pairs.py \
  --target-name 肺孢子菌肺炎 \
  --competitor-name 结核病 \
  --top-k 80
```

默认输出目录：

- 单疾病审计：`test_outputs/graph_audit/disease_ego/`
- 疾病对审计：`test_outputs/graph_audit/differential_pairs/`

每次审计会同时输出：

- JSON：供后续自动病例骨架生成继续消费
- Markdown：供人工审阅或论文分析
- `.llm_prompt.md`：已嵌入审计报告的 LLM 语义复核 prompt

程序化规则审计当前覆盖：

- 标签 / 分组异常：证据 label 不在搜索主流程集合内、group 为空或非法
- `acquisition_mode / evidence_cost` 异常：例如 `LabFinding=direct_ask`、`ClinicalFinding=high`、`ImagingFinding` 未标记为 `needs_imaging/high`
- 关系异常：关系类型不在核心关系集合内、泛化关系占比过高
- 重复或疑似重复节点：同名节点、高相似名称节点
- 证据结构失衡：缺少 symptom、risk、exam，或 detail 占比过高
- 疾病对质量：缺少 target_only / competitor_only、shared 占比过高、exam_pool 为空、主诊断独有证据弱

更详细的说明见：

- [knowledge_graph/README.md](/Users/loki/Workspace/GraduationDesign/knowledge_graph/README.md)
- [knowledge_graph_bak/README.md](/Users/loki/Workspace/GraduationDesign/knowledge_graph_bak/README.md)

## 第二阶段：问诊大脑

第二阶段已经不再只是脚手架，而是具备了更贴近论文的最小可运行实现：

- 更详细的目录说明见：[brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)
- [brain/types.py](/Users/loki/Workspace/GraduationDesign/brain/types.py)：状态、候选问题、假设分数等核心数据结构
- [brain/state_tracker.py](/Users/loki/Workspace/GraduationDesign/brain/state_tracker.py)：会话状态追踪器
- [brain/session_dag.py](/Users/loki/Workspace/GraduationDesign/brain/session_dag.py)：会话内存 DAG / DFS 追问骨架
- [brain/neo4j_client.py](/Users/loki/Workspace/GraduationDesign/brain/neo4j_client.py)：Neo4j 查询封装
- [brain/retriever.py](/Users/loki/Workspace/GraduationDesign/brain/retriever.py)：冷启动、正向假设、反向验证检索，当前已支持 `R1 / R2` 方向语义权重与实体链接相似度融合
- [scripts/run_retriever_smoke.py](/Users/loki/Workspace/GraduationDesign/scripts/run_retriever_smoke.py)：真实 Neo4j 图谱联调脚本
- [brain/question_selector.py](/Users/loki/Workspace/GraduationDesign/brain/question_selector.py)：下一问打分与选择器
- [brain/mcts_engine.py](/Users/loki/Workspace/GraduationDesign/brain/mcts_engine.py)：基于 UCT 的动作与树节点选择器
- [brain/simulation_engine.py](/Users/loki/Workspace/GraduationDesign/brain/simulation_engine.py)：支持多分支浅层 rollout 的 simulation 预演器
- [brain/med_extractor.py](/Users/loki/Workspace/GraduationDesign/brain/med_extractor.py)：患者原话到 `(P, C)` 的结构化抽取层
- [brain/entity_linker.py](/Users/loki/Workspace/GraduationDesign/brain/entity_linker.py)：mention 到 KG 节点的阈值化链接器
- [brain/search_tree.py](/Users/loki/Workspace/GraduationDesign/brain/search_tree.py)：显式搜索树结构
- [brain/trajectory_evaluator.py](/Users/loki/Workspace/GraduationDesign/brain/trajectory_evaluator.py)：轨迹聚合与最终答案评分器
- [brain/stop_rules.py](/Users/loki/Workspace/GraduationDesign/brain/stop_rules.py)：终止与降级规则
- [brain/report_builder.py](/Users/loki/Workspace/GraduationDesign/brain/report_builder.py)：结构化结果汇总
- [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)：A1-A4 问诊编排层

## 当前与 Med-MCTS 的对齐状态

当前第二阶段不是完整论文复现，但已经完成了“结构对齐后的更贴近论文实现”：

- `MedExtractor`：已补
- `A1`：当前已切换为 LLM-first 抽取，长文本不再静默回退规则词典
- `A2`：已支持患者上下文 + R1 候选排序，并可保留 `recommended_next_evidence`
- `A3`：已支持 R2 检索、动作构造、区分性 gain 与问句生成
- `A4`：已支持目标感知 LLM 解释、LLM deductive judge 与显式路由
- `SearchTree + UCT + rollout`：已支持多次 rollout 的 `select -> expand -> simulate -> backpropagate`
- `TrajectoryEvaluator`：已支持路径聚类、相似度驱动 diversity 和可选 LLM verifier 模式
- `llm_verifier`：当前会对齐 stop rule 的最早接受窗口；在 `turn_index` 或 `trajectory_count` 尚未达到可停止条件前，会先延后 verifier 调用并退回轻量 fallback 评分，避免 competitive replay 在早期追问轮次反复支付高成本评审
- `rollout / reroot`：当前使用轻量 `SessionState` 快照，不再把 `search_tree`、`last_search_result` 等运行时大对象递归复制进树节点；这能显著降低 competitive replay 后期的内存膨胀与 GC 卡顿

当前仍未完成的重点：

- 更深层的 rollout
- 更稳定的真实多轮会话收敛
- 更贴近论文的最终答案聚类与评审策略
- 更严格的真实图谱联调与离线 benchmark

## 图谱驱动虚拟病人

当前虚拟病人模块已经不只是 seed case 脚手架，而是形成了“图谱审计 -> 病例骨架 -> 病人代理 -> 自动对战”的闭环。详细设计说明见：

- [virtual_patient_generation_scheme.md](/Users/loki/Workspace/GraduationDesign/docs/virtual_patient_generation_scheme.md)

当前相关核心文件包括：

- 更详细的目录说明见：[simulator/README.md](/Users/loki/Workspace/GraduationDesign/simulator/README.md)
- [simulator/case_schema.py](/Users/loki/Workspace/GraduationDesign/simulator/case_schema.py)
- [simulator/generate_cases.py](/Users/loki/Workspace/GraduationDesign/simulator/generate_cases.py)
- [simulator/graph_case_generator.py](/Users/loki/Workspace/GraduationDesign/simulator/graph_case_generator.py)
- [simulator/patient_agent.py](/Users/loki/Workspace/GraduationDesign/simulator/patient_agent.py)
- [simulator/replay_engine.py](/Users/loki/Workspace/GraduationDesign/simulator/replay_engine.py)
- [simulator/benchmark.py](/Users/loki/Workspace/GraduationDesign/simulator/benchmark.py)
- [simulator/path_cache_builder.py](/Users/loki/Workspace/GraduationDesign/simulator/path_cache_builder.py)
- [scripts/generate_graph_virtual_patients.py](/Users/loki/Workspace/GraduationDesign/scripts/generate_graph_virtual_patients.py)：基于疾病审计结果生成图谱驱动病例骨架
- [scripts/run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)：批量虚拟病人回放与评测入口

当前实现的要点包括：

- 图谱病例生成器直接消费疾病级图谱审计 JSON，而不是直接从 Neo4j 读全图
- 病例类型分为 `ordinary / low_cost / exam_driven / competitive`
- `slot_truth_map` 使用真实图谱 `target_node_id` 作为 key，中文证据名称写入 `aliases`
- 当前已同时支持输出 `cases.jsonl` 和便于人工查看的 `cases.json`
- `run_batch_replay.py` 当前可直接读取 `JSONL` 或 `JSON` 数组病例文件
- `run_batch_replay.py` 当前支持病例级并发，默认 `--case-concurrency 4`；并发时每个病例使用独立 brain 实例，避免共享 `StateTracker`
- 对于标准 batch replay，先做小样本 smoke 更合适；当前支持 `--limit 10` 只先跑前 10 个病例
- `run_batch_replay.py` 当前会直接向终端设备输出运行信息；即使通过 `conda run` 启动，也会在病例启动、病例完成和长时间运行期间持续输出可见日志
- `run_batch_replay.py` 当前会在终端持续输出病例级进度条，并每 15 秒输出一次心跳，例如“已完成病例：2 / 10，活动病例：2，当前最久：case_xxx（已运行 12:30）”
- `run_batch_replay.py` 当前会像前端实时模式一样自动读取 `configs/frontend.yaml` 与 `configs/frontend.local.yaml`，把 Neo4j / LLM / brain 配置桥接到当前 CLI 进程环境
- `run_batch_replay.py` 当前启动时会直接记录 `llm_available=true/false`；如果为 `false`，批量回放会尽早失败，不再退回旧规则链路
- `run_batch_replay.py` 当前会在每个病例完成后立即追加写入 `replay_results.jsonl`、`run.log`，并刷新 `benchmark_summary.json` 与 `status.json`
- `run_batch_replay.py` 当前已支持单病例 `failed` 语义：若 `brain` 抛出 LLM 领域错误，该病例会带 `error.code / error.stage / error.message / error.attempts` 落盘，其他病例继续运行
- `run_batch_replay.py` 默认支持断点续跑：若输出目录里已经有 `replay_results.jsonl`，会自动跳过已完成病例；如需强制重跑，可加 `--no-resume`
- `run_batch_replay.py` 当前会记录病例级耗时信息：每个病例的 opening、初始 brain、逐轮 patient/brain、finalize 和总耗时会写入 `replay_results.jsonl`，并在 `benchmark_summary.json` / `status.json` 中聚合 `timing_summary`；运行日志对亚秒级耗时会保留更高精度，避免全部显示成 `0.00`
- `simulator/replay_engine.py` 当前会先累计原始浮点耗时，再在落盘前统一 round；这能减少毫秒级病例里 `brain_turn_seconds_total` 被逐轮 round 放大的误导
- `run_batch_replay.py` 当前在 `Ctrl+C` / `SIGTERM` 中断时会先写入 `status.json` / `run.log`，再强制退出进程，避免 `ThreadPoolExecutor` 的并发 worker 持续占用大量内存
- `run_batch_replay.py` 当前输出的 `final_report.metadata` 已做轻量化处理，不再携带原始 `search_tree` 和 `last_search_result` 运行态对象，以降低批量回放的内存占用
- 病人代理当前已改为“骨架驱动开场”：首轮输入优先由 `patient_agent.open_case(case)` 基于 opening slots 生成，而不是直接把 `chief_complaint` 当作唯一入口
- `brain/service.py` 当前对主诉澄清增加了防重复保护：若已经追问过一次 `chief complaint` 但仍无任何可推理线索，会以 `repeated_chief_complaint_without_signal` 终止，避免 bad opening 在 intake 环节空转 8 轮
- `brain/med_extractor.py` 与 `brain/evidence_parser.py` 当前补了 competitive 病例常见症状 / 风险词典，并对字符串型 `clinical_features` 输出增加了容错；即使 LLM schema 返回较松，也不至于把整段特征直接丢掉
- `simulator/graph_case_generator.py` 当前会过滤 `competitive` 病例里 `HIV感染 / HIV感染者 / 抗逆转录病毒治疗 / 免疫功能低下` 这类背景 opening，并优先回退到目标病自己的症状、具体检查结果或疾病名
- 在配置了可用 LLM 时，病人代理会使用受约束的 LLM 表达；否则自动退回规则模板
- 当前 LLM 调用已支持显式 `enable_thinking` 开关，默认值为 `false`，避免依赖服务端默认行为

当前已经基于：

- [all_diseases_20260420_disease_aliases_only](/Users/loki/Workspace/GraduationDesign/test_outputs/graph_audit/all_diseases_20260420_disease_aliases_only)

生成了一轮正式图谱驱动病例输出：

- [cases.json](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260426_final/cases.json)
- [cases.jsonl](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260426_final/cases.jsonl)
- [manifest.json](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260426_final/manifest.json)
- [summary.md](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260426_final/summary.md)
- [sampled_cases_4x5.json](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260426_final/sampled_cases_4x5.json)
- [sampled_cases_4x5.md](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260426_final/sampled_cases_4x5.md)

当前这轮共生成：

- `ordinary = 66`
- `low_cost = 49`
- `exam_driven = 61`
- `competitive = 51`
- 总数 `227`

当前抽样检查默认使用固定随机种子重新抽取四类病例，各 `5` 条，共 `20` 条，便于在规则修复后快速复核：

- `opening_slot_names` 是否仍混入 `LabTest / Pathogen / ClinicalAttribute`
- `selected_positive_slots` 是否仍出现多个 `CD4` 阈值或 `HIV RNA / 病毒载量` 状态并列
- `selected_positive_slots` 是否仍出现多个 `LDL / HDL / TG / TC / eGFR` 同 family 项并列

当前 `replay_engine` 已经不再是占位文件，而是能够驱动 `brain/service.py` 跑通最小的“系统问 -> 病人答 -> 系统再问”自动回放闭环，并支持批量回放。

## 前端演示界面

前端演示层位于 [frontend](/Users/loki/Workspace/GraduationDesign/frontend)，采用 Streamlit + Python 实现，不引入 React/Vue 等前后端分离框架。页面以中文为主，面向中期检查现场展示，重点让老师能直接看到“系统不是普通聊天，而是在执行结构化问诊决策”。

主要展示内容：

- 左侧展示多轮问诊对话、患者输入、系统追问与最终结论
- 右侧展示当前会话状态、A1 关键线索提取、A2 候选诊断排序、A3 下一问选择、A4 回答解释与路由
- 搜索摘要展示 rollout 次数、树节点数量、当前 best answer、一致性、路径多样性与代理评估 / 复核器结果
- 安全机制模块展示复核器是否允许停止、安全接受闸门是否阻止停止、搜索树是否换根、缺失关键证据与竞争诊断信息
- 实验复盘模式可直接浏览 [test_outputs/simulator_replay](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay) 下的历史 replay / sweep / ablation 输出

启动方式：

```bash
conda activate GraduationDesign
streamlit run frontend/app.py
```

或使用仓库脚本：

```bash
./scripts/run_streamlit_realtime.sh
```

启动后通常访问：

```text
http://localhost:8501
```

如果 8501 被占用，以 Streamlit 终端输出的 `Local URL` 为准。也可以手动指定端口：

```bash
streamlit run frontend/app.py --server.port 8514
```

前端支持三种模式：

- 回放模式：从 [frontend/demo_replays](/Users/loki/Workspace/GraduationDesign/frontend/demo_replays) 加载预置 JSON 问诊记录，不依赖 Neo4j、DashScope 或环境变量，适合作为现场保底演示
- 实验复盘模式：扫描 [test_outputs/simulator_replay](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay)，可选择历史输出目录和病例，逐轮查看 repair、verifier、guarded gate、A4 evidence audit 等实验记录
- 实时运行模式：直接调用 [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py) 的默认构造和 `process_turn()`，需要 Neo4j 与 LLM 配置可用

实验复盘模式当前可识别：

- `focused_repair_summary.jsonl`：最适合逐轮复盘，包含 root best action、repair selected action、reject reason、reroot、A4 evidence audit 等字段
- `replay_results.jsonl`：普通虚拟病人 replay 输出，包含问答轮次和 final_report；其中 `final_report.metadata` 默认只保留轻量摘要，不再原样序列化整棵搜索树和完整搜索结果对象
- `ablation_summary.jsonl`：病例级 ablation 摘要
- `focused_metrics.json`、`ablation_metrics.json`、`benchmark_summary.json`、`profile_summary.tsv`：实验指标汇总
- `status.json`、`run.log`：运行状态与增量日志；标准 `batch replay` 现在也会持续更新这两份文件，便于断点续跑和前端查看运行进度，其中会包含当前活动病例以及病例级耗时统计

实时模式配置读取顺序：

- 默认读取 [configs/frontend.yaml](/Users/loki/Workspace/GraduationDesign/configs/frontend.yaml)
- 如需本地密钥，可复制 [configs/frontend.local.example.yaml](/Users/loki/Workspace/GraduationDesign/configs/frontend.local.example.yaml) 为 `configs/frontend.local.yaml`
- `configs/frontend.local.yaml` 已加入忽略列表，适合填写 `llm.api_key`、Neo4j 密码等本地敏感配置
- 前端运行时会把配置适配为现有后端所需的环境变量与 `config_overrides`，因此不需要在命令行手动 export 大量变量

实时模式依赖说明：

- Neo4j 需要已导入知识图谱，并能通过 `configs/frontend.yaml` 或 `configs/frontend.local.yaml` 连接
- LLM 默认使用 DashScope compatible OpenAI 接口与 `qwen3-max`
- 如果实时模式连接失败，页面仍可切换到回放模式完成完整展示；对于 LLM 领域错误，页面会直接显示结构化中文提示，而不是再伪装成“规则降级成功”
- 如果患者第一句只是“你好，医生”等无症状问候，系统会主动询问主诉
- 如果患者只提供“我正在发热”等单一线索，系统会通过冷启动追问补充关键症状或风险因素

## 配置、测试与文档

- [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)
- [configs/stop_rules.yaml](/Users/loki/Workspace/GraduationDesign/configs/stop_rules.yaml)
- [configs/simulator.yaml](/Users/loki/Workspace/GraduationDesign/configs/simulator.yaml)
- [configs/frontend.yaml](/Users/loki/Workspace/GraduationDesign/configs/frontend.yaml)：前端实时模式默认配置
- [configs/frontend.local.example.yaml](/Users/loki/Workspace/GraduationDesign/configs/frontend.local.example.yaml)：前端本地密钥配置模板
- [tests](/Users/loki/Workspace/GraduationDesign/tests)：第二阶段测试脚手架
- 更详细的目录说明见：[tests/README.md](/Users/loki/Workspace/GraduationDesign/tests/README.md)
- [tests/test_replay_engine.py](/Users/loki/Workspace/GraduationDesign/tests/test_replay_engine.py)
- [tests/test_mcts_engine.py](/Users/loki/Workspace/GraduationDesign/tests/test_mcts_engine.py)
- [tests/test_simulation_engine.py](/Users/loki/Workspace/GraduationDesign/tests/test_simulation_engine.py)
- [tests/test_generate_cases.py](/Users/loki/Workspace/GraduationDesign/tests/test_generate_cases.py)
- [tests/test_graph_case_generator.py](/Users/loki/Workspace/GraduationDesign/tests/test_graph_case_generator.py)
- [tests/test_benchmark.py](/Users/loki/Workspace/GraduationDesign/tests/test_benchmark.py)
- [phase2_execution_checklist.md](/Users/loki/Workspace/GraduationDesign/docs/phase2_execution_checklist.md)：第二阶段与虚拟病人开发清单
- [diagnosis_system_todolist.md](/Users/loki/Workspace/GraduationDesign/docs/diagnosis_system_todolist.md)：当前诊断系统待完善点与后续迭代顺序
- [phase2_changelog.md](/Users/loki/Workspace/GraduationDesign/docs/phase2_changelog.md)：第二阶段实现历程、问题改进与论文写作素材整理
- [virtual_patient_generation_scheme.md](/Users/loki/Workspace/GraduationDesign/docs/virtual_patient_generation_scheme.md)：图谱驱动虚拟病人详细方案、病例类型规则、骨架字段与论文写作素材整理
- [scripts/run_brain_demo.py](/Users/loki/Workspace/GraduationDesign/scripts/run_brain_demo.py)：最小命令行问诊演示入口
- [scripts/diagnose_smoke10_failures.py](/Users/loki/Workspace/GraduationDesign/scripts/diagnose_smoke10_failures.py)：对指定 replay 输出目录做 `med_extractor / A1` payload 审计，并生成 JSON / Markdown 诊断报告
- [scripts/run_streamlit_realtime.sh](/Users/loki/Workspace/GraduationDesign/scripts/run_streamlit_realtime.sh)：Streamlit 前端启动入口
- [.streamlit/config.toml](/Users/loki/Workspace/GraduationDesign/.streamlit/config.toml)：关闭 Streamlit 首次启动邮箱提示，提升演示稳定性

当前 [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml) 已会被 [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py) 的默认构造逻辑真正读取并映射到：

- `MctsEngine`
- `SimulationEngine`
- `TrajectoryEvaluator`
- `EntityLinker`
- `GraphRetriever`
- `EvidenceParser`
- `HypothesisManager`
- `StopRuleEngine`

补充说明：

- 全局 README 主要说明整体结构与阶段划分
- [knowledge_graph/README.md](/Users/loki/Workspace/GraduationDesign/knowledge_graph/README.md) 说明当前搜索专用知识图谱处理链
- [knowledge_graph_bak/README.md](/Users/loki/Workspace/GraduationDesign/knowledge_graph_bak/README.md) 说明已废弃的旧版全量指南图谱备份
- [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md) 说明第二阶段问诊大脑目录结构与文件职责
- [simulator/README.md](/Users/loki/Workspace/GraduationDesign/simulator/README.md) 说明虚拟病人与离线回放目录结构与文件职责
- [frontend/README.md](/Users/loki/Workspace/GraduationDesign/frontend/README.md) 说明 Streamlit 前端启动、回放模式、实时模式与配置方式
- [tests/README.md](/Users/loki/Workspace/GraduationDesign/tests/README.md) 说明第二阶段测试组织方式与当前覆盖范围
- [phase2_changelog.md](/Users/loki/Workspace/GraduationDesign/docs/phase2_changelog.md) 重点记录第二阶段各轮改进分别解决了什么问题，适合作为论文写作材料
- [diagnosis_system_todolist.md](/Users/loki/Workspace/GraduationDesign/docs/diagnosis_system_todolist.md) 记录当前诊断系统仍待完善的点，适合作为后续实现顺序与回归目标清单

## 当前环境

- 推荐 conda 环境：`GraduationDesign`
- 推荐 Python：`3.10.x`
- 当前项目已使用并确认过的核心依赖：
  - `openai`
  - `neo4j`
  - `langchain`
  - `langchain_community`
  - `streamlit`

建议先执行：

```bash
conda activate GraduationDesign
```

## 测试与 Smoke

推荐测试命令：

```bash
conda run -n GraduationDesign python -m pytest -q
```

说明：

- 测试数会随第二阶段和前端适配迭代变化，建议以实际 `pytest` 输出为准
- 直接运行 `conda run -n GraduationDesign pytest -q` 在部分环境下可能出现导入路径问题
- 因此建议统一使用 `python -m pytest`

真实 Neo4j smoke：

```bash
NEO4J_PASSWORD=你的密码 conda run -n GraduationDesign python scripts/run_retriever_smoke.py --features 发热,干咳
```

真实 Neo4j 疾病级图谱审计 smoke：

```bash
NEO4J_PASSWORD=你的密码 conda run -n GraduationDesign python scripts/audit_disease_ego_graphs.py \
  --disease-name 肺孢子菌肺炎 \
  --top-k 40 \
  --output-root test_outputs/graph_audit/smoke_disease

NEO4J_PASSWORD=你的密码 conda run -n GraduationDesign python scripts/audit_differential_pairs.py \
  --target-name 肺孢子菌肺炎 \
  --competitor-name 结核病 \
  --top-k 40 \
  --output-root test_outputs/graph_audit/smoke_pair
```

真实端到端 smoke：

```bash
export DASHSCOPE_API_KEY="你的 key"
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}"
export OPENAI_MODEL="${OPENAI_MODEL:-qwen3-max}"
export NEO4J_PASSWORD="你的密码"

conda run --no-capture-output -n GraduationDesign python scripts/run_batch_replay.py --max-turns 5 --case-concurrency 4
```

这条端到端 smoke 会走：

- 真实 Neo4j 图谱检索
- `brain/service.py` 的默认构造逻辑
- `A1 -> A2 -> A3/A4 -> search -> report`
- 虚拟病人自动回放

真实 focused baseline ablation：

```bash
./scripts/run_baseline_focused_ablation.sh
```

说明：

- 默认跑 `pcp_typical_001,pcp_vague_001,concealing_risk_001`
- 默认只跑 `baseline` 变体，`MAX_TURNS=5`
- 如未提前设置 `DASHSCOPE_API_KEY`，脚本会在启动时静默提示输入
- 输出目录默认为 `test_outputs/simulator_replay/focused_ablation_baseline_<timestamp>`
- 运行中可查看 `run.log` 与 `status.json` 判断是否仍在跑、是否出错
- 最近一次输出目录会写入 `test_outputs/simulator_replay/latest_baseline_ablation_output.txt`

真实 focused acceptance sweep：

```bash
./scripts/run_acceptance_sweep.sh
```

说明：

- 默认在 baseline repair 策略下扫 `MAX_TURNS_SWEEP=3,5,7`
- 默认扫 `STOP_PROFILES=baseline,relaxed_thresholds`
- 因此默认会跑 `3 个 turn budget * 2 个 stop profile * 3 个病例 = 18 个真实病例回放`，每轮会调用真实 Neo4j 与 qwen3-max verifier，通常会明显慢于单次 baseline
- 可选设置 `ACCEPTANCE_PROFILES=baseline,slightly_lenient,guarded_lenient` 对比 verifier 接受倾向
- 输出目录默认为 `test_outputs/simulator_replay/acceptance_sweep_<timestamp>`
- 运行中可看 `run.log`、`status.json`、`current_combo.txt` 和 `sweep_results.jsonl`
- 最近一次 sweep 输出目录会写入 `test_outputs/simulator_replay/latest_acceptance_sweep_output.txt`

如果只想先快速确认链路，可以缩小 sweep：

```bash
MAX_TURNS_SWEEP=3 STOP_PROFILES=baseline ./scripts/run_acceptance_sweep.sh
```

真实 verifier acceptance sweep：

```bash
./scripts/run_verifier_acceptance_sweep.sh
```

说明：

- 默认固定 `MAX_TURNS=5`、`stop_profile=baseline`
- 默认只比较 verifier 接受倾向：`ACCEPTANCE_PROFILES=baseline,slightly_lenient,guarded_lenient`
- 默认 `CASE_CONCURRENCY=5`，同一 profile 内最多 5 个病例并行回放；如需保守串行可设置 `CASE_CONCURRENCY=1`
- 输出目录默认为 `test_outputs/simulator_replay/verifier_acceptance_sweep_<timestamp>`
- 运行中可看 `run.log`、`status.json`、`current_profile.txt`、`sweep_results.jsonl` 和 `profile_summary.tsv`
- 最近一次 sweep 输出目录会写入 `test_outputs/simulator_replay/latest_verifier_acceptance_sweep_output.txt`
- 该脚本重点观察 `verifier_called_count`、`accepted_with_verifier_metadata_count`、`accepted_without_verifier_metadata_count`、`accepted_on_turn1_count`、`wrong_accept_on_turn1_count`
- 同时继续观察 `first_correct_best_answer_turn`、`first_verifier_accept_turn`、`first_verifier_accept_turn_for_final_answer`、`correct_but_rejected_span`、`accepted_correct_count`、`accepted_wrong_count`
- 新增 guarded safety 指标：`wrong_accept_reason_counts`、`final_answer_changed_after_first_accept_count`、`accepted_after_negative_key_evidence_count`、`accepted_after_recent_hypothesis_switch_count`、`accepted_with_nonempty_alternative_candidates_count`
- 继续观察 gate 协同指标：`guarded_block_reason_counts`、`verifier_positive_but_gate_rejected_count`、`accept_candidate_without_confirmed_combo_count`
- 每个 profile 的 `baseline/guarded_gate_audit.jsonl` 会逐条记录 verifier positive 但 guarded gate 拒绝的 turn，包括 `block_reason`、已确认证据 family、缺失 family、强替代候选、最近关键证据状态，以及 hard/soft negative evidence 分层
- 节点级归因指标会统计 `guarded_negative_evidence_node_counts`、`guarded_negative_evidence_family_counts`、`guarded_negative_evidence_tier_counts`、`guarded_negative_evidence_scope_counts`，用于定位到底是哪几个节点把 guarded 接受率压低
- missing-family-first repair 指标会统计 `missing_family_first_selected_count`、`combo_anchor_selected_before_turn3_count`、`family_recorded_after_question_count`，用于区分“缺口没被问到”和“问到了但没进 confirmed family”
- `guarded_lenient` 会对 PCP、结核、真菌性肺部感染等高混淆呼吸道诊断全程要求 confirmed key evidence，并对 PCP 使用有限组合模板闸门，如影像+免疫/实验室、影像+病原/PCP-specific、影像+氧合+免疫、影像+典型呼吸道表现+免疫

真实 focused acceptance validation：

```bash
./scripts/run_focused_acceptance_validation.sh
```

说明：

- 默认使用 [focused_acceptance_cases.jsonl](/Users/loki/Workspace/GraduationDesign/simulator/focused_acceptance_cases.jsonl) 的 10 个核心病例
- 默认固定 `MAX_TURNS=5`、`stop_profile=baseline`、repair 策略不变
- 默认只比较 `ACCEPTANCE_PROFILES=baseline,slightly_lenient,guarded_lenient`
- 输出目录默认为 `test_outputs/simulator_replay/focused_acceptance_validation_<timestamp>`
- 最近一次输出目录会写入 `test_outputs/simulator_replay/latest_focused_acceptance_validation_output.txt`
- 重点观察 `accepted_correct_count`、`accepted_wrong_count`、`wrong_accept_on_turn1_count`、`wrong_accept_reason_counts`、`accept_reason_counts`、`median_first_verifier_accept_turn`、`median_first_verifier_accept_turn_for_final_answer`

## 推荐下一步

如果继续推进第二阶段，建议开发顺序如下：

1. 先运行 `./scripts/run_focused_acceptance_validation.sh`，验证 10 个 focused cases 上 `baseline`、`slightly_lenient` 与 `guarded_lenient` 的接受表现
2. 重点比较 `accepted_correct_count`、`accepted_wrong_count`、`wrong_accept_on_turn1_count`、`wrong_accept_reason_counts` 与 guarded safety 指标
3. 如果 `guarded_lenient` 能保留正确接受提升且压低 turn1 错误接受，再考虑把它作为新的默认 acceptance profile 候选
4. 继续扩展 `simulator/generate_cases.py` 的病例覆盖面和行为风格
5. 在 `benchmark.py` 中继续固化更多质量指标
6. 最后再推进更深层的 rollout 与路径缓存
