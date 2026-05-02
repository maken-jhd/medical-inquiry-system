# brain

`brain/` 目录承载第二阶段“问诊大脑”的核心代码。它建立在第一阶段已经完成的知识图谱底座之上，负责会话状态管理、图谱检索、候选动作生成、提问决策、终止判断与结果汇总。

当前这一层的实现路线已经从最初的“FSM + DFS 追问”转向更接近论文的方法：

- `A1`：核心症状提取
- `A2`：假设生成
- `A3`：证据验证
- `pending action interpretation`：统一消化上一轮提问对应的患者回答
- 外层再结合 `UCT`、局部 `Simulation` 与代码级路由

当前默认实现已经具备下面这些关键特征：

- `run_reasoning_search()` 会真正执行多次 `select -> expand -> simulate -> backpropagate`
- `select_leaf()` 已按 tree policy 沿树向下选择，而不是简单摊平叶子排序
- `rollout_from_tree_node()` 已支持浅层多步 rollout，并会显式记录 `A3 -> PENDING_ACTION -> ROUTE`
- `process_turn()` 已按 `STOP / A3 / A2 / A1 / FALLBACK` 分支使用 `route_after_pending_action`
- 默认构造已真正消费 [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)

当前抽取与解释链路还有一个新的固定约定：

- 长文本抽取与解释采用 `LLM-first`：`MedExtractor`、`turn_interpreter`、`A1`、`exam_context` 不再静默退回规则词典
- 仅保留极薄的确定性层：`有 / 没有 / 不太清楚` 这类短答仍可直接短路，避免每轮都支付一次 LLM 成本
- `LLM` 结构化调用统一由 [llm_client.py](/Users/loki/Workspace/GraduationDesign/brain/llm_client.py) 负责一次重试；仍失败时抛出结构化领域错误，而不是伪装成正常问诊结果
- 名称归一化集中收口到 [normalization.py](/Users/loki/Workspace/GraduationDesign/brain/normalization.py)，位置固定在“LLM 输出之后、Neo4j / EntityLinker 之前”
- 默认构造当前要求 `llm_available=true`；如果本机未配置可用 LLM，`build_default_brain_from_env()` 会尽早报错，而不是进入“半规则半模型”的模糊状态
- `process_turn()` 当前已切到“单轮只解释一次”的统一入口：
  - 每轮先调用 `turn_interpreter`
  - 再由同一份 `mentions` 同时派生 `PatientContext`、`A1 key_features`、`pending_action_result` 和会话级 `mention_context`
  - 不再对同一句患者回答分别跑多套长文本解释器
- 当前统一语义模型不再表达“医学 certainty”：
  - `MedExtractor` 只输出患者提及项 `mention_state = present / absent / unclear`
  - `A1` 只输出值得进入首轮检索的 `key_features + selection_decision`
  - `pending_action / slot / evidence` 统一使用 `resolution = clear / hedged / unknown` 表达“当前回答是否清晰”
  - 下游消费当前优先读取 `polarity`：
    - `router` 优先按 `present / absent / unclear` 决定下一阶段
    - `hypothesis_manager` 优先按 `EvidenceState.polarity` 做增减分
    - `report_builder` 与展示侧会显式暴露 `polarity` 和会话级 `mention_context`

当前需要明确区分三种工作模式：

- `interactive`：交互式问诊模式，围绕当前会话生成下一问
- `search`：论文风格的局部树搜索模式，围绕多个候选假设做 rollout
- `fallback`：当 KG 或搜索不可靠时退回启发式选择器

## 目录职责

`brain/` 当前主要负责以下几类工作：

- 定义第二阶段统一的数据结构
- 维护患者会话状态
- 管理会话内存 DAG
- 对接 Neo4j 图谱做 `R1 / R2` 检索
- 生成候选动作并决定下一问
- 使用 `UCT` 在候选动作中做动态平衡选择
- 使用局部 `Simulation` 预演动作收益
- 根据统一提及结果与上一轮动作解释执行路由和回溯
- 生成阶段性报告或最终报告

## 当前文件说明

### 1. 类型与基础结构

- [types.py](/Users/loki/Workspace/GraduationDesign/brain/types.py)
  - 定义第二阶段通用数据结构。
  - 包括患者提及项、槽位状态、回答清晰度、患者上下文、实体链接、候选假设、候选动作、搜索树节点、轨迹与 `pending_action_result / pending_action_decision`。

