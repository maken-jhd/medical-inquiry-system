# 第一阶段知识图谱建设历程与问题复盘

本文件用于系统记录第一阶段“医学知识工程与底层图谱搭建”的实际推进过程、遇到的问题、采取的修复策略、最终产出质量以及后续待完善点。它的用途主要有两个：

- 作为项目内部的阶段复盘文档，避免后续开发时遗忘关键决策背景
- 作为后续撰写毕业论文时的过程材料，帮助说明方案为何这样设计、经历了哪些迭代

## 一、阶段目标

第一阶段的目标不是直接构建问诊前端，而是先把 `HIV/` 目录下的大量非结构化指南、专家共识和专题资料，转化成可查询、可追溯、可持续迭代的医学知识图谱。

这一阶段的核心目标包括：

- 从原始 Markdown 文档中抽取结构化 `nodes / edges`
- 对同义节点进行归一化，降低图谱冗余
- 将最终图谱导入 Neo4j，为第二阶段问诊大脑提供知识底座

## 二、最初方案与第一轮问题

最初的设想比较直接：

1. 对 Markdown 文档切片
2. 直接调用大模型抽取 `nodes / edges`
3. 将结果导入 Neo4j

第一轮实践后，很快发现这样的问题比较明显：

- 原始文档中存在复杂表格，模型直接读取时容易漏掉方案、剂量、疗程和条件
- 同一概念在不同文档中存在大量写法差异，例如缩写、全称、空格差异、中英文混写
- 节点虽然能抽出来，但边不稳定，导致图谱中出现大量孤立节点
- 某些关系类型方向不稳定，存在明显语义错误

这意味着“原始文档 -> 直接抽取 -> 直接入库”的路径过于理想化，无法支撑一个较干净的医学知识底座。

## 三、第一次重要迭代：增加前置处理

为了解决原始文档噪声问题，加入了前置清理阶段，对应脚本：

- [clean_markdown.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/clean_markdown.py)

这一轮前置处理主要做了几类事情：

### 1. 结构与格式清洗

- 统一换行符
- 去除 BOM 和零宽字符
- 统一标题格式
- 去除重复标题
- 折叠多余空行

### 2. 医学术语、单位与符号归一

为减少大模型被格式噪声干扰，额外做了规则清洗，例如：

- 术语清洗：
  - `CD4+ T`
  - `CD4⁺T`
  - `CD4 T`
- 单位清洗：
  - `个/μL`
  - `cells/μL`
- 特殊符号清洗：
  - `~`
  - `～`
  - `≥`
  - `≤`

### 3. 表格转写为正文描述

这是前置处理里最关键的一步。

很多指南中的关键信息存在于表格中，例如：

- 首选 / 次选方案
- 不同阶段治疗方案
- 疗程
- 适用条件

因此加入了“表格转写”逻辑：

- 识别 Markdown 表格
- 将表格连同标题路径、前后文一起送给大模型
- 输出更适合抽取的自然语言描述
- 可选再让校验模型复核，避免新增事实或遗漏条件

这一轮的核心收益是：

- 提升了抽取前文本的一致性
- 降低了复杂表格直接进入抽取阶段造成的信息遗漏

## 四、第二次重要迭代：增加后置处理与节点合并

在加入前置处理之后，抽取得到的图谱质量有所提升，但新的问题仍然明显：

- 同义节点很多
- 同一概念在多个 chunk 中被重复抽取
- 缩写与全称并存，导致图谱冗余

为此，加入了后置处理的“节点名称审阅 + alias 合并”流程。

相关脚本包括：

- [collect_normalization_candidates.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/collect_normalization_candidates.py)
- [merge_nodes_by_aliases.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/merge_nodes_by_aliases.py)
- [aliases/](/Users/loki/Workspace/GraduationDesign/knowledge_graph/aliases)

### 1. 候选名称提取

先从抽取结果中按标签提取 `label -> name[]`：

- `Disease`
- `Medication`
- `LabTest`
- `Pathogen`
- 等

然后将这些名称交给人工和大模型辅助审阅，判断哪些属于同义实体。

### 2. 人工维护 alias 规则

没有选择“全自动实体融合”，而是采用“人工维护 alias 文件 + 规则合并”的方式。

原因是：

- 医学同义关系容错成本低，一旦合并错会污染底座
- 有些相似名称其实不是同一概念
- 某些标签（例如 `Recommendation`、`GuidelineSection`）不能简单跨文档合并

