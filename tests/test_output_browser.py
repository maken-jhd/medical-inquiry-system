"""测试实验复盘输出到前端对话结构与病例摘要的适配逻辑。"""

from frontend.output_browser import build_case_replay, summarize_case_record


def test_build_case_replay_keeps_later_followup_questions_in_chat() -> None:
    record = {
        "_record_kind": "replay_results",
        "case_id": "case_replay",
        "case_title": "测试病例",
        "status": "max_turn_reached",
        "initial_output": {
            "turn_index": 1,
            "patient_text": "我最近总发热。",
            "next_question": "最近做过检查吗？",
            "final_report": None,
        },
        "turns": [
            {
                "turn_index": 1,
                "question_text": "最近做过检查吗？",
                "answer_text": "不太记得。",
                "stage": "A3",
            },
            {
                "turn_index": 2,
                "question_text": "有没有吸烟？",
                "answer_text": "没有吸烟。",
                "stage": "A3",
            },
        ],
        "final_report": {},
    }

    replay = build_case_replay(record)
    turns = replay["turns"]

    assert turns[0]["system_question"] == "最近做过检查吗？"
    assert turns[0]["chat_order"] == "patient_then_system"
    assert turns[1]["patient_text"] == "不太记得。"
    assert turns[1]["system_question"] == ""
    assert turns[1]["chat_order"] == "system_then_patient"
    assert turns[2]["system_question"] == "有没有吸烟？"
    assert turns[2]["patient_text"] == "没有吸烟。"
    assert turns[2]["chat_order"] == "system_then_patient"


def test_summarize_case_record_includes_terminal_status_and_error() -> None:
    summary = summarize_case_record(
        {
            "case_id": "failed_case",
            "case_title": "失败病例",
            "status": "failed",
            "error": {
                "code": "llm_empty_extraction",
                "stage": "med_extractor",
                "prompt_name": "med_extractor",
                "message": "MedExtractor 未从当前长文本中抽取出任何临床特征。",
                "attempts": 1,
            },
        }
    )

    assert summary["run_status"] == "failed"
    assert summary["run_status_label"] == "异常出错结束"
    assert summary["error_code"] == "llm_empty_extraction"
    assert summary["error_stage"] == "med_extractor"
    assert summary["error_prompt_name"] == "med_extractor"
    assert summary["error_message"] == "MedExtractor 未从当前长文本中抽取出任何临床特征。"
    assert summary["error_attempts"] == 1
