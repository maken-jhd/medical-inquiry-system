"""测试自动回放引擎是否能驱动问诊闭环。"""

from simulator.case_schema import SlotTruth, VirtualPatientCase
from simulator.patient_agent import VirtualPatientAgent
from simulator.replay_engine import ReplayEngine


class FakeBrain:
    """使用固定输出来模拟问诊大脑。"""

    # 初始化假的会话存储。
    def __init__(self) -> None:
        self.sessions: dict[str, int] = {}
        self.first_inputs: dict[str, str] = {}

    # 创建假的会话。
    def start_session(self, session_id: str) -> None:
        self.sessions[session_id] = 0

    # 模拟一次单轮处理：第一轮给出问题，第二轮直接结束。
    def process_turn(self, session_id: str, patient_text: str) -> dict:
        _ = patient_text
        step = self.sessions[session_id]

        if step == 0:
            self.first_inputs[session_id] = patient_text

        if step == 0:
            self.sessions[session_id] = 1
            return {
                "next_question": "近期是否有发热？",
                "pending_action": {
                    "target_node_id": "发热",
                },
                "final_report": None,
            }

        self.sessions[session_id] = 2
        return {
            "next_question": None,
            "pending_action": None,
            "final_report": {
                "session_id": session_id,
                "summary": "测试完成",
            },
        }

    # 在回放未提前终止时返回一个兜底报告。
    def finalize(self, session_id: str) -> dict:
        return {
            "session_id": session_id,
            "summary": "兜底结束",
        }


# 验证回放引擎能够驱动一轮自动问答并拿到最终报告。
def test_replay_engine_runs_case_to_completion() -> None:
    brain = FakeBrain()
    engine = ReplayEngine(brain, VirtualPatientAgent())
    case = VirtualPatientCase(
        case_id="case1",
        title="test",
        slot_truth_map={
            "发热": SlotTruth(node_id="发热", value=True, group="symptom", aliases=["发热"], reveal_only_if_asked=False),
        },
    )

    result = engine.run_case(case)

    assert result.status == "completed"
    assert "发热" in result.opening_text
    assert "发热" in brain.first_inputs["replay::case1"]
    assert len(result.turns) == 1
    assert result.turns[0].question_node_id == "发热"
    assert result.turns[0].answer_text == "有。"
    assert result.final_report["summary"] == "测试完成"


# 验证回放引擎支持批量运行多个病例。
def test_replay_engine_runs_cases_in_batch() -> None:
    engine = ReplayEngine(FakeBrain(), VirtualPatientAgent())
    cases = [
        VirtualPatientCase(
            case_id="case1",
            title="test1",
            slot_truth_map={"发热": SlotTruth(node_id="发热", value=True, aliases=["发热"], reveal_only_if_asked=False)},
        ),
        VirtualPatientCase(
            case_id="case2",
            title="test2",
            slot_truth_map={"发热": SlotTruth(node_id="发热", value=False, aliases=["发热"])},
        ),
    ]

    results = engine.run_cases(cases)

    assert len(results) == 2
    assert all(item.status == "completed" for item in results)
