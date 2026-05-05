# 诊断算法三批落地执行清单（2026-05-04）

本文件给当前诊断算法的下一轮改造提供一份可直接执行的三批落地清单。

本文只聚焦“算法链路如何按顺序改、每一批改什么、改完看什么指标”，不替代更长期的阶段路线文档：

- 总体阶段路线见 [phase2_execution_checklist.md](/Users/loki/Workspace/GraduationDesign/docs/phase2_execution_checklist.md)
- 过程复盘与变更记录见 [phase2_changelog.md](/Users/loki/Workspace/GraduationDesign/docs/phase2_changelog.md)
- 当前系统差异分析与启发式来源见 [med_mcts_vs_current_system.md](/Users/loki/Workspace/GraduationDesign/docs/med_mcts_vs_current_system.md)

## 一、当前基线指标

当前算法基线统一以这次错误集重跑目录为准：

- 输出目录：
  - [error_focus_smoke95_qwen35_no_stop_gate](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/error_focus_smoke95_qwen35_no_stop_gate)
- 汇总文件：
  - [benchmark_summary.json](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/error_focus_smoke95_qwen35_no_stop_gate/benchmark_summary.json)
  - [non_completed_cases.json](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/error_focus_smoke95_qwen35_no_stop_gate/non_completed_cases.json)

当前最需要盯住的指标是：

- `top3_hypothesis_hit = 24/95 = 25.3%`
  - 这里的 `top3` 指 `top3_hypothesis_hit`，也就是“真实诊断是否进入 top3 候选”，不是 `top3 final answer hit`
- `top1_final_answer_hit = 10/95 = 10.5%`
- `average_revealed_slots = 0.79`
- `failed::llm_stage_failed = 29`
- `max_turn_reached::true_candidate_missing = 28`
- `max_turn_reached::true_candidate_but_final_wrong = 19`

当前指标的主要含义是：

- `top3_hypothesis_hit` 低，说明真实疾病经常连候选池都没有稳定进入
- `top1_final_answer_hit` 比 `top3` 更低，说明即使真实疾病进入候选，最终排序、rollout 或粒度控制仍会把答案带偏
- `average_revealed_slots` 过低，说明前几轮问题大多没有换来真正有区分度的真实证据

## 二、总执行原则

### 1. 代码落地顺序

必须严格按三批推进，不建议把三批算法开关一次性全部打开：

1. 第一批：`P1 + P2`
2. 第二批：`P3 + P6`
3. 第三批：`P4 + P5 + P7`

### 2. 每一批都要先做开关化

每批开始前，先把行为改动做成 `configs/brain.yaml` 可控开关，避免以下问题：

- 无法做 ablation
- 一次混入多个变量，replay 后无法归因
- 前一批刚稳定，后一批改动又把问题带回去

建议统一把新开关集中到以下配置段：

- `a2:`
- `a3:`
- `path_evaluation:`
- `repair:`
- `fallback:`
- 如需新增，可补一个 `rollout_control:` 或 `candidate_feedback:`

### 3. 每一批的固定交付物

每批完成后，至少交付以下内容：

- 代码改动
- 对应单元测试
- 一次 focused replay
- 一次 `error_focus_smoke95` replay
- 一段结果复盘，说明：
  - 本批实际打开了哪些开关
  - 主要改变了哪些问题
  - 哪些指标提升了
  - 哪些副作用出现了

### 4. 每一批都要记录的核心指标

每批 replay 后，统一记录下列字段：

- `top3_hypothesis_hit_count / rate`
- `top1_final_answer_hit_count / rate`
- `average_revealed_slots`
- `failed::llm_stage_failed`
- `true_candidate_missing`
- `true_candidate_but_final_wrong`
- `top_exact_correct_but_rejected_count`
- `top_family_correct_but_rejected_count`

建议额外新增 4 个研发期内部指标：

- `repair_action_override_rate`
  - repair 选出的动作，最后被 low-cost explorer 或 fallback 覆盖的比例
- `early_exam_context_trigger_rate`
  - 前 2 轮内触发 `collect_general_exam_context / collect_exam_context` 的病例比例
- `single_answer_group_rate`
  - `answer_group_scores` 只有 1 个答案组的病例比例
- `multi_hypothesis_feedback_hit_rate`
  - 一次真实证据反馈命中多个候选而不是只命中当前 hypothesis 的比例

