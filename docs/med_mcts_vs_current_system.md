# Med-MCTS 论文实现与当前系统实现对照

本文件用于整理两类内容：

- Med-MCTS 论文实现与当前交互式问诊系统实现的关键差异
- 当前系统中启发式参数的主要来源、当前合理定位，以及后续提升方向

这份说明主要服务于后续代码维护、实验设计和论文方法章节写作。

## 1. 总体定位差异

先用一句话概括：

- Med-MCTS 论文更接近“固定病例文本上的静态诊断搜索器”
- 当前系统更接近“真实多轮交互中的动态问诊代理”

两者都使用 `MCTS / UCT` 思路做搜索，但它们搜索的对象并不完全相同。

### 1.1 论文实现更偏静态病例诊断

论文中的输入是一段相对完整的患者病例描述。系统先从这段文本中抽取患者信息和临床特征，再围绕候选疾病与待验证指标做树搜索。论文中的 `A3 -> A4` 更像：

1. 选一个当前候选疾病
2. 选一个最值得核对的临床指标
3. 回到固定病例文本上判断该指标是否存在、是否足以支持或反驳该疾病
4. 再决定继续验证、回退到其他疾病，或接受当前疾病

因此，论文的 `simulation` 主要是在模拟“推理路径”，而不是模拟“真实患者下一轮会怎么回答”。

### 1.2 当前系统更偏动态问诊

当前系统的真实输入是逐轮到来的患者回答。系统每一轮都要：

1. 解析当前回答
2. 更新真实会话状态
3. 基于当前状态重新做 `A1 / A2 / A3`
4. 用搜索决定“下一轮最值得问什么”
5. 再等待患者真实回答

因此，当前系统中的搜索不是在一份固定病例文本上反复做静态判别，而是在一个持续变化的会话状态上做“前瞻式追问决策”。

## 2. 关键实现差异

### 2.1 搜索目标不同

- 论文：更偏向搜索“哪个疾病假设最终成立”
- 当前系统：同时要搜索“当前最可能的答案”和“下一轮最该问的动作”

当前系统在 `run_reasoning_search()` 中会同时产出：

- `best_answer`
- `selected_action`
- 全部 rollout 轨迹
- 按答案聚合后的轨迹评分

对应实现见：

