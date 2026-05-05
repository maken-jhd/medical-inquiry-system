# 诊断系统 Benchmark 可执行实验设计（2026-05-05）

本文档用于把当前诊断系统的 benchmark 方案落成一份可直接执行的实验设计，服务于后续论文实验章节、内部消融复盘和正式 benchmark 跑数。

它重点回答四个问题：

1. 当前这版仓库到底适合做哪些对比
2. 哪些对比现在就能跑，哪些需要先补最小工程能力
3. 正式 benchmark 应该用哪批病例、哪组指标、什么目录命名
4. 整个 benchmark 应该按什么顺序推进，避免跑了很多但最后难以解释

补充说明：

- 本文只讨论当前仓库真实可落地的 benchmark，不额外引入一整套新系统
- 当前主评测入口仍是 [scripts/run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)
- 当前 focused repair 消融辅助入口仍是 [scripts/run_focused_ablation.py](/Users/loki/Workspace/GraduationDesign/scripts/run_focused_ablation.py)

## 1. 当前代码现实与术语对齐

在设计实验前，先明确当前系统边界，避免继续沿用已经失效的旧口径。

### 1.1 当前已固定的主链路

- 结构化 `stop rule` 已删除，不再作为 completed 的主判定
- 当前最终接受由 [brain/acceptance_controller.py](/Users/loki/Workspace/GraduationDesign/brain/acceptance_controller.py) 控制
- 当前 completed 只消费：
  - `llm_verifier`
  - `observed_evidence_final_evaluator`
- verifier 拒绝后，不会直接停机，而是继续进入 repair

因此，后续实验命名应优先使用：

- `verifier`
- `repair`
- `acceptance_controller`
- `observed_evidence_final_evaluator`

不再建议把实验写成：

- `simple stop rule`
- `acceptance gate`
- `no stop gate`

### 1.2 当前已经能直接支持的实验能力

- `Opening-Only / One-shot` 已经可以直接做
  - [simulator/replay_engine.py](/Users/loki/Workspace/GraduationDesign/simulator/replay_engine.py) 的追问循环受 `max_turns` 控制
  - 将 `--max-turns 0` 传给 batch replay，即可得到“只用 opening，不做后续追问”的基线
- `No-Repair` 已经基本可以通过配置开关实现
  - 当前 repair 行为集中在 [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml) 的 `repair:` 段
- `KG + Greedy` 已经可以直接通过配置开关实现
  - 当前在 [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml) 中支持：
    - `search_policy.root_action_mode = mcts | greedy`
  - `greedy` 模式会保留同一套 KG、A1/A2/A3、verifier 与 repair，只把根节点下一问从 `rollout/value` 主导改为按 root action 当前先验优先级直接选择
- 当前 replay 已可输出：
  - `top1_final_answer_hit`
  - `top3_hypothesis_hit`
  - `wrong_accepted_count`
  - `non_completed_cases.json`
  - turn 级 `search_metadata`
  - turn 级 asked/revealed 分析字段
    - `asked_action_group / asked_action_evidence_cost / asked_action_selected_source`
    - `truth_hit`
    - `revealed_slot_group / revealed_slot_name / revealed_slot_families`
  - `case_type / case_qc_status / benchmark_qc_status` 等病例分层字段
  - `benchmark_summary.json` 中的自动 cohort 汇总：
    - overall
    - `eligible`
    - 按 `case_qc_status`
    - 按 `case_type`
    - `analysis_summary`
    - `eligible_analysis_summary`

### 1.3 当前还不具备的实验能力

- `LLM + 文本 RAG` 当前没有现成检索栈
  - 仓库当前检索主路径是 Neo4j / KG，不是文本块 RAG
- `Pure LLM 多轮医生代理` 当前没有现成 benchmark runner
- `No-Verifier` 当前不适合作为第一轮正式消融
  - 因为当前 completed 本来就依赖 verifier-like 信号
  - 直接关 verifier 会导致“系统如何停机”本身失去统一语义

## 2. Benchmark 目标与研究问题

