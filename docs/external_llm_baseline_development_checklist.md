# 外部 LLM 基线开发 Checklist（2026-05-14）

本文档用于规划三类外部基线实验的实现路径：

1. `纯 LLM` 多轮问诊基线
2. `LLM + 文本 RAG` 多轮问诊基线
3. `LLM + KG RAG` 多轮问诊基线

当前不把 `LLM + 向量 RAG` 作为本轮主线。原因不是它没有价值，而是当前更需要先把：

- 无检索的外部弱基线跑稳
- 非结构化医学文档检索增强跑通
- 结构化知识图谱检索增强跑通

这样后续 benchmark 才能清楚回答“不同知识形态的检索增强到底带来什么收益”，而不是先把工程复杂度堆到向量数据库、embedding 索引和 reranker 上。

目标不是另起一套评测框架，而是尽量复用当前已有的：

- [simulator/replay_engine.py](/Users/loki/Workspace/GraduationDesign/simulator/replay_engine.py)
- [simulator/patient_agent.py](/Users/loki/Workspace/GraduationDesign/simulator/patient_agent.py)
- [simulator/benchmark.py](/Users/loki/Workspace/GraduationDesign/simulator/benchmark.py)

这样外部实验就能与当前 `Full System / No-Tree Greedy / Opening-Only / No-Repair` 使用同一套评测口径。

## 0. 实验目标与边界

- [x] 目标 1：实现一个 `纯 LLM` 医生 baseline
  - 在最多 `8` 轮内与虚拟病人对话
  - 允许模型提前输出最终判断
  - 若到第 `8` 轮仍未结束，则强制输出 `top1 + top3`

- [x] 目标 2：实现一个 `LLM + 文本 RAG` 医生 baseline
  - 对话机制与 `纯 LLM` 一致
  - 区别只在于每轮可额外读取文本检索结果

- [x] 目标 3：实现一个 `LLM + KG RAG` 医生 baseline
  - 对话机制与 `纯 LLM` 一致
  - 区别只在于每轮可额外读取图谱检索结果
  - 图谱只提供检索上下文，不直接复用主系统的树搜索、repair 与 acceptance 决策

- [x] 目标 4：复用现有 benchmark 汇总
  - 输出目录结构仍兼容：
    - `replay_results.jsonl`
    - `benchmark_summary.json`
    - `non_completed_cases.json`
    - `status.json`

- [x] 当前明确不做
  - 不直接复用当前 `brain/service.py` 的图谱搜索与 repair 主链路
  - 不把图谱检索伪装成“文本 RAG”
  - 不单独维护另一套不兼容的 benchmark 统计脚本
  - 当前不引入向量数据库、embedding 检索与 reranker

## 1. 推荐实现总原则

- [x] 原则 1：新建 baseline doctor，而不是魔改现有 `ConsultationBrain`
  - 推荐新增目录：
    - [baselines/](/Users/loki/Workspace/GraduationDesign/baselines)

- [x] 原则 2：对 `ReplayEngine` 暴露同一最小接口
  - 必须实现：
    - `start_session(session_id)`
    - `process_turn(session_id, patient_text)`
    - `finalize(session_id)`

- [x] 原则 3：先做 `纯 LLM`，再做 `LLM + 文本 RAG`，最后做 `LLM + KG RAG`
  - `纯 LLM` 是最小 MVP
  - `文本 RAG` 建议在 `纯 LLM` smoke 跑通后再叠加
  - `KG RAG` 建议在 `文本 RAG` 契约稳定后接入，避免同时放大“检索问题”和“图谱依赖问题”

- [x] 原则 4：RAG 先做轻量版本
  - 第一版优先使用：
    - 文本侧：`HIV_cleaned/` 中的专家共识文档或由其整理出的文本化 disease profile
    - 文本检索：`BM25 / TF-IDF` 这类轻检索
    - 图谱侧：基于 [brain/retriever.py](/Users/loki/Workspace/GraduationDesign/brain/retriever.py) 的轻量 KG 检索封装
  - 暂不要求：
    - 全量原始文档 chunk
    - embedding 检索
    - reranker

