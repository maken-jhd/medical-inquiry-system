# 阶段二与虚拟病人执行清单

本文件重写第二阶段的实现路线。新的路线不再以“DFS 深度优先追问”作为主策略，而是改为：

- 状态机驱动
- UCT 动态选题
- 局部 Simulation 前瞻预演
- 演绎分析驱动的严格路由
- 虚拟病人支持下的离线自动对战与策略缓存

补充说明：

- 本文主要记录“目标路线”和“当前执行状态”
- 若需要查看更偏过程复盘与论文素材整理的内容，可参见 [phase2_changelog.md](/Users/loki/Workspace/GraduationDesign/docs/phase2_changelog.md)

## 当前进度快照

截至当前版本，下面这些点已经有了第一轮实现：

- `brain/med_extractor.py` 已提供 `patient_text -> (P, C)` 的 MedExtractor 层
- `brain/evidence_parser.py` 已支持 `A1` 的 LLM 主通道 / 规则回退，以及目标感知的答案解释
- `brain/entity_linker.py` 已提供 mention 到图谱节点的阈值化链接
- `brain/retriever.py` 已实现 `R1 / R2` 双向检索基础版，并增加真实 Neo4j smoke 检查入口
- `brain/search_tree.py` 已提供搜索树节点、父子关系和回传统计
- `brain/mcts_engine.py` 已从动作打分器扩展到 tree policy + 节点扩展 + 回传控制器
- `brain/simulation_engine.py` 已支持浅层多步 rollout 和轨迹对象输出
- `brain/trajectory_evaluator.py` 已支持按最终答案聚类轨迹，并计算一致性 / 相似度驱动多样性 / agent score
- `brain/service.py` 已能按 `PatientContext -> A1 -> A2 -> R2/A3 -> rollout -> report` 跑通多次 rollout 搜索闭环
- `brain/service.py` 已会真正读取 `configs/brain.yaml` 并驱动默认依赖构造
- `simulator/generate_cases.py` 已提供一批覆盖典型场景的 seed cases
- `simulator/replay_engine.py` 已支持批量自动回放
- `simulator/benchmark.py` 已支持基础离线指标统计
- 第二阶段测试已扩展到 `27` 条并全部通过

当前仍需继续加强的重点：

- `A2 / A3 / A4` 仍然是“论文近似实现”，还不是完整论文复现
- 搜索树虽然已经进入真实 rollout 循环，但 rollout 深度与最终聚合器仍偏轻量
- 真实 Neo4j 联调已打通，但 `R2` 的医学语义过滤还要继续收紧
- 多轮会话的稳定收敛能力仍需继续加强
- 更丰富的病例覆盖与更严格的离线指标仍待补齐

## 当前与 Med-MCTS 的对齐状态

| 模块 | 当前状态 | 说明 |
|---|---|---|
| MedExtractor | 已有基础版 | 已补 `patient_text -> (P, C)`，但仍以规则 + 可选 LLM 为主 |
| A1 | 部分完成 | 已支持 LLM 主通道与规则回退 |
| A2 | 部分完成 | 已支持患者上下文 + R1 候选排序，并可保留 recommended evidence |
| A3 | 部分完成 | 已支持 R2 检索、动作构建、UCT 选择与区分性 gain |
| A4 | 部分完成 | 已支持目标感知解释、显式路由与可选 LLM judge |
| R1 / R2 | 已完成基础版 | 已与真实 Neo4j 图谱联调，R1 已增加方向语义 |
| Search Tree | 已完成骨架 | 已支持显式树节点、tree policy 与回传 |
| Rollout | 已完成浅层版 | 已能输出多步路径，但深度与分支仍偏保守 |
| Trajectory Evaluation | 已完成基础版 | 已能做 consistency / similarity diversity / agent_eval 聚合 |
| Full Med-MCTS Reproduction | 未完成 | 目前处于“结构对齐 + 轻量实现”阶段 |

## 一、为什么要改路线

原方案中过于依赖固定主题分支和 DFS 连续追问，这会带来一个问题：

- 系统可能在某个已触发的症状分支里停留过久
- 却没有及时切换去询问更关键的流行病学史、高危行为史或红旗线索

在 HIV/AIDS 问诊场景中，这种“顺着一个分支一直问到底”的方式不够灵活。新的实现路线需要同时解决三件事：

