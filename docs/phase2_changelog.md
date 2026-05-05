# 第二阶段实现 Changelog 与问题改进记录

本文件用于系统记录第二阶段“问诊大脑 + 虚拟病人 + 搜索推理”在实际实现过程中的阶段目标、暴露问题、改进动作与阶段性结果。它的用途主要有两个：

- 作为项目内部的阶段变更记录，帮助后续继续开发时快速回忆“为什么这样改”
- 作为后续毕业论文撰写的过程材料，便于说明第二阶段并不是一次性完成，而是围绕关键问题持续迭代得到的结果

与 [phase2_execution_checklist.md](/Users/loki/Workspace/GraduationDesign/docs/phase2_execution_checklist.md) 的区别是：

- `phase2_execution_checklist.md` 更偏“路线设计与待办清单”
- 本文更偏“已经发生过哪些阶段性变化、分别解决了什么问题”

## 近期更新：2026-05-05 论文方法部分文稿修订（第 3 章 3.2 / 3.3 / 3.4）

## 近期更新：2026-05-05 回退到 benchmark 基线，并补三项低风险防空转修复

### 本次目标

- 在不继续扩大算法改动面的前提下，回退到上一版 `smoke60` 基线
- 只修 replay 中最确定、最浪费 turn 的三类问题：
  - 连续两轮原样重复同一句问法
  - 高成本检查收到“没做过 / 阴性”后仍追同一家族模板
  - 连续检查落空后没有及时退回低成本定义性证据

### 本次改动

