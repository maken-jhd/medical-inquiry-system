# simulator 运行链路指南

本文档按“当前真实代码”说明 `simulator/` 的主调用链，重点服务调试和代码定位，不再沿用旧的平铺模块心智模型。

## 1. 目录结构与职责

```text
simulator/
├── app/            # 稳定门面聚合
├── cases/          # 病例 schema、seed cases、IO、图谱病例生成
├── patient/        # opening、匹配、回复渲染、LLM 草稿
├── replay/         # 单病例回放 runtime、结果类型、分析与导出
├── benchmarking/   # benchmark 摘要、analysis summary、异常病例报表
├── audit/          # 疾病级图谱审计与差异证据报告
├── catalog/        # evidence family 分类与最低证据组建议
├── cache/          # 离线路径缓存
└── shared/         # 纯函数工具
```

顶层平铺模块如 [simulator/replay_engine.py](/Users/loki/Workspace/GraduationDesign/simulator/replay_engine.py) 现在只是兼容壳；真实实现已经迁到子包。

## 2. 单病例调用总图

```text
VirtualPatientAgent.open_case(case)
    -> ReplayEngine.run_case(case)
        -> brain.start_session(session_id)
        -> brain.process_turn(session_id, opening_text)
        -> while next_question:
             VirtualPatientAgent.answer_question(...)
             brain.process_turn(session_id, answer_text)
             ReplayRuntime 记录 ReplayTurn / timing / analysis
        -> brain.finalize(session_id)  # 若未提前给出 final_report
        -> ReplayResult
    -> summarize_benchmark(results)
    -> build_non_completed_case_report(results)
    -> build_benchmark_cohort_summary(results)
```

## 3. 从病人开场到诊断结束的真实函数链

下面以 `scripts/run_single_case_smoke.py` 或 `scripts/run_batch_replay.py` 驱动的一条病例为例说明。

### 3.1 病例装载

1. 病例通常先通过 [simulator/cases/io.py](/Users/loki/Workspace/GraduationDesign/simulator/cases/io.py) 的 `load_cases_jsonl()` 读入。
2. 反序列化时 `_deserialize_case()` 会把持久化字典还原成 `VirtualPatientCase` 和 `SlotTruth`。

### 3.2 虚拟病人 opening

1. 外部通过 [simulator/patient/agent.py](/Users/loki/Workspace/GraduationDesign/simulator/patient/agent.py) 的 `VirtualPatientAgent.open_case(case)` 调用稳定入口。
2. 门面把请求委托给 [simulator/patient/runtime.py](/Users/loki/Workspace/GraduationDesign/simulator/patient/runtime.py) 的 `VirtualPatientRuntime.open_case()`。
3. `open_case()` 内部依次调用：
   - [simulator/patient/opening.py](/Users/loki/Workspace/GraduationDesign/simulator/patient/opening.py) `collect_opening_truths()`
   - `render_opening()`
4. 如果骨架里存在 `reveal_only_if_asked=False` 的阳性 truth，会优先用这些槽位生成 opening；否则退回 `chief_complaint`，再不行用保底句子。

### 3.3 Replay 启动

1. 外部通过 [simulator/replay/engine.py](/Users/loki/Workspace/GraduationDesign/simulator/replay/engine.py) 的 `ReplayEngine.run_case(case)` 调用稳定入口。
2. 门面把请求委托给 [simulator/replay/runtime.py](/Users/loki/Workspace/GraduationDesign/simulator/replay/runtime.py) 的 `ReplayRuntime.run_case()`。
3. `run_case()` 会先：
   - 构造 `session_id = replay::<case_id>`
   - 调用 [simulator/replay/analysis.py](/Users/loki/Workspace/GraduationDesign/simulator/replay/analysis.py) `extract_case_benchmark_fields()`
   - 调用 `_build_pending_result()` 初始化 `ReplayResult`
   - 调用 `_start_case_session()`，内部执行 `brain.start_session(session_id)`

### 3.4 opening 进入 brain

