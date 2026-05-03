# simulator

`simulator/` 目录用于承载“虚拟病人生成、自动对战、离线评测与路径缓存”相关代码。它对应整个项目规划中的“虚拟患者数据集与离线预演”部分，是第二阶段问诊大脑的重要评测环境。

这个目录的目标不是替代真实临床资料，而是为问诊策略提供一个可重复、可量化、可自动化回放的测试场。

## 目录职责

`simulator/` 当前主要承担以下几类任务：

- 定义结构化虚拟病例格式
- 生成虚拟病例数据
- 模拟病人与问诊系统之间的问答交互
- 对问诊路径进行离线回放与评测
- 为未来离线路径缓存做数据准备

## 当前文件说明

### 1. 病例结构与生成

- [case_schema.py](/Users/loki/Workspace/GraduationDesign/simulator/case_schema.py)
  - 定义虚拟病人的结构化数据格式。
  - 当前病例统一遵循这里约束的字段，例如真实病情、行为风格、槽位真值、主动暴露槽位、隐藏槽位等。

- [generate_cases.py](/Users/loki/Workspace/GraduationDesign/simulator/generate_cases.py)
  - 用于生成虚拟病例数据集。
  - 当前已经内置一批覆盖 PCP、结核、急性 HIV、慢病共病、孕产期和隐瞒风险史等场景的 seed cases。
  - 当前已支持把病例写出/读回 `JSONL` 和 `JSON` 数组，便于批量回放和人工查看。

- [graph_case_generator.py](/Users/loki/Workspace/GraduationDesign/simulator/graph_case_generator.py)
  - 用于把疾病级图谱审计结果转换成图谱驱动的虚拟病人病例骨架。
  - 当前支持 `ordinary / low_cost / exam_driven / competitive` 四类病例。
  - 当前会优先读取 `disease_minimum_evidence_groups.json`，用 full-evidence catalog 为每个疾病提供最低 evidence family 约束；catalog 缺失或 disease_id 不命中时，才回退到内置 PCP / IRIS / CNS / 代谢类规则。
  - 生成病例时会先按 catalog family 约束选择阳性槽位，再按原 priority / specificity 补满；如果 catalog 要求的 family 不存在于当前 disease audit 证据池，会记录到 `benchmark_catalog_unavailable_family_groups`，不把不可选证据计入病例 QC 缺失项。
  - 当前会为病例生成 benchmark evidence-family QC 元数据，把证据映射到 `immune_status / respiratory_symptom / imaging / oxygenation / fungal_marker / pathogen / neurologic_symptom / art_or_reconstitution / worsening / metabolic_definition` 等可审计证据族。
  - 竞争病例的阴性槽位会过滤与目标病名称、目标病核心定义或已选阳性互斥 family 冲突的证据，避免把目标病本身写成阴性。
  - `competitive` 病例当前会主动过滤 `HIV感染 / HIV感染者 / 抗逆转录病毒治疗 / 免疫功能低下` 这类背景风险 opening，并优先回退到目标病自己的症状、具体检查结果或疾病名，避免把背景信息直接渲染成主诉。
  - 会同时输出 `cases.jsonl`、`cases.json`、`manifest.json` 和 `summary.md`。

- [evidence_family_catalog.py](/Users/loki/Workspace/GraduationDesign/simulator/evidence_family_catalog.py)
  - 用于把 Neo4j 中的证据节点归入可审计 evidence family。
  - 当前支持 symptom、risk、detail、lab、imaging、pathogen 六个证据大组；症状侧覆盖呼吸、神经、全身、消化、皮肤黏膜、口腔耳鼻咽喉、淋巴、泌尿生殖、肌肉骨骼、心血管、血液/出血、眼部、代谢、精神心理、免疫状态、病情恶化和重症线索等 family。
  - 检查侧覆盖 CD4 / viral load / oxygenation / fungal marker / CNS lab / disease-specific lab / serology / pathology / pulmonary imaging / CNS imaging / pathogen subtype 等 family；risk/detail 侧覆盖 ART、基础感染、暴露风险、用药风险、合并症、病程时间、严重度和治疗反应等 family。
  - 会基于每个疾病关联到的 evidence family，生成 symptom-only 和 full-evidence 两种 `minimum_evidence_groups` 建议；当前结果用于人工检查和后续接入病例生成器，不直接改变 `brain` 推理逻辑。

### 2. 病人代理

