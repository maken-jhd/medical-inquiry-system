# 诊断系统当前待完善点 Todo

## 1. 文档目的

本文档专门记录当前诊断系统仍待完善的点，作为后续迭代的待办清单。

它重点回答三个问题：

1. 当前系统已经固定了哪些边界，不应该再反复摇摆
2. 现阶段最值得优先解决的问题是什么
3. 每一类问题后续应如何验收，避免“改了很多但很难判断是否真的变好”

补充说明：

- 本文关注的是 `brain/` 为主的诊断链路，不是图谱抽取端的全量规划文档
- 图谱驱动虚拟病人与 replay 只在影响诊断质量、稳定性和评测可信度时纳入
- 过程复盘见 [phase2_changelog.md](/Users/loki/Workspace/GraduationDesign/docs/phase2_changelog.md)
- 总体阶段路线见 [phase2_execution_checklist.md](/Users/loki/Workspace/GraduationDesign/docs/phase2_execution_checklist.md)

## 2. 当前固定边界

下面这些不是本轮待重新讨论的话题，而是当前已经明确的实现边界：

- 长文本抽取与解释坚持 `LLM-first`，不再恢复“大规则词典兜底”的旧路线
- 确定性规则层只保留极薄能力，主要用于短答识别，如“有 / 没有 / 不太清楚”
- normalization 独立成层，位置固定为“LLM 输出之后、Neo4j / candidate mapping 之前”
- `EntityLinker` 目前继续沿用 lexical 方案，不在当前阶段引入 embedding / cosine 相似度
- batch replay 对单病例 LLM 失败采用 `status=failed` 并继续整批运行
- 实时前端对 LLM 领域错误采用显式报错停止，不再偷偷降级

这意味着后续完善方向应优先放在：

- 提升 LLM 主链路的稳定性、时延和可解释性
- 提升 normalization、entity linking、A2/A3/A4 的真实诊断质量
- 提升 replay / benchmark 对问题的暴露能力

而不是回到“继续补大词典，把 LLM 问题藏起来”的路线。

## 3. 优先级划分

本文按 `P0 / P1 / P2` 组织：

- `P0`：近期必须优先解决，不解决会直接影响真实 replay、现场演示或系统可信度
- `P1`：高价值增强，影响诊断质量和工程可维护性，但可以排在 `P0` 之后
- `P2`：中长期优化，更多面向论文完整性、长期演进或大规模评测

## 4. P0：近期优先完成

### 4.1 长尾耗时与失控搜索治理

现状：

- competitive replay 已从“毫秒级空转”修到“能真实走 LLM + 搜索”
- 但仍出现少数单病例极慢、单轮 `brain_turn_seconds` 极大的长尾问题
- 当前日志能看到病例级心跳，但还不够快定位“究竟卡在 A1 / R2 / rollout / verifier / repair 的哪一层”

待办：

- [ ] 为 `A1 / A4 / exam_context / verifier / rollout` 增加更细的阶段级耗时统计
- [ ] 给单轮 brain 推理增加预算控制，避免个别病例在某一轮无限拉长
- [ ] 给 rollout / verifier / repair 增加更明确的预算与提前截断条件
- [ ] 在 replay 输出中增加“最慢阶段”与“最慢 prompt”级别的诊断信息
- [ ] 建立一组固定的 `slow competitive cases` 作为性能回归集

完成信号：

- [ ] `competitive smoke` 不再出现单病例运行十几分钟以上的长尾
- [ ] 能从 `run.log / status.json / replay_results.jsonl` 直接判断慢点位于哪一层

### 4.2 LLM 结构化输出稳定性仍需继续收紧

现状：

- 现在已经从静默 fallback 改成了显式错误传播
- 但 `LLM-first` 的代价是：一旦 prompt 约束不够稳，就会直接暴露成 `empty_extraction / invalid_output / stage_failed`
- 当前 `MedExtractor`、`A1`、`A4 target-aware interpretation`、`exam_context` 仍主要依赖 prompt 质量和 schema 遵守率

待办：

- [ ] 为 `A1`、`a4_target_answer_interpretation`、`exam_context_interpretation` 增加更贴近真实病人表达的 few-shot 样例
- [ ] 明确覆盖转折句、否定句、不确定句、口语表达、夹带无关信息的长回答
- [ ] 继续收紧枚举字段和必填字段，减少“结构看似成功、信息其实为空”的情况
- [ ] 为各 prompt 增加版本号或 schema 版本字段，便于回放结果对齐
- [ ] 对 `llm_empty_extraction / llm_output_invalid` 做病例集统计，而不是只看单例报错

完成信号：

- [ ] 小样本 competitive / exam_context / negation smoke 中，空抽取和非法 payload 明显下降
- [ ] 失败时能快速判断是 prompt 问题、schema 问题还是病例开场问题

