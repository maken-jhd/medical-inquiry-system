"""保留稳定公共契约的虚拟病人门面。"""

from __future__ import annotations

from typing import Any

from brain.integrations import LlmClient

from .runtime import VirtualPatientRuntime


class VirtualPatientAgent:
    """对外暴露稳定接口的虚拟病人门面。"""

    def __init__(
        self,
        *,
        llm_client: LlmClient | None = None,
        use_llm: bool = False,
    ) -> None:
        # 门面只保留稳定入口；真实 opening/answer 逻辑都放在 runtime 内部。
        self.runtime = VirtualPatientRuntime(llm_client=llm_client, use_llm=use_llm)
        self.use_llm = self.runtime.use_llm
        self.llm_client = self.runtime.llm_client

    def __getattr__(self, name: str) -> Any:
        return getattr(self.runtime, name)

    def open_case(self, case):
        # 对外稳定入口：根据病例骨架生成首轮开场。
        return self.runtime.open_case(case)

    def answer_question(self, question_node_id: str, question_text: str, case):
        # 对外稳定入口：根据问题与病例真值生成病人回答。
        return self.runtime.answer_question(question_node_id, question_text, case)