- [patient_agent.py](/Users/loki/Workspace/GraduationDesign/simulator/patient_agent.py)
  - 负责模拟“虚拟病人如何回答问题”。
  - 当前已支持根据病例骨架中的 opening slots 生成首轮开场，并在问答中遵循“未被问到不主动透露、敏感信息可回避、未知项不乱答”的行为规则。
  - 当前 LLM 生成 opening 时会校验检查类关键锚点；如果把 `CD4 < 200`、`HIV RNA阳性`、具体病原体名或影像异常压缩成笼统“异常/偏低”，会退回规则模板，保证首轮证据更容易被 brain 链接进图谱。
  - 当前已支持 `__exam_context__::general/lab/imaging/pathogen` 检查上下文回答：`general` 会汇总 lab / imaging / pathogen 槽位，具体类型只汇总对应槽位；优先回答最多 3 条阳性检查结果，没有阳性但有阴性时回答最多 3 条阴性结果，无相关检查槽位时才回到 unknown。
  - 当前在精确匹配失败且配置了可用 LLM 时，会调用 `patient_slot_semantic_match`，只允许在病例已有 `candidate_slots` 内做医学语义等价匹配；匹配成功后按该槽位真值回答，匹配失败时给出简短明确否定且不揭示槽位。
  - 当前 no-match prompt 已区分普通症状和高成本检查/疾病定义性证据：后者缺槽位时倾向回答“没做过这项检查 / 没听医生提过 / 报告里没注意到”，交给 brain 解析成 `unclear`，不默认制造明确阴性。
  - 在配置了可用 LLM 时，会使用受约束的 LLM 生成更自然的患者表达；否则退回规则模板。

### 3. 自动对战与评测

- [replay_engine.py](/Users/loki/Workspace/GraduationDesign/simulator/replay_engine.py)
  - 负责让 `brain/` 中的问诊系统与虚拟病人自动对战。
  - 它的职责是串联“系统提问 -> 病人作答 -> 状态更新 -> 下一问”的离线闭环。
  - 当前首轮输入不再直接依赖 `chief_complaint`，而是优先由 `patient_agent.open_case(case)` 基于骨架生成 opening text。
  - 已支持批量运行多个病例、输出回放结果，并在结果里记录实际首轮 opening text。
  - 当前耗时统计会先累计原始浮点耗时，再在落盘前统一 round，降低毫秒级病例里 `brain_turn_seconds_total` 被累计 round 放大的误导。
  - 若单病例在 `brain` 内部触发 `BrainDomainError` 或其他未预期运行时异常，当前都会把该病例记为 `status=failed`，同时把结构化 `error` 一并落盘，而不会伪装成正常完成。

- [benchmark.py](/Users/loki/Workspace/GraduationDesign/simulator/benchmark.py)
  - 负责汇总自动对战结果。
  - 当前已支持统计平均轮次、完成率、假设命中率和红旗覆盖率等核心指标。

- [path_cache_builder.py](/Users/loki/Workspace/GraduationDesign/simulator/path_cache_builder.py)
  - 用于从大量回放结果中提取高价值路径，并生成在线问诊可直接检索的“离线最优路径缓存”。

### 4. 辅助文件

- [__init__.py](/Users/loki/Workspace/GraduationDesign/simulator/__init__.py)
  - Python 包初始化文件。

## 当前实现状态

当前 `simulator/` 已经形成一条可运行的主链路：

- 病例结构已定义并支持真实图谱 node id 对齐
- 已具备一批可直接跑的 seed cases
- 已具备基于疾病审计结果的图谱驱动病例骨架生成器
- 病人代理已支持骨架驱动开场、检查上下文汇总回答和候选内语义匹配受约束回答
- 病人 opening 的检查类关键锚点会被显式保留，避免首轮证据在自然语言压缩后丢失图谱锚点
- 检查/病原/疾病定义性问题 no-match 时不再默认强阴性，降低缺槽位对 guarded acceptance 的误伤
- 自动回放和基础评测已经能批量跑通
- `benchmark.py` 已区分“候选列表命中”和“最终答案命中”，会输出严格 top 命中、宽松/家族级 top 命中、accepted 准确率、wrong accepted 与 top 正确但被拒绝等指标
- 当前图谱驱动病例 role-QC 正式输出已固定落盘到 `test_outputs/simulator_cases/graph_cases_20260502_role_qc/`；生成器会写入 `case_qc_score / case_qc_status / case_qc_reasons`，避免只按 family 数量判断病例质量
- 当前新增 20 例 smoke 输入到 `test_outputs/simulator_cases/graph_cases_20260502_role_qc/smoke20/`；该批全部来自 `case_qc_status=eligible` 病例，类型分布为 `ordinary=9 / low_cost=1 / exam_driven=5 / competitive=5`
- 当前已根据本机 Neo4j 导出症状证据族目录到 `test_outputs/evidence_family/disease_symptom_catalog_20260502/`，其中包含 disease-symptom 查看版 Markdown、症状节点分类 JSON 和每个疾病的 symptom-only 最低证据组建议
- 当前已根据本机 Neo4j 导出全证据族目录到 `test_outputs/evidence_family/disease_evidence_catalog_20260502/`，其中包含 symptom / risk / detail / lab / imaging / pathogen 六类证据节点和每个疾病的 full-evidence 最低证据组建议
- 当前已将 full-evidence 最低证据组和 evidence-role case QC 接入病例生成器，并重新生成病例集到 `test_outputs/simulator_cases/graph_cases_20260502_role_qc/`
- 路径缓存仍然是后续待完成模块

