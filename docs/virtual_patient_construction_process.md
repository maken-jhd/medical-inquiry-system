# 虚拟病人构建过程说明

## 1. 文档目的

本文档专门说明当前仓库里“虚拟病人是如何被构建出来的”。

这里的“构建”分成两层：

1. 先构建一个结构化病例骨架，也就是 `VirtualPatientCase`
2. 再让 `VirtualPatientAgent` 基于这个骨架生成开场和后续回答

因此，当前系统里的虚拟病人并不是“先写好一整篇自然语言病例”，而是“先定义真实事实，再由病人代理把事实说出来”。

## 2. 一句话总图

当前真实链路可以概括为：

```text
seed case 或图谱审计结果
    -> VirtualPatientCase / SlotTruth
    -> cases.jsonl / cases.json
    -> VirtualPatientAgent.open_case(case)
    -> VirtualPatientAgent.answer_question(...)
    -> ReplayEngine.run_case(case)
    -> brain.process_turn(...)
```

对应的主要实现文件是：

- 病例结构：[simulator/cases/schema.py](../simulator/cases/schema.py)
- 内置种子病例：[simulator/cases/seed_cases.py](../simulator/cases/seed_cases.py)
- 图谱病例生成器：[simulator/cases/graph_generator.py](../simulator/cases/graph_generator.py)
- 病例读写：[simulator/cases/io.py](../simulator/cases/io.py)
- 虚拟病人运行时：[simulator/patient/runtime.py](../simulator/patient/runtime.py)
- 开场生成：[simulator/patient/opening.py](../simulator/patient/opening.py)
- 问题匹配：[simulator/patient/matching.py](../simulator/patient/matching.py)
- 回答渲染：[simulator/patient/replies.py](../simulator/patient/replies.py)
- LLM 受约束表达：[simulator/patient/llm.py](../simulator/patient/llm.py)

## 3. 两层心智模型

理解当前虚拟病人时，最好把它拆成两个对象：

### 3.1 结构化病例骨架

这层负责定义“这个病人到底有哪些真实事实”。

核心对象是：

- `VirtualPatientCase`
- `SlotTruth`

这层不负责生成完整自然语言，只负责回答这些问题：

- 真实疾病是什么
- 哪些症状/风险/检查结果为真
- 哪些槽位为假
- 哪些信息可以主动说
- 哪些信息必须问到才说
- 哪些信息即使为真也可能被隐藏

### 3.2 运行时病人代理

这层负责把骨架说成人话。

它主要做三件事：

1. 从骨架里挑出开场要暴露的 truth
2. 根据系统提问在 `slot_truth_map` 中匹配对应 truth
3. 按 `behavior_style / hidden_slots / use_llm` 决定回答口吻

这意味着：

- 医学事实边界由骨架控制
- 自然语言表达由病人代理控制
- 即使启用 LLM，也不会允许它凭空创造新的病例事实

## 4. 基础数据结构

当前病例骨架定义在 [simulator/cases/schema.py](../simulator/cases/schema.py)。

### 4.1 `SlotTruth`

`SlotTruth` 表示单个槽位真值，最重要的字段有：

- `node_id`
  - 图谱节点唯一 ID
  - 病人代理靠它和 brain 的 `pending_action.target_node_id` 对齐
- `value`
  - 该槽位真实值
  - 可以是 `bool`、数值、文本
- `group`
  - 证据大组，如 `symptom / risk / detail / lab / imaging / pathogen`
- `node_label`
  - 图谱标签，如 `ClinicalFinding / LabFinding / Pathogen`
- `mention_style`
  - 默认表达方式，如 `direct / vague`
- `reveal_only_if_asked`
  - 是否只在问到时才透露
- `aliases`
  - 这个槽位在自然语言问题里可能被叫到的别名

### 4.2 `VirtualPatientCase`

`VirtualPatientCase` 表示完整病例，最重要的字段有：

- `case_id`
  - 病例唯一 ID
- `title`
  - 调试和人工查看时的标题
- `true_conditions`
  - 该病例真实疾病或并发症列表
- `chief_complaint`
  - 一条缓存好的主诉文本
  - 但要注意：运行时开场并不一定直接使用它
- `behavior_style`
  - 病人风格，如 `cooperative / guarded / vague / concealing`
- `slot_truth_map`
  - 整个病例最核心的事实表
- `hidden_slots`
  - 即使为真，也倾向隐藏的槽位
- `red_flags`
  - 希望系统较快识别的重要危险信号
- `metadata`
  - 存放来源、病例类型、opening 槽位、QC 结果等附加信息

## 5. 病例骨架从哪里来

当前项目里，病例骨架有两条来源。

### 5.1 来源一：内置 seed cases

