# GraduationDesign

本项目面向 HIV/AIDS 场景的智能问诊系统建设，当前已完成第一阶段的知识图谱底座整理，并已经搭好第二阶段“问诊大脑”和“虚拟病人”开发脚手架。

## 当前阶段

当前工作可以分成两条主线：

- `knowledge_graph/`：第一阶段，负责医学资料清理、图谱抽取、关系修补、别名合并与 Neo4j 入库
- `brain/`、`simulator/`：第二阶段脚手架，负责 FSM 状态机、图谱联动问诊、虚拟病人生成与离线评测

一句话概括当前状态：

- 第一阶段：已可跑通
- 第二阶段：已经进入“最小闭环可跑”的阶段，但还在持续补强真实联调和离线评测能力

## 项目结构

```text
GraduationDesign/
├── HIV/                          # 原始医学资料
├── HIV_cleaned/                  # 清理后的资料输出
├── knowledge_graph/              # 第一阶段知识图谱处理链
│   ├── aliases/
│   ├── scripts/neo4j_init.cypher
│   ├── clean_markdown.py
│   ├── pipeline.py
│   ├── repair_relations_with_llm.py
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
├── brain/                        # 第二阶段问诊大脑脚手架
├── simulator/                    # 虚拟病人与离线评测脚手架
├── configs/                      # 第二阶段配置
├── docs/                         # 设计与执行清单
├── scripts/                      # 第二阶段演示与工具脚本
├── tests/                        # 第二阶段单元测试脚手架
├── test/                         # 小范围试跑输入
├── test_outputs/                 # 中间产物与实验输出
├── output_graph_test.jsonl       # 当前抽取主结果
├── output_graph_test_errors.jsonl
└── README.md
```

## 第一阶段：知识图谱处理链

第一阶段的脚本已经全部整理到 [knowledge_graph](/Users/loki/Workspace/GraduationDesign/knowledge_graph)。

主流程如下：

1. 原始 Markdown 清理  
   入口：[run_clean_markdown.sh](/Users/loki/Workspace/GraduationDesign/knowledge_graph/run_clean_markdown.sh)

2. 大模型抽取 `nodes / edges`  
   入口：[run_pipeline.sh](/Users/loki/Workspace/GraduationDesign/knowledge_graph/run_pipeline.sh)

3. 定向关系修补  
   入口：[run_repair_relations_with_llm.sh](/Users/loki/Workspace/GraduationDesign/knowledge_graph/run_repair_relations_with_llm.sh)

4. 提取待统一名称  
   入口：[run_collect_normalization_candidates.sh](/Users/loki/Workspace/GraduationDesign/knowledge_graph/run_collect_normalization_candidates.sh)

5. 人工维护 `aliases/`  
   目录：[aliases](/Users/loki/Workspace/GraduationDesign/knowledge_graph/aliases)

6. 按 alias 合并图谱  
   入口：[run_merge_nodes_by_aliases.sh](/Users/loki/Workspace/GraduationDesign/knowledge_graph/run_merge_nodes_by_aliases.sh)

7. 导入 Neo4j  
   入口：[run_import_merged_graph.sh](/Users/loki/Workspace/GraduationDesign/knowledge_graph/run_import_merged_graph.sh)

最常用的最终入库源通常是：

- [merged_graph_by_aliases.json](/Users/loki/Workspace/GraduationDesign/test_outputs/alias_merge/merged_graph_by_aliases.json)

Neo4j 初始化脚本位于：

- [neo4j_init.cypher](/Users/loki/Workspace/GraduationDesign/knowledge_graph/scripts/neo4j_init.cypher)

更详细的说明见：

- [knowledge_graph/README.md](/Users/loki/Workspace/GraduationDesign/knowledge_graph/README.md)

## 第二阶段：问诊大脑脚手架

第二阶段已经搭好基础目录与核心文件：

- 更详细的目录说明见：[brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)
- [brain/types.py](/Users/loki/Workspace/GraduationDesign/brain/types.py)：状态、候选问题、假设分数等核心数据结构
- [brain/state_tracker.py](/Users/loki/Workspace/GraduationDesign/brain/state_tracker.py)：会话状态追踪器
- [brain/session_dag.py](/Users/loki/Workspace/GraduationDesign/brain/session_dag.py)：会话内存 DAG / DFS 追问骨架
- [brain/neo4j_client.py](/Users/loki/Workspace/GraduationDesign/brain/neo4j_client.py)：Neo4j 查询封装
- [brain/retriever.py](/Users/loki/Workspace/GraduationDesign/brain/retriever.py)：冷启动、正向假设、反向验证检索骨架
- [scripts/run_retriever_smoke.py](/Users/loki/Workspace/GraduationDesign/scripts/run_retriever_smoke.py)：真实 Neo4j 图谱联调脚本
- [brain/question_selector.py](/Users/loki/Workspace/GraduationDesign/brain/question_selector.py)：下一问打分与选择器
- [brain/mcts_engine.py](/Users/loki/Workspace/GraduationDesign/brain/mcts_engine.py)：基于 UCT 的动作选择器
- [brain/simulation_engine.py](/Users/loki/Workspace/GraduationDesign/brain/simulation_engine.py)：局部 simulation 预演器
- [brain/stop_rules.py](/Users/loki/Workspace/GraduationDesign/brain/stop_rules.py)：终止与降级规则
- [brain/report_builder.py](/Users/loki/Workspace/GraduationDesign/brain/report_builder.py)：结构化结果汇总
- [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)：A1-A4 问诊编排层

