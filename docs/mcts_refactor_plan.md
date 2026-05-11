# MCTS Skeleton 重构说明

本文记录 2026-05-11 这次 MCTS 骨架重构的目标、边界和当前落地状态。

## 1. 本次重构目标

这次修改只做一件事：

- 把当前问诊系统中的 MCTS 骨架拆成更可插拔的结构

明确不做：

- 不修改 `simulator/*` 虚拟病人和病例生成逻辑
- 不重写 `A1 / A2 / A3` 的业务语义
- 不接入训练或 learned patient simulator

## 2. 为什么要拆

重构前，`brain/simulation_engine.py` 同时承担了：

- 回答分支概率估计
- rollout reward 计算
- branch payload 构造
- rollout 中具体分支选择

同时，`brain/mcts_engine.py` 中 child node 的 `state_signature` 更接近 action path id，而不是当前 belief state 的近似表示。

这会导致两个问题：

1. 后续若要接 learned transition / learned reward，只能继续往 `SimulationEngine` 里堆逻辑
2. 搜索树的复用和调试都缺少一个更清晰的状态表示层

## 3. 当前新增的四个抽象

### 3.1 `response_transition_model.py`

- 定义 `ResponseTransitionModel`
- 当前默认实现为 `HeuristicResponseTransitionModel`
- 当前也新增了 `StatisticalResponseTransitionModel`
- 输入当前动作、会话状态、候选假设和可选患者上下文
- 输出回答分支概率分布

当前默认仍支持：

- `positive`
- `negative`
- `doubtful`

接口层已经预留未来扩展到：

- `not_done`
- `done_positive`
- `done_negative`
- `done_unclear`

`StatisticalResponseTransitionModel` 当前的最小落地方式是：

- 先从 graph cases 与 replay 输出构建粗粒度条件统计
- 再按当前 `candidate_hypotheses` 做 top-k belief mixture
- 对普通问诊估计：
  - `P(present / absent / unclear | disease, evidence_family, question_type)`
- 对检查上下文估计：
  - `P(done / not_done | disease, exam_kind)`
  - `P(result | disease, test_type, done)`

### 3.2 `transition_statistics.py`

- 定义 `TransitionStatistics` 与 `TransitionStatisticsBuilder`
- 负责离线加载：
  - graph cases
  - replay results
  - disease evidence family catalog
- 负责集中抽取：
  - `question_type`
  - `evidence_family`
  - `exam_kind`
  - `test_type`
- 负责 belief helper：
  - `build_normalized_hypothesis_belief(...)`

### 3.3 `reward_model.py`

- 定义 `RolloutRewardModel`
- 当前默认实现为 `HeuristicRolloutRewardModel`
- 显式拆出：
  - `information_gain_surrogate`
  - `hypothesis_alignment`
  - `turn_cost`
  - `repeat_penalty`
  - `high_cost_penalty`
  - `uncertainty_penalty`

这样做的意义是：

- 先把旧 heuristic 从“硬编码公式”升级成“可替换接口”
- 后续如果要切到 entropy reduction、margin gain 或 learned reward，只需要换模型而不是重写 rollout 框架

### 3.4 `state_signature.py`

- 定义 `BeliefStateSignatureBuilder`
- 当前可把下面这些信息压成稳定签名：
  - `slots`
  - `asked_node_ids`
  - `active_topics`
  - `exam_context availability`
  - `top hypotheses`
  - `pending context`

在 `modular_v2` 下，child node 不再默认把 `state_signature` 直接写成 path id，而是改为：

- 基于当前状态
- 加入 post-action 近似 asked set / pending action / focus hypothesis
- 生成近似 belief-state signature

## 4. legacy 与 modular_v2 的关系

本次不是“覆盖旧实现”，而是“并行保留”：

- `search_impl=legacy`
  - 继续沿用原来的 inline heuristic 路径
  - 方便 benchmark 和 replay 做回归对照

- `search_impl=modular_v2`
  - 启用新的 `transition model + reward model + belief signature` 骨架
  - 当前 `transition_model.type` 已支持 `heuristic | statistical`
  - 但职责边界已经清晰，后续可逐层替换

## 5. 当前仍然保留的限制

这次重构后，系统仍然不是完整 chance-node MCTS：

- rollout 里还没有显式 chance node expansion
- `branch_selection_mode=expectation_ready` 还只是接口占位
- statistical transition model 当前仍是频数统计 baseline，不是 learned classifier
- reward model 还不是严格 Bayesian 信息增益

也就是说，这次完成的是“骨架升级”，不是“学习版 MCTS 完成”。

## 6. 后续建议

建议后续按下面顺序继续推进：

1. 把 `StatisticalResponseTransitionModel` 替换成 learned classifier，例如 `XGBoost / LightGBM / 小型分类器`
2. 在 `RolloutRewardModel` 上接更合理的 confidence / entropy surrogate
3. 若需要进一步逼近标准 MCTS，再考虑显式 chance node 或 expectation backup
4. 最后再考虑与 `simulator/*` 的 patient model 联动，而不是本次就一起改
