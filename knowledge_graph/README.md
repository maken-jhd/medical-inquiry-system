# Knowledge Graph Pipeline

本目录保存本项目第一阶段“医学知识工程与底层图谱搭建”的主要脚本、规则文件和运行入口。它负责把 `HIV/` 目录下的非结构化指南、专家共识和专题资料，逐步加工为可导入 Neo4j 的结构化知识图谱。

## 模块定位

这一阶段的目标不是直接做问诊前端，而是先把 HIV/AIDS 场景下的医学资料沉淀为一个尽可能稳定、可追溯、可人工校正的知识底座。

当前采用的总体路线是：

- 原始资料清理
- 大模型抽取 `nodes / edges`
- 定向关系修补
- 人工维护 `aliases`
- 规则合并节点与边
- 导入 Neo4j

这条路线强调：

- 大模型参与抽取和局部修补
- 关键合并决策由人工控制
- 中间产物全部保留，便于回溯和重跑

## 当前状态

当前这一阶段已经可以独立跑通，主流程已经收敛为：

1. [clean_markdown.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/clean_markdown.py)
2. [pipeline.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/pipeline.py)
3. [repair_relations_with_llm.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/repair_relations_with_llm.py)
4. [collect_normalization_candidates.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/collect_normalization_candidates.py)
5. `aliases/*.json`
6. [merge_nodes_by_aliases.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/merge_nodes_by_aliases.py)
7. [import_merged_graph.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/import_merged_graph.py)

当前推荐策略是：

- 抽取阶段允许大模型输出结构化节点和边
- 修补阶段默认“只补边，不删业务节点”
- 同义节点合并以人工维护的 `aliases/` 为准
- 最终图谱以合并后的 `merged_graph_by_aliases.json` 为准入库

为了便于后续论文撰写、实验复盘和版本追踪，本目录还单独整理了一份第一阶段建设历程文档：

- [build_retrospective.md](/Users/loki/Workspace/GraduationDesign/knowledge_graph/build_retrospective.md)

这份文档重点记录：

- 最初方案为何效果不理想
- 为什么要加入前置处理、后置 alias 合并与 repair
- 这些迭代各自解决了什么问题
- 当前图谱的最终验收结论
- 后续还需要继续清理的重点关系类型

## 当前质量判断

基于当前版本的入库验收结果，可以对第一阶段图谱做如下判断：

- 已达到“可入库、可浏览、可继续开发”的状态
- 已适合作为第二阶段问诊原型的知识底座
- 基础字段质量较好，关键结构字段已较稳定
- alias 合并效果较好，图谱冗余已明显下降
- 但仍有少量孤立节点和少量语义方向可疑的关系

当前如果继续提升图谱质量，最值得优先盯住的关系类型包括：

- `BELONGS_TO_CLASS`
- `COMPLICATED_BY`
- `CAUSED_BY`
- `APPLIES_TO`

## 目录结构

当前 `knowledge_graph/` 目录如下：

```text
knowledge_graph/
├── aliases/
├── build_retrospective.md
├── scripts/
│   └── neo4j_init.cypher
├── clean_markdown.py
├── pipeline.py
├── repair_relations_with_llm.py
├── collect_normalization_candidates.py
├── merge_nodes_by_aliases.py
├── import_merged_graph.py
├── run_clean_markdown.sh
├── run_pipeline.sh
├── run_repair_relations_with_llm.sh
├── run_collect_normalization_candidates.sh
├── run_merge_nodes_by_aliases.sh
├── run_import_merged_graph.sh
└── README.md
```

其中：

- [aliases](/Users/loki/Workspace/GraduationDesign/knowledge_graph/aliases) 保存按标签维护的人工别名合并规则
- [build_retrospective.md](/Users/loki/Workspace/GraduationDesign/knowledge_graph/build_retrospective.md) 记录第一阶段知识图谱建设历程、踩坑、修复策略与最终验收结果
- [scripts/neo4j_init.cypher](/Users/loki/Workspace/GraduationDesign/knowledge_graph/scripts/neo4j_init.cypher) 用于初始化 Neo4j 约束和索引
- 各 `run_*.sh` 文件是第一阶段的标准运行入口

## 上下游目录关系

虽然脚本集中放在本目录，但它们会读写项目根目录下的资料和产物。

主要相关目录包括：

- 原始资料目录：
  - [HIV](/Users/loki/Workspace/GraduationDesign/HIV)
