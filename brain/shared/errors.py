"""定义问诊大脑在 LLM 主链路下使用的领域错误类型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class BrainDomainError(RuntimeError):
    """统一承载可向前端 / replay 暴露的结构化领域错误。"""

    code: str
    stage: str
    message: str
    prompt_name: str = ""
    attempts: int = 1

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.message)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "stage": self.stage,
            "prompt_name": self.prompt_name,
            "message": self.message,
            "attempts": int(self.attempts),
        }


class LlmUnavailableError(BrainDomainError):
    """表示当前阶段所需 LLM 不可用。"""

    def __init__(self, stage: str, prompt_name: str, message: str = "当前未配置可用的大模型客户端。") -> None:
        super().__init__(
            code="llm_unavailable",
            stage=stage,
            prompt_name=prompt_name,
            message=message,
            attempts=0,
        )


class LlmTimeoutError(BrainDomainError):
    """表示结构化 LLM 调用在重试后仍超时。"""

    def __init__(self, stage: str, prompt_name: str, attempts: int, message: str) -> None:
        super().__init__(
            code="llm_timeout",
            stage=stage,
            prompt_name=prompt_name,
            message=message,
            attempts=attempts,
        )


class LlmOutputInvalidError(BrainDomainError):
    """表示 LLM 返回的结构化内容格式无效。"""

    def __init__(self, stage: str, prompt_name: str, attempts: int, message: str) -> None:
        super().__init__(
            code="llm_output_invalid",
            stage=stage,
            prompt_name=prompt_name,
            message=message,
            attempts=attempts,
        )


class LlmEmptyExtractionError(BrainDomainError):
    """表示 LLM 成功返回但未给出本阶段所需的有效结构化结果。"""

    def __init__(self, stage: str, prompt_name: str, attempts: int, message: str) -> None:
        super().__init__(
            code="llm_empty_extraction",
            stage=stage,
            prompt_name=prompt_name,
            message=message,
            attempts=attempts,
        )


class LlmStageFailedError(BrainDomainError):
    """表示 LLM 调用在重试后仍失败，但不属于超时或格式错误。"""

    def __init__(self, stage: str, prompt_name: str, attempts: int, message: str) -> None:
        super().__init__(
            code="llm_stage_failed",
            stage=stage,
            prompt_name=prompt_name,
            message=message,
            attempts=attempts,
        )
