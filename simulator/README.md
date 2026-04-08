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
  - 后续病例会统一遵循这里约束的字段，例如真实病情、隐藏症状、行为风格、槽位真值等。

- [generate_cases.py](/Users/loki/Workspace/GraduationDesign/simulator/generate_cases.py)
  - 用于生成虚拟病例数据集。
  - 当前已经内置一批覆盖 PCP、结核、急性 HIV、慢病共病、孕产期和隐瞒风险史等场景的 seed cases。
  - 也支持把病例写出/读回 JSONL，便于后续批量回放。

### 2. 病人代理

- [patient_agent.py](/Users/loki/Workspace/GraduationDesign/simulator/patient_agent.py)
  - 负责模拟“虚拟病人如何回答问题”。
  - 未来会严格遵循“未被问到不主动透露、问得模糊就模糊回答、敏感信息可能回避”的行为规则。

### 3. 自动对战与评测

- [replay_engine.py](/Users/loki/Workspace/GraduationDesign/simulator/replay_engine.py)
  - 负责让 `brain/` 中的问诊系统与虚拟病人自动对战。
  - 它的职责是串联“系统提问 -> 病人作答 -> 状态更新 -> 下一问”的离线闭环。
  - 当前已经能够驱动最小的自动问答闭环，并支持批量运行多个病例、输出回放结果。

- [benchmark.py](/Users/loki/Workspace/GraduationDesign/simulator/benchmark.py)
  - 负责汇总自动对战结果。
  - 当前已支持统计平均轮次、完成率、假设命中率和红旗覆盖率等核心指标。

- [path_cache_builder.py](/Users/loki/Workspace/GraduationDesign/simulator/path_cache_builder.py)
  - 用于从大量回放结果中提取高价值路径，并生成在线问诊可直接检索的“离线最优路径缓存”。

### 4. 辅助文件

- [__init__.py](/Users/loki/Workspace/GraduationDesign/simulator/__init__.py)
  - Python 包初始化文件。

## 当前实现状态

目前 `simulator/` 的完成度还处于早期：

- 病例结构已定义基础骨架
- 病人代理已有最小实现
- 已具备一批可直接跑的 seed cases
- 自动回放和基础评测已经能批量跑通
- 路径缓存仍然是后续待完成模块

因此，这个目录当前更适合被理解为：

- 已完成模块拆分与结构设计
- 尚未完成大规模病例生成与稳定离线评测

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
