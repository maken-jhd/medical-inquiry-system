# 诊断系统 Benchmark 执行 Checklist（2026-05-06）

本文档不再保留长篇方案说明，改为直接服务当前 benchmark 推进的执行清单。

目标只有三件事：

1. 明确哪些实验已经跑完、结论是什么
2. 明确哪些实验还没跑、优先级如何
3. 明确下一步应该做什么，避免继续分散在很多弱价值实验上

## 0. 当前结论快照

- [x] `Full System` 已完成全量 `full227`
  - 输出目录：
    - [full_system](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/benchmark_20260505_full227_baseline/full_system)
  - 关键指标：
    - `top1_final_answer_hit_rate = 0.4185`
    - `top3_hypothesis_hit_rate = 0.6740`
    - `completion_rate = 0.3921`
    - `accepted_exact_accuracy = 0.7079`
    - `accepted_exact_hit_rate_over_all = 0.2775`
    - `wrong_accepted_rate_over_all = 0.1145`

- [x] `KG + Greedy` 已完成全量 `full227`
  - 输出目录：
    - [kg_greedy](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/benchmark_20260505_full227_baseline/kg_greedy)
  - 当前结论：
    - 与 `Full System` 的 `top1 / top3` 差距较小
    - 更像“根动作选择策略”消融，不足以强力证明树搜索价值
    - 建议降级为补充材料或附录对照

- [x] `No-Tree Greedy` 已完成全量 `full227`
  - 输出目录：
    - [no_tree_greedy](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/benchmark_20260505_full227_baseline/no_tree_greedy)
  - 相比 `Full System`：
    - `top1_final_answer_hit_rate`: `0.4185 -> 0.4405`
    - `top3_hypothesis_hit_rate`: `0.6740 -> 0.6784`
    - `completion_rate`: `0.3921 -> 0.4273`
    - `accepted_exact_accuracy`: `0.7079 -> 0.6289`
    - `accepted_exact_hit_rate_over_all`: `0.2775 -> 0.2687`
    - `wrong_accepted_rate_over_all`: `0.1145 -> 0.1586`
  - 当前结论：
    - 去掉树搜索后，系统更容易更早完成
    - 但 accepted 质量明显下降，错误接受更多
    - 这组结果可以支撑“树搜索主要提升接受可靠性与粒度稳定性，而不只是提升粗粒度 top1/top3”

- [x] `Opening-Only` 已完成全量 `full227`
  - 输出目录：
    - [opening_only](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/benchmark_20260505_full227_baseline/opening_only)
  - 相比 `Full System`：
    - `top1_final_answer_hit_rate`: `0.4185 -> 0.1982`
    - `top3_hypothesis_hit_rate`: `0.6740 -> 0.4670`
    - `completion_rate`: `0.3921 -> 0.0000`
    - `average_turns`: `5.98 -> 0.00`
    - `top_exact_correct_but_rejected_count`: `32 -> 45`
  - `eligible112`：
    - `top1_final_answer_hit_rate = 0.2321`
    - `top3_hypothesis_hit_rate = 0.6071`
    - `completion_rate = 0.0000`
    - `top_exact_correct_but_rejected_count = 26`
  - 当前结论：
    - 这是一个有效的强负对照，能明确说明多轮问诊本身是必要的
    - opening 本身仍有初筛价值，但只靠 opening 难以补齐 verifier 所需的关键证据
    - 由于这组运行口径是 `--max-turns 0`，因此更适合拿来比较 `top1 / top3`，不适合把 `completion / accepted` 作为主要论点