- [x] 原则 5：检索只增强上下文，不替代 baseline doctor 自身决策
  - `纯 LLM / 文本 RAG / KG RAG` 三条路线都应共用同一 ask / final JSON 契约
  - `文本 RAG` 与 `KG RAG` 的差异只体现在 prompt 里额外注入的检索结果
  - 不把 `mcts_engine / reward_model / response_transition_model / acceptance_controller` 搬进外部 baseline

## 2. 目录与文件 Checklist

### 2.1 新增 baseline 代码目录

- [x] 新增 [baselines/](/Users/loki/Workspace/GraduationDesign/baselines)
- [x] 新增 [baselines/__init__.py](/Users/loki/Workspace/GraduationDesign/baselines/__init__.py)

### 2.2 纯 LLM baseline

- [x] 新增 [baselines/llm_consultation_brain.py](/Users/loki/Workspace/GraduationDesign/baselines/llm_consultation_brain.py)
  - 负责实现：
    - session 状态维护
    - 多轮问诊 prompt
    - ask / final 两类结构化输出

- [x] 新增 [baselines/llm_baseline_types.py](/Users/loki/Workspace/GraduationDesign/baselines/llm_baseline_types.py)
  - 建议放：
    - `BaselineSessionState`
    - `BaselineAskDecision`
    - `BaselineFinalDecision`
    - `BaselineTurnRecord`

### 2.3 文本 RAG baseline

- [x] 新增 [baselines/text_rag_retriever.py](/Users/loki/Workspace/GraduationDesign/baselines/text_rag_retriever.py)
  - 负责：
    - 加载文本 corpus
    - 查询
    - 返回 top-k 文本块

- [x] 新增 [baselines/llm_text_rag_consultation_brain.py](/Users/loki/Workspace/GraduationDesign/baselines/llm_text_rag_consultation_brain.py)
  - 复用 `llm_consultation_brain`
  - 只增加检索层与检索结果注入 prompt

### 2.4 KG RAG baseline

- [x] 新增 [baselines/kg_rag_retriever.py](/Users/loki/Workspace/GraduationDesign/baselines/kg_rag_retriever.py)
  - 负责：
    - 连接当前活跃 Neo4j 搜索图谱
    - 基于当前对话与候选病名检索关键证据画像
    - 返回 top-k 图谱证据块

- [x] 新增 [baselines/llm_kg_rag_consultation_brain.py](/Users/loki/Workspace/GraduationDesign/baselines/llm_kg_rag_consultation_brain.py)
  - 复用 `llm_consultation_brain`
  - 只增加 KG 检索层与检索结果注入 prompt

### 2.5 CLI 与语料构建

- [x] 新增 [scripts/run_baseline_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_baseline_replay.py)
  - 复用：
    - `VirtualPatientAgent`
    - `ReplayEngine`
    - `summarize_benchmark()`
    - `build_non_completed_case_report()`
    - `build_benchmark_cohort_summary()`
    - `timing_summary` 聚合逻辑
    - `status.json` 刷新逻辑
  - 目标不是只产出“同名文件”，而是尽量复用或抽出当前 [scripts/run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py) 的结果汇总骨架
  - 这样外部 baseline 的 `benchmark_summary.json / non_completed_cases.json / status.json` 才能与主 benchmark 保持同一 schema

- [x] 新增 [scripts/build_text_rag_corpus.py](/Users/loki/Workspace/GraduationDesign/scripts/build_text_rag_corpus.py)
  - 第一版负责把已有知识整理成文本化 disease profile corpus

- [ ] KG RAG 第一版不强制新增静态 corpus 构建脚本
  - 优先直接连接当前 Neo4j 搜索图谱
  - 如果后续需要脱离 Neo4j 跑离线对照，再考虑补充 KG snapshot 导出脚本

### 2.6 测试

- [x] 新增 [tests/test_llm_consultation_brain.py](/Users/loki/Workspace/GraduationDesign/tests/test_llm_consultation_brain.py)
- [x] 新增 [tests/test_llm_text_rag_consultation_brain.py](/Users/loki/Workspace/GraduationDesign/tests/test_llm_text_rag_consultation_brain.py)
- [x] 新增 [tests/test_text_rag_retriever.py](/Users/loki/Workspace/GraduationDesign/tests/test_text_rag_retriever.py)
- [x] 新增 [tests/test_llm_kg_rag_consultation_brain.py](/Users/loki/Workspace/GraduationDesign/tests/test_llm_kg_rag_consultation_brain.py)
- [x] 新增 [tests/test_kg_rag_retriever.py](/Users/loki/Workspace/GraduationDesign/tests/test_kg_rag_retriever.py)
- [x] 新增 [tests/test_run_baseline_replay.py](/Users/loki/Workspace/GraduationDesign/tests/test_run_baseline_replay.py)

