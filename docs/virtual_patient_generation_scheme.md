# 图谱驱动虚拟病人生成方案

## 1. 文档目的

本文档用于系统整理当前项目中“图谱驱动虚拟病人生成与自动对战”方案，供后续论文写作、实验复盘、方法章节整理和实现维护使用。

它回答四个核心问题：

1. 当前虚拟病人是基于什么输入构建的
2. 病例骨架是如何从知识图谱审计结果中生成的
3. 病人代理如何基于骨架而不是预写死的主诉参与对话
4. 生成结果如何进入自动回放与离线评测链路

本文档描述的是当前仓库内已经实现并可运行的方案，而不是纯粹的未来设想。

截至当前版本，图谱病例生成器已经包含一轮面向病例质量的小范围修复：

- 对同一指标的互斥阈值阳性证据做去重，只保留一条
- 重点覆盖了 `CD4`、`HIV RNA / 病毒载量`、`BMI`、`LDL`、`HDL`、`甘油三酯`、`总胆固醇`、`eGFR` 等指标的去冗余
- 对 opening slots 做 patient-friendly 过滤，避免把 `LabTest`、`Pathogen`、`ClinicalAttribute` 以及预后/疗效/统计类 finding 主动暴露给首轮开场
- 保留被过滤证据在 `slot_truth_map` 中的真值，只改变其是否主动暴露

## 2. 总体定位

本项目的虚拟病人模块不追求“生成完整自然语言病例叙述文本”，而是采用“图谱骨架 + 受约束病人代理”的实现路线。

核心思想是：

- 上游知识图谱提供疾病及其邻接证据
- 疾病级图谱审计把这些证据整理成适合病例生成的证据池
- 图谱病例生成器从证据池中构造病例骨架
- 病人代理根据骨架决定首轮开场和后续问答
- 问诊大脑与病人代理自动对战，形成可复现的 replay 数据

因此，虚拟病人的“事实边界”由骨架控制，自然语言表达则由病人代理负责组织；这比“完全手写病例文本”更可扩展，也比“完全自由生成患者对话”更可控。

## 3. 当前依赖的图谱与审计输入

### 3.1 当前图谱版本

当前推荐入库图谱为 `search_kg_20260419_125328` 目录下的疾病节点单文件修订版：

```text
test_outputs/search_kg/search_kg_20260419_125328/
  relation_repair/alias_merge/
    merged_graph_by_aliases_pruned_le3_reclassified_aliases_le3_disease_aliases_only_no_isolates.json
```

该版本的 Neo4j 校验结果为：

- `Disease = 80`
- `nodes = 1012`
- `relationships = 1725`
- `isolated_nodes = 0`

### 3.2 审计输入目录

当前图谱驱动虚拟病人生成直接消费疾病级图谱审计输出目录：

```text
test_outputs/graph_audit/all_diseases_20260420_disease_aliases_only/
```

该目录由脚本 [audit_disease_ego_graphs.py](/Users/loki/Workspace/GraduationDesign/scripts/audit_disease_ego_graphs.py) 生成，每个疾病对应一份局部证据报告 JSON。图谱病例生成器不直接访问 Neo4j，而是复用这批审计后的中间结果。

这样做有两个优点：

- 将“图谱质量问题”和“病例生成问题”解耦
- 使病例生成可以离线、可复现、可审查

## 4. 整体链路

当前完整链路可以概括为：

```text
搜索专用知识图谱
    -> 疾病级图谱审计
    -> 图谱病例骨架生成
    -> 病人代理开场/回答
    -> 问诊大脑自动对战
    -> replay / benchmark / ablation
```

对应实现文件如下：

- 图谱审计：
  - [simulator/graph_audit.py](/Users/loki/Workspace/GraduationDesign/simulator/graph_audit.py)
  - [scripts/audit_disease_ego_graphs.py](/Users/loki/Workspace/GraduationDesign/scripts/audit_disease_ego_graphs.py)
