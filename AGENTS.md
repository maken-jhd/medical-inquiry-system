# AGENTS.md

本文件给后续在本仓库工作的 coding agent 使用。请优先遵守当前用户请求；本文件只记录仓库内的长期约定与常用入口。

## 项目定位

- 这是一个面向 HIV/AIDS 场景的智能问诊系统毕业设计项目。
- `knowledge_graph/` 是当前激活的第一阶段搜索专用知识图谱处理链：清理医学资料、LLM 抽取诊断问诊子图、alias 合并、导入 Neo4j。
- `knowledge_graph_bak/` 是旧版全量指南知识图谱备份，已经废弃；除非用户明确要求恢复旧链路，否则只把它当历史参考，不要用于当前入库、replay 或实时问诊。
- `brain/` 是第二阶段问诊大脑：A1/A2/A3/A4、KG 检索、MCTS/UCT、rollout、终止规则、报告生成。
- `simulator/` 是虚拟病人与离线回放评测：病例 schema、病人代理、自动对战、benchmark。
- `frontend/` 是 Streamlit 演示界面：回放模式、实时运行模式、实验复盘模式。
- `configs/` 保存第二阶段配置，`docs/` 保存阶段清单和复盘资料，`tests/` 保存第二阶段测试。

## 工作原则

- 仓库文档以中文为主，新增说明、代码注释、测试注释优先使用中文。
- `brain/`、`simulator/`、`tests/` 已约定：文件顶部有中文说明，类/函数或测试函数附近保留简短中文用途说明。
- 保持现有 Python 风格：类型标注、`dataclass`、轻量配置对象和显式依赖注入。
- 不要把真实 API Key、Neo4j 私密密码或本机配置提交进仓库。私密前端配置写入 `configs/frontend.local.yaml`，该文件已被忽略。
- 不要随意提交或依赖大型本地产物：`HIV/`、`HIV_cleaned/`、`test_outputs/`、`output_graph_test*.jsonl` 都被忽略。
- `.gitignore` 默认忽略 `*.sh`，只有少数演示/评测脚本被显式放行；新增 shell 脚本如需入库，要同步检查 `.gitignore`。
- 触碰 `knowledge_graph/` 时，默认遵循搜索专用本体：`Disease`、`Symptom`、`Sign`、`ClinicalAttribute`、`LabTest`、`LabFinding`、`ImagingFinding`、`Pathogen`、`RiskFactor`、`RiskBehavior`、`PopulationGroup` 等，关系重点是 `R1/R2/A3/A4` 会消费的诊断与证据边。
- 当前抽取端为证据节点预留 `acquisition_mode` 和 `evidence_cost`，用于后续区分可直接询问证据和高成本检查证据；除非用户明确要求，本字段预留不应顺手改动 `brain/` 的搜索排序。
- 不要把旧版全量指南图谱的 `Recommendation`、`Medication`、`TreatmentRegimen`、`GuidelineDocument`、`EvidenceSpan`、`Assertion` 等标签重新加回当前活跃抽取端，除非用户明确要求切回旧方向。

## 常用命令

推荐环境：

```bash
conda activate GraduationDesign
```

运行单元测试：

```bash
conda run -n GraduationDesign python -m pytest -q
```

说明：仓库 README 明确建议使用 `python -m pytest`，直接运行 `pytest` 在部分环境下可能有导入路径问题。

启动 Streamlit 演示：

```bash
./scripts/run_streamlit_realtime.sh
```

或：

```bash
conda run -n GraduationDesign streamlit run frontend/app.py --browser.gatherUsageStats false
```

构建当前搜索专用知识图谱：

```bash
./knowledge_graph/run_search_kg_pipeline.sh
```

说明：该入口默认输出到 `test_outputs/search_kg/search_kg_<timestamp>/`，不会运行可选的孤立节点 relation repair，也不会自动导入 Neo4j；如需导入设置 `IMPORT_TO_NEO4J=true`。

如果需要对最近一次搜索图谱输出修补孤立节点关系，可运行：

```bash
./knowledge_graph/run_repair_relations_with_llm.sh
```

如果用户已经人工维护了某次输出目录下的 `aliases/`，优先用：

```bash
SEARCH_KG_OUTPUT_ROOT=对应输出目录 SKIP_EXTRACTION=true ./knowledge_graph/run_search_kg_pipeline.sh
```

这会跳过 LLM 抽取，复用该目录的 `output_graph.jsonl` 并优先读取该目录的 `aliases/`。

清空 Neo4j 旧图谱、导入当前搜索图谱并生成校验报告：

```bash
./knowledge_graph/run_reload_search_kg_neo4j.sh
```

真实 Neo4j smoke 需要本地 Neo4j 与密码：

```bash
NEO4J_PASSWORD=你的密码 conda run -n GraduationDesign python scripts/run_retriever_smoke.py --features 发热,干咳
```

真实端到端 replay 会调用 Neo4j 与 LLM，运行前确认 `DASHSCOPE_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` / `NEO4J_PASSWORD`：

```bash
conda run -n GraduationDesign python scripts/run_batch_replay.py --max-turns 5
```

## 配置与外部依赖

- `configs/brain.yaml` 会被 `brain/service.py` 默认构造逻辑读取，影响 MCTS、检索、A1-A4、终止规则与 verifier 行为。
- `configs/frontend.yaml` 保存非敏感默认配置；`configs/frontend.local.yaml` 用于本机密钥和私密覆盖。
- 默认 LLM 路径使用 DashScope compatible OpenAI 接口，模型通常为 `qwen3-max`。
- 实时模式和部分 smoke 依赖本地 Neo4j，默认 URI 是 `bolt://localhost:7687`。
- 常规单元测试应尽量避免依赖真实 Neo4j 或真实 LLM；需要外部服务时，在最终回复中明确说明。

## 测试与变更范围

- 小范围代码改动优先跑对应测试文件，例如：

```bash
conda run -n GraduationDesign python -m pytest tests/test_stop_rules.py -q
```

- 触碰 `brain/service.py`、路由、终止规则、replay 或报告字段时，优先考虑补跑相关服务流、router、stop_rules、report_builder、replay_engine 测试。
- 触碰 `frontend/` 时，至少确认 import/语法层面无误；启动 Streamlit 可能需要本地依赖和端口可用。
- 不要重写无关模块或清理用户未请求的历史输出。

## 重要入口

- 总体说明：`README.md`
- 问诊大脑说明：`brain/README.md`
- 虚拟病人与离线回放说明：`simulator/README.md`
- 当前搜索专用知识图谱处理链说明：`knowledge_graph/README.md`
- 已废弃的旧版全量指南图谱备份说明：`knowledge_graph_bak/README.md`
- 前端演示说明：`frontend/README.md`
- 测试说明：`tests/README.md`
- 第二阶段变更脉络：`docs/phase2_changelog.md`
- 第二阶段执行清单：`docs/phase2_execution_checklist.md`