## 3. 纯 LLM 基线 Checklist

### 3.1 session 状态

- [x] 每个病例保存独立 session state
  - 至少包括：
    - `session_id`
    - `dialogue_history`
    - `turn_index`
    - `asked_questions`
    - `last_model_top3`
    - `finalized`

### 3.2 每轮 prompt 契约

- [x] system prompt 约束
  - 你是 HIV/AIDS 场景下的问诊医生
  - 最多 `8` 轮
  - 每轮只能做一件事：
    - `ask` 一个问题
    - 或 `final` 给出最终判断

- [x] 显式禁止复合长问句
  - 不允许一次问多个 target
  - 不允许 “有没有 A/B/C” 这类三合一问法

- [x] 要求结构化 JSON 输出

- [x] 当前 pure LLM baseline 已注入 closed-set disease scope
  - 优先从当前 benchmark 病例目录对应的 `manifest.json` 提取全量 `Disease` 名称
  - 当前 role-QC 病例集对应 scope 为 `80` 个 disease nodes
  - prompt 内会显式要求：
    - `top3[].name` 必须从这份 disease scope 中精确选择
    - `final_answer` 必须从这份 disease scope 中精确选择
  - 这样能减少输出“某类机会性感染”这类泛化答案，令 pure LLM baseline 与主 benchmark 的闭集答案空间更一致

### 3.3 ask 决策 JSON

- [ ] 第一版建议输出字段：

```json
{
  "decision": "ask",
  "question_text": "……",
  "target_name": "……",
  "top3": [
    {"name": "……", "confidence": 0.0},
    {"name": "……", "confidence": 0.0},
    {"name": "……", "confidence": 0.0}
  ],
  "reasoning": "……"
}
```

- [x] 当前 pure LLM 第一版已收紧为更轻的输出契约
  - ask 阶段不再强制模型额外输出：
    - `question_group`
    - `evidence_cost`
    - 顶层 `confidence`
  - 其中：
    - `question_group` 由程序端根据 `question_text + target_name` 推断
    - `evidence_cost` 由程序端根据问题分组兜底
    - ask/final 的 `decision_confidence` 默认优先复用 `top3[0].confidence`
  - 这样可以减轻结构化输出负担，但 `pending_action.metadata.question_type_hint / evidence_cost` 仍会继续落盘，保证 replay 分析口径不丢

### 3.4 final 决策 JSON

- [ ] 第一版建议输出字段：

```json
{
  "decision": "final",
  "compiled": true,
  "final_answer": "……",
  "top3": [
    {"name": "……", "confidence": 0.0},
    {"name": "……", "confidence": 0.0},
    {"name": "……", "confidence": 0.0}
  ],
  "reasoning": "……"
}
```

### 3.5 对 ReplayEngine 的返回契约

- [x] 当 `decision=ask`
  - 返回：
    - `next_question`
    - `pending_action`
    - `search_report`
    - `final_report=None`

- [x] `pending_action` 最小建议字段
  - `action_id`
  - `action_type="baseline_ask"`
  - `target_node_id`
  - `target_node_name`
  - `target_node_label`
  - `metadata.question_type_hint`
  - `metadata.evidence_cost`
  - `metadata.selected_action_source="baseline_llm"`

- [x] `target_node_id` 第一版建议
  - 不强依赖真实图谱 node id，但也不要默认全部用任意 synthetic id
  - `exam_context` 类问题应保留当前虚拟病人已识别的前缀约定：
    - `__exam_context__::general`
    - `__exam_context__::lab`
    - `__exam_context__::imaging`
    - `__exam_context__::pathogen`
  - 普通 `symptom / risk / detail / lab / imaging / pathogen` 问题优先使用：
    - 稳定别名
    - 或已知槽位名 / 疾病画像中的标准名称
  - 只有在确实找不到合适稳定锚点时，才退回：
    - `baseline::<normalized_target_name>`
  - 即便如此，`question_text` 仍必须足够清晰，因为虚拟病人除 `target_node_id` 外也会结合 `question_text` 做匹配

