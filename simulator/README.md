# simulator

`simulator/` 负责虚拟病人、自动回放、benchmark 汇总、图谱病例生成与审计工具链。当前实现已经按运行职责拆成多层子包，顶层旧模块名继续保留为兼容壳，方便现有脚本和测试不改入口继续运行。

## 新目录结构

```text
simulator/
├── app/              # 稳定门面与统一导出
├── cases/            # 病例 schema、seed cases、IO、图谱病例生成
├── patient/          # 开场生成、槽位匹配、检查上下文回答、LLM 草稿
├── replay/           # 单病例回放 runtime、结果类型、分析、JSONL 导出
├── benchmarking/     # benchmark 摘要、cohort 报表、异常病例汇总
├── audit/            # 疾病图谱审计与差异证据报告
├── catalog/          # evidence family 分类与最低证据组建议
├── cache/            # 路径缓存构建
├── shared/           # 轻量纯函数工具
├── case_schema.py    # 兼容壳 -> cases/schema.py
├── patient_agent.py  # 兼容壳 -> patient/
├── replay_engine.py  # 兼容壳 -> replay/
├── benchmark.py      # 兼容壳 -> benchmarking/
└── ...
```

## 主运行链

单病例自动对战主链现在固定为：

1. `VirtualPatientAgent.open_case(case)` 生成 opening。
2. `ReplayEngine.run_case(case)` 启动回放会话。
3. `brain.start_session(session_id)` 建立问诊会话。
4. `brain.process_turn(session_id, opening_text)` 处理首轮主诉。
5. 若返回 `next_question + pending_action`，则进入问答循环：
   - `VirtualPatientAgent.answer_question(...)`
   - `brain.process_turn(session_id, answer_text)`
   - `ReplayRuntime` 记录 `ReplayTurn`、timing 与 turn analysis 字段
6. 若 brain 已给出 `final_report`，立即完成病例；否则达到上限后调用 `brain.finalize(session_id)`。
7. `benchmarking.summarize_benchmark()`、`build_non_completed_case_report()`、`build_benchmark_cohort_summary()` 对 replay 结果做批量汇总。

更详细的逐函数调用链、调试索引和旧新模块映射见：

- [simulator_runtime_call_chain_guide.md](/Users/loki/Workspace/GraduationDesign/docs/simulator_runtime_call_chain_guide.md)

## 稳定入口

推荐优先使用这些稳定入口：

- 包级门面：
  - [simulator/__init__.py](/Users/loki/Workspace/GraduationDesign/simulator/__init__.py)
  - [simulator/app/__init__.py](/Users/loki/Workspace/GraduationDesign/simulator/app/__init__.py)
- 旧路径兼容入口：
  - [simulator/case_schema.py](/Users/loki/Workspace/GraduationDesign/simulator/case_schema.py)
  - [simulator/generate_cases.py](/Users/loki/Workspace/GraduationDesign/simulator/generate_cases.py)
  - [simulator/patient_agent.py](/Users/loki/Workspace/GraduationDesign/simulator/patient_agent.py)
  - [simulator/replay_engine.py](/Users/loki/Workspace/GraduationDesign/simulator/replay_engine.py)
  - [simulator/benchmark.py](/Users/loki/Workspace/GraduationDesign/simulator/benchmark.py)
  - [simulator/graph_case_generator.py](/Users/loki/Workspace/GraduationDesign/simulator/graph_case_generator.py)
  - [simulator/graph_audit.py](/Users/loki/Workspace/GraduationDesign/simulator/graph_audit.py)
  - [simulator/evidence_family_catalog.py](/Users/loki/Workspace/GraduationDesign/simulator/evidence_family_catalog.py)
  - [simulator/path_cache_builder.py](/Users/loki/Workspace/GraduationDesign/simulator/path_cache_builder.py)

这些顶层模块现在只负责稳定导出；真实实现已经迁到对应子包。

## 四条子链

### 1. 病例生成链