## 虚拟病人脚手架

虚拟病人与离线评测模块也已经预留：

- 更详细的目录说明见：[simulator/README.md](/Users/loki/Workspace/GraduationDesign/simulator/README.md)
- [simulator/case_schema.py](/Users/loki/Workspace/GraduationDesign/simulator/case_schema.py)
- [simulator/generate_cases.py](/Users/loki/Workspace/GraduationDesign/simulator/generate_cases.py)
- [simulator/patient_agent.py](/Users/loki/Workspace/GraduationDesign/simulator/patient_agent.py)
- [simulator/replay_engine.py](/Users/loki/Workspace/GraduationDesign/simulator/replay_engine.py)
- [simulator/benchmark.py](/Users/loki/Workspace/GraduationDesign/simulator/benchmark.py)
- [simulator/path_cache_builder.py](/Users/loki/Workspace/GraduationDesign/simulator/path_cache_builder.py)
- [scripts/run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)：批量虚拟病人回放与评测入口

当前 `replay_engine` 已经不再是占位文件，而是能够驱动 `brain/service.py` 跑通最小的“系统问 -> 病人答 -> 系统再问”自动回放闭环，并支持批量回放。

## 配置、测试与文档

- [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)
- [configs/stop_rules.yaml](/Users/loki/Workspace/GraduationDesign/configs/stop_rules.yaml)
- [configs/simulator.yaml](/Users/loki/Workspace/GraduationDesign/configs/simulator.yaml)
- [tests](/Users/loki/Workspace/GraduationDesign/tests)：第二阶段测试脚手架
- 更详细的目录说明见：[tests/README.md](/Users/loki/Workspace/GraduationDesign/tests/README.md)
- [tests/test_replay_engine.py](/Users/loki/Workspace/GraduationDesign/tests/test_replay_engine.py)
- [tests/test_mcts_engine.py](/Users/loki/Workspace/GraduationDesign/tests/test_mcts_engine.py)
- [tests/test_simulation_engine.py](/Users/loki/Workspace/GraduationDesign/tests/test_simulation_engine.py)
- [tests/test_generate_cases.py](/Users/loki/Workspace/GraduationDesign/tests/test_generate_cases.py)
- [tests/test_benchmark.py](/Users/loki/Workspace/GraduationDesign/tests/test_benchmark.py)
- [phase2_execution_checklist.md](/Users/loki/Workspace/GraduationDesign/docs/phase2_execution_checklist.md)：第二阶段与虚拟病人开发清单
- [scripts/run_brain_demo.py](/Users/loki/Workspace/GraduationDesign/scripts/run_brain_demo.py)：最小命令行问诊演示入口

补充说明：

- 全局 README 主要说明整体结构与阶段划分
- [knowledge_graph/README.md](/Users/loki/Workspace/GraduationDesign/knowledge_graph/README.md) 说明第一阶段知识图谱处理链
- [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md) 说明第二阶段问诊大脑目录结构与文件职责
- [simulator/README.md](/Users/loki/Workspace/GraduationDesign/simulator/README.md) 说明虚拟病人与离线回放目录结构与文件职责
- [tests/README.md](/Users/loki/Workspace/GraduationDesign/tests/README.md) 说明第二阶段测试组织方式与当前覆盖范围

## 当前环境

- 推荐 conda 环境：`GraduationDesign`
- 推荐 Python：`3.10.x`
- 当前项目已使用并确认过的核心依赖：
  - `openai`
  - `neo4j`
  - `langchain`
  - `langchain_community`

建议先执行：

```bash
conda activate GraduationDesign
```

## 推荐下一步

如果继续推进第二阶段，建议开发顺序如下：

1. 继续补强 `brain/service.py` 的完整 A1-A4 闭环
2. 在本地 Neo4j 正常启动后，用 `run_retriever_smoke.py` 做真实图谱联调
3. 继续扩展 `simulator/generate_cases.py` 的病例覆盖面和行为风格
4. 用 `run_batch_replay.py` 跑批量回放并观察离线指标
5. 在 `benchmark.py` 中继续固化更多质量指标
6. 最后再推进更深层的 rollout 与路径缓存