入口在 [simulator/cases/seed_cases.py](../simulator/cases/seed_cases.py) 的 `build_seed_cases()`。

这条链路更适合：

- 本地 smoke
- 单病例调试
- 手工构造困难场景
- 基线脚本快速复用

它的构建方式很直接：

1. 用 `_slot()` 快速构造多个 `SlotTruth`
2. 把这些 truth 填进 `VirtualPatientCase.slot_truth_map`
3. 手工指定 `chief_complaint / behavior_style / hidden_slots / red_flags / metadata`
4. 最后由 `build_seed_cases()` 返回一组内置病例

这种方式的特点是：

- 可读性强
- 调试简单
- 适合明确验证某个问诊策略
- 覆盖面有限，不适合大规模 benchmark

### 5.2 来源二：图谱驱动病例生成

入口通常是脚本 [scripts/generate_graph_virtual_patients.py](../scripts/generate_graph_virtual_patients.py)。

它的主调用链是：

```text
generate_graph_virtual_patients.py
    -> GraphCaseGenerator.generate_from_audit_root(audit_root)
        -> load_disease_audit_reports(...)
        -> GraphCaseGenerator.generate_from_records(...)
        -> _build_profile(record)
        -> _build_ordinary_case / _build_low_cost_case / _build_exam_driven_case / _build_competitive_cases
        -> _build_case(...)
        -> write_graph_case_outputs(...)
```

这是当前大规模 benchmark 病例的主要来源。

## 6. 图谱驱动病例的详细构建步骤

### 6.1 读取疾病审计结果

第一步不是直接访问 Neo4j，而是读取疾病审计中间产物。

入口函数是：

- `load_disease_audit_reports(audit_root)`

它会把每个疾病 JSON 报告整理成 `DiseaseAuditRecord`，其中最关键的是：

- `disease_id`
- `disease_name`
- `evidence`
- `group_summary`
- `summary`

这样做的好处是：

- 图谱质量问题和病例生成问题解耦
- 病例生成可以离线、可复现
- 生成器不需要直接连 Neo4j

### 6.2 为每个疾病构建证据池 `DiseaseProfile`

接下来 `GraphCaseGenerator._build_profile(record)` 会把原始审计证据整理成一个 `DiseaseProfile`。

这一步的核心工作是：

1. 去重并排序全部证据
   - `_unique_evidence_items()`
   - `_sort_evidence_items()`
2. 切出不同用途的证据池
   - `chief_pool`
   - `low_cost_pool`
   - `exam_pool`
   - `exam_high_value_pool`
   - `symptom_pool`
   - `risk_detail_pool`
3. 计算当前疾病可覆盖的 evidence family
4. 解析最低证据组约束
   - 优先用 `disease_minimum_evidence_groups.json`
   - 不够时回退到内置规则

这一步的结果决定了：

- 某个疾病能不能生成某种病例类型
- 哪些证据适合做 opening
- 哪些证据适合做 low-cost 场景
- 哪些证据适合做 exam-driven 场景
- benchmark 所需的最低 evidence family 是否被覆盖

### 6.3 生成四类病例

`GraphCaseGenerator.generate_from_records()` 会尝试为每个疾病生成四类病例：

- `ordinary`
- `low_cost`
- `exam_driven`
- `competitive`

对应函数分别是：

- `_build_ordinary_case(profile)`
- `_build_low_cost_case(profile)`
- `_build_exam_driven_case(profile)`
- `_build_competitive_cases(profile, profiles)`

#### ordinary

普通病例强调：

- 有足够自然的主诉候选
- 至少有症状或 detail 能放进开场
- 再补一部分重要证据进入 `positive_items`

这里会用到：

- `_select_chief_complaint_items()`
- `_render_low_cost_complaint()`
- `_select_requirement_covering_items()`

#### low_cost

低成本病例强调：

- 尽量只靠 `symptom / risk / detail`
- 不依赖高成本检查
- 优先覆盖 benchmark 的最低 family 要求

#### exam_driven

检查驱动病例强调：

- 必须有足够的 `lab / imaging / pathogen` 证据
- 还要有足够“高价值检查锚点”
- 主诉文本也会更像“复查发现异常，想进一步确认”

这里主要依赖：

- `_select_high_value_exam_pool()`
- `_render_exam_driven_complaint()`

#### competitive

竞争病例强调：

- 与竞争病共享一部分低成本表型
- 同时保留目标病独有的区分性证据
- 再补一部分只对竞争病成立、可写成阴性的排除证据

它的目标是让诊断过程更像真实 differential diagnosis，而不是一眼就能锁定。

### 6.4 用 `_build_case()` 组装成 `VirtualPatientCase`

无论哪种病例类型，最后都会进入 `_build_case(...)`。

