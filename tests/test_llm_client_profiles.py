"""测试 verifier acceptance profile prompt 构造。"""

from brain.llm_client import LlmClient


def test_verifier_acceptance_profiles_are_distinct() -> None:
    client = LlmClient(api_key="")

    conservative = client._build_verifier_acceptance_profile_prompt("conservative")
    baseline = client._build_verifier_acceptance_profile_prompt("baseline")
    slightly_lenient = client._build_verifier_acceptance_profile_prompt("slightly_lenient")
    guarded_lenient = client._build_verifier_acceptance_profile_prompt("guarded_lenient")

    assert "acceptance_profile=conservative" in conservative
    assert "acceptance_profile=baseline" in baseline
    assert "acceptance_profile=slightly_lenient" in slightly_lenient
    assert "acceptance_profile=guarded_lenient" in guarded_lenient
    assert conservative != baseline
    assert slightly_lenient != baseline
    assert guarded_lenient != slightly_lenient


def test_trajectory_verifier_prompt_requires_accept_reason(monkeypatch) -> None:
    monkeypatch.setenv("TRAJECTORY_VERIFIER_ACCEPTANCE_PROFILE", "slightly_lenient")
    client = LlmClient(api_key="")
    prompt = client._build_prompt("trajectory_agent_verifier", {"answer_name": "肺孢子菌肺炎 (PCP)"})

    assert "accept_reason" in prompt
    assert "key_support_sufficient" in prompt
    assert "alternatives_reasonably_ruled_out" in prompt
    assert "trajectory_stable" in prompt


def test_patient_slot_semantic_match_prompt_constrains_candidate_matching() -> None:
    client = LlmClient(api_key="")
    prompt = client._build_prompt(
        "patient_slot_semantic_match",
        {
            "question_text": "有没有 HIV/AIDS？",
            "question_node_id": "kg_node_hiv_aids",
            "candidate_slots": [{"node_id": "hiv", "name": "HIV感染"}],
        },
    )

    assert "candidate_slots" in prompt
    assert "候选内匹配约束" in prompt
    assert "matched_node_id 只能取 candidate_slots 中已有的 node_id" in prompt
    assert "HIV/AIDS ~= HIV感染/HIV感染者" in prompt
    assert "ART ~= 抗逆转录病毒治疗/抗病毒治疗" in prompt
    assert "没有合适候选时 matched_node_id 必须为空字符串" in prompt
    assert "如果 question_text 问的是 lab/imaging/pathogen、高成本检查、病原体、检查结果或疾病定义性证据" in prompt
    assert "这些回答会被 brain 解析为 unclear" in prompt
    assert "不要写成“没有相关情况”或明确阴性" in prompt


def test_patient_answer_generation_prompt_blocks_out_of_case_facts() -> None:
    client = LlmClient(api_key="")
    prompt = client._build_prompt(
        "patient_answer_generation",
        {
            "question_text": "有没有 HIV/AIDS？",
            "answer_mode": "known",
            "matched_slot": {"node_id": "hiv", "name": "HIV感染", "value": True},
        },
    )

    assert "临床语义等价关系" in prompt
    assert "不得引入病例槽位外的新事实" in prompt
    assert "不得补充 matched_slot 之外的症状、检查、诊断、病史或治疗" in prompt


def test_turn_interpreter_prompt_keeps_unperformed_high_cost_exam_unclear() -> None:
    client = LlmClient(api_key="")
    prompt = client._build_prompt(
        "turn_interpreter",
        {
            "previous_question_text": "头颅CT有没有低密度病灶？",
            "pending_target_name": "头颅CT低密度病灶",
            "pending_target_label": "ImagingFinding",
            "question_type": "imaging",
            "acquisition_mode": "needs_imaging",
            "evidence_cost": "high",
            "relation_type": "HAS_IMAGING_FINDING",
            "patient_text": "没做过这项检查。",
        },
    )

    assert "只能把 pending_target_name 标为 unclear，不能标为 absent" in prompt
    assert "没做过这项检查" in prompt
    assert "结果明确阴性/不存在" in prompt


def test_patient_opening_generation_prompt_preserves_exam_anchors() -> None:
    client = LlmClient(api_key="")
    prompt = client._build_prompt(
        "patient_opening_generation",
        {
            "opening_slots": [
                {"node_id": "cd4_low", "name": "CD4+ T淋巴细胞计数 < 200/μL", "group": "lab"},
                {"node_id": "hiv_rna_positive", "name": "HIV RNA阳性", "group": "lab"},
            ],
        },
    )

    assert "关键医学锚点" in prompt
    assert "低于200" in prompt
    assert "阳性/阴性/升高/降低" in prompt
    assert "不得把 CD4 < 200、HIV RNA阳性、具体病原体名等压缩成单纯“异常/偏低”" in prompt


def test_llm_client_reads_timeout_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "12")
    client = LlmClient(api_key="")

    assert client.timeout_seconds == 12


def test_llm_client_timeout_has_safe_minimum(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "1")
    client = LlmClient(api_key="")

    assert client.timeout_seconds == 5.0


def test_llm_client_reads_enable_thinking_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_ENABLE_THINKING", "false")
    client = LlmClient(api_key="")

    assert client.enable_thinking is False


def test_llm_client_passes_enable_thinking_to_extra_body() -> None:
    captured: dict = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return type(
                "FakeResponse",
                (),
                {
                    "choices": [
                        type(
                            "FakeChoice",
                            (),
                            {"message": type("FakeMessage", (), {"content": "{}"})()},
                        )()
                    ]
                },
            )()

    class FakeChat:
        def __init__(self) -> None:
            self.completions = FakeCompletions()

    class FakeClient:
        def __init__(self) -> None:
            self.chat = FakeChat()

    client = LlmClient(api_key="", enable_thinking=False)
    client._client = FakeClient()  # type: ignore[assignment]

    client.run_structured_prompt("med_extractor", {"patient_text": "发热"}, dict)

    assert captured["extra_body"] == {"enable_thinking": False}
