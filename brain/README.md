# brain

`brain/` 目录承载第二阶段“问诊大脑”的核心代码。它建立在第一阶段已经完成的知识图谱底座之上，负责会话状态管理、图谱检索、候选动作生成、提问决策、终止判断与结果汇总。

当前这一层的实现路线已经从最初的“FSM + DFS 追问”转向更接近论文的方法：

- `A1`：核心症状提取
- `A2`：假设生成
- `A3`：证据验证
- `A4`：演绎分析
- 外层再结合 `UCT`、局部 `Simulation` 与代码级路由

当前默认实现已经具备下面这些关键特征：

- `run_reasoning_search()` 会真正执行多次 `select -> expand -> simulate -> backpropagate`
- `select_leaf()` 已按 tree policy 沿树向下选择，而不是简单摊平叶子排序
- `rollout_from_tree_node()` 已支持浅层多步 rollout，并会显式记录 `A3 -> A4 -> ROUTE`
- `process_turn()` 已按 `STOP / A3 / A2 / A1 / FALLBACK` 分支使用 A4 路由结果
- 默认构造已真正消费 [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)

当前需要明确区分三种工作模式：

- `interactive`：交互式问诊模式，围绕当前会话生成下一问
- `search`：论文风格的局部树搜索模式，围绕多个候选假设做 rollout
- `fallback`：当 KG 或搜索不可靠时退回启发式选择器

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
  - 包括槽位状态、置信度、患者上下文、实体链接、候选假设、候选动作、搜索树节点、轨迹与 A1/A2/A3/A4 阶段输出。

- [llm_client.py](/Users/loki/Workspace/GraduationDesign/brain/llm_client.py)
  - 统一封装第二阶段大模型结构化调用。
  - 当前供 `MedExtractor`、`A1` 抽取、`A2` 假设排序、`A4` deductive judge 和轨迹 verifier 复用。

- [state_tracker.py](/Users/loki/Workspace/GraduationDesign/brain/state_tracker.py)
  - 负责维护会话中的槽位状态。
  - 支持三态记录（阳性 / 阴性 / 未知）以及“确信 / 存疑”的置信维度。
  - 当前也负责保存轨迹列表与绑定搜索树。

- [session_dag.py](/Users/loki/Workspace/GraduationDesign/brain/session_dag.py)
  - 负责维护单个患者会话的内存 DAG。
  - 当前主要承担主题分支管理与节点开闭状态维护，不再是唯一调度器。

- [neo4j_client.py](/Users/loki/Workspace/GraduationDesign/brain/neo4j_client.py)
  - 对 Neo4j 查询做轻量封装。
  - 供检索器和后续其他图谱查询逻辑复用。

- [search_tree.py](/Users/loki/Workspace/GraduationDesign/brain/search_tree.py)
  - 实现显式搜索树。
  - 当前负责搜索节点管理、父子关系维护与 reward 回传。

### 2. 检索、选择与结果汇总

- [retriever.py](/Users/loki/Workspace/GraduationDesign/brain/retriever.py)
  - 负责和知识图谱交互，提供候选节点、候选假设和验证证据的查询入口。
  - 当前已经实现论文风格的 `R1 / R2` 双向检索基础版。
  - `R1` 已增加方向置信度与实体链接相似度融合。
  - `R2` 已支持方向优先、已问节点过滤与问题类型提示。

- [question_selector.py](/Users/loki/Workspace/GraduationDesign/brain/question_selector.py)
  - 负责对候选提问节点进行排序。
  - 当前已经降级为 `cold-start / no-search` 的 fallback 选择器。

- [stop_rules.py](/Users/loki/Workspace/GraduationDesign/brain/stop_rules.py)
  - 定义何时可以终止问诊、何时需要停止 rollout、何时接受最终答案。

- [report_builder.py](/Users/loki/Workspace/GraduationDesign/brain/report_builder.py)
  - 用于生成结构化阶段报告、搜索报告和最终报告。
  - 当前会额外输出 `trajectory_summary`、`why_this_answer_wins`、`evidence_for_best_answer` 等解释字段。

