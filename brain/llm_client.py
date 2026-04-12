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
        verifier_acceptance_profile = os.getenv("TRAJECTORY_VERIFIER_ACCEPTANCE_PROFILE", "baseline")
        prompt_blocks = {
            "med_extractor": (
                "请从患者原话中提取一般信息 P 和临床特征 C。"
                "输出字段必须包含 general_info 与 clinical_features。"
            ),
            "a1_key_symptom_extraction": (
                "请从患者上下文中提取最关键的临床特征。"
                "输出字段必须包含 key_features、uncertain_features、noise_features、reasoning_summary。"
            ),
            "intake_opening_response": (
                "患者当前输入没有明确症状、风险因素或检查结果，"
                "请生成一句简短、自然、专业的医生式回应，并主动询问本次就诊主要不适。"
                "必须严格输出 JSON object，且只包含字段：acknowledgement、question、reasoning。"
                "acknowledgement 用中文，长度不超过 30 字；"
                "question 用中文，必须鼓励患者描述主要症状、持续时间和最担心的问题，长度不超过 80 字；"
                "reasoning 用中文，说明为什么需要先采集主诉。"
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
                "必须严格输出一个 JSON object，且只使用以下字段："
                "score、should_accept_stop、reject_reason、reasoning、missing_evidence、risk_flags、"
                "recommended_next_evidence、alternative_candidates、accept_reason。"
                "score 取值范围为 0 到 1；should_accept_stop 必须是布尔值。"
                "reject_reason 必须始终填写，且只能精确取 missing_key_support、"
                "strong_alternative_not_ruled_out、trajectory_insufficient 之一；"
                "即使 should_accept_stop 为 true，也请选择最接近的枚举值，不要输出其他字符串。"
                "accept_reason 必须始终填写，且只能精确取 key_support_sufficient、"
                "alternatives_reasonably_ruled_out、trajectory_stable 之一；"
                "should_accept_stop=true 时，accept_reason 表示接受原因；"
                "should_accept_stop=false 时，也请选择最接近的未来接受条件，不要输出其他字符串。"
                "recommended_next_evidence 必须是字符串数组，表示下一步最值得验证的临床证据。"
                "alternative_candidates 必须是对象数组，每个对象包含 answer_id、answer_name、reason；"
                "answer_id 不确定时可为 null，但 answer_name 和 reason 必须给出。"
                + self._build_verifier_acceptance_profile_prompt(verifier_acceptance_profile)
            ),
        }
        prefix = prompt_blocks.get(prompt_name, "请完成结构化医学推理，并输出 JSON。")
        return prefix + "\n\n" + json.dumps(variables, ensure_ascii=False, indent=2, default=self._json_default)

    # 为 acceptance sweep 提供轻量可控的 verifier 接受倾向，不改变默认 baseline 行为。
    def _build_verifier_acceptance_profile_prompt(self, profile: str) -> str:
        normalized = profile.strip().lower()

        if normalized in {"conservative", "strict", "high_precision"}:
            return (
                "当前 acceptance_profile=conservative。"
                "只有当候选答案已经有直接且关键的支持证据、主要强替代诊断已被明确削弱，"
                "且不存在关键矛盾或关键缺失证据时，才允许 should_accept_stop=true。"
                "如果仍缺少会显著改变诊断结论的宿主因素、关键检查或病原学证据，"
                "请保持 should_accept_stop=false，并用 reject_reason 指明最主要缺口。"
            )

        if normalized in {"key_evidence_accepting", "accept_key_evidence", "accept_when_key_evidence_present"}:
            return (
                "当前 acceptance_profile=key_evidence_accepting。"
                "如果累计会话上下文与最佳推理路径已经覆盖候选答案的关键宿主因素、核心症状或关键检查证据，"
                "且未发现强替代诊断或明确矛盾证据，则应允许 should_accept_stop=true；"
                "不要因为仍可补充低优先级检查而机械拒停。"
                "只有缺失会改变诊断结论的关键证据时，才输出 should_accept_stop=false。"
            )

        if normalized in {"slightly_lenient", "lenient", "calibrated_lenient"}:
            return (
                "当前 acceptance_profile=slightly_lenient。"
                "请在保证不放过强矛盾和强替代诊断的前提下，降低机械性拒停。"
                "如果候选答案在当前轨迹组中稳定占优，患者累计上下文已覆盖关键宿主因素、核心症状或关键检查中的主要证据，"
                "且 alternative_candidates 中没有已被充分支持的强竞争诊断，则应倾向 should_accept_stop=true。"
                "不要仅因为还可以补充低优先级、不会改变诊断方向的检查而拒停；"
                "只有缺失证据会实质性改变 top1 与 top2 判断时，才输出 should_accept_stop=false。"
            )

        if normalized in {"guarded_lenient", "guarded-lenient", "guarded"}:
            return (
                "当前 acceptance_profile=guarded_lenient。"
                "无论判断如何，都必须严格输出同一 JSON schema，不要新增字段，不要输出解释性前后缀。"
                "reject_reason 只能是 missing_key_support、strong_alternative_not_ruled_out、trajectory_insufficient；"
                "accept_reason 只能是 key_support_sufficient、alternatives_reasonably_ruled_out、trajectory_stable。"
                "请把自己定位为候选接受信号提供者：当候选答案已经临床上较可信时，可以先输出 should_accept_stop=true，"
                "最终是否停止会由结构化 gate 继续校验 confirmed evidence、negative/doubtful 证据和 hypothesis 稳定性。"
                "不要因为仍可补充低优先级检查而机械拒停；只有缺失证据会实质性改变 top1/top2 时才拒停。"
                "对 PCP、结核、真菌性肺部感染、影像强但非 PCP 等高混淆呼吸道诊断，"
                "若已有影像/氧合证据，且累计上下文支持免疫抑制、病原学倾向或典型呼吸道组合证据，"
                "可以输出 should_accept_stop=true、accept_reason=key_support_sufficient。"
                "如果关键支持证据明确为 negative 或 doubtful，或仍存在强替代诊断，请输出 should_accept_stop=false、"
                "reject_reason=strong_alternative_not_ruled_out，并用 alternative_candidates 数组列出。"
                "如果路径刚发生答案切换或稳定性不足，请输出 should_accept_stop=false、"
                "reject_reason=trajectory_insufficient。"
            )

        return "当前 acceptance_profile=baseline，请按严格临床证据充分性进行评审。"

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