### 3. 合并效果

这一轮后，类似下列实体已经能较稳定合并：

- `PCP` -> `肺孢子菌肺炎 (PCP)`
- `SMZ-TMP` -> `复方磺胺甲噁唑 (TMP-SMX)`
- `AIDS` / `HIV感染` 等高频表达

这一轮的核心收益是：

- 图谱冗余显著下降
- 节点命名更一致
- 为 Neo4j 中的查询和子图浏览提供了更干净的底座

## 五、第三次重要迭代：发现孤立节点问题并引入 repair 流程

完成前置处理和 alias 合并后，图谱已经可以导入，但在真实入库和抽样检查时发现一个非常关键的问题：

- 仍然存在大量孤立节点

这些孤立节点主要集中在：

- `LabFinding`
- `LabTest`
- `Medication`
- `ClinicalAttribute`
- 部分 `Recommendation`

这说明问题已经从“节点抽不出来”转向“节点抽出来了，但关系不完整”。

为此，加入了关系修补阶段：

- [repair_relations_with_llm.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/repair_relations_with_llm.py)

### 1. repair 的最初目标

repair 的目标不是重抽整个图，而是定向处理：

- 孤立节点
- 缺少业务出边的 `Recommendation`

### 2. repair 的具体做法

以单个 chunk 为单位：

- 找出可疑节点
- 提供当前 chunk 原文
- 提供当前 chunk 的已有节点和边
- 让大模型只做“补边”或“建议删点”

### 3. repair 过程中遇到的问题

第一版 repair 很快暴露出新的问题：

- 删点过于激进
- 有些业务节点本身是有效的，只是当前 chunk 里还没被正确挂边
- 新增边偶尔方向错误或关系类型不合理

例如后续抽样里看到的高风险现象包括：

- 某些 chunk 删除了大量 `Medication` / `LabTest`，但新增边却很少
- 部分 `COMPLICATED_BY` 关系方向明显反了

### 4. repair 流程的收敛

为降低风险，最终将 repair 策略收敛为：

- 默认只补边，不删业务节点
- `drop_node_ids` 仅作为建议记录，不自动执行
- 增加本地规则拦截明显反向或不合理的边
- 对失败 chunk 支持定向 retry
- 重试时附加上一轮失败原因，避免纯概率重试
- 缩小输入到局部子图，减少超时和 JSON 截断

最终，repair 成为了第一阶段非常关键的一步：

- 它不是替代原始抽取
- 而是对“抽出了点、但没成图”的结果做定向补强

## 六、最终收敛的第一阶段主流程

到目前为止，第一阶段已经收敛为以下 7 步：

1. 原始 Markdown 清理
2. 大模型抽取 `nodes / edges`
3. 关系修补（repair）
4. 提取待统一名称
5. 人工维护 `aliases`
6. 按 alias 合并图谱
7. 导入 Neo4j

对应脚本分别是：

1. [clean_markdown.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/clean_markdown.py)
2. [pipeline.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/pipeline.py)
3. [repair_relations_with_llm.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/repair_relations_with_llm.py)
4. [collect_normalization_candidates.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/collect_normalization_candidates.py)
5. `aliases/*.json`
6. [merge_nodes_by_aliases.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/merge_nodes_by_aliases.py)
7. [import_merged_graph.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/import_merged_graph.py)

## 七、最终验收结果

在完成前置处理、后置合并与 repair 之后，对 Neo4j 中的图谱做了抽样验收，得到的主要结论如下。

### 1. 好的信号

#### 结构完整性较好

- 图谱节点数：`2715`
- 图谱边数：`3810`

并且与这一轮导入摘要完全一致，说明：

- merge 输出和入库结果对得上
- 导入流程本身是稳定的

#### 基础字段质量较好

- 空名称节点：`0`
- 缺 `test_id/operator` 的 `LabFinding`：`0`
- 缺 `recommendation_text` 的 `Recommendation`：`0`

这说明关键节点的结构化字段质量已经达到较好的稳定度。

#### alias 合并效果较好

例如以下同义表达已经可以稳定合并：

- `PCP` -> `肺孢子菌肺炎 (PCP)`
- `SMZ-TMP` -> `复方磺胺甲噁唑 (TMP-SMX)`
- `AIDS` / `HIV感染`

#### 标签与关系分布已经像一张可用图谱

节点前几类：

- `Recommendation 441`
- `Medication 276`
- `ClinicalAttribute 254`
- `LabFinding 230`