1. `ReplayRuntime._run_opening()` 调用 `patient_agent.open_case(case)` 取回 `PatientOpening`。
2. 同一函数内直接执行 `brain.process_turn(session_id, opening.opening_text)`。
3. 这一轮的输出会写入 `ReplayResult.initial_output`，并记录：
   - `timing.opening_seconds`
   - `timing.initial_brain_seconds`
   - `opening_text`
   - `opening_revealed_slot_ids`

### 3.5 问答循环

只要 brain 返回了 `next_question` 和 `pending_action.target_node_id`，就进入正式循环：

1. `ReplayRuntime.run_case()` 读取：
   - `question_text = current_output["next_question"]`
   - `question_node_id = current_output["pending_action"]["target_node_id"]`
2. 调用 `ReplayRuntime._run_single_turn()`。
3. `_run_single_turn()` 内部先执行 `patient_agent.answer_question(question_node_id, question_text, case)`。
4. `VirtualPatientRuntime.answer_question()` 内部依次分流：
   - [simulator/patient/matching.py](/Users/loki/Workspace/GraduationDesign/simulator/patient/matching.py) `render_exam_context_reply()`
   - `resolve_truth_with_fallback()`
   - 若命中 hidden slot，走 [simulator/patient/replies.py](/Users/loki/Workspace/GraduationDesign/simulator/patient/replies.py) `render_hidden_reply()`
   - 若完全未知，走 `render_unknown_reply()`
   - 若命中 truth，走 `render_truth()`
5. 拿到 `PatientReply` 后，`_run_single_turn()` 调用 `brain.process_turn(session_id, reply.answer_text)`。
6. 然后调用 [simulator/replay/analysis.py](/Users/loki/Workspace/GraduationDesign/simulator/replay/analysis.py)：
   - `build_turn_observation()`
   - `extract_turn_search_report()`
   - `extract_turn_search_metadata()`
7. 最后把结果写成一个 `ReplayTurn`，并通过 `_accumulate_turn_timing()` 更新 timing 聚合。

### 3.6 完成或兜底 finalize

1. 如果任意一轮 `brain.process_turn()` 已返回 `final_report`，`ReplayRuntime._complete_result()` 会立即收口。
2. `_complete_result()` 会：
   - 写入 `result.final_report`
   - 设置 `result.status`
   - 调用 `build_case_analysis(case, result)`
   - 调用 `_finalize_timing()`
3. 如果问答循环结束仍没有 `final_report`，则 `run_case()` 执行 `brain.finalize(session_id)`，并把状态记成 `max_turn_reached`。
4. 如果中间抛出 `BrainDomainError` 或普通 Python 异常，则统一落成 `status="failed"` 和结构化 `error`。

## 4. 批量评测链

单病例回放完成后，批量脚本通常会继续调用：

1. [simulator/benchmarking/metrics.py](/Users/loki/Workspace/GraduationDesign/simulator/benchmarking/metrics.py) `summarize_benchmark(results)`
2. [simulator/benchmarking/reports.py](/Users/loki/Workspace/GraduationDesign/simulator/benchmarking/reports.py) `build_non_completed_case_report(results)`
3. `build_benchmark_cohort_summary(results)`
4. `build_replay_analysis_summary(results)`

这些函数不会改 `ReplayResult` schema，而是围绕 `ReplayResult` / `ReplayTurn` 追加批量摘要。

## 5. 关键运行时对象

- `VirtualPatientCase`
  - 单条病例骨架，包含 `slot_truth_map / hidden_slots / metadata`
- `SlotTruth`
  - 单个槽位真值，决定病人回答时的事实边界
- `PatientOpening`
  - 首轮开场文本与 opening 已暴露的槽位 ID
- `PatientReply`
  - 单轮病人回答文本、命中槽位和回答置信度
- `ReplayConfig`
  - 当前主要控制 `max_turns`
- `ReplayTurn`
  - 单轮问答、action 语义、truth hit、timing 的稳定记录
- `ReplayResult`
  - 单病例 replay 最终结果对象