- [x] 当 `decision=final`
  - 直接返回 `final_report`
  - 不再返回下一问

## 4. final_report 兼容 Checklist

为了让 [simulator/benchmark.py](/Users/loki/Workspace/GraduationDesign/simulator/benchmark.py) 不用大改，外部 baseline 的 `final_report` 至少应兼容以下字段：

- [x] `best_final_answer.answer_name`
- [x] `candidate_hypotheses`
- [x] `stop_reason`
- [x] `metadata.backend`

### 4.1 推荐最小 final_report 结构

```json
{
  "best_final_answer": {
    "answer_name": "肺孢子菌肺炎",
    "confidence": 0.82
  },
  "candidate_hypotheses": [
    {"name": "肺孢子菌肺炎", "score": 0.82},
    {"name": "肺结核", "score": 0.10},
    {"name": "细菌性肺炎", "score": 0.08}
  ],
  "stop_reason": "final_answer_accepted",
  "metadata": {
    "backend": "pure_llm"
  }
}
```

### 4.2 8 轮强制收尾规则

- [x] 若前 `1..7` 轮模型未选择 `final`
  - 第 `8` 轮后调用 `finalize()`
  - 强制输出：
    - `top1`
    - `top3`

- [x] 强制收尾时建议：
  - `stop_reason = "baseline_turn_limit_finalize"`
  - 保持 `status = max_turn_reached`
  - 但仍让 benchmark 能统计 `top1 / top3`

## 5. 文本 RAG 基线 Checklist

### 5.1 语料来源

- [ ] 第一版不要直接接图谱推理链
- [ ] 第一版优先从 [HIV_cleaned](/Users/loki/Workspace/GraduationDesign/HIV_cleaned) 与其整理产物构建“文本化 disease profile”语料
  - 每个疾病一条或多条文本记录
  - 内容可包含：
    - 疾病名
    - 常见症状
    - 定义性 `lab / imaging / pathogen`
    - 易混疾病区分点
    - 高危背景

### 5.2 corpus 文件格式

- [ ] 建议输出到：
  - `test_outputs/rag_corpus/...`
  - 或 `artifacts/rag_corpus/...`

- [ ] 每条文档至少包含：
  - `doc_id`
  - `disease_name`
  - `title`
  - `content`
  - `tags`
  - 可选补充：
    - `source_path`
    - `chunk_id`

### 5.3 检索实现

- [ ] 第一版优先：
  - `BM25`
  - 或 `TF-IDF`

- [ ] 每轮检索 query 来源
  - 当前全部对话历史
  - 上一轮答案
  - 当前模型 top3 候选名

- [ ] 每轮给模型的 RAG 上下文
  - 建议仅保留 top-k 文本块
  - 避免把太多噪声文本塞给模型
  - 当前不要求向量数据库或 embedding 索引

### 5.4 RAG prompt 注入

- [ ] `LLM + RAG` 与 `纯 LLM` 共用同一 ask/final JSON 契约
- [ ] 唯一区别：
  - prompt 中额外增加：
    - `retrieved_documents`

- [ ] `search_report.search_metadata` 建议增加：
  - `backend = "llm_text_rag"`
  - `retrieved_doc_ids`
  - `retrieved_disease_names`
  - `retrieval_query`

## 6. KG RAG 基线 Checklist

### 6.1 数据来源

- [x] 第一版直接连接当前活跃搜索图谱 Neo4j，不单独维护另一套 KG 数据副本
- [x] 第一版优先复用 [brain/retriever.py](/Users/loki/Workspace/GraduationDesign/brain/retriever.py) 的现有检索能力做轻量封装
  - `retrieve_candidate_evidence_profile()`
  - `retrieve_r2_expected_evidence()`
  - 必要时补一个更薄的 baseline 专用适配层
- [x] 不直接复用 [brain/service.py](/Users/loki/Workspace/GraduationDesign/brain/service.py) 的主搜索、repair、acceptance 主链

### 6.2 检索结果形态

