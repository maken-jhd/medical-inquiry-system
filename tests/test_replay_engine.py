"""测试自动回放引擎是否能驱动问诊闭环。"""

from collections import deque

from brain.errors import LlmOutputInvalidError
from simulator.case_schema import SlotTruth, VirtualPatientCase
from simulator.patient_agent import VirtualPatientAgent
from simulator import replay_engine
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
                "search_report": {
                    "turn_index": 1,
                    "search_metadata": {
                        "selected_action_source": "default_search_action",
                    },
                },
                "final_report": None,
            }

        self.sessions[session_id] = 2
        return {
            "next_question": None,
            "pending_action": None,
            "search_report": {
                "turn_index": 2,
                "search_metadata": {
                    "repair_mode": "none",
                },
            },
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
    assert result.initial_output["search_report"]["search_metadata"]["selected_action_source"] == "default_search_action"
    assert result.turns[0].search_report["turn_index"] == 2
    assert result.turns[0].search_metadata["repair_mode"] == "none"
    assert result.turns[0].patient_answer_seconds >= 0.0
    assert result.turns[0].brain_turn_seconds >= 0.0
    assert result.timing["opening_seconds"] >= 0.0
    assert result.timing["initial_brain_seconds"] >= 0.0
    assert result.timing["total_seconds"] >= 0.0
    assert result.timing["turn_count"] == 1
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


# 验证 timing 累计使用原始浮点值，不会因逐轮 round 让 brain 总耗时大于 total。
def test_replay_engine_timing_totals_do_not_exceed_case_total(monkeypatch) -> None:
    class LoopBrain:
        def start_session(self, session_id: str) -> None:
            self.session_id = session_id

        def process_turn(self, session_id: str, patient_text: str) -> dict:
            _ = session_id, patient_text
            return {
                "next_question": "近期是否有发热？",
                "pending_action": {
                    "target_node_id": "发热",
                },
                "final_report": None,
            }

        def finalize(self, session_id: str) -> dict:
            return {
                "session_id": session_id,
                "summary": "到达最大轮次",
            }

    perf_values = deque(
        [
            0.0,
            0.0,
            0.0,
            0.00001,
            0.00006,
            0.00006,
            0.00006,
            0.00006,
            0.00012,
            0.00012,
            0.00012,
            0.00012,
            0.00018,
            0.00018,
            0.00018,
            0.00018,
            0.00024,
            0.00024,
            0.00024,
            0.00024,
            0.00030,
            0.00030,
            0.00030,
            0.00030,
            0.00036,
            0.00036,
            0.00036,
            0.00036,
            0.00042,
            0.00042,
            0.00042,
            0.00042,
            0.00048,
            0.00048,
            0.00048,
            0.00048,
            0.00054,
            0.00054,
            0.00055,
            0.00056,
        ]
    )

    monkeypatch.setattr(replay_engine, "perf_counter", lambda: perf_values.popleft())

    engine = ReplayEngine(LoopBrain(), VirtualPatientAgent(), replay_engine.ReplayConfig(max_turns=8))
    case = VirtualPatientCase(
        case_id="timing_case",
        title="timing",
        slot_truth_map={"发热": SlotTruth(node_id="发热", value=True, aliases=["发热"], reveal_only_if_asked=False)},
    )

    result = engine.run_case(case)

    assert result.status == "max_turn_reached"
    assert result.timing["brain_turn_seconds_total"] <= result.timing["total_seconds"]
    assert result.timing["initial_brain_seconds"] <= result.timing["total_seconds"]


# 验证 LLM 主链路抛出领域错误时，单病例会标记 failed 并保留结构化错误。
def test_replay_engine_marks_case_failed_on_domain_error() -> None:
    class FailingBrain:
        def start_session(self, session_id: str) -> None:
            self.session_id = session_id

        def process_turn(self, session_id: str, patient_text: str) -> dict:
            _ = session_id, patient_text
            raise LlmOutputInvalidError(
                stage="a1_key_symptom_extraction",
                prompt_name="a1_key_symptom_extraction",
                attempts=2,
                message="结构化输出缺少 key_features。",
            )

        def finalize(self, session_id: str) -> dict:
            _ = session_id
            raise AssertionError("failed 病例不应再进入 finalize")

    engine = ReplayEngine(FailingBrain(), VirtualPatientAgent())
    case = VirtualPatientCase(
        case_id="failed_case",
        title="failed",
        slot_truth_map={"发热": SlotTruth(node_id="发热", value=True, aliases=["发热"], reveal_only_if_asked=False)},
    )

    result = engine.run_case(case)

    assert result.status == "failed"
    assert result.final_report == {}
    assert result.error["code"] == "llm_output_invalid"
    assert result.error["stage"] == "a1_key_symptom_extraction"
    assert result.error["attempts"] == 2


# 验证普通运行时异常也会被转成单病例 failed，而不是直接炸出 replay。
def test_replay_engine_marks_case_failed_on_unexpected_runtime_error() -> None:
    class CrashingBrain:
        def start_session(self, session_id: str) -> None:
            self.session_id = session_id

        def process_turn(self, session_id: str, patient_text: str) -> dict:
            _ = session_id, patient_text
            raise AttributeError("'ClinicalFeatureItem' object has no attribute 'status'")

        def finalize(self, session_id: str) -> dict:
            _ = session_id
            raise AssertionError("failed 病例不应再进入 finalize")

    engine = ReplayEngine(CrashingBrain(), VirtualPatientAgent())
    case = VirtualPatientCase(
        case_id="runtime_failed_case",
        title="runtime-failed",
        slot_truth_map={"发热": SlotTruth(node_id="发热", value=True, aliases=["发热"], reveal_only_if_asked=False)},
    )

    result = engine.run_case(case)

    assert result.status == "failed"
    assert result.final_report == {}
    assert result.error["code"] == "unexpected_runtime_error"
    assert result.error["stage"] == "replay_engine"
    assert result.error["error_type"] == "AttributeError"