- 图谱病例生成：
  - [simulator/graph_case_generator.py](/Users/loki/Workspace/GraduationDesign/simulator/graph_case_generator.py)
  - [scripts/generate_graph_virtual_patients.py](/Users/loki/Workspace/GraduationDesign/scripts/generate_graph_virtual_patients.py)
  - [scripts/sample_graph_virtual_patients.py](/Users/loki/Workspace/GraduationDesign/scripts/sample_graph_virtual_patients.py)
- 病例 schema：
  - [simulator/case_schema.py](/Users/loki/Workspace/GraduationDesign/simulator/case_schema.py)
- 病人代理：
  - [simulator/patient_agent.py](/Users/loki/Workspace/GraduationDesign/simulator/patient_agent.py)
- 自动对战：
  - [simulator/replay_engine.py](/Users/loki/Workspace/GraduationDesign/simulator/replay_engine.py)
  - [scripts/run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)

## 5. 设计原则

当前方案遵循以下原则。

### 5.1 以图谱证据为中心，而不是以自由文本为中心

病例的真实性来自图谱中的结构化证据，而不是 LLM 即兴扩写。

### 5.2 自然语言表达与事实边界分离

- 骨架负责定义“这个病人有哪些事实”
- 病人代理负责把这些事实说成人话

### 5.3 不同病例模板服务不同评测目标

不是所有疾病都用同一种病例模板，而是按不同证据结构生成不同类型的病例，以覆盖：

- 普通问诊
- 低成本问诊
- 检查驱动问诊
- 竞争诊断问诊

### 5.4 允许降级运行

病人代理在有可用 LLM 时使用受约束生成；没有可用 LLM 时自动退回规则模板，因此整个链路不会因为模型不可用而无法运行。

## 6. 病例类型设计

当前实现保留四类病例：

- `ordinary`
- `low_cost`
- `exam_driven`
- `competitive`

这些类型对应不同的证据选择策略和不同的问诊场景。

### 6.1 ordinary：普通病例

`ordinary` 目标是模拟常规问诊中的“症状或背景主导型”病人，而不是检查单主导型病人。

生成条件：

```text
total_pool >= 4
chief_complaint_friendly_pool >= 2
```

其中：

- `total_pool`：该疾病全部可用证据总数
- `chief_complaint_friendly_pool`：满足以下条件的证据池

```text
group in {symptom, risk, detail}
and acquisition_mode in {direct_ask, history_known}
and evidence_cost != high
```

此外，当前实现还要求：

- 从 `chief_pool` 中选出的 opening 证据里，必须至少有一个来自 `symptom` 或 `detail`
- 否则即使数量够，也会跳过 ordinary

这样是为了避免 ordinary 被“年龄、HIV感染者、既往背景”这类证据偷渡成弱主诉。

### 6.2 low_cost：低成本病例

`low_cost` 目标是评估系统在不依赖高成本检查时，是否能仅凭问诊信息推进诊断。

生成条件：

```text
low_cost_pool >= 4
```

定义：

```text
group in {symptom, risk, detail}
and acquisition_mode in {direct_ask, history_known}
and evidence_cost != high
```

注意：

- `needs_clinician_assessment` 不纳入 low_cost 主阳性池
- `lab / imaging / pathogen` 不作为 low_cost 的核心阳性槽位

因此，像“梅毒”这类主要依赖实验室证据的疾病，可能可以生成 `exam_driven`，但不会生成 `low_cost`。

### 6.3 exam_driven：检查驱动病例

`exam_driven` 用于模拟“患者最先带着检查异常来就诊”的场景。

生成条件：

```text
exam_pool_total >= 3
exam_pool_high_value >= 2
```

其中：

`exam_pool_total` 定义为：

```text
group in {lab, imaging, pathogen}
or acquisition_mode in {needs_lab_test, needs_imaging, needs_pathogen_test}
```

`exam_pool_high_value` 满足任一即可：

- priority 位于当前疾病 exam pool 前 50%
- `relation_specificity >= 0.85`
- `group in {lab, imaging, pathogen}` 且 `relation_type` 属于：
  - `DIAGNOSED_BY`
  - `HAS_LAB_FINDING`
  - `HAS_IMAGING_FINDING`
  - `HAS_PATHOGEN`