- [x] `No-Repair` 已完成全量 `full227`
  - 输出目录：
    - [no_repair](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/benchmark_20260505_full227_baseline/no_repair)
  - 相比 `Full System`：
    - `top1_final_answer_hit_rate`: `0.4185 -> 0.3128`
    - `top3_hypothesis_hit_rate`: `0.6740 -> 0.5066`
    - `completion_rate`: `0.3921 -> 0.1850`
    - `accepted_exact_accuracy`: `0.7079 -> 0.6429`
    - `accepted_exact_hit_count`: `63 -> 27`
    - `wrong_accepted_count`: `26 -> 15`
    - `average_turns`: `5.98 -> 7.09`
    - `top_exact_correct_but_rejected_count`: `32 -> 44`
  - `eligible112`：
    - `top1_final_answer_hit_rate = 0.3750`
    - `top3_hypothesis_hit_rate = 0.6786`
    - `completion_rate = 0.2768`
    - `accepted_exact_accuracy = 0.6129`
    - `top_exact_correct_but_rejected_count = 23`
  - 当前结论：
    - `repair` 不是边缘 safety layer，而是当前系统把 verifier 拒停信号转成下一步关键补证据动作的核心机制
    - 去掉 `repair` 后，不只是 accepted 变少，连 `top1 / top3` 也明显下滑
    - 该组结果非常适合写入正文，用来证明 repair 对完整诊断闭环是必要的

- [ ] `No Scope-Aware Rerank` 尚未跑全量 `full227`

## 1. 主表口径

- [x] 主运行集固定为：
  - [full227 cases.jsonl](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260502_role_qc/cases.jsonl)

- [x] 主分析子集固定为：
  - `eligible112`

- [x] 统一运行预算固定为：
  - `max_turns = 8`
  - `case_concurrency = 6`
  - `api_error_retries = 2`
  - `BATCH_API_ERROR_COOLDOWN_SECONDS = 2.0`

- [x] 主表指标固定为：
  - `top1_final_answer_hit_rate`
  - `top3_hypothesis_hit_rate`
  - `completion_rate`
  - `accepted_exact_accuracy`
  - `accepted_exact_hit_count / rate_over_all`
  - `wrong_accepted_count / rate_over_all`
  - `average_turns`

- [x] 辅助机制指标固定为：
  - `question_count_by_cost`
  - `question_count_by_group`
  - `question_truth_hit_by_group`
  - `required_family_coverage`
  - `selected_action_source_count`

## 2. 主实验矩阵 Checklist

### 2.1 已完成

- [x] A0 `Full System`
  - 作用：
    - 论文主方法
  - 当前状态：
    - 已跑完全量，可直接进入主表

- [x] A2a `KG + Greedy`
  - 作用：
    - 弱版树搜索消融
  - 当前状态：
    - 已跑完全量
  - 当前建议：
    - 不作为“树搜索价值”的主要证据
    - 可放附录或补充实验

- [x] A2b `No-Tree Greedy`
  - 作用：
    - 强版树搜索消融
  - 当前状态：
    - 已跑完全量
  - 当前建议：
    - 取代 `KG + Greedy` 成为正文里的树搜索主消融

- [x] A1 `Opening-Only`
  - 作用：
    - 回答“多轮问诊本身是否必要”
  - 当前状态：
    - 已跑完全量
  - 当前建议：
    - 作为正文里的强负对照
    - 重点比较 `top1 / top3`
    - 对 `completion / accepted` 的解释需注明 `--max-turns 0` 口径

- [x] A3 `No-Repair`
  - 作用：
    - 回答“verifier 拒停后的自修复是否必要”
  - 当前状态：
    - 已跑完全量
  - 当前建议：
    - 作为正文里的 repair 主消融
    - 除主指标外，建议同步展示内部机制指标，尤其是 `selected_action_source_count` 与 `question_count_by_cost`

### 2.2 待完成

- [ ] A4 `No Scope-Aware Rerank`
  - 作用：
    - 回答“scope-aware 早期排序是否有效”
  - 推荐输出目录：
    - `test_outputs/simulator_replay/benchmark_20260505_full227_baseline/no_scope_rerank`
  - 推荐优先级：
    - `P2`

### 2.3 暂不继续投入

- [ ] `Pure LLM One-shot`
  - 当前建议：
    - 若时间紧，可不进入本轮主表

- [ ] `LLM + 文本 RAG`
  - 当前建议：
    - 本轮不做

## 3. 当前推荐的论文主叙事

- [x] 主叙事 1：
  - `Full System vs Opening-Only`
  - 回答“多轮问诊是否必要”

- [x] 主叙事 2：
  - `Full System vs No-Tree Greedy`
  - 回答“树搜索是否必要”
  - 当前建议：
    - 正文主消融用 `No-Tree Greedy`
    - `KG + Greedy` 放补充材料