这是把“证据列表”真正转换成“虚拟病人病例对象”的关键步骤。

它会依次做这些事：

1. 过滤冲突的阳性证据
   - `_filter_conflicting_positive_items()`
2. 过滤与阳性集不兼容的阴性证据
   - `_filter_incompatible_negative_items()`
3. 过滤不适合作为 opening 的条目
   - `_filter_opening_items()`
4. 计算病例级 QC 摘要
   - `_build_benchmark_qc_summary()`
5. 生成 `opening_node_ids`
6. 生成 `slot_truth_map`
   - 对 `positive_items` 调用 `_build_slot_truth(item, True)`
   - 对 `negative_items` 调用 `_build_slot_truth(item, False)`
7. 对 opening 中的阳性 truth，把 `reveal_only_if_asked` 改成 `False`
8. 生成 `case_id`
   - `_build_case_id(...)`
9. 写入结构化 `metadata`
   - `case_type`
   - `disease_id / disease_name`
   - `opening_slot_ids / opening_slot_names`
   - `selected_positive_slots / selected_negative_slots`
   - `case_qc_status / case_qc_score / case_qc_reasons`

最后返回一个真正的 `VirtualPatientCase`。

### 6.5 `chief_complaint` 和运行时 opening 不是一回事

这是调试时非常重要的一点。

图谱生成器在 `_build_case()` 里会写入 `chief_complaint`，例如：

- `_render_low_cost_complaint(...)`
- `_render_exam_driven_complaint(...)`

但运行时 `VirtualPatientRuntime.open_case()` 并不是先看 `chief_complaint`，而是：

1. 先调用 `collect_opening_truths(case)`
2. 优先根据 opening truths 生成 opening
3. 只有没有 opening truths 时，才回退到 `chief_complaint`

所以：

- `chief_complaint` 更像一条缓存好的可读主诉
- 真正首轮说什么，优先由 `reveal_only_if_asked=False` 的 truth 决定

## 7. 病例如何写入文件

病例骨架构建完成后，通常通过 [simulator/cases/io.py](../simulator/cases/io.py) 或 `write_graph_case_outputs()` 落盘。

常见输出包括：

- `cases.jsonl`
- `cases.json`
- `manifest.json`
- `summary.md`

各文件作用不同：

- `cases.jsonl`
  - 批量回放最常用
- `cases.json`
  - 方便人工阅读
- `manifest.json`
  - 记录某个疾病为什么生成成功/失败
- `summary.md`
  - 生成人工检查摘要

后续 `load_cases_jsonl()` 会把这些持久化字典重新还原成 `VirtualPatientCase`。

## 8. 运行时病人代理如何把骨架变成“活的病人”

### 8.1 开场生成 `open_case()`

入口是 [simulator/patient/runtime.py](../simulator/patient/runtime.py) 的 `VirtualPatientRuntime.open_case(case)`。

真实链路是：

```text
VirtualPatientRuntime.open_case(case)
    -> collect_opening_truths(case)
    -> render_opening(truths, case, ...)
        -> try_generate_opening_with_llm(...)   # 可选
        -> render_opening_fallback(...)         # 规则兜底
```

#### `collect_opening_truths(case)` 的逻辑

它会优先收集：

- `reveal_only_if_asked == False`
- 且不是明显阴性的 truth

如果没有，就回退到：

- `case.metadata["opening_slot_ids"]`

这说明 opening 的事实范围依然受病例骨架严格约束。

#### `render_opening(...)` 的逻辑

它先尝试 `try_generate_opening_with_llm(...)`，但这个 LLM 不是自由发挥：

- 输入只来自 opening truths
- 还会调用 `opening_preserves_critical_anchors(...)`
- 如果关键检查/病原锚点被说丢了，就直接放弃 LLM 文本

失败时退回 `render_opening_fallback(...)` 的确定性模板。

### 8.2 问题回答 `answer_question()`

入口是 `VirtualPatientRuntime.answer_question(question_node_id, question_text, case)`。

它的真实分流顺序是：

```text
answer_question(...)
    -> render_exam_context_reply(...)
    -> resolve_truth_with_fallback(...)
    -> hidden slot ? render_hidden_reply(...)
    -> truth is None ? render_unknown_reply(...)
    -> else render_truth(...)
```

#### 第一步：检查上下文问题

如果 `question_node_id` 是 `__exam_context__::...` 这类节点，会优先进入：

- `render_exam_context_reply(...)`
- `collect_exam_context_truths(...)`

这一步不是去匹配单个槽位，而是直接汇总当前病例中对应检查大类的 truth。

#### 第二步：普通槽位匹配

普通问题会调用：

- `resolve_truth(...)`
- `resolve_truth_with_fallback(...)`

匹配优先级大致是：