因此，这个目录当前更适合被理解为：

- 已完成可运行的病例生成与自动对战闭环
- 仍在继续提升 opening 自然度、行为多样性和大规模稳定评测

## 与其他目录的关系

- 问诊大脑：
  - [brain/README.md](/Users/loki/Workspace/GraduationDesign/brain/README.md)

- 配置文件：
  - [configs/simulator.yaml](/Users/loki/Workspace/GraduationDesign/configs/simulator.yaml)

- 第二阶段测试：
  - [tests/README.md](/Users/loki/Workspace/GraduationDesign/tests/README.md)

## 当前可直接使用的脚本

- [run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)
  - 使用 seed cases 或指定病例文件批量运行回放，并输出评测摘要。
  - 当前支持读取 `JSONL` 或 `JSON` 数组病例文件。
  - 当前支持病例级并发，默认 `--case-concurrency 4`；每个并发任务使用独立 brain 实例，避免共享会话状态。
  - 当前支持 `--limit`，便于先做 10 例左右的小样本 smoke。
  - 当前会自动读取 `configs/frontend.yaml` 与 `configs/frontend.local.yaml`，把 Neo4j / LLM / brain 配置桥接到 CLI 运行环境，避免 replay 入口和前端实时模式配置脱节。
  - 当前会直接向终端设备输出运行信息；即使通过 `conda run` 启动，也会看到病例启动、病例完成和运行中心跳。
  - 当前会在终端持续打印病例级进度条，并每 15 秒打印一次心跳，便于观察长时间运行任务的完成度和当前卡在哪个病例。
  - 当前启动日志会直接写出 `llm_available=true/false`；若为 `false`，批量回放会在启动前直接失败，不再静默退回规则链路。
  - 当前会在每个病例完成后立即追加写入 `replay_results.jsonl`，并同步刷新 `benchmark_summary.json`、`non_completed_cases.json`、`status.json` 和 `run.log`。
  - 当前 `non_completed_cases.json` 会只记录 `status != completed` 的异常诊断病例，并按 `failed::*`、`max_turn_reached::top_exact_correct_but_rejected`、`max_turn_reached::true_candidate_but_final_wrong`、`max_turn_reached::no_final_answer` 等类别分组，方便全量 benchmark 后优先复盘。
  - 当前 `replay_results.jsonl` / `status.json` / `benchmark_summary.json` / `non_completed_cases.json` / `run.log` 都已支持 `failed` 病例语义；失败病例会保留 `error.code / error.stage / error.prompt_name / error.message / error.attempts`。
  - 当前 batch runner 也补了一层单病例异常保护；即使某个 worker 内部抛出普通 Python 异常，也会尽量把该病例转成 `failed` 结果继续整批运行，而不是直接让整个 smoke 异常终止。
  - 当前默认支持断点续跑；如果输出目录里已有完成病例，会自动跳过这些病例，只继续未完成部分。若需要强制重跑，可使用 `--no-resume`。
  - 当前会记录病例级耗时拆分：`opening_seconds`、`initial_brain_seconds`、逐轮 `patient_answer_seconds / brain_turn_seconds`、`finalize_seconds` 与 `total_seconds`，并把聚合摘要写入 `benchmark_summary.json` / `status.json`；运行日志对亚秒级耗时会保留更高精度。
  - 当前续跑读取历史 `replay_results.jsonl` 时也会保留逐轮 `patient_answer_seconds / brain_turn_seconds / total_seconds`，便于后续继续做 turn 级复盘。
  - 当前在 `Ctrl+C` 或 `SIGTERM` 时会先写出中断状态，再强制结束进程，避免并发线程池在后台继续占用内存。
  - 当前会自动轻量化 `final_report.metadata`，不再把原始 `search_tree` 和 `last_search_result` 运行态对象直接写进 replay 结果，便于控制批量运行的内存占用。

- [diagnose_smoke10_failures.py](/Users/loki/Workspace/GraduationDesign/scripts/diagnose_smoke10_failures.py)
  - 对指定 replay 目录中的 failed opening 做 `med_extractor / A1` LLM payload 审计。
  - 会读取 `replay_results.jsonl`，复现同一批 opening 的结构化调用，并输出：
    - `llm_payload_audit.json`
    - `llm_payload_audit_summary.json`
    - `llm_payload_audit_report.md`
  - 适合在 `failed=10`、`turns=0` 这类“前置抽取链路没跑起来”的场景下快速定位是 prompt、payload 还是业务层 coercion 的问题。
  - 当前 `graph_cases_20260430_smoke10` 的最新审计结果已经显示：`med_probe_status_counts = {"ok": 10}`、`a1_probe_status_counts = {"ok": 10}`、`med_raw_empty_like_count = 0`、`a1_raw_empty_like_count = 0`，说明旧的 intake `certainty` 语义错位已经被修正；后续若 replay 仍失败，应优先排查网络 / API 连接或下游诊断阶段，而不是再回头怀疑 `MedExtractor / A1` payload 语义。