- [x] 主叙事 3：
  - `Full System vs No-Repair`
  - 回答“repair 是否必要”

- [ ] 主叙事 4：
  - `Full System vs No Scope-Aware Rerank`
  - 若时间够，再作为第四组内部消融

## 4. 当前默认配置提醒

- [x] 当前 [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml) 处于 `No-Tree Greedy` 实验态
  - `search_policy.root_action_mode = no_tree_greedy`
  - `repair.enable_best_repair_action = false`
  - `repair.protect_search_root_action_from_low_cost_explorer = true`

因此，继续跑后续 benchmark 前不要直接依赖默认配置，优先改为使用专用变体文件：

- [x] `Opening-Only`：
  - [configs/brain_benchmark_opening_only.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain_benchmark_opening_only.yaml)
- [x] `No-Repair`：
  - [configs/brain_benchmark_no_repair.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain_benchmark_no_repair.yaml)
- [x] service 层已支持：
  - `BRAIN_CONFIG_PATH=...` 直接切换整套 brain 配置

## 5. 接下来该做什么

### 5.1 下一步优先级

- [ ] 第一步：把当前 4 组主实验结果整理进论文主表
  - 当前已具备：
    - `Full System`
    - `No-Tree Greedy`
    - `Opening-Only`
    - `No-Repair`
  - 建议主表同时放：
    - `overall`
    - `eligible112`

- [ ] 第二步：补写正文分析
  - `Opening-Only`：
    - 证明多轮问诊必要
  - `No-Tree Greedy`：
    - 证明树搜索主要提升接受可靠性与错误接受控制
  - `No-Repair`：
    - 证明 repair 是把 verifier 拒停转成有效补证据动作的关键机制

- [ ] 第三步：视时间决定是否跑 `No Scope-Aware Rerank`
  - 原因：
    - 它重要，但优先级低于 `Opening-Only` 和 `No-Repair`

### 5.2 当前最推荐的最小可交付主表

如果时间有限，至少先把下面 4 组凑齐：

- [x] `Full System`
- [x] `No-Tree Greedy`
- [x] `Opening-Only`
- [x] `No-Repair`

这 4 组已经足够支撑论文正文的主叙事：

- 多轮问诊是否必要
- 树搜索是否必要
- repair 是否必要

## 6. 下一步命令模板

### 6.1 `Opening-Only`

使用配置：

- [x] [configs/brain_benchmark_opening_only.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain_benchmark_opening_only.yaml)

命令：

```bash
OPENAI_MODEL=qwen3.5-flash \
BRAIN_CONFIG_PATH=configs/brain_benchmark_opening_only.yaml \
BATCH_API_ERROR_COOLDOWN_SECONDS=2.0 \
conda run --no-capture-output -n GraduationDesign python scripts/run_batch_replay.py \
  --cases-file test_outputs/simulator_cases/graph_cases_20260502_role_qc/cases.jsonl \
  --output-root test_outputs/simulator_replay/benchmark_20260505_full227_baseline/opening_only \
  --max-turns 0 \
  --case-concurrency 6 \
  --api-error-retries 2 \
  --no-resume
```

### 6.2 `No-Repair`

使用配置：

- [x] [configs/brain_benchmark_no_repair.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain_benchmark_no_repair.yaml)

命令：

```bash
OPENAI_MODEL=qwen3.5-flash \
BRAIN_CONFIG_PATH=configs/brain_benchmark_no_repair.yaml \
BATCH_API_ERROR_COOLDOWN_SECONDS=2.0 \
conda run --no-capture-output -n GraduationDesign python scripts/run_batch_replay.py \
  --cases-file test_outputs/simulator_cases/graph_cases_20260502_role_qc/cases.jsonl \
  --output-root test_outputs/simulator_replay/benchmark_20260505_full227_baseline/no_repair \
  --max-turns 8 \
  --case-concurrency 6 \
  --api-error-retries 2 \
  --no-resume
```

### 6.3 `No Scope-Aware Rerank`

运行前要求：