- [service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - 第二阶段的总编排层。
  - 当前已经串联 `PatientContext -> A1 -> A2 -> R2/A3 -> rollout -> report` 的搜索闭环。
  - 当前也是读取 [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml) 并构造默认依赖的入口。

### 3. 论文式 A1-A4 模块

- [med_extractor.py](/Users/loki/Workspace/GraduationDesign/brain/med_extractor.py)
  - 对齐论文中的 MedExtractor。
  - 负责把患者原话拆成一般信息 `P` 和临床特征 `C`。

- [evidence_parser.py](/Users/loki/Workspace/GraduationDesign/brain/evidence_parser.py)
  - 对应 `A1` 与答案解释层。
  - 负责从患者回答中提取关键医学线索，并解释目标验证点的回答结果。
  - 当前会输出 `supporting_span / negation_span / uncertain_span`，并支持可选 LLM deductive judge。

- [entity_linker.py](/Users/loki/Workspace/GraduationDesign/brain/entity_linker.py)
  - 对齐论文里的实体链接与阈值过滤。
  - 负责把 mention 对齐到图谱节点，并决定当前是否可信地启用 KG。

- [hypothesis_manager.py](/Users/loki/Workspace/GraduationDesign/brain/hypothesis_manager.py)
  - 对应 `A2` 假设生成。
  - 负责整理由图谱检索得到的候选疾病，并维护主假设与备选假设。
  - 当前已能结合患者上下文和证据类型做轻量重排。
  - 若启用 LLM 排序，还会把 `supporting_features / conflicting_features / recommended_next_evidence` 写入 metadata。

- [action_builder.py](/Users/loki/Workspace/GraduationDesign/brain/action_builder.py)
  - 对应 `A3` 证据验证的动作生成层。
  - 负责把图谱返回的验证证据转成“下一步可执行动作”。
  - 当前已支持结合 competing hypotheses 估计 `discriminative_gain`。
  - 当前也会消费 `recommended_next_evidence`，让动作更贴近鉴别诊断。

- [router.py](/Users/loki/Workspace/GraduationDesign/brain/router.py)
  - 对应 `A4` 演绎分析后的代码级路由。
  - 根据 `Exist / Non-exist` 与 `Confident / Doubt` 的组合决定继续验证、回溯、切换假设或终止。
  - 当前已支持把 A4 结果转换为显式 `DeductiveDecision`。

### 4. 搜索与前瞻模块

- [mcts_engine.py](/Users/loki/Workspace/GraduationDesign/brain/mcts_engine.py)
  - 负责按 `UCT` 公式在候选动作和树节点中做动态选择。
  - 当前已支持状态签名、tree policy、子节点扩展和 reward 回传。

- [simulation_engine.py](/Users/loki/Workspace/GraduationDesign/brain/simulation_engine.py)
  - 负责对候选动作做浅层局部预演。
  - 当前会估算 `positive / negative / doubtful` 三种回答分支的收益。
  - 当前已支持从树节点出发做浅层多步 rollout。

- [trajectory_evaluator.py](/Users/loki/Workspace/GraduationDesign/brain/trajectory_evaluator.py)
  - 对齐论文最后的轨迹聚合器。
  - 当前负责按最终答案聚类轨迹，并计算 `consistency / diversity / agent_evaluation`。
  - `diversity` 已从“唯一动作数”升级为基于轨迹相似度的组内平均差异。
  - `agent_evaluation` 当前支持 `fallback` 与可选 `llm_verifier` 两种模式。

### 5. 辅助文件

- [__init__.py](/Users/loki/Workspace/GraduationDesign/brain/__init__.py)
  - Python 包初始化文件。

## 当前实现状态

目前 `brain/` 的状态可以概括为：

- 类型系统已搭好
- 基础状态机和会话图已搭好
- MedExtractor、实体链接、搜索树和轨迹评估器都已有第一轮实现
- 图谱检索入口已和当前图谱 schema 做了第一轮对齐
- A1-A4 的第一批模块已建立
- UCT、局部 simulation 和轨迹评分都已接成默认主路径
- `service.py` 已经能够跑通多次 rollout 的最小搜索闭环
- 但还没有完全复现论文中的更深 rollout、完整 verifier 和最终轨迹判别器

也就是说，当前目录已经从“空脚手架”进入“可持续填充核心逻辑”的阶段。

## 与 Med-MCTS 论文的对齐状态

| 组件 | 当前状态 | 说明 |
|---|---|---|
| MedExtractor | 基础版完成 | 已有 `patient_text -> (P, C)` |
| A1 | 部分完成 | 已支持 LLM 主通道与规则回退 |
| A2 | 部分完成 | 已支持患者上下文 + R1 候选排序 |
| A3 | 部分完成 | 已支持 R2 检索、动作构造、区分性 gain 与问题生成 |
| A4 | 部分完成 | 已支持目标感知解释、可选 LLM judge 与显式路由 |
| R1 / R2 | 基础版完成 | 已与真实 Neo4j 联调，R1 已增加方向语义 |
| Search Tree | 基础版完成 | 已有显式树、tree policy 和回传统计 |
| Rollout | 浅层版完成 | 已支持多次 rollout 与局部多步路径输出 |
| Path Evaluation | 基础版完成 | 已支持一致性 / 相似度驱动多样性 / agent score |
| 完整论文复现 | 未完成 | 当前仍处于“结构对齐 + 轻量实现”阶段 |

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

- [run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)
  - 运行真实端到端 smoke：问诊大脑 + 虚拟病人 + 搜索报告 + benchmark 汇总。

## 代码注释规范

本目录已统一采用中文注释规范：

- 每个文件顶部有中文文件说明
- 每个类有中文说明
- 每个函数上方都应有中文用途注释

后续新增文件和函数时，也应继续遵守这一规范。