生成时：

- 以高价值检查证据为主
- 允许最多补 1 个 `symptom/risk`
- opening 更偏向“检查提示异常，想进一步看看”

### 6.4 competitive：竞争病例

`competitive` 用于模拟“主诉相似、需要继续追问或检查才能分开”的竞争诊断场景。

与只按 symptom overlap 挑竞争病不同，当前实现采用综合竞争分数：

```text
competition_score
= 0.40 * low_cost_overlap
+ 0.25 * symptom_jaccard
+ 0.20 * risk_detail_overlap
+ 0.15 * exam_path_similarity
```

各项定义：

- `low_cost_overlap`：目标病与竞争病在低成本证据上的 Jaccard
- `symptom_jaccard`：目标病与竞争病 symptom 集合的 Jaccard
- `risk_detail_overlap`：目标病与竞争病在 risk/detail 集合上的 Jaccard
- `exam_path_similarity`：目标病与竞争病在检查/病原方向上的相似度

竞争病例生成门槛：

```text
shared_low_cost >= 2
target_only_discriminative >= 2
competitor_only_negative >= 1
```

含义分别是：

- `shared_low_cost`：双方共享的低成本可问证据
- `target_only_discriminative`：目标病独有、且有区分度的关键证据
- `competitor_only_negative`：竞争病独有、在本病例中要写成阴性的证据

当前默认每个疾病只取 1 个最高分竞争病，但配置上已支持扩展到多个。

## 7. 输入校验

图谱病例生成前，会先校验每条 evidence 是否包含以下必需字段：

- `group`
- `relation_type`
- `priority`
- `relation_specificity`
- `acquisition_mode`
- `evidence_cost`

若缺失这些字段，则该疾病不会参与病例生成，并在 `manifest.json` 中记录：

```json
{
  "reason": "audit_report_missing_required_fields"
}
```

这一设计的目的，是避免把“图谱字段不完整”误判成“病例模板不适配”。

## 8. 病例骨架数据结构

### 8.1 SlotTruth

当前病例骨架中的最小事实单元是 `SlotTruth`：

- `node_id`：真实图谱节点 id
- `value`：该槽位真假或具体值
- `group`：证据分组，例如 `symptom`、`lab`
- `node_label`：图谱标签，例如 `ClinicalFinding`、`LabFinding`
- `mention_style`：表达风格，当前多为 `direct`，也支持 `vague`
- `reveal_only_if_asked`：是否只能在被问到时透露
- `aliases`：该槽位的自然语言别名

### 8.2 VirtualPatientCase

`VirtualPatientCase` 当前包含：

- `case_id`
- `title`
- `true_disease_phase`
- `true_conditions`
- `chief_complaint`
- `behavior_style`
- `slot_truth_map`
- `hidden_slots`
- `red_flags`
- `metadata`

需要强调的是：

- `chief_complaint` 现在已不是首轮对话的唯一来源
- 它被降级为兼容字段和缓存字段
- 真正的首轮输入优先由病人代理根据骨架生成

### 8.3 为什么 `slot_truth_map` 的 key 使用真实图谱 node id

当前实现中：

- `slot_truth_map` 的 key 使用真实 `target_node_id`
- `SlotTruth.node_id` 也写真实 `merged_node_*`
- 中文名称放在 `aliases`

这样设计是为了保证 replay 过程中：

- 系统如果问的是图谱真实节点 id，对得上
- 系统如果问的是中文证据名，对得上
- 不会出现“病例里存的是中文，系统里问的是 node id，二者无法匹配”的问题

## 9. 病例骨架如何生成

图谱病例生成器位于：

- [simulator/graph_case_generator.py](/Users/loki/Workspace/GraduationDesign/simulator/graph_case_generator.py)

命令行入口位于：

- [scripts/generate_graph_virtual_patients.py](/Users/loki/Workspace/GraduationDesign/scripts/generate_graph_virtual_patients.py)