- [llm_client.py](/Users/loki/Workspace/GraduationDesign/brain/llm_client.py)
  - 统一封装第二阶段大模型结构化调用。
  - 当前供 `MedExtractor`、`turn_interpreter`、`A1` 抽取、`A2` 假设排序、`exam_context` 解释和轨迹 verifier 复用。
  - 当前会统一处理结构化 prompt 的单次重试，并把超时、输出非法、空抽取等情况转换为显式领域错误。
  - 当前 `turn_interpreter` prompt 已明确区分高成本检查/疾病定义性证据的“未检查、没听说”和“结果明确阴性”，前者按 `unclear` 处理，避免缺槽位回答被误写成 hard negative。

- [errors.py](/Users/loki/Workspace/GraduationDesign/brain/errors.py)
  - 定义 LLM-first 链路下统一对外暴露的领域错误。
  - 当前包含 `llm_unavailable`、`llm_timeout`、`llm_output_invalid`、`llm_empty_extraction`、`llm_stage_failed`。

- [normalization.py](/Users/loki/Workspace/GraduationDesign/brain/normalization.py)
  - 集中维护 alias、canonical name 和常见口语映射。
  - 当前供 `MedExtractor`、`A1`、`exam_context` 和 `EntityLinker` 共用，避免各模块各自维护一套零散词典。
  - 当前新增 `expand_graph_mentions()`，用于把患者口语表达扩展成若干图谱候选 surface form，例如 CD4 低值、HIV RNA 阳性、下肢/双足发麻、药物使用和腹型肥胖等接口层表达。

- [state_tracker.py](/Users/loki/Workspace/GraduationDesign/brain/state_tracker.py)
  - 负责维护会话中的槽位状态。
  - 支持三态记录（阳性 / 阴性 / 未知）以及 `resolution = clear / hedged / unknown` 的回答清晰度维度。
  - 当前也负责保存轨迹列表与绑定搜索树。
  - 当前已为 rollout / reroot 提供轻量状态快照，避免把 `search_tree`、`last_search_result` 等重量级运行时对象一并 deepcopy。
  - 当前还会维护 `mention_context`，并按 `present > unclear > absent` 的优先级合并跨轮次提及项。

- [evidence_anchor.py](/Users/loki/Workspace/GraduationDesign/brain/evidence_anchor.py)
  - 新增 observed anchor 计算层，只消费真实会话中的 `slots / evidence_states`。
  - 会把 `Pathogen / LabFinding / ImagingFinding / LabTest / 定义性 detail` 的阳性证据分为 `strong_anchor / provisional_anchor`，把 HIV、CD4、发热、免疫抑制等高连接证据降为 `background_supported`。
  - 会过滤 rollout / simulation 来源的模拟阳性证据，并把明确否定的定义性检查结果标为 `negative_anchor`，供 A2 排序、repair 和 stop gate 共用。
  - anchor 必须命中候选疾病自己的 KG evidence payload；仅带有历史 `hypothesis_id` 但没有 payload 匹配的证据不会给该候选加锚点。
  - minimum evidence family coverage 只统计 `present + clear` 的已观察证据，`absent / unclear / hedged` 不会补足 coverage。

- [session_dag.py](/Users/loki/Workspace/GraduationDesign/brain/session_dag.py)
  - 负责维护单个患者会话的内存 DAG。
  - 当前主要承担主题分支管理与节点开闭状态维护，不再是唯一调度器。

- [neo4j_client.py](/Users/loki/Workspace/GraduationDesign/brain/neo4j_client.py)
  - 对 Neo4j 查询做轻量封装。
  - 供检索器和后续其他图谱查询逻辑复用。

- [search_tree.py](/Users/loki/Workspace/GraduationDesign/brain/search_tree.py)
  - 实现显式搜索树。
  - 当前负责搜索节点管理、父子关系维护与 reward 回传。

### 2. 检索、选择与结果汇总

- [retriever.py](/Users/loki/Workspace/GraduationDesign/brain/retriever.py)
  - 负责和知识图谱交互，提供候选节点、候选假设和验证证据的查询入口。
  - 当前已经实现论文风格的 `R1 / R2` 双向检索基础版。
  - `R1` 已增加方向语义权重与实体链接相似度融合。
  - `R1` 当前会额外估计 `disease_specific_anchor_score`，让病原体、HIV RNA、关键检查结果等更能区分目标疾病的强证据，压过 CD4/HIV 背景这类共享泛证据。
  - `R2` 已支持方向优先、已问节点过滤与问题类型提示。

- [question_selector.py](/Users/loki/Workspace/GraduationDesign/brain/question_selector.py)
  - 负责对候选提问节点进行排序。
  - 当前已经降级为 `cold-start / no-search` 的 fallback 选择器。