1. 避免陷入单一主题的冗长追问
2. 不只看单步信息增益，而要看后续几步是否有价值
3. 让槽位状态真正驱动流程跳转，而不是只做被动记录

## 二、新的总体路线

新的第二阶段不再是：

- 状态记录 -> DFS 追问 -> 结束

而是改成：

- A1 状态更新
- A2 候选假设生成
- A3 候选问题生成与 UCT 选择
- A4 演绎分析 / 回溯 / 路由调整
- 必要时进入局部 Simulation 预演
- 最终输出报告或继续追问

一句话说：

- `state_tracker` 负责记录“现在知道什么”
- `router` 负责判断“现在该进入哪一类推理阶段”
- `mcts_engine` 负责决定“下一问最值得问什么”
- `simulation_engine` 负责“向后看几步”

## 三、核心思想改造

### 1. 破除 DFS 刚性，改为 UCT 动态平衡

原来的 `session_dag.py` 不再作为“唯一调度器”，而只保留为：

- 会话内存图
- 主题关联结构
- 已开分支与已关闭分支的记录器

真正决定“现在问哪个主题”的，不再是 DFS，而是 UCT 分值：

$$
UCT(s,a)=\overline{Q}(s,a)+c\sqrt{\frac{\ln N_{parent}(s)}{N(s,a)}}
$$

这里：

- `s`：当前会话状态
- `a`：一个候选提问动作
- `Q(s,a)`：当前动作的平均收益
- `N_parent(s)`：当前状态被访问次数
- `N(s,a)`：当前动作被选择次数

在问诊里，这意味着：

- 如果某个主题分支已经被问了很多次，探索项会下降
- 如果另一个关键主题还没问过，探索项会自动升高
- 系统会在“顺着当前线索问下去”和“切去问别的主题”之间动态平衡

### 2. 从单步信息增益升级为局部预演

新的策略不只问：

- “问这个问题能增加多少信息？”

而是问：

- “问完这个问题后，后面 2 到 4 步会不会更快收敛？”

也就是说，候选问题不只做即时打分，还要做局部 rollout：

- 假设回答为阳性
- 假设回答为阴性
- 分别快速更新状态
- 观察这些分支最终对候选疾病/阶段分布的影响

再把结果折回当前动作的 `Q(s,a)`。

### 3. 让高维状态真正驱动路由

原来的三态/四态状态现在要成为主动路由器的一部分。

状态判定建议采用：

- `exist_confident`
- `exist_uncertain`
- `non_exist_confident`
- `non_exist_uncertain`
- `unknown`

然后和路由绑定：

- `exist_confident`
  - 关闭该问题子树
  - 提升相关假设权重
- `non_exist_confident`
  - 剪掉依赖该证据的局部分支
  - 回到假设重生成
- `exist_uncertain`
  - 不立刻扩新主题
  - 回到验证阶段，生成该证据的细化问题
- `unknown`
  - 保持候选状态，由 UCT 决定是否探索

## 四、新的代码结构建议

当前 `brain/` 目录已经有可复用基础，但第二阶段建议新增和重构如下：

```text
brain/
  __init__.py
  types.py
  neo4j_client.py
  state_tracker.py
  session_dag.py
  retriever.py
  stop_rules.py
  report_builder.py
  service.py

  router.py
  mcts_engine.py
  simulation_engine.py
  rollout_policy.py
  action_builder.py
  hypothesis_manager.py
  evidence_parser.py
  state_signature.py

simulator/
  __init__.py
  case_schema.py
  generate_cases.py
  patient_agent.py
  replay_engine.py
  benchmark.py
  path_cache_builder.py
```

## 五、第二阶段新的实现清单

### A. 状态与类型层

1. `brain/types.py`
- 在现有基础上补充：
  - `EvidenceState`
  - `ActionStats`
  - `StateVisitStats`
  - `MctsAction`
  - `SimulationOutcome`
  - `RouteDecision`

目标：

- 为 UCT、Simulation、演绎路由提供统一数据结构

2. `brain/state_tracker.py`
- 继续保留当前会话状态记录功能
- 新增：
  - `set_evidence_state(...)`
  - `get_evidence_state(...)`
  - `set_action_stats(...)`
  - `increment_action_visit(...)`
  - `update_action_value(...)`

目标：

- 让状态追踪器不仅记录槽位值，还记录搜索过程中的访问统计

### B. 路由与假设管理