- [x] 每轮返回的 KG 上下文优先组织成“可直接喂给 LLM 的证据块”，而不是整段 Cypher 原始结果
- [x] 每条证据块建议至少包含：
  - `node_id`
  - `name`
  - `label`
  - `relation_type`
  - `question_type_hint`
  - `evidence_cost`
  - `priority`
- [ ] 如有必要，可额外按 `symptom / risk / detail / lab / imaging / pathogen` 分组，减少 prompt 噪声

### 6.3 检索策略

- [x] 第一版 skeleton 当前优先围绕 `session.last_model_top3` 拉取候选疾病画像与待验证证据；首轮若尚无稳定 top3，允许暂时不注入 KG 上下文
- [x] 当前实现虽然也会像 `text_rag` 一样构造 `retrieval_query`，但 [baselines/kg_rag_retriever.py](/Users/loki/Workspace/GraduationDesign/baselines/kg_rag_retriever.py) 目前并不会直接用这段 query 文本去图谱里做“症状 -> 疾病”反查
  - 现有主链路仍是：`last_model_top3 -> candidate profile / expected evidence -> prompt`
  - 因此“对话里已经出现了哪些症状”目前只会间接影响 LLM 自己的 top3，不会像 `text_rag` 那样直接参与 KG 检索
- [ ] 每轮 KG query 来源可优先使用：
  - 当前全部对话历史
  - 当前模型 top3 候选名
  - 当前病例闭集 disease scope
  - 已明确的阳性主诉或高价值线索
- [ ] 前几轮候选尚不稳定时，优先拉“候选疾病画像 + 高优先级待验证证据”
- [ ] top3 已相对稳定后，优先拉“区分当前候选的关键证据”
- [ ] 每轮给模型的 KG 上下文同样只保留 top-k，避免把图谱检索结果整包灌进 prompt

### 6.3A 症状反查候选疾病插件（已实现，可选启用）

- [x] 可行性结论：该方案可行，且建议作为 `kg_rag` 的可插拔辅助召回模块，而不是替代当前“围绕 top3 候选疾病拉证据”的主链
  - 现有 [brain/retriever.py](/Users/loki/Workspace/GraduationDesign/brain/retriever.py) 已经具备 `retrieve_r1_candidates()`，本质上就是“特征 / 症状 -> 候选疾病”的反向召回能力
  - 现有 [brain/entity_linker.py](/Users/loki/Workspace/GraduationDesign/brain/entity_linker.py) 与 [brain/med_extractor.py](/Users/loki/Workspace/GraduationDesign/brain/med_extractor.py) 已经提供了“患者原话 -> 归一化特征 -> 图谱节点”这条链路
  - 真正缺的不是图谱检索能力，而是 baseline 侧尚未维护“目前已知的所有症状 / 风险 / 检查线索”的轻量状态
  - 当前搜索图谱里直接可稳定返回的是 `Disease` 候选，而不是 benchmark 意义上的“病例实例”；因此 prompt 中应表述为“候选疾病列表”，不要伪装成“匹配到的病例”

- [x] baseline 已新增专用轻量状态，而不是把主系统整条会话状态机搬进外部 baseline
  - 当前落点是 [baselines/llm_baseline_types.py](/Users/loki/Workspace/GraduationDesign/baselines/llm_baseline_types.py) 的 `BaselineSessionState.observed_features`
  - 作用：维护当前 baseline 会话里已明确提到、且值得用于图谱反查的特征集合
  - 当前保存：`normalized_name / mention_state / node_id / canonical_name / similarity / source_turn / source_kind`

- [x] 已把“已知特征”的来源拆成两条，避免实现一上来就过重
  - 路径 A：对 opening 或信息量较大的患者自由文本，复用 `MedExtractor + EntityLinker`
  - 路径 B：对“上一轮刚问过某个 target，患者给出明确肯定回答”的短答，直接把 `target_name` 写入轻量状态
  - 当前第一版优先纳入：`present` 且 `is_trusted=true` 的 opening / free-text 特征，以及明确肯定短答
  - 当前先聚焦 `symptom / risk / lab / imaging / pathogen`，`unclear` 与弱链接结果默认不参与症状反查