生成步骤可分为：

1. 读取审计目录中的疾病 JSON
2. 解析为 `DiseaseAuditRecord`
3. 构造 `DiseaseProfile`
4. 按模板分别尝试生成 `ordinary / low_cost / exam_driven / competitive`
5. 形成 `VirtualPatientCase`
6. 写出 `cases.jsonl`、`cases.json`、`manifest.json`、`summary.md`
7. 按病例类型重新抽样，写出人工复核用的 `sampled_cases_4x5.json` 与 `sampled_cases_4x5.md`

### 9.1 DiseaseProfile 中缓存的关键证据池

为了避免重复计算，生成器先对每个疾病缓存以下证据池：

- `all_evidence`
- `chief_pool`
- `low_cost_pool`
- `exam_pool`
- `exam_high_value_pool`
- `symptom_pool`
- `risk_detail_pool`

同时缓存几类集合键：

- `all_keys`
- `low_cost_keys`
- `symptom_keys`
- `risk_detail_keys`
- `exam_name_keys`
- `exam_group_keys`
- `exam_relation_keys`

这些集合主要用于：

- 计算 overlap / Jaccard
- 选择 competitor
- 生成 manifest

### 9.2 opening slots 的选择

当前实现不再把 `chief_complaint` 作为病例定义本体，而是在生成病例时显式挑选一组 opening slots。

这些 opening slots 会：

- 写入 `metadata.opening_slot_ids`
- 写入 `metadata.opening_slot_names`
- 对应的 `SlotTruth.reveal_only_if_asked = false`

因此，病人代理首轮发言时，会优先读取这些 opening slots 来组织自然语言开场。

在当前版本中，opening 不是“谁先被选进正向证据就直接暴露”，而是会再经过一轮 patient-friendly 过滤。具体规则是：

- 允许主动暴露：
  - `symptom`
  - `risk` 且 `acquisition_mode in {direct_ask, history_known}`
  - 具体 `lab/imaging` 结果，例如带有 `阳性`、`升高`、`<`、`>`、`磨玻璃影`、`空洞` 等结果词的项目
- 不允许主动暴露：
  - `target_label == LabTest`
  - `target_label == Pathogen` 或 `group == pathogen`
  - `target_label == ClinicalAttribute`
  - `年龄`
  - `身体质量指数 / BMI`
  - `测量部位`
  - `持续时间 / 减重持续时间`
  - 纯检查项目名，如 `CD4+ T淋巴细胞计数`、`HIV RNA`、`CMV DNA检测`、`胸部CT`
  - 纯病原体名，如 `巨细胞病毒`、`刚地弓形虫`
  - 预后/疗效/统计/筛查类 finding，如 `AIDS相关病死率高`、`临床症状无改善`、`体征无改善`
  - 过泛表达，如 `异常`、`检查`、`筛查`、`诊断`

如果原始 opening 候选在过滤后为空，生成器会从正向证据里兜底挑选，优先级为：

1. `symptom`
2. `risk` 且 `acquisition_mode in {direct_ask, history_known}`
3. 具体 `lab/imaging` 结果

最多保留 3 个 opening slots；如果仍然找不到合适项，则交给病人代理回退到 `chief_complaint` 或保底开场句。

### 9.3 正向证据的互斥清理

为了避免图谱里同一指标的多个分层阈值同时出现在一个病例中，当前生成器会在 `_build_case()` 内先清理正向证据，再写入 `slot_truth_map`。

当前已覆盖的互斥族包括：

- BMI / 身体质量指数 / `kg/m²`
- CD4
- HIV RNA / 病毒载量
- 甘油三酯
- HDL
- LDL
- 总胆固醇
- eGFR / 肾小球滤过率
- 年龄

需要强调的是：当前实现只做 lightweight 去冗余，不做复杂医学推理。例如：

- 不把普通症状互斥化
- 不把 `收缩压` 和 `舒张压` 强行并成同一族
- 不把 HPV 多亚型感染做互斥
- 不把病原体和对应阳性检测做互斥