1. `slot_truth_map[question_node_id]`
2. `truth.node_id == question_node_id`
3. `truth.node_id` 出现在问题文本里
4. `aliases` 命中问题文本
5. 规则失败时，再交给 `try_resolve_truth_with_llm(...)`

这里的 LLM 同样是受限的：

- 只能在已有 `candidate_slots` 中做语义匹配
- 不能创造一个病例里不存在的新槽位

#### 第三步：隐藏槽位

如果命中了 truth，但同时满足：

- `truth.node_id in case.hidden_slots`
- `behavior_style in {"guarded", "concealing"}`

那么不会直接回答真相，而是走：

- `render_hidden_reply(...)`

这就是“病例事实是真的，但病人不愿意说”的实现方式。

#### 第四步：未知问题

如果没有命中任何 truth，就走：

- `render_unknown_reply(...)`

这表示：

- 不是这个病例的已定义事实
- 或当前问题文本没能稳定对齐到病例中的某个槽位

#### 第五步：已知 truth 的正常回答

如果命中了 truth，就走：

- `render_truth(...)`

规则回答很简单：

- `bool True` 往往回答“有”
- `bool False` 往往回答“没有”
- `mention_style == "vague"` 时会更含糊
- 非布尔值会直接返回对应值文本

如果启用了 `use_llm=True` 且 LLM 可用，则会先尝试 `try_generate_answer_with_llm(...)`。

但这个 LLM 仍然是受约束的：

- 它只能围绕 `matched_slot` 改写表达
- 不能篡改 truth

## 9. 为什么这种设计更适合当前项目

当前虚拟病人的设计并不是追求“文学化的病例叙事”，而是为了满足下面几个工程目标：

### 9.1 可控

病例真实边界由 `slot_truth_map` 明确限定，不会因为自由生成而漂移。

### 9.2 可复现

同一个 `VirtualPatientCase` 可以重复 replay，便于 benchmark 和 ablation。

### 9.3 可调试

当 brain 问错、病人答怪、槽位没命中时，可以明确追到：

- 是病例骨架的问题
- 是 opening 选择的问题
- 是 alias 匹配的问题
- 还是回答渲染的问题

### 9.4 可扩展

上游只要继续产生更多疾病审计结果和 evidence family catalog，就能批量生成更多病例，而不需要逐条手写文本。

## 10. 调试时最常看的位置

### 10.1 首轮 opening 和预期不一致

优先看：

- `simulator/patient/opening.py`
  - `collect_opening_truths()`
  - `render_opening()`
  - `render_opening_fallback()`
- `simulator/cases/graph_generator.py`
  - `_build_case()`
  - `_select_chief_complaint_items()`
  - `_render_low_cost_complaint()`
  - `_render_exam_driven_complaint()`

最常见原因：

- opening truth 没被标成 `reveal_only_if_asked=False`
- `chief_complaint` 被写了，但运行时其实优先走了 opening truths
- LLM opening 没通过锚点校验，退回了 fallback

### 10.2 问题总是匹配不到 truth

优先看：

- `simulator/patient/matching.py`
  - `resolve_truth()`
  - `resolve_truth_with_fallback()`

最常见原因：

- `question_node_id` 和 `slot_truth_map` 的 key / `truth.node_id` 对不上
- `aliases` 不够
- 问题文本里根本没有包含可命中的别名

### 10.3 病人本来知道，但一直回答“不确定”

优先看：

- `hidden_slots`
- `behavior_style`
- `render_hidden_reply()`
- `render_unknown_reply()`

尤其要区分：

- 是命中了 hidden slot，所以故意回避
- 还是根本没匹配到 truth，所以走了 unknown

### 10.4 检查上下文回答不合理

优先看：

- `simulator/patient/matching.py`
  - `render_exam_context_reply()`
  - `collect_exam_context_truths()`

最常见原因：

- truth 的 `group` 没有正确写成 `lab / imaging / pathogen`
- 隐藏槽位在 `guarded / concealing` 风格下被过滤掉了

### 10.5 某个疾病根本没生成出病例

优先看：

- `manifest.json`
- `GraphCaseGenerator.generate_from_records()`
- `_build_profile()`
- `_build_ordinary_case()`
- `_build_low_cost_case()`
- `_build_exam_driven_case()`
- `_build_competitive_cases()`

最常见原因：

- 证据池不够
- chief-friendly 证据不够
- high-value exam pool 不够
- minimum evidence family 要求没覆盖

## 11. 建议的阅读顺序

如果你要从代码层面真正吃透虚拟病人的构建过程，推荐按下面顺序读：