- [x] 新模块已设计成 `KgRagRetriever.query()` 内的可选第二分支，而不是改写现有 disease-centric 分支
  - 分支 1：保留当前逻辑，继续围绕 `session.last_model_top3` 拉 `candidate profile + expected evidence`
  - 分支 2：当开关开启时，使用 `BaselineSessionState.observed_features` 中的已知特征，通过 `retrieve_r1_candidates()` 反查候选疾病
  - 两个分支最终共同注入 prompt，但不要混为一种数据结构再让字段语义变糊

- [x] 症状反查 disease recall 结果已使用单独字段，不直接塞进 `retrieved_kg_context`
  - 当前新增：`retrieved_kg_candidate_diseases`
  - 当前新增：`retrieved_kg_candidate_disease_total`
  - 当前新增：`retrieved_kg_candidate_disease_has_more`
  - 当前新增：`retrieved_kg_candidate_disease_notice`
  - 单条 item 当前包含：
    - `disease_name`
    - `score`
    - `matched_features`
    - `evidence_names`
    - `retrieval_mode = "symptom_candidate_recall"`
  - 原因：`retrieved_kg_context` 当前语义是“候选疾病周围的证据块”，如果直接混入 `Disease` 候选，prompt 会同时出现“病”和“病的证据节点”，阅读负担会明显上升

- [x] 当前已实现数量控制
  - 若反查得到的候选疾病 `<= top_k`，则全部返回
  - 若 `> top_k`，只返回按 `semantic_score` 排序后的前 `top_k` 个
  - 同时补充：
    - `retrieved_kg_candidate_disease_total = 实际总数`
    - `retrieved_kg_candidate_disease_has_more = true`
    - `retrieved_kg_candidate_disease_notice = "基于当前已知症状从图谱反查到更多候选疾病，当前仅展示前 N 个。"`

- [x] 当前已实现排序与过滤
  - 直接复用 `retrieve_r1_candidates()` 当前的 `semantic_score`
  - 按 `score DESC, disease_name ASC` 排序
  - 若当前 baseline 已启用 closed-set disease scope，会先把症状反查结果限制在当前病例集对应的 disease scope 内，减少 prompt 被 scope 外疾病污染
  - 对与 `session.last_model_top3` 完全重复的 disease，当前保留并显式标记 `already_in_top3=true`

- [x] 当前已实现最小触发策略
  - 增加布尔开关：`enable_symptom_candidate_recall = false`
  - 当前默认关闭，只在对照实验中显式开启
  - 当前开启后要求已知阳性特征数量 `>= 2`
  - 暂未根据“disease-centric 证据块过少”做自适应触发，保持第一版足够轻量

- [x] 当前实现遵守边界约束
  - 症状反查结果只作为 prompt 参考，不直接覆盖 `last_model_top3`
  - 不把该模块变成主系统的 `A1 -> R1 -> R2 -> MCTS` 缩小版
  - 不在该模块里引入 `repair / acceptance / reward_model`
  - 若本轮没有可信的已知特征，模块返回空列表，而不是退化成全文模糊搜索

- [ ] 风险与代价评估
  - 常见症状如“发热 / 咳嗽 / 头痛”会召回很多泛化疾病，若不过滤会明显放大 prompt 噪声
  - baseline 当前没有主系统那种完整的会话级证据状态，因此短答解析若做得太弱，会把“没听医生提过”这类不确定回答错误当成阴性
  - 若 disease-centric 分支与 symptom-centric 分支都把同一类疾病反复注入，LLM 可能被“重复出现次数”而不是“证据质量”带偏

- [ ] 推荐实现顺序
  - 第一步：只做 `opening + 明确肯定短答` 的已知特征维护，不处理复杂否定和跨句改写
  - 第二步：接入 `retrieve_r1_candidates()`，得到 `retrieved_kg_candidate_diseases`
  - 第三步：加上 `top10 + has_more + notice`
  - 第四步：再考虑是否把 `risk / lab / imaging / pathogen` 一并纳入反查特征源

### 6.4 KG RAG prompt 注入

- [x] `LLM + KG RAG` 与 `纯 LLM` 共用同一 ask/final JSON 契约
- [x] 唯一区别：
  - prompt 中额外增加：
    - `retrieved_kg_context`
