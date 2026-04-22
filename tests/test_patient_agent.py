"""测试虚拟病人代理的基础回答行为。"""

from simulator.case_schema import SlotTruth, VirtualPatientCase
from simulator.patient_agent import VirtualPatientAgent


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
