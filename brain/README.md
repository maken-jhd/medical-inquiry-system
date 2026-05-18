# brain

`brain/` 是第二阶段“问诊大脑”的核心目录。当前版本已经从旧的平铺模块结构重构为按运行阶段分层的包结构，目标是让调试入口、职责边界和代码导航都更清晰。

## 当前稳定入口

外部稳定调用面仍保持不变：

- [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - 对 frontend / replay / scripts 暴露稳定入口
- `build_default_brain_from_env()`
- `ConsultationBrain.process_turn(session_id, patient_text)`
- `ConsultationBrain.finalize(session_id)`

当前实现中：

- `brain/service.py` 只做公共导出
- [brain/app/brain.py](/Users/loki/Workspace/GraduationDesign/brain/app/brain.py) 保存
  - `ConsultationBrain` 薄门面
  - `BrainRuntime` 运行时实现
- facade 会把主链分别委托给 3 个协调器：
  - [brain/turn/coordinator.py](/Users/loki/Workspace/GraduationDesign/brain/turn/coordinator.py)
  - [brain/search/coordinator.py](/Users/loki/Workspace/GraduationDesign/brain/search/coordinator.py)
  - [brain/acceptance/coordinator.py](/Users/loki/Workspace/GraduationDesign/brain/acceptance/coordinator.py)

## 目录分层

### 1. `app/`

- 门面、依赖注入、默认构造、运行时主实现
- 关键文件：
  - [brain/app/brain.py](/Users/loki/Workspace/GraduationDesign/brain/app/brain.py)

### 2. `turn/`

- 负责本轮解释与状态写回
- 关键文件：
  - [brain/turn/parser.py](/Users/loki/Workspace/GraduationDesign/brain/turn/parser.py)
  - [brain/turn/extractor.py](/Users/loki/Workspace/GraduationDesign/brain/turn/extractor.py)
  - [brain/turn/coordinator.py](/Users/loki/Workspace/GraduationDesign/brain/turn/coordinator.py)

### 3. `search/`

- 负责 `R1 / R2` 检索、A2、A3、MCTS、rollout、trajectory evaluation
- 关键文件：
  - [brain/search/retriever.py](/Users/loki/Workspace/GraduationDesign/brain/search/retriever.py)
  - [brain/search/hypothesis_manager.py](/Users/loki/Workspace/GraduationDesign/brain/search/hypothesis_manager.py)
  - [brain/search/action_builder.py](/Users/loki/Workspace/GraduationDesign/brain/search/action_builder.py)
  - [brain/search/mcts.py](/Users/loki/Workspace/GraduationDesign/brain/search/mcts.py)
  - [brain/search/simulation.py](/Users/loki/Workspace/GraduationDesign/brain/search/simulation.py)
  - [brain/search/evaluator.py](/Users/loki/Workspace/GraduationDesign/brain/search/evaluator.py)
  - [brain/search/coordinator.py](/Users/loki/Workspace/GraduationDesign/brain/search/coordinator.py)

### 4. `acceptance/`

- 负责 verifier acceptance 与 completed 判定
- 关键文件：
  - [brain/acceptance/controller.py](/Users/loki/Workspace/GraduationDesign/brain/acceptance/controller.py)
  - [brain/acceptance/coordinator.py](/Users/loki/Workspace/GraduationDesign/brain/acceptance/coordinator.py)

### 5. `state/`

- 负责会话状态、阶段结果、anchor、state signature
- 类型已从旧 `types.py` 拆分为：
  - [brain/state/runtime.py](/Users/loki/Workspace/GraduationDesign/brain/state/runtime.py)
  - [brain/state/results.py](/Users/loki/Workspace/GraduationDesign/brain/state/results.py)
- 其他关键文件：
  - [brain/state/tracker.py](/Users/loki/Workspace/GraduationDesign/brain/state/tracker.py)
  - [brain/state/anchors.py](/Users/loki/Workspace/GraduationDesign/brain/state/anchors.py)
  - [brain/state/signature.py](/Users/loki/Workspace/GraduationDesign/brain/state/signature.py)

### 6. `integrations/`

- 负责与外部系统交互
- 关键文件：
  - [brain/integrations/llm.py](/Users/loki/Workspace/GraduationDesign/brain/integrations/llm.py)
  - [brain/integrations/neo4j.py](/Users/loki/Workspace/GraduationDesign/brain/integrations/neo4j.py)
  - [brain/integrations/entity_linker.py](/Users/loki/Workspace/GraduationDesign/brain/integrations/entity_linker.py)

### 7. `reporting/`

- 负责结构化结果与最终报告输出
- 关键文件：
  - [brain/reporting/report_builder.py](/Users/loki/Workspace/GraduationDesign/brain/reporting/report_builder.py)

### 8. `shared/`

- 放纯函数级公共能力
- 当前主要包括：
  - [brain/shared/errors.py](/Users/loki/Workspace/GraduationDesign/brain/shared/errors.py)
  - [brain/shared/normalization.py](/Users/loki/Workspace/GraduationDesign/brain/shared/normalization.py)

### 9. `config/`

- 提供配置加载入口
- 关键文件：
  - [brain/config/loader.py](/Users/loki/Workspace/GraduationDesign/brain/config/loader.py)

## 当前主链

当前一轮问诊的真实主链是：

1. `ConsultationBrain.process_turn()`
2. `TurnCoordinator.process_turn()`
3. `BrainRuntime._process_turn_impl()`
4. `turn_interpreter -> mention merge -> pending action merge`
5. `SearchCoordinator.run_reasoning_search()`
6. `BrainRuntime._run_reasoning_search_impl()`
7. `A2 -> A3 -> MCTS/greedy/no_tree_greedy -> trajectory evaluator`
8. `AcceptanceCoordinator.finalize_from_search()`
9. `BrainRuntime._finalize_from_search_impl()`
10. `verifier acceptance / repair / rescue -> next question or final report`

详细说明见：

- [docs/brain_runtime_call_chain_guide.md](/Users/loki/Workspace/GraduationDesign/docs/brain_runtime_call_chain_guide.md)

## 当前仍保留的实验能力

为了保证 benchmark / ablation 不回退，当前依然保留下面这些配置入口：

- `search_impl = legacy | modular_v2`
- `search_policy.root_action_mode = mcts | greedy | no_tree_greedy`
- `transition_model.type = heuristic | statistical`
- `reward_model.type = heuristic_v2 | belief_aware_v1`
- `repair`、`candidate_feedback`、`acceptance_calibration` 等现有 YAML 键名全部兼容

默认配置仍来自：

- [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)

并且仍支持：

- `BRAIN_CONFIG_PATH`

## 当前重点行为约定

### 1. LLM-first

- 长文本解释走 `LLM-first`
- 仅保留极薄的短答直通规则
- LLM 不可用时会抛显式领域错误，不再静默伪装成正常问诊结果

### 2. 单轮只解释一次

- 当前每轮先统一解释 `mentions`
- 再由同一份解释结果派生 `PatientContext`、`A1`、`pending_action_result`

### 3. 患者已明确说过的内容直接视为 confirmed evidence

- 只要患者已有陈述能和当前 `R2` 节点语义对上
- 就直接补成 graph-grounded `evidence_state`
- 并从后续追问动作池里剔除，避免重复问

### 4. completed 只由 verifier-like 信号驱动

- 当前是否完成，不再走旧 stop-rule 主链
- 最终由 `TrajectoryEvaluator + VerifierAcceptanceController` 联合决定

## 已删除的历史残留

以下内容已不再属于当前主链：

- `brain/types.py`
- `brain/session_dag.py`
- `LearnedResponseTransitionModel` 占位实现

## 推荐阅读顺序

如果你现在要 debug，建议按下面顺序读：

1. [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
2. [brain/app/brain.py](/Users/loki/Workspace/GraduationDesign/brain/app/brain.py)
3. [docs/brain_runtime_call_chain_guide.md](/Users/loki/Workspace/GraduationDesign/docs/brain_runtime_call_chain_guide.md)
4. [brain/turn/parser.py](/Users/loki/Workspace/GraduationDesign/brain/turn/parser.py)
5. [brain/search/hypothesis_manager.py](/Users/loki/Workspace/GraduationDesign/brain/search/hypothesis_manager.py)
6. [brain/search/mcts.py](/Users/loki/Workspace/GraduationDesign/brain/search/mcts.py)
7. [brain/search/evaluator.py](/Users/loki/Workspace/GraduationDesign/brain/search/evaluator.py)
8. [brain/acceptance/controller.py](/Users/loki/Workspace/GraduationDesign/brain/acceptance/controller.py)
