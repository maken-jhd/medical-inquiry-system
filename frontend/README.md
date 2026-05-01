# Streamlit 演示前端

本目录提供一个面向中期检查的前后端一体演示界面。目标是快速、稳定地展示当前第二阶段问诊系统的核心能力：

- 多轮问诊对话
- A1 / A2 / A3 / A4 阶段结果
- 候选诊断排序
- 下一问选择与 repair action
- 搜索摘要
- 复核器与安全接受闸门
- 历史实验输出复盘

前端采用 `Streamlit + Python`，不引入 React / Vue 等前后端分离方案。

## 启动方式

在仓库根目录运行：

```bash
streamlit run frontend/app.py
```

如果当前环境没有安装 Streamlit：

```bash
pip install streamlit
streamlit run frontend/app.py
```

如果使用项目 conda 环境，可按你的环境名运行：

```bash
conda run -n GraduationDesign streamlit run frontend/app.py
```

仓库已提供项目级 Streamlit 配置：

```text
.streamlit/config.toml
```

该配置默认关闭 Streamlit 首次启动时的邮箱 / 使用统计提示，避免 `conda run` 启动时卡在交互式 onboarding。

如果仍然遇到邮箱提示，也可以使用显式参数启动：

```bash
conda run -n GraduationDesign streamlit run frontend/app.py --browser.gatherUsageStats false
```

## 模式 A：演示回放模式

这是默认模式，也是现场演示的保底模式。

特点：

- 不依赖 Neo4j
- 不依赖 DashScope / OpenAI API Key
- 不调用真实 LLM
- 直接加载本地 JSON demo
- 可以逐轮查看问诊对话、A1-A4、搜索摘要和安全机制

当前内置两个示例：

- `示例 1：PCP 模糊证据逐步收敛`
- `示例 2：PCP 与结核混淆时的安全拒停`

示例文件位于：

```text
frontend/demo_replays/pcp_provisional_success.json
frontend/demo_replays/tb_vs_pcp_safety_gate.json
```

## 模式 B：实时运行模式

实时模式会直接调用现有后端入口：

```python
brain.service.build_default_brain_from_env()
ConsultationBrain.process_turn(...)
```

实时模式最终会把配置映射到后端需要的环境变量。推荐使用 `configs/frontend.yaml` 与可选的 `configs/frontend.local.yaml`，等价字段如下：

```bash
export NEO4J_URI="bolt://localhost:7687"
export NEO4J_USER="neo4j"
export NEO4J_PASSWORD="你的 Neo4j 密码"
export NEO4J_DATABASE="neo4j"

export DASHSCOPE_API_KEY="你的 DashScope Key"
export OPENAI_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
export OPENAI_MODEL="qwen3-max"
```

如果这些环境变量、Neo4j 服务或 LLM 调用不可用，页面会显示中文错误提示；此时切回“演示回放模式”仍可完整展示。
当前实时模式也遵循后端的 `LLM-first` 约定：长文本抽取 / 解释不会静默退回规则链路；若 `LLM` 不可用、超时或结构化输出非法，页面会显示明确的中文领域错误并停止当前实时会话。

### 推荐一键启动方式

仓库提供了实时模式启动脚本：

```bash
./scripts/run_streamlit_realtime.sh
```

脚本只负责启动页面，不再注入实时运行配置。实时配置由 Python 在运行时自动读取：

```text
configs/frontend.yaml
configs/frontend.local.yaml
```

`configs/frontend.yaml` 已提供非敏感默认值：

```bash
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=admin123456
NEO4J_DATABASE=neo4j
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_MODEL=qwen3-max
OPENAI_ENABLE_THINKING=false
BRAIN_ACCEPTANCE_PROFILE=guarded_lenient
TRAJECTORY_VERIFIER_ACCEPTANCE_PROFILE=guarded_lenient
BRAIN_AGENT_EVAL_MODE=llm_verifier
```

真实 API Key 不建议写入版本库。请复制本机私密配置模板：

```bash
cp configs/frontend.local.example.yaml configs/frontend.local.yaml
```

然后在 `configs/frontend.local.yaml` 中填写：

```yaml
llm:
  api_key: "你的 DashScope API Key"
  enable_thinking: false
```

`configs/frontend.local.yaml` 已被 `.gitignore` 忽略，不会提交。