同一互斥族内只保留一条，但不同 family 的排序规则不完全相同。

通用回退优先级为：

1. `priority` 更高
2. `relation_specificity` 更高
3. `target_name` 更长，也就是通常更具体的描述优先

其中，`CD4` family 有一层额外的严重度排序：

- 结果型证据优先于纯检查项目名
- `<` / `≤` 阈值中，数值越小越优先
- `>` / `≥` 阈值中，数值越大越优先

例如：

- `CD4+ T淋巴细胞计数 < 50/μL`
- `CD4+ T淋巴细胞计数 < 100/μL`
- `CD4+ T淋巴细胞计数 < 200/μL`
- `CD4+ T淋巴细胞计数 < 300/μL`
- `CD4+ T淋巴细胞计数`

如果这些同时出现在同一病例中，最终只保留最具体、最严重的一条，而不会并列保留多个阈值和一个纯检查项目名。

`HIV RNA / 病毒载量`、`LDL`、`HDL`、`甘油三酯`、`总胆固醇`、`eGFR` 则使用“阈值优先于泛化状态，具体值优先于项目名”的 lightweight 规则。例如：

- `HIV RNA < 50 copies/mL` 会优先于 `HIV病毒载量未受抑制` 这类泛化描述
- `甘油三酯 >= 1.7 mmol/L` 会优先于 `甘油三酯升高`
- `eGFR < 30` 会优先于 `eGFR < 60` 和 `eGFR < 90`

这样可以避免例如肥胖病例中同时出现：

- `BMI>=37.5kg/m²`
- `32.5<=BMI<37.5kg/m²`
- `28.0<=BMI<32.5kg/m²`

这类互斥阳性同时为真的问题。

## 10. 病人代理设计

### 10.1 当前定位

病人代理负责两件事：

1. 生成首轮开场
2. 对系统后续追问给出受约束回答

实现文件：

- [simulator/patient_agent.py](/Users/loki/Workspace/GraduationDesign/simulator/patient_agent.py)

### 10.2 首轮开场机制

当前 replay 不再直接把 `case.chief_complaint` 送进 brain，而是：

1. `ReplayEngine` 调用 `patient_agent.open_case(case)`
2. 病人代理根据骨架生成 opening text
3. 再把这句 opening text 送给 `brain.process_turn()`

如果病例中有 `reveal_only_if_asked = false` 的阳性槽位，则优先基于这些 opening slots 开场；否则退回到：

- `chief_complaint`
- 再不行用一个保底句子

### 10.3 后续问答机制

当系统发出问题时，病人代理会：

1. 先根据 `question_node_id` 直接匹配
2. 如果不行，再匹配 `question_text`
3. 再根据 `aliases` 做字符串级别匹配

因此它既能响应图谱 id，也能响应中文问法。

### 10.4 hidden slots

若某个槽位属于 `hidden_slots`，且行为风格是：

- `guarded`
- `concealing`

则病人代理会倾向回避回答，而不是直接说真值。

### 10.5 unknown 情况

如果系统问到病例骨架中没有的槽位，病人代理会返回：

- 模糊风格：`说不上来，不能确定有没有。`
- 普通风格：`这个我不太确定，没专门注意过。`

这对应现实中的“病人并不知道自己不存在某检查结果”。

## 11. LLM 驱动的病人表达

### 11.1 为什么要引入 LLM

如果完全使用规则模板，病人首轮开场和后续回答会比较僵硬，容易出现：

- 过于像结构化表格
- 过于一致
- 不像真实病人说话

因此当前方案中，病人代理支持在 LLM 可用时进行“受约束自然化表达”。

### 11.2 当前 prompt

当前为病人代理补充了两类结构化 prompt：

- `patient_opening_generation`
- `patient_answer_generation`

实现位于：

- [brain/llm_client.py](/Users/loki/Workspace/GraduationDesign/brain/llm_client.py)

它们的约束是：

- 只能基于给定骨架表达
- 不能发明新的医学事实
- 不能直接泄露疾病名
- 输出必须是结构化 JSON