- [simulator/cases/schema.py](/Users/loki/Workspace/GraduationDesign/simulator/cases/schema.py)：定义 `VirtualPatientCase`、`SlotTruth`
- [simulator/cases/seed_cases.py](/Users/loki/Workspace/GraduationDesign/simulator/cases/seed_cases.py)：内置 seed cases
- [simulator/cases/io.py](/Users/loki/Workspace/GraduationDesign/simulator/cases/io.py)：`load_cases_jsonl()`、`write_cases_json()`、`write_cases_jsonl()`
- [simulator/cases/graph_generator.py](/Users/loki/Workspace/GraduationDesign/simulator/cases/graph_generator.py)：图谱审计结果 -> 结构化病例骨架

### 2. 虚拟病人链

- [simulator/patient/agent.py](/Users/loki/Workspace/GraduationDesign/simulator/patient/agent.py)：稳定门面
- [simulator/patient/runtime.py](/Users/loki/Workspace/GraduationDesign/simulator/patient/runtime.py)：`open_case()`、`answer_question()`
- [simulator/patient/opening.py](/Users/loki/Workspace/GraduationDesign/simulator/patient/opening.py)：opening truth 收集与渲染
- [simulator/patient/matching.py](/Users/loki/Workspace/GraduationDesign/simulator/patient/matching.py)：槽位匹配与 exam context 处理
- [simulator/patient/replies.py](/Users/loki/Workspace/GraduationDesign/simulator/patient/replies.py)：truth / hidden / unknown 回复渲染
- [simulator/patient/llm.py](/Users/loki/Workspace/GraduationDesign/simulator/patient/llm.py)：受约束 LLM 草稿生成

### 3. 回放与评测链

- [simulator/replay/engine.py](/Users/loki/Workspace/GraduationDesign/simulator/replay/engine.py)：稳定门面
- [simulator/replay/runtime.py](/Users/loki/Workspace/GraduationDesign/simulator/replay/runtime.py)：单病例回放编排
- [simulator/replay/analysis.py](/Users/loki/Workspace/GraduationDesign/simulator/replay/analysis.py)：turn observation、case analysis、timing 归一化
- [simulator/replay/types.py](/Users/loki/Workspace/GraduationDesign/simulator/replay/types.py)：`ReplayTurn`、`ReplayResult`、`ReplayConfig`
- [simulator/benchmarking/metrics.py](/Users/loki/Workspace/GraduationDesign/simulator/benchmarking/metrics.py)：`summarize_benchmark()`
- [simulator/benchmarking/reports.py](/Users/loki/Workspace/GraduationDesign/simulator/benchmarking/reports.py)：异常病例、cohort summary、analysis summary

### 4. 生成前置工具链

- [simulator/audit/graph_audit.py](/Users/loki/Workspace/GraduationDesign/simulator/audit/graph_audit.py)：疾病图谱审计
- [simulator/catalog/evidence_family_catalog.py](/Users/loki/Workspace/GraduationDesign/simulator/catalog/evidence_family_catalog.py)：evidence family 分类
- [simulator/cache/path_cache_builder.py](/Users/loki/Workspace/GraduationDesign/simulator/cache/path_cache_builder.py)：路径缓存占位构建器

## 常用脚本

- [scripts/run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)：标准 batch replay 入口
- [scripts/run_baseline_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_baseline_replay.py)：外部 baseline replay 入口
- [scripts/run_single_case_smoke.py](/Users/loki/Workspace/GraduationDesign/scripts/run_single_case_smoke.py)：单病例快速烟雾验证
- [scripts/generate_graph_virtual_patients.py](/Users/loki/Workspace/GraduationDesign/scripts/generate_graph_virtual_patients.py)：图谱病例生成入口
- [scripts/audit_disease_ego_graphs.py](/Users/loki/Workspace/GraduationDesign/scripts/audit_disease_ego_graphs.py)：疾病级图谱审计
- [scripts/audit_differential_pairs.py](/Users/loki/Workspace/GraduationDesign/scripts/audit_differential_pairs.py)：竞争病差异审计

## 当前兼容原则

- `ReplayResult`、`ReplayTurn`、benchmark summary 的字段名保持不变。
- `VirtualPatientAgent`、`ReplayEngine`、`VirtualPatientCase` 等核心入口行为保持不变。
- `scripts/`、`tests/`、病例 `JSON/JSONL`、benchmark 产物 schema 保持兼容。
- 顶层平铺模块继续可 import，但内部新实现统一走子包路径。