- [stop_rules.py](/Users/loki/Workspace/GraduationDesign/brain/stop_rules.py)
  - 定义何时可以终止问诊、何时需要停止 rollout、何时接受最终答案。
  - 当前新增 `acceptance_profile=anchor_controlled`：最终接受需要真实 observed strong anchor，或显式最低证据 family 覆盖达标；若存在更强 anchored alternative 或 clear negative definition evidence，会拒停并把原因交给 repair。
  - 结构化 stop gate 优先读取显式 `StopRuleConfig` / [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml)，`BRAIN_ACCEPTANCE_PROFILE` 只覆盖默认 baseline；它不再被 verifier prompt 的 `TRAJECTORY_VERIFIER_ACCEPTANCE_PROFILE` 覆盖。

- [report_builder.py](/Users/loki/Workspace/GraduationDesign/brain/report_builder.py)
  - 用于生成结构化阶段报告、搜索报告和最终报告。
  - 当前会额外输出 `trajectory_summary`、`why_this_answer_wins`、`evidence_for_best_answer` 等解释字段。
  - 当前最终报告也会显式输出 `confirmed_slots[].polarity` 与会话级 `mention_context`，便于前端和 replay 直接展示统一提及语义。

- [service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py)
  - 第二阶段的总编排层。
  - 当前已经串联 `turn_interpreter -> mention merge -> A1 -> A2 -> R2/A3 -> rollout -> report` 的搜索闭环。
  - 当前也是读取 [configs/brain.yaml](/Users/loki/Workspace/GraduationDesign/configs/brain.yaml) 并构造默认依赖的入口。
  - `process_turn()` 当前已按“统一解释本轮回答 -> 消化上一轮 pending action -> 判断本轮阶段 -> search / verifier / repair -> 输出下一问或最终报告”的顺序补充分段中文注释，便于顺着源码阅读控制流。
  - 当前会先把可信实体链接回填到 `mention.node_id / normalized_name`，再派生 `PatientContext` 和 `A1`，保证 opening 证据、slot 更新、R1 和 mention_context 使用同一图谱锚点。
  - 当前会把 `exam_context` 回答中的检查名与结果原文再次送入实体链接；可信命中 `LabFinding / ImagingFinding / Pathogen` 时直接写入 slot/evidence_state，且不再围绕 `__exam_context__::general` 重复追问。
  - 当前病原体阳性、影像/化验阳性、CD4 低值等强证据进入后，会设置 `force_a2_refresh` 和 `force_tree_refresh`，促使下一步重新执行 A2 并围绕新强证据收束。
  - 当前会在 A2、repair、stop 前刷新 `observed_anchor_index`，让真实病原体/检查强锚点稳定进入候选排序和拒停原因；rollout 模拟阳性只保留为路径推演，不再污染真实 confirmed evidence。
  - 当前 verifier / repair 的主控制原因已收敛到 `missing_required_anchor / anchored_alternative_exists / insufficient_evidence_family_coverage`；`hard_negative_key_evidence`、`strong_unresolved_alternative_candidates` 等细粒度原因继续保存在 metadata 中供复盘和消融使用。
  - 当前 verifier 上下文会携带累计真实会话证据 `observed_session_evidence`，避免把 rollout 模拟路径里的阳性检查当作患者已经确认的事实。
  - 当前会把检查、病原、影像、数值型 detail 的“没做过 / 没听说 / 没注意 / 不记得”统一后处理为 `unclear`；只有“阴性 / 未检出 / 未见异常 / 医生排除”等结果性否定才写成 `absent`。
  - 当 rollout 没有形成具体最终答案、或只形成 `UNKNOWN` 答案组时，当前会从 A2 候选态生成保守 `FinalAnswerScore`，避免 top hypothesis 已存在但 `best_answer=None`。

### 3. 核心解释与决策模块

- [med_extractor.py](/Users/loki/Workspace/GraduationDesign/brain/med_extractor.py)
  - 对齐论文中的 MedExtractor。
  - 负责把患者原话拆成一般信息 `P` 和临床特征 `C`。
  - 当前长文本只接受 LLM 结构化抽取；短答仍允许走极薄的 direct reply 规则。
  - 当前会兼容真实观测到的 `clinical_features` 多种 payload 形态，如 `str / list[str] / dict-wrapped`，并统一收敛为提及项列表。