- [generate_graph_virtual_patients.py](/Users/loki/Workspace/GraduationDesign/scripts/generate_graph_virtual_patients.py)
  - 使用疾病级图谱审计输出生成图谱驱动虚拟病人病例。
  - 当前支持 `--minimum-evidence-groups-file` 指定 full-evidence catalog 约束文件；默认读取 `test_outputs/evidence_family/disease_evidence_catalog_20260502/disease_minimum_evidence_groups.json`。

- [export_disease_symptom_family_catalog.py](/Users/loki/Workspace/GraduationDesign/scripts/export_disease_symptom_family_catalog.py)
  - 连接当前 Neo4j，导出 `Disease` 与 `ClinicalFinding` 之间的 `MANIFESTS_AS` 关系。
  - 输出 `disease_symptom_family_catalog.json/md`、`symptom_family_nodes.json` 和 `disease_minimum_symptom_groups.json`，便于先人工检查症状分类，再决定如何把结果接入病例生成器。

- [export_disease_evidence_family_catalog.py](/Users/loki/Workspace/GraduationDesign/scripts/export_disease_evidence_family_catalog.py)
  - 连接当前 Neo4j，导出 `Disease` 与 `ClinicalFinding / RiskFactor / PopulationGroup / ClinicalAttribute / LabFinding / LabTest / ImagingFinding / Pathogen` 的核心证据关系。
  - 输出 `disease_evidence_family_catalog.json/md`、`evidence_family_nodes.json` 和 `disease_minimum_evidence_groups.json`，作为后续全疾病 benchmark QC 的候选约束底稿。

- [sample_graph_virtual_patients.py](/Users/loki/Workspace/GraduationDesign/scripts/sample_graph_virtual_patients.py)
  - 从 `cases.json` 或 `cases.jsonl` 中按病例类型固定抽样，输出 `sampled_cases_4x5.json` 和 `sampled_cases_4x5.md`，用于人工检查 opening 与 positive slots 质量。

- [build_graph_case_smoke_set.py](/Users/loki/Workspace/GraduationDesign/scripts/build_graph_case_smoke_set.py)
  - 从完整图谱病例中抽取可回放 smoke 输入。
  - 默认只选择 `case_qc_status=eligible` 病例，优先按类型均衡；某一类型 eligible 数量不足时，用其他类型 eligible 病例补齐总数。

- [build_non_completed_smoke_set.py](/Users/loki/Workspace/GraduationDesign/scripts/build_non_completed_smoke_set.py)
  - 从全量 replay 的 `non_completed_cases.json` 中精确抽取未完成病例骨架，输出后续回归 smoke 用的 `cases.jsonl` / `cases.json` / `manifest.json` / `summary.md`。
  - 当前已将 `graph_cases_20260502_role_qc_full` 中 80 个未完成病例抽取到 `test_outputs/simulator_cases/graph_cases_20260502_role_qc/non_completed_smoke80/`，类别分布为 `no_final_answer=11 / top_exact_correct_but_rejected=25 / top_family_correct_but_rejected=3 / true_candidate_but_final_wrong=7 / true_candidate_missing=34`。

- [run_role_qc_smoke20_replay.sh](/Users/loki/Workspace/GraduationDesign/scripts/run_role_qc_smoke20_replay.sh)
  - 运行最新 `graph_cases_20260502_role_qc/smoke20` 的一键 replay 脚本。

## 详细方案文档

更适合论文写作和方法复盘的详细说明见：

- [virtual_patient_generation_scheme.md](/Users/loki/Workspace/GraduationDesign/docs/virtual_patient_generation_scheme.md)

## 后续重点建设方向

按照当前路线，`simulator/` 后续最值得优先推进的是：

1. 完善 `patient_agent.py` 的行为规则
2. 让 `replay_engine.py` 真正驱动 `brain/service.py`
3. 扩展 `generate_cases.py`，先生成一批小规模高质量样例
4. 继续扩展 `benchmark.py` 的疾病层级、父子节点和同族疾病评分口径
5. 最后推进 `path_cache_builder.py`

## 代码注释规范

本目录已统一采用中文注释规范：

- 每个文件顶部有中文文件说明
- 每个类有中文说明
- 每个函数上方都应有中文用途注释

后续新增文件和函数时，也应继续遵守这一规范。