1. [simulator/cases/schema.py](../simulator/cases/schema.py)
2. [simulator/cases/seed_cases.py](../simulator/cases/seed_cases.py)
3. [simulator/cases/graph_generator.py](../simulator/cases/graph_generator.py)
4. [simulator/cases/io.py](../simulator/cases/io.py)
5. [simulator/patient/opening.py](../simulator/patient/opening.py)
6. [simulator/patient/matching.py](../simulator/patient/matching.py)
7. [simulator/patient/replies.py](../simulator/patient/replies.py)
8. [simulator/patient/llm.py](../simulator/patient/llm.py)
9. [simulator/patient/runtime.py](../simulator/patient/runtime.py)
10. [simulator/replay/runtime.py](../simulator/replay/runtime.py)

这样会比较容易建立一条完整心智链：

```text
病例数据长什么样
    -> 病例如何生成
    -> opening 如何构建
    -> 问题如何命中 truth
    -> 病人如何回答
    -> replay 如何消费这个病人
```

## 12. 图谱驱动病人生成过程的中文解释版

上面前几节更像“按真实实现拆开的说明书”。如果换一种更适合建立直觉的说法，当前图谱驱动病人生成流程，其实可以概括成一句话：

先把每个疾病整理成一组可用证据池，再按照四种问诊场景定义去挑证据，最后把挑出来的证据压成一个结构化病例骨架。

这里有两个重点：

1. 系统不是先写自然语言病例，再去抽结构化槽位。
2. 系统也不是随机从图谱里抓几条证据就拼成病人。

它真正做的是：

1. 先为每个疾病建立“候选证据空间”
2. 再定义四类不同的病例生成目标
3. 再根据目标，从证据空间里挑出 opening、阳性证据、阴性证据
4. 最后把这些证据写成 `VirtualPatientCase`

### 12.1 为什么要先定义四类病人

如果只生成一种“标准病例”，那么 benchmark 很容易变成一种单调场景：

- 有些病例只要听到主诉就很容易猜出来
- 有些病例主要靠检查结果
- 有些病例真正难点在鉴别诊断
- 有些病例的价值在于考察系统能不能先用低成本追问收集足够证据

所以当前系统不是追求“每个疾病只造一个病人”，而是尽量让同一个疾病在不同问诊难点下都能出现。这样离线回放时，系统面对的不是单一模板，而是几类有明确差异的问诊任务。

四类病例本质上对应四种不同的问诊压力：

- `ordinary`：普通问诊型，强调自然主诉和常规证据组合
- `low_cost`：低成本问诊型，强调尽量不依赖高成本检查
- `exam_driven`：检查驱动型，强调关键结论主要来自检查
- `competitive`：竞争鉴别型，强调目标病和竞争病之间的区分过程

可以把它理解成：系统先定义“想考察什么样的问诊难点”，然后再去构造与之匹配的病人骨架。

### 12.2 四类病人分别是怎么定义的

#### 1. 普通问诊型 `ordinary`

普通问诊型追求的是“像临床中最常见的自然病例开场”。

这类病例通常满足几个特征：

- 证据池总体不能太小，否则病例内容太单薄
- 必须存在足够适合拿来当主诉的条目
- 开场优先使用症状和细节，而不是一上来就把抽象背景或检查结论全抛出来
- 除了主诉，还要补入若干关键阳性证据，保证后续问诊仍然有可验证空间

它并不刻意压低检查信息，也不刻意制造强竞争关系，而是尽量模拟“病人带着几个自然不适来就诊，系统再逐步展开问诊”的情况。

从生成目标看，`ordinary` 更像默认场景：既要可问，又要自然，还要能支撑后续诊断收敛。

#### 2. 低成本问诊型 `low_cost`

低成本问诊型的重点不是“病例更简单”，而是“系统在不依赖高成本检查时，能不能通过症状、风险和细节把诊断逼近到足够可靠”。

因此它会尽量把证据限制在这些范围里：

- 症状
- 风险因素
- 病史或关键细节

同时刻意避免把高成本证据当成核心支撑，例如：

- 复杂病原学检测
- 影像结果
- 必须做检查才能得到的关键结论

这类病例的目的，是逼问诊系统把低成本信息利用到位。换句话说，它考察的是“先问什么、怎么问、能不能靠病史和症状把候选病缩窄”，而不是“有没有马上跳到检查结论上”。

#### 3. 检查驱动型 `exam_driven`

检查驱动型对应的是另一种真实场景：病人就诊时，决定性信息不是主诉本身，而是已经做过或必须追问的检查结果。

这类病例会被要求满足更强的检查相关条件：

- 检查类证据总体数量要够
- 其中必须有若干“高价值检查锚点”

这里所谓“高价值检查锚点”，可以理解为那些真正能推动诊断收敛的检查结果，例如：

- 关键影像异常
- 关键实验室异常
- 病原学阳性
- 直接与诊断关系很强的检查结论