- [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - 新增问句指纹记录：
    - 每轮真正发出去的问句会记录 `last_question_fingerprint`
    - 若下一轮候选动作渲染出的问句与上一轮完全相同，则直接判为不可问
  - 新增高成本检查负反馈冷却：
    - 对 `collect_general_exam_context / collect_exam_context`
    - 以及 `needs_lab_test / needs_imaging / needs_pathogen_test`
    - 若患者回答为“没做过 / 不清楚 / 阴性”类反馈，会写入短期 family cooldown
    - 冷却 key 会按 `target`、`exam_kind` 与 `HIV 相关检查 / 胸部 CT / 眼科检查 / GI 检查` 等模板族聚类
  - 新增连续检查落空后的低成本定义性回退：
    - 若高成本检查连续两轮都只得到 `no_result` 反馈
    - 后续优先启用 low-cost explorer
    - 且若存在定义性低成本证据，会优先回退到这类动作，而不是继续围绕检查模板打转
  - `filtered_repeated_action_reason` 现在会显式区分：
    - `same_question_as_previous_turn`
    - `negative_feedback_cooldown`
    - `target_already_asked`
    - `exam_context_resolved`
- [tests/test_service_low_cost_explorer_priority.py](/Users/loki/Workspace/GraduationDesign/tests/test_service_low_cost_explorer_priority.py)
  - 新增 3 个回归测试：
    - 连续相同问句拦截
    - HIV 高成本检查负反馈冷却
    - 连续检查落空后的低成本定义性回退
- [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)
  - 补充 service 层两条新约束：
    - 同句连续拦截
    - 高成本检查负反馈后的 family cooldown 与低成本定义性回退

### 预期影响

- `smoke60` 中连续重复问句、HIV 相关检查连问、胸部 CT 模板空转应减少
- 8 轮预算会更少浪费在“患者已经明确说没做过/没结果”的检查链上
- 这一轮更像“压 turn 浪费、保 baseline”，不是继续扩大全链路算法改造

### 验证结果

- 已完成定向测试：
  - `conda run -n GraduationDesign python -m pytest tests/test_service_low_cost_explorer_priority.py -q`
  - `conda run -n GraduationDesign python -m pytest tests/test_action_builder.py -q`
  - `conda run -n GraduationDesign python -m pytest tests/test_exam_context_flow.py -q`
  - `conda run -n GraduationDesign python -m pytest tests/test_replay_engine.py -q`
- 结果：
  - `7 passed`
  - `13 passed`
  - `7 passed`
  - `5 passed`

### 本次目标

- 按论文写作口径收紧第 3 章方法部分表达，降低“工程说明”语气
- 统一方法章节中的中英文术语，减少除专有名词外的中英文混杂
- 补强多轮问诊主流程的形式化表达，并同步理顺相关图号

### 本次改动

- [面向 HIV_AIDS 场景的多轮问诊系统设计.docx](/Users/loki/Workspace/GraduationDesign/面向%20HIV_AIDS%20场景的多轮问诊系统设计.docx)
  - 第 `3.2` 节：
    - 强化“搜索专用知识图谱”围绕 `R1 / R2` 服务问诊搜索的任务导向表述
    - 补写节点类型与关系类型的正文概括，降低对表格的单独依赖
    - 将关系设计描述改写为中文学术表达，减少内部英文标签直接暴露
    - 收紧“图谱入库与查询支持”段落，使其更突出对问诊方法的支撑作用
  - 第 `3.3` 节：
    - 将 `verify / repair`、`rollout`、`pending action` 等混杂术语改为中文表达
    - 将决策输出从单一动作记号改写为“动作集合或诊断结果集合”的更严谨形式
    - 补清接受条件的形式化约束，引入真实锚点、反证冲突与候选差值阈值
    - 删除重复段落，并统一算法步骤中的中文术语
  - 第 `3.4` 节：
    - 将“图谱骨架 + 受约束病人代理”链路改写为更完整的论文方法表述
    - 强化疾病级图谱审计、病例骨架生成、病人代理与自动回放之间的因果关系
    - 将“可用大模型”相关描述改为“可选增强模块”口径，避免误解为强依赖
  - 同步理顺图号引用：
    - 搜索专用知识图谱构建流程改为 `图 3.2`
    - 多轮问诊总体闭环改为 `图 3.3`
    - 树搜索后的验证修复流程改为 `图 3.4`

### 预期影响

- 方法章节的任务边界、形式化表达和术语口径更接近毕业论文常见写法
- 第 `3.2 / 3.3 / 3.4` 节之间的衔接更加一致，图号与方法链路更易对应
- 后续答辩时，针对“为什么这样设计”“公式中的条件是什么意思”“为什么不用英文术语直写”等问题会更容易解释

### 验证结果

- 已完成：
  - 重新提取并复查论文文档中第 `3.2 / 3.3 / 3.4` 节正文
  - 检查方法部分是否仍残留 `verify / repair / rollout / benchmark / pending action` 等混杂术语
- 结果：
  - 相关段落已完成改写
  - 方法部分目标术语已统一为中文口径

## 近期更新：2026-05-05 replay 自动补全问法/真值命中分析字段，并新增 balanced `smoke60` 子集

### 本次目标

- 让 replay 结果不仅能看 `top1/top3`，还能进一步判断问题出在：
  - 没问到关键证据
  - 问法分布失衡
  - 问了但没命中病例真值
  - 已经问到真值但排序或 acceptance 仍没上来
- 让 `benchmark_summary.json` 在整体汇总之外，再自动产出可直接做错误归因的分析指标
- 新增一个四类病例均衡的中等规模 smoke 子集，便于后续做比 `smoke20` 更稳的回归测试

### 本次改动

- [simulator/replay_engine.py](/Users/loki/Workspace/GraduationDesign/simulator/replay_engine.py)
  - 新增标准分析分组：
    - `STANDARD_ANALYSIS_GROUPS`
    - `STANDARD_QUESTION_GROUPS`
    - `STANDARD_COST_BUCKETS`
  - `ReplayTurn` 新增 turn 级诊断字段：
    - `asked_action_*`
    - `truth_hit`
    - `revealed_slot_*`
  - `ReplayResult` 新增：
    - `opening_revealed_slot_ids`
    - `analysis`
  - `run_case()` 现在会在每轮回放后同步构建：
    - 当前问法属于什么组
    - 是否命中病例真值
    - 实际揭示的槽位属于哪类证据
  - 单病例 replay 结束后会自动产出 case-level `analysis`，用于后续 batch 汇总
- [simulator/benchmark.py](/Users/loki/Workspace/GraduationDesign/simulator/benchmark.py)
  - 新增 `build_replay_analysis_summary()`
  - `build_benchmark_cohort_summary()` 现在会额外输出：
    - `analysis_summary`
    - `eligible_analysis_summary`
  - 分析汇总当前会自动统计：
    - `question_count_by_group`
    - `question_count_by_cost`
    - `question_truth_hit_by_group`
    - `revealed_positive_coverage_by_group`
    - `selected_action_source_count`
    - `required_family_coverage`
- [scripts/run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)
  - `_payload_to_replay_result()` 已兼容恢复上述新字段，保证断点续跑和历史结果反序列化不丢字段
- 新增 [scripts/build_smoke_case_subset.py](/Users/loki/Workspace/GraduationDesign/scripts/build_smoke_case_subset.py)
  - 支持从全量病例中按 `case_type` 做可复现均衡抽样
  - 当前已用于生成：
    - [smoke60/cases.jsonl](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260502_role_qc/smoke60/cases.jsonl)
    - [smoke60/sample_summary.json](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260502_role_qc/smoke60/sample_summary.json)
  - 抽样口径为：
    - `ordinary = 15`
    - `low_cost = 15`
    - `exam_driven = 15`
    - `competitive = 15`
- [tests/test_replay_engine.py](/Users/loki/Workspace/GraduationDesign/tests/test_replay_engine.py)
- [tests/test_benchmark.py](/Users/loki/Workspace/GraduationDesign/tests/test_benchmark.py)
- [tests/test_run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/tests/test_run_batch_replay.py)
- 新增 [tests/test_build_smoke_case_subset.py](/Users/loki/Workspace/GraduationDesign/tests/test_build_smoke_case_subset.py)
  - 新增 replay 分析字段、benchmark 分析汇总和 balanced smoke 抽样回归测试
- [docs/diagnosis_benchmark_experiment_design.md](/Users/loki/Workspace/GraduationDesign/docs/diagnosis_benchmark_experiment_design.md)
- [simulator/README.md](/Users/loki/Workspace/GraduationDesign/simulator/README.md)
- [README.md](/Users/loki/Workspace/GraduationDesign/README.md)
  - 同步补充新的 replay 分析字段、自动 cohort 分析汇总与 `smoke60` 用法说明

### 预期影响

- 后续不需要再只盯着 `top1_final_answer_hit` 这一层结果，可以直接判断：
  - 某次算法改动有没有让系统多问到真实阳性
  - 哪类证据明明病例里存在，却始终没有被揭示
  - `low_cost` / `exam_driven` / `competitive` 是否出现了问法偏移
- `smoke60` 可以作为 `smoke20` 和 `full227` 之间的中间层回归集：
  - 比 `smoke20` 更均衡
  - 比 `full227` 更便宜

### 验证结果

- 已执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_replay_engine.py tests/test_benchmark.py tests/test_run_batch_replay.py tests/test_build_smoke_case_subset.py -q
```

- 结果：
  - `28 passed`

## 近期更新：2026-05-05 支持 `KG + Greedy` 根动作选择开关，并同步 benchmark 文档口径

### 本次目标

- 把 `KG + Greedy` 从口头 benchmark 方案落成真正可切换的代码开关
- 保持默认主链路仍为 MCTS，不影响当前 full-system 配置
- 同步清理 benchmark 文档中已经完成的待办式描述

### 本次改动

- [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - 新增 `SearchPolicyConfig`
  - `BrainDependencies` 新增 `search_policy`
  - 默认构造现在会读取：
    - `search_policy.root_action_mode`
  - `run_reasoning_search()` 根动作选择改为经过策略分发：
    - `mcts`：保持现有 `select_root_action()`
    - `greedy`：切到贪心根动作选择
  - `search_metadata` 新增：
    - `root_action_mode`
- [brain/mcts_engine.py](/Users/loki/Workspace/GraduationDesign/brain/mcts_engine.py)
  - 抽出根节点可选 child 收集逻辑
  - 新增 `select_root_action_greedy()`：
    - 按 `prior_score`
    - 再按 `visit_count`
    - 再按 `node_id`
    选择 root action
- [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)
  - 新增：
    - `search_policy.root_action_mode: mcts`
- [tests/test_mcts_engine.py](/Users/loki/Workspace/GraduationDesign/tests/test_mcts_engine.py)
- [tests/test_service_config.py](/Users/loki/Workspace/GraduationDesign/tests/test_service_config.py)
  - 新增 greedy 根动作与配置映射回归测试
- [docs/diagnosis_benchmark_experiment_design.md](/Users/loki/Workspace/GraduationDesign/docs/diagnosis_benchmark_experiment_design.md)
  - `KG + Greedy` 改为“当前直接可跑”
  - 删除“还需补 `KG + Greedy` 开关”的待办式描述
  - 同步更新 smoke / full benchmark 执行顺序
- [README.md](/Users/loki/Workspace/GraduationDesign/README.md)
- [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)
  - 同步补充 `search_policy.root_action_mode` 入口说明

### 预期影响

- 当前 benchmark 已可以直接切换：
  - `Full System`
  - `KG + Greedy`
- 后续如果要比较“树搜索是否必要”，不需要再手改 service 逻辑，只需切配置
- replay 产物会直接带上 `search_metadata.root_action_mode`，便于离线确认本轮跑的是哪种策略

### 验证结果

- 已执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_mcts_engine.py tests/test_service_config.py -q
```

- 结果：
  - `7 passed`

## 近期更新：2026-05-05 落地诊断系统 benchmark 可执行实验设计文档

### 本次目标

- 把当前论文实验与内部消融的 benchmark 方案整理成一份可直接执行的设计文档
- 明确哪些实验当前就能跑，哪些需要先补最小工程能力
- 统一 benchmark 的病例集选择、指标口径、输出目录命名和执行顺序

### 本次改动

- 新增：
  - [docs/diagnosis_benchmark_experiment_design.md](/Users/loki/Workspace/GraduationDesign/docs/diagnosis_benchmark_experiment_design.md)
    - 明确当前代码现实：
      - 结构化 `stop rule` 已删除
      - 当前 completed 由 verifier-only acceptance 控制
      - `Opening-Only` 可直接通过 `--max-turns 0` 运行
      - `KG + Greedy` 仍需补主链路开关
      - `LLM + 文本 RAG` 不纳入本轮正式 benchmark
    - 收敛出第一轮正式主表建议：
      - `Full System`
      - `Opening-Only`
      - `KG + Greedy`
      - `No-Repair`
      - `No Scope-Aware Rerank`
    - 补齐统一实验设置：
      - `smoke20` 仅用于联调
      - `eligible112` 用作正式主 benchmark 集
      - `error_focus_smoke95` 只做错误回归验证
    - 给出输出目录命名规范、命令模板和正式执行顺序
    - 明确正式 benchmark 前建议补的最小工程能力：
      - `run_batch_replay.py` 增加 variant / config override 能力
      - `brain/service.py` 增加 `KG + Greedy` 根动作选择开关
      - 新增矩阵驱动脚本统一调度多变体运行
- 更新：
  - [README.md](/Users/loki/Workspace/GraduationDesign/README.md)
    - 新增 benchmark 实验设计文档入口
  - [simulator/README.md](/Users/loki/Workspace/GraduationDesign/simulator/README.md)
    - 在详细方案文档区新增 benchmark 设计入口
  - [docs/diagnosis_system_todolist.md](/Users/loki/Workspace/GraduationDesign/docs/diagnosis_system_todolist.md)
    - 在 benchmark 体系待办段落补入新设计文档入口

### 预期影响

- 后续 benchmark 不再停留在口头方案，而是可以按统一矩阵直接执行
- 论文实验章节可以更清楚地区分：
  - 主表内部消融
  - 小样本 focused ablation
  - 错误集回归验证
- 避免把当前仓库里尚不存在的 `LLM + 文本 RAG` 或 `No-Verifier` 实验提前写成正式必做项

### 验证结果

- 本次为文档与入口整理，不涉及代码逻辑改动
- 未新增单元测试与 replay 运行

## 近期更新：2026-05-05 batch replay 自动补全病例分层字段，并产出 overall / eligible / case-type cohort 指标

### 本次目标

- 让正式 benchmark 可以以 `full227` 作为主运行集，只跑一次 replay
- 在回放结果中直接保留病例分层字段，避免后处理时再人工回连
- 在 `benchmark_summary.json` 中自动输出 overall、`eligible` 与各病例类型的指标

### 本次改动

- [simulator/replay_engine.py](/Users/loki/Workspace/GraduationDesign/simulator/replay_engine.py)
  - `ReplayResult` 新增字段：
    - `case_type`
    - `case_qc_status`
    - `benchmark_qc_status`
    - `case_qc_reasons`
  - 新增 `extract_case_benchmark_fields()`，统一从病例元数据中提炼 replay 分层字段
  - 正常单病例 replay 结果现在会直接带上这些 cohort 字段
- [scripts/run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)
  - `_build_unexpected_case_failure_result()` 也会写入病例分层字段，避免 failed 病例缺少 cohort 信息
  - `_payload_to_replay_result()` / resume 逻辑已兼容新的 replay 字段
  - 新增：
    - `_enrich_replay_result_from_case()`
    - `_enrich_replay_results_from_cases()`
  - 历史输出目录断点续跑时，会根据当前 `cases.jsonl` 自动补齐旧结果里缺失的 cohort 字段
  - `_build_summary_payload()` 现在会额外调用 cohort 汇总逻辑，把以下结构写入 `benchmark_summary.json`：
    - `eligible_summary`
    - `case_qc_status_summaries`
    - `benchmark_qc_status_summaries`
    - `case_type_summaries`
    - `metadata_field_coverage`
- [simulator/benchmark.py](/Users/loki/Workspace/GraduationDesign/simulator/benchmark.py)
  - 新增：
    - `benchmark_summary_to_payload()`
    - `build_benchmark_cohort_summary()`
  - 支持按：
    - `case_qc_status`
    - `benchmark_qc_status`
    - `case_type`
    自动分组生成 benchmark 指标
  - `non_completed_cases.json` 的单病例记录也新增：
    - `case_type`
    - `case_qc_status`
    - `benchmark_qc_status`
    - `case_qc_reasons`
- [docs/diagnosis_benchmark_experiment_design.md](/Users/loki/Workspace/GraduationDesign/docs/diagnosis_benchmark_experiment_design.md)
  - benchmark 口径改为：
    - `full227` 为主运行集
    - `eligible112` 为主分析子集
  - 同步说明自动 cohort 汇总与 replay 补充字段
  - 补充主表指标口径：
    - `accepted_exact_accuracy` 不能单独解读
    - 主表需要同时看 `completion_rate / accepted_final_answer_count / average_turns / red_flag_hit_rate`
    - 读表时固定配对：
      - `top1_final_answer_hit` vs `top3_hypothesis_hit`
      - `accepted_exact_accuracy` vs `completion_rate / accepted_final_answer_count`
- [README.md](/Users/loki/Workspace/GraduationDesign/README.md)
- [simulator/README.md](/Users/loki/Workspace/GraduationDesign/simulator/README.md)
  - 同步说明 replay 与 benchmark 汇总新增的 cohort 字段和自动子集指标
- [tests/test_benchmark.py](/Users/loki/Workspace/GraduationDesign/tests/test_benchmark.py)
- [tests/test_run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/tests/test_run_batch_replay.py)
  - 新增 cohort 汇总和 replay 字段保留回归测试

### 预期影响

- 后续 benchmark 可以直接跑全量，再从同一份输出里读取：
  - overall
  - `eligible`
  - `ordinary / low_cost / exam_driven / competitive`
  - `eligible / weak_anchor / not_benchmark_eligible`
- 不再需要手工把 `replay_results.jsonl` 与原始病例文件 join 后才能算核心 cohort 指标

### 验证结果

- 计划执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_benchmark.py tests/test_run_batch_replay.py -q
```

## 近期更新：2026-05-04 补第一批 replay 复盘，并落地第二批 P3 + P6（多候选反馈 + competition repair）

### 本次目标

- 把第一批 `error_focus_smoke95` replay 的真实结果补回执行清单
- 补齐 replay turn 级 `search_report / search_metadata` 落盘，解除内部观测指标无法离线统计的问题
- 按执行清单严格落地第二批：
  - `P3`：证据反馈不再只作用当前 hypothesis
  - `P6`：`missing_key_support` 升级为 competition repair

### 本次改动

- [docs/diagnosis_algorithm_batch_execution_checklist.md](/Users/loki/Workspace/GraduationDesign/docs/diagnosis_algorithm_batch_execution_checklist.md)
  - 补入第一批实际 replay 结果：
    - `top3_hypothesis_hit: 24 -> 37`
    - `top1_final_answer_hit: 10 -> 12`
    - `average_revealed_slots: 0.79 -> 1.42`
    - `completed: 7 -> 21`
    - `failed::llm_stage_failed: 29 -> 0`
  - 同步记录当前新瓶颈：
    - `true_candidate_missing: 28 -> 30`
    - `true_candidate_but_final_wrong: 19 -> 29`
    - `wrong_accepted_count: 4 -> 15`
  - 增补内部观测说明：
    - 历史 replay turn 未真正落 `search_report / search_metadata`
    - 本轮已补落盘，后续 replay 可以离线统计内部指标
- [simulator/replay_engine.py](/Users/loki/Workspace/GraduationDesign/simulator/replay_engine.py)
  - `ReplayTurn` 新增：
    - `search_report`
    - `search_metadata`
  - 每轮患者回答后的 brain 输出现在会按 turn 落盘 `search_report/search_metadata`
- [scripts/run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)
  - `_payload_to_replay_result()` 同步恢复新的 turn 级回放字段
- [brain/hypothesis_manager.py](/Users/loki/Workspace/GraduationDesign/brain/hypothesis_manager.py)
  - `HypothesisManagerConfig` 新增第二批开关：
    - `enable_multi_hypothesis_feedback`
    - `use_scope_weighted_feedback`
    - `max_related_hypotheses_per_evidence`
  - `build_hypothesis_scores()` 会保留 `evidence_node_ids`
  - `apply_evidence_feedback()` 新增 `feedback_weights` 支持
  - 新增多候选 fan-out 权重解析逻辑：
    - 结合 `evidence_node_ids / evidence_names / relation_types`
    - 再结合 `observed_anchor_score / exact_scope_anchor_score / family_scope_anchor_score / anchor_tier`
- [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - 新增 turn 级 evidence feedback 观测：
    - `turn_evidence_feedback`
    - `multi_hypothesis_feedback_hit_count`
    - `multi_hypothesis_feedback_hit_rate`
  - 真实 pending-action 反馈、exam-context 反馈、generic exam link 反馈统一切到多候选 fan-out 更新
  - `RepairPolicyConfig` 新增第二批开关：
    - `enable_missing_key_support_competition_escalation`
    - `missing_key_support_retry_threshold`
    - `zero_anchor_current_answer_force_competition`
  - `missing_key_support` 新增升级逻辑：
    - 当前答案 `observed_anchor_score = 0`
    - 或同一答案连续达到 `missing_key_support_retry_threshold`
    - 或 verifier 推荐证据明显偏高成本，且备选已有更强真实锚点
    - 满足条件时切到 `force_competition_repair`
- [brain/simulation_engine.py](/Users/loki/Workspace/GraduationDesign/brain/simulation_engine.py)
  - rollout 内部模拟证据反馈也复用多候选 fan-out 逻辑
- [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)
  - 新增 `candidate_feedback:` 默认配置
  - 新增第二批 repair escalation 默认配置
- [tests/test_hypothesis_manager.py](/Users/loki/Workspace/GraduationDesign/tests/test_hypothesis_manager.py)
- [tests/test_simulation_engine.py](/Users/loki/Workspace/GraduationDesign/tests/test_simulation_engine.py)
- [tests/test_service_repair_flow.py](/Users/loki/Workspace/GraduationDesign/tests/test_service_repair_flow.py)
- [tests/test_replay_engine.py](/Users/loki/Workspace/GraduationDesign/tests/test_replay_engine.py)
- [tests/test_run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/tests/test_run_batch_replay.py)
- [tests/test_service_config.py](/Users/loki/Workspace/GraduationDesign/tests/test_service_config.py)
  - 补齐第二批 fan-out、competition repair、replay 落盘与配置映射回归测试
- [README.md](/Users/loki/Workspace/GraduationDesign/README.md)
- [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)
  - 同步说明第二批改造与 replay 元数据能力

### 预期影响

- 优先改善：
  - `true_candidate_missing`
  - `top3_hypothesis_hit`
- 次要改善：
  - 一部分 `true_candidate_but_final_wrong`
- 同时让后续 replay 可以直接离线统计：
  - `early_exam_context_trigger_rate`
  - `repair_action_override_rate`
  - `multi_hypothesis_feedback_hit_rate`

### 验证结果

- 已执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_hypothesis_manager.py tests/test_simulation_engine.py tests/test_service_repair_flow.py tests/test_replay_engine.py tests/test_run_batch_replay.py tests/test_service_config.py -q
conda run -n GraduationDesign python -m pytest -q
```

- 结果：
  - `50 passed`
  - `233 passed`

## 近期更新：2026-05-04 落地第一批 P1 + P2（repair 保护 + early exam-context rescue）

### 本次目标

- 落地诊断算法三批清单中的第一批改动
- 修正 `repair_selected_action` 会被 low-cost explorer 抢走的问题
- 在前几轮且候选明显 exam-driven 时，优先把下一问拉回 `exam_context`

### 本次改动

- [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - 新增 `RepairPolicyConfig` 开关：
    - `protect_repair_action_from_low_cost_explorer`
    - `allow_low_cost_explorer_after_repair_if_unaskable_only`
  - 新增 `A3RoutingPolicyConfig`：
    - `enable_early_exam_context_rescue`
    - `early_exam_context_turn_limit`
    - `early_exam_context_revealed_count_threshold`
    - `exam_context_rescue_high_cost_role_threshold`
  - `process_turn()` 中增加动作来源记录：
    - `selected_action_source`
    - `selected_action_source_priority_rank`
    - `selected_action_source_reason`
  - verifier repair 后，若 repair 动作仍可问，则跳过 low-cost explorer，不再允许 explorer 覆盖
  - 新增 early exam rescue：
    - 前 2 轮、真实揭示证据少、top hypothesis 仍停留在 `background_supported / phenotype_supported / speculative`
    - 且候选动作明显以 `lab / imaging / pathogen` 高价值证据为主时
    - 优先选择 `collect_general_exam_context`
    - `general` 已问过后，优先选择 `collect_exam_context`
- [brain/action_builder.py](/Users/loki/Workspace/GraduationDesign/brain/action_builder.py)
  - 为 exam-context 动作新增 metadata：
    - `exam_context_entry_kind`
    - `exam_context_priority_reason`
    - `exam_context_rescue_candidate`
- [brain/report_builder.py](/Users/loki/Workspace/GraduationDesign/brain/report_builder.py)
  - `build_search_report()` 与 `build_final_reasoning_report()` 新增 `search_metadata`
- [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)
  - 新增第一批相关默认开关
- [tests/test_service_low_cost_explorer_priority.py](/Users/loki/Workspace/GraduationDesign/tests/test_service_low_cost_explorer_priority.py)
  - 新增第一批仲裁与 rescue 单测
- [tests/test_action_builder.py](/Users/loki/Workspace/GraduationDesign/tests/test_action_builder.py)
- [tests/test_service_config.py](/Users/loki/Workspace/GraduationDesign/tests/test_service_config.py)
  - 补充第一批 metadata / 配置覆盖测试
- [README.md](/Users/loki/Workspace/GraduationDesign/README.md)
- [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)
  - 同步说明第一批新增开关与动作仲裁行为

### 预期影响

- 优先改善：
  - `top3_hypothesis_hit`
  - `average_revealed_slots`
- 直接减少：
  - repair 推荐高价值检查证据却被 low-cost explorer 抢走的情况
  - exam-driven 病例前几轮一直围绕泛症状打转的情况

### 验证结果

- 已执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_service_low_cost_explorer_priority.py tests/test_service_repair_flow.py tests/test_exam_context_flow.py tests/test_action_builder.py -q
conda run -n GraduationDesign python -m pytest tests/test_service_stop_flow.py tests/test_report_builder.py tests/test_service_config.py -q
conda run -n GraduationDesign python -m pytest -q
```

- 结果：
  - `37 passed`
  - `16 passed`
  - `228 passed`

### 补充修复：2026-05-04 修正 batch replay 启动期 `a3_config` NameError

- 触发背景：
  - 第一批改动后，重新运行 `error_focus_smoke95` 时，95 个病例全部在 `turn_count=0` 直接失败
  - `run.log` / `non_completed_cases.json` 统一报错：
    - `error_code=unexpected_runtime_error`
    - `error_stage=batch_runner`
    - `message=NameError: name 'a3_config' is not defined`
- 根因：
  - [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py) 的 `build_default_brain()` 末尾已经开始读取 `a3.enable_early_exam_context_rescue` 等新配置
  - 但函数开头漏了 `a3_config = dict(config.get("a3", {}))`
  - 导致每个 worker 在构造默认 brain 时就直接抛 `NameError`，病例还没真正进入 opening / turn1
- 修复：
  - 在 `build_default_brain()` 中补回 `a3_config = dict(config.get("a3", {}))`
  - 在 [tests/test_service_config.py](/Users/loki/Workspace/GraduationDesign/tests/test_service_config.py) 新增默认构造回归测试，显式验证 `a3 / repair` 新配置会被真正映射到运行期 policy
- 验证：

```bash
conda run -n GraduationDesign python -m pytest -q
```

- 结果：
  - `228 passed`

## 近期更新：2026-05-04 新增诊断算法三批落地执行清单

### 本次目标

- 把当前围绕 `top3_hypothesis_hit / top1_final_answer_hit` 的算法改造方向落成一份可直接执行的开发清单
- 明确当前基线指标、三批推进顺序、每批的修改点、验收方式和预期改善指标
- 将新清单接入仓库级入口文档，便于后续直接按批次推进

### 本次改动

- 新增：
  - [docs/diagnosis_algorithm_batch_execution_checklist.md](/Users/loki/Workspace/GraduationDesign/docs/diagnosis_algorithm_batch_execution_checklist.md)
- 更新：
  - [docs/phase2_execution_checklist.md](/Users/loki/Workspace/GraduationDesign/docs/phase2_execution_checklist.md)
  - [README.md](/Users/loki/Workspace/GraduationDesign/README.md)
  - [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)

### 具体内容

- 新清单文档在开头明确记录当前算法基线：
  - `top3_hypothesis_hit = 24/95 = 25.3%`
  - `top1_final_answer_hit = 10/95 = 10.5%`
  - `average_revealed_slots = 0.79`
  - `true_candidate_missing = 28`
  - `true_candidate_but_final_wrong = 19`
- 将当前改造路线按三批拆分：
  - 第一批：`P1 + P2`
    - repair 不再被 explorer 覆盖
    - 早期 `exam-context rescue`
  - 第二批：`P3 + P6`
    - 证据反馈不再只作用当前 hypothesis
    - `missing_key_support` 升级为 competition repair
  - 第三批：`P4 + P5 + P7`
    - 多分支 rollout / 防塌缩
    - final score 重平衡
    - scope-aware rerank 前移
- 对每一批都补充：
  - 重点修改文件与函数
  - 建议新增配置开关
  - 建议补充的测试与 replay
  - 预计优先改善的指标
- 在 `README.md`、`brain/README.md` 和 `phase2_execution_checklist.md` 中新增入口，方便后续从仓库主文档直接跳转到该清单

### 验证结果

- 已执行：

```bash
rg -n "diagnosis_algorithm_batch_execution_checklist|top3_hypothesis_hit|top1_final_answer_hit" README.md brain/README.md docs/phase2_execution_checklist.md docs/diagnosis_algorithm_batch_execution_checklist.md
```

- 结果：
  - 新清单文档已入库
  - README、brain README 与 phase2 执行清单已挂接新入口
  - 本次为文档与开发清单更新，未运行 Python 单元测试

## 近期更新：2026-05-03 为 evidence anchor 常量补充中文注释约定

### 本次目标

- 给 `brain/evidence_anchor.py` 中一组较难直观看懂的 anchor 常量补充中文用途注释
- 把“新增常量定义也要带中文注释”固化进仓库级 agent 约定
- 同步把这条注释规范写入 `brain/README.md`

### 本次改动

- [brain/evidence_anchor.py](/Users/loki/Workspace/GraduationDesign/brain/evidence_anchor.py)
  - 为下列常量补充中文注释，解释它们在 anchor 分类、low-cost profile 和 evidence role 判断中的作用：
    - `STRONG_ANCHOR_LABELS`
    - `ANCHOR_RELATION_TYPES`
    - `DETAIL_DEFINITION_RELATION_TYPES`
    - `DISEASE_SELF_ANCHOR_RELATION_TYPE`
    - `BACKGROUND_FAMILY_TAGS`
    - `LOW_COST_PROFILE_EXCLUDED_FAMILY_TAGS`
    - `SPECIFIC_ANCHOR_FAMILY_TAGS`
    - `DEFINITION_FAMILY_TAGS`
- [AGENTS.md](/Users/loki/Workspace/GraduationDesign/AGENTS.md)
  - 新增长期约定：模块级常量，尤其是标签集合、关系集合、family tag、状态枚举、阈值分组等定义前，要补中文用途注释
  - 明确以后新增常量时也默认遵守该约定
- [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)
  - 在代码注释规范中补充“模块级常量也要有中文用途注释”

### 验证结果

- 已执行：

```bash
python -m py_compile brain/evidence_anchor.py
conda run -n GraduationDesign python -m pytest tests/test_evidence_anchor.py -q
```

- 结果：
  - `brain/evidence_anchor.py` 语法检查通过
  - `tests/test_evidence_anchor.py` 通过

## 近期更新：2026-05-03 清理仓库内无引用的历史函数

### 本次目标

- 扫描当前仓库中已经没有调用链的历史兼容接口与残留辅助函数
- 只删除“静态扫描无引用 + 人工复核确认不在当前主链路”的函数，避免误删仍在间接使用的公共工具
- 用最小范围测试确认当前问诊主流程、前端适配和图谱审计没有被清理影响

### 本次改动

- [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - 删除仓库内无引用的旧兼容接口：
    - `apply_updates()`
    - `get_next_question()`
    - `ingest_patient_turn()`
- [brain/retriever.py](/Users/loki/Workspace/GraduationDesign/brain/retriever.py)
  - 删除无引用的旧兼容检索包装：
    - `get_forward_hypotheses()`
    - `get_reverse_validation_questions()`
- [brain/evidence_parser.py](/Users/loki/Workspace/GraduationDesign/brain/evidence_parser.py)
  - 删除旧 A1 / exam-context 残留辅助函数：
    - `build_slot_updates_from_a1()`
    - `_run_a1_with_llm()`
    - `_infer_exam_availability()`
    - `_extract_mentioned_exam_results()`
    - `_coerce_resolution_value()`
- [brain/normalization.py](/Users/loki/Workspace/GraduationDesign/brain/normalization.py)
  - 删除无引用的旧包装：
    - `expand_graph_mentions()`
    - `_template_graph_mentions()`
- [brain/state_tracker.py](/Users/loki/Workspace/GraduationDesign/brain/state_tracker.py)
  - 删除无引用的状态克隆包装：
    - `clone_session_state()`
- [brain/stop_rules.py](/Users/loki/Workspace/GraduationDesign/brain/stop_rules.py)
  - 删除已不再被当前搜索主流程调用的旧 stop/fallback 包装：
    - `should_fallback()`
    - `should_stop_rollout()`
    - `should_stop_search()`
- [frontend/ui_adapter.py](/Users/loki/Workspace/GraduationDesign/frontend/ui_adapter.py)
  - 删除无引用展示翻译函数：
    - `translate_existence()`
    - `translate_certainty()`
- [simulator/graph_audit.py](/Users/loki/Workspace/GraduationDesign/simulator/graph_audit.py)
  - 删除未再接入当前规则审计主路径的历史辅助函数：
    - `_audit_single_evidence_item()`
    - `_audit_duplicate_evidence()`
- [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)
  - 同步记录本轮已清理无引用历史接口，当前默认只保留主链路仍在消费的入口

### 验证结果

- 已执行：

```bash
python -m py_compile brain/service.py brain/retriever.py brain/evidence_parser.py brain/normalization.py brain/state_tracker.py brain/stop_rules.py frontend/ui_adapter.py simulator/graph_audit.py
conda run -n GraduationDesign python -m pytest tests/test_service_stop_flow.py tests/test_service_repair_flow.py tests/test_exam_context_flow.py tests/test_retriever.py tests/test_stop_rules.py tests/test_frontend_ui_adapter.py tests/test_graph_audit.py -q
```

- 结果：
  - `py_compile` 通过
  - 相关测试 `54 passed`

## 近期更新：2026-05-03 运行链路文档切换到三阶段与 anchor 口径

### 本次目标

- 更新 `brain` 运行链路说明，去掉旧 `A1/A2/A3/A4 + stop rule` 叙述
- 让仓库内对外说明与当前代码实现保持一致
- 明确当前主流程已经是 `A1 / A2 / A3 + pending action + anchor-controlled acceptance/repair`

### 本次改动

- [docs/brain_runtime_call_chain_guide.md](/Users/loki/Workspace/GraduationDesign/docs/brain_runtime_call_chain_guide.md)
  - 整体重写运行链路说明
  - 将主流程改写为：
    - 每轮统一 `turn_interpreter`
    - 先消化 `pending_action`
    - 再在 `A1 / A2 / A3` 三阶段间切换
    - 最后由 `observed anchor + verifier repair` 决定停机或继续追问
  - 明确说明旧 `A4` 当前主要拆散到 `pending action` 解释、证据写回和路由阶段
  - 明确说明 [`brain/stop_rules.py`](/Users/loki/Workspace/GraduationDesign/brain/stop_rules.py) 文件名虽保留，但默认语义已是 `anchor_controlled acceptance gate`
- [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)
  - 在运行链路入口处补充说明，提示当前详细文档已切到三阶段 + anchor 口径
- [README.md](/Users/loki/Workspace/GraduationDesign/README.md)
  - 在总 README 的运行链路指南入口处补充当前口径说明

### 验证结果

- 已执行：

```bash
rg -n "A4|stop rule|anchor|pending action|A1 / A2 / A3" docs/brain_runtime_call_chain_guide.md brain/README.md README.md
```

- 结果：
  - 新版运行链路文档已改为三阶段主流程描述
  - README 入口文字已同步到 `A1 / A2 / A3 + anchor` 口径
  - 本次为文档更新，未运行 Python 单元测试

## 近期更新：2026-05-03 benchmark 最终答案指标收口

### 本次目标

- 修正 `hypothesis_hit_rate` 容易被误读为最终诊断准确率的问题
- 在批量 replay 的 `benchmark_summary.json` 中直接输出最终答案层面的严格与宽松指标
- 为正式 benchmark 前的 pilot 分析提供 wrong accepted 与 top 正确但被拒绝的结构化计数
- 在批量 replay 输出目录中新增异常诊断病例索引，方便全量 benchmark 后快速定位没有 `completed` 的样本
- 将全量 replay 中未完成的虚拟病人骨架精确抽出，形成后续回归 smoke 集

### 本次改动

- [simulator/benchmark.py](/Users/loki/Workspace/GraduationDesign/simulator/benchmark.py)
  - 保留旧 `hypothesis_hit_count / hypothesis_hit_rate` 作为候选列表命中率
  - 新增最终 top answer 指标：
    - `final_answer_exact_hit_count / final_answer_exact_hit_rate`
    - `final_answer_family_hit_count / final_answer_family_hit_rate`
  - 新增 accepted answer 指标：
    - `accepted_final_answer_count`
    - `accepted_exact_hit_count / accepted_exact_accuracy`
    - `accepted_family_hit_count / accepted_family_accuracy`
    - `wrong_accepted_count`
    - `family_wrong_accepted_count`
  - 新增拒停诊断指标：
    - `top_exact_correct_but_rejected_count`
    - `top_family_correct_but_rejected_count`
  - 新增 `build_non_completed_case_report()`，按 `failed::*`、`max_turn_reached::top_exact_correct_but_rejected`、`max_turn_reached::true_candidate_but_final_wrong`、`max_turn_reached::no_final_answer` 等类别输出未完成病例复盘记录
  - 宽松/家族级匹配暂用归一化包含关系 + 名称相似度阈值，后续可替换为 KG disease family / parent-child evaluator
- [scripts/run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)
  - 每次刷新 `benchmark_summary.json` 时同步刷新 `non_completed_cases.json`
  - `non_completed_cases.json` 只收 `status != completed` 的病例，并保留真实诊断、最终答案、候选 top5、最后一轮问答、错误信息和病例级耗时
- [scripts/build_non_completed_smoke_set.py](/Users/loki/Workspace/GraduationDesign/scripts/build_non_completed_smoke_set.py)
  - 读取完整病例集与 `non_completed_cases.json`，按未完成 `case_id` 精确抽取病例骨架
  - 输出 `cases.jsonl / cases.json / manifest.json / summary.md`，并在 manifest 中保留异常类别、病例类型、真实诊断、最终答案和 stop reason
- [tests/test_benchmark.py](/Users/loki/Workspace/GraduationDesign/tests/test_benchmark.py)
  - 覆盖严格命中、宽松命中、错答 accepted、top 正确但被拒绝四类场景
- [tests/test_build_non_completed_smoke_set.py](/Users/loki/Workspace/GraduationDesign/tests/test_build_non_completed_smoke_set.py)
  - 覆盖按 `non_completed_cases.json` 精确抽取病例、按异常类别过滤、Markdown 摘要渲染
- [simulator/README.md](/Users/loki/Workspace/GraduationDesign/simulator/README.md)
  - 同步说明 benchmark 指标已经区分候选命中与最终答案命中，并记录 `non_completed_cases.json` 与未完成 smoke 集的用途

### 新增输出

- `test_outputs/simulator_cases/graph_cases_20260502_role_qc/non_completed_smoke80/cases.jsonl`
- `test_outputs/simulator_cases/graph_cases_20260502_role_qc/non_completed_smoke80/cases.json`
- `test_outputs/simulator_cases/graph_cases_20260502_role_qc/non_completed_smoke80/manifest.json`
- `test_outputs/simulator_cases/graph_cases_20260502_role_qc/non_completed_smoke80/summary.md`

本次从全量 227 例 replay 中抽出 80 个未完成病例，分布为：

- 异常类别：`no_final_answer=11 / top_exact_correct_but_rejected=25 / top_family_correct_but_rejected=3 / true_candidate_but_final_wrong=7 / true_candidate_missing=34`
- 病例类型：`competitive=9 / exam_driven=14 / low_cost=43 / ordinary=14`

### 对 role-QC smoke20 的重新解释

使用新指标重新读取现有：

`test_outputs/simulator_replay/graph_cases_20260502_role_qc_smoke20/replay_results.jsonl`

得到：

- `completion_rate = 0.9`
- `hypothesis_hit_rate = 0.9`，仍表示真实诊断进入候选列表
- `final_answer_exact_hit_rate = 0.75`
- `final_answer_family_hit_rate = 0.85`
- `accepted_exact_accuracy = 0.7222`
- `accepted_family_accuracy = 0.8333`
- `wrong_accepted_count = 5`
- `family_wrong_accepted_count = 3`
- `top_exact_correct_but_rejected_count = 2`
- `top_family_correct_but_rejected_count = 2`

### 验证结果

- 已执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_benchmark.py tests/test_run_batch_replay.py tests/test_build_non_completed_smoke_set.py -q
conda run -n GraduationDesign python -m pytest -q
```

- 结果：
  - `tests/test_benchmark.py tests/test_run_batch_replay.py tests/test_build_non_completed_smoke_set.py`: `16 passed`
  - 全量测试：`207 passed`

## 近期更新：2026-05-02 虚拟病人病例生成 role-QC 升级

### 本次目标

- 让病例生成 catalog 从“满足 family 数量”升级为“同时保证 family 质量”
- 不针对 smoke10 个例写补丁，而是用 evidence role 和疾病大类核心路径做通用病例 QC
- 过滤 CD4、HIV、年龄、既往病史、ART 等纯背景线索独立构成 benchmark 病例的情况
- 重新生成一份 role-QC 病例集，并抽取 20 例 smoke 输入

### 本次改动

- [simulator/graph_case_generator.py](/Users/loki/Workspace/GraduationDesign/simulator/graph_case_generator.py)
  - 新增病例级 `case_qc_score / case_qc_status / case_qc_reasons`
  - 新增 evidence role：`disease_specific_anchor / definition_anchor / phenotype_support / risk_or_context / background_context`
  - 新增疾病大类核心路径检查：
    - 感染类要求 pathogen / disease-specific lab / serology / imaging 至少一类特异证据
    - 代谢/定义类要求 metabolic definition lab/detail/risk
    - 肿瘤类要求 imaging / pathology / tumor marker / 肿瘤相关定义证据
    - 神经类要求 neurologic phenotype / CNS imaging / CNS lab
  - 新增高连接证据统计，跨疾病出现过多或背景型证据不能单独撑起病例
  - 保留兼容字段：只有 `case_qc_status=eligible` 时 `benchmark_qc_status=eligible`
- [scripts/build_graph_case_smoke_set.py](/Users/loki/Workspace/GraduationDesign/scripts/build_graph_case_smoke_set.py)
  - 新增 smoke 抽样脚本，默认只抽 `case_qc_status=eligible`
  - 优先类型均衡；若某类 eligible 不足，用其他 eligible 类型补齐总数
- [scripts/run_role_qc_smoke20_replay.sh](/Users/loki/Workspace/GraduationDesign/scripts/run_role_qc_smoke20_replay.sh)
  - 新增 role-QC smoke20 一键 replay 脚本
- [.gitignore](/Users/loki/Workspace/GraduationDesign/.gitignore)
  - 放行新增的一键 replay shell 脚本
- [docs/virtual_patient_generation_scheme.md](/Users/loki/Workspace/GraduationDesign/docs/virtual_patient_generation_scheme.md)、[README.md](/Users/loki/Workspace/GraduationDesign/README.md)、[simulator/README.md](/Users/loki/Workspace/GraduationDesign/simulator/README.md)
  - 同步更新设计方案、输出目录、QC 口径和复现实验命令

### 输出结果

- 完整病例集：
  - `test_outputs/simulator_cases/graph_cases_20260502_role_qc/cases.jsonl`
  - `test_outputs/simulator_cases/graph_cases_20260502_role_qc/cases.json`
  - `test_outputs/simulator_cases/graph_cases_20260502_role_qc/manifest.json`
  - `test_outputs/simulator_cases/graph_cases_20260502_role_qc/summary.md`
- 病例总数：
  - `ordinary = 66`
  - `low_cost = 49`
  - `exam_driven = 61`
  - `competitive = 51`
  - total = `227`
- case QC：
  - `eligible = 112`
  - `weak_anchor = 46`
  - `not_benchmark_eligible = 69`
- benchmark 兼容字段：
  - `eligible = 112`
  - `ineligible = 115`
- smoke20：
  - `test_outputs/simulator_cases/graph_cases_20260502_role_qc/smoke20/cases.jsonl`
  - `ordinary = 9`
  - `low_cost = 1`
  - `exam_driven = 5`
  - `competitive = 5`

### 验证结果

- 已执行：

```bash
python -m py_compile simulator/graph_case_generator.py scripts/build_graph_case_smoke_set.py
conda run -n GraduationDesign python -m pytest tests/test_graph_case_generator.py -q
conda run -n GraduationDesign python -m pytest tests/test_graph_case_generator.py tests/test_evidence_family_catalog.py tests/test_run_batch_replay.py -q
conda run -n GraduationDesign python -m pytest -q
conda run -n GraduationDesign python scripts/generate_graph_virtual_patients.py --audit-root test_outputs/graph_audit/all_diseases_20260420_disease_aliases_only --minimum-evidence-groups-file test_outputs/evidence_family/disease_evidence_catalog_20260502/disease_minimum_evidence_groups.json --output-file test_outputs/simulator_cases/graph_cases_20260502_role_qc/cases.jsonl --output-json-file test_outputs/simulator_cases/graph_cases_20260502_role_qc/cases.json --manifest-file test_outputs/simulator_cases/graph_cases_20260502_role_qc/manifest.json --summary-file test_outputs/simulator_cases/graph_cases_20260502_role_qc/summary.md
conda run -n GraduationDesign python scripts/build_graph_case_smoke_set.py --cases-file test_outputs/simulator_cases/graph_cases_20260502_role_qc/cases.jsonl --output-root test_outputs/simulator_cases/graph_cases_20260502_role_qc/smoke20 --total-size 20 --target-size-per-type 5 --seed 42
```

- 结果：
  - `28 passed`
  - `45 passed`
  - `204 passed`
  - 生成完整病例 `227` 例
  - 抽取 smoke `20` 例，全部为 `case_qc_status=eligible`

## 近期更新：2026-05-02 stop rule 打薄与 evidence role 驱动排序

### 本次目标

- 不针对 smoke10 的个别病例写补丁，而是把诊断控制逻辑上移到通用 evidence role 与特异度
- 打薄 stop rule，删除旧 `guarded_lenient` / PCP combo 这类带医学合同感的二次 gate
- 让 A2 observed anchor 排序按“角色 + 特异度”工作：病原体、定义性检查、疾病特异检查优先，CD4/HIV/发热/年龄等高连接背景降权
- 让 repair/action 从“追当前 top hypothesis”改为“补缺失证据角色”

### 本次改动

- [brain/stop_rules.py](/Users/loki/Workspace/GraduationDesign/brain/stop_rules.py)
  - 重建为薄 stop policy：保留 turn 窗口、trajectory 门槛、verifier 明确拒绝、真实 observed anchor、更强 anchored alternative、clear negative definition evidence 与基础分数阈值
  - 删除 guarded/PCP combo/minimum evidence family 合同型判断
  - `anchor_controlled` 下，只有 `strong_anchor / definition_anchor / provisional_anchor` 可进入最终接受；`background_supported / speculative` 会以 `missing_required_anchor` 进入 repair
  - `strong_anchor / definition_anchor` 可使用更低 trajectory 门槛，避免明确真实强证据被纯轨迹数量卡死
- [brain/evidence_anchor.py](/Users/loki/Workspace/GraduationDesign/brain/evidence_anchor.py)
  - 新增通用 evidence role：`disease_specific_anchor / definition_anchor / phenotype_support / risk_or_comorbidity / background_context`
  - A2 observed anchor 排序新增 role tier 与 `role_specificity_score`
  - 疾病节点自身命中会作为 `SELF_DISEASE_MATCH` 强锚点参与排序，避免“巨细胞病毒感染”等真实疾病提及被 CD4 背景证据压住
  - CD4、HIV、免疫抑制、发热、年龄、既往病史等高连接证据统一降为 background；病原体、疾病特异化验/影像、定义性 detail 保留高权重
- [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - 移除 verifier repair 中的 guarded family / PCP combo 分支
  - repair context 新增 `missing_evidence_roles`
  - `missing_required_anchor` 下会把 top-k hypothesis 的动作纳入候选池，而不是只追当前 top
  - repair action scoring 改为通用 role score：优先 `disease_specific_anchor / definition_anchor`，降权 background-only action，同时保留推荐证据命中、区分度、新颖度和成本偏置
- [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)
  - 移除旧 stop gate 的 `guarded_lenient_early_turn_index`
- [tests/test_stop_rules.py](/Users/loki/Workspace/GraduationDesign/tests/test_stop_rules.py)、[tests/test_evidence_anchor.py](/Users/loki/Workspace/GraduationDesign/tests/test_evidence_anchor.py)、[tests/test_service_repair_flow.py](/Users/loki/Workspace/GraduationDesign/tests/test_service_repair_flow.py)
  - 更新 stop/anchor/repair 测试到 evidence role 驱动的新语义

### 影响范围

- stop rule 不再承担疾病专科诊断合同，只做通用流程停机与安全拒停
- 病原体阳性、疾病名直接命中、定义性数值/检查证据会更稳定地压过 CD4/HIV 等背景证据
- 背景证据仍可扩大候选池，但不能单独触发最终接受
- repair 在背景证据不足时会主动寻找特异锚点或定义锚点，减少沿错误 top hypothesis 一路追问的概率

### 验证结果

- 已执行：

```bash
python -m py_compile brain/stop_rules.py brain/evidence_anchor.py brain/service.py brain/hypothesis_manager.py
conda run -n GraduationDesign python -m pytest tests/test_stop_rules.py tests/test_evidence_anchor.py tests/test_service_repair_flow.py tests/test_service_config.py tests/test_hypothesis_manager.py -q
conda run -n GraduationDesign python -m pytest -q
```

- 结果：
  - `35 passed`
  - `201 passed`

## 近期更新：2026-05-02 verifier 真实证据隔离与答案聚合兜底

### 本次目标

- 修复 completed 但答案错误时暴露的关键问题：verifier 不能把 rollout 模拟路径中的阳性检查当成患者真实已确认事实
- 修复 top hypothesis 已经正确但 `best_answer=None / no_answer_score` 的断层
- 本轮只实现方案 1 和方案 3，不调整候选重排权重、guarded acceptance 或更激进的 stop 规则

### 本次改动

- [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - `_build_verifier_patient_context()` 会把累计 slot 与 evidence state 整理成 `observed_session_evidence`
  - `trajectory_agent_verifier` 看到的是累计真实会话证据，而不是只看最新一句患者回答
  - 当轨迹聚合没有具体最终答案、或只产出 `UNKNOWN` 答案组时，改用当前 A2 候选态生成保守 `FinalAnswerScore`
- [brain/trajectory_evaluator.py](/Users/loki/Workspace/GraduationDesign/brain/trajectory_evaluator.py)
  - verifier prompt 输入中显式区分 `observed_session_evidence` 和 `simulated_trajectory_evidence`
  - 新增真实证据 guard：如果接受信号只依赖 rollout 模拟阳性强证据，而真实会话没有当前答案的特异支持，则强制改为 `missing_key_support` 拒停
  - 新增 `score_candidate_hypotheses_without_trajectories()`，在轨迹答案聚合断层时把现有候选疾病转成低分兜底 answer score
- [brain/llm_client.py](/Users/loki/Workspace/GraduationDesign/brain/llm_client.py)
  - `trajectory_agent_verifier` prompt 明确要求不能把 `simulated_trajectory_evidence` 当成 confirmed evidence
- [tests/test_trajectory_evaluator.py](/Users/loki/Workspace/GraduationDesign/tests/test_trajectory_evaluator.py)、[tests/test_service_stop_flow.py](/Users/loki/Workspace/GraduationDesign/tests/test_service_stop_flow.py)
  - 覆盖“rollout 模拟阳性不能触发接受”和“UNKNOWN 答案组需要候选态兜底”的行为

### 影响范围

- `kg_ordinary_0950b716_001` 这类错误 completed 场景中，若结核阳性只来自 rollout 模拟路径而非患者真实回答，应被 verifier guard 拦下
- `kg_competitive_0950b716_vs_b247711a_001` 这类 top hypothesis 已正确但轨迹答案聚合为空的场景，不再直接落到 `best_answer=None`
- 兜底 answer score 的 `trajectory_count=0`，仍会被 stop rule 的轨迹数量门槛拦住，因此它主要服务 repair / 下一问，不会单独造成提前停诊

### 验证结果

- 已执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_trajectory_evaluator.py tests/test_llm_client_profiles.py tests/test_service_stop_flow.py tests/test_service_repair_flow.py -q
```

- 结果：
  - `39 passed`

## 近期更新：2026-05-02 repair 细粒度分流与推荐证据硬引导

### 本次目标

- 修复 guarded repair 中 `hard_negative_key_evidence` 与 `strong_unresolved_alternative_candidates` 都被压成 `strong_alternative_not_ruled_out` 的问题
- 让 `recommended_next_evidence` 在 `missing_key_support` 和硬反证修复场景下更接近“硬引导”，而不是轻量软加分
- 不改病例骨架，不放宽 stop/verifier/guarded acceptance，只修 repair 分流和动作落地排序

### 本次改动

- [brain/stop_rules.py](/Users/loki/Workspace/GraduationDesign/brain/stop_rules.py)
  - guarded block reason 到 repair reason 的映射保留细粒度原因
  - `hard_negative_key_evidence` 与 `strong_unresolved_alternative_candidates` 不再统一写成 `strong_alternative_not_ruled_out`
- [brain/hypothesis_manager.py](/Users/loki/Workspace/GraduationDesign/brain/hypothesis_manager.py)
  - `apply_verifier_repair()` 识别 guarded 细粒度 repair reason
  - 对硬反证引入独立下调幅度，对强备选未排除继续支持 alternative boost
- [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - `hard_negative_key_evidence` 优先围绕当前答案和 verifier 推荐证据修复，避免被误导到普通竞争诊断动作
  - `strong_unresolved_alternative_candidates` 继续进入竞争诊断动作池
  - `missing_key_support / hard_negative_key_evidence` 下提高推荐证据命中分权重，推荐锚点可以压过高先验泛化症状
- [brain/action_builder.py](/Users/loki/Workspace/GraduationDesign/brain/action_builder.py)
  - 高成本检查聚合成 `collect_general_exam_context` 时，把 `recommended_match_score / verifier_recommended_match_score / joint_recommended_match_score / discriminative_gain` 等字段同步到动作顶层
  - 修复 repair scorer 只能在候选 payload 里看到推荐命中、但最终排序读不到的问题

### 影响范围

- 硬反证不再自动等价于“强备选未排除”，repair 下一问会优先补当前答案的确认性锚点
- 强备选未排除仍会拉入 alternative hypothesis 动作池，保留鉴别诊断能力
- 检查上下文类动作能真实继承 verifier 推荐证据的排序优势

### 验证结果

- 已执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_stop_rules.py tests/test_service_repair_flow.py tests/test_hypothesis_manager.py tests/test_action_builder.py -q
conda run -n GraduationDesign python -m pytest tests/test_evidence_parser.py tests/test_llm_client_profiles.py tests/test_patient_agent.py tests/test_retriever.py tests/test_service_stop_flow.py tests/test_exam_context_flow.py tests/test_report_builder.py -q
```

- 结果：
  - `46 passed`
  - `57 passed`

## 近期更新：2026-05-02 no-match 语义与强证据 A2 重排

### 本次目标

- 对 `lab / imaging / pathogen`、高成本检查和疾病定义性证据，避免虚拟病人缺槽位时默认生成明确阴性
- 让病原体阳性、HIV RNA 阳性、CD4 低值等强图谱证据进入后，触发更强的 A2 refresh 和后续收束
- 不改病例骨架，不放宽 stop/verifier/guarded acceptance

### 本次改动

- [brain/llm_client.py](/Users/loki/Workspace/GraduationDesign/brain/llm_client.py)
  - `patient_slot_semantic_match` prompt 区分普通症状 no-match 与高成本检查/疾病定义性证据 no-match
  - 高成本检查/病原/检查结果缺槽位时，鼓励回答“没做过这项检查 / 没听医生提过 / 报告里没注意到”，不默认写成“没有相关情况”
  - `turn_interpreter` prompt 明确要求把“未检查/没听说”解析为 `unclear`，只有明确阴性结果或医生明确排除时才解析为 `absent`
- [simulator/patient_agent.py](/Users/loki/Workspace/GraduationDesign/simulator/patient_agent.py)
  - LLM no-match 未返回话术时，默认回退为不确定/未听医生提过，而不是明确阴性
- [brain/evidence_parser.py](/Users/loki/Workspace/GraduationDesign/brain/evidence_parser.py)
  - `turn_interpreter` 输入补充 `pending_target_label / acquisition_mode / evidence_cost / relation_type`，让 prompt 能判断当前是否是高成本检查或定义性证据
  - 对高成本检查/定义性证据的负向短答，不再直接走 deterministic absent 短路，而是交回 `turn_interpreter` prompt 结合问题语境判断
- [brain/retriever.py](/Users/loki/Workspace/GraduationDesign/brain/retriever.py)
  - R1 候选新增 `disease_specific_anchor_score`
  - R1 查询会保留证据名称、标签和关系类型的 payload，病原体、疾病名强相关证据和定义性检查会获得额外语义加权，CD4/HIV 背景等共享泛证据不再同等推动所有机会感染/肿瘤候选
- [brain/hypothesis_manager.py](/Users/loki/Workspace/GraduationDesign/brain/hypothesis_manager.py)
  - A2 竞争重排阶段消费 `disease_specific_anchor_score`
  - 提高病原/定义性证据的正向反馈倍率，让强证据进入后更容易影响下一轮主假设排序
- [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - 强阳性图谱证据写入后设置 `force_a2_refresh / force_tree_refresh`
  - 触发来源包括 `turn_interpreter`、`exam_context` 和普通 pending action 的强检查/病原证据

### 影响范围

- 缺槽位导致的检查类 no-match 不再轻易变成 hard negative
- 病原学和疾病特异证据进入后，更容易把 A2 从泛 HIV/CD4 背景候选拉回目标病
- 本次仍不改变 stop/verifier 的验收口径，只改善进入验收层之前的证据语义和候选排序

### 验证结果

- 已执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_llm_client_profiles.py tests/test_patient_agent.py tests/test_evidence_parser.py tests/test_retriever.py tests/test_hypothesis_manager.py tests/test_service_stop_flow.py tests/test_exam_context_flow.py -q
```

- 结果：
  - `56 passed`

## 近期更新：2026-05-02 修复虚拟病人到 Brain 的证据对接

### 本次目标

- 只修“证据进入 brain”和“检查上下文重复问”两件事
- 不改病例骨架，不调整候选重排权重、guarded acceptance、stop/verifier
- 让虚拟病人的 opening 与 `__exam_context__::general` 回答能更稳定落到图谱节点和 evidence state

### 本次改动

- [brain/normalization.py](/Users/loki/Workspace/GraduationDesign/brain/normalization.py)
  - 新增 `expand_graph_mentions()`，把患者口语表达扩展为多个图谱候选 surface form
  - 覆盖 CD4 低值、HIV RNA 阳性、下肢/双足发麻、药物使用、腹型肥胖等通用接口层表达
- [brain/entity_linker.py](/Users/loki/Workspace/GraduationDesign/brain/entity_linker.py)
  - 单个 mention 会查询扩展候选并统一排序
  - metadata 记录 `expanded_mentions / matched_mention / link_source / template_match`
- [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - 可信链接成功后回填 `mention.node_id`，并把 `mention.normalized_name` 更新为图谱 canonical name
  - `process_turn()` 改为先完成实体链接回填，再派生 `PatientContext / A1`
  - `exam_context` 中的 `mentioned_tests` 与 `mentioned_results.raw_text` 会再走实体链接，可信命中 `LabFinding / ImagingFinding / Pathogen` 时写入 slot/evidence_state
  - 增加 selected action 可问性过滤，防止 `__exam_context__::general` 和已问节点重复发问
- [brain/action_builder.py](/Users/loki/Workspace/GraduationDesign/brain/action_builder.py)
  - `general` 检查上下文已回答后，不再生成 `collect_general_exam_context`
  - 若 general 已问过但仍 unknown，只允许退到具体 `lab / imaging / pathogen` 检查入口
- [simulator/patient_agent.py](/Users/loki/Workspace/GraduationDesign/simulator/patient_agent.py)
  - LLM 生成 opening 时会校验检查类关键锚点；锚点丢失时退回规则模板
- [brain/llm_client.py](/Users/loki/Workspace/GraduationDesign/brain/llm_client.py)
  - `patient_opening_generation` prompt 明确要求保留数值阈值、阳性/阴性/升高/降低、具体病原体名和影像异常名

### 影响范围

- 本次只增强虚拟病人自然语言到 `brain` 图谱证据的接口层
- 不改变疾病生成算法，不改变 R1/R2 权重，不放宽 stop/verifier，也不把疾病诊断同义词硬塞进推理规则
- 预期改善：
  - opening 中的 CD4 低值、HIV RNA 阳性、病原体名等更容易进入 `linked_entities / evidence_states`
  - `__exam_context__::general` 已回答后不再在同一个抽象入口上连续追问

### 验证结果

- 已执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_entity_linker.py tests/test_exam_context_flow.py tests/test_action_builder.py tests/test_patient_agent.py tests/test_llm_client_profiles.py tests/test_replay_engine.py -q
```

- 结果：
  - `43 passed`

## 近期更新：2026-05-02 Neo4j 疾病-症状证据族目录

### 本次目标

- 在把最低证据组全面接入虚拟病例生成前，先从当前 Neo4j 导出一个只包含 `Disease` 与 `ClinicalFinding` 的中间检查文件
- 避免继续靠少数疾病名称规则手写 family requirement，先让症状节点统一归类，再为每个疾病生成 symptom-only 的最低证据组建议
- 给后续“每个疾病都有可解释 benchmark 约束”提供可人工复核的数据底稿

### 本次改动

- [simulator/evidence_family_catalog.py](/Users/loki/Workspace/GraduationDesign/simulator/evidence_family_catalog.py)
  - 新增症状节点证据族分类规则，覆盖呼吸、神经、全身、消化、皮肤黏膜、口腔耳鼻咽喉、淋巴、泌尿生殖、肌肉骨骼、心血管、血液/出血、眼部、代谢、精神心理、免疫状态、病情恶化和重症线索等 family
  - 根据疾病关联症状的 family coverage，生成每个疾病的 `minimum_evidence_groups` 建议
- [scripts/export_disease_symptom_family_catalog.py](/Users/loki/Workspace/GraduationDesign/scripts/export_disease_symptom_family_catalog.py)
  - 连接当前 Neo4j，导出 `Disease` - `MANIFESTS_AS` - `ClinicalFinding` 关系
  - 同时输出完整 catalog、症状节点清单和疾病最低症状证据组清单
- [tests/test_evidence_family_catalog.py](/Users/loki/Workspace/GraduationDesign/tests/test_evidence_family_catalog.py)
  - 覆盖常见症状分类、最低组优先级和疾病级聚合

### 新输出

- 新目录：
  - `test_outputs/evidence_family/disease_symptom_catalog_20260502/`
- 生成结果：
  - `disease_count = 80`
  - `symptom_node_count = 204`
  - `disease_symptom_edge_count = 405`
  - `unclassified_symptom_node_count = 8`
- 关键文件：
  - `disease_symptom_family_catalog.md`
  - `disease_symptom_family_catalog.json`
  - `symptom_family_nodes.json`
  - `disease_minimum_symptom_groups.json`

### 影响范围

- 当前只是生成可检查的中间层，不直接改变病例生成器、`brain` 检索、MCTS 或 stop/verifier
- 后续可以把 `disease_minimum_symptom_groups.json` 作为全疾病 minimum evidence requirement 的基础，再叠加 lab / imaging / pathogen family，形成完整 benchmark QC

### 验证结果

- 已执行：
  - `conda run -n GraduationDesign python -m pytest tests/test_evidence_family_catalog.py -q`
- 结果 `3 passed`
- 已执行 Neo4j 导出：
  - `conda run -n GraduationDesign python scripts/export_disease_symptom_family_catalog.py --output-root test_outputs/evidence_family/disease_symptom_catalog_20260502`
- 结果 `status=ok`

## 近期更新：2026-05-02 Neo4j 疾病-全证据族目录

### 本次目标

- 在 symptom-only catalog 基础上，把 `lab / imaging / pathogen / risk / detail` 一起纳入 family catalog
- 让每个疾病都能看到 symptom、risk、detail、lab、imaging、pathogen 六个证据大组下的 family coverage 和最低证据组建议
- 为后续把 full-evidence requirement 接入 `graph_case_generator.py` 提供可复核的中间产物

### 本次改动

- [simulator/evidence_family_catalog.py](/Users/loki/Workspace/GraduationDesign/simulator/evidence_family_catalog.py)
  - 保留 symptom-only catalog 能力
  - 新增 full-evidence 分类：`ClinicalFinding / RiskFactor / PopulationGroup / ClinicalAttribute / LabFinding / LabTest / ImagingFinding / Pathogen`
  - 新增 lab family：CD4/免疫状态、viral load、oxygenation、fungal marker、CNS lab、disease-specific lab、serology、blood count、liver/renal function、metabolic definition、pathology 等
  - 新增 imaging family：pulmonary / CNS / abdominal / lymph node / cardiovascular / bone imaging
  - 新增 pathogen family：fungal / mycobacterial / viral / parasitic / bacterial pathogen
  - 新增 risk/detail family：underlying infection、ART/reconstitution、exposure risk、medication risk、comorbidity risk、population risk、onset timing、severity、treatment response、location detail
- [scripts/export_disease_evidence_family_catalog.py](/Users/loki/Workspace/GraduationDesign/scripts/export_disease_evidence_family_catalog.py)
  - 连接当前 Neo4j，导出 Disease 与全类型证据节点的核心关系
  - 输出 full catalog、证据节点清单和疾病最低证据组清单
- [tests/test_evidence_family_catalog.py](/Users/loki/Workspace/GraduationDesign/tests/test_evidence_family_catalog.py)
  - 新增非 symptom 证据分类测试
  - 新增 full-evidence 疾病聚合测试

### 新输出

- 新目录：
  - `test_outputs/evidence_family/disease_evidence_catalog_20260502/`
- 生成结果：
  - `disease_count = 80`
  - `evidence_node_count = 850`
  - `disease_evidence_edge_count = 1562`
  - `unclassified_evidence_node_count = 76`
- 关键文件：
  - `disease_evidence_family_catalog.md`
  - `disease_evidence_family_catalog.json`
  - `evidence_family_nodes.json`
  - `disease_minimum_evidence_groups.json`

### 影响范围

- 当前仍是可检查中间层，不直接改变病例生成器或 `brain` 行为
- full catalog 已能为 PCP 等疾病给出跨组建议，例如 symptom/risk/lab/imaging/pathogen/detail 各组的最低 evidence family
- 后续适合把 `disease_minimum_evidence_groups.json` 接入病例生成器，作为全疾病 QC 约束来源；同时保留 disease-family 专属规则作为覆盖不足时的兜底

### 验证结果

- 已执行：
  - `conda run -n GraduationDesign python -m pytest tests/test_evidence_family_catalog.py -q`
- 结果 `5 passed`
- 已执行 Neo4j 导出：
  - `conda run -n GraduationDesign python scripts/export_disease_evidence_family_catalog.py --output-root test_outputs/evidence_family/disease_evidence_catalog_20260502`
- 结果 `status=ok`

## 近期更新：2026-05-02 病例生成器接入 full-evidence catalog 约束

### 本次目标

- 将 `test_outputs/evidence_family/disease_evidence_catalog_20260502/disease_minimum_evidence_groups.json` 接入 [simulator/graph_case_generator.py](/Users/loki/Workspace/GraduationDesign/simulator/graph_case_generator.py)
- 让每个疾病优先按 catalog 中的最低 evidence family 生成病例，而不是只依赖少数疾病大类的内置规则
- 保留内置 PCP / IRIS / CNS / 代谢类规则作为 catalog 缺失或 disease_id 未命中时的兜底

### 本次改动

- [simulator/graph_case_generator.py](/Users/loki/Workspace/GraduationDesign/simulator/graph_case_generator.py)
  - `GraphCaseGeneratorConfig` 新增 `minimum_evidence_groups_file` 和 `minimum_evidence_group_match_by_name`
  - 生成器初始化时读取 full-evidence catalog，并按 disease_id 解析每个疾病的 `minimum_evidence_groups`
  - `ordinary / low_cost / exam_driven / competitive` 的阳性槽位选择都会优先尝试覆盖 catalog family
  - 病例 metadata 新增或扩展：
    - `benchmark_requirement_source`
    - `benchmark_catalog_required_family_groups`
    - `benchmark_catalog_required_family_groups_by_evidence_group`
    - `benchmark_catalog_unavailable_family_groups`
  - catalog 来自全 Neo4j，而病例生成输入来自 disease audit 证据池；如果 catalog family 在当前 audit 证据池中不可选，会记录为 unavailable，不作为 QC 缺失项
  - `POSITIVE_SLOT_LIMIT` 从 6 调整为 8，以承接 full catalog 默认最多 8 个最低证据组
- [scripts/generate_graph_virtual_patients.py](/Users/loki/Workspace/GraduationDesign/scripts/generate_graph_virtual_patients.py)
  - 新增 `--minimum-evidence-groups-file`
  - 新增 `--minimum-evidence-group-match-by-name`
- [tests/test_graph_case_generator.py](/Users/loki/Workspace/GraduationDesign/tests/test_graph_case_generator.py)
  - 新增 catalog minimum evidence group 接入测试

### 新输出

- 新病例集目录：
  - `test_outputs/simulator_cases/graph_cases_20260502_catalog_qc/`
- 生成结果：
  - `generated_case_count = 227`
  - `generated_case_count_by_type = {"ordinary": 66, "low_cost": 49, "exam_driven": 61, "competitive": 51}`
  - `minimum_evidence_requirement_catalog.requirement_count_by_id = 80`
  - `benchmark_qc_count_by_status = {"eligible": 175, "ineligible": 52}`
  - `benchmark_eligible_count_by_type = {"ordinary": 63, "low_cost": 3, "exam_driven": 60, "competitive": 49}`

### 影响范围

- 影响后续图谱驱动虚拟病人的证据槽位选择和 benchmark QC metadata
- 不改变 `brain` 检索、MCTS、stop/verifier 或实时问诊逻辑
- 新目录 `graph_cases_20260502_catalog_qc` 可作为 full-evidence catalog 约束版病例集；旧目录 `graph_cases_20260502_family_qc` 可作为内置 family-QC 版对照

### 验证结果

- 已执行：
  - `conda run -n GraduationDesign python -m pytest tests/test_graph_case_generator.py tests/test_evidence_family_catalog.py -q`
- 结果 `30 passed`
- 已执行新病例生成：
  - `conda run -n GraduationDesign python scripts/generate_graph_virtual_patients.py --output-file test_outputs/simulator_cases/graph_cases_20260502_catalog_qc/cases.jsonl --output-json-file test_outputs/simulator_cases/graph_cases_20260502_catalog_qc/cases.json --manifest-file test_outputs/simulator_cases/graph_cases_20260502_catalog_qc/manifest.json --summary-file test_outputs/simulator_cases/graph_cases_20260502_catalog_qc/summary.md`
- 结果 `status=ok`

## 近期更新：2026-05-02 图谱虚拟病人生成器 evidence-family QC

### 本次目标

- 为毕业论文 benchmark 准备更稳定、可解释、可复现的虚拟病人病例集
- 不围绕 smoke10 个别样本手工补洞，而是在病例生成算法里加入“证据族覆盖”与“竞争负例冲突过滤”
- 保留旧病例集作为无 family-QC baseline，重新生成一版 benchmark-quality 候选集

### 本次改动

- [simulator/graph_case_generator.py](/Users/loki/Workspace/GraduationDesign/simulator/graph_case_generator.py)
  - 新增 evidence-family 分类，将证据统一映射到 `respiratory_symptom / neurologic_symptom / immune_status / art_or_reconstitution / worsening / imaging / oxygenation / fungal_marker / pathogen / metabolic_definition` 等可审计证据族
  - 新增 benchmark QC metadata：
    - `benchmark_qc_status`
    - `benchmark_required_family_groups`
    - `benchmark_missing_family_groups`
    - `evidence_family_coverage`
    - `negative_evidence_family_coverage`
  - 对 PCP、IRIS、中枢感染、代谢类疾病定义最低可判定证据族
  - `ordinary / exam_driven / competitive` 的阳性证据选择改为先覆盖 required family，再按原 priority / specificity 补满
  - `competitive` 的阴性证据会过滤与目标病名称、目标病核心定义或阳性互斥 family 冲突的项目，避免出现“目标病主诉阳性但目标病槽位阴性”
  - manifest / summary 新增 benchmark QC 统计
- [tests/test_graph_case_generator.py](/Users/loki/Workspace/GraduationDesign/tests/test_graph_case_generator.py)
  - 新增 PCP 竞争病例证据族覆盖测试
  - 新增竞争负例过滤目标病核心定义冲突测试
- [simulator/README.md](/Users/loki/Workspace/GraduationDesign/simulator/README.md)
  - 同步记录 evidence-family QC 与新病例输出目录

### 新输出

- 新病例集目录：
  - `test_outputs/simulator_cases/graph_cases_20260502_family_qc/`
- 生成结果：
  - `generated_case_count = 227`
  - `generated_case_count_by_type = {"ordinary": 66, "low_cost": 49, "exam_driven": 61, "competitive": 51}`
  - `benchmark_qc_count_by_status = {"eligible": 200, "ineligible": 27}`
  - `benchmark_eligible_count_by_type = {"ordinary": 60, "low_cost": 38, "exam_driven": 54, "competitive": 48}`

### 影响范围

- 影响图谱驱动虚拟病人的后续生成逻辑和 benchmark 病例质量
- 不改变 `brain` 的检索、MCTS、stop/verifier 或实时问诊逻辑
- 旧病例集 `graph_cases_20260426_final` 不被覆盖，可继续作为无 evidence-family QC 的对照 baseline

### 验证结果

- 已执行生成器单测：
  - `conda run -n GraduationDesign python -m pytest tests/test_graph_case_generator.py -q`
- 结果 `24 passed`
- 已执行新病例生成：
  - `conda run -n GraduationDesign python scripts/generate_graph_virtual_patients.py --output-file test_outputs/simulator_cases/graph_cases_20260502_family_qc/cases.jsonl --output-json-file test_outputs/simulator_cases/graph_cases_20260502_family_qc/cases.json --manifest-file test_outputs/simulator_cases/graph_cases_20260502_family_qc/manifest.json --summary-file test_outputs/simulator_cases/graph_cases_20260502_family_qc/summary.md`
- 结果 `status=ok`

## 近期更新：2026-05-01 虚拟病人检查上下文与候选内语义匹配

### 本次目标

- 只增强虚拟病人侧可回答性，不放宽 `brain` 的 stop / verifier
- 不把 HIV/AIDS、ART、气促、抽搐等医学同义词硬编码进诊断系统或 KG entity linker
- 让病例骨架里已经存在的检查、病原学和语义等价槽位能被虚拟病人稳定回答出来

### 本次改动

- [simulator/patient_agent.py](/Users/loki/Workspace/GraduationDesign/simulator/patient_agent.py)
  - 新增 `__exam_context__::general/lab/imaging/pathogen` 专门回答逻辑
  - `general` 汇总 `lab / imaging / pathogen` 槽位，具体类型只汇总对应 group
  - 有阳性检查真值时优先回答最多 3 条“做过，结果提示 XXX”
  - 只有阴性检查真值时回答最多 3 条“做过相关检查，没有提示 XXX”
  - 无相关检查槽位时回到 unknown，不把检查上下文误判成普通症状否定
  - `_resolve_truth()` 保留原有精确匹配；精确匹配失败且 `use_llm=True` 时，新增 LLM 候选内语义匹配分支
  - 语义匹配只允许返回病例 `candidate_slots` 中已有的 `node_id`；匹配成功后按该槽位真值回答，匹配失败时给出简短明确否定且不揭示槽位
- [brain/llm_client.py](/Users/loki/Workspace/GraduationDesign/brain/llm_client.py)
  - 新增 `patient_slot_semantic_match` prompt 与轻量输出 schema
  - prompt 明确约束候选内匹配，并给出 `HIV/AIDS ~= HIV感染/HIV感染者`、`ART ~= 抗逆转录病毒治疗/抗病毒治疗`、`活动后气促 ~= 气促/呼吸困难`、`抽搐 ~= 癫痫` 等语义等价示例
  - 调整 `patient_answer_generation` prompt：允许围绕 matched slot 做临床语义等价回答，但禁止引入病例槽位外事实
- [tests/test_patient_agent.py](/Users/loki/Workspace/GraduationDesign/tests/test_patient_agent.py)
  - 补充检查上下文阳性汇总、阴性汇总、LLM 语义命中、LLM no-match 和 `use_llm=False` fallback 测试
- [tests/test_llm_client_profiles.py](/Users/loki/Workspace/GraduationDesign/tests/test_llm_client_profiles.py)
  - 补充语义匹配 prompt 约束与病例外事实禁止 prompt 测试
- [simulator/README.md](/Users/loki/Workspace/GraduationDesign/simulator/README.md)
  - 同步记录虚拟病人检查上下文与候选内语义匹配规则

### 影响范围

- 影响虚拟病人回答生成与相关 LLM prompt
- 不改变 `brain` 的 KG 检索、entity linker、stop rules、trajectory verifier 或最终诊断接受规则
- 对 replay 的预期影响：
  - `__exam_context__::*` 提问更容易揭示病例骨架中已有的检查支持证据
  - HIV/AIDS、ART、气促、抽搐等问法与病例槽位名称不完全一致时，虚拟病人可在候选槽位内回答，而不是机械“不确定”
  - 语义匹配失败时不会污染 revealed slots

### 验证结果

- 已执行针对性回归：
  - `conda run -n GraduationDesign python -m pytest tests/test_patient_agent.py tests/test_llm_client_profiles.py -q`
- 结果 `16 passed`

## 近期更新：2026-04-30 `ClinicalFeatureItem.status` 回归修复与 batch 单病例异常保护

### 本次目标

- 修复 `certainty -> resolution / mention_state` 重构后遗留的字段兼容 bug
- 避免未来再出现“某个病例抛了普通 Python 异常，整批 replay 直接异常终止”的问题

### 本次改动

- [brain/entity_linker.py](/Users/loki/Workspace/GraduationDesign/brain/entity_linker.py)
  - 将 `link_clinical_features()` 中残留的 `item.status == "exist"` 改为新语义 `item.mention_state == "present"`
- [simulator/replay_engine.py](/Users/loki/Workspace/GraduationDesign/simulator/replay_engine.py)
  - `run_case()` 现在除了继续捕获 `BrainDomainError`，也会把普通运行时异常转成单病例 `status=failed`
  - 新增 `unexpected_runtime_error` 结构化错误负载，统一记录 `code / stage / prompt_name / message / attempts / error_type`
- [scripts/run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)
  - 新增 batch runner 级别的 `_run_single_case_guarded()`
  - 即使 `_run_single_case()` 或 worker future 内部抛出普通异常，也会尽量把该病例转成 `ReplayResult(status="failed")`，继续整批运行
- 测试补充：
  - [tests/test_entity_linker.py](/Users/loki/Workspace/GraduationDesign/tests/test_entity_linker.py)
  - [tests/test_replay_engine.py](/Users/loki/Workspace/GraduationDesign/tests/test_replay_engine.py)
  - [tests/test_run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/tests/test_run_batch_replay.py)

### 解决的问题

- 真实 `smoke10` 中曾出现：
  - `AttributeError: 'ClinicalFeatureItem' object has no attribute 'status'`
- 根因不是病例本身失败，而是：
  - `entity_linker` 里还有旧字段访问
  - `ReplayEngine` 只把 `BrainDomainError` 降成单病例 `failed`
  - `run_batch_replay.py` 在 `future.result()` 处拿到普通异常后，直接把整批 run 标成 `failed`
- 本次修复后：
  - 语义重构后的 `ClinicalFeatureItem` 能正常进入实体链接
  - 普通代码异常也会尽量落成单病例失败，不再轻易拖死整批并发回放

### 影响范围

- 影响 `brain` 的实体链接入口、`simulator` 的单病例回放引擎、`scripts/run_batch_replay.py` 的并发调度保护层
- 外部可见行为变化：
  - 某个病例若再出现未预期运行时异常，`run.log` 中应优先表现为该病例 `status=failed`
  - 整批 `status.json` 不会因为单个普通异常而直接停在 `completed_cases=0`

### 验证结果

- 已执行针对性回归：
  - `conda run -n GraduationDesign python -m pytest tests/test_entity_linker.py tests/test_replay_engine.py tests/test_run_batch_replay.py -q`
- 结果 `19 passed`
- 已执行全量回归：
  - `conda run -n GraduationDesign python -m pytest -q`
- 结果 `163 passed`

## 近期更新：2026-04-30 LLM-first 抽取与解释链路重构

### 本次目标

- 把 `MedExtractor / A1 / A4 verify_evidence / exam_context` 从“LLM 优先 + 规则 fallback”重构为“LLM 主链路 + 极薄 deterministic 层 + 薄 normalization 层 + 显式错误传播”
- 明确 batch replay、实时前端和默认 brain 构造在 `LLM` 失败场景下的外部行为

### 本次改动

- 新增 [brain/errors.py](/Users/loki/Workspace/GraduationDesign/brain/errors.py)，统一承载 `llm_unavailable / llm_timeout / llm_output_invalid / llm_empty_extraction / llm_stage_failed`
- 新增 [brain/normalization.py](/Users/loki/Workspace/GraduationDesign/brain/normalization.py)，集中收口 alias、canonical name 与常见口语映射；位置固定在 “LLM 输出之后、Neo4j / candidate mapping 之前”
- [brain/llm_client.py](/Users/loki/Workspace/GraduationDesign/brain/llm_client.py) 新增结构化 prompt 的单次重试，并把超时、空抽取、非法 payload 统一转换为领域错误
- [brain/med_extractor.py](/Users/loki/Workspace/GraduationDesign/brain/med_extractor.py) 改为长文本只接受 LLM 抽取；短答仍允许直接短路，但不再静默退回规则词典
- [brain/evidence_parser.py](/Users/loki/Workspace/GraduationDesign/brain/evidence_parser.py) 将 `A1`、`a4_target_answer_interpretation`、`exam_context_interpretation` 与 `a4_deductive_judge` 接成 LLM-first 主链路；仅保留 `有 / 没有 / 不太清楚` 一类 direct reply 的确定性短路
- [brain/entity_linker.py](/Users/loki/Workspace/GraduationDesign/brain/entity_linker.py) 改为先使用集中式 normalization，再做现有 lexical linking
- [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py) 默认构造当前要求 `llm_available=true`；若未配置可用模型，会在启动时尽早失败
- [simulator/replay_engine.py](/Users/loki/Workspace/GraduationDesign/simulator/replay_engine.py) 新增单病例 `failed` 语义；一旦 `brain` 抛出领域错误，会记录结构化 `error` 并继续整批运行
- [scripts/run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py) 启动前会检查 `llm_available`；若不可用，直接写出失败状态，不再进入半规则半模型模式
- [frontend/app.py](/Users/loki/Workspace/GraduationDesign/frontend/app.py) 当前会把领域错误翻译成更明确的中文提示，不再把所有失败都笼统归因成“可能环境未配置”
- 删除 `configs/brain.yaml` 中旧的 `a1.fallback_to_rules` 配置，改为新增 `llm.structured_retry_count: 1`

### 解决的问题

- 之前长文本链路存在“LLM 失败后偷偷退回规则”的隐式分支，导致 replay 结果表面正常，但真实诊断链路已经偏离预期
- 规则 fallback 和分散 alias 词典让模块心智负担越来越重，也让定位“到底是 LLM 失败还是词典没覆盖”变得困难
- batch replay 和实时前端过去都不够明确地区分“模型不可用”“模型输出非法”和“正常问诊结果为空”这几类场景

### 影响范围

- 影响 [brain](/Users/loki/Workspace/GraduationDesign/brain)、[simulator](/Users/loki/Workspace/GraduationDesign/simulator)、[scripts/run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)、[frontend/app.py](/Users/loki/Workspace/GraduationDesign/frontend/app.py) 以及对应测试
- 外部行为变化是显式的：
  - 默认 brain 构造要求有可用 LLM
  - batch replay 启动前若 `llm_available=false` 会直接失败
  - 单病例 LLM 领域错误会记为 `failed`
  - 实时前端遇到 LLM 领域错误会直接提示并停止当前会话

### 验证结果

- 已执行 `conda run -n GraduationDesign python -m pytest -q`
- 结果 `160 passed`

## 近期更新：2026-04-30 诊断链路语义简化与 `resolution` 收口

### 本次目标

- 把“自述症状被写成 `doubt certainty`，进而在 `A1` 被丢进 `uncertain_features`”的旧链路收掉
- 重构为：
  - `MedExtractor` 只做“提到了什么”
  - `A1` 只做“哪些提及值得进入首轮检索”
  - `A4` 才做“当前回答是否给出了清晰结论”
- 统一全链路只表达“回答清晰度 / 结论清晰度”，不再表达“医学 certainty”

### 本次改动

- [brain/types.py](/Users/loki/Workspace/GraduationDesign/brain/types.py)
  - 新增统一语义：
    - `MentionState = present / absent / unclear`
    - `Resolution = clear / hedged / unknown`
  - `ClinicalFeatureItem` 删除 `status / certainty`，新增 `mention_state`
  - `KeyFeature` 删除 `status / certainty`
  - `A1ExtractionResult` 新增 `selection_decision`
  - `SlotState`、`EvidenceState`、`SlotUpdate`、`A4DeductiveResult`、`DeductiveDecision` 全部收口到 `resolution`
- [brain/med_extractor.py](/Users/loki/Workspace/GraduationDesign/brain/med_extractor.py)
  - 长文本抽取改为“提及项抽取”，不再把自述默认打成 `doubt`
  - 增加对真实观测 payload 形态的兼容：
    - `clinical_features: "头痛、偏瘫、抽搐"`
    - `clinical_features: ["发热", "咳嗽"]`
    - `clinical_features: {"C": "体重增加"}`
    - `clinical_features: {"C": ["发烧"]}`
    - `general_info` 允许 `dict` 或 `str`
- [brain/evidence_parser.py](/Users/loki/Workspace/GraduationDesign/brain/evidence_parser.py)
  - `A1` 改为只输出 `key_features + selection_decision + reasoning_summary`
  - 对真实 A1 返回形态增加 coercion，支持对象列表、字符串列表以及布尔风格 `selection_decision`
  - 若 opening 已有明显症状提及，但模型返回空 `key_features` 或 `none_salient`，会视为无效输出而不是“患者不确定”
  - `A4` 的回答解释语义统一从 `certainty` 切到 `resolution`
- [brain/retriever.py](/Users/loki/Workspace/GraduationDesign/brain/retriever.py)、[brain/router.py](/Users/loki/Workspace/GraduationDesign/brain/router.py)、[brain/hypothesis_manager.py](/Users/loki/Workspace/GraduationDesign/brain/hypothesis_manager.py)、[brain/stop_rules.py](/Users/loki/Workspace/GraduationDesign/brain/stop_rules.py)
  - 全部改为消费 `resolution`
  - 语义固定为：
    - `exist + clear`：明确支持
    - `non_exist + clear`：明确反证
    - `exist + hedged`：弱支持，优先复核
    - `non_exist + hedged`：弱否定，不直接排除
- [frontend/ui_adapter.py](/Users/loki/Workspace/GraduationDesign/frontend/ui_adapter.py)、[frontend/app.py](/Users/loki/Workspace/GraduationDesign/frontend/app.py)、[frontend/output_browser.py](/Users/loki/Workspace/GraduationDesign/frontend/output_browser.py)
  - 展示层统一优先使用 `resolution`
  - 仍保留对旧 demo / 历史 replay 中 `certainty` 字段的兼容读取，避免旧实验记录失效
- [scripts/diagnose_smoke10_failures.py](/Users/loki/Workspace/GraduationDesign/scripts/diagnose_smoke10_failures.py)
  - 用于复现同一批 opening 的 `MedExtractor / A1` 真实 payload，并验证语义收口后是否仍有 intake 层空成功

### 解决的问题

- 之前“病人只是主动提到症状”会被系统误写成“医学上存疑”，语义边界混乱
- `A1` 经常把明显症状塞进 `uncertain_features`，导致 `key_features=[]`，看起来像 LLM 没抽到，其实是上下游契约错位
- `A4`、router、hypothesis scoring、stop rule 对 `certainty` 的消费也混入了“医学确定性”和“回答清晰度”两层含义

### 影响范围

- 影响 `brain` 主链路、replay 结果展示、诊断审计脚本和前端复盘视图
- 外部可观察变化包括：
  - intake 特征不再被当成“已验证且存疑的证据”
  - `A1` 不再输出 `uncertain_features / noise_features`
  - `A4` 与下游状态机统一使用 `resolution`
  - 历史 replay / demo 仍可读，但新输出默认不再以 `certainty` 为主字段

### 验证结果

- 已执行 `conda run -n GraduationDesign python -m pytest -q`
- 结果 `160 passed`
- 已执行：
  - `conda run -n GraduationDesign python scripts/diagnose_smoke10_failures.py --replay-dir test_outputs/simulator_replay/graph_cases_20260430_smoke10`
- 最新审计摘要：
  - `med_probe_status_counts = {"ok": 10}`
  - `a1_probe_status_counts = {"ok": 10}`
  - `med_raw_empty_like_count = 0`
  - `a1_raw_empty_like_count = 0`
- 结论：
  - `MedExtractor / A1` 的旧语义错位已经修正
  - 若小样本真实 replay 仍在 `turn 0` 失败，当前更像外部 `APIConnectionError` 或传输层问题，而不是 intake 语义问题

## 近期更新：2026-04-30 新增诊断系统 Todo 文档

### 本次目标

- 把当前诊断系统仍待完善的点系统整理成一份可持续维护的待办清单
- 让后续优化不再零散地散落在对话、日志和临时笔记里

### 本次改动

- 新增 [diagnosis_system_todolist.md](/Users/loki/Workspace/GraduationDesign/docs/diagnosis_system_todolist.md)
- 文档按 `P0 / P1 / P2` 拆分当前待完善点
- 明确写入当前固定边界：
  - `LLM-first`
  - 极薄 deterministic 层
  - normalization 在 `LLM -> Neo4j` 之间
  - 暂不优先引入 embedding / cosine entity linking
- 同步在 [README.md](/Users/loki/Workspace/GraduationDesign/README.md) 与 [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md) 中补充入口链接

### 解决的问题

- 之前“接下来该先修什么”主要分散在 replay 日志、临时分析和多轮对话里，容易重复讨论
- `LLM-first` 重构完成后，系统已经进入新的稳定边界，需要一份新的 todo 来约束后续工作顺序

### 影响范围

- 仅新增文档与索引，不改业务逻辑

### 验证结果

- 已检查文档链接路径与内容
- 本次改动不涉及代码行为变更

## 近期更新：2026-04-30 smoke10 failed opening 诊断闭环

### 本次目标

- 针对 `graph_cases_20260430_smoke10` 这批 `failed=10 / turns=0` 的 competitive replay，补一轮可复现的小诊断闭环
- 不直接修改业务代码，先把失败点定位到 `med_extractor`、`A1`、prompt schema 还是业务层 coercion

### 本次改动

- 新增 [scripts/diagnose_smoke10_failures.py](/Users/loki/Workspace/GraduationDesign/scripts/diagnose_smoke10_failures.py)
- 该脚本会：
  - 读取指定 replay 目录下的 `replay_results.jsonl`
  - 对每条 failed opening 单独调用 `med_extractor` prompt
  - 若 `MedExtractor` 能成功构造 `PatientContext`，继续调用 `a1_key_symptom_extraction`
  - 同步输出：
    - `llm_payload_audit.json`
    - `llm_payload_audit_summary.json`
    - `llm_payload_audit_report.md`
- 已在 [test_outputs/simulator_replay/graph_cases_20260430_smoke10](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/graph_cases_20260430_smoke10) 下生成上述 3 份审计产物

### 实验结果

- replay 原始失败分布：
  - `med_extractor = 4`
  - `a1_key_symptom_extraction = 6`
- live payload probe 结果：
  - `med_probe_status_counts = {"ok": 10}`
  - `a1_probe_status_counts = {"skipped": 5, "ok": 5}`
  - `med_raw_empty_like_count = 0`
  - `a1_raw_empty_like_count = 5`
- 关键发现：
  - `MedExtractor` 并不是“模型什么都没返回”，而是模型经常返回了字符串或对象形状不一致的 `clinical_features`
  - 典型例子如：
    - `["发热"]`
    - `"发热、咳嗽、呼吸困难"`
    - `{"C": "体重增加"}`
  - `A1` 也不是“模型完全没理解 opening”，而是普遍把特征放进了 `uncertain_features`，同时把 `key_features` 留空
  - 当前业务层只消费 `key_features`，因此最终被判成 `llm_empty_extraction`

### 解决的问题

- 之前只能从 `run.log` 看出“失败发生在 med_extractor 或 A1”
- 现在已经能更具体地区分：
  - 是模型真空抽取
  - 还是 payload shape 与 parser 合同错位
  - 还是模型把信号放进了 `uncertain_features` 但业务层未消费

### 影响范围

- 新增诊断脚本与实验产物，不改业务逻辑

### 验证结果

- 已执行 `python -m py_compile scripts/diagnose_smoke10_failures.py`
- 已在真实配置下执行：
  - `conda run -n GraduationDesign python scripts/diagnose_smoke10_failures.py --replay-dir test_outputs/simulator_replay/graph_cases_20260430_smoke10`

## 近期更新：2026-04-29 复杂函数可读性增强

### 本次目标

- 为 `brain/` 中较长或较复杂的函数补充函数内部中文注释
- 让后续阅读者可以直接顺着源码理解“患者输入 -> A1/A2/A3/A4 -> search -> verifier -> repair -> report”的关键链路

### 本次改动

- 在 `brain/service.py`、`brain/stop_rules.py`、`brain/retriever.py`、`brain/evidence_parser.py`、`brain/simulation_engine.py`、`brain/action_builder.py`、`brain/hypothesis_manager.py`、`brain/trajectory_evaluator.py`、`brain/report_builder.py` 等核心模块中，为长函数的关键分支、状态写回、排序逻辑和 fallback / repair 入口补充了中文块级注释
- 同时补充了 `brain/med_extractor.py`、`brain/entity_linker.py`、`brain/llm_client.py`、`brain/mcts_engine.py`、`brain/state_tracker.py`、`brain/router.py` 等支撑模块中的关键步骤注释
- 更新了 [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)，把“复杂函数内部也要有中文块级注释”写成目录级约定

### 解决的问题

- 之前很多函数虽然有函数级说明，但函数内部的阶段切换、guarded gate、repair、rollout、R1/R2 排序来源仍然需要靠来回跳转代码才能理解
- 对首次接手 `brain/` 的同学来说，`service.py`、`stop_rules.py`、`simulation_engine.py` 这类文件的阅读成本偏高
- 这次改动后，核心控制流和状态流转点都能在源码就地读懂，更适合继续维护、调试和撰写论文实现说明

### 影响范围

- 仅增加注释与 README / changelog 说明，不改变业务逻辑
- 主要影响 `brain/` 目录下的可读性与维护成本

### 验证结果

- 已执行 `python -m compileall brain`
- 结果通过，未发现语法错误

## 一、第二阶段总目标

第二阶段的目标不是简单把知识图谱接到一个问答界面上，而是构建一个更接近论文 Med-MCTS 思路的问诊系统。这个阶段希望同时完成下面几件事：

- 把患者原话结构化为可推理的患者上下文
- 基于知识图谱生成主假设和备选假设
- 在多个候选验证动作之间做动态平衡，而不是固定 DFS 追问
- 让 A4 演绎分析真正驱动路由，而不是只做事后解释
- 用虚拟病人回放评估搜索路径和最终答案质量
- 为后续论文撰写保留足够完整的过程材料、问题记录与实验结果

一句话概括第二阶段的演进方向：

- 从“脚手架式问诊系统”逐步推进到“可做真实端到端 smoke 的论文近似实现”

## 二、阶段 0：初始脚手架阶段

### 阶段目标

- 搭建 `brain/`、`simulator/`、`tests/` 的基本目录结构
- 补齐核心类型、状态追踪器、会话内存图、基础检索器和虚拟病人骨架

### 当时的实现特点

- `state_tracker.py`、`session_dag.py`、`retriever.py`、`patient_agent.py` 已有初版
- 系统已经能形成“状态记录 -> 候选问题 -> 继续追问”的最小闭环
- 但核心流程仍然偏工程脚手架，而不是论文风格的搜索推理

### 暴露出的主要问题

- 搜索树虽然存在，但没有真正参与多次 rollout
- A4 更多只是“把回答解释成存在/不存在”，没有真正控制阶段跳转
- 模块之间的调用关系已经成形，但还没有形成一个稳定的搜索范式

### 这一阶段的意义

- 奠定了第二阶段的代码组织方式
- 为后续将 DFS 风格逻辑替换成 MCTS 风格逻辑提供了可改造的基础

## 三、阶段 1：从 DFS 风格转向 Med-MCTS 结构对齐

### 阶段目标

- 不再以固定 DFS 追问作为主策略
- 引入更贴近论文的 `A1 -> A2 -> A3 -> A4` 分层推理
- 将 `UCT + 局部 simulation + 路由控制` 纳入主流程

### 核心改动

- 明确了 `A1`、`A2`、`A3`、`A4` 四个阶段的数据结构和模块职责
- 引入了 `search_tree.py`、`mcts_engine.py`、`simulation_engine.py`、`trajectory_evaluator.py`
- 将 `service.py` 从单纯总控逻辑，逐步改造成搜索编排入口

### 主要解决的问题

- 解决了原始问诊流程中“固定沿一个主题问到底”的刚性问题
- 让系统具备了“候选动作并行评估”的能力
- 为后续把局部路径聚合成最终答案打好了结构基础

### 当时仍然存在的问题

- 搜索树只是“有了节点结构”，但还没有形成标准的 rollout 循环
- `trajectory` 更像动作日志，不像真正的 reasoning path
- A4 路由还没有真正主导后续阶段

## 四、阶段 2：A1 / A2 / A3 / A4 核心模块补齐

### 阶段目标

- 让四个阶段都不再只是占位
- 建立从患者原话到假设、从假设到动作、从动作到路由的完整链路

### 关键改进

#### 1. A1：核心线索提取

- `med_extractor.py` 提供 `patient_text -> (P, C)` 的结构化入口
- `evidence_parser.py` 支持 `A1` 的 LLM 主通道和规则回退

解决的问题：

- 让问诊系统不再直接依赖未结构化原话
- 让后续 R1 检索和实体链接有了稳定输入

#### 2. A2：假设生成

- `hypothesis_manager.py` 支持主假设和备选假设管理
- R1 检索结果可转为 `HypothesisScore`
- 后续又增加了 `supporting_features / conflicting_features / recommended_next_evidence` 的 metadata 保留

解决的问题：

- 系统不再只有“当前最可能问什么”，而是开始具备“当前最可能是什么病”的显式表示
- 为 A3 的区分性提问创造了条件

#### 3. A3：证据验证动作生成

- `action_builder.py` 将 R2 返回的证据行转为 `MctsAction`
- 当前已支持 `discriminative_gain / novelty_score / patient_burden / is_red_flag`
- 后续又让它开始消费 competing hypotheses 和 recommended evidence

解决的问题：

- A3 不再只是“把 R2 结果原样转成问题”
- 开始向“鉴别诊断中的下一问选择”靠拢

#### 4. A4：演绎分析与路由

- `evidence_parser.py` 支持目标感知答案解释
- `router.py` 支持 `STOP / A3 / A2 / A1 / FALLBACK` 等阶段分流

解决的问题：

- A4 不再只是解释层，而开始承担控制器角色
- 为后续搜索中的路径分叉和终止创造了基础

## 五、阶段 3：从“有树结构”升级到“真的在树上搜索”

这是第二阶段实现里最重要的一次迭代。

### 阶段目标

- 让 `run_reasoning_search()` 真正执行多次 rollout
- 让 `SearchTree`、`select_leaf()`、`expand_node()`、`backpropagate()` 进入主流程
- 把系统从“浅层动作打分器”推进到“显式树搜索器”

### 迭代前的主要问题

- 虽然配置中已经有 `num_rollouts`
- 但主流程没有真正按 rollout 次数循环
- `select_leaf()` 没有被实质使用
- 扩展几乎总是贴着 root 发生，树深无法增长
- `trajectory.score` 更像 best-action ranking 的分数，而不是树搜索中的 reward

### 核心改动

- 重写 `brain/service.py::run_reasoning_search()`
- 将主流程改为明确的：
  - `select`
  - `expand`
  - `simulate`
  - `backpropagate`
- 新增：
  - `_ensure_search_tree()`
  - `_build_rollout_context_from_leaf()`
  - `_expand_actions_for_leaf()`
- `mcts_engine.py::select_leaf()` 改成沿树向下执行 tree policy，而不是简单摊平叶子排序
- `mcts_engine.py` 增加 `score_tree_node()` 和 `select_root_action()`

### 解决的问题

- `num_rollouts` 终于从“配置项”变成了“真实生效的搜索循环”
- 搜索树不再只是调试结构，而开始真实积累访问统计和平均价值
- 系统从“有搜索外观”转变为“真的在树上搜索”

### 对论文写作的价值

这一部分可以在论文中作为一个非常清晰的工程贡献点来描述：

- 我们不是直接复现论文公式，而是经历了从“浅层动作打分”到“真实 rollout 搜索”的结构重构
- 这个重构使系统的搜索行为与论文算法更一致

## 六、阶段 4：把 simulation 从“动作估值”推进到“路径预演”

### 阶段目标

- 让 simulation 不再只输出一条两步轨迹
- 让 rollout 能显式模拟 `A3 -> A4 -> route -> A2/A3`

### 迭代前的问题

- `rollout_from_action()` 本质上还是“当前动作 + 一个模拟分支”
- `patient_context` 没有真正参与 rollout
- `max_depth` 只是记录在 metadata 中，没有控制多步展开
- `trajectory` 更像动作日志，不像 reasoning path

### 核心改动

- 在 `simulation_engine.py` 中新增 `rollout_from_tree_node()`
- 引入 `positive / negative / doubtful` 三种回答分支
- 对每个分支：
  - 生成 A4 风格 deductive result
  - 调用 router 决定下一阶段
  - 必要时继续扩展下一条 A3 动作
- 将 rollout 状态写入临时 `SessionState`

### 解决的问题

- 轨迹现在能体现“路径为什么继续、为什么终止、为什么切假设”
- `TrajectoryEvaluator` 开始真正评估 path，而不只是 action
- `patient_context` 不再完全闲置，而开始对预演收益产生轻量影响

## 七、阶段 5：A4 从“证据识别”升级到“演绎路由控制”

### 阶段目标

- 让 A4 不再只是简单判断“有 / 没有 / 不确定”
- 让它输出更细的解释信息，并真正驱动 `process_turn()`

### 迭代前的问题

- 全句级规则判断容易污染目标感知结果
- 只输出 existence / certainty / reasoning 不够支撑后续路由
- `route_after_a4` 虽然存在，但对主流程影响还不够大

### 核心改动

- `evidence_parser.py::interpret_answer_for_target()`
  - 增加 `supporting_span`
  - 增加 `negation_span`
  - 增加 `uncertain_span`
  - 改为 target-aware 解析
- 新增 `judge_deductive_result()`
  - 支持可选 LLM deductive judge
  - 同时保留规则回退
- `router.py::build_deductive_decision()`
  - 增加 `should_terminate_current_path`
  - 增加 `should_spawn_alternative_hypotheses`
  - 增加更细的 contradiction metadata
- `service.py::process_turn()`
  - 明确按 `STOP / A3 / A2 / A1 / FALLBACK` 分支执行

### 解决的问题

- A4 真正变成了流程控制器，而不再只是解释器
- 多轮问诊的结构性明显增强
- 路由逻辑的可解释性也更适合写进论文

## 八、阶段 6：R1 / 实体链接 / 假设竞争信息流增强

### 阶段目标

- 提升候选假设的语义质量
- 让后续 A2、A3、A4 共享更多竞争性信息

### 核心改动

#### 1. R1 检索增强

- `retriever.py::retrieve_r1_candidates()`
  - 从无向匹配改为方向优先 + 反向降权
  - 融合 `direction_confidence`
  - 融合 `entity_link_similarity`

解决的问题：

- R1 不再只看“节点之间是否连得上”
- 开始考虑“从特征到候选疾病”的方向语义

#### 2. 实体链接增强

- `entity_linker.py`
  - 保留 `SequenceMatcher`
  - 增加别名 exact 命中奖励
  - 增加简单医学同义词 bonus
  - 在 metadata 中保留 top-k matches

解决的问题：

- 实体链接不再只输出单点 best match
- 为后续错误分析、候选解释和图谱调参提供了更丰富的信息

#### 3. 假设竞争信息增强

- `hypothesis_manager.py::_try_rank_with_llm()`
  - 保留 `supporting_features`
  - 保留 `conflicting_features`
  - 保留 `why_primary_beats_alternatives`
  - 保留 `recommended_next_evidence`
- `action_builder.py`
  - 开始消费 `competing_hypotheses`
  - 开始消费 `recommended_next_evidence`

解决的问题：

- A3 不再只围绕单个主假设问问题
- 开始具备“为什么问这个问题比问另一个更能区分 top1/top2”的基础能力

## 九、阶段 7：路径评估、报告解释与配置接线

### 阶段目标

- 让最终答案评分和报告输出更接近论文可展示形式
- 让配置文件不再只是文档，而是真正驱动默认构造

### 核心改动

#### 1. 路径评估增强

- `trajectory_evaluator.py`
  - `diversity` 从“唯一动作数比例”升级为基于轨迹相似度的组内平均差异
  - `agent_evaluation` 支持：
    - `fallback`
    - `llm_verifier`

解决的问题：

- 最终评分不再只是简单平均分
- 为后续加入真实 verifier 留出了稳定接口

#### 2. 报告生成增强

- `report_builder.py`
  - 增加 `trajectory_summary`
  - 增加 `why_this_answer_wins`
  - 增加 `evidence_for_best_answer`
  - 增加 `evidence_against_top_alternatives`

解决的问题：

- 最终报告更适合系统演示
- 也更适合作为论文中的案例分析材料

#### 3. 配置文件真正生效

- `service.py` 新增 `load_brain_config()`
- 默认构造会真正消费 `configs/brain.yaml`
- 当前会驱动：
  - `MctsEngine`
  - `SimulationEngine`
  - `TrajectoryEvaluator`
  - `EntityLinker`
  - `GraphRetriever`
  - `EvidenceParser`
  - `HypothesisManager`
  - `StopRuleEngine`

解决的问题：

- 参数不再散落在代码默认值里
- 后续实验更容易做对比与复现
- 这对论文实验部分尤其重要

## 十、阶段 8：真实端到端 smoke 打通

### 阶段目标

- 用真实 Neo4j 图谱和真实大模型接口验证系统不是“只在单测里能跑”
- 获取能够写进论文实验部分的第一批真实 smoke 信号

### 已完成的验证

#### 1. 真实 Neo4j smoke

通过 `scripts/run_retriever_smoke.py` 验证：

- 图谱标签可读
- 关系类型可读
- `R1` 可返回候选假设
- `R2` 可返回待验证证据

#### 2. 真实 end-to-end smoke

通过 `scripts/run_batch_replay.py --max-turns 5` 跑通了：

- 真实 Neo4j
- 默认问诊大脑构造
- 虚拟病人自动回放
- 搜索报告生成
- benchmark 汇总输出

### 当前得到的 smoke 信号

当前真实 smoke 已经说明：

- 工程链路是通的
- 搜索、路由、报告、benchmark 都能落到真实输出
- 当前最主要瓶颈已经从“系统能不能跑通”转向“诊断质量是否足够好”

这对论文写作很重要，因为它意味着可以明确区分两类结论：

- 工程实现方面：核心链路已经打通
- 诊断质量方面：仍有明显提升空间

## 十一、阶段 9：收紧 R1/A2 并引入真实 verifier 抑制过早 STOP

### 阶段目标

- 继续压缩 R1 中“泛化候选、弱语义候选”过早排前的问题
- 让 A2 真正利用候选之间的竞争关系，而不是只看单个候选自身分数
- 将 `llm_verifier` 从接口级能力推进到真实 stop gating
- 把“verifier 明确拒停，但系统仍 completed”的控制流漏洞补掉

### 迭代前暴露的问题

- `R1` 虽然已经有方向置信度，但仍可能把只命中单个弱证据的疾病阶段拉到前面
- `A2` 候选排序更多是单候选视角，缺少“谁拥有独特证据、谁与其他候选高度重叠”的竞争信息
- `TrajectoryEvaluator` 已能调用 verifier，但 `process_turn()` 仍可能因为 `top1_margin_sufficient` 直接终止
- 真实 focused smoke 中已经出现了：
  - verifier 给出 `should_accept=false`
  - 但 `initial_output.final_report` 仍然非空
  - 说明“答案评分”与“真正允许停止”之间还有控制流旁路

### 核心改动

#### 1. R1 候选语义收紧

- `retriever.py`
  - 增加 `r1_min_semantic_score`
  - 将 `matched_feature_count / feature_coverage / relation_types / label_prior / relation_specificity` 纳入评分
  - 对旧版多候选标签下的单弱证据泛化候选增加额外惩罚；当前新图谱 schema 已统一收敛为 `Disease`
  - 将 `entity_link_similarity` 和方向置信度真正并入最终语义分数

解决的问题：

- R1 不再只靠“能连上图谱”进入前列
- 对单证据、弱关系、泛化标签候选的抑制更强
- 为 A2 提供了更干净的初始候选池

#### 2. A2 竞争性重排增强

- `hypothesis_manager.py`
  - 新增 `_rerank_candidates_with_competition()`
  - 对 unique evidence 给 bonus
  - 对 overlap ratio 给 penalty
  - 额外融合 feature coverage 与 semantic score
  - 将 `competition_rerank_bonus / unique_evidence_count / overlap_ratio` 写回 metadata
- 继续保留并下传：
  - `supporting_features`
  - `conflicting_features`
  - `why_primary_beats_alternatives`
  - `recommended_next_evidence`

解决的问题：

- A2 不再只是“谁分高谁排前”
- 开始体现“谁更能解释当前特征组合、谁与其他候选的区分度更高”
- 为 A3 的鉴别性提问提供了更合理的上游输入

#### 3. 真实 verifier 接入最终答案评分

- `trajectory_evaluator.py`
  - `agent_evaluation` 正式支持 `llm_verifier`
  - verifier 现在返回：
    - `score`
    - `should_accept_stop`
    - `reasoning`
    - `missing_evidence`
    - `risk_flags`
- `configs/brain.yaml`
  - 默认启用 `path_evaluation.agent_eval_mode: llm_verifier`

解决的问题：

- 最终答案不再只依赖轨迹聚合分
- verifier 可以显式指出“为什么现在还不能停”
- 这使得 stop 控制从“阈值裁剪”升级为“有诊断性解释的拒停”

#### 4. Stop gating 与 service 控制流修复

- `stop_rules.py`
  - 增加：
    - `min_turn_index_before_final_answer`
    - `min_trajectory_count_before_accept`
    - `require_verifier_accept_flag`
- `service.py`
  - 新增“有搜索信号时，只有 accept decision 才能真正 completed”的门控逻辑
  - `finalize()` 与 `finalize_from_search()` 优先保留 verifier 的 stop reason，而不再回退为 `top1_margin_sufficient`
  - 增加对应回归测试，锁住：
    - verifier 拒停时不能直接 completed
    - 最终报告应保留 `verifier_rejected_stop`

解决的问题：

- 修复了“verifier 已拒停，但系统仍然 final”的真实控制流漏洞
- stop 逻辑从“候选足够强就停”改成“候选足够强 + verifier 同意才停”
- 这一步对论文中的安全性与保守性论述非常关键

#### 5. 重复追问抑制

- 真实 focused replay 又暴露出一个新问题：
  - verifier 拒停后，系统虽然不再直接 completed
  - 但会在多轮中重复选择同一个 root action
- 为此又补了：
  - `mcts_engine.select_root_action()` 支持排除已问节点
  - `service.py::run_reasoning_search()` 在选择 root action 时真正传入 `asked_node_ids`

解决的问题：

- 切断了“历史最优 child 被无限重选”的循环来源
- 为 verifier 拒停后的继续追问提供了更合理的动作切换基础
- 这也是从“能继续问”进一步走向“能继续问得合理”的必要一步

#### 6. verifier 拒停后的显式 repair 分流

- `trajectory_evaluator.py`
  - verifier 现在会保留：
    - `verifier_reject_reason`
    - `verifier_recommended_next_evidence`
    - `verifier_alternative_candidates`
- `service.py`
  - 新增显式 repair context：
    - `missing_key_support`
    - `strong_alternative_not_ruled_out`
    - `trajectory_insufficient`
  - 并针对三类拒停走不同的后续动作策略
- `hypothesis_manager.py`
  - 新增 verifier-driven hypothesis reshuffle
  - 当 verifier 指出强 alternative 未排除时，会显式给 alternative bonus，并对当前 top1 加 uncertainty penalty
- `service.py`
  - 下一问不再只依赖 root best action
  - 改为在 verifier 拒停后选择 “best repair action”

解决的问题：

- verifier 的输出不再只是“告诉系统先别停”
- 而是开始真正决定“接下来该补什么证据、是否要重新排序 hypothesis、该问哪一类问题”
- 这让 stop gating 从“防止早停”进一步升级为“驱动后续修复动作”

#### 7. 每轮状态签名换根

- `service.py::_ensure_search_tree()`
  - 现在会把当前 top hypothesis id 纳入根状态签名
  - 当出现：
    - verifier reject
    - top hypothesis 改变
    - 当前状态签名变化
  - 就按当前状态重新建 root

解决的问题：

- 降低了跨轮复用旧树时把历史 root action 误带进新状态的风险
- 对问诊这种强状态依赖任务来说，这一步比单纯“复用一棵树”更稳健

## 十二、阶段 10：repair 行为可观测化与 focused replay 验证

### 阶段目标

- 不再只从最终 `completed / max_turn_reached` 判断 repair 是否生效
- 让每一轮真实回放都能直接观察：
  - verifier 为什么拒停
  - root best action 是什么
  - repair 后实际选了什么动作
  - 是否发生 reroot
  - 是否仍在重复追问

### 核心改动

#### 1. repair 可观测字段显式输出

- `brain/types.py`
  - `SearchResult` 增加：
    - `root_best_action`
    - `repair_selected_action`
    - `verifier_repair_context`
- `brain/report_builder.py`
  - `search_report` 与 `final_report` 现在都会保留：
    - `selected_action`
    - `root_best_action`
    - `repair_selected_action`
    - `repair_context`
- `brain/service.py`
  - 新增 `_build_observable_repair_context()`
  - 将 `reject_reason / recommended_next_evidence / alternative_candidates / repair_mode / rerooted / previous_selected_action / new_selected_action` 统一写回可观测上下文

解决的问题：

- 失败 replay 不再需要靠人工猜测“系统到底为什么改问这个问题”
- 可以明确区分：
  - tree 本来想问什么
  - repair 最终改问了什么
  - 改问背后对应的是哪类拒停原因

#### 2. 单病例 smoke 摘要脚本增强

- `scripts/run_single_case_smoke.py`
  - 新增 `--summary-only`
  - 新增 `--output-file`
  - 输出 turn 级紧凑摘要：
    - `reject_reason`
    - `recommended_next_evidence`
    - `alternative_candidates`
    - `selected_action`
    - `root_best_action`
    - `repair_selected_action`
    - `route_after_a4_stage`
    - `best_answer_name`
    - `stop_reason`
    - `same_question_as_previous`

解决的问题：

- 真实 replay 不再只能看超长原始 `search_report`
- 每个 turn 的 repair 行为都可以直接落盘，适合后续论文和误差分析

### focused replay 的真实验证结果

本轮使用真实 Neo4j + DashScope `qwen3-max` 运行了两例问题病例：

- [single_case_smoke_pcp_typical_001_v2.jsonl](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/single_case_smoke_pcp_typical_001_v2.jsonl)
- [single_case_smoke_concealing_risk_001_v2.jsonl](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/single_case_smoke_concealing_risk_001_v2.jsonl)

#### 1. `concealing_risk_001`

观察到：

- `turn_0` 时：
  - `root_best_action = CD4+ T淋巴细胞计数 < 200/μL`
  - `repair_selected_action = 淋巴结肿大`
  - `reject_reason = missing_key_support`
- `turn_2` 时：
  - `root_best_action = 皮疹`
  - `repair_selected_action = 关节疼痛`
- `turn_3` 时：
  - `root_best_action = 皮疹`
  - `repair_selected_action = 咽痛`
- 各轮 `same_question_as_previous = false`

说明：

- verifier 拒停后，系统已经不再机械追问 root best action
- repair flow 在真实链路中确实能把问题切换到其他区分性节点
- 当前至少已经摆脱了“拒停后原地重复同一个 node”的旧问题

#### 2. `pcp_typical_001`

观察到：

- `turn_1` 时：
  - `root_best_action = 动脉血氧分压 (PaO2) < 70 mmHg`
  - `repair_selected_action = CD4+ T淋巴细胞计数 < 200/μL`
  - `rerooted = true`
  - `reroot_reason = state_signature_changed`
- `turn_2` 与 `turn_3` 时：
  - 问题继续在 `CD4 / PaO2 / 肺泡-动脉氧分压差` 这些支持性实验室证据之间切换
  - `same_question_as_previous = false`
  - 但 `alternative_candidates` 仍为空

说明：

- repair 策略已经能避免重复同一个问题
- reroot 也确实在真实路径里发生
- 但当前 repair 仍主要是“围绕主假设补支持证据”
- 还没有充分体现“切向强 alternative 的区分性提问”

### 这一阶段的结论

这一轮 focused replay 最重要的结论不是“准确率提高了多少”，而是：

- 系统已经从“verifier 拒停后原地踏步”推进到“verifier 拒停后能够换一个问题继续问”

但同时也明确暴露出下一步最应该优化的位置：

- `repair-aware A3`
- 即让 repair action 不只是换一个未问过的问题
- 而是更明确地根据：
  - `missing_key_support`
  - `strong_alternative_not_ruled_out`
  - `trajectory_insufficient`
  选择真正更有修复价值的问题

### 基于 focused replay 的后续修复：repair-aware A3

在完成上述 focused replay 后，又进一步对 `A3` 做了面向 repair 的补强，核心是让 verifier 指出的“证据缺口”能够更直接地影响下一问的构造与排序。

#### 1. 动作构造层增强

- `brain/action_builder.py`
  - 将 `recommended_next_evidence` 的利用从近似 exact match，升级为：
    - 文本归一化
    - 医学关键词标签
    - 证据家族匹配
  - 新增 metadata：
    - `recommended_match_score`
    - `alternative_overlap`
    - `evidence_tags`

解决的问题：

- verifier 推荐“询问免疫状态 / 获取胸部CT / 检测 β-D-葡聚糖”时，不再因为名称写法不同而完全匹配不上图谱节点
- repair scoring 能更清楚地区分“这个动作是否真的在补 verifier 指出的缺口”

#### 2. service repair 选问逻辑增强

- `brain/service.py`
  - `missing_key_support` 下会同时考虑：
    - 推荐证据匹配度
    - question type 多样性
    - 证据家族是否与上一问过于接近
  - `strong_alternative_not_ruled_out` 下：
    - 不再只从当前 top1 hypothesis 取动作
    - 会把强备选 hypothesis 的动作也纳入 repair 候选池

解决的问题：

- 下一问不再只是“挑一个没问过的动作”
- 而是更接近“挑一个最能修复当前 verifier 缺口的动作”

#### 3. 新一轮真实 replay 信号

在完成上述修复后，再次运行：

- [single_case_smoke_pcp_typical_001_v3.jsonl](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/single_case_smoke_pcp_typical_001_v3.jsonl)
- [single_case_smoke_concealing_risk_001_v3.jsonl](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/single_case_smoke_concealing_risk_001_v3.jsonl)

观察到：

- `turn_0`：
  - `root_best_action = 胸部CT磨玻璃影`
  - `repair_selected_action = CD4+ T淋巴细胞计数 < 200/μL`
- `turn_1`：
  - 在 CD4 未确认后，下一问切回 `胸部CT磨玻璃影`
- `turn_2`：
  - `root_best_action = 动脉血氧分压 (PaO2) < 70 mmHg`
  - `repair_selected_action = (1,3)-β-D-葡聚糖检测 (G试验)`
- `turn_3`：
  - `root_best_action = 动脉血氧分压 (PaO2) < 70 mmHg`
  - `repair_selected_action = 胸部CT检查`

这和上一版 [single_case_smoke_pcp_typical_001_v2.jsonl](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/single_case_smoke_pcp_typical_001_v2.jsonl) 相比，最大的变化是：

- 不再主要在 `CD4 / PaO2 / 肺泡-动脉氧分压差` 之间打转
- 开始切向：
  - 免疫状态
  - 影像学证据
  - 病原 / 真菌学证据

这一信号说明：

- repair-aware A3 已经把真实链路中的下一问选择，从“补同类支持证据”推进到了“补不同类型的关键缺口证据”
- 同时在 `concealing_risk_001` 中，原先已经成立的“从 `CD4` 切到 `淋巴结肿大 / 关节疼痛`”这类 repair 行为仍然保留，没有明显回退

## 十三、阶段 11：focused repair ablation

### 阶段目标

- 不再只看“最终是否拒停”
- 通过小规模对照实验判断：
  - verifier-driven reshuffle 是否真的改变 hypothesis 竞争关系
  - best repair action 是否真的负责把问题从 root best 改到修复性问题
  - reroot 是否真的有助于维持搜索树与当前状态一致

### 实验设置

本轮只跑 focused cases，避免在全量病例上消耗过多外部模型调用：

- `pcp_typical_001`
- `concealing_risk_001`
- 每例 `max_turns = 3`
- 使用真实 Neo4j 与 DashScope `qwen3-max`

输出目录：

- [ablation_baseline_v1](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/ablation_baseline_v1)
- [ablation_no_reshuffle_v1](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/ablation_no_reshuffle_v1)
- [ablation_no_best_repair_action_v1](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/ablation_no_best_repair_action_v1)
- [ablation_no_reroot_v1](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/ablation_no_reroot_v1)

### 新增实验开关

- `configs/brain.yaml`
  - `repair.enable_verifier_hypothesis_reshuffle`
  - `repair.enable_best_repair_action`
  - `repair.enable_tree_reroot`
- `scripts/run_focused_repair_replay.py`
  - 新增 `--disable-verifier-reshuffle`
  - 新增 `--disable-best-repair-action`
  - 新增 `--disable-reroot`
  - 每组输出 `focused_metrics.json`

### 指标对照

| 组别 | stop reason | repair turns | repair override turns | rerooted turns | repeated question turns |
| --- | --- | ---: | ---: | ---: | ---: |
| baseline | `verifier_rejected_stop: 2` | 8 | 6 | 6 | 0 |
| no verifier reshuffle | `verifier_rejected_stop: 2` | 8 | 5 | 6 | 0 |
| no best repair action | `verifier_rejected_stop: 2` | 7 | 0 | 6 | 0 |
| no reroot | `no_answer_score: 2` | 1 | 1 | 0 | 0 |

### 关键观察

#### 1. best repair action 是当前最直接的动作切换来源

关闭 `best repair action` 后：

- `repair_override_turns` 从 baseline 的 `6` 下降到 `0`
- 说明 root action filtering 本身可以避免重复节点，但不能负责“把 root best 改成修复性问题”
- 对应问题序列也更像继续沿 root 排名走，而不是按 verifier 缺口修补

#### 2. reroot 对保持搜索树与状态一致很关键

关闭 `reroot` 后：

- `rerooted_turns` 变为 `0`
- `repair_turns` 从 baseline 的 `8` 降到 `1`
- 最终 `stop_reason` 退化为 `no_answer_score`

说明：

- 在强状态依赖的问诊场景里，完全复用旧树会让后续搜索报告失去足够有效的 answer score
- 当前 evidence 更新后仍需要按状态签名换根，否则 verifier repair 很难稳定进入后续评分与问法选择

#### 3. verifier-driven reshuffle 在本轮 focused cases 中影响较小

关闭 `verifier-driven reshuffle` 后：

- 总体 stop reason 与 reroot 次数没有明显变化
- `repair_override_turns` 只从 `6` 变成 `5`

原因分析：

- 本轮两例的主要拒停原因都是 `missing_key_support`
- `alternative_candidates` 大多为空或难以映射到当前 KG hypothesis
- 因此 reshuffle 的效果没有像 `strong_alternative_not_ruled_out` 场景那样充分显现

这提示后续如果要验证 reshuffle 的价值，需要补充更适合的 focused case，例如 verifier 明确指出“强备选诊断未排除”的病例。

### ablation 阶段结论

本轮小规模 ablation 支持一个更清晰的结论：

- `best repair action` 是“拒停后换成修复性问题”的直接来源
- `reroot` 是“让旧树不污染新状态、让后续仍有有效评分”的必要机制
- `verifier-driven reshuffle` 在当前两例中信号较弱，需要构造更强 alternative 场景继续验证

这一结果对论文写作很有价值，因为它能把“verifier 自己在起作用”与“repair 分流 / reroot / A3 选问策略在起作用”拆开说明。

### 追加验证：三病例 focused replay

在上述两病例 ablation 后，继续加入 `pcp_vague_001`，形成三病例 focused replay：

- `pcp_typical_001`
- `pcp_vague_001`
- `concealing_risk_001`

输出目录：

- [focused_3case_baseline_v1](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/focused_3case_baseline_v1)
- [focused_3case_no_best_repair_action_v1](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/focused_3case_no_best_repair_action_v1)
- [focused_3case_no_reshuffle_v1](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/focused_3case_no_reshuffle_v1)
- [focused_3case_no_reroot_v1](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/focused_3case_no_reroot_v1)

指标对照：

| 组别 | stop reason | repair turns | repair override turns | hypothesis switch turns | rerooted turns | repeated question turns |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| baseline | `verifier_rejected_stop: 3` | 11 | 9 | 4 | 9 | 0 |
| no best repair action | `verifier_rejected_stop: 3` | 12 | 0 | 0 | 9 | 0 |
| no verifier reshuffle | `verifier_rejected_stop: 3` | 11 | 7 | 0 | 9 | 0 |
| no reroot | `no_answer_score: 3` | 2 | 2 | 1 | 0 | 0 |

这组三病例结果比两病例更清楚：

- 关闭 `best repair action` 后，`repair override turns` 从 `9` 直接降到 `0`，说明 repair-aware A3 是动作改写的直接来源。
- 关闭 `verifier-driven reshuffle` 后，`hypothesis switch turns` 从 `4` 降到 `0`，说明 reshuffle 在加入 `pcp_vague_001` 这种更容易混淆的病例后，确实帮助系统切换到不同 hypothesis。
- 关闭 `reroot` 后，三例最终都退化为 `no_answer_score`，说明旧树复用会削弱后续 answer score 生成，reroot 不是单纯 metadata，而是维持后续评分和修复流程的必要机制。

典型行为例子：

- baseline 下，`pcp_vague_001` 的实际动作从 root best 的 `胸部CT磨玻璃影 / 肺孢子菌肺炎 (PCP)` 切到 `低氧血症 / 另一个候选 hypothesis`，说明 repair 不只是换同一 hypothesis 下的节点，也会发生 hypothesis 层面的切换。
- 关闭 `best repair action` 后，`pcp_typical_001` 与 `pcp_vague_001` 都更像沿 root best 连续追问氧合相关实验室证据，例如 `PaO2`、`肺泡-动脉氧分压差`、`肺泡-动脉血氧分压差`。
- 关闭 `reroot` 后，repair context 大量消失，最终 `best_answer_name` 为空，说明旧树复用会让状态更新后的搜索报告失去足够有效的可评估路径。

因此，三病例 ablation 进一步支持：

- `best repair action` 负责“换问题”
- `verifier-driven reshuffle` 负责“更容易换 hypothesis”
- `reroot` 负责“让换题后的搜索仍能生成有效评分”

## 十四、当前仍未彻底解决的问题

虽然第二阶段已经从脚手架推进到了真实 smoke 可跑，但以下问题仍然需要继续记录和补强：

### 1. 过早终止

- 虽然 verifier gating 已经接上，但仍需继续用全量真实 replay 验证“过早停止是否稳定下降”
- 尤其需要观察“verifier 拒停后，系统是否能够稳定继续追问，而不是通过其它旁路提前结束”

### 1.5. verifier 拒停后的策略切换仍需继续验证

- 当前已修掉重复选择已问 root action 的明显问题
- 但还需要继续确认：
  - 系统是否会稳定切换到第二优先验证动作
  - 是否会在 verifier 拒停后重新组织更合适的候选假设
  - 是否会从“重复同一问题”转向“有区分性的下一问”

### 2. R1 语义仍不够稳定

- 虽然已经引入方向语义和实体链接相似度
- 当前又增加了 coverage / specificity / generic penalty
- 但典型病例仍可能被拉向不理想的候选疾病或疾病阶段，需要继续看真实回放结果

### 3. rollout 深度仍偏浅

- 当前仍是“浅层多步 rollout”
- 还没有完全达到论文中更强的深层搜索与路径评审能力

### 4. verifier 仍偏轻量

- `llm_verifier` 已经真实接入最终答案评分与 stop gating
- 但其 prompt 仍是轻量版，还可以继续增强对鉴别诊断、证据缺口和风险提示的评审能力

### 5. 虚拟病人评测集仍偏小

- 当前病例集可以做 smoke
- 但还不足以形成稳定、可信的论文实验结论

## 十五、适合直接写进论文的表述点

为了方便后续论文撰写，可以直接从本 changelog 中提炼下面几类内容：

### 1. 方法演进动机

- 从 DFS 风格追问转向 Med-MCTS 风格搜索
- 动机是提升主题切换能力、前瞻性和路由控制能力

### 2. 工程实现贡献

- 将搜索树从“结构存在”推进到“真实多次 rollout”
- 将 A4 从“解释层”推进到“控制层”
- 将路径评估从“动作平均分”推进到“路径聚合评分”

### 3. 系统迭代逻辑

- 每一轮改动都不是凭空发生的
- 都是围绕“当前暴露的问题 -> 定向修复 -> 再验证”的过程推进

### 4. 实验结论的边界

- 当前真实 smoke 已经证明系统链路打通
- 但还不能据此宣称诊断质量已经达到最终目标
- 这为论文中如实陈述系统能力边界提供了依据

## 十六、当前阶段结论

到目前为止，第二阶段已经完成了一个重要转折：

- 它不再是“只有模块、没有主流程”的脚手架
- 也不再是“有搜索名字、但没有真实 rollout”的近似实现
- 它已经进入“真实 smoke 可跑、结构与论文明显对齐、但质量仍需继续提升”的阶段

如果用一句话总结当前状态，可以写成：

- 第二阶段已完成从问诊脚手架到 Med-MCTS 风格原型系统的关键过渡，当前重点已从工程连通性转向诊断质量与评估严谨性的持续提升。

## 十七、repair-aware A3 与标准 ablation 脚本化

本轮围绕“verifier 拒停后是否能换成更有鉴别价值的问题”继续收紧，而不是继续扩大框架。

### 1. repair-aware A3 打分增强

- `strong_alternative_not_ruled_out` 分支进一步提高非当前 top1 hypothesis 动作的奖励，并提高 `discriminative_gain` 权重
- `trajectory_insufficient` 分支进一步鼓励 question type 与 evidence family 多样性，并显式惩罚同一 evidence family 的相似追问
- `missing_key_support` 分支更强绑定 verifier 推荐证据、原 hypothesis 推荐证据，以及两者共同命中的缺口证据
- `ActionBuilder` 为每个 A3 action 增加 `verifier_recommended_match_score`、`hypothesis_recommended_match_score`、`joint_recommended_match_score`
- `HypothesisManager.apply_verifier_repair()` 保留 `hypothesis_recommended_next_evidence` 与 `verifier_recommended_next_evidence`，方便后续区分“原假设建议”与“verifier 拒停建议”

### 2. verifier 输出硬结构化

- `trajectory_agent_verifier` prompt 明确要求输出固定 JSON object
- `reject_reason` 固定为 `missing_key_support | strong_alternative_not_ruled_out | trajectory_insufficient`
- `alternative_candidates` 固定为 `{answer_id, answer_name, reason}` 对象数组
- `TrajectoryEvaluator` 新增 `verifier_reject_reason_source` 与 `verifier_schema_valid`，当 LLM 未遵守枚举时仍保留 fallback 推断，但会显式标记来源
- 修复了字符串 `"false"` 被 Python `bool()` 误判为 `True` 的 verifier 接受逻辑风险

### 3. focused ablation 标准实验入口

- 新增 `scripts/run_focused_ablation.py`
- 支持 `baseline`、`no_best_repair_action`、`no_reshuffle`、`no_reroot` 四组变体
- 输出每个变体的 `focused_repair_summary.jsonl` 与 `focused_metrics.json`
- 额外输出总表 `ablation_summary.jsonl` 与 `ablation_metrics.json`
- 汇总指标包括 `repair_turns`、`repair_override_turns`、`hypothesis_switch_turns`、`rerooted_turns`、`repeated_turns`、`semantic_repeat_turns`、`root_vs_repair_diff_rate`

### 4. 当前验证

- 通过 `python -m py_compile brain/action_builder.py brain/service.py brain/hypothesis_manager.py brain/trajectory_evaluator.py brain/llm_client.py scripts/run_focused_ablation.py`
- 通过 `conda run -n GraduationDesign python -m pytest -q`
- 当前测试结果：`46 passed`

## 十八、verifier acceptance 校准实验入口

本轮将目标从“拒停后是否会换问题”切到“答案已正确时 verifier 是否愿意放行”。

### 1. acceptance profile 拆分

- 在 `LlmClient` 的 `trajectory_agent_verifier` prompt 中新增 `conservative` 与 `slightly_lenient`
- 保留 `baseline` 作为默认开发基线
- `conservative` 更强调直接关键证据与强替代诊断排除
- `slightly_lenient` 更强调在关键证据已覆盖、强替代未被支持时避免机械性拒停

### 2. acceptance 时序指标

focused replay / ablation 汇总新增：

- `first_correct_best_answer_turn`：第一次出现正确 best answer 的轮次
- `first_verifier_accept_turn`：第一次 verifier 对 best answer 给出接受信号的轮次
- `correct_but_rejected_span`：best answer 已正确但仍持续未被 verifier 放行的轮次数
- `verifier_called_count`：best answer 评分中成功带回 verifier metadata 的 turn 数
- `accepted_with_verifier_metadata_count`：最终 accepted 且 accepted turn 带完整 verifier metadata 的病例数
- `accepted_without_verifier_metadata_count`：最终 accepted 但缺失完整 verifier metadata 的病例数
- `accepted_on_turn1_count`：第一次 verifier accept 出现在 turn 1 的 accepted 病例数
- `accept_reason_counts`：accepted verifier 调用中的接受原因分布
- `wrong_accept_on_turn1_count`：turn 1 verifier accept 且最终错误接受的病例数
- `median_first_verifier_accept_turn`：首次 verifier accept 轮次的中位数

这些指标用于判断系统到底是“答案还没对”，还是“答案早已对但 verifier 迟迟拒停”。
同时也用于核查 accepted 路径是否真的经过了 verifier，而不是由记录链缺口或 stop 旁路造成。

### 3. 固定开发基线

- 新增 `scripts/run_verifier_acceptance_sweep.sh`
- 默认固定 `MAX_TURNS=5`
- 默认固定 `stop_profile=baseline`
- 默认只扫 `ACCEPTANCE_PROFILES=baseline,slightly_lenient,guarded_lenient`
- 新增 `scripts/run_focused_acceptance_validation.sh`，默认使用 10 个 focused acceptance cases 做扩样本验证
- 新增 `simulator/focused_acceptance_cases.jsonl`，覆盖 PCP 正样本、PCP vs TB、PCP vs 真菌感染、非 PCP 呼吸道感染、风险史/影像/系统性症状干扰等类型

这将下一轮真实实验的变量压缩到 verifier acceptance 倾向，避免继续把 turn budget、stop threshold 和 verifier prompt 混在一起分析。

## 十九、guarded_lenient 接受闸门

本轮根据 10-case focused validation 的结果继续收紧 acceptance calibration。
实验显示 `slightly_lenient` 能减少正确拒停，但会显著增加 turn1 错误接受，因此不能直接作为默认 profile。

### 1. 新增 guarded_lenient profile

- `trajectory_agent_verifier` 新增 `guarded_lenient`
- 它保留“关键支持证据充分时更敢停”的倾向
- 但 prompt 明确要求遇到强替代诊断、近期 hypothesis 切换、负向或不确定关键证据时保持拒停
- focused validation 默认 profile 变为 `baseline,slightly_lenient,guarded_lenient`

### 2. StopRule 安全闸门

`StopRuleEngine.should_accept_final_answer()` 在 `guarded_lenient` 下新增二次安全 gate：

- 早期接受必须至少有 1 条 A4 `exist + confident` 的定义性关键证据
- PCP、结核、真菌性肺部感染、影像强但非 PCP 等高混淆呼吸道诊断，全程都必须有 confirmed key evidence
- confirmed key evidence 不再只依赖 `relation_type`，同时消费 `evidence_tags`，覆盖 `imaging`、`oxygenation`、`pathogen`、`immune_status`
- PCP 单独影像或单独氧合证据不足以接受，必须有免疫背景、病原学证据，或影像/氧合/典型呼吸道表现构成的组合证据
- 当前答案相关的关键支持证据若出现 `non_exist` 或 `doubt`，禁止立即接受
- 最近 1 轮 best hypothesis / final answer 发生切换时，禁止立即接受
- verifier 返回非空强替代候选时，禁止立即接受
- 如果首次 verifier accept 的答案与当前最终答案不一致，要求当前答案至少先稳定通过一轮 verifier

guarded gate 会把拒绝原因写入 `FinalAnswerScore.metadata` 与 `SessionState.metadata`，并映射回 repair 可消费的三类拒停原因。

后续根据 focused validation 结果，guarded gate 从单一 PCP combo 改为分 block reason 的条件化策略：

- `negative_or_doubtful_key_evidence` 继续严格拦截，不降低安全门槛
- `missing_confirmed_key_evidence` 优先作为 evidence tagging / A4 记录链审计信号，而不是直接放宽接受
- `pcp_combo_insufficient` 改为有限组合模板，包括影像+免疫/实验室、影像+病原或 PCP-specific、影像+氧合+免疫、影像+典型呼吸道表现+免疫
- 高混淆呼吸道答案会允许 `imaging`、`oxygenation`、`immune_status`、`respiratory` 等可共享临床证据进入 guarded family 归集，但病原类证据仍保持更谨慎的 PCP-specific 识别
- guarded block 会把缺失 family 写回 repair context，引导 A3 优先补 CD4/HIV/免疫抑制、β-D 葡聚糖、PCP PCR 等能完成 combo 的证据
- missing-family-first repair 会在 `pcp_combo_insufficient` 与 `missing_confirmed_key_evidence` 下优先围绕当前 verifier candidate answer 修复，而不是跟随 reshuffle 后的 top1 继续问普通症状路径
- 对 CD4、β-D 葡聚糖、PCP PCR、BAL / 支气管肺泡灌洗相关 PCP 检测增加 combo-repair anchor bonus
- 如果已经确认了 `imaging` 或 `oxygenation`，且仍缺 `immune_status`、`pathogen` 或 `pcp_specific`，继续追问 `respiratory/oxygenation` 会被显著降权

再根据 audit 结果，`negative_or_doubtful_key_evidence` 被拆成两层：

- `hard_negative_key_evidence`：当前答案作用域内、定义性关键证据出现 `non_exist + confident`，继续作为硬拦截
- `soft_negative_needs_stability`：共享临床证据、`unknown`、`doubt` 或非核心 family 只作为延迟信号，要求同一答案有 prior verifier accept 或补足其他 confirmed family，而不是一票否决

### 3. 新增 acceptance 安全指标

focused replay / ablation 汇总新增：

- `wrong_accept_reason_counts`
- `first_verifier_accept_turn_for_final_answer`
- `median_first_verifier_accept_turn_for_final_answer`
- `final_answer_changed_after_first_accept_count`
- `accepted_after_negative_key_evidence_count`
- `accepted_after_recent_hypothesis_switch_count`
- `accepted_with_nonempty_alternative_candidates_count`
- `guarded_block_reason_counts`
- `verifier_positive_but_gate_rejected_count`
- `accept_candidate_without_confirmed_combo_count`
- `guarded_gate_audit_records`
- `guarded_negative_evidence_node_counts`
- `guarded_negative_evidence_family_counts`
- `guarded_negative_evidence_tier_counts`
- `guarded_negative_evidence_scope_counts`
- `missing_family_first_selected_count`
- `missing_family_repair_turn_count`
- `combo_anchor_selected_before_turn3_count`
- `family_recorded_after_question_count`
- `family_recorded_after_question_attempt_count`

这些指标用于判断错误接受是否来自“轨迹稳定但证据不足”、“负向关键证据被忽略”、“近期答案切换后过早停”或“强替代候选未排除”。

同时每个 focused ablation profile 会额外落盘 `guarded_gate_audit.jsonl`，逐条记录：

- `block_reason`
- `current_answer_name`
- `confirmed_evidence_families`
- `missing_families`
- `alternative_candidates`
- `recent_key_evidence_states`
- `pcp_combo_missing_family_options`
- `hard_negative_key_evidence`
- `soft_negative_or_doubtful_key_evidence`

### 4. guarded verifier 与 gate 协同校准

- `guarded_lenient` prompt 不再单方面压低 `should_accept_stop`
- verifier 现在被定位为“候选接受信号提供者”，允许在临床上较可信时先给出 accept 信号
- 最终是否停止仍由结构化 gate 校验 confirmed evidence、negative/doubtful 证据、强替代候选和答案稳定性
- focused validation 脚本默认 `CASE_CONCURRENCY=5`，同一 profile 内最多 5 个病例并行回放，以降低真实 qwen3-max smoke 的等待时间

## 十二、2026-04-26：图谱驱动病例重生成与固定抽样刷新

### 本次目标

- 在不修改知识图谱抽取链路的前提下，基于现有审计目录重新生成一轮图谱驱动虚拟病人病例
- 使用固定随机种子重新执行四类病例各 `5` 条的抽样，刷新人工质检材料
- 将本次输出路径、计数结果和抽样用途同步回 README 与 changelog

### 本次执行的命令

重新生成病例：

```bash
conda run -n GraduationDesign python scripts/generate_graph_virtual_patients.py \
  --audit-root test_outputs/graph_audit/all_diseases_20260420_disease_aliases_only \
  --output-file test_outputs/simulator_cases/graph_cases_20260421/cases.jsonl \
  --output-json-file test_outputs/simulator_cases/graph_cases_20260421/cases.json \
  --manifest-file test_outputs/simulator_cases/graph_cases_20260421/manifest.json \
  --summary-file test_outputs/simulator_cases/graph_cases_20260421/summary.md
```

重新抽样：

```bash
conda run -n GraduationDesign python scripts/sample_graph_virtual_patients.py \
  --cases-file test_outputs/simulator_cases/graph_cases_20260421/cases.json \
  --output-file test_outputs/simulator_cases/graph_cases_20260421/sampled_cases_4x5.json \
  --summary-file test_outputs/simulator_cases/graph_cases_20260421/sampled_cases_4x5.md \
  --sample-size-per-type 5 \
  --seed 42
```

### 本次输出

- [cases.json](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260421/cases.json)
- [cases.jsonl](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260421/cases.jsonl)
- [manifest.json](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260421/manifest.json)
- [summary.md](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260421/summary.md)
- [sampled_cases_4x5.json](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260421/sampled_cases_4x5.json)
- [sampled_cases_4x5.md](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260421/sampled_cases_4x5.md)

### 本次结果

- `ordinary = 66`
- `low_cost = 49`
- `exam_driven = 61`
- `competitive = 51`
- 总病例数 `227`
- 固定抽样数 `20`

### 本次验证

- 病例生成脚本正常完成，输出计数与上一轮一致
- 抽样脚本正常完成，四类病例各抽 `5` 条
- 本次没有新增代码逻辑修改，也没有额外运行单元测试

## 十三、2026-04-26：正式目录 graph_cases_20260426_final 生成与抽样

### 本次目标

- 将图谱驱动虚拟病人的正式产物切换到新的输出目录 `graph_cases_20260426_final`
- 用最新生成器重新生成完整病例集，并重新执行四类病例各 `5` 条的固定抽样
- 同步脚本默认路径、README 和详细方案文档中的产物路径

### 本次执行的命令

重新生成病例：

```bash
conda run -n GraduationDesign python scripts/generate_graph_virtual_patients.py \
  --audit-root test_outputs/graph_audit/all_diseases_20260420_disease_aliases_only \
  --output-file test_outputs/simulator_cases/graph_cases_20260426_final/cases.jsonl \
  --output-json-file test_outputs/simulator_cases/graph_cases_20260426_final/cases.json \
  --manifest-file test_outputs/simulator_cases/graph_cases_20260426_final/manifest.json \
  --summary-file test_outputs/simulator_cases/graph_cases_20260426_final/summary.md
```

重新抽样：

```bash
conda run -n GraduationDesign python scripts/sample_graph_virtual_patients.py \
  --cases-file test_outputs/simulator_cases/graph_cases_20260426_final/cases.json \
  --output-file test_outputs/simulator_cases/graph_cases_20260426_final/sampled_cases_4x5.json \
  --summary-file test_outputs/simulator_cases/graph_cases_20260426_final/sampled_cases_4x5.md \
  --sample-size-per-type 5 \
  --seed 42
```

### 本次输出

- [cases.json](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260426_final/cases.json)
- [cases.jsonl](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260426_final/cases.jsonl)
- [manifest.json](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260426_final/manifest.json)
- [summary.md](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260426_final/summary.md)
- [sampled_cases_4x5.json](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260426_final/sampled_cases_4x5.json)
- [sampled_cases_4x5.md](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260426_final/sampled_cases_4x5.md)

### 本次结果

- 正式目录切换到 `graph_cases_20260426_final`
- 抽样目录随正式目录同步更新
- 脚本默认路径与 README / 方案文档引用已同步刷新
- 重新生成结果：
  - `ordinary = 66`
  - `low_cost = 49`
  - `exam_driven = 61`
  - `competitive = 51`
  - 总病例数 `227`
  - 固定抽样数 `20`

### 抽样检查

针对 `sampled_cases_4x5.json` 的 20 个样本，额外做了一次程序化检查，重点确认：

- 同一病例是否出现多个 `CD4` 阈值
- 同一病例是否出现多个 `HIV RNA / 病毒载量` 状态
- 是否出现多个 `BMI` 分层
- `opening_slot_names` 中是否仍出现 `骨密度测量部位`、`减重持续时间`、单独病原体名

本次检查结果：

- 未发现多个 `CD4` 阈值并列
- 未发现多个 `HIV RNA / 病毒载量` 状态并列
- 未发现多个 `BMI` 分层并列
- 未发现 `opening_slot_names` 混入 `骨密度测量部位`、`减重持续时间`、单独病原体名

### replay 运行前置检查

本次同时检查了 batch replay 的运行条件与结果产物能力：

- LLM 侧默认模型配置为 `qwen3-max`
- LLM base URL 已指向 DashScope OpenAI 兼容接口
- 本机存在 `configs/frontend.local.yaml`
- 当前环境或本机配置中：
  - `LLM API Key` 已配置
  - `Neo4j password` 已配置
- 标准 `run_batch_replay.py` 当前默认是串行执行，不带并发
- `replay_results.jsonl` 已可记录：
  - `opening_text`
  - 每轮 `question_text`
  - 每轮 `answer_text`
  - `final_report`
  - `initial_output`
- 前端实验复盘模式已能直接读取 `replay_results.jsonl` 与 `benchmark_summary.json` 做展示

### 本次验证

- 重新生成命令已成功完成
- 重新抽样命令已成功完成
- 抽样规则检查通过
- 本次没有新增核心生成逻辑修改，因此未额外运行单元测试

## 十四、2026-04-26：标准 batch replay 增加病例级并发

### 本次目标

- 为 `scripts/run_batch_replay.py` 增加病例级并发，缩短 200+ 图谱病例的正式回放耗时
- 保持 replay 结果结构不变，不修改病例 schema，不改前端读取协议
- 默认并发数设置为 `4`

### 本次实现

- 为 `run_batch_replay.py` 新增参数：
  - `--case-concurrency`
- 默认值为 `4`
- 并发实现采用 `ThreadPoolExecutor`
- 每个并发任务都会独立创建：
  - `ConsultationBrain`
  - `VirtualPatientAgent`
  - `ReplayEngine`

这样做的原因是：

- `StateTracker` 维护可变会话状态，不适合多个病例共享同一个 brain 实例并发运行
- 通过“每病例独立 brain”可以安全并发，同时复用 Neo4j 读查询与 LLM 调用能力

### 结果影响

- `replay_results.jsonl` 结构不变
- `benchmark_summary.json` 额外补充：
  - `case_concurrency`
  - `case_file`
- 前端实验复盘模式无需额外适配，仍可直接读取 `replay_results.jsonl` 与 `benchmark_summary.json`

### 文档同步

- [README.md](/Users/loki/Workspace/GraduationDesign/README.md)
- [simulator/README.md](/Users/loki/Workspace/GraduationDesign/simulator/README.md)
- [virtual_patient_generation_scheme.md](/Users/loki/Workspace/GraduationDesign/docs/virtual_patient_generation_scheme.md)

## 十五、2026-04-26：标准 batch replay 增加 limit，用于 10 例 smoke

### 本次目标

- 在标准 `run_batch_replay.py` 中增加一个轻量的病例数量限制参数
- 便于在正式全量回放前，先跑前 `10` 个病例做 smoke

### 本次实现

- 新增参数：
  - `--limit`
- 语义：
  - `0` 表示不限制
  - `10` 表示只运行前 `10` 个病例
- `benchmark_summary.json` 额外补充：
  - `case_limit`

### 使用建议

对于当前这种标准 batch replay：

- 不建议先折腾“深度思考模式”
- 更合适的顺序是：
  1. 先用默认模型配置跑 `--limit 10`
  2. 确认 Neo4j、LLM、日志与前端展示链路正常
  3. 再决定是否全量跑 `227` 个病例

## 十七、2026-04-26：batch replay 增加终端进度条

### 本次目标

- 改善标准 `run_batch_replay.py` 的终端可观测性
- 在小样本 smoke 或全量回放时，实时看到已完成病例数和总病例数

### 本次实现

- [run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)
  - 新增 `_format_progress_line()`
  - 新增 `_emit_progress()`
  - `run_cases` 执行期间会向 `stderr` 持续输出进度条
- 典型输出形式：

```text
[batch_replay] 进度 [##########--------------] 已完成病例：2 / 10
```

### 结果影响

- 不改变 `stdout` 最终 JSON 摘要
- 不改变 `replay_results.jsonl`
- 不改变 `benchmark_summary.json`
- 只增强终端运行时可见性

## 十八、2026-04-26：压缩 batch replay 最终报告中的重量级 metadata

### 本次目标

- 降低标准 `run_batch_replay.py` 在批量运行时的内存占用
- 避免把完整搜索树和完整搜索结果对象长期挂在每个病例的 `final_report` 上

### 问题来源

此前 `ReportBuilder.build_final_report()` 会把 `session_state.metadata` 原样写入：

- `search_tree`
- `last_search_result`

这两个字段本身都是运行态对象。批量 replay 时，每个病例的 `ReplayResult.final_report` 都会继续引用它们，导致已经跑完的病例也无法及时释放对应的搜索树和轨迹对象，内存占用会被持续放大。

### 本次实现

- [brain/report_builder.py](/Users/loki/Workspace/GraduationDesign/brain/report_builder.py)
  - 新增 `_build_public_metadata()`
  - 新增 `_sanitize_lightweight_metadata_value()`
  - 新增 `search_tree` 轻量摘要：
    - `search_tree_summary.root_id`
    - `search_tree_summary.node_count`
  - 新增 `last_search_result` 轻量摘要：
    - `best_answer_id`
    - `best_answer_name`
    - `trajectory_count`
    - `answer_group_score_count`
  - 最终报告不再原样保留：
    - `search_tree`
    - `last_search_result`
- [tests/test_report_builder.py](/Users/loki/Workspace/GraduationDesign/tests/test_report_builder.py)
  - 新增回归测试，确保最终报告 metadata 中不再出现原始重量级对象

### 结果影响

- `replay_results.jsonl` 仍可直接被前端实验复盘模式读取
- `final_report` 的核心字段不变：
  - `candidate_hypotheses`
  - `best_final_answer`
  - `answer_group_scores`
  - `why_this_answer_wins`
  - `trajectory_count`
- 仅 `final_report.metadata` 从“原始运行态对象”改为“轻量摘要 + 小型 JSON 友好字段”

### 验证

执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_report_builder.py tests/test_run_batch_replay.py tests/test_replay_engine.py -q
```

结果：

```text
9 passed
```

## 十九、2026-04-26：batch replay 支持增量落盘与断点续跑

### 本次目标

- 让标准 `run_batch_replay.py` 在长时间运行时更可靠
- 每完成一个病例立即落盘，而不是等全部结束后一次性写出
- 若运行被中断，下次启动时可以自动跳过已完成病例

### 本次实现

- [scripts/run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)
  - 新增 `_run_cases_streaming()`，允许病例完成后立刻触发回调
  - 标准主流程改为“每完成一个病例就立刻”：
    - 追加写入 `replay_results.jsonl`
    - 追加写入 `run.log`
    - 覆盖更新 `benchmark_summary.json`
    - 覆盖更新 `status.json`
  - 新增 `_load_existing_replay_results()`，启动时会读取已有 `replay_results.jsonl`
  - 默认启用断点续跑：若输出目录里已经存在已完成病例，会自动按 `case_id` 跳过
  - 新增参数：
    - `--no-resume`：禁用断点续跑，强制重跑全部病例

### 结果影响

- 即使 batch replay 意外中断，已经完成的病例结果也不会丢失
- 再次运行同一输出目录时，系统会直接从未完成病例继续
- 前端实验复盘模式可直接读取：
  - `replay_results.jsonl`
  - `benchmark_summary.json`
  - `status.json`
  - `run.log`

### 验证

执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_run_batch_replay.py tests/test_replay_engine.py -q
```

结果：

```text
8 passed
```

## 二十、2026-04-26：batch replay 增加病例级耗时统计

### 本次目标

- 定位标准 batch replay 为什么会长时间卡住
- 不再只知道“还没跑完”，而是能看到慢在：
  - opening 生成
  - 首轮 brain 处理
  - 患者逐轮回答
  - brain 逐轮处理
  - finalize

### 本次实现

- [simulator/replay_engine.py](/Users/loki/Workspace/GraduationDesign/simulator/replay_engine.py)
  - `ReplayTurn` 新增：
    - `patient_answer_seconds`
    - `brain_turn_seconds`
    - `total_seconds`
  - `ReplayResult` 新增：
    - `timing`
  - 每个病例现在会记录：
    - `started_at`
    - `finished_at`
    - `opening_seconds`
    - `initial_brain_seconds`
    - `patient_answer_seconds_total`
    - `brain_turn_seconds_total`
    - `finalize_seconds`
    - `total_seconds`
    - `max_patient_answer_seconds`
    - `max_brain_turn_seconds`
    - `slowest_turn_index`
    - `slowest_turn_total_seconds`
- [scripts/run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)
  - `benchmark_summary.json` 新增 `timing_summary`
  - `status.json` 新增 `timing_summary`
  - `run.log` 现在会在每个病例完成时输出完整耗时拆分
  - `run.log` / `status.json` 还会记录当前已启动的活动病例，便于判断是不是某几个病例异常慢

### 结果影响

- 现在只要打开：
  - `run.log`
  - `status.json`
  - `benchmark_summary.json`
  就能快速判断慢在哪里
- 对前端实验复盘没有破坏性影响；只是 replay 结果里多了 timing 字段

### 验证

执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_run_batch_replay.py tests/test_replay_engine.py -q
```

结果：

```text
8 passed
```

## 二十一、2026-04-26：修复 Ctrl+C 中断后线程池不退出导致的高内存占用

### 本次目标

- 解决用户在 `batch replay` 运行中按下 `Ctrl+C` 后，Python 进程仍长期占用大量内存的问题
- 避免主线程已中断，但 `ThreadPoolExecutor` worker 仍继续运行

### 问题原因

标准 `ThreadPoolExecutor` 的 worker 线程是非 daemon。此前 `run_batch_replay.py` 使用：

- `with ThreadPoolExecutor(...) as executor`

当 `KeyboardInterrupt` 在主线程中触发时，`with` 语句退出会调用：

- `executor.shutdown(wait=True)`

这会导致主进程继续等待并发 worker 收尾。对于已经进入 Neo4j 检索、LLM 调用或搜索推理的任务，这种等待可能很长，因此表现为：

- 终端已经按下 `Ctrl+C`
- 但 `python3.10` 进程仍继续占用几十 GB 内存

### 本次实现

- [scripts/run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)
  - 将 `_run_cases_streaming()` 中的 `ThreadPoolExecutor` 改为手动管理，而不是依赖 `with`
  - 捕获 `KeyboardInterrupt` 时先执行：
    - `executor.shutdown(wait=False, cancel_futures=True)`
  - 主流程在写完：
    - `status.json`
    - `run.log`
    后，不再 `raise SystemExit(130)`，而是调用：
    - `_force_exit_after_interrupt()`
    - 内部使用 `os._exit(130)` 直接结束进程

### 结果影响

- `Ctrl+C` 后不会再长时间等待线程池自然收尾
- 已落盘的：
  - `replay_results.jsonl`
  - `benchmark_summary.json`
  - `status.json`
  - `run.log`
  仍然保留，可直接续跑
- 由于当前已经支持断点续跑，强制退出不会破坏下一次恢复运行

### 验证

执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_run_batch_replay.py tests/test_replay_engine.py -q
```

结果：

```text
9 passed
```

## 二十二、2026-04-26：增强 batch replay 的终端可见性与运行中心跳

### 本次目标

- 解决用户运行 `batch replay` 半小时后终端几乎没有任何输出、无法判断是否卡住的问题
- 让 `conda run` 场景下也能看到实时启动/完成/心跳信息

### 问题原因

此前标准 `run_batch_replay.py` 虽然已经支持进度条，但仍有两个现实限制：

- 进度只会在病例完成时更新；如果并发中的最后 1-2 个病例运行很久，终端会长时间沉默
- 用户常用的是：
  - `conda run -n GraduationDesign python scripts/run_batch_replay.py ...`
  在默认 capture 模式下，`stdout/stderr` 的实时输出不稳定，容易让人误以为后端“完全没有反应”

### 本次实现

- [scripts/run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)
  - 新增终端输出助手，优先直接写 `/dev/tty`，退回时再写 `stderr`
  - 保留病例级进度条，但改为每次更新都输出完整可见行
  - 新增病例启动提示：
    - `病例启动 3/10：case_id=...`
  - 新增病例完成提示：
    - `病例完成 8/10：case_id=... total_seconds=...`
  - 新增后台心跳线程，默认每 15 秒输出一次：
    - 已完成病例数
    - 当前活动病例数
    - 当前运行时间最长的活动病例
  - `status.json` 对外仍只保留：
    - `case_id`
    - `case_title`
    - `started_at`
    不暴露内部 `started_epoch`

### 结果影响

- 即使长时间没有新病例完成，终端也会持续输出心跳
- 更容易判断系统到底是：
  - 正在正常运行
  - 卡在某个长病例
  - 已经有病例启动但尚未完成
- 对外部结果文件没有破坏性影响：
  - `replay_results.jsonl`
  - `benchmark_summary.json`
  - `status.json`
  - `run.log`
  都维持原有用途

### 建议运行方式

为避免 `conda run` 的输出捕获影响观察，当前推荐：

```bash
conda run --no-capture-output -n GraduationDesign python scripts/run_batch_replay.py ...
```

### 验证

执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_run_batch_replay.py tests/test_replay_engine.py -q
```

结果将在本轮修改完成后记录。

实际执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_run_batch_replay.py tests/test_replay_engine.py -q
```

结果：

```text
11 passed
```

## 二十三、2026-04-26：修正 batch replay 的信号中断与亚秒级耗时日志

### 本次目标

- 继续追查 `Ctrl+C` 后 Python 进程仍残留的问题
- 解释并修正 `run.log` 中大量 `0.00` 耗时导致的可观测性误导

### 现象复盘

用户提供的 `run.log` 显示了两个问题：

- 部分病例完成后，`total_seconds / opening_seconds / brain_turn_seconds_total` 看起来全是 `0.00`
- 中断后仍需要手动 `kill` 残留 Python 进程

进一步检查落盘结果发现：

- 那些“全 0”病例并非真的没有运行，而是实际耗时处于毫秒级，例如：
  - `initial_brain_seconds = 0.0001`
  - `brain_turn_seconds_total = 0.0008`
  - `total_seconds = 0.0007`
- 由于 `run.log` 统一保留两位小数，毫秒级耗时被四舍五入成了 `0.00`
- 病人回答侧持续接近 `0.00`，在当前 smoke 命令下通常意味着：
  - 实际已经退回规则回答
  - 或当前环境未提供可用 LLM key

### 本次实现

- [scripts/run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)
  - 新增 `_format_duration_value()`：
    - 对 `<1s` 的耗时保留四位小数
    - 对 `>=1s` 的耗时保留两位小数
  - 启动时追加记录：
    - `llm_available=true/false`
  - 新增显式信号处理：
    - `SIGINT`
    - `SIGTERM`
  - 中断时仍保持原有策略：
    - 先写 `status.json`
    - 再写 `run.log`
    - 最后强制退出进程
  - 运行结束或测试场景下，显式恢复原信号处理器

### 结果影响

- `run.log` 不会再把 `0.0007s` 这类真实运行时间误显示成 `0.00`
- 看到 `patient_answer_seconds_total≈0` 时，可以直接结合启动行里的 `llm_available` 判断是不是已经退回规则病人
- 中断路径不再只依赖 `KeyboardInterrupt`，也覆盖 `SIGTERM`

### 验证

执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_run_batch_replay.py tests/test_replay_engine.py -q
conda run -n GraduationDesign python -m py_compile scripts/run_batch_replay.py simulator/replay_engine.py
```

结果：

```text
11 passed
py_compile passed
```

## 二十四、2026-04-26：让 batch replay CLI 自动读取 frontend 本机配置

### 本次目标

- 解决 `frontend.local.yaml` 已配置 API key，但 `run_batch_replay.py` 单独运行时仍显示 `llm_available=false` 的问题
- 统一前端实时模式与 CLI replay 的配置来源

### 本次实现

- [scripts/run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)
  - 启动时自动执行：
    - `load_frontend_config()`
    - `apply_config_to_environment(...)`
  - 这样 CLI replay 会像前端一样读取：
    - `configs/frontend.yaml`
    - `configs/frontend.local.yaml`
  - 然后再构建：
    - `LlmClient`
    - `ConsultationBrain`
    - `VirtualPatientAgent`

### 结果影响

- 不再要求每次手动 `export DASHSCOPE_API_KEY` / `OPENAI_MODEL` / `NEO4J_PASSWORD`
- 只要本机 `frontend.local.yaml` 已配置，batch replay 启动日志中的：
  - `llm_available=true/false`
  就能真实反映当前 CLI 环境

### 验证

执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_run_batch_replay.py tests/test_replay_engine.py -q
conda run -n GraduationDesign python -m py_compile scripts/run_batch_replay.py
```

结果：

```text
11 passed
py_compile passed
```

## 十六、2026-04-26：显式关闭 LLM 深度思考

### 本次目标

- 不再依赖 DashScope / OpenAI compatible 服务端的默认 thinking 行为
- 把 `enable_thinking` 做成明确配置，并默认关闭

### 本次实现

- [brain/llm_client.py](/Users/loki/Workspace/GraduationDesign/brain/llm_client.py)
  - 新增 `enable_thinking` 配置读取
  - 支持从 `OPENAI_ENABLE_THINKING` / `DASHSCOPE_ENABLE_THINKING` 读取
  - 默认值为 `false`
  - 调用 `chat.completions.create()` 时显式传入：
    - `extra_body={"enable_thinking": false}`
- [frontend/config_loader.py](/Users/loki/Workspace/GraduationDesign/frontend/config_loader.py)
  - 新增 `llm.enable_thinking`
  - 会桥接为环境变量 `OPENAI_ENABLE_THINKING`
- [configs/frontend.yaml](/Users/loki/Workspace/GraduationDesign/configs/frontend.yaml)
  - 默认配置加入 `enable_thinking: false`

### 结果影响

- 当前仓库内的标准 LLM 调用已明确关闭深度思考
- 前端配置表会直接显示“LLM 深度思考：关闭”
- 后续如果要临时开启，只需要把：
  - `configs/frontend.local.yaml` 中的 `llm.enable_thinking`
  - 或环境变量 `OPENAI_ENABLE_THINKING`
  改为 `true`

### 本次意义

- 刷新了当前用于人工质检的抽样材料
- 让 `README.md`、`simulator/README.md` 和 changelog 中记录的输出文件保持与当前实际落盘内容一致
- 为后续继续检查 `selected_positive_slots` 与 `opening_slot_names` 是否仍有结构化残留提供了统一入口

## 二十五、2026-04-26：收口 competitive opening、intake 空转与 replay timing 口径

### 本次目标

- 接住一批 `competitive` bench replay 在 intake 阶段空转的问题
- 修掉 `HIV感染 / 抗逆转录病毒治疗 / 免疫功能低下` 被直接渲染成主诉 opening 的病例质量问题
- 让 batch replay 的 timing 字段更接近真实口径，避免毫秒级 round 累计误导

### 现象复盘

这轮排查先确认了两件事：

- `run_batch_replay.py` 接入前端本机配置后，CLI 启动日志已经可以稳定显示 `llm_available=true`
- 但即使 LLM 可用，仍有一批 `competitive` 病例会在首轮 opening 就把背景风险信息当成主诉，例如：
  - `最近主要想咨询一下HIV感染、抗逆转录病毒治疗相关的情况。`

进一步复盘发现问题分成三层：

- `brain/service.py`
  - 对无信息 opening 会不断重复 `collect_chief_complaint`
- `brain/med_extractor.py` / `brain/evidence_parser.py`
  - 规则词典对 `畏光 / 视力下降 / 嗜睡 / 精神错乱 / 认知异常` 等 competitive 常见表达覆盖不足
  - LLM 若返回 `clinical_features: "嗜睡、精神错乱、痴呆"` 这类字符串，而不是数组，会被整段丢掉
- `simulator/graph_case_generator.py`
  - `competitive` opening 直接使用 `shared_low_cost`
  - 一旦 shared 里主要是 `HIV感染 / HIV感染者 / ART / 免疫功能低下` 这类背景项，就会把它们直接渲染成 chief complaint

另外，timing 里还暴露了一个可观测性问题：

- 某些毫秒级病例会出现：
  - `initial_brain_seconds = 0.0001`
  - `brain_turn_seconds_total = 0.0008`
  - `total_seconds = 0.0007`
- 这不代表 wall-clock 丢失，而是逐轮 round 后累计，导致 `brain_turn_seconds_total` 被放大

### 本次实现

- [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - 新增 repeated chief complaint 保护
  - 若已经追问过一次主诉，但回答仍无任何可推理线索，则直接以：
    - `repeated_chief_complaint_without_signal`
    停止，而不是继续空转 8 轮

- [brain/med_extractor.py](/Users/loki/Workspace/GraduationDesign/brain/med_extractor.py)
  - 扩充 competitive 常见症状 / 风险 fallback 词典
  - 新增对字符串型 `clinical_features` 的容错解析
  - 会把这类输出拆成可落到 `PatientContext.clinical_features` 的结构化条目

- [brain/evidence_parser.py](/Users/loki/Workspace/GraduationDesign/brain/evidence_parser.py)
  - 扩充 A1 fallback 词典
  - 若 LLM 通道成功返回但 `key_features=[]`，自动退回规则抽取，而不是把“空成功”当成有效结果

- [simulator/graph_case_generator.py](/Users/loki/Workspace/GraduationDesign/simulator/graph_case_generator.py)
  - `competitive` opening 改为：
    - 先选 shared 里的自然开场项
    - 不足时再用 target-only 的症状 / 具体检查结果补足
    - 若仍没有自然 opening，则回退到疾病名
  - opening 过滤中新增：
    - `PopulationGroup`
    - `HIV感染 / HIV感染者 / HIV/AIDS / 抗逆转录病毒治疗 / 免疫功能低下`
    这类背景风险项不再作为 chief complaint

- [simulator/replay_engine.py](/Users/loki/Workspace/GraduationDesign/simulator/replay_engine.py)
  - timing 改为先累计原始浮点耗时，再在落盘前统一 round
  - 降低毫秒级病例里逐轮 round 带来的放大误导

- [scripts/run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)
  - 续跑读取历史 `replay_results.jsonl` 时，保留逐轮：
    - `patient_answer_seconds`
    - `brain_turn_seconds`
    - `total_seconds`

### 结果影响

- `competitive` 病例不再把 `HIV / ART / 免疫功能低下` 直接当成开场主诉
- 即使 opening 仍然较弱，brain 也不会继续重复 `__chief_complaint__` 到最大轮次
- LLM schema 稍微“松”一点时，A1 / MedExtractor 仍能保住核心线索
- batch replay 的 timing 字段更适合和真实 wall-clock 一起看，不会再因为逐轮 round 把毫秒级累计放大

### 验证

执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_graph_case_generator.py tests/test_replay_engine.py tests/test_run_batch_replay.py -q
conda run -n GraduationDesign python -m pytest tests/test_service_stop_flow.py tests/test_med_extractor.py tests/test_evidence_parser.py -q
conda run -n GraduationDesign python scripts/generate_graph_virtual_patients.py
```

结果：

- `35 passed`
- `26 passed`
- 重新生成正式图谱病例输出：
  - `generated_case_count = 227`
  - `competitive = 51`
- 对新生成的 `graph_cases_20260426_final/cases.json` 复查后：
  - `competitive` 中 `HIV / ART / 免疫功能低下` 式 opening 数量为 `0`

## 二十六、2026-04-26：延后早期 verifier，收紧 competitive 慢病例的每轮成本

### 本次目标

- 处理 `competitive` replay 中少数病例长时间占住 worker、不在数分钟内收口的问题
- 避免在还不可能 stop 的早期轮次反复调用高成本 `trajectory_agent_verifier`

### 现象复盘

继续复盘 `graph_cases_20260426_smoke10` 后，确认：

- `kg_competitive_1094c4fa_vs_77bbd6d1_001` 这类病例并不是坏 opening 或 intake 死循环
- 真正问题是高混淆神经系统竞争病例在常规追问里重复支付多次 LLM 成本

在修复前，对同一慢病例做 turn profile，曾观察到：

- `TURN 0` 约 `39s`
- `TURN 1` 约 `33s`
- `TURN 2` 约 `36s`

其中主要消耗来自：

- `A2` 在 A3 常规追问中重复重算
- `A4 deductive judge` 对“没有步态异常”这类明确短答也继续调用 LLM
- `trajectory_agent_verifier` 在每轮都运行，即使：
  - `turn_index` 还没达到最早可接受窗口
  - 同一答案的 `trajectory_count` 也还不足以 stop

### 本次实现

- [brain/trajectory_evaluator.py](/Users/loki/Workspace/GraduationDesign/brain/trajectory_evaluator.py)
  - 新增：
    - `llm_verifier_min_turn_index`
    - `llm_verifier_min_trajectory_count`
  - 当当前轮次还没进入可停止观察窗口时：
    - 不调用 `trajectory_agent_verifier`
    - 临时退回 fallback agent evaluation
    - 在 metadata 中记录：
      - `verifier_mode = llm_verifier_deferred`
      - `verifier_deferred_reason`

- [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - `run_reasoning_search()` 现在会把 `session_turn_index` 传给 `TrajectoryEvaluator`
  - 默认构造会把 verifier 延后阈值对齐到 stop 配置：
    - `min_turn_index_before_final_answer`
    - `min_trajectory_count_before_accept`

- [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)
  - 显式加入：
    - `path_evaluation.llm_verifier_min_turn_index: 2`
    - `path_evaluation.llm_verifier_min_trajectory_count: 2`

### 结果影响

- 早期 A3 追问轮次不会再因为 verifier 过早出场而把单轮耗时拉到 30~40 秒
- verifier 仍保留在真正可能 stop 或需要更严谨终局评审的窗口里
- repair / guarded gate 的主要语义不变，但不再在“还不可能终止”的轮次提前付费

### 验证

执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_trajectory_evaluator.py tests/test_service_config.py tests/test_service_stop_flow.py tests/test_evidence_parser.py tests/test_med_extractor.py -q
python -m py_compile brain/trajectory_evaluator.py brain/service.py
```

结果：

- `34 passed`
- `py_compile` 通过

对同一慢病例 `kg_competitive_1094c4fa_vs_77bbd6d1_001` 做 turn-by-turn 复核后，前 6 个 brain turn 耗时已经下降到：

- `TURN 0 = 25.267s`
- `TURN 1 = 17.302s`
- `TURN 2 = 15.920s`
- `TURN 3 = 11.727s`
- `TURN 4 = 8.330s`
- `TURN 5 = 9.331s`

这说明当前问题已经从“单轮 30~40 秒连续堆叠、导致病例长时间占住 worker”明显收缩到“仍然偏慢，但回到可接受的分钟级 replay 成本”。

## 二十七、2026-04-26：修掉 competitive 晚期 replay 的大对象复制与 GC 爆炸

### 本次目标

- 继续处理少数 `competitive` 病例在第 7~8 轮突然退化到数百秒甚至上千秒的问题
- 判断这类超慢病例到底是：
  - 医学推理逻辑错误
  - LLM / Neo4j 外部调用变慢
  - 还是 Python 本地运行时对象膨胀

### 现象复盘

在上一轮优化后，大多数病例已经回到 `100~160s` 左右，但仍有少数病例在最后几轮异常退化，例如：

- `kg_competitive_3726b8b4_vs_b4059736_001`
  - `total_seconds = 1145.24`
  - `slowest_turn = 8:476.61s`
- `kg_competitive_2102c689_vs_b247711a_001`
  - `total_seconds = 1737.95`
  - `slowest_turn = 8:1449.70s`
- `kg_competitive_32e052bf_vs_d0c8e771_001`
  - 在原 smoke10 中直到用户中断前仍未完成

逐轮轨迹显示，这些病例在后期常出现明显跑偏的问题，例如：

- `新型冠状病毒感染 vs 结核病`
  - 后面开始追问 `BMI`
  - 甚至追问 `抗病毒药依从性`
- `CMV肺炎 vs CMV脑炎`
  - 在 verifier 拒停后不断被引向 `HIV感染者` / `免疫抑制状态`

这说明：

- 病例本身不是 bad opening
- 也不只是 LLM 调得慢
- 而是后期 repair / reroot 过程中，某些通用高连接证据节点被带入搜索，进而放大运行时状态复制成本

为了确认是不是本地运行时问题，对卡长中的 Python 进程做了采样，看到：

- 一个进程主要卡在：
  - `dict_dealloc`
- 另一个进程主要卡在：
  - `gc_collect_main`
  - `deduce_unreachable`
- 当时物理内存已经膨胀到：
  - `14.9GB`
  - `19.0GB`

所以这次慢的主因不是网络，而是：

- 搜索树 / rollout state / 最近搜索结果之间形成了巨大的对象图
- reroot 时又把这些对象通过 `deepcopy(state)` 递归复制进新的树节点
- 最后在第 7~8 轮触发超重 GC

### 根因定位

进一步检查发现，问题集中在两条链上：

- [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - `_ensure_search_tree()` 创建新 root 时，会把：
    - `deepcopy(state)`
    存进 root metadata 的 `rollout_state`
  - 但此时 `state.metadata` 里可能已经带着旧的：
    - `search_tree`
    - `last_search_result`
- `run_reasoning_search()` 中 child node 也会保存 rollout state
  - 一旦 rollout state 本身再带着旧树、旧 search result，就会出现递归复制

换句话说，之前晚期超慢的更准确机制是：

- verifier 拒停
- reroot 频繁发生
- 每次 reroot 都把上一轮整棵树和最近搜索结果再拷一层
- Python 最后把大量时间花在对象析构和 GC 上

### 本次实现

- [brain/state_tracker.py](/Users/loki/Workspace/GraduationDesign/brain/state_tracker.py)
  - 新增：
    - `get_rollout_session_copy()`
    - `build_rollout_session_snapshot()`
  - rollout 专用快照只保留推演必需字段：
    - `turn_index`
    - `active_topics`
    - `slots`
    - `evidence_states`
    - `exam_context`
    - `candidate_hypotheses`
    - `asked_node_ids`
    - `fail_count`
  - 明确去掉：
    - `metadata`
    - `trajectories`
    - `action_stats`
    - `state_visit_stats`

- [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - `_ensure_search_tree()` 创建 root 时，不再使用：
    - `deepcopy(state)`
  - 改为：
    - `tracker.get_rollout_session_copy(session_id)`
  - `_build_rollout_context_from_leaf()` 在没有缓存 state 时，也改为取轻量 rollout copy
  - child node 写回 `rollout_state` 时，再次做轻量 snapshot，避免未来改动把重 metadata 带回来

### 结果影响

- reroot 不再递归复制旧的 `search_tree` 与 `last_search_result`
- competitive 病例后几轮不再因为 Python GC 爆炸而拖到 8 分钟 / 24 分钟
- 这次修复主要改变的是运行时内存行为，不改变医学推理判定本身

### 验证

执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_state_tracker.py tests/test_service_repair_flow.py tests/test_service_stop_flow.py tests/test_trajectory_evaluator.py -q
python -m py_compile brain/state_tracker.py brain/service.py
```

结果：

- `24 passed`
- `py_compile` 通过

另外对三条最慢病例做了单病例复测：

- `kg_competitive_2102c689_vs_b247711a_001`
  - 修复前：
    - `total_seconds = 1737.95`
    - `max_brain_turn_seconds = 1439.18`
  - 修复后：
    - `total_seconds = 93.90`
    - `max_brain_turn_seconds = 8.98`

- `kg_competitive_3726b8b4_vs_b4059736_001`
  - 修复前：
    - `total_seconds = 1145.24`
    - `max_brain_turn_seconds = 470.61`
  - 修复后：
    - `total_seconds = 118.93`
    - `max_brain_turn_seconds = 14.06`

- `kg_competitive_32e052bf_vs_d0c8e771_001`
  - 原 smoke10 中未在用户中断前完成
  - 修复后单跑：
    - `total_seconds = 111.97`
    - `max_brain_turn_seconds = 10.49`

### 当前结论

这批“最后一条特别慢”的根因主要是诊断系统的运行时实现问题，而不是病例质量问题本身：

- 病例会让系统进入更容易 reroot / repair 的高混淆路径
- 但真正把耗时放大到几十分钟的，是 rollout state 对 `search_tree` / `last_search_result` 的递归复制与后续 GC 爆炸

因此，这次优先修运行时对象管理是正确顺序。

## 二十八、2026-04-27：补充 brain 详细运行链路指南

### 本次目标

- 为后续论文写作、答辩讲解和代码交接补一份可以直接顺着源码阅读的 `brain` 运行说明
- 重点回答“病人说了一句话之后，系统内部到底按什么顺序调用了哪些函数”
- 把实时模式、CLI 与离线 replay 共用的主入口和差异讲清楚

### 本次实现

- 新增文档：
  - [brain_runtime_call_chain_guide.md](/Users/loki/Workspace/GraduationDesign/docs/brain_runtime_call_chain_guide.md)
  - 内容覆盖：
    - `start_session()` / `process_turn()` 作为统一对外入口
    - `PatientContext`、`SessionState`、`MctsAction`、`SearchResult`、`StopDecision` 等核心运行时对象
    - 从 `MedExtractor -> update_from_pending_action -> A1 -> A2 -> A3/search -> verifier/repair -> report` 的完整单轮链路
    - `collect_chief_complaint`、`collect_exam_context`、普通 `verify_evidence` 三类回答处理分支
    - `run_reasoning_search()` 内部的 `search tree / select_leaf / R2 / ActionBuilder / rollout / backprop / trajectory evaluator`
    - 实时前端、CLI、`ReplayEngine` 的外层调用链

- 更新入口文档：
  - [README.md](/Users/loki/Workspace/GraduationDesign/README.md)
    - 增加 brain 详细运行链路指南链接
  - [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)
    - 在目录关系区补充详细运行链路说明入口

### 结果影响

- 现在可以从文档直接回答：
  - 首轮主诉进入系统后具体跑了哪些函数
  - 后续病人回答上一轮问题时，A4 和状态更新先发生什么
  - 为什么系统不是机械的 `A1 -> A2 -> A3 -> A4` 单向流水线，而是“先消化上一问，再决定下一步”的单轮编排器
  - verifier / guarded gate / repair 为什么会让系统“已经像能停了，但还继续问一轮”

### 验证

- 人工逐文件核对并回填到文档：
  - [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - [brain/state_tracker.py](/Users/loki/Workspace/GraduationDesign/brain/state_tracker.py)
  - [brain/types.py](/Users/loki/Workspace/GraduationDesign/brain/types.py)
  - [brain/retriever.py](/Users/loki/Workspace/GraduationDesign/brain/retriever.py)
  - [brain/action_builder.py](/Users/loki/Workspace/GraduationDesign/brain/action_builder.py)
  - [brain/simulation_engine.py](/Users/loki/Workspace/GraduationDesign/brain/simulation_engine.py)
  - [brain/trajectory_evaluator.py](/Users/loki/Workspace/GraduationDesign/brain/trajectory_evaluator.py)
  - [simulator/replay_engine.py](/Users/loki/Workspace/GraduationDesign/simulator/replay_engine.py)
  - [simulator/patient_agent.py](/Users/loki/Workspace/GraduationDesign/simulator/patient_agent.py)
- 本次为纯文档更新，未改动运行逻辑，因此未额外执行单元测试

## 二十九、2026-04-28：为 `process_turn()` 补充分段中文注释

### 本次目标

- 让 `brain/service.py` 里的核心单轮编排函数 `process_turn()` 更容易被直接阅读
- 不改任何控制流，只在关键阶段切换处补充“这一段为什么存在”的中文说明
- 让后续读代码的人能更容易把源码和 `brain_runtime_call_chain_guide.md` 对上

### 本次实现

- [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - 在 `process_turn()` 内部新增分段中文注释，覆盖：
    - 单轮入口先做 `increment_turn + ingest_patient_turn + update_from_pending_action`
    - A1 / entity linking 的执行条件
    - `route_after_a4` 与 `route_after_slot_update` 如何共同决定 `effective_stage`
    - 检查 follow-up、主诉澄清、fallback 等快捷分支
    - 常规主路径中的 `A2 -> run_reasoning_search()`
    - search 结束后的 `stop rule -> verifier -> repair`
    - 为什么要把 `selected_action` 再写回 `pending_action`

- [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)
  - 在 `service.py` 说明处补充：
    - `process_turn()` 已带分段中文注释，便于顺着源码阅读控制流

### 结果影响

- 现在即使不先看详细设计文档，也能直接在 `process_turn()` 源码里看到：
  - 哪一段是在消化上一轮回答
  - 哪一段是在决定本轮是否跑 A1 / A2 / A3
  - 哪一段是在做 stop / verifier / repair
  - 哪一段是在构造下一轮 `pending_action`

### 验证

- 执行：

```bash
python -m py_compile brain/service.py
```

- 结果：
  - `py_compile` 通过

- 本次仅增加注释，未改动运行逻辑，因此未额外执行单元测试

## 三十、2026-04-28：补充 AGENTS 长期注释约定

### 本次目标

- 把“核心函数内部关键步骤前也要有中文注释”固化进仓库级 agent 约定
- 避免后续只满足“文件头 / 函数头有中文说明”，但长函数内部仍缺少流程分段注释

### 本次实现

- 更新：
  - [AGENTS.md](/Users/loki/Workspace/GraduationDesign/AGENTS.md)
- 新增约定：
  - 对于 `brain/`、`simulator/` 等核心流程较长的函数，除了函数上方用途说明外，函数内部的关键步骤、阶段切换、分支入口前也要补充简短中文注释，帮助后续读代码的人顺着控制流理解实现

### 结果影响

- 以后仓库里的注释规范不再只要求：
  - 文件顶部说明
  - 类 / 函数用途说明
- 还明确要求：
  - 长函数内部关键步骤前的中文分段注释

### 验证

- 人工核对 [AGENTS.md](/Users/loki/Workspace/GraduationDesign/AGENTS.md) 中“工作原则”小节，新增规则已写入
- 本次为文档规则更新，未涉及代码逻辑与测试执行

## 三十一、2026-04-30：修复单轮病例导致前端 slider 崩溃

### 本次目标

- 修复 Streamlit 前端在展示“只有 1 轮”的 demo / replay 病例时抛出：
  - `StreamlitAPIException: Slider min_value must be less than the max_value`
- 保持修复范围只在前端展示层，不改后端 replay 数据结构

### 本次实现

- 更新：
  - [frontend/app.py](/Users/loki/Workspace/GraduationDesign/frontend/app.py)
  - [frontend/README.md](/Users/loki/Workspace/GraduationDesign/frontend/README.md)
- 具体改动：
  - 演示回放模式中，只有 `len(turns) > 1` 时才渲染“回放轮次” slider
  - 实验复盘模式中，只有 `len(turns) > 1` 时才渲染“复盘轮次” slider
  - 当病例仅有 1 轮记录时，改为显示“无需切换轮次”的提示文案

### 结果影响

- 单轮病例不再因为前端控件边界条件直接导致整页报错
- 该问题被明确收敛为前端渲染 bug，而不是 replay 结果文件格式异常

### 验证

- 执行：

```bash
python -m py_compile frontend/app.py
```

- 结果：
  - `py_compile` 通过

## 三十二、2026-04-30：修复 replay 复盘对话缺失后续系统提问

### 本次目标

- 修复实验复盘页面中 `replay_results.jsonl` 对话区只显示首轮系统问题、后续轮次只剩患者回答的问题
- 让自动 replay 的问答顺序与真实执行顺序一致，避免前端把问题漏掉或重复展示

### 本次实现

- 更新：
  - [frontend/output_browser.py](/Users/loki/Workspace/GraduationDesign/frontend/output_browser.py)
  - [frontend/ui_adapter.py](/Users/loki/Workspace/GraduationDesign/frontend/ui_adapter.py)
  - [frontend/app.py](/Users/loki/Workspace/GraduationDesign/frontend/app.py)
  - [frontend/README.md](/Users/loki/Workspace/GraduationDesign/frontend/README.md)
  - [tests/test_output_browser.py](/Users/loki/Workspace/GraduationDesign/tests/test_output_browser.py)
- 具体改动：
  - 为前端对话结构增加 `chat_order` 语义，区分：
    - `patient_then_system`
    - `system_then_patient`
  - `replay_results.jsonl` 的 `initial_output` 继续展示“患者 opening -> 系统首问”
  - 后续 replay turn 改为按“系统先问 -> 患者再答”渲染
  - 当 `initial_output` 已经显示首轮问题时，自动抑制 turn 1 的重复 `question_text`

### 结果影响

- 复盘页面现在能正确展示第 2 轮及之后的系统追问
- 首轮系统问题不会被重复展示两次
- focused repair summary 与 replay results 的对话顺序更接近真实执行语义

### 验证

- 执行：

```bash
python -m pytest tests/test_output_browser.py tests/test_frontend_ui_adapter.py -q
python -m py_compile frontend/app.py frontend/output_browser.py frontend/ui_adapter.py
```

- 结果：
  - 通过

## 三十六、2026-05-01：把 `process_turn()` 主链路切到统一 `turn_interpreter`

### 本次目标

- 落地“去 A4 化后的统一提及项驱动”第一阶段重构
- 让 `brain/service.py::process_turn()` 每轮只做一次 LLM 长文本解释
- 用同一份 `mentions` 同时驱动：
  - `PatientContext`
  - `A1 key_features`
  - pending action 的 target-aware 解释
  - 会话级 `mention_context` 合并

### 本次实现

- 更新：
  - [brain/types.py](/Users/loki/Workspace/GraduationDesign/brain/types.py)
  - [brain/state_tracker.py](/Users/loki/Workspace/GraduationDesign/brain/state_tracker.py)
  - [brain/entity_linker.py](/Users/loki/Workspace/GraduationDesign/brain/entity_linker.py)
  - [brain/llm_client.py](/Users/loki/Workspace/GraduationDesign/brain/llm_client.py)
  - [brain/evidence_parser.py](/Users/loki/Workspace/GraduationDesign/brain/evidence_parser.py)
  - [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)
  - [tests/test_evidence_parser.py](/Users/loki/Workspace/GraduationDesign/tests/test_evidence_parser.py)
  - [tests/test_service_stop_flow.py](/Users/loki/Workspace/GraduationDesign/tests/test_service_stop_flow.py)
- 具体改动：
  - `types.py`
    - 新增 `MentionPolarity`、`TurnInterpretationResult`、`MentionContextItem`
    - 为 `ClinicalFeatureItem / SlotState / EvidenceState / SlotUpdate / SessionState` 补上统一 mention 语义字段
  - `state_tracker.py`
    - 新增 `merge_mention_items()`
    - 会话级提及项按 `present > unclear > absent` 做简单优先级合并
  - `entity_linker.py`
    - 新增 `link_mention_items()`，允许对 `present / unclear / absent` 提及项统一做链接
  - `llm_client.py`
    - 新增 `turn_interpreter` 结构化 prompt
    - 输出固定收口到 `mentions + reasoning_summary`
  - `evidence_parser.py`
    - 新增 `interpret_turn()` 主入口
    - `A1` 改为从统一 `mentions` 派生
    - `interpret_answer_for_target()` 改为复用同一份 `turn_interpreter` 结果，而不是再做第二次长文本解释
    - 短答命中时会显式保留 `direct_reply` 元数据，避免 judge 再额外调用一次 LLM
  - `service.py`
    - `process_turn()` 改成：
      1. 调用一次 `turn_interpreter`
      2. 合并 `mention_context`
      3. 落通用 `slots / evidence_states`
      4. 再消费 pending action
      5. 由同一份结果派生 `A1`
    - 新增对旧 `entity_linker` 测试桩的薄兼容，避免只实现了 `link_clinical_features()` 的老桩直接报错

### 结果影响

- 长文本患者回答不再被 `MedExtractor` 和 `A4 target parser` 各解释一遍
- “新症状”与“回答上一轮问题”开始共用同一个统一提及结果
- 短答规则链真正可以短路，不会再被后续 judge 二次触发 LLM
- 会话内开始显式保留 `mention_context`，为后续继续推进“去 A4 化”和上下文合并简化打下基础

### 验证

- 执行：

```bash
python -m pytest tests/test_evidence_parser.py tests/test_service_stop_flow.py tests/test_state_tracker.py tests/test_entity_linker.py tests/test_replay_engine.py tests/test_run_batch_replay.py -q
python -m pytest tests/test_stop_rules.py tests/test_router_control_flow.py tests/test_exam_context_flow.py -q
```

- 结果：
  - 通过

## 三十七、2026-05-01：继续把下游收口到 `polarity` 语义

### 本次目标

- 让统一 `mentions` 主链路继续向下游收口
- 减少 `router / hypothesis_manager / report_builder / search signature / guarded stop` 对旧 `existence/status` 语义的主依赖
- 保持历史对象兼容，但把“优先读 `polarity`”固定下来

### 本次实现

- 更新：
  - [brain/types.py](/Users/loki/Workspace/GraduationDesign/brain/types.py)
  - [brain/hypothesis_manager.py](/Users/loki/Workspace/GraduationDesign/brain/hypothesis_manager.py)
  - [brain/router.py](/Users/loki/Workspace/GraduationDesign/brain/router.py)
  - [brain/mcts_engine.py](/Users/loki/Workspace/GraduationDesign/brain/mcts_engine.py)
  - [brain/retriever.py](/Users/loki/Workspace/GraduationDesign/brain/retriever.py)
  - [brain/report_builder.py](/Users/loki/Workspace/GraduationDesign/brain/report_builder.py)
  - [brain/stop_rules.py](/Users/loki/Workspace/GraduationDesign/brain/stop_rules.py)
  - [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)
  - [tests/test_hypothesis_manager.py](/Users/loki/Workspace/GraduationDesign/tests/test_hypothesis_manager.py)
  - [tests/test_router_control_flow.py](/Users/loki/Workspace/GraduationDesign/tests/test_router_control_flow.py)
  - [tests/test_report_builder.py](/Users/loki/Workspace/GraduationDesign/tests/test_report_builder.py)
- 具体改动：
  - `types.py`
    - 为 `SlotState`、`EvidenceState` 新增 `effective_polarity()`，把旧 `status/existence` 自动兼容映射到统一极性
  - `hypothesis_manager.py`
    - 证据反馈改为优先按 `EvidenceState.effective_polarity()` 计分
    - 为 `unclear` 引入轻量惩罚，表达“降低置信、等待复核”
  - `router.py`
    - 路由决策优先读取 `A4DeductiveResult.metadata["polarity"]`
    - 旧 `existence` 字段保留为兼容 fallback
  - `mcts_engine.py`
    - 状态签名开始显式编码 `present / absent / unclear`
  - `retriever.py`
    - 证据画像状态判断优先按 `effective_polarity()` 输出
    - 对 `unclear` 补充 `待复核` 展示文案
  - `report_builder.py`
    - 最终报告新增 `confirmed_slots[].polarity`
    - 新增会话级 `mention_context` 输出
  - `service.py`
    - verifier 上下文摘要补入 `mention_context` 与 `polarity`
  - `stop_rules.py`
    - guarded acceptance 相关关键证据判断改为优先按 `effective_polarity()` 处理

### 结果影响

- 下游开始真正把 `present / absent / unclear` 当成第一语义来源
- 旧 `existence/status` 仍可兼容历史对象和旧测试桩，但不再是主判断入口
- 报告和前端展示更容易直接表达“患者提到了什么、是肯定/否定/不清楚”
- 为后续继续弱化 `A4` 的中心地位、进一步简化状态机打下了更稳的基础

### 验证

- 执行：

```bash
python -m pytest tests/test_hypothesis_manager.py tests/test_router_control_flow.py tests/test_report_builder.py -q
python -m pytest tests/test_stop_rules.py tests/test_service_stop_flow.py tests/test_exam_context_flow.py tests/test_retriever.py tests/test_simulation_engine.py tests/test_replay_engine.py -q
python -m py_compile brain/types.py brain/hypothesis_manager.py brain/router.py brain/mcts_engine.py brain/retriever.py brain/report_builder.py brain/stop_rules.py brain/service.py
```

- 结果：
  - 通过

## 三十三、2026-04-30：修复实验复盘下拉框切换不灵敏

### 本次目标

- 修复实验复盘模式中“选择病例记录”经常需要点两下才真正切换的问题
- 统一 Streamlit widget 状态与 `experiment_run_key / experiment_case_index` 的来源，减少交互迟滞

### 本次实现

- 更新：
  - [frontend/app.py](/Users/loki/Workspace/GraduationDesign/frontend/app.py)
  - [frontend/README.md](/Users/loki/Workspace/GraduationDesign/frontend/README.md)
- 具体改动：
  - 为实验输出目录与病例记录下拉框增加显式 `session_state` key
  - 下拉框选项值改为稳定的整数 index，而不再依赖长字符串 label 反查
  - 在 `_load_experiment_run()` / `_load_experiment_case()` 中同步回写对应 widget 状态
  - 刷新实验索引或当前 run key 失效时，重置下拉框状态，避免旧值残留

### 结果影响

- 实验复盘模式中的病例切换更接近单击即生效
- 前端不再同时维护“widget 当前选项”和“手动 case index”两套容易打架的状态

### 验证

- 执行：

```bash
python -m py_compile frontend/app.py
```

- 结果：
  - `py_compile` 通过

## 三十四、2026-04-30：修复实验病例下拉框的 Streamlit session_state 写回异常

### 本次目标

- 修复实验复盘模式切换病例时出现：
  - `st.session_state.experiment_case_select cannot be modified after the widget with key experiment_case_select is instantiated`
- 保留上一轮的下拉框同步策略，但消除对 Streamlit widget 生命周期的违规写入

### 本次实现

- 更新：
  - [frontend/app.py](/Users/loki/Workspace/GraduationDesign/frontend/app.py)
  - [frontend/README.md](/Users/loki/Workspace/GraduationDesign/frontend/README.md)
- 具体改动：
  - 删除 `_load_experiment_case()` 中对 `EXPERIMENT_CASE_SELECT_KEY` 的重复回写
  - 保留在 widget 创建前的初始化 / reset / run 切换时同步 key 的逻辑

### 结果影响

- 切换病例时不再因同轮回写已实例化 widget key 而直接报错
- 实验复盘下拉框的状态同步改为更符合 Streamlit 生命周期约束的方式

### 验证

- 执行：

```bash
python -m py_compile frontend/app.py
```

- 结果：
  - `py_compile` 通过

## 三十五、2026-04-30：增强实验复盘中的病例切换与终态展示

### 本次目标

- 为“选择病例记录”补充“上一条病例 / 下一条病例”按钮，降低连续复盘时的切换成本
- 让前端直接展示当前病例是：
  - 圆满结束
  - 达到最大轮次停止
  - 异常出错结束
- 当病例异常失败时，前端可直接查看错误原因与结构化错误详情

### 本次实现

- 更新：
  - [frontend/app.py](/Users/loki/Workspace/GraduationDesign/frontend/app.py)
  - [frontend/output_browser.py](/Users/loki/Workspace/GraduationDesign/frontend/output_browser.py)
  - [frontend/README.md](/Users/loki/Workspace/GraduationDesign/frontend/README.md)
  - [tests/test_output_browser.py](/Users/loki/Workspace/GraduationDesign/tests/test_output_browser.py)
- 具体改动：
  - 在实验复盘的病例下拉框上方新增“上一条病例 / 下一条病例”按钮
  - `summarize_case_record()` 现在会整理：
    - `run_status`
    - `run_status_label`
    - `error_code / error_stage / error_prompt_name / error_message / error_attempts`
  - 当前病例摘要新增“运行结果”字段
  - 当病例 `failed` 时，页面会额外展示错误摘要、错误字段表，以及可展开的结构化错误 JSON

### 结果影响

- 连续翻看病例时不再只能依赖长下拉框逐个点选
- replay / smoke 输出中的失败病例不再只存在于 JSONL 文件里，而能在前端直接看见失败原因
- “停止原因”和“运行结果”被明确区分：
  - 运行结果回答“这条病例是怎么结束的”
  - 停止原因继续保留后端原始 stop reason 语义

### 验证

- 执行：

```bash
python -m pytest tests/test_output_browser.py tests/test_frontend_ui_adapter.py -q
python -m py_compile frontend/app.py frontend/output_browser.py
```

- 结果：
  - 通过

## 三十六、2026-05-01：完成去 A4 化的全量替换与输出收口

### 本次目标

- 把运行时里的 `A4DeductiveResult / DeductiveDecision / route_after_a4 / a4_evidence_audit` 全量替换为统一的 `pending_action_*` 结构
- 让 `EvidenceState` 真正由统一 `mentions` 直接写入，pending action 只做目标节点富化、反馈和路由
- 同步收口前端、focused replay 脚本、README 与配置命名，避免出现“一半叫 A4、一半叫 mentions”的混合状态

### 本次实现

- 更新：
  - [brain/types.py](/Users/loki/Workspace/GraduationDesign/brain/types.py)
  - [brain/router.py](/Users/loki/Workspace/GraduationDesign/brain/router.py)
  - [brain/evidence_parser.py](/Users/loki/Workspace/GraduationDesign/brain/evidence_parser.py)
  - [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - [brain/simulation_engine.py](/Users/loki/Workspace/GraduationDesign/brain/simulation_engine.py)
  - [brain/llm_client.py](/Users/loki/Workspace/GraduationDesign/brain/llm_client.py)
  - [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)
  - [frontend/ui_adapter.py](/Users/loki/Workspace/GraduationDesign/frontend/ui_adapter.py)
  - [frontend/app.py](/Users/loki/Workspace/GraduationDesign/frontend/app.py)
  - [frontend/output_browser.py](/Users/loki/Workspace/GraduationDesign/frontend/output_browser.py)
  - [frontend/README.md](/Users/loki/Workspace/GraduationDesign/frontend/README.md)
  - [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)
  - [scripts/run_focused_repair_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_focused_repair_replay.py)
  - [scripts/run_focused_ablation.py](/Users/loki/Workspace/GraduationDesign/scripts/run_focused_ablation.py)
  - [scripts/run_single_case_smoke.py](/Users/loki/Workspace/GraduationDesign/scripts/run_single_case_smoke.py)
  - 多个相关测试文件
- 具体改动：
  - 新增 `PendingActionResult / PendingActionDecision`，删除运行时对 `A4DeductiveResult / DeductiveDecision` 的依赖
  - `process_turn()` 统一返回：
    - `pending_action_result`
    - `pending_action_decision`
    - `route_after_pending_action`
    - `pending_action_audit`
  - 普通 `verify_evidence` 不再额外生成一份 A4 专用 `slot / evidence_state`
    - 统一先由 `turn_interpreter -> mentions -> slots / evidence_states`
    - pending action 再只对目标证据节点补充 action metadata、family 标记、reward 与 hypothesis feedback
  - `simulation_engine` 的 rollout 轨迹从 `A3 -> A4 -> ROUTE` 改为 `A3 -> PENDING_ACTION -> ROUTE`
  - focused replay / ablation 输出文件改名为 `pending_action_audit.jsonl`
  - 前端卡片从 “A4 回答解释与路由” 改为 “上一轮回答解释与路由”
  - 移除不再使用的 `a4_deductive_judge` / `a4_target_answer_interpretation` prompt 定义以及 `configs/brain.yaml` 中的 `a4.use_llm_deductive_judge`

### 结果影响

- 运行时不再维护第二套 A4 专用语义外壳，真正收口到“统一提及项 + 上一轮动作解释”这条主链路
- `EvidenceState` 的来源更单一，后续调试“为什么某条证据进入 / 没进入状态机”时可以直接回看 `mentions`
- 前端、脚本和实验复盘输出都改成同一套 `pending_action_*` 命名，减少理解和排错负担

### 验证

- 执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_router_control_flow.py tests/test_exam_context_flow.py tests/test_evidence_parser.py tests/test_focused_replay_metrics.py tests/test_service_config.py -q
conda run -n GraduationDesign python -m pytest tests/test_frontend_ui_adapter.py tests/test_output_browser.py tests/test_simulation_engine.py tests/test_service_stop_flow.py tests/test_report_builder.py -q
```

- 结果：
  - `23 passed`
  - `17 passed`

## 三十七、2026-05-02：Observed Anchor-Controlled Reasoning 接入诊断链路

### 本次目标

- 借鉴 Med-MCTS 的路径边界，把 rollout 保持为候选推理路径，不再让模拟阳性污染真实会话证据
- 用真实患者回答中的高特异证据形成 `observed anchor`，统一支配 A2 排序、repair 分流和 stop gate
- 收敛 verifier / guarded / repair 的职责边界，把细粒度 guarded 原因降级为 metadata，主控制原因转向 anchor + evidence family coverage
- 修复检查、病原、影像和数值型 detail 的“没做过 / 没听说 / 没注意”被误写成 hard negative 的问题

### 本次实现

- 新增：
  - [brain/evidence_anchor.py](/Users/loki/Workspace/GraduationDesign/brain/evidence_anchor.py)
  - [tests/test_evidence_anchor.py](/Users/loki/Workspace/GraduationDesign/tests/test_evidence_anchor.py)
- 更新：
  - [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - [brain/stop_rules.py](/Users/loki/Workspace/GraduationDesign/brain/stop_rules.py)
  - [brain/hypothesis_manager.py](/Users/loki/Workspace/GraduationDesign/brain/hypothesis_manager.py)
  - [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)
  - [configs/frontend.yaml](/Users/loki/Workspace/GraduationDesign/configs/frontend.yaml)
  - [frontend/config_loader.py](/Users/loki/Workspace/GraduationDesign/frontend/config_loader.py)
  - [frontend/README.md](/Users/loki/Workspace/GraduationDesign/frontend/README.md)
  - [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)
  - [docs/diagnosis_system_todolist.md](/Users/loki/Workspace/GraduationDesign/docs/diagnosis_system_todolist.md)
  - 相关 stop / repair 单测
- 具体改动：
  - `EvidenceAnchorAnalyzer` 只读取真实 `SessionState.slots / evidence_states`，并过滤 `rollout / simulation` 来源证据
  - anchor 分级为 `strong_anchor / provisional_anchor / background_supported / negative_anchor`
  - A2 写回候选前会执行 anchor-aware rerank，候选 metadata 增加 `observed_anchor_score / anchor_tier / anchor_supporting_evidence / background_support_score / anchor_negative_evidence`
  - 收紧 anchor 与 coverage 判定：证据必须命中候选疾病自身 KG evidence payload 才能成为 anchor；minimum evidence family coverage 只统计 `present + clear` 的真实证据，`absent / unclear / hedged` 不再补 coverage
  - 新增 `acceptance_profile=anchor_controlled`，默认写入 [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)
  - stop gate 改为优先读取显式 `StopRuleConfig` / 配置文件；`BRAIN_ACCEPTANCE_PROFILE` 只覆盖默认 baseline，并与 verifier prompt 使用的 `TRAJECTORY_VERIFIER_ACCEPTANCE_PROFILE` 分离；前端默认结构化 stop 为 `anchor_controlled`，verifier prompt 仍保留 `guarded_lenient`
  - anchor gate 拒停时统一输出 `missing_required_anchor / anchored_alternative_exists / insufficient_evidence_family_coverage`
  - repair 在 `anchored_alternative_exists` 时直接围绕 anchored candidate 取 R2，不再沿错误 top hypothesis 持续补证据
  - 普通 `verify_evidence` 对检查/病原/影像/测量类 no-result 回答做后处理：未做、没听说、没注意、不记得归为 `unclear`；阴性、未检出、未见异常仍保留 `absent`

### 结果影响

- `水痘-带状疱疹病毒 / 巨细胞病毒` 这类真实病原体锚点进入会话后，会稳定保护对应候选，不再被只靠 HIV/CD4 背景或 rollout 模拟阳性的候选压过
- rollout 里的 `MTB培养阳性` 之类模拟证据不会计入 stop gate 的 confirmed evidence
- stop / repair 的主路径更接近“真实观测锚点 + 最低证据覆盖”，旧 guarded 细规则仍保留为复盘字段和消融 baseline
- BMI、CD4、CT、病原体等检查或测量结果的“没做过”不再直接变成 hard negative

### 验证

- 执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_evidence_anchor.py tests/test_stop_rules.py tests/test_service_repair_flow.py -q
conda run -n GraduationDesign python -m pytest tests/test_evidence_parser.py tests/test_action_builder.py tests/test_exam_context_flow.py tests/test_service_stop_flow.py tests/test_trajectory_evaluator.py tests/test_retriever.py tests/test_hypothesis_manager.py -q
conda run -n GraduationDesign python -m pytest -q
```

- 结果：
  - `46 passed`
  - `53 passed`
  - `217 passed`

## 三十八、2026-05-03：Evidence Linker 泛化增强与 Evidence-Profile Stop Gate

### 本次目标

- 继续从算法接口层修复“患者表达到图谱节点”的漏连问题，而不是为 smoke 病例写疾病级补丁
- 让 `anchor_controlled` stop gate 区分两条路径：
  - 有真实强锚点时继续走 observed-anchor 控制
  - 没有强锚点的低成本问诊路径，允许用多族低成本真实阳性证据、top 稳定和无强冲突作为保守放行依据

### 本次实现

- 更新：
  - [brain/normalization.py](/Users/loki/Workspace/GraduationDesign/brain/normalization.py)
  - [brain/entity_linker.py](/Users/loki/Workspace/GraduationDesign/brain/entity_linker.py)
  - [brain/evidence_anchor.py](/Users/loki/Workspace/GraduationDesign/brain/evidence_anchor.py)
  - [brain/stop_rules.py](/Users/loki/Workspace/GraduationDesign/brain/stop_rules.py)
  - [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)
  - [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)
  - 相关实体链接、anchor 与 stop rule 单测
- 具体改动：
  - `NameNormalizer` 新增通用 surface form 扩展：高烧/高热、免疫力低下、BDG/β-D 葡聚糖升高、乙肝/HBV 感染、空腹血糖偏高、甘油三酯偏高等表达可扩展到图谱候选节点
  - `EntityLinker` 记录 `expansion_rule / expansion_rules`，并把模板扩展命中的 surface form 作为可审计 metadata 写回
  - `EvidenceAnchorAnalyzer` 计算低成本 evidence profile：只统计 `present + clear`、低成本、非背景、跨多个 family 的真实观测证据；HIV/CD4/发热等背景线索不会单独满足 low-cost profile
  - family coverage 与 low-cost profile 共用 catalog 风格 family 名称，避免 `respiratory` 和 `respiratory_symptom` 这类旧/新标签把同一证据重复计数
  - `StopRuleConfig` 新增 `enable_evidence_profile_acceptance / min_low_cost_profile_families / min_low_cost_profile_present_clear_count / min_low_cost_profile_stable_top_count / allow_soft_verifier_reject_with_evidence_profile`
  - `anchor_controlled` 在没有强锚点时，如果满足多族低成本证据、top answer 稳定、无更强 anchored alternative、无 clear negative definition evidence，允许覆盖 soft verifier reject；`hard_negative_key_evidence / anchored_alternative_exists / contradiction` 等硬拒绝仍然阻断 stop

### 结果影响

- 对高烧、免疫力低下、BDG 升高、HBV 感染、血糖/甘油三酯偏高这类通用医学表达，opening 或后续回答更容易稳定落到图谱节点
- 低成本问诊型疾病不再被“没有强检查锚点”一律卡死；但放行仍需要跨 family 的真实阳性证据和 top 稳定，且不能覆盖硬反证或更强 anchored alternative
- stop gate 的策略从“只有强锚点才可能停”扩展为“强锚点路径 + 低成本证据画像路径”，更适合后续 benchmark 中检查型疾病与低成本问诊型疾病共存的设置

### 验证

- 执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_entity_linker.py tests/test_evidence_anchor.py tests/test_stop_rules.py -q
conda run -n GraduationDesign python -m pytest tests/test_service_stop_flow.py tests/test_service_repair_flow.py -q
conda run -n GraduationDesign python -m pytest tests/test_entity_linker.py tests/test_evidence_anchor.py tests/test_stop_rules.py tests/test_service_stop_flow.py tests/test_service_repair_flow.py tests/test_service_config.py tests/test_trajectory_evaluator.py tests/test_hypothesis_manager.py -q
conda run -n GraduationDesign python -m pytest -q
```

- 结果：
  - `23 passed`
  - `26 passed`
  - `60 passed`
  - `212 passed`

## 三十九、2026-05-04：五类诊断算法问题的统一修复

### 本次目标

- 从算法根源处理 smoke80 暴露的 `best_final_answer` 错误，而不是继续增加病例级补丁
- 将 observed anchor 从“有无强锚点”细化为“精确作用域 / 同族作用域 / 背景作用域”
- 让 A2 排序、repair 反馈、trajectory 聚合和 cold-start fallback 共用同一套真实观测证据信号

### 本次实现

- 更新：
  - [brain/evidence_anchor.py](/Users/loki/Workspace/GraduationDesign/brain/evidence_anchor.py)
  - [brain/hypothesis_manager.py](/Users/loki/Workspace/GraduationDesign/brain/hypothesis_manager.py)
  - [brain/trajectory_evaluator.py](/Users/loki/Workspace/GraduationDesign/brain/trajectory_evaluator.py)
  - [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - [brain/stop_rules.py](/Users/loki/Workspace/GraduationDesign/brain/stop_rules.py)
  - [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)
  - 相关 anchor、trajectory、repair、cold-start 单测
- 具体改动：
  - `EvidenceAnchorAnalyzer` 新增 `anchor_scope`：`exact_scope / family_scope / phenotype_scope / background_scope / competing_scope`
  - 病原证据如果只说明“同一种病原”，但候选是脑膜炎、肺炎、肝炎、视网膜炎等部位特异疾病，会降为 `family_anchor`；只有精确疾病或定义性证据才进入 `strong_anchor / definition_anchor`
  - HBV/HCV 共病、HIV/CD4/发热/免疫低下等泛背景证据被压到 `background_supported`，并记录 `background_attractor_score`
  - `blood_count` 不再因为 family tag 被全局当作 definition anchor；定义性 detail 仍由 `DIAGNOSED_BY / REQUIRES_DETAIL` 或真正定义阈值驱动
  - A2 anchor rerank 改为优先比较 `exact_scope_anchor_score`，其次比较 `family_scope_anchor_score`，再看 phenotype/background，减少卡波西、COVID、HIV/CD4 背景吸引子
  - repair 新增 `repair_feedback_counts`：同一答案连续因 `missing_key_support / missing_required_anchor / insufficient_evidence_family_coverage / trajectory_insufficient` 被拒停时，会逐轮增加当前答案降权；已有 observed anchor 的备选会获得 repair 抬分
  - `TrajectoryEvaluator` 读取 `observed_anchor_index`，把 exact/family anchor 写入 `FinalAnswerScore.metadata` 并转成 `observed_anchor_agent_bonus`
  - rollout 模拟关键阳性如果没有真实 observed anchor 承接，会产生 `simulated_key_evidence_penalty`，不再让模拟路径单独把错误答案顶到最前
  - `score_candidate_hypotheses_without_trajectories()` 也消费 anchor profile，缓解 top hypothesis 已经正确但 `best_answer=None / no_answer_score` 的断层
  - cold-start fallback 先扩展当前候选下仍可直接回答的低成本 R2 证据；没有低成本证据时才退回全局探针，并降权 HIV/CD4/年龄/发热等抽象背景问题

### 结果影响

- 同病原不同部位/亚型的错误会更少被单一病原证据直接接受，系统会继续追问部位、影像、症状或定义性检查
- 乙肝、HIV/CD4、发热、免疫低下这类背景证据仍能提供上下文，但不再轻易压过真实特异证据
- repair 不再只是一轮 soft hint；重复缺口会回写到候选排序，推动系统切换到更有真实支持的备选或补更关键的证据
- trajectory 聚合更接近 Med-MCTS 的边界：rollout 负责探索，真实患者回答负责最终答案排序与可接受性

### 验证

- 执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_evidence_anchor.py tests/test_hypothesis_manager.py tests/test_trajectory_evaluator.py tests/test_service_stop_flow.py tests/test_service_repair_flow.py tests/test_stop_rules.py -q
conda run -n GraduationDesign python -m pytest -q
```

- 结果：
  - `60 passed`
  - `219 passed`

## 四十、2026-05-04：批量模型替换 smoke 与回放链路运行时修复

### 本次目标

- 评估当前动态问诊链路是否必须继续使用 `qwen3-max`
- 用统一 10 例 smoke 对比 `qwen3-max / qwen3.5-flash / qwen3.5-plus` 的正确率与耗时
- 修复阻塞批量回放的运行时问题，避免模型对比结果被代码异常污染

### 本次实现

- 更新：
  - [frontend/config_loader.py](/Users/loki/Workspace/GraduationDesign/frontend/config_loader.py)
  - [brain/evidence_anchor.py](/Users/loki/Workspace/GraduationDesign/brain/evidence_anchor.py)
  - [brain/trajectory_evaluator.py](/Users/loki/Workspace/GraduationDesign/brain/trajectory_evaluator.py)
  - [README.md](/Users/loki/Workspace/GraduationDesign/README.md)
  - [tests/test_frontend_config_loader.py](/Users/loki/Workspace/GraduationDesign/tests/test_frontend_config_loader.py)
- 具体改动：
  - `apply_config_to_environment()` 允许命令行里显式设置的 `OPENAI_MODEL` 覆盖 `frontend.local.yaml` 默认模型，便于不修改本地私密配置就做模型 smoke
  - 修复 `EvidenceAnchorAnalyzer` 的作用域/签名回归，恢复 definition-anchor 与 negative-anchor 的运行链路
  - 修复 `TrajectoryEvaluator` 的 observed-anchor fallback 链路，避免 verifier 分支在读取 anchor profile 时触发运行时异常
  - `TrajectoryEvaluator._observed_support_from_anchor_profile()` 对 anchor evidence 做轻量去重，减少 verifier 输入里重复证据

### 模型 smoke

- 统一参数：

```bash
conda run -n GraduationDesign python -B scripts/run_batch_replay.py \
  --cases-file simulator/focused_acceptance_cases.jsonl \
  --max-turns 5 \
  --case-concurrency 1 \
  --no-resume
```

- 输出目录：
  - `test_outputs/model_swap_eval/20260504_model_swap_eval_final/qwen3-max/smoke10/`
  - `test_outputs/model_swap_eval/20260504_model_swap_eval_final/qwen3.5-flash/smoke10/`
  - `test_outputs/model_swap_eval/20260504_model_swap_eval_final/qwen3.5-plus/smoke10/`
- 结果摘要：
  - `qwen3-max`：`final_answer_exact_hit_rate=0.2`，`hypothesis_hit_rate=0.6`，`average_case_seconds=135.7743`，`accepted_final_answer_count=0`
  - `qwen3.5-flash`：`final_answer_exact_hit_rate=0.2`，`hypothesis_hit_rate=0.7`，`average_case_seconds=53.0081`，`accepted_final_answer_count=1`
  - `qwen3.5-plus`：`final_answer_exact_hit_rate=0.2`，`hypothesis_hit_rate=0.8`，`average_case_seconds=87.1138`，`accepted_final_answer_count=0`

### 结果影响

- 在这组 10 例里，`qwen3-max` 没有表现出更高的最终命中率，反而平均单例耗时最高
- `qwen3.5-plus` 比 `flash` 更慢，但没有换来更好的 `final_answer_exact_hit_rate` 或 stop 完成率
- `qwen3.5-flash` 目前是三者里性价比最高的候选：速度明显快于 `max` 和 `plus`，而关键准确率指标没有更差

### 验证

- 执行：

```bash
conda run -n GraduationDesign python -B -m pytest tests/test_evidence_anchor.py tests/test_trajectory_evaluator.py tests/test_frontend_config_loader.py -q
```

- 结果：
  - `16 passed`

## 四十一、2026-05-04：默认 replay 模型切换为 qwen3.5-flash

### 本次目标

- 将本地回放与前端默认 LLM 从 `qwen3-max` 切换为 `qwen3.5-flash`
- 保留命令行环境变量覆盖能力，方便后续继续做模型对比或消融实验

### 本次实现

- 更新：
  - [configs/frontend.yaml](/Users/loki/Workspace/GraduationDesign/configs/frontend.yaml)
  - [frontend/config_loader.py](/Users/loki/Workspace/GraduationDesign/frontend/config_loader.py)
  - [README.md](/Users/loki/Workspace/GraduationDesign/README.md)
  - [frontend/README.md](/Users/loki/Workspace/GraduationDesign/frontend/README.md)
  - [tests/test_frontend_config_loader.py](/Users/loki/Workspace/GraduationDesign/tests/test_frontend_config_loader.py)
- 具体改动：
  - `configs/frontend.yaml` 默认模型改为 `qwen3.5-flash`
  - `frontend/config_loader.py` 保留显式 `OPENAI_MODEL`，避免命令行临时模型被 YAML 覆盖
  - README 同步默认模型与模型对比运行说明

### 验证

- 执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_frontend_config_loader.py -q
```

- 结果：
  - `1 passed`

## 四十二、2026-05-04：top1/top3 评估口径补充与全量单测回归

### 本次目标

- 在保留原有 `final_answer_exact_hit` 与 `hypothesis_hit` 的基础上，显式增加更适合 benchmark 复盘的两个口径
- 让未完成病例报告也能直接判断“最终答案是否已命中”和“真实诊断是否已进入前三候选”

### 本次实现

- 更新：
  - [simulator/benchmark.py](/Users/loki/Workspace/GraduationDesign/simulator/benchmark.py)
  - [tests/test_benchmark.py](/Users/loki/Workspace/GraduationDesign/tests/test_benchmark.py)
  - [README.md](/Users/loki/Workspace/GraduationDesign/README.md)
  - [simulator/README.md](/Users/loki/Workspace/GraduationDesign/simulator/README.md)
- 具体改动：
  - `BenchmarkSummary` 新增 `top1_final_answer_hit_count / top1_final_answer_hit_rate`
  - `BenchmarkSummary` 新增 `top3_hypothesis_hit_count / top3_hypothesis_hit_rate`
  - `non_completed_cases.json` 的每条病例记录新增 `top1_final_answer_hit / top3_hypothesis_hit`
  - `top1_final_answer_hit` 使用严格最终答案命中口径；`top3_hypothesis_hit` 使用最终候选前三的家族级召回口径

### 验证

- 执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_evidence_anchor.py tests/test_stop_rules.py tests/test_evidence_parser.py tests/test_benchmark.py tests/test_action_builder.py tests/test_service_stop_flow.py tests/test_service_repair_flow.py tests/test_hypothesis_manager.py tests/test_run_batch_replay.py tests/test_retriever.py tests/test_question_selector.py tests/test_llm_client_profiles.py -q
conda run -n GraduationDesign python -m pytest -q
```

- 结果：
  - `108 passed`
  - `223 passed`

## 四十三、2026-05-04：旁路结构化 stop gate 的全量消融开关

### 本次目标

- 为验证“只移除 stop rule 阈值，保留 verifier + repair”增加运行时消融模式
- 保留旧 `anchor_controlled` 代码，不删除 stop / verifier / repair 相关实现，便于后续随时切回对比

### 本次实现

- 更新：
  - [brain/stop_rules.py](/Users/loki/Workspace/GraduationDesign/brain/stop_rules.py)
  - [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)
  - [configs/frontend.yaml](/Users/loki/Workspace/GraduationDesign/configs/frontend.yaml)
  - [frontend/config_loader.py](/Users/loki/Workspace/GraduationDesign/frontend/config_loader.py)
  - [tests/test_stop_rules.py](/Users/loki/Workspace/GraduationDesign/tests/test_stop_rules.py)
- 具体改动：
  - 新增 `acceptance_profile=no_stop_gate`，别名兼容 `disabled / off / no-stop-gate`
  - `should_accept_final_answer()` 在该 profile 下旁路 `turn_index / anchor_controlled / trajectory_count / consistency / agent_eval / final_score` 等 stop rule 阈值
  - 当 verifier 已调用且接受时返回 `should_stop=true / reason=final_answer_accepted`
  - 当 verifier 已调用且拒绝时返回 `should_stop=false / reason=verifier_rejected_stop`，保留 repair 控制信号
  - `check_sufficiency()` 在该 profile 下不再触发 hypothesis margin stop，避免另一条 stop gate 继续影响消融
  - verifier 尚未调用时返回 `stop_gate_disabled` 并继续追问，避免没有 verifier 的早期 best answer 直接 completed
  - 默认配置切换为 `no_stop_gate`，本机 `configs/frontend.local.yaml` 也同步改为该 profile，确保 `run_batch_replay.py` 读取本地配置后不会回到 `anchor_controlled`

### 验证

- 执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_stop_rules.py tests/test_service_stop_flow.py tests/test_replay_engine.py tests/test_benchmark.py tests/test_frontend_config_loader.py -q
```

- 结果：
  - `31 passed`

## 四十四、2026-05-04：error-focus smoke95 病例集与旧 replay 输出清理

### 本次目标

- 从最新 `qwen3.5-flash + no_stop_gate` 全量 replay 中抽取后续专门回归的困难病例
- 删除旧版 smoke80 与历史 replay 输出，避免后续复盘时混淆新旧结果

### 本次输出

- 新增病例集：
  - `test_outputs/simulator_cases/graph_cases_20260502_role_qc/error_focus_smoke95_qwen35_no_stop_gate/cases.jsonl`
  - `test_outputs/simulator_cases/graph_cases_20260502_role_qc/error_focus_smoke95_qwen35_no_stop_gate/cases.json`
  - `test_outputs/simulator_cases/graph_cases_20260502_role_qc/error_focus_smoke95_qwen35_no_stop_gate/manifest.json`
  - `test_outputs/simulator_cases/graph_cases_20260502_role_qc/error_focus_smoke95_qwen35_no_stop_gate/summary.md`
- 抽取规则：
  - `completed::wrong_accepted`
  - `failed::api_connection_error`
  - `max_turn_reached::no_top1_final_answer_hit`
- 病例数：
  - total: `95`
  - wrong accepted: `38`
  - API failed: `6`
  - max-turn 且 top1 未命中: `51`
- 病例类型：
  - competitive: `18`
  - exam_driven: `25`
  - low_cost: `27`
  - ordinary: `25`

### 清理结果

- `test_outputs/simulator_replay/` 下仅保留：
  - `graph_cases_20260502_role_qc_full_qwen35_flash_no_stop_gate`
- 删除旧版：
  - `test_outputs/simulator_replay/graph_cases_20260502_role_qc_smoke80_qwen35_flash`
  - 其他旧 replay 输出目录
  - `test_outputs/simulator_cases/graph_cases_20260502_role_qc/non_completed_smoke80`

## 四十五、2026-05-04：fallback final evaluator、scope-aware 粒度守门与 low-cost explorer

### 本次目标

- 不继续微调 stop threshold，改为增强诊断链路本身：
  - `candidate_state_fallback` 没有 trajectory 时也能做真实证据最终评估
  - 减少泛疾病、部位特异疾病、IRIS、播散型疾病之间的粒度错误接受
  - 提升 low-cost 病例的有效证据揭示，避免只围绕 HIV/CD4/发热等背景线索漂移
  - 强 observed anchor 进入后扩展同病原 sibling 候选召回

### 本次实现

- 更新：
  - [brain/trajectory_evaluator.py](/Users/loki/Workspace/GraduationDesign/brain/trajectory_evaluator.py)
  - [brain/evidence_anchor.py](/Users/loki/Workspace/GraduationDesign/brain/evidence_anchor.py)
  - [brain/retriever.py](/Users/loki/Workspace/GraduationDesign/brain/retriever.py)
  - [brain/action_builder.py](/Users/loki/Workspace/GraduationDesign/brain/action_builder.py)
  - [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - [brain/stop_rules.py](/Users/loki/Workspace/GraduationDesign/brain/stop_rules.py)
  - [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)
  - [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)
  - 相关单测
- 具体改动：
  - `score_candidate_hypotheses_without_trajectories()` 新增 observed-evidence final evaluator：没有 rollout trajectory 时，根据真实 anchor、minimum family coverage、low-cost profile、负证据与 scope mismatch 生成 verifier-like metadata
  - `no_stop_gate` 现在识别 `observed_evidence_final_evaluator`：接受时可 `final_answer_accepted`，拒绝时仍进入 verifier/repair 控制链
  - `trajectory_agent_verifier` 的 LLM 接受结果后新增 deterministic scope guard：如果 `scope_mismatch_score / generic_scope_penalty / missing_scope_facets` 表示证据粒度不足，会改成 `strong_alternative_not_ruled_out`
  - `EvidenceAnchorAnalyzer` 新增 `candidate_scope_facets / observed_scope_facets / missing_scope_facets / scope_specificity_score / generic_scope_penalty / scope_requirement_missing_score`，用于统一处理泛疾病、部位特异、IRIS 和播散型粒度
  - `GraphRetriever` 新增 `retrieve_scope_sibling_candidates()`，强 observed anchor 进入后按病原/作用域关键词召回同族 sibling 疾病，再交给 anchor/scope rerank 裁决
  - `ActionBuilder` 为低成本动作增加 `low_cost_discriminative_bonus` 与背景问题降权，`ConsultationBrain` 新增 low-cost explorer，从 top3 候选 R2 中主动选择可直接回答、非背景且有区分度的问题

### 验证

- 执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_trajectory_evaluator.py tests/test_stop_rules.py tests/test_action_builder.py tests/test_evidence_anchor.py tests/test_service_repair_flow.py -q
```

- 结果：
  - `63 passed`

## 四十六、2026-05-04：移除结构化 stop rule，保留 verifier + repair

### 本次目标

- 从诊断运行链路中删除结构化 stop rule，避免继续用 turn、trajectory、anchor、score 等额外阈值阻断问诊结束
- 保留 verifier 与 repair：verifier 接受则 completed，verifier 拒绝则继续生成 repair 控制信号和下一问
- 针对批量 replay 中偶发的 `APIConnectionError / Connection error`，增加单病例自动重试 1 次

### 本次实现

- 删除：
  - `brain/stop_rules.py`
  - `tests/test_stop_rules.py`
- 新增：
  - [brain/acceptance_controller.py](/Users/loki/Workspace/GraduationDesign/brain/acceptance_controller.py)
  - [tests/test_acceptance_controller.py](/Users/loki/Workspace/GraduationDesign/tests/test_acceptance_controller.py)
- 更新：
  - [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - [scripts/run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)
  - [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)
  - [configs/frontend.yaml](/Users/loki/Workspace/GraduationDesign/configs/frontend.yaml)
  - [frontend/config_loader.py](/Users/loki/Workspace/GraduationDesign/frontend/config_loader.py)
  - [README.md](/Users/loki/Workspace/GraduationDesign/README.md)
  - [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)
  - [frontend/README.md](/Users/loki/Workspace/GraduationDesign/frontend/README.md)
  - [simulator/README.md](/Users/loki/Workspace/GraduationDesign/simulator/README.md)

### 具体改动

- `ConsultationBrain` 不再 import / 实例化 / 调用 `StopRuleEngine`
- `VerifierAcceptanceController` 只消费两类 verifier-like 结果：
  - `llm_verifier`
  - `observed_evidence_final_evaluator`
- 当 `verifier_should_accept=true` 时返回 `final_answer_accepted`
- 当 verifier 明确拒绝时返回 `verifier_rejected_stop`，并保留 `repair_reject_reason / path_control_reason` 给 repair 使用
- `configs/brain.yaml` 移除 `stop:` 段；前端配置不再设置 `BRAIN_ACCEPTANCE_PROFILE`
- `run_batch_replay.py` 新增 `--api-error-retries`，默认 `1`；单病例返回 failed 或抛异常时，只要错误文本包含 `APIConnectionError / Connection error`，就自动重试

### 验证

- 执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_acceptance_controller.py tests/test_service_stop_flow.py tests/test_service_repair_flow.py tests/test_exam_context_flow.py tests/test_run_batch_replay.py tests/test_service_config.py tests/test_frontend_config_loader.py -q
conda run -n GraduationDesign python -m pytest -q
```

- 结果：
  - `54 passed`
  - 全量 `222 passed`

## 四十七、2026-05-04：batch replay 复用 worker 级 LLM client，并为 APIConnectionError 增加冷却

### 本次目标

- 降低标准 `batch replay` 在 `case_concurrency > 1` 时的 LLM 建连抖动，减少同一时间段大量新建 HTTP client 带来的连接雪崩
- 让 `APIConnectionError / Connection error` 在整例重试前先退避冷却，而不是立刻再次打到同一 provider

### 本次实现

- 更新：
  - [brain/llm_client.py](/Users/loki/Workspace/GraduationDesign/brain/llm_client.py)
  - [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - [scripts/run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)
  - [tests/test_run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/tests/test_run_batch_replay.py)
  - [README.md](/Users/loki/Workspace/GraduationDesign/README.md)
  - [simulator/README.md](/Users/loki/Workspace/GraduationDesign/simulator/README.md)

### 具体改动

- `run_batch_replay.py` 新增线程本地 worker runtime：
  - 同一 worker 会复用一套 `LlmClient`
  - 每个 case 仍然新建独立 `brain`，因此不会共享 `StateTracker / SessionState`
  - `VirtualPatientAgent` 与 `brain` 在同一 worker / 同一 case 内共用这套 client，减少 `brain + patient` 双 client 并发建连
- `build_default_brain_from_env()` / `build_default_brain()` 现在支持注入外部 `llm_client`，便于 batch worker 传入共享 client
- `LlmClient` 新增 `close()`，方便 batch worker 结束时显式释放底层 HTTP 客户端
- batch 外层的 `APIConnectionError / Connection error` 整例重试现在会先执行冷却退避：
  - 冷却基线读取 `BATCH_API_ERROR_COOLDOWN_SECONDS`，默认 `2.0`
  - 冷却按重试次数指数退避，并限制最大 `12s`
  - 累计冷却时长会写入 `timing.batch_retry_cooldown_seconds_total`；失败病例 `error` 中也会带同名字段

### 验证

- 执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_run_batch_replay.py -q
conda run -n GraduationDesign python -m py_compile scripts/run_batch_replay.py brain/service.py brain/llm_client.py
```

- 结果：
  - `17 passed`
  - `py_compile` 通过

## 四十八、2026-05-05：第三批落地 rollout 防塌缩、final score 重平衡与 scope cluster rerank 前移

### 本次目标

- 落地执行清单第三批 `P4 + P5 + P7`
- 让同一个 child action 不再只复制单条最乐观 rollout
- 降低“单答案且低真实锚点”时 consistency/diversity 对 top1 的误导
- 把 scope / granularity 信息从 acceptance guard 前移到 A2 rerank 与 final score

### 本次实现

- 更新：
  - [brain/simulation_engine.py](/Users/loki/Workspace/GraduationDesign/brain/simulation_engine.py)
  - [brain/trajectory_evaluator.py](/Users/loki/Workspace/GraduationDesign/brain/trajectory_evaluator.py)
  - [brain/evidence_anchor.py](/Users/loki/Workspace/GraduationDesign/brain/evidence_anchor.py)
  - [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)
  - [docs/diagnosis_algorithm_batch_execution_checklist.md](/Users/loki/Workspace/GraduationDesign/docs/diagnosis_algorithm_batch_execution_checklist.md)
  - [tests/test_simulation_engine.py](/Users/loki/Workspace/GraduationDesign/tests/test_simulation_engine.py)
  - [tests/test_trajectory_evaluator.py](/Users/loki/Workspace/GraduationDesign/tests/test_trajectory_evaluator.py)
  - [tests/test_evidence_anchor.py](/Users/loki/Workspace/GraduationDesign/tests/test_evidence_anchor.py)
  - [tests/test_service_config.py](/Users/loki/Workspace/GraduationDesign/tests/test_service_config.py)
  - [README.md](/Users/loki/Workspace/GraduationDesign/README.md)
  - [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)

### 具体改动

- `SimulationEngine` 新增第三批 rollout 控制开关：
  - `enable_multi_branch_rollout`
  - `branch_budget_per_action`
  - `enable_anti_collapse_penalty`
- `run_reasoning_search()` 现在会对同一个 child action 生成多条 branch-seeded trajectory，而不是固定只保留一条最乐观路径
- `search_metadata` 新增：
  - `rollout_trajectory_count`
  - `answer_group_count`
  - `single_answer_group`
  - `rollout_branch_seed_counts`
- `TrajectoryEvaluator` 新增第三批 final score 控制：
  - 动态 group weights
  - `single_answer_group_score_cap`
  - `candidate_rank_prior`
  - `final_scope_penalty`
- `EvidenceAnchorAnalyzer` 新增 `scope_cluster_bonus / scope_cluster_level`，并把它前移到 A2 observed-anchor rerank
- `build_default_brain()` 与 `configs/brain.yaml` 新增第三批配置映射：
  - `rollout_control.*`
  - `path_evaluation.enable_dynamic_group_weights`
  - `path_evaluation.enable_single_answer_group_cap`
  - `path_evaluation.low_anchor_single_group_score_cap`
  - `path_evaluation.enable_scope_penalty_in_final_score`
  - `a2.enable_scope_cluster_rerank`
  - `a2.scope_cluster_exact_bonus`
  - `a2.scope_cluster_generic_penalty`
- 执行清单补入第二批 replay 实测结果，为是否进入第三批提供了明确归因

### 验证

- 执行：

```bash
conda run -n GraduationDesign python -m pytest tests/test_simulation_engine.py tests/test_trajectory_evaluator.py tests/test_evidence_anchor.py tests/test_service_config.py -q
conda run -n GraduationDesign python -m pytest -q
python -m py_compile brain/simulation_engine.py brain/trajectory_evaluator.py brain/evidence_anchor.py brain/service.py
```

- 结果：
  - 聚焦测试 `31 passed`
  - 全量测试 `236 passed`
  - `py_compile` 通过

## 四十九、2026-05-05：记录第三批 replay 结果，并补充第三批后的 ablation 方案

### 本次目标

- 把第三批 `error_focus_smoke95` replay 的真实结果固定记录到执行清单中
- 根据第三批结果，补一组可直接执行的 ablation 方案，拆分 `P4 / P5 / P7` 的实际贡献

### 本次更新

- 更新：
  - [docs/diagnosis_algorithm_batch_execution_checklist.md](/Users/loki/Workspace/GraduationDesign/docs/diagnosis_algorithm_batch_execution_checklist.md)

### 记录的第三批 replay 结果

- 结果目录：
  - [error_focus_smoke95_qwen35_batch1_p4p5p7](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/error_focus_smoke95_qwen35_batch1_p4p5p7)
- 相比第二批：
  - `top1_final_answer_hit`: `13/95` -> `12/95`
  - `top3_hypothesis_hit`: `40/95` -> `37/95`
  - `hypothesis_hit`: `58/95` -> `59/95`
  - `average_revealed_slots`: `1.48` -> `1.45`
  - `wrong_accepted_count`: `13` -> `12`
  - `true_candidate_missing`: `31` -> `29`
  - `true_candidate_but_final_wrong`: `29` -> `31`

### 本次结论

- 第三批的内部行为是生效的：
  - `single_answer_group_rate` 已降到 `3.68%`
  - `rollout_branch_seed_counts` 已显示正反两类 seed 都在真实产物里稳定出现
- 但第三批没有带来主指标净提升：
  - `top1_final_answer_hit` 与 `top3_hypothesis_hit` 都小幅回退
- 当前更像是把一部分问题从“真病没进候选”转成了“真病进入同家族竞争，但最终排到错误 sibling / generic disease”
- 因此下一步不宜继续在 `P4 + P5 + P7` 全开状态上叠新逻辑，而应先做 ablation

### 新增的 ablation 方案

- `batch2 + P4 only`
  - 用于判断多分支 rollout / 防塌缩本身是否有正收益
- `batch2 + P4 + P5`
  - 用于判断 final score 重平衡是否主要改善 accepted 质量
- `batch2 + P7 only`
  - 用于判断当前 scope-aware rerank 是否是第三批回退的主要来源

### 下一步建议

- 建议按以下顺序执行：
  1. `batch2 + P4 only`
  2. `batch2 + P4 + P5`
  3. `batch2 + P7 only`

## 五十、2026-05-05：将默认 brain 配置切到 `batch2 + P4 only`

### 本次目标

- 为下一轮 ablation replay 直接切换默认配置
- 保留 `P4`，关闭 `P5` 与 `P7`

### 本次更新

- 更新：
  - [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)

### 具体改动

- 保留：
  - `rollout_control.enable_multi_branch_rollout = true`
  - `rollout_control.branch_budget_per_action = 2`
  - `rollout_control.enable_anti_collapse_penalty = true`
- 关闭：
  - `path_evaluation.enable_dynamic_group_weights = false`
  - `path_evaluation.enable_single_answer_group_cap = false`
  - `path_evaluation.enable_scope_penalty_in_final_score = false`
  - `a2.enable_scope_cluster_rerank = false`

### 说明

- 当前这份默认配置是为了直接运行 `batch2 + P4 only`
- 后续如果要继续跑：
  - `batch2 + P4 + P5`
  - `batch2 + P7 only`
  需要再按 ablation 方案切换对应开关

## 五十一、2026-05-05：记录 `batch2 + P4 only` 结果，并将默认配置切到 `batch2 + P4 + P5`

### 本次目标

- 记录 `P4 only` ablation replay 的真实结果
- 基于结果判断是否保留 `P4`
- 将默认配置切换到下一轮 `batch2 + P4 + P5`

### 本次更新

- 更新：
  - [docs/diagnosis_algorithm_batch_execution_checklist.md](/Users/loki/Workspace/GraduationDesign/docs/diagnosis_algorithm_batch_execution_checklist.md)
  - [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)

### 记录的 `P4 only` replay 结果

- 结果目录：
  - [error_focus_smoke95_qwen35_batch1_p4only](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/error_focus_smoke95_qwen35_batch1_p4only)
- 相比第二批：
  - `top1_final_answer_hit`: `13/95` -> `12/95`
  - `top3_hypothesis_hit`: `40/95` -> `39/95`
  - `hypothesis_hit`: `58/95` -> `60/95`
  - `completed`: `19` -> `23`
  - `true_candidate_missing`: `31` -> `28`
  - `true_candidate_but_final_wrong`: `29` -> `32`
  - `wrong_accepted_count`: `13` -> `16`

### 本次结论

- `P4` 本身是有效的：
  - 多分支 rollout 已稳定生效
  - 真病进入候选池的能力有所增强
- 但 `P4` 单独使用还不够：
  - 没有把 `top1_final_answer_hit` 拉起来
  - 还放大了 `true_candidate_but_final_wrong` 与 `wrong_accepted_count`
- 因此最合理的下一步是：
  - 保留 `P4`
  - 打开 `P5`
  - 继续关闭 `P7`

### 本次配置切换

- 当前默认配置已切到 `batch2 + P4 + P5`
- 具体为：
  - 保留 `rollout_control.*`
  - 打开 `path_evaluation.enable_dynamic_group_weights = true`
  - 打开 `path_evaluation.enable_single_answer_group_cap = true`
  - 继续关闭 `path_evaluation.enable_scope_penalty_in_final_score = false`
  - 继续关闭 `a2.enable_scope_cluster_rerank = false`

## 五十二、2026-05-05：修复问句模板错位，并将默认配置切到 `P4 + P5 + P7`

### 本次目标

- 修复 replay 中“检查模态提示和目标证据语义不匹配”的问句模板浪费 turn 问题
- 打开 `P7` 相关配置，便于继续观察 scope-aware rerank 的真实收益

### 本次更新

- 更新：
  - [brain/action_builder.py](/Users/loki/Workspace/GraduationDesign/brain/action_builder.py)
  - [tests/test_action_builder.py](/Users/loki/Workspace/GraduationDesign/tests/test_action_builder.py)
  - [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)
  - [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)

### 具体改动

- 为 `ActionBuilder` 增加 `question_type_hint` 纠偏：
  - 会按 `label + acquisition_mode` 强制归一成 `lab / imaging / pathogen / risk / detail / symptom`
  - 避免高成本化验、影像、病原节点被上游脏 metadata 误导成普通症状问法
- 重写通用检查模板分流：
  - `imaging` 不再一律问“胸片或 CT”，而是按眼底 / 头颅 / 胸部 / 腹盆腔分流
  - `pathogen` 不再一律问“痰检、核酸/PCR 或支气管镜取样”，而是按 HIV 抽血检查 / 脑脊液 / 皮损分泌物 / 呼吸道样本分流
  - `lab` 对 HIV 相关抽血检查与脑脊液类化验增加更贴切的默认问法
- 增加回归测试覆盖：
  - 眼底类影像不再落入胸片/胸部 CT 模板
  - HIV 相关目标不再落入痰检/支气管镜模板
  - `question_type_hint` 会按标签与获取方式自动纠偏
- 默认配置切换为：
  - 打开 `path_evaluation.enable_scope_penalty_in_final_score = true`
  - 打开 `a2.enable_scope_cluster_rerank = true`

### 预期收益

- 减少 8 轮预算里被明显错误问句直接浪费的 turn
- 提高 repair / verifier 推荐证据真正被患者回答命中的概率
- 为后续继续观察 `top1_final_answer_hit` 与 `scope` 相关错配问题提供更干净的输入

## 五十三、2026-05-05：刷新运行链路文档，移除旧 `stop rule` 口径

### 本次目标

- 把第二阶段运行链路文档更新到当前实现
- 明确当前系统已经从旧 `A1 / A2 / A3 / A4 + stop rule` 口径迁移到 `turn_interpreter -> A1 / A2 / A3 -> verifier-only acceptance -> repair`
- 避免后续复盘、答辩或论文写作继续引用过时的 stop-rule 描述

### 本次更新

- 更新：
  - [docs/brain_runtime_call_chain_guide.md](/Users/loki/Workspace/GraduationDesign/docs/brain_runtime_call_chain_guide.md)
  - [README.md](/Users/loki/Workspace/GraduationDesign/README.md)
  - [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)

### 具体改动

- 重写 `brain_runtime_call_chain_guide.md`，统一为当前实现口径：
  - `process_turn()` 以 `turn_interpreter` 为真正单轮入口
  - 先做 generic state merge，再做 pending-action-specific merge
  - `A2` 现在明确包括 `R1 + rescue candidate pools + observed anchor rerank`
  - `A3` 之后不再描述为旧 `A4`，而是 `trajectory aggregation + verifier + repair`
  - 明确写出 `MCTS/rollout` 仍是启发式搜索内核，但最终 `completed` 只由 verifier / observed final evaluator 接受信号触发
  - 解释 `StopDecision` 仍在，但旧结构化 `stop rule` 已从主链路移除
- README 中同步更新运行链路指南索引说明，避免目录页继续沿用旧 `A1 / A2 / A3 + anchor` 的简化口径
- `brain/README.md` 中同步去掉旧 `STOP` 路由主流程口径，改为当前 `pending action -> A1 / A2 / A3 -> verifier / repair` 描述

### 结果影响

- 后续查看运行链路时，不再把 `A4` 和旧 stop-rule 误认为仍在主流程内
- 更容易对齐当前代码中的 `repair / early exam rescue / low-cost explorer / verifier-only acceptance`
- 为答辩、论文描述和后续参数实验提供一致的流程叙述基线

## 五十四、2026-05-05：统一绪论术语口径并补强研究现状过渡

### 本次目标

- 修正绪论中除 `MCTS` 外的中英文混杂术语
- 收紧研究现状综述段落与本文工作的过渡关系
- 将工程化表述改写为更适合论文正文的表达

### 本次更新

- 更新：
  - [绪论.docx](/Users/loki/Workspace/GraduationDesign/绪论.docx)

### 具体改动

- 将 `LLMs` 改为“大语言模型”
- 将 `1.2.4` 小节标题统一为“基于 MCTS 的测试时搜索医疗推理研究现状”
- 将 `verify、repair` 改为“验证、修复”
- 将 `medical-inquiry-system` 改写为“智能问诊原型系统”
- 补强 `1.2.1` 与 `1.2.4` 小节结尾的综合评述，使研究空缺与本文切入点衔接更清晰

### 结果影响

- 绪论术语口径与正文方法章节更一致
- 研究现状部分由“文献罗列”进一步收束为“问题归纳 + 本文切入”
- `1.3` 中“本文主要工作”的学术表达更自然

## 五十五、2026-05-05：清理论文正文重复段落并复查近邻重复

### 本次目标

- 删除第 `3.4.3` 节中残留的旧版病人代理定义段落
- 复查主论文文档中是否还存在明显的近邻重复段落

### 本次更新

- 更新：
  - [面向 HIV_AIDS 场景的多轮问诊系统设计.docx](/Users/loki/Workspace/GraduationDesign/面向%20HIV_AIDS%20场景的多轮问诊系统设计.docx)

### 具体改动

- 删除 `3.4.3 病人代理构建` 开头旧版定义段落，保留“大语言模型驱动的受约束病人代理”版本
- 对主论文文档执行近邻高相似度段落扫描，重点检查相邻 8 段内的重复定义与重复说明

### 结果影响

- `3.4.3` 小节开头不再重复定义同一概念
- 当前主论文文档未再发现同级别的明显近邻重复段落

## 五十六、2026-05-05：整理第 3.2 节关系类型表格样式

### 本次目标

- 清理第 `3.2` 节关系类型表格中重复列带来的视觉冗余
- 将关系名改为“中文主名 + 英文原名小写”的论文展示形式

### 本次更新

- 更新：
  - [面向 HIV_AIDS 场景的多轮问诊系统设计.docx](/Users/loki/Workspace/GraduationDesign/面向%20HIV_AIDS%20场景的多轮问诊系统设计.docx)

### 具体改动

- 删除关系类型表格中与首列重复的第 `5` 列
- 将首列关系名统一改写为：
  - `诊断依据（diagnosed_by）`
  - `实验室发现（has_lab_finding）`
  - `影像学发现（has_imaging_finding）`
  - `病原体证据（has_pathogen）`
  - `症状表现（manifests_as）`
  - `细节追问（requires_detail）`
  - `风险因素（risk_factor_for）`
  - `适用人群（applies_to）`
  - `并发关系（complicated_by）`

### 结果影响

- 表格由 `5` 列精简为 `4` 列
- 关系类型展示更符合论文正文阅读习惯，也避免了中英文列重复造成的冗余

## 五十七、2026-05-05：调整“相关技术与理论基础”文稿口径与衔接

### 本次目标

- 收紧第 2 章理论基础文稿中的中英文术语使用
- 加强 MCTS 基础原理与后续问诊系统设计之间的过渡
- 降低第 2 章与第 3 章方法设计之间的内容重叠

### 本次更新

- 更新：
  - [相关技术与理论基础.docx](/Users/loki/Workspace/GraduationDesign/相关技术与理论基础.docx)

### 具体改动

- 将 `Selection / Expansion / Simulation / Backpropagation` 统一改为“选择 / 扩展 / 模拟 / 回传”
- 将 `benchmark / CoT / RAG` 调整为“中文 + 括号英文”的写法
- 收紧 `2.1.3` 中关于测试时推理的表述，避免与 `2.4` 的 MCTS 展开重复
- 调整 `2.2.2`，保留实体、关系、属性的基本概念说明，并将具体本体设计留到第 `3` 章展开
- 在 `2.4.1` 结尾补入“树节点对应局部问诊状态、边对应候选问诊动作”的过渡句
- 将 `2.4.2` 中关于相关方法优势的表述改为更稳妥的学术口吻

### 结果影响

- 第 2 章术语口径与正文方法章节更一致
- 理论基础与方法设计之间的边界更清晰
- MCTS 理论部分与后文问诊系统建模的衔接更自然

### 补充修订：2026-05-05 清理“相关技术与理论基础”重复段并恢复小节标题

### 本次更新

- 更新：
  - [相关技术与理论基础.docx](/Users/loki/Workspace/GraduationDesign/相关技术与理论基础.docx)

### 具体改动

- 删除 `2.1.3` 中“除提示工程和结构化抽取外……”的突兀过渡段，避免与后文测试时推理展开重复
- 删除 `2.2.2` 中重复出现的“知识图谱的核心组成包括实体、关系和属性”段落
- 删除 `2.4.1` 中保留英文步骤名的旧版本段落，仅保留“选择 / 扩展 / 模拟 / 回传”的中文版本
- 删除 `2.4.2` 中旧的较强结论表述，仅保留“相关医学基准测试（benchmark）”“思维链（CoT）”“检索增强生成（RAG）”版本
- 恢复 `2.2.3 图数据库 Neo4j 与图检索方法`、`2.4.2 Med-MCTS 及相关方法在医疗推理中的应用`、`2.5 本章小结` 三个小节标题

### 验证结果

- 逐段复查后，未再发现相邻重复段落
- 第 `2` 章的小节结构已经恢复完整，章节衔接正常
