"""统一封装第二阶段使用的大模型结构化调用接口。"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from typing import Any, Type

from openai import OpenAI


class LlmClient:
    """负责执行统一的结构化 Prompt 调用。"""

    # 初始化模型客户端与默认模型配置。
    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.model = model or os.getenv("OPENAI_MODEL", "qwen3-max")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
        self._client: OpenAI | None = None

        if self.api_key:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )

    # 判断当前是否具备可用的大模型调用条件。
    def is_available(self) -> bool:
        return self._client is not None

    # 执行结构化 Prompt，并尝试将输出反序列化为指定 schema。
    def run_structured_prompt(self, prompt_name: str, variables: dict, schema: Type[Any]) -> Any:
        if self._client is None:
            raise RuntimeError("当前未配置可用的大模型客户端。")

        prompt = self._build_prompt(prompt_name, variables)
        response = self._client.chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": "你是医学结构化信息抽取助手。请严格输出 JSON，不要输出额外文本。",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        )
        content = response.choices[0].message.content or "{}"
        payload = json.loads(content)
        return self._coerce_schema(payload, schema)

    # 根据 prompt 名称和变量构建统一的文本提示。
    def _build_prompt(self, prompt_name: str, variables: dict) -> str:
        prompt_blocks = {
            "med_extractor": (
                "请从患者原话中提取一般信息 P 和临床特征 C。"
                "输出字段必须包含 general_info 与 clinical_features。"
            ),
            "a1_key_symptom_extraction": (
                "请从患者上下文中提取最关键的临床特征。"
                "输出字段必须包含 key_features、uncertain_features、noise_features、reasoning_summary。"
            ),
            "a2_hypothesis_generation": (
                "请根据患者一般信息、临床特征和图谱候选疾病生成主假设与备选假设。"
                "输出字段必须包含 primary_hypothesis、alternatives、reasoning、"
                "supporting_features、conflicting_features、why_primary_beats_alternatives、recommended_next_evidence。"
            ),
            "a4_deductive_judge": (
                "请根据目标验证点、患者回答、当前主假设和备选假设，给出诊断性演绎判断。"
                "输出字段必须包含 existence、certainty、decision_type、next_stage、"
                "diagnostic_rationale、contradiction_explanation、"
                "should_terminate_current_path、should_spawn_alternative_hypotheses、reasoning。"
            ),
            "trajectory_agent_verifier": (
                "请作为临床推理评审者，结合患者上下文、候选最终答案和最佳推理路径，"
                "给出该答案的代理评审分数。"
                "输出字段必须包含 score、should_accept_stop、reject_reason、reasoning、"
                "missing_evidence、risk_flags、recommended_next_evidence、alternative_candidates。"
                "其中 reject_reason 只能取 missing_key_support、strong_alternative_not_ruled_out、trajectory_insufficient 之一。"
                "score 取值范围为 0 到 1。"
            ),
        }
        prefix = prompt_blocks.get(prompt_name, "请完成结构化医学推理，并输出 JSON。")
        return prefix + "\n\n" + json.dumps(variables, ensure_ascii=False, indent=2, default=self._json_default)

    # 将 Python 对象安全地转成 JSON 可序列化形式。
    def _json_default(self, value: Any) -> Any:
        if is_dataclass(value):
            return asdict(value)

        return str(value)

    # 将模型输出的 JSON 负载尽量转换为指定 schema 对象。
    def _coerce_schema(self, payload: Any, schema: Type[Any]) -> Any:
        try:
            return schema(**payload)
        except Exception:
            return payload