### 4.3 normalization 与 KG 对齐仍不够系统

现状：

- [brain/normalization.py](/Users/loki/Workspace/GraduationDesign/brain/normalization.py) 已建立集中式归一化入口
- 但它现在更像“人工收过一轮的核心映射表”，还不是从图谱 alias 与 replay 数据闭环生成的长期机制
- 一旦 LLM 抽到的名称和图谱节点存在口语差、缩写差、检查名变体差，仍可能影响后续 linking 与检索

待办：

- [ ] 梳理 normalization 的唯一数据来源，明确哪些映射来自 KG alias，哪些来自问诊口语
- [ ] 基于 replay 结果补一条“未命中 mention 审计”链路，输出高频未归一化表达
- [ ] 重点补齐 HIV / ART / 宿主状态 / 常见化验 / 常见影像名称的常见变体
- [ ] 区分 `疾病名 / 症状名 / 检查名 / 检查结果名` 的归一化命名空间，减少跨类型误归一
- [ ] 形成“新增 alias 如何进入 normalization”的固定维护流程

完成信号：

- [ ] 能产出高频未命中 mention 清单，而不是靠人工偶然发现
- [ ] 高频口语表达和检查缩写能稳定映射到图谱可消费名称

### 4.4 replay 与前端的可观测性还不够深

现状：

- 当前已经能看到 `llm_available`、病例级耗时、`failed` 病例和结构化错误
- 但对于诊断系统调试来说，还缺少“这一轮具体发生了什么”的统一复盘视角

待办：

- [ ] 在 replay 结果中增加更清楚的阶段级摘要，如 `A1 source`、`A4 interpretation source`、`exam_context source`
- [ ] 增加失败病例专用视图，优先展示 `error.code / stage / prompt_name / attempts`
- [ ] 在前端实验复盘模式中补充 `failed` 病例浏览与过滤
- [ ] 为慢病例增加“耗时拆分卡片”或等价字段，避免只看到总秒数

完成信号：

- [ ] 不打开源码也能快速判断某个失败或慢病例的大致问题类型
- [ ] 前端复盘模式可以直接筛出失败病例和长尾病例

## 5. P1：高价值增强

### 5.1 EntityLinker 还停留在 lexical 基础版

现状：

- 当前 `EntityLinker` 已接入 normalization，较之前稳定很多
- 但核心仍是 lexical matching，面对近义表达、缩写歧义、跨类别同名项时仍偏脆弱

待办：

- [ ] 在现有 lexical 框架内先补候选生成与歧义消解，而不是立刻换向量方案
- [ ] 让 linking 更明确地消费 `label / stage / question_type / target context`
- [ ] 输出更多候选与打分解释，方便人工排查“为什么连到这个节点”
- [ ] 对 HIV / 艾滋病、CT / 胸部CT、G 试验 / β-D-葡聚糖检测 这类高频别名建立 focused regression

完成信号：

- [ ] 常见歧义表达的链接结果更稳定
- [ ] 调试时能看出“是没候选、候选排序错、还是 normalization 前就没对齐”

### 5.2 A2 / A3 的鉴别诊断质量仍可继续加强

现状：

- 当前系统已经能跑 `A1 -> A2 -> A3 -> A4`
- 但在 competitive 病例里，后续追问是否真正围绕“区分 target 与 competitor 的关键证据”展开，仍有提升空间

待办：

- [ ] 继续增强 competing hypothesis 场景下的 `target-only / competitor-only / shared` 证据利用
- [ ] 控制重复问同一 evidence family 的问题，减少“问了很多，但都是同一类信息”
- [ ] 更明确地区分低成本可直接问证据与需要检查才能获得的高成本证据
- [ ] 对 `repair action` 是否真的补到了 verifier 指出的缺口做更细评估
- [ ] 建立“首个正确鉴别证据出现轮次”的回归指标

完成信号：

- [ ] competitive replay 中，系统更早问到真正有鉴别价值的问题
- [ ] 重复语义问题和重复 family 问题减少

### 5.3 exam_context 解析到证据节点的映射还可以更强

现状：

- 当前 `exam_context` 已由 LLM 负责自由文本解释，再由确定性逻辑回填 candidate evidence
- 这条路线是对的，但还需要继续打磨“检查名、检查结果、阈值型 finding、阴性结果”的映射精度

待办：

- [ ] 补更多 `CD4 / HIV RNA / β-D-葡聚糖 / 胸部CT / 病原学检查` 的 focused case
- [ ] 区分“做过某项检查”和“已经明确给出某项阳性/阴性结果”
- [ ] 强化阈值型 finding 的结果归一化，如 `低 / 高 / 阳性 / 阴性 / 未检出`
- [ ] 审查 candidate evidence mapping 的阈值与截断规则，避免一句话误命中过多节点