- `BenchmarkSummary`
  - 批量 benchmark 核心摘要

## 6. Debug 索引

### 6.1 opening 不准

先看：

- [simulator/patient/opening.py](/Users/loki/Workspace/GraduationDesign/simulator/patient/opening.py)
- [simulator/patient/runtime.py](/Users/loki/Workspace/GraduationDesign/simulator/patient/runtime.py) `open_case()`

重点检查：

- `reveal_only_if_asked`
- `metadata.opening_slot_ids`
- opening LLM 是否把关键检查锚点压缩丢失

### 6.2 truth 不命中

先看：

- [simulator/patient/matching.py](/Users/loki/Workspace/GraduationDesign/simulator/patient/matching.py)
- [simulator/patient/llm.py](/Users/loki/Workspace/GraduationDesign/simulator/patient/llm.py)

重点检查：

- `question_node_id` 是否能直接命中 `slot_truth_map`
- `aliases` 是否覆盖当前问法
- LLM semantic match 是否只在候选槽位内成功匹配

### 6.3 exam_context 空转

先看：

- `render_exam_context_reply()`
- `truth_exam_group` / `truth_is_positive`

重点检查：

- `question_node_id` 是否形如 `__exam_context__::lab`
- truth 的 `group / node_label` 是否能被识别成 `lab / imaging / pathogen`

### 6.4 max_turn_reached

先看：

- [simulator/replay/runtime.py](/Users/loki/Workspace/GraduationDesign/simulator/replay/runtime.py) `run_case()`
- `ReplayResult.turns`
- `ReplayResult.analysis`
- `benchmarking/reports.py` 里的 `_classify_non_completed_case()`

重点判断：

- 是一直没有 `next_question` 但也没有 `final_report`
- 还是候选命中了但 verifier/stop 没放行
- 还是根本没把真实病种召回进候选

### 6.5 failed case

先看：

- `ReplayResult.error`
- `ReplayRuntime.run_case()` 的异常分支
- batch 脚本里的 `_build_unexpected_case_failure_result()`

重点区分：

- `BrainDomainError`
- 普通 Python 运行时错误
- batch 外层并发调度失败

## 7. 旧模块到新结构的映射

| 旧入口 | 当前真实实现 |
|---|---|
| `simulator/case_schema.py` | `simulator/cases/schema.py` |
| `simulator/generate_cases.py` | `simulator/cases/seed_cases.py` + `simulator/cases/io.py` |
| `simulator/patient_agent.py` | `simulator/patient/agent.py` + `simulator/patient/runtime.py` |
| `simulator/replay_engine.py` | `simulator/replay/engine.py` + `simulator/replay/runtime.py` + `simulator/replay/analysis.py` |
| `simulator/benchmark.py` | `simulator/benchmarking/metrics.py` + `simulator/benchmarking/reports.py` |
| `simulator/graph_case_generator.py` | `simulator/cases/graph_generator.py` |
| `simulator/graph_audit.py` | `simulator/audit/graph_audit.py` |
| `simulator/evidence_family_catalog.py` | `simulator/catalog/evidence_family_catalog.py` |
| `simulator/path_cache_builder.py` | `simulator/cache/path_cache_builder.py` |

## 8. 调试建议

如果你现在是为了顺着代码打断点，最推荐的入口顺序是：

1. [scripts/run_single_case_smoke.py](/Users/loki/Workspace/GraduationDesign/scripts/run_single_case_smoke.py)
2. [simulator/replay/runtime.py](/Users/loki/Workspace/GraduationDesign/simulator/replay/runtime.py)
3. [simulator/patient/runtime.py](/Users/loki/Workspace/GraduationDesign/simulator/patient/runtime.py)
4. [brain/app/brain.py](/Users/loki/Workspace/GraduationDesign/brain/app/brain.py)
5. [docs/brain_runtime_call_chain_guide.md](/Users/loki/Workspace/GraduationDesign/docs/brain_runtime_call_chain_guide.md)