- [evidence_parser.py](/Users/loki/Workspace/GraduationDesign/brain/evidence_parser.py)
  - 对应 `A1` 与统一回答解释层。
  - 当前主入口是 `interpret_turn()`，负责把患者回答统一解析成 `mentions`。
  - 再由这些 `mentions` 派生首轮检索线索，并解释上一轮目标动作的回答结果。
  - `A1` 当前只输出 `key_features + selection_decision`，不再输出 `uncertain_features / noise_features` 一类历史契约。
  - 当前会输出 `supporting_span / negation_span / uncertain_span`，长回答统一走 `turn_interpreter`；`exam_context_interpretation` 仍保留给检查上下文动作。
  - 当前对高成本检查 / 病原 / 定义性证据的否定短答，会优先交回 `turn_interpreter` prompt 做语义判断，避免“没做过检查”和“结果明确阴性”被同一个直通规则混在一起。
  - 当前不再把长回答静默退回规则词典；LLM 失败会直接向上抛出领域错误。

- [entity_linker.py](/Users/loki/Workspace/GraduationDesign/brain/entity_linker.py)
  - 对齐论文里的实体链接与阈值过滤。
  - 负责把 mention 对齐到图谱节点，并决定当前是否可信地启用 KG。
  - 当前在入图前会先消费集中式 normalization 结果，减少 `艾滋病 -> HIV感染`、`咳嗽 -> 干咳` 这类名称不齐造成的漏连。
  - 当前会对单个 mention 查询多个扩展 surface form，并在结果 metadata 中记录 `expanded_mentions / matched_mention / link_source / template_match`，便于排查患者表达到图谱节点的对接质量。

- [hypothesis_manager.py](/Users/loki/Workspace/GraduationDesign/brain/hypothesis_manager.py)
  - 对应 `A2` 假设生成。
  - 负责整理由图谱检索得到的候选疾病，并维护主假设与备选假设。
  - 当前已能结合患者上下文和证据类型做轻量重排。
  - 若启用 LLM 排序，还会把 `supporting_features / conflicting_features / recommended_next_evidence` 写入 metadata。
  - 当前 repair 重排能识别 anchor-controlled 与 guarded 两套拒停原因，对缺少真实锚点、anchored alternative、证据 family 覆盖不足、硬反证和关键支持缺失分别施加不同分数调整。

- [action_builder.py](/Users/loki/Workspace/GraduationDesign/brain/action_builder.py)
  - 对应 `A3` 证据验证的动作生成层。
  - 负责把图谱返回的验证证据转成“下一步可执行动作”。
  - 当前已支持结合 competing hypotheses 估计 `discriminative_gain`。
  - 当前也会消费 `recommended_next_evidence`，让动作更贴近鉴别诊断。
  - 当前高成本检查聚合成 `collect_general_exam_context` 时，也会把推荐证据命中分、区分度和新颖度提到动作顶层，保证 repair scorer 真正读到 verifier 推荐缺口。
  - 当前检查上下文已经带状态门控：`general` 已回答后不再生成 `collect_general_exam_context`，具体 `lab / imaging / pathogen` 已明确未做时会跳过对应高成本结果追问。

- [router.py](/Users/loki/Workspace/GraduationDesign/brain/router.py)
  - 对应上一轮动作解释后的代码级路由。
  - 当前优先根据统一提及链路写入的 `polarity + resolution` 决定继续验证、回溯、切换假设或终止。
  - 当前已支持把 `pending_action_result` 转换为显式 `PendingActionDecision`。

### 4. 搜索与前瞻模块

- [mcts_engine.py](/Users/loki/Workspace/GraduationDesign/brain/mcts_engine.py)
  - 负责按 `UCT` 公式在候选动作和树节点中做动态选择。
  - 当前已支持状态签名、tree policy、子节点扩展和 reward 回传。

- [simulation_engine.py](/Users/loki/Workspace/GraduationDesign/brain/simulation_engine.py)
  - 负责对候选动作做浅层局部预演。
  - 当前会估算 `positive / negative / doubtful` 三种回答分支的收益。
  - 当前已支持从树节点出发做浅层多步 rollout。

- [trajectory_evaluator.py](/Users/loki/Workspace/GraduationDesign/brain/trajectory_evaluator.py)
  - 对齐论文最后的轨迹聚合器。
  - 当前负责按最终答案聚类轨迹，并计算 `consistency / diversity / agent_evaluation`。
  - `diversity` 已从“唯一动作数”升级为基于轨迹相似度的组内平均差异。
  - `agent_evaluation` 当前支持 `fallback` 与可选 `llm_verifier` 两种模式。
  - 当前 `llm_verifier` 会和 stop rule 的最早接受窗口对齐：如果 `turn_index` 或 `trajectory_count` 还未达到可接受最终答案的最低条件，就先延后 verifier，临时使用 fallback 评分，避免早期 A3 追问每轮都重复触发高成本 verifier。
  - 当前 `trajectory_agent_verifier` 会显式区分 `observed_session_evidence` 与 `simulated_trajectory_evidence`；若接受理由只依赖 rollout 模拟阳性强证据、真实会话没有当前答案的特异支持，会被二次 guard 改为 `missing_key_support` 拒停。
  - 当前支持 `score_candidate_hypotheses_without_trajectories()`，用于在轨迹聚合断层时把现有候选疾病转成低分、不可直接停机的 answer score，供 repair / 下一问继续使用。