### 11.3 回退机制

如果 LLM 不可用，或调用失败，则：

- 开场退回规则模板
- 回答退回 `有。/没有。/说不上来。/不太想回答。`

这样既保证自然度，也保证链路稳定性。

## 12. Replay 入口与自动对战

当前自动对战入口主要是：

- [scripts/run_batch_replay.py](/Users/loki/Workspace/GraduationDesign/scripts/run_batch_replay.py)

它现在支持：

- 读取 `JSONL`
- 读取 `JSON` 数组
- seed cases
- 图谱驱动病例

回放过程中：

1. 病人代理先给出 opening
2. 问诊大脑接收 opening 并进入 A1/A2/A3/A4
3. 系统提问
4. 病人代理根据骨架回答
5. 达到终止条件后输出 final report

Replay 结果中已经新增：

- `opening_text`

用于记录该病例在本轮自动对战中的实际首轮发言。

## 13. 输出文件设计

图谱病例生成当前输出四类文件：

### 13.1 `cases.jsonl`

用途：

- 适合逐行处理
- 适合历史 replay 工具
- 适合批量实验脚本

### 13.2 `cases.json`

用途：

- 适合人工查看
- 适合外部程序直接整体读取
- 适合论文写作时人工抽样

### 13.3 `manifest.json`

用途：

- 记录每个疾病生成了哪些病例
- 没生成哪些病例
- 没生成的原因是什么
- 各类 pool count 是多少

这是最关键的“病例生成审计报告”。

### 13.4 `summary.md`

用途：

- 提供人工快速浏览摘要
- 适合汇报或论文草稿阶段快速查看

### 13.5 `sampled_cases_4x5.json`

用途：

- 按 `ordinary / low_cost / exam_driven / competitive` 四类各抽固定数量病例
- 便于人工做小样本质检
- 适合对比不同生成规则调整前后的病例差异

### 13.6 `sampled_cases_4x5.md`

用途：

- 提供抽样病例的 Markdown 摘要
- 便于直接检查 `opening_slot_names`、`selected_positive_slots`、`selected_negative_slots`
- 便于发现 opening 不自然、正向证据互斥、竞争病例区分度不足等问题

## 14. 当前一轮实际输出

截至当前版本，已经基于当前疾病审计目录生成一轮完整图谱病例：

```text
test_outputs/simulator_cases/graph_cases_20260421/
```

其中包含：

- `cases.jsonl`
- `cases.json`
- `manifest.json`
- `summary.md`
- `sampled_cases_4x5.json`
- `sampled_cases_4x5.md`

该轮结果共生成：

- `ordinary = 66`
- `low_cost = 49`
- `exam_driven = 61`
- `competitive = 51`
- 总数 `227`

在当前这轮小范围质量修复后，病例总数没有变化，但抽样质量有所改善，主要体现在：

- opening 中出现 `LabTest`、`Pathogen`、`ClinicalAttribute` 的概率明显下降
- `BMI` 与 `CD4` 的多阈值并列问题已被显著压缩
- `selected_positive_slots` 更接近“可并列成立的事实集合”，而不是同一指标不同分层的堆叠

这个数量来自当前图谱与当前模板规则，并不是固定值；只要图谱或模板阈值改变，重新生成后病例数量会变化。

## 15. 关键实现细节

### 15.1 `chief_complaint` 的角色变化

最初实现中，`chief_complaint` 是 replay 首轮输入的唯一来源。

现在的设计已经调整为：

- `chief_complaint` 仍保留在 schema 中
- 但主要作为兼容字段和缓存字段
- replay 首轮优先走病人代理的 `open_case()`

这使病例定义从“文本优先”转为“骨架优先”。

### 15.2 为什么不直接让 LLM 自由扮演病人

如果完全自由生成，会出现几个问题：

- 难以保证与图谱一致
- 难以控制阳性/阴性边界
- 难以重现实验
- 容易产生与目标疾病无关的新事实

因此当前路线是“结构化真值表约束 + 受限自然语言生成”，本质上是把 LLM 当作表达器，而不是当作事实决定器。