3. `brain/hypothesis_manager.py`
- 负责：
  - 根据当前阳性/阴性/不确定槽位生成候选假设
  - 对假设进行增权、减权、剪枝

4. `brain/router.py`
- 负责在 A1/A2/A3/A4 间切换
- 核心函数建议：
  - `route_after_slot_update(...)`
  - `route_after_question_answer(...)`
  - `route_after_simulation(...)`

目标：

- 不再依赖固定 DFS 流程，而是根据状态主动选择阶段

### C. 检索与动作生成

5. `brain/retriever.py`
- 保留当前冷启动、正向假设、反向验证检索
- 增加按主题检索、按红旗检索、按流行病学史检索

6. `brain/action_builder.py`
- 新增
- 负责把检索结果转成候选动作集合
- 每个动作包含：
  - 目标节点
  - 所属主题
  - 关联假设
  - 是否红旗
  - 当前先验权重

目标：

- 把图谱候选节点提升为可供 MCTS 选择的“动作”

### D. UCT 选择与提问决策

7. `brain/mcts_engine.py`
- 新增
- 负责：
  - Selection：按 UCT 选择动作
  - Expansion：扩展新动作
  - Backpropagation：回传动作收益

建议函数：

- `select_action(session_state, actions)`
- `compute_uct(action_stats, parent_visits, exploration_constant)`
- `backpropagate(session_state, action_id, reward)`

说明：

- 这一层替代原来单纯的 DFS / 固定主题顺序

8. `brain/question_selector.py`
- 不再只是简单打分排序器
- 改造成 UCT 的薄封装入口：
  - 如果有离线路径缓存且命中，则优先用缓存
  - 否则调用 `mcts_engine`

### E. 局部 Simulation

9. `brain/simulation_engine.py`
- 新增
- 负责对一个候选动作进行局部 rollout

建议函数：

- `simulate_action(session_state, action, depth=3)`
- `simulate_positive_branch(...)`
- `simulate_negative_branch(...)`
- `estimate_terminal_reward(...)`

目标：

- 不只看当前问题本身，而是看它后续几步的诊断收敛价值

10. `brain/rollout_policy.py`
- 新增
- 负责 rollout 时的简化策略

例如：

- 正向回答概率用图谱先验 + 历史统计近似
- 优先选择和当前候选假设最相关的后续问题

### F. 证据解析与状态签名

11. `brain/evidence_parser.py`
- 新增
- 负责把用户回答或虚拟病人回答转换为 `SlotUpdate`
- 第一版可以从规则做起，不必一开始上 LLM

12. `brain/state_signature.py`
- 新增
- 负责把当前会话状态压缩成可缓存的签名

例如：

- `发热=true|干咳=true|CD4<200=unknown|高危行为=unknown`

目标：

- 支持离线路径缓存和后续快速命中

### G. 终止与报告

13. `brain/stop_rules.py`
- 继续保留
- 但终止条件要改成基于：
  - Top1/Top2 假设差距
  - 可用动作数量
  - rollout 后的剩余收益

14. `brain/report_builder.py`
- 报告要增加：
  - 已确认事实
  - 存疑事实
  - 被排除的重要方向
  - 当前最可能的阶段/并发问题
  - 下一步建议检查

15. `brain/service.py`
- 改造成真正的总编排器
- 编排顺序建议：
  - 接收新回答
  - 调用 `evidence_parser`
  - 更新 `state_tracker`
  - 触发 `router`
  - 获取候选动作
  - 调用 `question_selector / mcts_engine`
  - 返回下一问或最终报告

## 六、虚拟病人路线重写

### F. 病例结构

16. `simulator/case_schema.py`
- 在现有结构上补充：
  - `truth_hypothesis_path`
  - `trigger_nodes`
  - `forbidden_disclosures`
  - `answer_policy`

目标：

- 不只是知道患者“有什么”，还要知道“问到什么才会说”

### G. 病例生成

17. `simulator/generate_cases.py`
- 第一阶段先做 20-50 个高质量 seed cases
- 类型建议覆盖：
  - 急性期
  - 无症状期
  - PCP
  - 结核
  - 隐球菌
  - CMV
  - 弓形虫脑病
  - 妊娠管理
  - 慢病共病
  - 模糊回答
  - 隐瞒高危行为

### H. 病人代理

18. `simulator/patient_agent.py`
- 从“看到 node_id 就答”升级为：
  - 只有命中允许披露条件时才回答
  - 支持模糊、回避、二次追问后松口

