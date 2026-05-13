---
description: "Use when editing Python in brain, simulator, baselines, or tests. Covers Chinese comments, type hints, dataclass and dependency-injection patterns, normalization placement, and validation expectations."
name: "Core Python Modules"
applyTo:
  - "brain/**/*.py"
  - "simulator/**/*.py"
  - "baselines/**/*.py"
  - "tests/**/*.py"
---

# Core Python Modules

- 文档和注释优先中文；测试函数附近保留中文意图说明。
- 新增或修改接口时补全参数与返回值类型；优先复用 `dataclass`、轻量配置对象和显式依赖注入，不要随手扩大 `dict` 或 `Any` 的使用范围。
- `brain/` 和 `simulator/` 的长流程函数在阶段切换、关键分支、状态更新前补简短中文注释。
- 对标签集、状态枚举、阈值分组等模块级常量，补中文说明其用途和分组理由。
- intake 和回答解释层统一使用 `mention_state` 与 `resolution`；诊断推理层再落到 `polarity`。不要把患者自述直接编码成医学 certainty。
- 归一化逻辑集中在 `brain/normalization.py` 附近的现有链路；新增抽取或解析分支时不要绕过这一层。
- LLM 调用优先复用 `brain/llm_client.py` 或已有封装；配置开关同步检查 `configs/brain.yaml` 和对应 benchmark 配置。
- 修改 `brain/service.py`、router、acceptance、report、replay 流程时，优先补跑对应 pytest 切片；链路说明见 [docs/brain_runtime_call_chain_guide.md](../../docs/brain_runtime_call_chain_guide.md)。