本轮 benchmark 建议围绕下面 4 个核心研究问题展开。

### RQ1：多轮问诊本身是否有必要

要回答的是：

- 如果只看 opening，不做后续追问，系统性能会掉多少
- 当前多轮问诊带来的收益究竟主要体现在 `top3` 还是 `top1`

对应实验：

- `Full System`
- `Opening-Only / One-shot`

### RQ2：树搜索是否真的比贪心提问更有价值

要回答的是：

- 当前收益到底来自 KG + 候选构造
- 还是来自 `UCT / rollout / backpropagate` 这一层搜索策略

对应实验：

- `Full System`
- `KG + Greedy`

### RQ3：repair 机制是否真的改善了错误拒停和候选漂移

要回答的是：

- verifier 拒停之后，repair 是否真的在补关键缺口
- 去掉 repair 后，系统是更早崩掉，还是其实差别不大

对应实验：

- `Full System`
- `No-Repair`

### RQ4：scope-aware 排序与粒度约束是否真的改善最终 top1

要回答的是：

- 当前 top1 不稳，是否主要是粒度漂移、部位漂移、IRIS 漂移
- scope-aware rerank 与 final scope penalty 到底有没有实际贡献

对应实验：

- `Full System`
- `No Scope-Aware Rerank`
- 可选扩展：`No Final Scope Penalty`

## 3. 建议采用的实验矩阵

### 3.1 第一轮正式主表

这一轮建议只保留 5 组，足够支撑论文主结论，也基本符合当前仓库的可实现性。

| 编号 | 实验名 | 类型 | 回答的问题 | 当前可行性 | 建议纳入 |
| --- | --- | --- | --- | --- | --- |
| A0 | `Full System` | 主方法 | 当前完整系统整体效果 | 直接可跑 | 是 |
| A1 | `Opening-Only` | 内部消融 | 多轮追问是否必要 | 直接可跑 | 是 |
| A2 | `KG + Greedy` | 结构基线 / 内部消融 | 树搜索是否必要 | 直接可跑 | 是 |
| A3 | `No-Repair` | 内部消融 | repair 是否有效 | 需配置切换 | 是 |
| A4 | `No Scope-Aware Rerank` | 内部消融 | scope-aware 早期排序是否有效 | 需配置切换 | 是 |

### 3.2 可选扩展表

下面这些适合作为第二层实验，不建议挡住第一轮正式 benchmark。

| 编号 | 实验名 | 类型 | 说明 | 当前建议 |
| --- | --- | --- | --- | --- |
| B1 | `No Final Scope Penalty` | 内部消融 | 只关 final score 的 scope penalty，保留 A2 scope cluster rerank | 可选 |
| B2 | `Pure LLM One-shot` | 外部基线 | 只把 opening text 送给 LLM 直接输出 top1 / top3 | 放附录更合适 |
| B3 | `Focused Repair Ablation` | 小样本补充实验 | 当前 [scripts/run_focused_ablation.py](/Users/loki/Workspace/GraduationDesign/scripts/run_focused_ablation.py) 已支持 repair 相关变体 | 建议保留为补充材料 |

### 3.3 本轮建议暂不纳入正式 benchmark 的方案

| 方案 | 不建议本轮纳入的原因 |
| --- | --- |
| `LLM + 文本 RAG` | 当前仓库没有现成文本检索栈，做它会演变成另起一条系统 |
| `No-Verifier` | 当前系统 completed 本来就依赖 verifier-only acceptance，直接关闭会导致停机语义失真 |
| `No-Anchor Acceptance` | anchor 影响的不只是 acceptance，还会影响 A2、final score、scope penalty，边界不干净 |
| `Pure LLM 多轮医生代理` | 当前没有现成 doctor-agent benchmark runner，公平实现成本太高 |

## 4. 每个实验的精确定义

### A0：`Full System`

定义：

- 使用当前默认 [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)
- 保留：
  - KG `R1 / R2`
  - `A1 / A2 / A3`
  - `UCT / rollout / backpropagate`
  - verifier
  - repair
  - acceptance controller
  - observed anchor / scope-aware 相关逻辑

