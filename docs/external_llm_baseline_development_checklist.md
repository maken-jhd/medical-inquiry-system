# 外部 LLM 基线开发 Checklist（2026-05-07）

本文档用于规划两类外部基线实验的实现路径：

1. `纯 LLM` 多轮问诊基线
2. `LLM + 文本 RAG` 多轮问诊基线

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

- [x] 目标 3：复用现有 benchmark 汇总
  - 输出目录结构仍兼容：
    - `replay_results.jsonl`
    - `benchmark_summary.json`
    - `non_completed_cases.json`
    - `status.json`

- [x] 当前明确不做
  - 不直接复用当前 `brain/service.py` 的图谱搜索与 repair 主链路
  - 不把图谱检索伪装成“文本 RAG”
  - 不单独维护另一套不兼容的 benchmark 统计脚本

## 1. 推荐实现总原则

- [x] 原则 1：新建 baseline doctor，而不是魔改现有 `ConsultationBrain`
  - 推荐新增目录：
    - [baselines/](/Users/loki/Workspace/GraduationDesign/baselines)

- [x] 原则 2：对 `ReplayEngine` 暴露同一最小接口
  - 必须实现：
    - `start_session(session_id)`
    - `process_turn(session_id, patient_text)`
    - `finalize(session_id)`

- [x] 原则 3：先做 `纯 LLM`，再做 `LLM + 文本 RAG`
  - `纯 LLM` 是最小 MVP
  - `文本 RAG` 建议在 `纯 LLM` smoke 跑通后再叠加

- [x] 原则 4：RAG 先做轻量版本
  - 第一版优先使用：
    - 文本化疾病画像
    - `BM25 / TF-IDF` 这类轻检索
  - 暂不要求：
    - 全量原始文档 chunk
    - embedding 检索
    - reranker

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

- [ ] 新增 [baselines/text_rag_retriever.py](/Users/loki/Workspace/GraduationDesign/baselines/text_rag_retriever.py)
  - 负责：
    - 加载文本 corpus
    - 查询
    - 返回 top-k 文本块

- [ ] 新增 [baselines/llm_rag_consultation_brain.py](/Users/loki/Workspace/GraduationDesign/baselines/llm_rag_consultation_brain.py)
  - 复用 `llm_consultation_brain`
  - 只增加检索层与检索结果注入 prompt

### 2.4 CLI 与语料构建

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

- [ ] 新增 [scripts/build_text_rag_corpus.py](/Users/loki/Workspace/GraduationDesign/scripts/build_text_rag_corpus.py)
  - 第一版负责把已有知识整理成文本化 disease profile corpus

### 2.5 测试

- [x] 新增 [tests/test_llm_consultation_brain.py](/Users/loki/Workspace/GraduationDesign/tests/test_llm_consultation_brain.py)
- [ ] 新增 [tests/test_llm_rag_consultation_brain.py](/Users/loki/Workspace/GraduationDesign/tests/test_llm_rag_consultation_brain.py)
- [ ] 新增 [tests/test_text_rag_retriever.py](/Users/loki/Workspace/GraduationDesign/tests/test_text_rag_retriever.py)
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
- [ ] 第一版优先构建“文本化 disease profile”语料
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

## 6. run_baseline_replay.py Checklist

### 6.1 CLI 参数

- [x] `--baseline-mode`
  - 当前已支持：
    - `pure_llm`
  - 后续待补：
    - `text_rag`

- [x] `--cases-file`
- [x] `--output-root`
- [x] `--max-turns`
- [x] `--case-concurrency`
- [x] `--api-error-retries`
- [ ] `--rag-corpus-file`
- [ ] `--retrieval-top-k`

### 6.2 运行逻辑

- [x] 启动时先复用当前 batch runner 的环境引导
  - 先加载 `configs/frontend.yaml / configs/frontend.local.yaml`
  - 再把本机 LLM 配置写回环境变量
  - 然后再创建 worker 级 `LlmClient`
- [x] 复用当前 worker 级 `LlmClient`
- [x] 复用 `VirtualPatientAgent(use_llm=True, llm_client=shared_client)`
- [x] 复用 `ReplayEngine`
- [x] 写出与 `run_batch_replay.py` 相同格式的结果文件
- [x] `benchmark_summary.json` 应继续包含：
  - `eligible_summary`
  - `case_qc_status_summaries`
  - `benchmark_qc_status_summaries`
  - `case_type_summaries`
  - `timing_summary`
- [x] `status.json` 应继续包含运行进度、完成计数、失败计数与 timing 汇总

### 6.3 输出目录建议

- [ ] `test_outputs/simulator_replay/benchmark_external_baselines/pure_llm/...`
- [ ] `test_outputs/simulator_replay/benchmark_external_baselines/text_rag/...`

## 7. 测试与验证 Checklist

### 7.1 单元测试

- [ ] `llm_consultation_brain` 能正确：
  - ask
  - final
  - 8 轮强制 finalize

- [ ] `text_rag_retriever` 能正确：
  - 加载 corpus
  - 返回 top-k

- [ ] `run_baseline_replay.py` 能正确输出：
  - `replay_results.jsonl`
  - `benchmark_summary.json`

### 7.2 smoke 顺序

- [ ] 先跑极小 smoke
  - `limit=5`

- [ ] 再跑 balanced smoke
  - `smoke20`
  - 或 `smoke60`

- [ ] 最后再跑：
  - `full227`

## 8. 推荐实现顺序

- [ ] 第一步：实现 `baselines/llm_consultation_brain.py`
- [ ] 第二步：实现 `scripts/run_baseline_replay.py`
- [ ] 第三步：先跑 `pure_llm` 的 `smoke5`
- [ ] 第四步：补 `final_report` 契约细节，确保 benchmark 指标都能正常出
- [ ] 第五步：实现 `build_text_rag_corpus.py`
- [ ] 第六步：实现 `text_rag_retriever.py`
- [ ] 第七步：实现 `llm_rag_consultation_brain.py`
- [ ] 第八步：跑 `text_rag` 的 `smoke5 / smoke20`
- [ ] 第九步：两组外部基线都稳定后，再跑 `full227`

## 9. 当前推荐的最小可交付版本

如果时间非常紧，建议只先完成下面这组最小闭环：

- [ ] `pure_llm`
- [ ] `run_baseline_replay.py`
- [ ] `smoke5`
- [ ] `smoke20`

这样你就已经能得到一个可写进论文的外部最低基线。

`LLM + 文本 RAG` 则作为下一阶段补充：

- [ ] `text_rag`
- [ ] `smoke20`
- [ ] `full227`

## 10. 与当前 benchmark 文档的关系

- [x] 当前主系统内部消融仍以 [diagnosis_benchmark_experiment_design.md](/Users/loki/Workspace/GraduationDesign/docs/diagnosis_benchmark_experiment_design.md) 为主
- [x] 本文档只负责外部基线的开发与实现清单
- [x] 外部基线真正开始实现后，应把运行命令与结果摘要再回填到主 benchmark 文档