完成信号：

- [ ] 常见 HIV 相关检查语句能稳定映射回正确 evidence 节点
- [ ] 误命中多个候选节点的情况下降

### 5.4 verifier / stop 仍有进一步统一空间

现状：

- 当前 `A1 + A4 verify_evidence + exam_context` 已经切到 LLM-first
- 但 `trajectory_evaluator.py` 中的 verifier 仍保留了一部分 fallback 推断和 schema 兼容逻辑

待办：

- [ ] 明确 verifier 未来是否也要进一步朝“更纯的显式错误传播”收敛
- [ ] 继续减少 schema 不合规时的隐式推断空间
- [ ] 复盘 verifier、guarded gate、repair 三者的职责边界，避免彼此重叠
- [ ] 补充“答案已基本正确但 verifier 持续拒停”的 focused case

完成信号：

- [ ] stop reason 更稳定、更易解释
- [ ] `accepted_wrong`、`correct_but_rejected` 这两类错误能更清楚地归因

### 5.5 代码结构还可以继续拆薄

现状：

- [brain/evidence_parser.py](/Users/loki/Workspace/GraduationDesign/brain/evidence_parser.py) 在经历多轮迭代后仍然偏大
- 当前虽然功能能跑通，但阅读和后续继续修改的成本仍偏高

待办：

- [ ] 评估是否将 `A1`、`A4 target interpretation`、`exam_context interpretation` 拆成更独立的解释器
- [ ] 清理重构后遗留的旧 helper、兼容分支和历史命名
- [ ] 让 prompt 构造、payload 校验、normalization、slot update 更容易分层测试

完成信号：

- [ ] 关键文件职责更单一
- [ ] 后续改 prompt 或改映射时，不必同时理解整份大文件

## 6. P2：中长期优化

### 6.1 建立更系统的 focused benchmark 体系

现状：

- 当前有 replay、competitive 病例、graph-case sampling 等工具
- 但还缺少一组固定、可持续回归的“诊断链路专项用例集”

待办：

- [ ] 建立 `negation / uncertainty / target-aware / exam_context / slow-case / strong-alternative` 几类 focused case 集
- [ ] 对每类 case 明确主要观察指标，而不是只看最终是否 completed
- [ ] 为重构后的关键模块建立更长期稳定的 smoke 入口

完成信号：

- [ ] 后续每改一轮 prompt、normalization 或 A3 策略，都能快速回归核心风险点

### 6.2 构建“replay 发现问题 -> 反哺 KG / normalization / prompt”的闭环

现状：

- 目前已经开始有这种工作方式，但还比较人工

待办：

- [ ] 让 replay 输出更容易沉淀出“高频未命中 alias”“高频失败 prompt”“高频慢病例”
- [ ] 为 KG alias、normalization、prompt 迭代建立标准回流表
- [ ] 把“人工观察到的问题”尽量转成机器可汇总的报表

完成信号：

- [ ] 每轮 replay 后能更系统地知道下一轮应优先修哪类问题

### 6.3 更完整地对齐论文式 Med-MCTS 实验叙事

现状：

- 当前工程链路已经可运行
- 但从论文叙事角度看，很多模块还是“可工作的轻量实现”，还不是最终的完整实验体系

待办：

- [ ] 继续补强更深 rollout、path evaluation 和 verifier 协同的实验说明
- [ ] 补齐“为什么这样设计，而不是别的设计”的对比实验材料
- [ ] 形成更清晰的模块级消融路径，支撑论文方法章节与实验章节

完成信号：

- [ ] 诊断系统的工程实现和论文叙事之间更容易一一对应

## 7. 当前不建议优先做的事

为了控制范围，下面这些点当前不建议优先投入：

- [ ] 不优先把 deterministic 词典重新扩展成“全图谱覆盖解析器”
- [ ] 不优先把 `EntityLinker` 立刻切成 embedding / cosine 相似度方案
- [ ] 不优先同时大改 KG schema、brain 主链路和虚拟病人生成器三层

原因：

- 这几类改动范围都很大
- 很容易把问题来源重新搅混
- 当前更高价值的是先把 `LLM-first` 主链路的稳定性、性能和对齐问题收紧

## 8. 建议执行顺序

如果按最现实的收益顺序推进，建议这样排：

1. 先收 `P0` 中的长尾耗时、LLM 稳定性、normalization 对齐和可观测性
2. 再做 `P1` 中的 entity linking、A2/A3 鉴别质量、exam_context 映射和 verifier 边界
3. 最后再做 `P2` 中的 benchmark 闭环与论文叙事完善

一句话总结：

- 当前系统最缺的不是“再补一层大规则”，而是把 `LLM-first` 诊断链路做得更稳、更快、更可解释、更容易复盘。