这是论文主方法。

### A1：`Opening-Only`

定义：

- 仍保留 KG 检索、候选生成、最终评分
- 但不做后续追问
- 只用 opening text 完成一次 `brain.process_turn()`

当前落地方式：

- 直接运行 batch replay，并传 `--max-turns 0`

### A2：`KG + Greedy`

定义：

- 保留同一图谱、同一 `A1 / A2 / A3`、同一 verifier / acceptance 口径
- 取消 `UCT / rollout / backpropagate` 对“下一问动作选择”的主导
- 每轮直接选择当前最优、且仍可问的 root action

建议使用的精确定义：

- root action 基于当前 root candidate actions 的本地优先级直接选
- 不再依赖多轮 rollout 结果决定下一问
- 最终答案仍可复用当前候选疾病评分和 verifier 机制

这样定义的好处是：

- 只隔离“树搜索带来的额外价值”
- 不会把 KG、候选构造、verifier 一起删掉

当前落地方式：

- 在 [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml) 或 config override 中设置：

```yaml
search_policy:
  root_action_mode: greedy
```

### A3：`No-Repair`

定义：

- 保留 verifier
- 保留 acceptance controller
- 但关闭 repair 相关重排和动作接管机制

建议关闭的配置项：

- `repair.enable_verifier_hypothesis_reshuffle = false`
- `repair.enable_best_repair_action = false`
- `repair.enable_tree_reroot = false`
- `repair.enable_missing_key_support_competition_escalation = false`

这个实验回答的是：

- verifier 拒停后，如果系统不做 repair，会丢掉多少 `top3` 与 `top1`

### A4：`No Scope-Aware Rerank`

定义：

- 保留 final score 的其他项
- 只关闭 A2 阶段前移的 scope-aware rerank

建议关闭的配置项：

- `a2.enable_scope_cluster_rerank = false`

这个实验回答的是：

- 当前 top1 提升中，前移到 A2 的 scope-aware 排序到底有多少贡献

### B1：`No Final Scope Penalty`

定义：

- 保留 A2 的 scope-aware rerank
- 只关闭 final score 里的 scope penalty

建议关闭的配置项：

- `path_evaluation.enable_scope_penalty_in_final_score = false`

这个实验更适合放在扩展表，而不是第一轮主表。

## 5. 数据集与病例集使用原则

### 5.1 `smoke20` 与 `smoke60` 用于联调和中等规模 smoke

路径：

- [smoke20/cases.jsonl](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260502_role_qc/smoke20/cases.jsonl)
- [smoke60/cases.jsonl](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260502_role_qc/smoke60/cases.jsonl)
- [smoke60/sample_summary.json](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260502_role_qc/smoke60/sample_summary.json)

用途：

- `smoke20`
  - 检查命令是否能跑通
  - 检查输出目录结构是否正确
  - 检查新变体是否出现全量失败
- `smoke60`
  - 用四类病例各 `15` 例组成一个更均衡的中等规模 smoke
  - 适合在不跑 `full227` 的前提下，先看某次算法改动是否对 `ordinary / low_cost / exam_driven / competitive` 同时生效
  - 适合快速观察 `top1/top3`、问法分布和不同病例类型的退化点

不建议：

- 直接用 `smoke20` 或 `smoke60` 写论文主结论

### 5.2 `full227` 应作为正式主运行集

当前完整病例集位于：

- [graph_cases_20260502_role_qc/cases.jsonl](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260502_role_qc/cases.jsonl)

当前总病例数为：

- `227` 例

建议正式 benchmark 以这 `227` 例作为主运行集，先完整跑一次 replay。

原因：

- 它天然包含：
  - `eligible`
  - `weak_anchor`
  - `not_benchmark_eligible`
  - `ordinary / low_cost / exam_driven / competitive`
- 更适合评估系统整体鲁棒性，而不只是“理想病例上的诊断能力”
- 当前 batch replay 已能在汇总阶段自动按病例字段回连并输出 cohort 指标，不需要手工二次统计