- 清理后资料目录：
  - [HIV_cleaned](/Users/loki/Workspace/GraduationDesign/HIV_cleaned)
- 小范围测试输入：
  - [test](/Users/loki/Workspace/GraduationDesign/test)
- 中间产物目录：
  - [test_outputs](/Users/loki/Workspace/GraduationDesign/test_outputs)

目前 `HIV/` 资料的组织方式主要是：

- `HIV AIDS 本身`
- `HIV 合并机会性感染`
- `HIV阳性的孕产妇`
- 各类单独专题文件，例如慢性肾病、肥胖、骨质疏松、高血脂等

这意味着该阶段处理的不只是单一指南，而是一个逐渐扩展的 HIV 领域资料库。

## 当前工程环境

推荐环境：

- conda 环境：`GraduationDesign`
- Python：`3.10.x`

已用到的主要依赖：

- `openai`
- `neo4j`
- `langchain`
- `langchain_community`

推荐先执行：

```bash
conda activate GraduationDesign
```

## 当前已实现脚本与功能

### 1. Neo4j 初始化脚本

文件：

- [neo4j_init.cypher](/Users/loki/Workspace/GraduationDesign/knowledge_graph/scripts/neo4j_init.cypher)

功能：

- 创建核心标签唯一约束
- 创建高频查询索引
- 覆盖 `Disease`、`Symptom`、`LabFinding`、`Recommendation`、`Assertion` 等核心标签

### 2. 原始文档清理脚本

文件：

- [clean_markdown.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/clean_markdown.py)

功能：

- 遍历 `HIV/` 或指定目录下的 Markdown 文件
- 统一换行、去除 BOM 和零宽字符
- 统一标题格式，支持到 `#####`
- 去除重复标题、折叠多余空行
- 统一术语写法，例如 `CD4+ T`、`CD4⁺T`、`CD4 T`
- 统一单位和符号写法，例如 `个/μL`、`cells/μL`、`~`、`～`、`≥`
- 识别复杂 Markdown 表格
- 调用大模型将复杂表格转写为更适合抽取的正文
- 可选对表格转写结果做二次校验
- 支持并发处理多个文件
- 生成清理报告与表格首轮草稿文件

### 3. 图谱抽取脚本

文件：

- [pipeline.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/pipeline.py)

功能：

- 按标题层级切分 Markdown 文本
- 保持医学上下文完整，避免过碎切片
- 调用大模型输出 `nodes / edges`
- 对关键节点做结构校验
- 对 `LabFinding`、`Recommendation` 等节点做本地修复
- 将结果按 JSONL 逐块追加写入
- 对失败块生成单独错误日志
- 支持按错误日志定向重试失败块

### 4. 关系修补脚本

文件：

- [repair_relations_with_llm.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/repair_relations_with_llm.py)

功能：

- 读取原始抽取结果
- 自动识别孤立节点
- 自动识别缺少业务出边的 `Recommendation`
- 仅在单个 chunk 内补边，避免跨 chunk 串联
- 通过大模型补充关系
- 本地拦截明显反向或不合理的边
- 默认不自动删业务节点
- 支持对失败 chunk 做定向 retry

### 5. 候选名称提取脚本

文件：

- [collect_normalization_candidates.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/collect_normalization_candidates.py)

功能：

- 读取抽取或修补后的 JSONL
- 只提取节点 `label` 和 `name`
- 输出 `label -> name[]` 的唯一名称列表
- 供后续人工审阅和补充 `aliases/`

### 6. alias 合并脚本

文件：

- [merge_nodes_by_aliases.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/merge_nodes_by_aliases.py)

功能：

- 读取抽取或修补后的 JSONL
- 根据 `aliases/` 中人工确认过的规则合并节点
- 同步重写边的 `source_id` 和 `target_id`
- 输出统一的合并图谱 JSON
- 输出合并报告

### 7. Neo4j 导入脚本

文件：

- [import_merged_graph.py](/Users/loki/Workspace/GraduationDesign/knowledge_graph/import_merged_graph.py)

功能：

- 读取 `merged_graph_by_aliases.json`
- 按节点标签和关系类型分批写入 Neo4j
- 使用 `id` 做幂等 `MERGE`
- 支持通过环境变量配置 Neo4j 连接参数和批大小

## 运行方式

### 推荐主流程

推荐按下面顺序执行：