因此这类病例的开场往往不是“我咳嗽发热”，而更像：

- 最近复查发现某些异常
- 检查提示有问题，想进一步确认原因

它考察的是系统能不能正确利用检查入口，不要在已经有高价值结果时还停留在低价值重复追问上。

#### 4. 竞争鉴别型 `competitive`

竞争鉴别型是四类里最像“专门为 differential diagnosis 设计”的病例。

它不是简单地把目标病证据堆起来，而是刻意构造三层证据：

- 一部分目标病和竞争病都可能出现的共享低成本表型
- 一部分只对目标病更有区分力的证据
- 一部分只对竞争病成立、因此在目标病里应该表现为阴性或缺失的证据

这类病例的生成目标，是让系统在前几轮不能太轻松地锁定答案。它需要先经历“为什么不是另一个很像的病”的过程，才真正考察到搜索式问诊和鉴别追问的价值。

所以 `competitive` 本质上是在模拟：

- 早期线索看起来很像多种病
- 只有追到区分性证据，才能真正拉开差距

### 12.3 四类病人不是“手写标签”，而是由证据池条件决定能不能生成

四类病人并不是人工先给每个疾病打标签，然后强行套模板。

真实过程正好相反：

1. 先把这个疾病的证据池整理出来
2. 再看它是否满足某种病例类型的生成门槛
3. 满足就生成，不满足就跳过

也就是说，一个疾病能不能生成 `ordinary`、`low_cost`、`exam_driven`、`competitive`，取决于它自己在图谱审计结果里到底有没有足够合适的证据。

例如：

- 如果某个疾病几乎没有自然症状，只有检查结论，它就不一定适合生成普通问诊型
- 如果某个疾病低成本线索太少，它就不适合生成低成本问诊型
- 如果某个疾病没有足够高价值检查证据，它就不适合生成检查驱动型
- 如果某个疾病找不到像样的竞争病，也缺少共享表型和区分证据，它就不适合生成竞争鉴别型

这就是为什么 manifest 里会记录“为什么没生成出某类病例”。那不是程序异常，而是生成器在执行明确的场景门槛。

### 12.4 在定义四类病人之前，系统先做了什么准备

在真正生成病例之前，系统会先把每个疾病的原始审计证据整理成一个“疾病画像”。

这个画像至少做了五件事：

#### 1. 去重和排序

同一条证据如果在原始审计结果里重复出现，系统不会把它当成多条独立信息。  
它会先去重，再按优先级和关系特异性排序。

这样做的目的很简单：

- 避免某些证据因为重复出现而被误判成特别重要
- 后续在挑 opening 和阳性证据时，有稳定的“先后顺序”

#### 2. 按用途拆成多个证据池

不是所有证据都适合干同一件事，所以系统会把同一个疾病的证据拆成多种池子：

- 适合做主诉的池
- 低成本池
- 检查池
- 高价值检查池
- 症状池
- 风险 / 细节池

这样后面在生成不同病例类型时，就不会“明明想造低成本病例，却拿检查锚点来开场”，或者“明明想造检查驱动病例，却只拿模糊症状来撑场面”。

#### 3. 计算证据 family 覆盖情况

系统不只看“具体证据名称”，还会看“这些证据属于哪些诊断家族”。

例如某条证据可能属于：

- 呼吸道症状
- 免疫状态
- 病原学
- 影像
- 氧合异常
- 代谢定义

之所以要做这一层，是因为 benchmark 关心的不只是“有没有某一条具体证据”，而是“病例是否覆盖了该疾病最起码应具备的几类诊断信息”。

#### 4. 决定该疾病的最低证据族要求

系统会尽量为每个疾病建立一个“最低证据族要求”。

这套要求的意思不是“病例必须包含固定同一条证据”，而是：

- 至少要从某个 family 组里命中一条
- 某些 family 组之间可以互相替代

比如某个疾病要求：

- 必须有呼吸道症状
- 必须体现免疫状态
- 必须体现影像
- 必须体现病原或真菌标志物

这其实定义的是“最低可判定性”，也就是这个病例至少得给系统留下一条可走通的诊断路径。

#### 5. 估计哪些证据过于“高连接”

有些证据出现在很多疾病里，例如泛化背景风险、非常常见的弱症状。  
如果一个病例的阳性证据几乎全是这种“很多病都有”的高连接线索，那么即使看起来证据不少，它也不一定是一个好 benchmark 病例。

所以系统会统计一条证据跨多少个疾病出现，并把“几乎全是高连接背景线索”的病例在 QC 阶段降级。

### 12.5 四类病人是如何一步步生成出来的

如果把实现逻辑翻译成中文步骤，当前真实生成过程可以理解为：

#### 第一步：先为每个疾病尝试普通型、低成本型、检查驱动型