补充说明：

- 第一批 replay 产物里，`replay_results.jsonl` 的每轮 `turn` 过去没有真正落出 `search_report / search_metadata`
- 所以像 `early_exam_context_trigger_rate`、`repair_action_override_rate` 这类依赖 `search_metadata` 的内部指标，当时还不能直接离线统计
- 这个问题不阻塞第二批实现，但会阻塞后续归因分析
- 本轮代码已经补上：
  - `ReplayTurn.search_report`
  - `ReplayTurn.search_metadata`
  - `turn_evidence_feedback / multi_hypothesis_feedback_hit_rate` 所需的 turn 级 metadata
- 因此从第二批 replay 开始，上述内部指标应可以直接从回放产物离线统计

## 三、第一批：P1 + P2

### 本批目标

- 先修链路正确性
- 纠正前几轮问法偏差
- 让 verifier / repair 真正控制下一问
- 更早把检查驱动病例拉回“先问检查是否做过、再问具体结果”的轨道

### 本批预计主要改善的指标

- 优先提升：`top3_hypothesis_hit`
- 同时改善：`average_revealed_slots`
- 风险最低，适合作为第一批落地

### 本批要改的核心问题

1. verifier 已经给出 `repair` 动作，但随后又被 low-cost explorer 覆盖  
2. 前几轮过度偏好低成本泛症状，导致 exam-driven / pathogen-driven / imaging-driven 病例问不到关键证据

### 本批修改点

#### P1：repair 不再被 explorer 覆盖

