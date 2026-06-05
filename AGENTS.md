# AGENTS.md

本文件只保留后续 coding agent 需要长期记住、且不容易从代码里直接看出来的信息。实现细节优先跳到对应 README 和 docs，不在这里重复扩写。

## 项目速览

- 这是一个面向 HIV/AIDS 场景的智能问诊系统毕业设计仓库。
- 当前活跃链路只有三条：`knowledge_graph/` 负责搜索专用知识图谱，`brain/` 与 `simulator/` 负责问诊大脑和离线回放，`frontend/` 负责 Streamlit 演示。
- `knowledge_graph_bak/` 是旧版全量指南图谱备份，只作历史参考；除非用户明确要求，不要把旧本体、旧关系或旧导入链路带回当前系统。

## 快速定位

- 总览与常用入口：`README.md`
- 问诊大脑：`brain/README.md`
- 运行链路与 A1/A2/A3/A4：`docs/brain_runtime_call_chain_guide.md`
- 虚拟病人与 replay：`simulator/README.md`
- benchmark 设计：`docs/diagnosis_benchmark_experiment_design.md`
- 搜索图谱链路：`knowledge_graph/README.md`
- 搜索图谱本体：`docs/search_kg_label_guide.md`
- 前端演示：`frontend/README.md`

## 仓库级约定

- 文档、README、代码注释和测试说明优先中文。
- 保持现有 Python 风格：完整类型标注、`dataclass`、轻量配置对象、显式依赖注入。
- `brain/`、`simulator/`、`tests/` 的核心流程函数需要简短中文用途说明；长函数在关键阶段、分支入口和状态切换前补简短中文注释。
- 标签集合、关系集合、family tag、状态枚举、阈值分组等模块级常量，补中文说明“给谁用、为什么这样分组”。
- 不提交真实 API Key、Neo4j 密码或本机私密配置；私密前端配置放 `configs/frontend.local.yaml`。
- 不依赖或清理用户未明确要求的大型本地产物，例如 `HIV/`、`HIV_cleaned/`、`test_outputs/`。
- `.gitignore` 默认忽略 `*.sh`；新增 shell 脚本前先确认是否需要显式放行。
- 默认使用 `python -m pytest`，不要直接运行 `pytest`。
- 完成相对独立的实现工作后，除非用户明确限制范围，同步更新相关 README 与运行链路文档。

## 常用命令

```bash
conda activate GraduationDesign
conda run -n GraduationDesign python -m pytest -q
conda run -n GraduationDesign python -m pytest tests/test_acceptance_controller.py -q
conda run -n GraduationDesign streamlit run frontend/app.py --browser.gatherUsageStats false
./knowledge_graph/run_search_kg_pipeline.sh
./knowledge_graph/run_reload_search_kg_neo4j.sh
conda run -n GraduationDesign python scripts/run_batch_replay.py --max-turns 5
```

## 外部依赖与验证

- 默认 LLM 走 DashScope compatible OpenAI 接口；常见环境变量：`DASHSCOPE_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`、`NEO4J_PASSWORD`。
- 本地 Neo4j 默认 `bolt://localhost:7687`；需要真实 Neo4j 或真实 LLM 的 smoke / replay，在最终回复中明确说明依赖。
- 小改动优先跑最小测试切片；改 `brain/service.py`、router、acceptance、report、replay 时，补跑相邻服务流或回放测试。

## 任务路由

- 改问诊策略、搜索、奖励、终止：先看 `brain/service.py`、`brain/router.py`、`brain/mcts_engine.py`、`brain/reward_model.py`、`configs/brain*.yaml`。
- 改病例生成、虚拟病人或 benchmark：先看 `simulator/`、`scripts/run_batch_replay.py`、`docs/diagnosis_benchmark_experiment_design.md`。
- 改搜索图谱抽取或导入：先看 `knowledge_graph/README.md`、`docs/search_kg_label_guide.md`，再看 `knowledge_graph/` 当前入口脚本。
- 改演示界面：先看 `frontend/app.py`、`frontend/ui_adapter.py`、`frontend/README.md`；至少做 import 或语法级验证。