### 15.3 为什么 manifest 非常重要

论文写作时，病例生成最容易被质疑的点是：

- 为什么这个疾病没有 low_cost 病例
- 为什么某些疾病没有竞争病例
- 生成失败到底是图谱问题还是模板问题

`manifest.json` 可以直接回答这些问题，因此它不仅是工程调试文件，也是论文实验解释的重要依据。

### 15.4 为什么还需要固定抽样输出

`manifest.json` 更适合解释“生成成功或失败的结构原因”，但不适合直接判断病例自然度。

因此当前实现又补了一层固定抽样输出：

- 用固定随机种子
- 每类病例固定抽 `5` 条
- 同时输出 JSON 与 Markdown

这样在每次重新生成病例后，都可以快速检查：

- opening 是否还出现不自然的表格化槽位
- 同一指标的互斥阳性是否被正确清理
- 是否还出现多个 `CD4` 阈值并列
- 是否还出现多个 `HIV RNA / 病毒载量` 状态并列
- 是否还出现多个 `LDL / HDL / TG / TC / eGFR` 同 family 项并列
- competitive 病例的 shared / target-only / negative 证据是否仍然合理

## 16. 当前局限性

当前方案虽然已经形成闭环，但仍有几个明确局限。

### 16.1 opening 自然度已改善，但 chief complaint 缓存仍可能保留旧式表达

当前版本已经对 opening slots 做了过滤，因此 `年龄`、`BMI`、纯病原体名、测量部位、持续时间等不自然槽位一般不会再主动暴露为首轮开场。

但仍有两个剩余问题：

- `chief_complaint` 作为缓存字段，可能仍保留一部分旧式模板表达
- 某些疾病本身缺少足够自然的症状型低成本证据，因此 opening 虽然合规，但仍然偏弱

这说明 opening 质量已经从“明显错误”转为“基本可用但仍需继续打磨”的阶段。

### 16.2 positive slots 虽已做去冗余，但仍可能保留部分“对话上不自然”的结构化事实

当前实现已经会压缩同一指标的明显互斥阈值，但 `selected_positive_slots` 仍然服务于“病例真值表完整性”，而不是“患者自然口语化表达”。

因此某些病例中仍可能看到：

- `年龄`
- `身体质量指数（BMI）`
- `骨密度测量部位`
- `ART治疗启动`

这类不适合主动开场、但仍适合作为病例事实保留的证据。

这不是 opening 过滤失效，而是当前设计本来就允许“truth map 比 opening 更结构化”。

### 16.3 行为风格还不够丰富

当前 `behavior_style` 主要影响：

- 模糊表达
- 回避回答

但尚未做到更精细的人格化差异，例如：

- 主动描述偏多/偏少
- 时间线表达差异
- 敏感信息迂回程度差异

### 16.4 LLM 回答仍以短句为主

为了控制事实边界，当前病人代理回答很短，这对稳定性有利，但也意味着对话自然度仍有提升空间。

### 16.5 目前仍以单轮病例骨架为主

当前骨架以“有哪些阳性/阴性事实”为中心，尚未系统编码：

- 症状出现顺序
- 症状持续时间
- 时间演化
- 就诊动机变化

这些可以作为下一阶段增强方向。

## 17. 论文写作建议

如果后续要写论文方法章节，建议按下面顺序组织。

### 17.1 方法章节

可拆为四部分：

1. 搜索专用知识图谱构建
2. 疾病级图谱审计
3. 图谱驱动病例骨架生成
4. 骨架约束下的 LLM 病人代理与自动对战

### 17.2 实验章节

可以报告：

- 图谱规模
- 审计后的疾病数
- 各类型病例数量
- 各类型病例跳过原因分布
- replay 完成率、平均轮次、误诊情况

### 17.3 讨论章节

适合重点讨论：

- 为什么采用骨架驱动而不是自由病例生成
- 为什么 competitive 病例对 differential diagnosis 更有价值
- 为什么 manifest 可以作为病例生成可解释性的证据
- 当前 opening 自然度和时间结构不足的局限性

