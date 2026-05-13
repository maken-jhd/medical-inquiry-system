---
description: "Use when editing README files or docs markdown in this repository. Covers Chinese-first documentation style, link-first updates, and changelog expectations."
name: "Docs And Readmes"
applyTo:
  - "README.md"
  - "**/README.md"
  - "docs/**/*.md"
---

# Docs And Readmes

- 文档默认使用中文，先说明当前活跃链路，再补命令、输入输出和验证结果。
- 采用“链接而不是复制”：已有说明优先引用根 README、模块 README 和 `docs/` 文档，不要把大段背景重复写进多个文件。
- 需要提到搜索图谱时，明确 `knowledge_graph/` 是当前活跃链路，`knowledge_graph_bak/` 仅作历史参考。
- 完成相对独立的实现改动后，默认同步更新相关 README 和 `docs/phase2_changelog.md`，记录影响范围、输出文件和验证结果。
- 设计或实验说明优先对齐 [docs/brain_runtime_call_chain_guide.md](../../docs/brain_runtime_call_chain_guide.md)、[docs/diagnosis_benchmark_experiment_design.md](../../docs/diagnosis_benchmark_experiment_design.md)、[docs/virtual_patient_generation_scheme.md](../../docs/virtual_patient_generation_scheme.md)。