### 5.3 `eligible112` 应作为正式主分析子集

在 `full227` 内部，benchmark-ready 的核心子集仍然是：

- `case_qc_status = eligible` 共 `112` 例

它的作用不是替代 `full227`，而是作为正式主分析子集：

- 用来回答“在 benchmark-ready 病例上，算法本身到底有多强”
- 用来避免把算法问题和病例构造先天不足混在一起

当前建议口径是：

- `full227`：主运行集
- `eligible112`：主分析子集
- `weak_anchor46 / not_benchmark_eligible69`：鲁棒性与失败归因子集

### 5.4 `error_focus_smoke95` 只用于回归分析

路径：

- [error_focus_smoke95_qwen35_no_stop_gate/cases.jsonl](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260502_role_qc/error_focus_smoke95_qwen35_no_stop_gate/cases.jsonl)

用途：

- 看算法改动是否真的打到了历史错误点
- 专门分析：
  - `true_candidate_missing`
  - `true_candidate_but_final_wrong`
  - `wrong_accepted`

不建议：

- 直接拿这批结果写论文主表

因为它本来就是“错误样本重跑集”，分布是偏的。

## 6. 统一实验设置

为了保证实验公平，第一轮 benchmark 建议统一以下设置。

### 6.1 固定外部依赖

- 固定 `OPENAI_MODEL`
- 固定 Neo4j 图谱版本
- 固定 `configs/frontend.yaml` 与 `configs/frontend.local.yaml`
- 固定 [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml) 的默认基线版本

### 6.2 固定运行预算

建议默认设置：

- `max_turns = 8`
- `case_concurrency = 4`
- `api_error_retries = 2`
- `BATCH_API_ERROR_COOLDOWN_SECONDS = 2.0`

其中：

- `Opening-Only` 例外，使用 `max_turns = 0`

### 6.3 固定输出规范

正式 benchmark 每个变体都使用全新输出目录，不复用旧目录。

推荐命名：

```text
test_outputs/simulator_replay/benchmark_20260505_main/
  full_system/
  opening_only/
  kg_greedy/
  no_repair/
  no_scope_rerank/
  optional_no_final_scope_penalty/
  optional_pure_llm_oneshot/
```

每个变体目录下的 `benchmark_summary.json` 当前会至少包含：

- overall 指标
- `analysis_summary`
- `eligible_summary`
- `eligible_analysis_summary`
- `case_qc_status_summaries`
- `benchmark_qc_status_summaries`
- `case_type_summaries`
- `metadata_field_coverage`

对应的 `replay_results.jsonl` 当前已为每个病例补落以下字段，便于离线复盘与二次切片：

- `case_type`
- `case_qc_status`
- `benchmark_qc_status`
- `case_qc_reasons`
- `opening_revealed_slot_ids`
- `analysis`

同时当前也会保留 turn 级诊断字段：

- `asked_action_group`
- `asked_action_question_type_hint`
- `asked_action_acquisition_mode`
- `asked_action_evidence_cost`
- `asked_action_selected_source`
- `asked_action_selected_source_priority_rank`
- `truth_hit`
- `revealed_slot_group`
- `revealed_slot_name`
- `revealed_slot_positive`
- `revealed_slot_families`

### 6.4 固定主指标与次指标

主指标：

- `top1_final_answer_hit_count / rate`
- `top3_hypothesis_hit_count / rate`
- `completion_rate`
- `accepted_final_answer_count / rate`
- `average_turns`
- `red_flag_hit_rate`

次指标：

- `accepted_exact_accuracy`
- `wrong_accepted_count`
- `average_revealed_slots`
- `completed_count`
- `failed::*`
- `max_turn_reached::true_candidate_missing`
- `max_turn_reached::true_candidate_but_final_wrong`

当前 batch replay 已能自动汇总的内部分析指标：

