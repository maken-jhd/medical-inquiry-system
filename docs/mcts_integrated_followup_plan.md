# MCTS Integrated Root Pool 后续改造方案（待后续实现）

本文档记录当前对 `MCTS vs Greedy` 差距过小问题的复盘，以及后续更完整的 MCTS 改造方向。  
它不是当前这轮 benchmark 的立即实现项，而是后续有时间时再推进的设计备忘。

## 1. 当前问题判断

当前 `MCTS` 与 `Greedy` 差距过小，核心不是 rollout 次数太少，而是三件事叠加：

1. `MCTS` 的 reward 与 `Greedy` 的 prior 高度同源
   - rollout value 很大程度仍继承 `prior_score`
   - 导致 `MCTS` 按 `average_value` 选根动作时，容易和 `Greedy` 按 `prior_score` 选出的结果趋同

2. 真正发出去的下一问经常不是 MCTS root action
   - `repair`
   - `early exam rescue`
   - `low-cost explorer`
   这三类 post-hoc 覆盖器会在 root policy 之后重新改问，导致 `MCTS / Greedy` 都被同一套后处理拉平

3. root action space 仍偏窄
   - 当前 root 主要围绕 `top1 hypothesis` 扩动作
   - 若真病在 `top2/top3`，MCTS 往往会更努力验证错误的 `top1`，而不是主动拉开 sibling 诊断

## 2. 当前轮采取的保守策略

由于当前 benchmark 时间紧，这一轮不直接改 MCTS 主干，而是先做更干净的 greedy 基线：

- `root_action_mode = greedy`
- greedy 不再享受：
  - `verifier repair`
  - `early exam rescue`
  - `low-cost explorer`

对应配置文件：

- [configs/brain_benchmark_greedy_clean.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain_benchmark_greedy_clean.yaml)

这样做的目的不是最终方案，而是先把 greedy baseline 清理干净，避免继续拿“被后处理覆盖过的 greedy”与 MCTS 对比。

## 3. 后续推荐的真正改造方向

后续不建议继续把 `repair / low-cost / exam rescue` 作为 MCTS 之后的覆盖器。  
更合理的方向是把这些信号内置到统一的 root candidate pool 中。

旧链路：

```text
MCTS root action
-> repair 覆盖
-> exam rescue 覆盖
-> low-cost explorer 覆盖
```

建议的新链路：

```text
integrated root candidate pool
-> MCTS rollout / score / select
```

## 4. 建议的 integrated root candidate pool

在 root depth 合并以下候选来源：

- `regular_r2_actions`
- `repair_candidate_actions`
- `low_cost_observable_actions`
- `exam_context_actions`
- `anchor_gap_candidates`
- `scope_disambiguation_candidates`

每个 action 需要统一写入 metadata，例如：

- `mcts_candidate_source`
- `repair_gap_score`
- `anchor_gap_score`
- `scope_gain`
- `family_coverage_gain`
- `answerability_score`
- `cost_penalty`
- `competition_gain`

## 5. reward 与 prior 的解耦原则

后续版本里应尽量避免继续让 reward 直接复用 `prior_score`。

建议：

- `prior`
  - 只负责 root expansion bias
  - 表示“局部看上去值得先扩的程度”

- `reward`
  - 表示“这条动作在多步推演后，是否真的提升最终诊断质量”
  - 可重点融合：
    - `diagnostic_gain`
    - `competition_gain`
    - `anchor_gain`
    - `scope_gain`
    - `family_coverage_gain`
    - `answerability_score`
    - `cost_penalty`
    - `negative / doubtful branch penalty`

## 6. 推荐实现顺序

为了控制风险，建议分两阶段推进。

### Phase 1：Root-only integrated benchmark

先新增两种严格配对模式：

- `mcts_integrated`
- `greedy_integrated`

要求：

- 共用同一个 integrated root candidate pool
- 只比较 root policy
- post-hoc 覆盖器只保留不可问时的兜底，不再正常覆盖 root action

目的：

- 先证明在统一候选池下，MCTS 是否能比 Greedy 更好地选第一问

### Phase 2：Deep integrated rollout

只有在 Phase 1 已经能稳定拉开差距后，再把 integrated candidate logic 往 deeper rollout 扩展。

目的：

- 让 repair / exam / low-cost 不只是 root 候选，而是成为整棵树里可被统一消费的动作空间

## 7. 当前结论

当前时间约束下，更合理的做法是：

1. 先补跑 `Greedy Clean`
2. 用它替代历史 `KG + Greedy` 作为更干净的 greedy 参考
3. 将 `MCTS integrated root pool` 方案保存在本文档中，待 benchmark 阶段结束后再推进
