# brain

`brain/` 目录承载第二阶段“问诊大脑”的核心代码。它建立在第一阶段已经完成的知识图谱底座之上，负责会话状态管理、图谱检索、候选动作生成、提问决策、终止判断与结果汇总。

当前这一层的实现路线已经从最初的“FSM + DFS 追问”转向更接近论文的方法：

- `A1`：核心症状提取
- `A2`：假设生成
- `A3`：证据验证
- `A4`：演绎分析
- 外层再结合 `UCT`、局部 `Simulation` 与代码级路由

## 目录职责

`brain/` 当前主要负责以下几类工作：

- 定义第二阶段统一的数据结构
- 维护患者会话状态
- 管理会话内存 DAG
- 对接 Neo4j 图谱做 `R1 / R2` 检索
- 生成候选动作并决定下一问
- 使用 `UCT` 在候选动作中做动态平衡选择
- 使用局部 `Simulation` 预演动作收益
- 根据 A4 的结果执行路由和回溯
- 生成阶段性报告或最终报告

## 当前文件说明

### 1. 类型与基础结构

- [types.py](/Users/loki/Workspace/GraduationDesign/brain/types.py)
  - 定义第二阶段通用数据结构。
  - 包括槽位状态、置信度、核心特征、候选假设、候选动作、A1/A2/A3/A4 阶段输出以及会话状态。

- [state_tracker.py](/Users/loki/Workspace/GraduationDesign/brain/state_tracker.py)
  - 负责维护会话中的槽位状态。
  - 支持三态记录（阳性 / 阴性 / 未知）以及“确信 / 存疑”的置信维度。

- [session_dag.py](/Users/loki/Workspace/GraduationDesign/brain/session_dag.py)
  - 负责维护单个患者会话的内存 DAG。
  - 当前主要承担主题分支管理与节点开闭状态维护。

- [neo4j_client.py](/Users/loki/Workspace/GraduationDesign/brain/neo4j_client.py)
  - 对 Neo4j 查询做轻量封装。
  - 供检索器和后续其他图谱查询逻辑复用。

### 2. 检索、选择与结果汇总

- [retriever.py](/Users/loki/Workspace/GraduationDesign/brain/retriever.py)
  - 负责和知识图谱交互，提供候选节点、候选假设和验证证据的查询入口。
  - 当前已经实现论文风格的 `R1 / R2` 双向检索，并补充了真实 Neo4j smoke 检查入口。

- [question_selector.py](/Users/loki/Workspace/GraduationDesign/brain/question_selector.py)
  - 负责对候选提问节点进行排序。
  - 当前是启发式基础版，后续会进一步接入 `UCT + rollout`。

- [stop_rules.py](/Users/loki/Workspace/GraduationDesign/brain/stop_rules.py)
  - 定义何时可以终止问诊、何时需要降级或回退。

- [report_builder.py](/Users/loki/Workspace/GraduationDesign/brain/report_builder.py)
  - 用于生成结构化阶段报告和最终报告。

- [service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - 第二阶段的总编排层。
  - 当前已经开始串联状态更新、A1-A4 运行、候选动作选择、终止判断和报告输出。

### 3. 论文式 A1-A4 模块

- [evidence_parser.py](/Users/loki/Workspace/GraduationDesign/brain/evidence_parser.py)
  - 对应 `A1` 核心症状提取。
  - 负责从患者回答中提取关键医学线索，并转成结构化特征。

- [hypothesis_manager.py](/Users/loki/Workspace/GraduationDesign/brain/hypothesis_manager.py)
  - 对应 `A2` 假设生成。
  - 负责整理由图谱检索得到的候选疾病，并维护主假设与备选假设。

- [action_builder.py](/Users/loki/Workspace/GraduationDesign/brain/action_builder.py)
  - 对应 `A3` 证据验证的动作生成层。
  - 负责把图谱返回的验证证据转成“下一步可执行动作”。

- [router.py](/Users/loki/Workspace/GraduationDesign/brain/router.py)
  - 对应 `A4` 演绎分析后的代码级路由。
  - 根据 `Exist / Non-exist` 与 `Confident / Doubt` 的组合决定继续验证、回溯、切换假设或终止。

### 4. 搜索与前瞻模块

- [mcts_engine.py](/Users/loki/Workspace/GraduationDesign/brain/mcts_engine.py)
  - 负责按 `UCT` 公式在候选动作中做动态选择。
  - 当前会综合历史访问统计、动作先验分数和 simulation 收益做排序。

- [simulation_engine.py](/Users/loki/Workspace/GraduationDesign/brain/simulation_engine.py)
  - 负责对候选动作做浅层局部预演。
  - 当前会估算正反两种回答分支的收益，并返回期望奖励。

### 5. 辅助文件

- [__init__.py](/Users/loki/Workspace/GraduationDesign/brain/__init__.py)
  - Python 包初始化文件。

## 当前实现状态

目前 `brain/` 的状态可以概括为：

- 类型系统已搭好
- 基础状态机和会话图已搭好
- 图谱检索入口已和当前图谱 schema 做了第一轮对齐
- A1-A4 的第一批模块已建立
- UCT 与局部 simulation 已有最小可运行版本
- `service.py` 已经能够跑通单轮 A1-A4 编排
- 但还没有完全联成一个可长期对话、可稳定收敛的完整问诊系统

也就是说，当前目录已经从“空脚手架”进入“可持续填充核心逻辑”的阶段。

## 与其他目录的关系

- 第一阶段知识图谱底座：
  - [knowledge_graph/README.md](/Users/loki/Workspace/GraduationDesign/knowledge_graph/README.md)

- 虚拟病人与离线评测：
  - [simulator/README.md](/Users/loki/Workspace/GraduationDesign/simulator/README.md)

- 第二阶段测试：
  - [tests/README.md](/Users/loki/Workspace/GraduationDesign/tests/README.md)

## 当前可直接使用的脚本

- [run_brain_demo.py](/Users/loki/Workspace/GraduationDesign/scripts/run_brain_demo.py)
  - 运行最小命令行问诊演示。

- [run_retriever_smoke.py](/Users/loki/Workspace/GraduationDesign/scripts/run_retriever_smoke.py)
  - 直接连本地 Neo4j，检查当前图谱标签、关系分布以及 `R1 / R2` 是否能返回结果。

## 代码注释规范

本目录已统一采用中文注释规范：

- 每个文件顶部有中文文件说明
- 每个类有中文说明
- 每个函数上方都应有中文用途注释

后续新增文件和函数时，也应继续遵守这一规范。