## 18. 当前建议的复现实验命令

### 18.1 重新生成图谱驱动病例

```bash
conda run -n GraduationDesign python scripts/generate_graph_virtual_patients.py \
  --audit-root test_outputs/graph_audit/all_diseases_20260420_disease_aliases_only \
  --output-file test_outputs/simulator_cases/graph_cases_20260421/cases.jsonl \
  --output-json-file test_outputs/simulator_cases/graph_cases_20260421/cases.json \
  --manifest-file test_outputs/simulator_cases/graph_cases_20260421/manifest.json \
  --summary-file test_outputs/simulator_cases/graph_cases_20260421/summary.md
```

### 18.2 重新按病例类型抽样做人工检查

```bash
conda run -n GraduationDesign python scripts/sample_graph_virtual_patients.py \
  --cases-file test_outputs/simulator_cases/graph_cases_20260421/cases.json \
  --output-file test_outputs/simulator_cases/graph_cases_20260421/sampled_cases_4x5.json \
  --summary-file test_outputs/simulator_cases/graph_cases_20260421/sampled_cases_4x5.md \
  --sample-size-per-type 5 \
  --seed 42
```

建议每次改动图谱病例生成规则后，都按上面命令重新抽样一次，再人工检查：

- `opening_slot_names`
- `selected_positive_slots`
- `selected_negative_slots`
- `slot_truth_positive`

特别建议针对以下冲突做定向检查：

- `selected_positive_slots` 中是否还存在多个 `CD4` 阈值并列
- 是否还存在多个 `HIV RNA / 病毒载量` 状态并列
- 是否还存在多个 `LDL / HDL / TG / TC / eGFR` 同 family 项并列
- `opening_slot_names` 是否还包含 `LabTest`、`Pathogen`、`ClinicalAttribute`、预后/疗效/统计类 `ClinicalFinding`

### 18.3 用生成病例跑 replay

```bash
NEO4J_PASSWORD=你的密码 conda run -n GraduationDesign python scripts/run_batch_replay.py \
  --cases-file test_outputs/simulator_cases/graph_cases_20260421/cases.json \
  --max-turns 8
```

注意：当前 `run_batch_replay.py` 既支持 `cases.jsonl`，也支持 `cases.json`。

## 19. 相关文件索引

### 19.1 方法与实现

- [simulator/graph_case_generator.py](/Users/loki/Workspace/GraduationDesign/simulator/graph_case_generator.py)
- [scripts/generate_graph_virtual_patients.py](/Users/loki/Workspace/GraduationDesign/scripts/generate_graph_virtual_patients.py)
- [scripts/sample_graph_virtual_patients.py](/Users/loki/Workspace/GraduationDesign/scripts/sample_graph_virtual_patients.py)
- [simulator/case_schema.py](/Users/loki/Workspace/GraduationDesign/simulator/case_schema.py)
- [simulator/patient_agent.py](/Users/loki/Workspace/GraduationDesign/simulator/patient_agent.py)
- [simulator/replay_engine.py](/Users/loki/Workspace/GraduationDesign/simulator/replay_engine.py)
- [brain/llm_client.py](/Users/loki/Workspace/GraduationDesign/brain/llm_client.py)

### 19.2 当前实验输出

- [cases.json](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260421/cases.json)
- [cases.jsonl](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260421/cases.jsonl)
- [manifest.json](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260421/manifest.json)
- [summary.md](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260421/summary.md)
- [sampled_cases_4x5.json](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260421/sampled_cases_4x5.json)
- [sampled_cases_4x5.md](/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_cases/graph_cases_20260421/sampled_cases_4x5.md)

### 19.3 配套说明

- [simulator/README.md](/Users/loki/Workspace/GraduationDesign/simulator/README.md)
- [README.md](/Users/loki/Workspace/GraduationDesign/README.md)
- [phase2_changelog.md](/Users/loki/Workspace/GraduationDesign/docs/phase2_changelog.md)