如果需要临时覆盖端口：

```bash
./scripts/run_streamlit_realtime.sh --server.port 8502
```

### 手动启动方式

如果不使用脚本，可以直接运行：

```bash
conda run -n GraduationDesign streamlit run frontend/app.py --browser.gatherUsageStats false
```

页面启动后，实时模式会自动读取 `configs/frontend.yaml` 与可选的 `configs/frontend.local.yaml`。

## 模式 C：实验复盘模式

实验复盘模式会直接扫描：

```text
test_outputs/simulator_replay/
```

它适合在跑完 focused replay、acceptance sweep 或 ablation 后，回到前端中逐病例复盘。该模式不重新调用 Neo4j 或 LLM，只读取本地历史输出文件。

当前可识别的文件包括：

- `focused_repair_summary.jsonl`：优先使用，包含逐病例、逐轮 repair / verifier / guarded gate 摘要
- `replay_results.jsonl`：普通 replay 输出，包含自动病人问答和 final_report
- `ablation_summary.jsonl`：病例级 ablation 摘要
- `focused_metrics.json` / `ablation_metrics.json` / `benchmark_summary.json`：实验指标汇总
- `profile_summary.tsv`：acceptance profile 对比汇总
- `status.json` / `run.log`：运行状态和日志尾部
- `a4_evidence_audit.jsonl` / `guarded_gate_audit.jsonl`：A4 证据记录与安全闸门审计

页面中会新增“实验复盘模式”：

- 左侧仍按轮次展示问诊对话或实验摘要
- 右侧继续复用 A1 / A2 / A3 / A4、搜索摘要和安全机制卡片
- 顶部额外展示实验目录、识别到的文件、病例数、正确接受、错误接受、正确但被拒停、repair 轮数、语义重复轮数和闸门拒绝次数
- 可以通过“刷新实验索引”按钮重新扫描新生成的输出目录
- 若某条病例只有 1 轮记录，页面会直接提示“无需切换轮次”，不会渲染轮次 slider
- 对 `replay_results.jsonl` 的自动回放记录，页面会按真实语义重建对话顺序：开场后首问来自 `initial_output.next_question`，后续轮次则按“系统先问、患者再答”展示
- 实验复盘里的“实验输出目录 / 病例记录”下拉框现在与 `session_state` 显式同步，并避免在 widget 创建后再次回写同 key，减少“点一次只高亮、点两次才真正切换”以及 `StreamlitAPIException` 风险
- 病例选择区额外提供“上一条病例 / 下一条病例”按钮，便于快速连续复盘
- 当前病例摘要会明确展示运行结果：圆满结束、达到最大轮次停止、异常出错结束；若异常失败，还会展示错误原因与结构化错误详情

说明：

- 如果一个目录只有 `profile_summary.tsv`、`status.json` 或 `run.log`，页面会展示目录汇总，但不会出现逐病例轮次
- 如果需要完整逐轮复盘，优先选择包含 `focused_repair_summary.jsonl` 的子目录，例如 `.../turns_5__verifier_guarded_lenient__stop_baseline/baseline`
- 新跑出的实验目录无需手动登记，刷新索引后会自动进入下拉列表

## 文件说明

```text
frontend/
  app.py                    Streamlit 主页面
  ui_adapter.py             将后端 process_turn / replay JSON 转为中文 UI 视图模型
  output_browser.py         扫描 test_outputs/simulator_replay 并适配实验复盘数据
  demo_cases.py             内置 demo registry
  demo_replays/             离线演示 JSON
  README.md                 启动与模式说明
```

## 字段降级策略

后端不同运行路径返回的字段可能不完全一致。前端通过 `ui_adapter.py` 做了轻量降级：

- 如果没有 `a1.key_features`，A1 卡片显示“本轮暂无新的关键线索”
- 如果没有 `a2.primary_hypothesis`，优先从 `final_report.candidate_hypotheses` 或 `search_report.final_answer_scores` 补候选诊断
- 如果没有 `search_report.tree_node_count`，搜索摘要显示“暂无”
- 如果没有 `guarded_acceptance` 信息，安全模块显示“未阻止”或“暂无”
- 如果实时模式抛错，不影响回放模式

这样可以优先保证中期检查时“可运行、可展示、可解释、可保底”。