这三类都只依赖当前疾病自己的证据池，不需要先找竞争病。

生成器会逐个问：

- 你有没有足够自然的主诉线索？
- 你有没有足够多的低成本线索？
- 你有没有足够多且足够关键的检查线索？

每个问题都对应明确门槛。满足门槛就进入组装阶段，不满足就记录跳过原因。

#### 第二步：再单独寻找竞争病，尝试生成竞争鉴别型

竞争鉴别型不是随便找一个别的疾病配对，而是先给所有候选竞争病打分。

这个打分看的是“像不像但又不完全一样”，核心由四部分组成：

- 低成本证据重叠程度，权重最高
- 症状重叠程度
- 风险和细节重叠程度
- 检查路径相似程度

当前实现里，竞争分数本质上就是一个加权相似度：

- 低成本重叠占 40%
- 症状重叠占 25%
- 风险 / 细节重叠占 20%
- 检查路径相似占 15%

这个设计很有针对性，因为竞争病例最重要的是“前期像”，而前期最容易暴露出来的通常正是低成本信息和常见症状，所以它们被赋予了更高权重。

#### 第三步：从证据池中挑出 opening、阳性证据、阴性证据

这一步才真正开始“造病人骨架”。

它不是简单地按优先级从上往下截取，而是带着场景目标去挑：

- opening 要像病人会主动说出来的话
- 阳性证据要尽量覆盖最低诊断族要求
- 阴性证据只能在竞争病例中使用，而且不能和目标病核心定义打架

也就是说，病例骨架里的证据不是“重要证据列表”，而是“围绕问诊行为被重新组织过的一组事实”。

### 12.6 生成骨架时，最核心的算法其实是“覆盖最低证据族”

如果只按优先级取前几条证据，生成出来的病例很容易有两个问题：

- 证据看起来都很强，但都属于同一类信息，诊断路径不完整
- 某个本来应当出现的关键证据族根本没被覆盖

所以系统在选阳性证据时，不是先看“最强的前八条”，而是先做一轮“覆盖要求”：

1. 先遍历该疾病的每个最低证据族要求组
2. 对每个要求组，找一条最合适的证据去覆盖它
3. 等所有能覆盖的组都覆盖完，再用剩余偏好证据补满总槽位数

这里的“要求组”往往是一个 family 集合，而不是单个 family。  
意思是：只要命中其中任意一种，就算这个要求组被满足。

例如：

- `{"fungal_marker", "pathogen"}` 代表真菌标志物或病原学命中一个即可
- `{"imaging", "disease_specific_lab", "pathogen"}` 代表这几类里至少应当有一类出现

这种设计的价值在于：它允许不同疾病、不同图谱质量下存在灵活替代，但仍然要求病例具备“最少可判定性”。

### 12.7 选“覆盖证据”时，系统如何判断哪条更好

当多个候选证据都能覆盖同一个要求组时，系统不会随机选，而是更偏好下面这些特征：

- 语义更具体的证据
- 已经带有明确结果标记的证据
- 图谱标签上更像 LabFinding、ImagingFinding、Pathogen 的证据
- 与高价值检查关系更接近的证据
- 优先级更高、关系特异性更高的证据

也就是说，系统不是只问“这条证据能不能覆盖要求”，还会问“这条证据是不是更像一个能真正推动诊断的锚点”。

这能减少一种常见问题：虽然形式上覆盖了 family，但选中的其实是弱而泛的替代项，导致病例看起来合规、实际不够有力。

### 12.8 opening 是怎么挑的，为什么不能把所有强证据都放到开场

病例骨架生成时，opening 不是“越强越好”，而是“越像病人第一句会主动说的话越好”。

因此 opening 会优先考虑：

- 症状
- 可自然说出口的 detail
- 某些病人本来就会提到的低成本风险信息
- 少数非常具体、病人可能已经知道的检查结果

而下面这些内容通常不适合直接当 opening：

- 纯背景性信息
- 太抽象的医学定义
- 需要系统追问后才更自然浮现的结论

这是因为病人代理后续会把 opening 中的证据设成“可主动暴露”，如果 opening 选得太学术、太背景化、太结论化，就会把整个回放环境变得不自然。

所以系统还专门做了 opening 过滤和兜底：

- 先过滤不适合主动暴露的条目
- 如果过滤后不够，就退而求其次，从症状、低成本风险、具体检查结果中补几条

这说明 opening 的目标不是“最强证据展示”，而是“自然开场 + 后续仍可问”。

### 12.9 为什么还要构造阴性证据，尤其是竞争病例里的阴性证据

普通病例、低成本病例、检查驱动病例主要靠阳性证据定义。  
但竞争病例如果只有阳性证据，很容易变成“虽然目标病和竞争病都像，但没有明确排除动作”。