- `analysis_summary.question_count_total / average_question_count_total`
- `analysis_summary.question_count_by_group`
- `analysis_summary.question_count_by_cost`
- `analysis_summary.question_truth_hit_by_group`
- `analysis_summary.revealed_positive_coverage_by_group`
- `analysis_summary.selected_action_source_count`
- `analysis_summary.required_family_coverage`

如果只看 `eligible` 子集，可直接读：

- `eligible_analysis_summary`

如果要继续派生更细的内部指标，也已经有足够原始字段可离线统计，例如：

- `asked_action_group=exam_context` 可派生 `early_exam_context_trigger_rate`
- `asked_action_selected_source=repair` 可派生 `repair_action_override_rate`
- `truth_hit + revealed_slot_group` 可分析“问到了什么类型的真值”
- `opening_revealed_slot_ids + required_family_coverage` 可分析 opening 先天覆盖和 replay 后补全能力

建议固定看 3 层口径：

1. `overall`
2. `eligible`
3. `case_type`
   - `ordinary`
   - `low_cost`
   - `exam_driven`
   - `competitive`

补充说明：

- `accepted_exact_accuracy` 不能单独解读
- 因为它的分母是“accepted 的病例数”，不是全体病例数
- 如果某个系统只接受很少一部分病例，`accepted_exact_accuracy` 可能看起来很好，但整体能力并不一定强
- 因此这个指标必须与下面这些量一起看：
  - `completion_rate`
  - `accepted_final_answer_count / rate`
  - `wrong_accepted_count`

有了上面的 replay 分析字段后，当前已经可以把错误大致拆成四类：

1. 问得太少或问法分布失衡
   - 看 `question_count_total / question_count_by_group / question_count_by_cost`
2. 确实在问，但经常没命中病例真实阳性
   - 看 `question_truth_hit_by_group`
3. 病例里本来有可问的阳性证据，但回放后仍没被揭示
   - 看 `revealed_positive_coverage_by_group`
4. required family 已经补得差不多，但 `top1` 仍低
   - 这时更像是候选排序、scope 粒度或 acceptance 的问题

### 6.5 固定结论口径

正式主结论优先围绕这三件事写：

1. `Opening-Only` 与 `Full System` 的差异
2. `KG + Greedy` 与 `Full System` 的差异
3. `No-Repair / No Scope-Aware Rerank` 与 `Full System` 的差异

不要把：

- `error_focus_smoke95`
- `focused_ablation`
- `Pure LLM One-shot`

和主表混在同一层叙事里。

## 7. 当前立刻可跑的实验

当前最容易直接执行的是下面四组。

如果只是检查命令链路是否通，继续用 `smoke20` 即可；如果希望在 smoke 阶段就同时覆盖四类病例，建议把下面命令里的 `smoke20/cases.jsonl` 替换成 `smoke60/cases.jsonl`。

### 7.1 `Full System`

```bash
OPENAI_MODEL=qwen3.5-flash \
BATCH_API_ERROR_COOLDOWN_SECONDS=2.0 \
conda run --no-capture-output -n GraduationDesign python scripts/run_batch_replay.py \
  --cases-file test_outputs/simulator_cases/graph_cases_20260502_role_qc/smoke20/cases.jsonl \
  --output-root test_outputs/simulator_replay/benchmark_20260505_smoke20/full_system \
  --max-turns 8 \
  --case-concurrency 4 \
  --api-error-retries 2 \
  --no-resume
```

### 7.2 `Opening-Only`

```bash
OPENAI_MODEL=qwen3.5-flash \
BATCH_API_ERROR_COOLDOWN_SECONDS=2.0 \
conda run --no-capture-output -n GraduationDesign python scripts/run_batch_replay.py \
  --cases-file test_outputs/simulator_cases/graph_cases_20260502_role_qc/smoke20/cases.jsonl \
  --output-root test_outputs/simulator_replay/benchmark_20260505_smoke20/opening_only \
  --max-turns 0 \
  --case-concurrency 4 \
  --api-error-retries 2 \
  --no-resume
```

### 7.3 `KG + Greedy`