```bash
./knowledge_graph/run_clean_markdown.sh
./knowledge_graph/run_pipeline.sh
./knowledge_graph/run_repair_relations_with_llm.sh
./knowledge_graph/run_collect_normalization_candidates.sh
```

人工补充 `aliases/` 后继续：

```bash
./knowledge_graph/run_merge_nodes_by_aliases.sh
./knowledge_graph/run_import_merged_graph.sh
```

### 常见说明

- `run_clean_markdown.sh` 会优先处理原始资料并输出到 `HIV_cleaned/`
- `run_pipeline.sh` 默认优先读取 `HIV_cleaned/`，不存在时回退到 `HIV/`
- `run_repair_relations_with_llm.sh` 默认“只补边，不删业务节点”
- `run_collect_normalization_candidates.sh` 建议读取修补后的 JSONL
- `run_merge_nodes_by_aliases.sh` 建议同样读取修补后的 JSONL
- `run_import_merged_graph.sh` 建议以最终的 `merged_graph_by_aliases.json` 为准导入

### Retry 说明

抽取阶段支持：

```bash
./knowledge_graph/run_pipeline.sh retry
```

关系修补阶段支持：

```bash
./knowledge_graph/run_repair_relations_with_llm.sh retry
```

关系修补的 `retry` 模式会只重跑上一轮失败的 chunk，并将成功结果自动回填到基线结果里。

## 主要输入与输出

### 输入

- 原始资料：
  - [HIV](/Users/loki/Workspace/GraduationDesign/HIV)
- 清理后资料：
  - [HIV_cleaned](/Users/loki/Workspace/GraduationDesign/HIV_cleaned)

### 核心中间产物

- 原始抽取结果：
  - [output_graph_test.jsonl](/Users/loki/Workspace/GraduationDesign/output_graph_test.jsonl)
- 原始抽取错误日志：
  - [output_graph_test_errors.jsonl](/Users/loki/Workspace/GraduationDesign/output_graph_test_errors.jsonl)
- 关系修补输出目录：
  - [relation_repair](/Users/loki/Workspace/GraduationDesign/test_outputs/relation_repair)
- 待统一名称目录：
  - [normalization_candidates](/Users/loki/Workspace/GraduationDesign/test_outputs/normalization_candidates)
- alias 合并输出目录：
  - [alias_merge](/Users/loki/Workspace/GraduationDesign/test_outputs/alias_merge)

### 推荐最终入库源

- [merged_graph_by_aliases.json](/Users/loki/Workspace/GraduationDesign/test_outputs/alias_merge/merged_graph_by_aliases.json)

## 当前建模约定

当前图谱采用“可查询 + 可解释”的折中建模方式，重点包含：

- 临床事实层：
  - `Disease`
  - `DiseasePhase`
  - `Symptom`
  - `Sign`
  - `LabTest`
  - `LabFinding`
  - `OpportunisticInfection`
  - `Comorbidity`

- 干预决策层：
  - `Medication`
  - `DrugClass`
  - `TreatmentRegimen`
  - `PreventionStrategy`
  - `ManagementAction`
  - `Recommendation`

- 证据与解释层：
  - `GuidelineDocument`
  - `GuidelineSection`
  - `EvidenceSpan`
  - `Assertion`

当前特别约定：

- `Recommendation` 作为独立节点保留
- `LabFinding` 尽量结构化记录 `operator / value / unit`
- 对无法稳定数值化的结果，允许保留 `value_text`
- `REQUIRES_DETAIL` 用于支持问诊追问
- `CONTRAINDICATED_IN` 与 `NOT_RECOMMENDED_FOR` 用于表达禁忌与限制条件
- 跨文档同义实体优先通过人工维护 `aliases/` 后再合并

## 当前别名规则策略

目前节点统一不再依赖复杂自动后处理，而是采用人工维护的别名规则文件。

主要特点：

- 每个标签一个 alias 文件
- 由人工确认哪些名称应归并为同一节点
- `merge_nodes_by_aliases.py` 严格按这些规则执行

这种策略的优点是：

- 更可控
- 更适合医学场景下的高风险实体合并
- 更便于后续专家在环修正

## 后续可继续做的事

- 继续补充 `aliases/` 中高频别名规则
- 对残余孤立节点做定向质量检查
- 增加图谱质检脚本，自动筛明显异常边
- 优化 `merge_nodes_by_aliases.py` 的字段保留与报告能力
- 进一步细化 Neo4j 导入后的校验流程