所以竞争病例会额外引入一小部分阴性证据，这些阴性证据来自竞争病独有、而目标病不应该有的证据。

它们的作用不是增加噪声，而是制造真正有价值的排除问题：

- 问到了，能帮助把竞争病压下去
- 没问到，系统就可能一直停留在模糊竞争态

但这些阴性证据也不能乱放，所以系统还会做几层过滤：

- 不能和目标病自己的阳性证据重复
- 不能在名字上直接和目标病定义冲突
- 不能破坏目标病本应成立的核心 family
- 不能把目标病自己的定义性证据错误写成阴性

因此这里的阴性证据不是“随便编几个没有”，而是很谨慎地从竞争病差异里反推回来。

### 12.10 为什么还要过滤“互相冲突的阳性证据”

图谱里有些证据虽然都和某个疾病相关，但彼此可能属于同一个互斥分层家族。

例如：

- 同一类程度分层
- 同一类结果档位
- 语义上高度重合但表达不同的条目

如果把这些证据全都写成同一个病例的阳性事实，就会出现一种“图谱上都能连上，病例里却像把多个版本同时装进一个人”的问题。

因此系统会在写骨架前做阳性冲突过滤，只保留每个互斥 family 里最合适的一条。  
这样生成出来的病人事实更像一个真实个体，而不是图谱证据集合的简单并集。

### 12.11 骨架最终是怎么落成 `VirtualPatientCase` 的

当 opening、阳性证据、阴性证据都挑好之后，系统才真正组装病例骨架。

这个组装过程可以理解成五步：

#### 1. 把阳性证据写成阳性 truth

每条阳性证据都会变成一个 `SlotTruth`，其真实值为真。

#### 2. 把阴性证据写成阴性 truth

竞争病例里保留下来的阴性证据，会被写成真实值为假的 `SlotTruth`。

#### 3. 把 opening 中出现的阳性 truth 标成“可以主动说”

这些 truth 后续在运行时开场生成时会优先被拿出来。

#### 4. 写入病例元数据

例如：

- 这是什么病例类型
- 目标疾病是谁
- opening 由哪些槽位组成
- 阳性和阴性一共选了哪些条目
- 本病例的 QC 状态如何

#### 5. 形成最终 `VirtualPatientCase`

到这里，一个病人骨架才真正成型。  
后面的病人代理只是围绕这个骨架去表达，并不会重新决定医学事实。

### 12.12 为什么生成后还要做 QC，而不是“能生成就算成功”

因为“能拼成一个病例”和“这是一个值得拿来做 benchmark 的病例”不是一回事。

系统后面会再做一层病例质量检查，主要看几件事：

- 有没有至少一条像样的阳性诊断锚点
- 是否覆盖了最低证据族要求
- 开场是不是只剩背景信息
- 阳性证据是不是几乎全是高连接、很多病共有的泛化线索
- 是否满足该疾病类型应有的核心诊断路径

这里尤其重要的一条是：  
像 HIV、CD4 低、一般风险背景这类信息，虽然在某些疾病里非常相关，但很多时候只能算背景支持，不能单独撑起一个高质量 benchmark 病例。

所以如果一个病例只有这些背景信息，而没有真正的疾病特异锚点，它通常会被判成弱病例，甚至直接不纳入 benchmark。

### 12.13 用一句更像“算法描述”的方式总结整个流程

如果把整套方法压缩成一句偏算法化的话，可以这样描述：

对于每个疾病，系统先从审计结果中构造按用途划分的证据池和最低证据族约束；然后分别按普通型、低成本型、检查驱动型和竞争鉴别型四种场景定义，筛选满足门槛的 opening 候选、阳性证据和阴性证据；再通过“优先覆盖最低证据族、过滤互斥阳性、过滤不兼容阴性、限制 opening 自然性”的规则，把这些证据压缩成一个结构化 `VirtualPatientCase`；最后用病例级 QC 判断它是否适合作为 benchmark 病例。

### 12.14 从研究设计角度看，这样做解决了什么问题

这种做法解决了三个很核心的问题：

#### 1. 避免病例完全自由生成，导致事实边界漂移

因为所有事实都先被压进骨架，再由病人代理表达，所以病例不会因为语言生成而失控。

#### 2. 避免病例只体现“图谱有什么”，而不体现“问诊要考什么”

四类病例定义把“问诊场景目标”显式注入生成过程，使 benchmark 不只是图谱抽样结果，而是带任务设计的病例集合。

#### 3. 避免病例看起来很多，实际却没有诊断判别力

最低证据族约束、区分性证据选择和病例级 QC，一起保证了这些病人不是“能说几句话就算病例”，而是真正能用于检验问诊策略的结构化测试样本。