- [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
- [brain/trajectory_evaluator.py](/Users/loki/Workspace/GraduationDesign/brain/trajectory_evaluator.py)
- [brain/mcts_engine.py](/Users/loki/Workspace/GraduationDesign/brain/mcts_engine.py)

### 2.2 路径内部裁判不同

- 论文：`A4` 更像“演绎裁判”
- 当前系统：rollout 更像“启发式前瞻器”

论文中的 `A4` 会围绕“当前疾病 + 当前待验证指标 + 当前病例描述”做语义判断：该指标是否存在、是否足以确认、是否足以反驳、是否仍需继续验证。

当前系统中，`rollout` 并不会真的调用一个强语义裁判去逐步判断未来路径，而是把未来回答压缩为 `positive / negative / doubtful` 三类标准分支，再用动作先验、关系类型、当前候选病分数等启发量估计收益。

对应实现见：

- [brain/simulation_engine.py](/Users/loki/Workspace/GraduationDesign/brain/simulation_engine.py)
- [brain/router.py](/Users/loki/Workspace/GraduationDesign/brain/router.py)

### 2.3 终局判定结构不同

- 论文：`MCTS` 搜索结束后，再按答案组做多维判别
- 当前系统：搜索结束后，还要经过 `verifier + anchor-controlled acceptance + repair`

当前系统的最终接受不是只看 rollout 分数，还要额外检查：

- 真实观测锚点是否足够强
- 是否存在更强 anchored alternative
- verifier 是否明确拒停
- 当前答案轨迹数、一致性、最终分数是否达标

对应实现见：

- [brain/trajectory_evaluator.py](/Users/loki/Workspace/GraduationDesign/brain/trajectory_evaluator.py)
- [brain/stop_rules.py](/Users/loki/Workspace/GraduationDesign/brain/stop_rules.py)
- [brain/evidence_anchor.py](/Users/loki/Workspace/GraduationDesign/brain/evidence_anchor.py)
- [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)

### 2.4 当前系统额外引入了“真实会话约束”

由于这是一个动态问诊系统，当前实现里还有论文没有的约束：

- `pending_action` 机制
- 检查上下文门控
- 已问节点去重
- 高成本证据与低成本证据区分
- 真实回答与 rollout 模拟证据隔离

这些约束会显著影响搜索行为，因此当前系统不能简单等同于论文中的静态 Med-MCTS。

## 3. 当前系统中的启发式参数是怎么来的

当前系统不是一个端到端可微模型，因此不存在统一的“反向传播学出来的启发函数”。它更像一个“多层启发式搜索系统”，启发量主要来自以下几层。

### 3.1 R1 候选疾病语义分

候选疾病的初始排序来自 `R1` 检索后的语义打分，主要综合：

- 输入特征覆盖率
- 命中关系数量
- 关系是否更具定义性
- 实体链接可信度
- 疾病特异锚点强度
- 对“只吃到一条泛化弱证据”的候选做额外降权

对应实现见：

- [brain/retriever.py](/Users/loki/Workspace/GraduationDesign/brain/retriever.py)

这部分是当前系统最基础的“候选病先验”。

### 3.2 A2 竞争性重排分

在 R1 分数基础上，`A2` 还会继续做一次轻量竞争性重排，主要关注：

- 某候选是否命中了更多“独有证据”
- 某候选是否过度依赖和其他疾病共享的泛证据
- 候选自身的语义分是否稳定
- 是否已经出现更疾病特异的 observed anchor

对应实现见：

- [brain/hypothesis_manager.py](/Users/loki/Workspace/GraduationDesign/brain/hypothesis_manager.py)

### 3.3 A3 动作 prior

“下一轮最该问什么”主要依赖动作的 `prior_score`。这部分来自：

- 图谱中目标节点与关系的基础权重
- 当前证据对竞争诊断的区分价值
- 推荐证据命中情况
- 问题是否低成本、是否更容易获取
- 是否属于红旗证据
- 是否与备选诊断高度重叠

对应实现见：

- [brain/retriever.py](/Users/loki/Workspace/GraduationDesign/brain/retriever.py)
- [brain/action_builder.py](/Users/loki/Workspace/GraduationDesign/brain/action_builder.py)

### 3.4 rollout 中的启发式收益估计

当前 rollout 不直接调用强语义 judge，而是用启发式公式估计动作价值。它会综合：

- `prior_score`
- 关系类型 bonus
- 当前主假设分数
- 阳性 / 阴性 / 模糊三类回答分支概率
- 反证优先级

对应实现见：

- [brain/simulation_engine.py](/Users/loki/Workspace/GraduationDesign/brain/simulation_engine.py)

这部分就是当前“启发式模拟”的核心。

### 3.5 observed anchor 与 acceptance gate

除了搜索期启发外，当前系统还对真实会话证据引入了一套显式锚点权重：

- strong anchor bonus
- provisional anchor bonus
- background support bonus
- negative anchor penalty
- 不同关系类型和节点标签的先验权重

对应实现见：

- [brain/evidence_anchor.py](/Users/loki/Workspace/GraduationDesign/brain/evidence_anchor.py)
- [brain/stop_rules.py](/Users/loki/Workspace/GraduationDesign/brain/stop_rules.py)

这部分严格说不是搜索 prior，但它会反过来影响候选病排序、repair 方向和最终是否允许停机，因此也是一类重要的启发式参数。

### 3.6 历史真实问答带来的在线 reward

当前系统也不是纯静态规则。真实问答结束后，会把结果回写成动作 reward，例如：

- 明确阳性给更高 reward
- 模糊阳性给中等奖励
- 明确阴性给负 reward
- 模糊阴性给较轻负 reward

这些 reward 会累积到 `action_stats` 中，后续又参与 `UCT` 计算。

对应实现见：

- [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
- [brain/state_tracker.py](/Users/loki/Workspace/GraduationDesign/brain/state_tracker.py)
- [brain/mcts_engine.py](/Users/loki/Workspace/GraduationDesign/brain/mcts_engine.py)

因此，当前系统可以概括为：

- 前半段：知识驱动的显式启发式
- 后半段：真实交互反馈驱动的在线经验修正

## 4. 这些启发参数目前是如何确定的

当前参数来源主要分为四类：

### 4.1 文献迁移参数

一部分搜索超参数直接沿用了论文或接近论文的设置，例如：

- `num_rollouts`
- `max_depth`
- `max_child_nodes`
- `exploration_weight`
- `discount_factor`

这些参数主要保存在：

- [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)

### 4.2 领域知识先验

另一部分参数来自医学和问诊常识，例如：

- `DIAGNOSED_BY`、`HAS_PATHOGEN` 通常比普通症状更定义性
- 高成本检查不应在信息量相近时总是优先于低成本追问
- 背景 HIV / CD4 证据不能和病原体阳性处于同等强度

这类先验主要体现在：

- 关系类型权重
- anchor 权重
- acquisition mode / evidence cost 偏置
- reward 设计

### 4.3 工程约束驱动的手工设定

还有一部分参数不是“医学知识”，而是“为了让交互式系统更稳定、更可控”而设定的，例如：

- 重复问题惩罚
- 高成本检查轻度降权
- verifier 延迟触发窗口
- soft reject override 的阈值

这类参数更多服务于系统稳定性、成本控制和用户体验。

### 4.4 小规模 replay / ablation / sweep 校准

仓库中已经有 focused replay、ablation 和 acceptance sweep 的实验入口，这说明系统并非完全依赖“拍脑袋常数”，而是有在用实验结果做人工校准。

相关入口见：

- [scripts/run_focused_ablation.py](/Users/loki/Workspace/GraduationDesign/scripts/run_focused_ablation.py)
- [scripts/run_acceptance_sweep.sh](/Users/loki/Workspace/GraduationDesign/scripts/run_acceptance_sweep.sh)
- [scripts/run_verifier_acceptance_sweep.sh](/Users/loki/Workspace/GraduationDesign/scripts/run_verifier_acceptance_sweep.sh)

但需要明确：

- 当前系统的很多核心启发参数仍然是“显式设计 + 人工校准”
- 还不是“通过大规模数据自动学习出来的最优参数”

## 5. 这样做是否科学

如果把当前系统表述为“知识驱动的启发式搜索系统”，那么这套方法是科学的；关键在于要把方法边界讲清楚。

比较准确的描述应该是：

1. 系统主体是可解释的规则加权搜索结构
2. 参数初值来自文献、领域知识和工程约束
3. 再通过 focused replay、ablation 和参数 sweep 做经验校准
4. 最终在独立病例集上验证整体效果

因此，当前更适合将这些参数称为：

- 启发式权重
- 校准参数
- 搜索控制参数

而不是称为：

- 端到端学习得到的模型参数

## 6. 后续提高方向

当前系统下一步更适合走“更系统的参数校准”和“局部学习化”，而不是直接追求整套规则系统的端到端反向传播。

### 6.1 先做更系统的参数校准

建议优先把关键参数分成开发集校准对象，固定测试集后再统一评估。优先关注：

- `exploration_weight`
- `positive_branch_probability`
- `positive_clear_bonus / negative_clear_penalty`
- `disease_specific_anchor_bonus`
- `strong_anchor_bonus`
- `min_final_score / min_agent_eval_score`

同时补充：

- 参数敏感性分析
- 模块级 ablation
- 不同病例类型下的 profile 对比

### 6.2 把“未来回答概率”从常数升级为条件化模型

当前 rollout 中的回答分支概率仍然较粗糙。后续可以考虑让它依赖：

- 当前候选疾病
- 当前患者已知证据
- 问题类型
- 病例类型

这会让 rollout 的前瞻更接近真实问诊。

### 6.3 引入局部 A4-style judge，而不是全量替换 rollout

当前不一定要把整套搜索都改成论文式语义裁判。更现实的方向是：

- 保留当前启发式 rollout 的速度和稳定性
- 只对 top-k 高价值动作增加一个轻量语义 judge
- 或仅在 stop 前、repair 前调用更强局部验证

这样可以在成本可控的前提下提高语义判别力。

### 6.4 让启发函数逐步“半学习化”

比起直接训练整个系统，更稳妥的方案是分模块替换：

- 学一个 `action prior` 模型
- 学一个 `rollout value` 模型
- 学一个 `acceptance scorer`

然后把模型输出继续接回当前 MCTS / verifier / repair 框架，而不是一次性推翻现有结构。

### 6.5 建立更清晰的实验闭环

后续应逐步形成：

1. 训练/校准集
2. 开发集
3. 测试集
4. 指标面板
5. 参数 sweep 记录
6. 关键失败病例归因

这样后续不论是继续调规则，还是引入学习模块，都会更稳。

## 7. 当前结论

当前系统与论文的关系可以概括为：

- 在搜索框架上参考了 Med-MCTS
- 在真实交互链路上明显扩展出了自己的动态问诊结构
- 在路径内部没有完整复现论文式 A4，而是采用了更工程化的启发式 rollout
- 在路径外补入了 `verifier + anchor + repair` 作为更强的安全闸门

因此，当前系统的合理定位不是“论文原样复现”，而是：

**面向真实多轮问诊场景、以知识驱动启发式搜索为核心，并由 verifier / anchor / repair 提供安全兜底的交互式诊断系统。**
