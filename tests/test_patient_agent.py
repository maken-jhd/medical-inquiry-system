"""测试虚拟病人代理的基础回答行为。"""

from typing import Any

from simulator.case_schema import SlotTruth, VirtualPatientCase
from simulator.patient_agent import VirtualPatientAgent


class FakePatientLlmClient:
    """用于测试虚拟病人 LLM 分支的轻量 fake。"""

    def __init__(
        self,
        *,
        semantic_payload: dict[str, Any] | None = None,
        known_answer_text: str = "有。",
    ) -> None:
        self.semantic_payload = semantic_payload or {}
        self.known_answer_text = known_answer_text
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def is_available(self) -> bool:
        return True

    def run_structured_prompt(self, prompt_name: str, variables: dict, schema: Any) -> Any:
        self.calls.append((prompt_name, variables))

        if prompt_name == "patient_slot_semantic_match":
            return schema(**self.semantic_payload)

        if prompt_name == "patient_answer_generation":
            return schema(answer_text=self.known_answer_text, reasoning="")

        return schema()


# 验证病人代理在问到已知槽位时能返回明确答案。
def test_patient_agent_answers_known_slot() -> None:
    agent = VirtualPatientAgent()
    case = VirtualPatientCase(
        case_id="case1",
        title="test",
        slot_truth_map={"发热": SlotTruth(node_id="发热", value=True)},
    )

    reply = agent.answer_question("发热", "有没有发热？", case)

    assert reply.answer_text == "有。"
    assert reply.revealed_slot_id == "发热"


# 验证病人代理可根据骨架中的主动暴露槽位生成首轮发言。
def test_patient_agent_opens_case_from_proactive_slots() -> None:
    agent = VirtualPatientAgent()
    case = VirtualPatientCase(
        case_id="case2",
        title="test",
        slot_truth_map={
            "发热": SlotTruth(
                node_id="发热",
                value=True,
                group="symptom",
                aliases=["发热"],
                reveal_only_if_asked=False,
            ),
            "干咳": SlotTruth(
                node_id="干咳",
                value=True,
                group="symptom",
                aliases=["干咳"],
                reveal_only_if_asked=False,
            ),
        },
    )

    opening = agent.open_case(case)

    assert "发热" in opening.opening_text
    assert "干咳" in opening.opening_text
    assert opening.revealed_slot_ids == ["发热", "干咳"]


# 验证 unknown fallback 使用不确定表述，而不是容易被误判为阴性的固定句式。
def test_unknown_reply_is_uncertain_not_negative() -> None:
    agent = VirtualPatientAgent(use_llm=False)
    case = VirtualPatientCase(case_id="case3", title="test", slot_truth_map={})

    reply = agent.answer_question("未命中节点", "有没有盗汗？", case)

    assert reply.answer_text != "没有特别注意到。"
    assert ("不太确定" in reply.answer_text) or ("不能确定" in reply.answer_text)


# 验证通用检查上下文会汇总 lab/pathogen/imaging 中的阳性结果。
def test_exam_context_general_reports_positive_exam_results() -> None:
    agent = VirtualPatientAgent(use_llm=False)
    case = VirtualPatientCase(
        case_id="case4",
        title="test",
        slot_truth_map={
            "cd4_low": SlotTruth(
                node_id="cd4_low",
                value=True,
                group="lab",
                aliases=["CD4 很低"],
            ),
            "cmv_dna": SlotTruth(
                node_id="cmv_dna",
                value=True,
                group="pathogen",
                aliases=["CMV DNA阳性"],
            ),
            "fever": SlotTruth(
                node_id="fever",
                value=True,
                group="symptom",
                aliases=["发热"],
            ),
        },
    )

    reply = agent.answer_question("__exam_context__::general", "最近做过什么检查吗？", case)

    assert "做过" in reply.answer_text
    assert "CD4 很低" in reply.answer_text
    assert "CMV DNA阳性" in reply.answer_text
    assert "发热" not in reply.answer_text
    assert reply.revealed_slot_id == "cd4_low"


# 验证具体检查类型只有阴性槽位时，会回答阴性检查结果。
def test_exam_context_lab_reports_negative_results_when_only_negative() -> None:
    agent = VirtualPatientAgent(use_llm=False)
    case = VirtualPatientCase(
        case_id="case5",
        title="test",
        slot_truth_map={
            "mtb_culture": SlotTruth(
                node_id="mtb_culture",
                value=False,
                group="lab",
                aliases=["MTB培养"],
            ),
            "tb_dna": SlotTruth(
                node_id="tb_dna",
                value="阴性",
                group="lab",
                aliases=["结核分枝杆菌"],
            ),
        },
    )

    reply = agent.answer_question("__exam_context__::lab", "有没有做过实验室检查？", case)

    assert "做过相关检查" in reply.answer_text
    assert "没有提示" in reply.answer_text
    assert "MTB培养" in reply.answer_text
    assert "结核分枝杆菌" in reply.answer_text
    assert reply.revealed_slot_id == "mtb_culture"


# 验证精确匹配失败时，LLM 可以在病例候选槽位内做语义等价匹配。
def test_llm_semantic_truth_match_answers_known_slot() -> None:
    fake_llm = FakePatientLlmClient(semantic_payload={"matched_node_id": "hiv"})
    agent = VirtualPatientAgent(llm_client=fake_llm, use_llm=True)
    case = VirtualPatientCase(
        case_id="case6",
        title="test",
        slot_truth_map={
            "hiv": SlotTruth(
                node_id="hiv",
                value=True,
                group="risk",
                aliases=["HIV感染"],
            )
        },
    )

    reply = agent.answer_question("kg_node_hiv_aids", "有没有 HIV/AIDS？", case)

    assert reply.answer_text == "有。"
    assert reply.revealed_slot_id == "hiv"
    assert fake_llm.calls[0][0] == "patient_slot_semantic_match"
    assert fake_llm.calls[0][1]["question_node_id"] == "kg_node_hiv_aids"
    assert fake_llm.calls[0][1]["candidate_slots"][0]["node_id"] == "hiv"


# 验证语义匹配无候选时给出明确否定，且不揭示任何槽位。
def test_llm_semantic_no_match_returns_negative_without_revealing_slot() -> None:
    fake_llm = FakePatientLlmClient(
        semantic_payload={
            "matched_node_id": "",
            "no_match_answer": "没有这个症状。",
        }
    )
    agent = VirtualPatientAgent(llm_client=fake_llm, use_llm=True)
    case = VirtualPatientCase(
        case_id="case7",
        title="test",
        slot_truth_map={
            "fever": SlotTruth(
                node_id="fever",
                value=True,
                group="symptom",
                aliases=["发热"],
            )
        },
    )

    reply = agent.answer_question("night_sweat", "有没有盗汗？", case)

    assert reply.answer_text == "没有这个症状。"
    assert reply.revealed_slot_id is None


# 验证关闭 LLM 时，精确匹配失败仍沿用原有 unknown fallback。
def test_llm_disabled_keeps_unknown_fallback_for_semantic_miss() -> None:
    agent = VirtualPatientAgent(use_llm=False)
    case = VirtualPatientCase(
        case_id="case8",
        title="test",
        slot_truth_map={
            "hiv": SlotTruth(
                node_id="hiv",
                value=True,
                group="risk",
                aliases=["HIV感染"],
            )
        },
    )

    reply = agent.answer_question("kg_node_hiv_aids", "有没有 HIV/AIDS？", case)

    assert reply.revealed_slot_id is None
    assert ("不太确定" in reply.answer_text) or ("不能确定" in reply.answer_text)
