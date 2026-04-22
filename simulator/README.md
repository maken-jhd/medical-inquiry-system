# simulator

`simulator/` 目录用于承载“虚拟病人生成、自动对战、离线评测与路径缓存”相关代码。它对应整个项目规划中的“虚拟患者数据集与离线预演”部分，是第二阶段问诊大脑的重要评测环境。

这个目录的目标不是替代真实临床资料，而是为问诊策略提供一个可重复、可量化、可自动化回放的测试场。

## 目录职责

`simulator/` 当前主要承担以下几类任务：

- 定义结构化虚拟病例格式
- 生成虚拟病例数据
- 模拟病人与问诊系统之间的问答交互
- 对问诊路径进行离线回放与评测
- 为未来离线路径缓存做数据准备

## 当前文件说明

### 1. 病例结构与生成

- [case_schema.py](/Users/loki/Workspace/GraduationDesign/simulator/case_schema.py)
  - 定义虚拟病人的结构化数据格式。
  - 当前病例统一遵循这里约束的字段，例如真实病情、行为风格、槽位真值、主动暴露槽位、隐藏槽位等。

- [generate_cases.py](/Users/loki/Workspace/GraduationDesign/simulator/generate_cases.py)
  - 用于生成虚拟病例数据集。
  - 当前已经内置一批覆盖 PCP、结核、急性 HIV、慢病共病、孕产期和隐瞒风险史等场景的 seed cases。
  - 当前已支持把病例写出/读回 `JSONL` 和 `JSON` 数组，便于批量回放和人工查看。

- [graph_case_generator.py](/Users/loki/Workspace/GraduationDesign/simulator/graph_case_generator.py)
  - 用于把疾病级图谱审计结果转换成图谱驱动的虚拟病人病例骨架。
  - 当前支持 `ordinary / low_cost / exam_driven / competitive` 四类病例。
  - 会同时输出 `cases.jsonl`、`cases.json`、`manifest.json` 和 `summary.md`。

### 2. 病人代理

- [patient_agent.py](/Users/loki/Workspace/GraduationDesign/simulator/patient_agent.py)
  - 负责模拟“虚拟病人如何回答问题”。
  - 当前已支持根据病例骨架中的 opening slots 生成首轮开场，并在问答中遵循“未被问到不主动透露、敏感信息可回避、未知项不乱答”的行为规则。
  - 在配置了可用 LLM 时，会使用受约束的 LLM 生成更自然的患者表达；否则退回规则模板。

### 3. 自动对战与评测

- [replay_engine.py](/Users/loki/Workspace/GraduationDesign/simulator/replay_engine.py)
  - 负责让 `brain/` 中的问诊系统与虚拟病人自动对战。
  - 它的职责是串联“系统提问 -> 病人作答 -> 状态更新 -> 下一问”的离线闭环。
  - 当前首轮输入不再直接依赖 `chief_complaint`，而是优先由 `patient_agent.open_case(case)` 基于骨架生成 opening text。
  - 已支持批量运行多个病例、输出回放结果，并在结果里记录实际首轮 opening text。

- [benchmark.py](/Users/loki/Workspace/GraduationDesign/simulator/benchmark.py)
  - 负责汇总自动对战结果。
  - 当前已支持统计平均轮次、完成率、假设命中率和红旗覆盖率等核心指标。

- [path_cache_builder.py](/Users/loki/Workspace/GraduationDesign/simulator/path_cache_builder.py)
  - 用于从大量回放结果中提取高价值路径，并生成在线问诊可直接检索的“离线最优路径缓存”。

### 4. 辅助文件

- [__init__.py](/Users/loki/Workspace/GraduationDesign/simulator/__init__.py)
  - Python 包初始化文件。

## 当前实现状态

当前 `simulator/` 已经形成一条可运行的主链路：

- 病例结构已定义并支持真实图谱 node id 对齐
- 已具备一批可直接跑的 seed cases
- 已具备基于疾病审计结果的图谱驱动病例骨架生成器
- 病人代理已支持骨架驱动开场和受约束回答
- 自动回放和基础评测已经能批量跑通
- 路径缓存仍然是后续待完成模块

因此，这个目录当前更适合被理解为：

- 已完成可运行的病例生成与自动对战闭环
- 仍在继续提升 opening 自然度、行为多样性和大规模稳定评测

## 与其他目录的关系

- 问诊大脑：
  - [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)

- 配置文件：
  - [configs/simulator.yaml](/Users/loki/Workspace/GraduationDesign/configs/simulator.yaml)

- 第二阶段测试：
  - [tests/README.md](/Users/loki/Workspace/GraduationDesign/tests/README.md)

## 当前可直接使用的脚本

- [run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)
  - 使用 seed cases 或指定病例文件批量运行回放，并输出评测摘要。
  - 当前支持读取 `JSONL` 或 `JSON` 数组病例文件。

- [generate_graph_virtual_patients.py](/Users/loki/Workspace/GraduationDesign/scripts/generate_graph_virtual_patients.py)
  - 使用疾病级图谱审计输出生成图谱驱动虚拟病人病例。

## 详细方案文档

更适合论文写作和方法复盘的详细说明见：

- [virtual_patient_generation_scheme.md](/Users/loki/Workspace/GraduationDesign/docs/virtual_patient_generation_scheme.md)

## 后续重点建设方向

按照当前路线，`simulator/` 后续最值得优先推进的是：

1. 完善 `patient_agent.py` 的行为规则
2. 让 `replay_engine.py` 真正驱动 `brain/service.py`
3. 扩展 `generate_cases.py`，先生成一批小规模高质量样例
4. 在 `benchmark.py` 中固化核心离线评估指标
5. 最后推进 `path_cache_builder.py`

## 代码注释规范

本目录已统一采用中文注释规范：

- 每个文件顶部有中文文件说明
- 每个类有中文说明
- 每个函数上方都应有中文用途注释

后续新增文件和函数时，也应继续遵守这一规范。
