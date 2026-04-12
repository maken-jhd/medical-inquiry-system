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