- 重点文件：
  - [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
- 重点函数：
  - `process_turn()`
  - `_choose_low_cost_explorer_action()`
  - `_filter_selected_action_for_repeat()`

- 具体改法：
  - 增加动作来源优先级：
    - `repair_selected_action`
    - `exam_context_followup_action`
    - `default_search_action`
    - `low_cost_explorer_action`
    - `cold_start_action`
  - 当 `repair_context` 存在且 `repair_selected_action` 仍可问时，跳过 low-cost explorer
  - 只有在以下情况才允许 explorer 接管：
    - `repair_selected_action is None`
    - repair 动作被重复过滤
    - repair 动作已因检查状态不可问
  - 将“repair 被覆盖”的原因写入 `search_result.metadata`

- 建议新增配置：
  - `repair.protect_repair_action_from_low_cost_explorer: true`
  - `repair.allow_low_cost_explorer_after_repair_if_unaskable_only: true`

#### P2：早期 exam-context rescue

- 重点文件：
  - [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - [brain/action_builder.py](/Users/loki/Workspace/GraduationDesign/brain/action_builder.py)
- 重点函数：
  - `_choose_low_cost_explorer_action()`
  - `_should_use_low_cost_explorer()`
  - `_choose_cold_start_probe_action()`
  - `build_verification_actions()`
  - `_accumulate_exam_context_action()`

- 具体改法：
  - 新增 `exam_first_needed` 判定：
    - `turn_index <= 2 or 3`
    - `revealed_present_clear_count < 2`
    - top hypothesis 的 `anchor_tier` 仍是 `background_supported / phenotype_supported / speculative`
    - top hypothesis 的 R2 高价值证据里 `lab / imaging / pathogen` 占比高
  - 满足条件时，优先选择：
    - `collect_general_exam_context`
    - 已知 general 做过后，再优先走具体 `lab / imaging / pathogen` 入口
  - 若患者回答“做过”，下一轮必须优先生成 exam-result follow-up，不允许立刻退回泛症状
  - 当 replay 已进入 repair 且推荐证据主要是检查结果时，也允许直接拉起 exam-context rescue

- 建议新增配置：
  - `a3.enable_early_exam_context_rescue: true`
  - `a3.early_exam_context_turn_limit: 2`
  - `a3.early_exam_context_revealed_count_threshold: 2`
  - `a3.exam_context_rescue_high_cost_role_threshold: 0.45`

### 本批可执行开发任务

- [ ] 在 `service.py` 中补动作优先级仲裁，不再让 repair 动作被 explorer 随意覆盖
- [ ] 新增 `repair_action_override_rate` 观测字段
- [ ] 在 `service.py` 中新增 `exam_first_needed` 判定与对应 rescue 入口
- [ ] 在 `action_builder.py` 中补充对 exam-context 动作优先级的注释与 metadata
- [ ] 为 exam-context rescue 增加单测：
  - repair 存在时 explorer 不覆盖
  - exam-driven 病例前两轮优先问检查
  - general exam context 已确认“做过”后，下一轮优先追具体结果
- [ ] 跑 focused replay，优先选：
  - 检查驱动病例
  - 病原/影像驱动病例
  - 之前 repair 推荐高成本证据却被低成本泛症状覆盖的病例

### 本批测试与回放建议

- 单测重点：
  - `tests/test_service_repair_flow.py`
  - `tests/test_exam_context_flow.py`
  - 新增 `tests/test_service_low_cost_explorer_priority.py`
- replay 建议：
  - 先跑一组 focused error cases
  - 再跑一次 `error_focus_smoke95`

### 本批验收标准

- `repair_action_override_rate` 显著下降，目标先压到 `<5%`
- `early_exam_context_trigger_rate` 明显上升
- `average_revealed_slots` 相比当前 `0.79` 有明显提升
- `top3_hypothesis_hit` 优先抬升
- 若 `top1_final_answer_hit` 暂时只小幅提升，也可以接受

### 本批预计改善什么指标

- 首要改善：
  - `top3_hypothesis_hit`
- 次要改善：
  - `average_revealed_slots`
  - 一部分 `true_candidate_missing`

### 第一批实际 replay 结果（2026-05-04）

第一批完成后，已对以下目录完成 replay：

- 基线：
  - [error_focus_smoke95_qwen35_no_stop_gate](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/error_focus_smoke95_qwen35_no_stop_gate)
- 第一批结果：
  - [error_focus_smoke95_qwen35_batch1_p1p2](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/error_focus_smoke95_qwen35_batch1_p1p2)

核心指标变化：

- `top3_hypothesis_hit`: `24/95 = 25.3%` -> `37/95 = 38.9%`
- `top1_final_answer_hit`: `10/95 = 10.5%` -> `12/95 = 12.6%`
- `hypothesis_hit`: `34/95 = 35.8%` -> `58/95 = 61.1%`
- `average_revealed_slots`: `0.79` -> `1.42`
- `completed`: `7` -> `21`
- `failed::llm_stage_failed`: `29` -> `0`

同时暴露出的新瓶颈：

- `true_candidate_missing`: `28` -> `30`
- `true_candidate_but_final_wrong`: `19` -> `29`
- `wrong_accepted_count`: `4` -> `15`

这一轮的结论是：

- 第一批目标基本达成
- 问法和可揭示证据数量已经明显改善
- APIConnectionError 失败问题已经被 worker 级 client 复用与 batch retry/cooldown 兜住
- 但系统瓶颈已经从“前几轮问法偏差”转到“候选池动态”和“repair 仍然围绕错答案自修补”
- 因此下一步应该进入第二批 `P3 + P6`

## 四、第二批：P3 + P6

### 本批目标

- 修候选池动态
- 让一次真实回答真正影响多个相关候选
- 避免系统围着“已经缺了两轮关键支持的错答案”继续自我修补

### 本批预计主要改善的指标

- 优先降低：`true_candidate_missing`
- 同时改善：一部分 `true_candidate_but_final_wrong`

### 本批要改的核心问题

1. 真实证据反馈目前几乎只作用于当前 `action.hypothesis_id`  
2. verifier 给出 `missing_key_support` 后，系统常继续围绕当前错答案补证据，而不是切换到竞争诊断 repair

### 本批修改点

#### P3：证据反馈不再只作用当前 hypothesis

- 重点文件：
  - [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - [brain/simulation_engine.py](/Users/loki/Workspace/GraduationDesign/brain/simulation_engine.py)
  - [brain/hypothesis_manager.py](/Users/loki/Workspace/GraduationDesign/brain/hypothesis_manager.py)
- 重点函数：
  - `_apply_hypothesis_feedback()`
  - `_apply_generic_exam_context_evidence_feedback()`
  - `SimulationEngine._apply_rollout_state_update()`
  - `HypothesisManager.apply_evidence_feedback()`

- 具体改法：
  - 新增“证据命中的候选集合”计算层
  - 不再只把 `related_ids` 设为当前 hypothesis
  - 对每条真实 evidence，根据候选的 `evidence_payloads / evidence_node_ids / relation_types` 找出所有相关候选
  - 对所有命中候选同步做增减分：
    - `exact_scope` 命中加分最大
    - `family_scope / phenotype` 命中加分较弱
    - `background` 只做极轻支持
  - 对明确阴性证据：
    - 强依赖该证据的候选同步降分
    - 泛症状降分轻
    - `HAS_PATHOGEN / DIAGNOSED_BY / definition` 相关候选降分重
  - rollout 内的模拟反馈也要采用同样的多候选更新逻辑，避免模拟路径局部自嗨

- 建议新增配置：
  - `candidate_feedback.enable_multi_hypothesis_feedback: true`
  - `candidate_feedback.use_scope_weighted_feedback: true`
  - `candidate_feedback.max_related_hypotheses_per_evidence: 5`

#### P6：`missing_key_support` 升级为 competition repair

- 重点文件：
  - [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - [brain/hypothesis_manager.py](/Users/loki/Workspace/GraduationDesign/brain/hypothesis_manager.py)
- 重点函数：
  - `_build_verifier_repair_context()`
  - `_select_current_repair_hypothesis()`
  - `_select_repair_hypotheses()`
  - `_apply_verifier_repair_strategy()`

- 具体改法：
  - 为 `missing_key_support` 增加升级规则：
    - 当前 top1 `observed_anchor_score == 0`
    - 或同一答案连续 `>=2` 轮被 `missing_key_support` 拒停
    - 或 verifier 推荐证据大多是高成本，且 alternative 已有更强 observed anchor
  - 满足条件时，将 repair 模式从“继续补当前答案”升级为“切入 hypothesis competition”
  - repair 应优先选择：
    - 已有 stronger observed anchor 的 alternative
    - verifier 明确点名的 alternative
    - 同 scope / 同病原的 sibling competitor

- 建议新增配置：
  - `repair.enable_missing_key_support_competition_escalation: true`
  - `repair.missing_key_support_retry_threshold: 2`
  - `repair.zero_anchor_current_answer_force_competition: true`

### 本批可执行开发任务

- [ ] 在 `service.py` 中新增 evidence fan-out 相关 helper
- [ ] 将真实反馈与 rollout 反馈统一切到“多候选联动更新”
- [ ] 为 `missing_key_support` 增加升级到 competition repair 的逻辑
- [ ] 在 `repair_feedback_counts` 上保留逐答案、逐原因的连续拒停计数
- [ ] 为以下场景补单测：
  - 一个阴性证据同时压低多个竞争候选
  - 同一错误答案连续 2 轮 `missing_key_support` 后切到 competition repair
  - 当前答案零真实锚点、alternative 已有 anchor 时直接切 repair 目标

### 本批测试与回放建议

- 单测重点：
  - `tests/test_service_repair_flow.py`
  - `tests/test_hypothesis_manager.py`
  - `tests/test_simulation_engine.py`
- replay 建议：
  - 优先重跑 `true_candidate_missing`
  - 再看 `true_candidate_but_final_wrong`

### 本批验收标准

- `true_candidate_missing` 明显下降
- 候选 top3 的真病进入率上升
- `missing_key_support` 下的“同一错答案连续自修补”现象明显减少

### 本批预计改善什么指标

- 首要改善：
  - `true_candidate_missing`
  - `top3_hypothesis_hit`
- 次要改善：
  - 一部分 `true_candidate_but_final_wrong`

### 第二批实际 replay 结果（2026-05-05）

第二批完成后，已对以下目录完成 replay：

- 第一批结果：
  - [error_focus_smoke95_qwen35_batch1_p1p2](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/error_focus_smoke95_qwen35_batch1_p1p2)
- 第二批结果：
  - [error_focus_smoke95_qwen35_batch1_p3p6](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/error_focus_smoke95_qwen35_batch1_p3p6)

核心指标变化：

- `top3_hypothesis_hit`: `37/95 = 38.9%` -> `40/95 = 42.1%`
- `top1_final_answer_hit`: `12/95 = 12.6%` -> `13/95 = 13.7%`
- `average_revealed_slots`: `1.42` -> `1.48`
- `completed`: `21` -> `19`
- `wrong_accepted_count`: `15` -> `13`
- `true_candidate_missing`: `30` -> `31`
- `true_candidate_but_final_wrong`: `29` -> `29`

第二批新增可离线统计的内部观测：

- `turn_count = 690`
- `turns_with_search_metadata = 687`
- `turns_with_feedback = 538`
- `feedback_total = 645`
- `feedback_multi = 348`
- `multi_hypothesis_feedback_hit_rate = 53.95%`
- `repair_override_guarded = 442`
- `early_exam_triggered_turns = 47`

这一轮的结论是：

- 第二批对 `top3_hypothesis_hit` 仍然有增益，说明“真实证据同时作用多个候选”是有效的
- 但 `top1_final_answer_hit` 只从 `12` 提到 `13`，改善非常有限
- `true_candidate_missing` 没有下降，`true_candidate_but_final_wrong` 也没有改善，说明系统主瓶颈已经转到：
  - rollout 塌缩
  - final score 偏向“错误但自洽”的单答案
  - scope / granularity 信息进入最终排序仍然不够早
- 因此可以进入第三批 `P4 + P5 + P7`

## 五、第三批：P4 + P5 + P7

### 本批目标

- 修 rollout 塌缩
- 修最终排序偏置
- 把 scope / granularity 控制从 acceptance guard 前移到 rerank 阶段

### 本批预计主要改善的指标

- 优先提升：`top1_final_answer_hit`
- 同时减少：
  - family drift
  - generic drift
  - 部位漂移
  - IRIS / 泛感染漂移

### 本批要改的核心问题

1. rollout 当前容易整齐塌缩到一个错误答案  
2. final score 过度奖励 consistency / diversity，导致“错误但自洽”的答案吃掉 top1  
3. scope / granularity 主要在 acceptance guard 才发力，前面的候选排序还不够早地把泛疾病压下去

### 本批修改点

#### P4：多分支 rollout / 防塌缩

- 重点文件：
  - [brain/simulation_engine.py](/Users/loki/Workspace/GraduationDesign/brain/simulation_engine.py)
  - [brain/router.py](/Users/loki/Workspace/GraduationDesign/brain/router.py)
- 重点函数：
  - `rollout_from_tree_node()`
  - `_build_branch_payloads()`
  - `_advance_hypothesis_after_route()`

- 具体改法：
  - 不再每层只取单个 `weighted_reward` 最大分支
  - 至少保留：
    - `positive`
    - `negative / doubtful`
    两类分支进入不同 rollout
  - 或采用“受控随机 + branch budget”方式，保证同一 action 不会 8 条 rollout 全部复制同一条乐观路径
  - 当某个答案在 rollout 中垄断，但真实 `observed_anchor_score` 很低时，引入 `anti_collapse_penalty`
  - 对“明确阴性导致切 A2”的分支提高保留概率，避免竞争诊断完全消失

- 建议新增配置：
  - `rollout_control.enable_multi_branch_rollout: true`
  - `rollout_control.branch_budget_per_action: 2`
  - `rollout_control.enable_anti_collapse_penalty: true`

#### P5：final score 重平衡

- 重点文件：
  - [brain/trajectory_evaluator.py](/Users/loki/Workspace/GraduationDesign/brain/trajectory_evaluator.py)
- 重点函数：
  - `score_groups()`
  - `score_candidate_hypotheses_without_trajectories()`
  - `_anchor_alignment_bonus()`
  - `_simulated_key_evidence_penalty()`

- 具体改法：
  - 将 `consistency / diversity / agent_evaluation` 改为动态权重，而不是固定 `0.3 / 0.4 / 0.3`
  - 当 `answer_group_count == 1` 且 `observed_anchor_score` 低时：
    - 降低 consistency 权重
    - 降低 diversity 权重
    - 提高 agent / anchor / observed evidence 权重
  - 增加硬约束：
    - 无真实 disease-specific / definition anchor 的单答案塌缩，不允许仅靠 rollout 自洽拿 top1
  - 将 A2 当前候选顺位作为轻量先验并入 final score，防止“候选第 5 名因为 rollout 自证突然登顶”

- 建议新增配置：
  - `path_evaluation.enable_dynamic_group_weights: true`
  - `path_evaluation.enable_single_answer_group_cap: true`
  - `path_evaluation.low_anchor_single_group_score_cap: 0.62`

#### P7：scope-aware rerank 前移

- 重点文件：
  - [brain/evidence_anchor.py](/Users/loki/Workspace/GraduationDesign/brain/evidence_anchor.py)
  - [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - [brain/trajectory_evaluator.py](/Users/loki/Workspace/GraduationDesign/brain/trajectory_evaluator.py)
- 重点函数：
  - `EvidenceAnchorAnalyzer.rerank_hypotheses()`
  - `_apply_observed_anchor_rerank_to_scores()`
  - `_anchor_profile_for_answer()`

- 具体改法：
  - 对同病原、同家族候选增加 scope cluster 二次排序：
    - `exact_scope`
    - `family_scope`
    - `generic`
  - 将 opening、已确认阳性证据、检查结果里的 scope facet 更早写入 A2 / rerank，而不是等 verifier 才发现 `missing_scope_facets`
  - 对以下类型显式加大惩罚：
    - 泛病名压过部位特异病
    - 泛感染压过 IRIS
    - 同病原 generic disease 压过播散型 / CNS / 肺部 / GI 具体诊断
  - 将 `scope_mismatch_score / generic_scope_penalty / missing_scope_facets` 更早写入候选排序与 final score

- 建议新增配置：
  - `a2.enable_scope_cluster_rerank: true`
  - `a2.scope_cluster_exact_bonus: 0.35`
  - `a2.scope_cluster_generic_penalty: 0.28`
  - `path_evaluation.enable_scope_penalty_in_final_score: true`

### 本批可执行开发任务

- [ ] 将 rollout 从单分支贪心切到多分支或受控采样
- [ ] 给单答案塌缩增加惩罚与 debug 观测字段
- [ ] 在 `trajectory_evaluator.py` 中增加动态 final score 权重
- [ ] 把 scope-aware 惩罚提前接入 A2 / rerank / final score
- [ ] 为以下场景补单测：
  - 单答案塌缩但零真实锚点时不能轻易 top1
  - 同病原 generic disease 不应压过 exact scope disease
  - IRIS / 播散型 / 部位特异病在有 scope facet 时应被拉起

### 本批测试与回放建议

- 单测重点：
  - `tests/test_simulation_engine.py`
  - `tests/test_trajectory_evaluator.py`
  - `tests/test_evidence_anchor.py`
- replay 建议：
  - 优先重跑 `true_candidate_but_final_wrong`
  - 再看 `top_family_correct_but_rejected`
  - 最后跑完整 `error_focus_smoke95`

### 本批验收标准

- `top1_final_answer_hit` 明显提升
- `single_answer_group_rate` 显著下降
- scope / family / generic 漂移样例数量明显减少

### 本批预计改善什么指标

- 首要改善：
  - `top1_final_answer_hit`
- 次要改善：
  - `true_candidate_but_final_wrong`
  - `top_family_correct_but_rejected`

### 第三批实际 replay 结果（2026-05-05）

第三批完成后，已对以下目录完成 replay：

- 第二批结果：
  - [error_focus_smoke95_qwen35_batch1_p3p6](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/error_focus_smoke95_qwen35_batch1_p3p6)
- 第三批结果：
  - [error_focus_smoke95_qwen35_batch1_p4p5p7](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/error_focus_smoke95_qwen35_batch1_p4p5p7)

核心指标变化：

- `top1_final_answer_hit`: `13/95 = 13.7%` -> `12/95 = 12.6%`
- `top3_hypothesis_hit`: `40/95 = 42.1%` -> `37/95 = 38.9%`
- `hypothesis_hit`: `58/95 = 61.1%` -> `59/95 = 62.1%`
- `average_revealed_slots`: `1.48` -> `1.45`
- `completed`: `19` -> `19`
- `wrong_accepted_count`: `13` -> `12`
- `accepted_exact_hit_count`: `6` -> `7`
- `accepted_family_hit_count`: `8` -> `9`
- `true_candidate_missing`: `31` -> `29`
- `true_candidate_but_final_wrong`: `29` -> `31`
- `top_exact_correct_but_rejected_count`: `7` -> `5`
- `top_family_correct_but_rejected_count`: `11` -> `11`

第三批新增可离线统计的内部观测：

- `turn_count = 679`
- `turns_with_search_metadata = 676`
- `answer_group_count_dist = {0: 5, 1: 25, 2: 520, 3: 43, 4: 8}`
- `single_answer_group_turns = 25`
- `single_answer_group_rate = 3.68%`
- `rollout_branch_seed_counts = {'positive': 4601, 'negative': 4601}`
- `turns_with_feedback = 534`
- `feedback_total = 648`
- `feedback_multi_turn_rate = 58.24%`
- `single_answer_group_cap_applied_turns = 8`

这一轮的结论是：

- 第三批的内部行为是生效的：
  - 多分支 rollout 已经真正打开
  - `single_answer_group_rate` 已经很低，说明“整齐塌缩到单答案”不再是主要矛盾
  - `wrong_accepted_count` 下降，说明 final score 重平衡和 acceptance 质量有一点正向帮助
- 但第三批没有带来主目标上的净提升：
  - `top1_final_answer_hit` 没有上升，反而小幅回退
  - `top3_hypothesis_hit` 也小幅回退
- 当前更像是发生了“问题迁移”而不是“问题消失”：
  - `true_candidate_missing` 少了 `2`
  - 但 `true_candidate_but_final_wrong` 多了 `2`
  - 说明部分病例从“真病没进候选”变成了“真病进入同家族竞争，但最终排到错误 sibling / generic disease”
- 因此不能直接把第三批当前配置当成最终版继续往前推，下一步需要做 ablation，把 `P4 / P5 / P7` 的实际贡献拆开看

### 第三批后的下一步 ablation 方案

第三批复盘后，建议不要继续在 `P4 + P5 + P7` 全开状态上叠新改动，而是先跑下面三组拆分方案：

#### 方案 A：`batch2 + P4 only`

- 目标：
  - 只验证“多分支 rollout / 防塌缩”本身是否对 `top1_final_answer_hit` 有帮助
  - 排除 `P5 / P7` 对召回和重排的干扰
- 需要打开的开关：
  - `rollout_control.enable_multi_branch_rollout = true`
  - `rollout_control.branch_budget_per_action = 2`
  - `rollout_control.enable_anti_collapse_penalty = true`
- 需要关闭或回退的开关：
  - `path_evaluation.enable_dynamic_group_weights = false`
  - `path_evaluation.enable_single_answer_group_cap = false`
  - `path_evaluation.enable_scope_penalty_in_final_score = false`
  - `a2.enable_scope_cluster_rerank = false`
- 最需要看的指标：
  - `top1_final_answer_hit`
  - `single_answer_group_rate`
  - `top3_hypothesis_hit`
- 预期：
  - 如果这组能稳住 `top3`，同时让 `single_answer_group_rate` 继续维持低位，说明 `P4` 可以保留
- 建议输出目录：
  - `test_outputs/simulator_replay/error_focus_smoke95_qwen35_ablation_p4_only`

##### 方案 A 实际 replay 结果（2026-05-05）

已完成以下目录 replay：

- [error_focus_smoke95_qwen35_batch1_p4only](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/error_focus_smoke95_qwen35_batch1_p4only)

相对第二批 `batch1_p3p6` 的核心指标变化：

- `top1_final_answer_hit`: `13/95 = 13.7%` -> `12/95 = 12.6%`
- `top3_hypothesis_hit`: `40/95 = 42.1%` -> `39/95 = 41.1%`
- `hypothesis_hit`: `58/95 = 61.1%` -> `60/95 = 63.2%`
- `completed`: `19` -> `23`
- `average_revealed_slots`: `1.48` -> `1.47`
- `true_candidate_missing`: `31` -> `28`
- `true_candidate_but_final_wrong`: `29` -> `32`
- `wrong_accepted_count`: `13` -> `16`
- `top_family_correct_but_rejected_count`: `4` -> `2`

本组内部观测：

- `turn_count = 662`
- `turns_with_search_metadata = 659`
- `answer_group_count_dist = {0: 5, 1: 24, 2: 513, 3: 44, 4: 1}`
- `single_answer_group_turns = 24`
- `single_answer_group_rate = 3.63%`
- `rollout_branch_seed_counts = {'positive': 4543, 'negative': 4543}`
- `single_answer_group_cap_applied_turns = 0`

这一轮的结论是：

- `P4` 本身是有效的：
  - 多分支 rollout 已稳定生效
  - `single_answer_group_rate` 持续维持低位
  - `true_candidate_missing` 明显下降，说明它能把更多病例从“没召回”推进到“进入竞争”
- 但 `P4` 单独使用还不够：
  - `top1_final_answer_hit` 没有上升
  - `true_candidate_but_final_wrong` 与 `wrong_accepted_count` 反而上升
- 当前最合理的判断是：
  - `P4` 可以保留
  - 下一步应进入 `batch2 + P4 + P5`
  - 暂时不建议直接跳到 `P7`

#### 方案 B：`batch2 + P4 + P5`

- 目标：
  - 在保留 `P4` 的前提下，单独验证 `P5` 的 final score 重平衡是否真的减少“错误但自洽”的 top1
  - 观察 `single_answer_group_score_cap` 是否真的改善 accepted 质量
- 需要打开的开关：
  - `P4` 全部开关
  - `path_evaluation.enable_dynamic_group_weights = true`
  - `path_evaluation.enable_single_answer_group_cap = true`
  - `path_evaluation.low_anchor_single_group_score_cap = 0.62`
- 需要关闭或回退的开关：
  - `a2.enable_scope_cluster_rerank = false`
  - `path_evaluation.enable_scope_penalty_in_final_score = false`
- 最需要看的指标：
  - `top1_final_answer_hit`
  - `wrong_accepted_count`
  - `accepted_exact_hit_count`
  - `top_exact_correct_but_rejected_count`
- 预期：
  - 如果这组相比 `P4 only` 继续降低 `wrong_accepted_count`，且不明显伤害 `top3`，说明 `P5` 可以和 `P4` 绑定保留
- 建议输出目录：
  - `test_outputs/simulator_replay/error_focus_smoke95_qwen35_ablation_p4p5`

#### 方案 C：`batch2 + P7 only`

- 目标：
  - 单独判断 `scope-aware rerank` 是否是第三批指标回退的主要来源
  - 重点验证它是在修 scope drift，还是把召回和同家族排序一起压坏了
- 需要打开的开关：
  - `a2.enable_scope_cluster_rerank = true`
  - `a2.scope_cluster_exact_bonus = 0.35`
  - `a2.scope_cluster_generic_penalty = 0.28`
  - `path_evaluation.enable_scope_penalty_in_final_score = true`
- 需要关闭或回退的开关：
  - `rollout_control.enable_multi_branch_rollout = false`
  - `rollout_control.enable_anti_collapse_penalty = false`
  - `path_evaluation.enable_dynamic_group_weights = false`
  - `path_evaluation.enable_single_answer_group_cap = false`
- 最需要看的指标：
  - `top3_hypothesis_hit`
  - `true_candidate_missing`
  - `true_candidate_but_final_wrong`
  - `top_family_correct_but_rejected_count`
- 预期：
  - 如果这组单独打开后 `top3_hypothesis_hit` 回退，而 family/scope 错位并没有明显减少，就基本可以判断当前 `P7` 实现过强或时机过早
- 建议输出目录：
  - `test_outputs/simulator_replay/error_focus_smoke95_qwen35_ablation_p7_only`

### ablation 的建议执行顺序

建议按下面顺序跑，而不是同时开三组：

1. `batch2 + P4 only`
2. `batch2 + P4 + P5`
3. `batch2 + P7 only`

这样做的原因是：

- 先确认 `P4` 这个“结构性改动”本身是不是正收益
- 再确认 `P5` 是不是只改善接受质量，还是也会伤害主指标
- 最后单独审判 `P7`，避免它和 `P4 / P5` 混在一起时难以归因

## 六、建议的实际推进顺序

推荐按下面顺序落地，而不是把三批糅在一次提交里：

1. `feat/diag-batch1-repair-exam-rescue`
2. `feat/diag-batch2-candidate-feedback-competition-repair`
3. `feat/diag-batch3-rollout-final-score-scope-rerank`

每一批都保持：

- 一个主分支目标
- 一组配置开关
- 一套 focused tests
- 一次 focused replay
- 一次 `error_focus_smoke95`

## 七、每一批结束后的复盘模板

每批结束后，建议统一补一段简短复盘，写入 [phase2_changelog.md](/Users/loki/Workspace/GraduationDesign/docs/phase2_changelog.md)：

- 本批打开了哪些开关
- 实际修改了哪些函数
- 哪些指标提升了
- 哪些副作用出现了
- 下一批是否还能继续基于当前开关推进

这样后面再看 replay 指标时，可以明确区分：

- 是召回真的好了
- 还是只是 verifier 更保守了
- 是问法拿到了更多真实证据
- 还是 rollout / final scorer 只是换了另一种偏置
