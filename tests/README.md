# tests

`tests/` 目录用于存放第二阶段问诊大脑与虚拟病人相关的测试代码。当前测试以单元测试和最小行为测试为主，目标是保证二阶段核心闭环在持续扩展时不发生基础回归。

## 当前目录定位

这个目录不是第一阶段知识图谱抽取链的测试目录，而是主要面向：

- `brain/` 中的状态追踪、会话结构、候选排序逻辑
- `simulator/` 中的虚拟病人最小行为逻辑

随着第二阶段逐步落地，这个目录后续还会扩展到：

- 问诊闭环测试
- Neo4j 检索联调测试
- 虚拟病人自动对战测试
- 回放指标统计测试

当前测试数量为 `27`。

## 当前文件说明

- [test_state_tracker.py](/Users/loki/Workspace/GraduationDesign/tests/test_state_tracker.py)
  - 测试 `brain/state_tracker.py`
  - 主要覆盖槽位状态初始化、更新、证据追加等基础行为

- [test_session_dag.py](/Users/loki/Workspace/GraduationDesign/tests/test_session_dag.py)
  - 测试 `brain/session_dag.py`
  - 主要覆盖主题分支管理、节点状态流转等逻辑

- [test_retriever.py](/Users/loki/Workspace/GraduationDesign/tests/test_retriever.py)
  - 测试 `brain/retriever.py`
  - 当前已覆盖 `R1 / R2` 的最小行为验证，包含方向语义融合后的 `R1` 基本路径

- [test_question_selector.py](/Users/loki/Workspace/GraduationDesign/tests/test_question_selector.py)
  - 测试 `brain/question_selector.py`
  - 主要用于验证候选提问排序逻辑和优先级规则

- [test_patient_agent.py](/Users/loki/Workspace/GraduationDesign/tests/test_patient_agent.py)
  - 测试 `simulator/patient_agent.py`
  - 用于保证虚拟病人不会无规则地泄露信息，并能按照预设风格作答

- [test_replay_engine.py](/Users/loki/Workspace/GraduationDesign/tests/test_replay_engine.py)
  - 测试 `simulator/replay_engine.py`
  - 主要验证自动回放引擎能否驱动一个最小的问诊闭环并支持批量回放

- [test_mcts_engine.py](/Users/loki/Workspace/GraduationDesign/tests/test_mcts_engine.py)
  - 测试 `brain/mcts_engine.py`
  - 主要验证 `UCT` 选择器是否会优先选择更高综合收益的动作，以及 tree policy 是否会沿树向下选择叶子

- [test_simulation_engine.py](/Users/loki/Workspace/GraduationDesign/tests/test_simulation_engine.py)
  - 测试 `brain/simulation_engine.py`
  - 主要验证局部 simulation 对不同关系类型动作的收益估计是否合理，以及 rollout 是否会展开多步路径

- [test_evidence_parser.py](/Users/loki/Workspace/GraduationDesign/tests/test_evidence_parser.py)
  - 测试 `brain/evidence_parser.py`
  - 当前已覆盖 target-aware A4 解释、否定片段提取和 uncertain span 提取

- [test_router_control_flow.py](/Users/loki/Workspace/GraduationDesign/tests/test_router_control_flow.py)
  - 测试 `brain/router.py`
  - 当前已覆盖 `STOP / A2` 等核心路由分支以及 A4 输出 metadata

- [test_service_config.py](/Users/loki/Workspace/GraduationDesign/tests/test_service_config.py)
  - 测试 `brain/service.py`
  - 当前已覆盖 `configs/brain.yaml` 的读取入口

- [test_report_builder.py](/Users/loki/Workspace/GraduationDesign/tests/test_report_builder.py)
  - 测试 `brain/report_builder.py`
  - 当前已覆盖 `trajectory_summary / why_this_answer_wins / evidence_for_best_answer` 等解释性字段

- [test_generate_cases.py](/Users/loki/Workspace/GraduationDesign/tests/test_generate_cases.py)
  - 测试 `simulator/generate_cases.py`
  - 主要验证 seed cases 的数量、唯一性和覆盖面

- [test_benchmark.py](/Users/loki/Workspace/GraduationDesign/tests/test_benchmark.py)
  - 测试 `simulator/benchmark.py`
  - 主要验证完成率、命中率、红旗覆盖率等基础指标计算

## 当前测试特点

目前这批测试的特点是：

- 目标明确，优先覆盖基础模块
- 更偏向“脚手架是否稳固”，而不是“完整系统是否最优”
- 已覆盖最近这轮的树搜索、A4 路由、配置读取和解释性报告回归点
- 适合在持续开发中快速发现基础回归

当前还没有系统性覆盖的部分包括：

- `brain/service.py` 的完整 A1-A4 编排闭环
- `brain/retriever.py` 与真实 Neo4j 数据库的稳定集成测试
- `simulator/replay_engine.py` 的端到端自动对战测试
- `path_cache_builder.py` 的结果一致性测试

## 与其他目录的关系

- 被测试对象：
  - [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)
  - [simulator/README.md](/Users/loki/Workspace/GraduationDesign/simulator/README.md)

- 开发清单：
  - [docs/phase2_execution_checklist.md](/Users/loki/Workspace/GraduationDesign/docs/phase2_execution_checklist.md)

## 后续建议补充的测试方向

后续建议按下面顺序继续扩展：

1. `service.py` 的最小闭环测试
2. `router.py` 的路由分支测试
3. `hypothesis_manager.py` 的假设更新测试
4. `action_builder.py` 的动作生成测试
5. `replay_engine.py` 的自动回放测试
6. 结合 Neo4j 测试库的检索联调测试

## 代码注释规范

本目录已统一采用中文注释规范：

- 每个文件顶部有中文文件说明
- 每个测试函数上方都有中文用途注释

后续新增测试文件与测试函数时，也应继续遵守这一规范。