- [ ] 把 [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml) 切到：
  - `search_policy.root_action_mode = mcts`
  - `a2.enable_scope_cluster_rerank = false`
  - 其余尽量回到 `Full System` 基线

命令：

```bash
OPENAI_MODEL=qwen3.5-flash \
BATCH_API_ERROR_COOLDOWN_SECONDS=2.0 \
conda run --no-capture-output -n GraduationDesign python scripts/run_batch_replay.py \
  --cases-file test_outputs/simulator_cases/graph_cases_20260502_role_qc/cases.jsonl \
  --output-root test_outputs/simulator_replay/benchmark_20260505_full227_baseline/no_scope_rerank \
  --max-turns 8 \
  --case-concurrency 6 \
  --api-error-retries 2 \
  --no-resume
```

## 7. 结果落表 Checklist

- [ ] 主表 A：`overall + eligible`
  - 字段：
    - `top1_final_answer_hit_rate`
    - `top3_hypothesis_hit_rate`
    - `completion_rate`
    - `accepted_exact_accuracy`
    - `accepted_exact_hit_rate_over_all`
    - `wrong_accepted_rate_over_all`
    - `average_turns`

- [ ] 主表 B：按 `case_type` 拆分
  - `ordinary`
  - `low_cost`
  - `exam_driven`
  - `competitive`

- [ ] 补充表：机制指标
  - `question_count_by_cost`
  - `question_count_by_group`
  - `required_family_coverage`
  - `selected_action_source_count`

- [x] 当前已确认应写入正文的两条机制分析
  - `Opening-Only`
    - `top1_final_answer_hit_rate: 0.4185 -> 0.1982`
    - `top3_hypothesis_hit_rate: 0.6740 -> 0.4670`
    - `completion_rate: 0.3921 -> 0.0000`
    - `top_exact_correct_but_rejected_count: 32 -> 45`
    - 结论：
      - opening 具有初筛价值，但不足以构成稳定的最终诊断接受依据
  - `No-Repair`
    - `top1_final_answer_hit_rate: 0.4185 -> 0.3128`
    - `top3_hypothesis_hit_rate: 0.6740 -> 0.5066`
    - `completion_rate: 0.3921 -> 0.1850`
    - `accepted_exact_hit_count: 63 -> 27`
    - `top_exact_correct_but_rejected_count: 32 -> 44`
    - 结论：
      - repair 缺失后，系统会大量转向 low-cost explorer，问得更多但命中更少，无法有效补齐 verifier 所要求的关键证据

- [x] 当前已确认应放进补充分析表的内部指标
  - `Full System -> No-Repair`
    - `average_turns: 5.98 -> 7.09`
    - `avg_truth_hit_questions: 1.85 -> 1.41`
    - `avg_required_family_coverage_gain: 1.74 -> 0.59`
    - `verifier_rejected_stop: 112 -> 178`
    - `repair_selected_action: 782 -> 0`
    - `low_cost_explorer_action: 388 -> 1167`
    - `question_count_by_cost.high: 564 -> 30`
    - `question_count_by_group.exam_context: 285 -> 35`
    - `question_count_by_group.lab: 195 -> 11`
    - `question_count_by_group.imaging: 125 -> 6`
    - `question_count_by_group.pathogen: 44 -> 0`

## 8. 当前一句话判断

- [x] `KG + Greedy`：
  - 差距太小，不适合单独承担“树搜索有效”的主证据

- [x] `No-Tree Greedy`：
  - 已经足够写进毕业设计正文
  - 但论点应写成：
    - 树搜索主要提升接受可靠性、粒度稳定性与错误接受控制
    - 而不是简单提升粗粒度 `top1 / top3`

- [x] `Opening-Only`：
  - 是有效的强负对照
  - 已足够支撑“多轮问诊是必要的”

- [x] `No-Repair`：
  - 是有效的机制消融
  - 已足够支撑“repair 是当前系统诊断闭环中的关键模块”

- [ ] 当前真正的下一步：
  - 先把 `Full System / No-Tree Greedy / Opening-Only / No-Repair` 整理成主表和正文分析
  - 再决定是否补跑 `No Scope-Aware Rerank`