### I. 自动对战与策略缓存

19. `simulator/replay_engine.py`
- 真正驱动：
  - `ConsultationBrain`
  - `VirtualPatientAgent`
- 每轮记录：
  - 当前状态签名
  - 选中的动作
  - 回答结果
  - 更新后的假设分布

20. `simulator/benchmark.py`
- 输出：
  - 平均轮次
  - 命中率
  - 红旗漏检率
  - 无效追问率
  - 错误主题停留时长

21. `simulator/path_cache_builder.py`
- 从 replay 结果提取：
  - `state_signature -> best_next_action`
- 供在线阶段优先命中

## 七、开发顺序重排

新的推荐顺序是：

1. `brain/types.py`
2. `brain/state_tracker.py`
3. `brain/hypothesis_manager.py`
4. `brain/router.py`
5. `brain/retriever.py`
6. `brain/action_builder.py`
7. `brain/mcts_engine.py`
8. `brain/simulation_engine.py`
9. `brain/question_selector.py`
10. `brain/evidence_parser.py`
11. `brain/service.py`
12. `simulator/case_schema.py`
13. `simulator/patient_agent.py`
14. `simulator/generate_cases.py`
15. `simulator/replay_engine.py`
16. `simulator/benchmark.py`
17. `simulator/path_cache_builder.py`

## 八、当前已有基础如何复用

当前已完成的文件并不废弃，只是角色要改变：

- `state_tracker.py`
  - 保留，升级为“状态 + 搜索统计”容器
- `session_dag.py`
  - 保留，但只作为会话内存图，不再做唯一调度器
- `retriever.py`
  - 保留并扩展
- `question_selector.py`
  - 从静态排序器升级为 UCT 入口
- `stop_rules.py`
  - 保留并扩展
- `service.py`
  - 继续作为总编排器

## 九、最小可运行 MVP 定义

当满足以下条件时，可以认为新的第二阶段 MVP 跑通：

1. 能把回答解析成状态更新
2. 能根据状态生成候选假设
3. 能把候选节点转成动作集合
4. 能用 UCT 从动作集合中选出下一问
5. 能对候选动作做 1 到 2 层局部 simulation
6. 能根据状态置信度执行路由切换
7. 能用虚拟病人至少跑通 5 个 seed case

## 十、当前代码注释规范

第二阶段代码默认采用以下规范：

- 每个文件都应有中文文件说明
- 每个函数上方都应有中文用途说明
- 类说明、模块说明优先使用中文
- 说明性注释不再使用纯英文

## 十一、2026-04-12 切换前待做清单

本节记录后端第二阶段当前的暂停点，以及下一轮切换到前端搭建时应优先完成的事项。

### A. 后端当前阶段结论

- [x] `guarded_lenient` 已从“几乎不接受”校准到更可用的状态：10 个 focused cases 中 `accepted_correct_count=4`、`accepted_wrong_count=0`。
- [x] `family promotion / evidence recording` 已打通一轮：CD4、β-D 葡聚糖、PCP PCR / BAL、胸部 CT / 磨玻璃影等高价值 anchor 已能进入更稳定的 evidence family。
- [x] `provisional_evidence_families` 已生效：`pcp_vague_001` 和 `pcp_conflicting_001` 可通过 `confirmed + provisional` 的 PCP combo 被接受。
- [x] 安全闸门仍有效：`TB vs PCP`、`影像强但非 PCP` 等强混淆负样本仍保持 `wrong_rejected`。
- [x] 最新 focused validation 输出目录：`test_outputs/simulator_replay/focused_acceptance_validation_20260412_003500`。

### B. 后端暂存待办

- [ ] 拆分并修正 `accepted_on_turn1_count` 的语义。
- 建议改成：
  - `first_verifier_accept_on_turn1_count`
  - `final_accept_on_turn1_count`
- 原因：当前指标更像“verifier 第一次愿意 accept 出现在 turn1”，不是“系统最终在 turn1 停止”。

- [ ] 单独处理非 PCP / 阶段类答案的 acceptance。
- 当前代表病例：`systemic_non_pcp_001`。
- 当前现象：`best_answer=急性期` 且正确，但 verifier 持续认为“急性期”过于宽泛，给出 `missing_key_support`，最终成为 `correct_rejected`。
- 下一步方向：为“阶段 / 综合征 / 非特异性急性表现”设计独立 acceptance 规则，而不是继续改 PCP combo gate。