运行前把 [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml) 的 `search_policy.root_action_mode` 切到 `greedy`，或通过后续 batch replay variant / config override 注入：

```bash
OPENAI_MODEL=qwen3.5-flash \
BATCH_API_ERROR_COOLDOWN_SECONDS=2.0 \
conda run --no-capture-output -n GraduationDesign python scripts/run_batch_replay.py \
  --cases-file test_outputs/simulator_cases/graph_cases_20260502_role_qc/smoke20/cases.jsonl \
  --output-root test_outputs/simulator_replay/benchmark_20260505_smoke20/kg_greedy \
  --max-turns 8 \
  --case-concurrency 4 \
  --api-error-retries 2 \
  --no-resume
```

### 7.4 `Focused Repair Ablation`

如果想先做小样本补充实验，可直接用现有 focused 工具：

```bash
OPENAI_MODEL=qwen3.5-flash \
conda run --no-capture-output -n GraduationDesign python scripts/run_focused_ablation.py \
  --cases-file test_outputs/simulator_cases/graph_cases_20260502_role_qc/smoke20/cases.jsonl \
  --variants baseline,no_best_repair_action,no_reshuffle,no_reroot \
  --output-root test_outputs/simulator_replay/focused_ablation_20260505 \
  --max-turns 3 \
  --case-concurrency 1
```

注意：

- 这组更适合补充分析 repair 行为
- 不建议替代正式 benchmark 主表

## 8. 正式 benchmark 前仍建议补的工程能力

为了让全部实验真正“统一命令、统一目录、统一复现”，当前还建议继续补 2 个工程能力。

### 8.1 为 batch replay 增加变体配置切换能力

当前问题：

- [scripts/run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py) 没有直接暴露 `config_overrides`
- 这意味着 `No-Repair / No Scope-Aware Rerank` 目前只能靠手改 [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)

建议补法二选一：

1. 给 `run_batch_replay.py` 增加 `--variant`
2. 给 `run_batch_replay.py` 增加 `--config-overrides-json`

推荐优先做 `--variant`，因为更适合正式 benchmark。

建议支持的 variant：

- `full_system`
- `opening_only`
- `no_repair`
- `no_scope_rerank`
- `no_final_scope_penalty`
- `kg_greedy`

### 8.2 新增一个 benchmark 矩阵驱动脚本

建议新增：

- `scripts/run_benchmark_matrix.py`

职责：

- 接收：
  - `--cases-file`
  - `--variants`
  - `--output-root`
  - `--max-turns`
  - `--case-concurrency`
- 对每个 variant 串行调用 batch replay
- 最终生成一个总表，例如：
  - `benchmark_matrix_summary.json`
  - `benchmark_matrix_summary.md`

这样后续 benchmark 就不是“手工跑 5 次命令”，而是“一次矩阵运行”。

## 9. 正式执行顺序

建议严格按下面 4 步推进。

### 第一步：先在 `smoke20` 上做联调

建议先只跑：

- `full_system`
- `opening_only`
- `kg_greedy`
- `no_repair`
- `no_scope_rerank`

目的：

- 验证变体配置是否真的生效
- 验证输出目录结构是否一致
- 验证不会出现 `turn_count=0` 全量失败

### 第二步：在 `full227` 上跑正式主表

正式主表建议顺序：

1. `full_system`
2. `opening_only`
3. `kg_greedy`
4. `no_repair`
5. `no_scope_rerank`

原因：

- `kg_greedy` 现在已经能直接切换，建议尽早和 `full_system` 成对跑，先回答“树搜索是否必要”
- 前 5 个跑起来后，benchmark 就已有一版完整主表
- 当前 batch replay 会在所有病例完成后自动给出：
  - overall 指标
  - `eligible` 指标
  - 各 `case_type` 指标
  - 各 `case_qc_status` 指标

### 第三步：按 cohort 读取正式结果

正式读取结果时，建议至少看：

1. `overall`
2. `eligible_summary`
3. `case_type_summaries`
4. `case_qc_status_summaries`

建议解释顺序：