- [x] 若启用“症状反查候选疾病”插件，当前会额外增加：
  - `retrieved_kg_candidate_diseases`
  - `retrieved_kg_candidate_disease_total`
  - `retrieved_kg_candidate_disease_has_more`
  - `retrieved_kg_candidate_disease_notice`
- [x] `search_report.search_metadata` 建议增加：
  - `backend = "llm_kg_rag"`
  - `retrieved_node_ids`
  - `retrieved_disease_names`
  - `retrieval_query`
  - `retrieval_mode`
- [x] 若启用插件，`search_report.search_metadata / final_report.metadata` 当前还会增加：
  - `retrieved_candidate_disease_names_from_symptoms`
  - `retrieved_candidate_disease_count_from_symptoms`
  - `retrieved_candidate_disease_total_from_symptoms`
  - `retrieved_candidate_disease_has_more_from_symptoms`
  - `retrieved_candidate_disease_notice_from_symptoms`

### 6.5 边界约束

- [x] KG RAG baseline 的 ask / final 决策仍由 baseline LLM 给出
- [x] 不把主系统的 `MCTS / reward / verifier / repair / acceptance` 作为外部 baseline 的内部组件
- [x] 若运行环境没有 Neo4j，会显式报出依赖缺失，而不是静默退化成纯 LLM

## 7. run_baseline_replay.py Checklist

### 7.1 CLI 参数

- [x] `--baseline-mode`
  - 当前已支持：
    - `pure_llm`
    - `text_rag`
    - `kg_rag`

- [x] `--cases-file`
- [x] `--output-root`
- [x] `--max-turns`
- [x] `--case-concurrency`
- [x] `--api-error-retries`
- [x] `--rag-corpus-file`
- [x] `--retrieval-top-k`
- [x] 若将症状反查做成显式可插拔模块，当前已增加：
  - `--kg-enable-symptom-candidate-recall`
  - `--kg-symptom-candidate-top-k`

说明：

- `--rag-corpus-file` 主要服务于 `text_rag`
- `kg_rag` 第一版优先继续复用现有 Neo4j 环境变量与连接配置，不急于暴露一组新的 CLI 参数
- 若只在实验阶段启用症状反查插件，优先把它作为 `kg_rag` 的可选参数或配置开关，而不是默认行为

### 7.2 运行逻辑

- [x] 启动时先复用当前 batch runner 的环境引导
  - 先加载 `configs/frontend.yaml / configs/frontend.local.yaml`
  - 再把本机 LLM 配置写回环境变量
  - 然后再创建 worker 级 `LlmClient`
- [x] 复用当前 worker 级 `LlmClient`
- [x] 复用 `VirtualPatientAgent(use_llm=True, llm_client=shared_client)`
- [x] 复用 `ReplayEngine`
- [x] 写出与 `run_batch_replay.py` 相同格式的结果文件
- [x] `text_rag` worker 级缓存文本 corpus 与稀疏检索索引
- [x] `kg_rag` worker 级缓存 Neo4j client 与 KG retriever
- [x] `benchmark_summary.json` 应继续包含：
  - `eligible_summary`
  - `case_qc_status_summaries`
  - `benchmark_qc_status_summaries`
  - `case_type_summaries`
  - `timing_summary`
- [x] `status.json` 应继续包含运行进度、完成计数、失败计数与 timing 汇总

### 7.3 输出目录建议

- [ ] `test_outputs/simulator_replay/benchmark_external_baselines/pure_llm/...`
- [ ] `test_outputs/simulator_replay/benchmark_external_baselines/text_rag/...`
- [ ] `test_outputs/simulator_replay/benchmark_external_baselines/kg_rag/...`

## 8. 测试与验证 Checklist

### 8.1 单元测试

- [x] `llm_consultation_brain` 能正确：
  - ask
  - final
  - 8 轮强制 finalize

- [x] `text_rag_retriever` 能正确：
  - 加载 corpus
  - 返回 top-k

- [x] `llm_text_rag_consultation_brain` 能正确：
  - 注入 `retrieved_documents`
  - 保持与 pure LLM 相同的 ask / final 契约

- [x] `kg_rag_retriever` 能正确：
  - 返回候选疾病画像或关键待验证证据
  - 对 Neo4j 不可用给出清晰错误