- [ ] 继续收紧 verifier schema 稳定性。
- 当前问题：`guarded_lenient` 中仍有少量 `verifier_schema_valid_false`，主要集中在 PCP 的 accepted / blocked 轮次。
- 当前判断：accept 侧字段可用，但 reject schema 偶尔 fallback。
- 下一步方向：优化 verifier prompt / schema 后处理，减少 `fallback_inferred`。

- [ ] 保留 guarded gate，不再整体放松。
- 当前判断：`accepted_wrong_count=0` 是这条线最重要的安全收益。
- 后续只做结构化放宽，不做全局阈值放宽。

- [ ] 暂不继续大调 repair-aware A3。
- 当前判断：missing-family-first repair、combo anchor 提前选择、reroot 和 reshuffle 已经能推动关键证据追问。
- 下一步除非出现“问不到关键 family”，否则不优先动 repair 权重。

### C. 前端搭建第一阶段目标

- [ ] 确认前端技术栈与目录位置。
- 推荐先检查仓库是否已有前端目录；若没有，建议新建 `frontend/`。
- 推荐技术栈：`Vite + React + TypeScript`，后续根据展示需要接入图谱 / 时间线 / 对话面板。

- [ ] 搭建最小可运行前端骨架。
- MVP 页面至少包含：
  - 问诊对话区
  - 当前问题 / 患者回答输入区
  - 当前 top hypothesis 面板
  - evidence family 面板
  - search / repair / verifier 状态面板
  - 最终报告面板

- [ ] 定义前后端 API 边界。
- 第一版建议只包一层轻量 API，不直接把复杂内部对象暴露给前端。
- 建议接口：
  - `POST /api/session/start`
  - `POST /api/session/{session_id}/turn`
  - `GET /api/session/{session_id}/state`
  - `GET /api/session/{session_id}/report`
  - `GET /api/replay/{run_id}/cases`

- [ ] 设计前端展示用的 session view model。
- 建议字段：
  - `messages`
  - `selected_action`
  - `root_best_action`
  - `repair_selected_action`
  - `repair_context`
  - `hypotheses`
  - `confirmed_evidence_families`
  - `provisional_evidence_families`
  - `guarded_gate_audit`
  - `verifier_metadata`
  - `final_report`

- [ ] 优先支持 replay 结果浏览。
- 原因：真实在线问诊需要 Neo4j + DashScope，前端第一版可以先消费已落盘 JSON/JSONL，快速做出可演示界面。
- 推荐优先读取：
  - `focused_repair_summary.jsonl`
  - `focused_metrics.json`
  - `guarded_gate_audit.jsonl`
  - `a4_evidence_audit.jsonl`

- [ ] 增加“论文演示模式”。
- 展示重点：
  - 为什么 root best action 被 repair action 替换
  - verifier 为什么拒停 / 接受
  - confirmed / provisional evidence family 如何累积
  - 当前最终答案为什么胜出
  - 哪些 alternative 被排除

### D. 前端第二阶段增强项

- [ ] 增加搜索树 / rollout 轨迹可视化。
- 展示 `select -> expand -> simulate -> backpropagate` 的路径和 reward。

- [ ] 增加 hypothesis timeline。
- 展示每轮 top1 / top2 如何变化，以及 verifier-driven reshuffle 是否发生。

- [ ] 增加 acceptance calibration 面板。
- 对比 `baseline`、`slightly_lenient`、`guarded_lenient` 的指标：
  - `accepted_correct_count`
  - `accepted_wrong_count`
  - `correct_best_answer_but_rejected_count`
  - `accepted_with_provisional_combo_count`
  - `verifier_positive_but_gate_rejected_count`

- [ ] 增加病例 replay 对比视图。
- 同一个 case 横向比较不同 profile 下：
  - 最终答案
  - stop reason
  - repair turns
  - guarded block reason
  - evidence family 变化

### E. 前端启动前检查

- [ ] 跑一次前端目录探查，确认是否已有 package 管理文件。
- [ ] 若新建前端，先只提交脚手架和静态 replay viewer，不急于接真实后端。
- [ ] 保留当前后端 replay 输出格式，前端优先适配现有落盘文件。
- [ ] 若需要在线调用后端，再新增轻量 API 层，避免前端直接依赖 `brain/` 内部类型。