1. 先看 `overall`，判断系统真实整体表现
2. 再看 `eligible_summary`，判断算法在 benchmark-ready 病例上的上限
3. 再看 `low_cost / exam_driven / competitive`，判断系统对不同问诊场景的适应性

读结果时还建议固定做两组配对：

1. `top1_final_answer_hit` 配 `top3_hypothesis_hit`
   - 前者看最终答案是否命中
   - 后者看候选召回是否已经到位
2. `accepted_exact_accuracy` 配 `completion_rate / accepted_final_answer_count`
   - 前者看“放出来的病例里有多少放对了”
   - 后两者看“系统到底放出来了多少病例”

### 第四步：在 `error_focus_smoke95` 上做回归验证

这一轮不再看主表结论，而是重点看：

- `true_candidate_missing`
- `true_candidate_but_final_wrong`
- `wrong_accepted_count`

## 10. 结果整理与论文落表建议

### 10.1 主表建议字段

正式论文主表建议至少包含两张表：

主表 A：overall + eligible

| Variant | Cohort | top1_final_answer_hit | top3_hypothesis_hit | completion_rate | accepted_final_answer_count | accepted_exact_accuracy | average_turns | red_flag_hit_rate | failed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

其中 `Cohort` 至少包含：

- `overall`
- `eligible`

主表 B：按病例类型拆分

| Variant | Case Type | top1_final_answer_hit | top3_hypothesis_hit | completion_rate | accepted_final_answer_count | accepted_exact_accuracy | average_turns |
| --- | --- | --- | --- | --- | --- | --- | --- |

读表原则：

- `top1_final_answer_hit` 代表最终答案层面的真实命中
- `top3_hypothesis_hit` 代表候选召回能力
- `completion_rate` 与 `accepted_final_answer_count / rate` 代表系统到底愿不愿意放行
- `accepted_exact_accuracy` 代表“在已经放行的病例中，放对了多少”
- `average_turns` 代表达到当前结果所花的平均对话预算
- `red_flag_hit_rate` 代表系统对重要危险线索的捕捉能力

因此：

- `accepted_exact_accuracy` 必须和 `completion_rate` 一起读
- `top1_final_answer_hit` 必须和 `top3_hypothesis_hit` 一起读

### 10.2 补充表建议字段

补充表建议包含：

| Variant | true_candidate_missing | true_candidate_but_final_wrong | wrong_accepted_count | top_exact_correct_but_rejected |
| --- | --- | --- | --- | --- |

### 10.3 误差分析建议

每个主变体都至少抽 10 个典型错误样本，分为：

- 真实疾病没进 top3
- 真实疾病进了 top3，但 top1 错
- top1 对了，但 verifier 不接受
- 明显问句模板错位，浪费 turn

错误分析优先使用：

- `non_completed_cases.json`
- `replay_results.jsonl`
- turn 级 `search_metadata`
- `benchmark_summary.json -> analysis_summary / eligible_analysis_summary`

## 11. 当前结论

按当前仓库状态，最合理的 benchmark 方案是：

1. 主实验以“内部消融对比”为主
2. 主运行集使用 `full227`
3. 主分析子集使用 `eligible112`
4. batch replay 在完成后自动输出 overall、`eligible` 与各病例类型指标
5. 第一轮正式主表保留 5 组：
   - `Full System`
   - `Opening-Only`
   - `KG + Greedy`
   - `No-Repair`
   - `No Scope-Aware Rerank`
6. `Pure LLM One-shot` 只作为可选附录基线
7. `LLM + 文本 RAG` 本轮不纳入

如果时间或工程预算有限，最小可交付 benchmark 也应至少包含：

- `Full System`
- `Opening-Only`
- `No-Repair`
- `KG + Greedy`

其中：

- `Opening-Only` 负责回答“多轮问诊是否必要”
- `KG + Greedy` 负责回答“树搜索是否必要”
- `No-Repair` 负责回答“verifier 拒停后的自修复是否必要”

这三条已经足够支撑论文方法有效性的主叙事。
