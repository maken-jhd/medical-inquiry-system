## 第5章对比结果整理

本目录用于集中整理实验章节中涉及的两套主对比结果：

- 全量 `227` 例病例对比结果
- 高质量 `112` 例病例对比结果

当前纳入整理的比较方法包括：

- 本文方法（MCTS）
- 纯大语言模型
- 文本检索增强生成（TextRAG）
- 知识图谱检索增强生成（KGRAG）

目录说明如下：

- [full227_comparison.md](/Users/loki/Workspace/GraduationDesign/docs/chapter5_comparison_results/full227_comparison.md)
  - 全量 `227` 例病例对比结果与简要观察
- [eligible112_comparison.md](/Users/loki/Workspace/GraduationDesign/docs/chapter5_comparison_results/eligible112_comparison.md)
  - 高质量 `112` 例病例对比结果与简要观察
- [comparison_metrics.json](/Users/loki/Workspace/GraduationDesign/docs/chapter5_comparison_results/comparison_metrics.json)
  - 两套结果的结构化指标汇总，便于后续制表或复用

本目录所用结果文件来源如下：

- MCTS：
  - `/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/benchmark_20260514_hybrid_transition/modular_v2_statistical_belief_aware_relaxed_full227_hybrid/benchmark_summary.json`
- 纯大语言模型：
  - `/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/benchmark_external_baselines/pure_llm_full227_20260507/benchmark_summary.json`
- TextRAG：
  - `/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/benchmark_external_baselines/text_rag_full227_20260513/benchmark_summary.json`
- KGRAG：
  - `/Users/loki/Workspace/GraduationDesign/test_outputs/simulator_replay/benchmark_external_baselines/kg_rag_full227_symptom_recall_20260514/benchmark_summary.json`

说明：

- `full227` 对应全部 `227` 例回放病例。
- `eligible112` 对应 `benchmark_qc_status=eligible` 的 `112` 例高质量病例。
- 对于纯大语言模型、TextRAG 和 KGRAG，因其默认总会在对话末尾直接给出答案，因此完成率恒为 `1.0`；而本文方法引入了接受控制，完成率应结合其设计语义理解，不能简单与外部基线直接等同。
