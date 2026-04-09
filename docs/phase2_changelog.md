# 第二阶段实现 Changelog 与问题改进记录

本文件用于系统记录第二阶段“问诊大脑 + 虚拟病人 + 搜索推理”在实际实现过程中的阶段目标、暴露问题、改进动作与阶段性结果。它的用途主要有两个：

- 作为项目内部的阶段变更记录，帮助后续继续开发时快速回忆“为什么这样改”
- 作为后续毕业论文撰写的过程材料，便于说明第二阶段并不是一次性完成，而是围绕关键问题持续迭代得到的结果

与 [phase2_execution_checklist.md](/Users/loki/Workspace/GraduationDesign/docs/phase2_execution_checklist.md) 的区别是：

- `phase2_execution_checklist.md` 更偏“路线设计与待办清单”
- 本文更偏“已经发生过哪些阶段性变化、分别解决了什么问题”

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
  - 对 `DiseasePhase / SyndromeOrComplication / Comorbidity` 的单弱证据候选增加额外惩罚
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
## 十二、当前仍未彻底解决的问题

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

## 十三、适合直接写进论文的表述点

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

## 十四、当前阶段结论

到目前为止，第二阶段已经完成了一个重要转折：

- 它不再是“只有模块、没有主流程”的脚手架
- 也不再是“有搜索名字、但没有真实 rollout”的近似实现
- 它已经进入“真实 smoke 可跑、结构与论文明显对齐、但质量仍需继续提升”的阶段

如果用一句话总结当前状态，可以写成：

- 第二阶段已完成从问诊脚手架到 Med-MCTS 风格原型系统的关键过渡，当前重点已从工程连通性转向诊断质量与评估严谨性的持续提升。