### 5. 辅助文件

- [__init__.py](/Users/loki/Workspace/GraduationDesign/brain/__init__.py)
  - Python 包初始化文件。

## 当前实现状态

目前 `brain/` 的状态可以概括为：

- 类型系统已搭好
- 基础状态机和会话图已搭好
- MedExtractor、实体链接、搜索树和轨迹评估器都已有第一轮实现
- 图谱检索入口已和当前图谱 schema 做了第一轮对齐
- A1/A2/A3 与 `pending action interpretation` 的第一批模块已建立
- UCT、局部 simulation 和轨迹评分都已接成默认主路径
- `service.py` 已经能够跑通多次 rollout 的最小搜索闭环
- 但还没有完全复现论文中的更深 rollout、完整 verifier 和最终轨迹判别器

也就是说，当前目录已经从“空脚手架”进入“可持续填充核心逻辑”的阶段。

补充说明：

- 当前 `brain/` 中较长或较复杂的函数，已经统一补充了函数内部关键步骤前的中文块级注释。
- 这些注释重点解释“当前阶段在做什么、为什么这样分支、状态写回到哪里”，方便按调用链阅读 `service / retriever / evidence_parser / simulation_engine / stop_rules` 等核心模块。

## 与 Med-MCTS 论文的对齐状态

| 组件 | 当前状态 | 说明 |
|---|---|---|
| MedExtractor | 基础版完成 | 已有 `patient_text -> (P, C)` |
| A1 | 部分完成 | 已切换为 LLM-first 抽取，短答保留极薄规则短路 |
| A2 | 部分完成 | 已支持患者上下文 + R1 候选排序 |
| A3 | 部分完成 | 已支持 R2 检索、动作构造、区分性 gain 与问题生成 |
| Pending action interpretation | 部分完成 | 已支持统一 mentions 驱动的目标回答解释与显式路由 |
| R1 / R2 | 基础版完成 | 已与真实 Neo4j 联调，R1 已增加方向语义 |
| Search Tree | 基础版完成 | 已有显式树、tree policy 和回传统计 |
| Rollout | 浅层版完成 | 已支持多次 rollout 与局部多步路径输出 |
| Path Evaluation | 基础版完成 | 已支持一致性 / 相似度驱动多样性 / agent score |
| 完整论文复现 | 未完成 | 当前仍处于“结构对齐 + 轻量实现”阶段 |

## 与其他目录的关系

- 详细运行链路说明：
  - [brain_runtime_call_chain_guide.md](/Users/loki/Workspace/GraduationDesign/docs/brain_runtime_call_chain_guide.md)

- 当前诊断系统待办清单：
  - [diagnosis_system_todolist.md](/Users/loki/Workspace/GraduationDesign/docs/diagnosis_system_todolist.md)

- 第一阶段知识图谱底座：
  - [knowledge_graph/README.md](/Users/loki/Workspace/GraduationDesign/knowledge_graph/README.md)

- 虚拟病人与离线评测：
  - [simulator/README.md](/Users/loki/Workspace/GraduationDesign/simulator/README.md)

- 第二阶段测试：
  - [tests/README.md](/Users/loki/Workspace/GraduationDesign/tests/README.md)

## 当前可直接使用的脚本

- [run_brain_demo.py](/Users/loki/Workspace/GraduationDesign/scripts/run_brain_demo.py)
  - 运行最小命令行问诊演示。

- [run_retriever_smoke.py](/Users/loki/Workspace/GraduationDesign/scripts/run_retriever_smoke.py)
  - 直接连本地 Neo4j，检查当前图谱标签、关系分布以及 `R1 / R2` 是否能返回结果。

- [run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)
  - 运行真实端到端 smoke：问诊大脑 + 虚拟病人 + 搜索报告 + benchmark 汇总。

## 代码注释规范

本目录已统一采用中文注释规范：

- 每个文件顶部有中文文件说明
- 每个类有中文说明
- 每个函数上方都应有中文用途注释
- 对较长或较复杂的函数，还应在函数内部关键步骤前补充中文块级注释，优先解释阶段切换、状态更新、排序依据、fallback 与 repair 分支。

后续新增文件和函数时，也应继续遵守这一规范。