关系前几类：

- `RECOMMENDS 767`
- `REQUIRES_DETAIL 311`
- `TREATED_WITH 272`
- `APPLIES_TO 241`

#### 文档结构链基本成立

例如：

- `HIV/AIDS患者新型冠状病毒感染临床诊治专家共识` 有 `19` 个 section
- `中国艾滋病诊疗指南 (2024 版)` 有 `13` 个 section

这说明图谱已经不只是医学事实图，也保留了较好的文档结构信息。

### 2. 仍然存在的问题

#### 仍有少量孤立节点

当前残留的孤立节点主要包括：

- `LabFinding: 25`
- `LabTest: 24`
- `Medication: 21`
- `ClinicalAttribute: 14`

这说明图谱已经比最初好很多，但仍有部分点没有挂上合理关系。

#### 仍有少量关系方向或语义可疑

抽样中看到的代表性问题包括：

- `PCP -[:BELONGS_TO_CLASS]-> Assertion(PCP治疗方案)`
- `PCP -[:COMPLICATED_BY]-> 获得性免疫缺陷综合征`
- `HIV感染/AIDS合并结核分枝杆菌感染 -[:CAUSED_BY]-> TB-IRIS`
- `活动性结核病 -[:APPLIES_TO]-> 经验性抗菌治疗/诊断性抗结核治疗`

这些问题说明：

- 当前图谱已经可以用，但还没有达到“语义完全干净”的程度
- 如果后续要做高置信规则推理，还需要再做一轮关系语义清洗

## 八、当前阶段的总体判断

基于上述结果，当前第一阶段图谱可以被定义为：

- **可入库**
- **可浏览**
- **可检索**
- **可作为第二阶段问诊原型的知识底座**

但还不建议直接将其视为：

- 完全自动化、无需人工纠偏的最终图谱
- 高置信的规则推理库

更准确地说，当前状态是：

- “可用于原型开发”
- “可用于问诊大脑的检索与验证底座”
- “可继续迭代清理语义层”

## 九、后续待完善的重点方向

后续如果继续提升第一阶段图谱质量，建议重点关注以下几个方向。

### 1. 关系语义清洗

重点关注以下关系类型：

- `BELONGS_TO_CLASS`
- `COMPLICATED_BY`
- `CAUSED_BY`
- `APPLIES_TO`

建议做法：

- 先用规则脚本筛明显异常关系
- 再对少量拿不准的边做定向 LLM 裁决

### 2. 继续清理孤立节点

当前残留孤立节点主要是：

- `LabFinding`
- `LabTest`
- `Medication`
- `ClinicalAttribute`

建议后续做更细粒度的定向补边，而不是再做一次全量大修。

### 3. 继续扩充 alias 规则

虽然当前 alias 合并已经有效，但随着资料继续增加：

- 同义节点会继续增多
- 新专题中的缩写 / 全称映射还需要补充

因此 `aliases/` 仍然是一个持续维护对象。

### 4. 进一步提升 recommendation 链条质量

虽然 `Recommendation` 数量很多，且大部分已有出边，但后续如果要做问诊解释链，还可以继续加强：

- `Recommendation -> RECOMMENDS -> target`
- `Recommendation -> APPLIES_TO -> population`
- `Recommendation -> SUPPORTED_BY -> evidence`

## 十、对论文写作的建议记录方式

后续在论文中，可以把第一阶段的建设历程概括为：

1. 从“直接抽取”出发
2. 发现原始资料噪声和表格问题，加入前置处理
3. 发现同义节点冗余，加入人工控制的 alias 合并
4. 发现孤立节点和缺边问题，加入 repair 流程
5. 最终形成“前置清理 + 抽取 + 修补 + 人工别名合并 + 入库”的完整链路

这样的叙述方式有几个优点：

- 能体现你不是一次成型，而是通过工程迭代逐步收敛
- 能解释为什么既使用大模型，又保留人工在环
- 能说明最终图谱为什么“可用但仍需持续清理”

## 十一、一句话总结

第一阶段最终形成的不是一条“纯自动化抽取链”，而是一条：

- 以大模型为核心抽取能力
- 以前置清理和后置规则为稳定器
- 以人工维护 alias 和定向 repair 为质量保障
- 最终可支撑第二阶段问诊系统开发的知识图谱工程链路

这也是当前项目从“资料整理”真正迈向“系统建设”的第一道关键底座。