- [x] 症状反查插件已补充验证：
  - opening 中的阳性症状能被写入 baseline 轻量状态
  - 明确肯定短答能把上一轮 `target_name` 写入已知特征集合
  - `retrieve_r1_candidates()` 返回的疾病能被裁剪到前 `10` 个
  - 当总数 `> 10` 时，`has_more / total / notice` 正确落盘
  - 启用 closed-set disease scope 时，scope 外 disease 不会进入最终 prompt

- [x] `llm_kg_rag_consultation_brain` 能正确：
  - 注入 `retrieved_kg_context`
  - 保持与 pure LLM 相同的 ask / final 契约
- [x] 症状反查插件已补充验证：
  - prompt 中能额外看到 `retrieved_kg_candidate_diseases`
  - `search_report.search_metadata` 与 `final_report.metadata` 中能保留该字段及其计数信息

- [x] `run_baseline_replay.py` 能正确输出：
  - `replay_results.jsonl`
  - `benchmark_summary.json`

### 8.2 smoke 顺序

- [ ] 先跑极小 smoke
  - `pure_llm limit=5`
  - `text_rag limit=5`
  - `kg_rag limit=5`

- [ ] 再跑 balanced smoke
  - `text_rag smoke20`
  - `kg_rag smoke20`
  - 如有需要再扩大到 `smoke60`

- [ ] 最后再跑：
  - 稳定路线的 `full227`

说明：

- `kg_rag` 的 smoke / full replay 依赖真实 Neo4j
- `text_rag` 不依赖 Neo4j，更适合作为第一条检索增强路线先跑通

## 9. 推荐实现顺序

- [ ] 第一步：实现 `baselines/llm_consultation_brain.py`
- [ ] 第二步：实现 `scripts/run_baseline_replay.py`
- [ ] 第三步：先跑 `pure_llm` 的 `smoke5`
- [ ] 第四步：补 `final_report` 契约细节，确保 benchmark 指标都能正常出
- [ ] 第五步：实现 `build_text_rag_corpus.py`
- [ ] 第六步：实现 `text_rag_retriever.py`
- [ ] 第七步：实现 `llm_text_rag_consultation_brain.py`
- [ ] 第八步：跑 `text_rag` 的 `smoke5 / smoke20`
- [ ] 第九步：实现 `kg_rag_retriever.py`
- [ ] 第十步：实现 `llm_kg_rag_consultation_brain.py`
- [ ] 第十一步：跑 `kg_rag` 的 `smoke5 / smoke20`
- [ ] 第十二步：三条外部基线都稳定后，再跑 `full227`

## 10. 当前推荐的最小可交付版本

如果时间非常紧，建议先完成下面这组最小闭环：

- [ ] `pure_llm`
- [ ] `text_rag`
- [ ] `kg_rag`
- [ ] 三条路线各自 `smoke5`
- [ ] 至少 `text_rag / kg_rag` 各自 `smoke20`

这样你就已经能得到一组可写进论文、且知识来源差异清楚的外部 baseline 对照。

如果时间再紧一档，则最低优先级的缩减顺序建议是：

- [ ] 先不做 `full227`
- [ ] 只保留 `kg_rag smoke5`
- [ ] 但尽量保留 `text_rag smoke20`

## 11. 当前不纳入本轮的向量 RAG

- [x] 当前不把 `LLM + 向量 RAG` 作为第四条并行主线
- [ ] 只有在 `text_rag` 与 `kg_rag` 都已经稳定后，再评估是否值得补做
- [ ] 若后续要补，至少还需要额外明确：
  - 文本 chunk 策略
  - embedding 模型与缓存策略
  - 向量索引持久化
  - 查询改写与结果去重
  - 如何与当前 `text_rag` 公平比较

## 12. 与当前 benchmark 文档的关系

- [x] 当前主系统内部消融仍以 [diagnosis_benchmark_experiment_design.md](/Users/loki/Workspace/GraduationDesign/docs/diagnosis_benchmark_experiment_design.md) 为主
- [x] 本文档只负责外部基线的开发与实现清单
- [x] 外部基线真正开始实现后，应把运行命令与结果摘要再回填到主 benchmark 文档
- [x] 后续结果表建议至少区分：
  - `pure_llm`
  - `llm_text_rag`
  - `llm_kg_rag`
