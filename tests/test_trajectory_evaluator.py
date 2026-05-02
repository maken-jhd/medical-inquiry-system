"""测试轨迹评估器的分组评分与最佳答案选择。"""

from brain.trajectory_evaluator import TrajectoryEvaluator, TrajectoryEvaluatorConfig
from brain.types import HypothesisScore, PatientContext, ReasoningTrajectory


# 验证轨迹评估器会优先选择轨迹数量更多且得分更高的答案。
def test_trajectory_evaluator_prefers_more_consistent_answer_group() -> None:
    evaluator = TrajectoryEvaluator()
    trajectories = [
        ReasoningTrajectory(
            trajectory_id="t1",
            final_answer_id="d1",
            final_answer_name="肺孢子菌肺炎",
            steps=[{"action_name": "发热"}],
            score=0.8,
        ),
        ReasoningTrajectory(
            trajectory_id="t2",
            final_answer_id="d1",
            final_answer_name="肺孢子菌肺炎",
            steps=[{"action_name": "低氧血症"}],
            score=0.9,
        ),
        ReasoningTrajectory(
            trajectory_id="t3",
            final_answer_id="d2",
            final_answer_name="结核病",
            steps=[{"action_name": "盗汗"}],
            score=0.4,
        ),
    ]

    grouped = evaluator.group_by_answer(trajectories)
    scores = evaluator.score_groups(grouped)
    best = evaluator.select_best_answer(scores)

    assert best is not None
    assert best.answer_id == "d1"


class FakeVerifierClient:
    """模拟 trajectory agent verifier 的结构化输出。"""

    def is_available(self) -> bool:
        return True

    def run_structured_prompt(self, prompt_name: str, variables: dict, schema: type) -> dict:
        assert "answer_candidates" in variables
        _ = schema
        assert prompt_name == "trajectory_agent_verifier"
        return {
            "score": 0.88,
            "should_accept_stop": False,
            "reject_reason": "missing_key_support",
            "reasoning": "最佳轨迹与患者上下文一致，但缺少关键支持证据。",
            "missing_evidence": ["低氧血症"],
            "risk_flags": ["支持证据不足"],
            "recommended_next_evidence": ["低氧血症"],
            "alternative_candidates": [{"answer_name": "结核病", "reason": "尚未完成鉴别"}],
        }


class InvalidReasonVerifierClient:
    """模拟 verifier 未遵守 reject_reason 枚举时的兼容兜底。"""

    def is_available(self) -> bool:
        return True

    def run_structured_prompt(self, prompt_name: str, variables: dict, schema: type) -> dict:
        _ = prompt_name, variables, schema
        return {
            "score": 0.42,
            "should_accept_stop": "false",
            "reject_reason": "needs_more_work",
            "reasoning": "强替代诊断尚未排除，需要继续鉴别。",
            "missing_evidence": [],
            "risk_flags": ["替代诊断未排除"],
            "recommended_next_evidence": ["核酸检测"],
            "alternative_candidates": [{"answer_name": "新型冠状病毒感染", "reason": "需要排除"}],
        }


class SimulatedEvidenceAcceptingVerifierClient:
    """模拟 verifier 试图把 rollout 阳性当成真实已确认事实。"""

    def is_available(self) -> bool:
        return True

    def run_structured_prompt(self, prompt_name: str, variables: dict, schema: type) -> dict:
        assert prompt_name == "trajectory_agent_verifier"
        assert "observed_session_evidence" in variables
        assert "simulated_trajectory_evidence" in variables
        _ = schema
        return {
            "score": 0.93,
            "should_accept_stop": True,
            "reject_reason": "missing_key_support",
            "reasoning": "rollout 假设已经拿到关键阳性证据。",
            "missing_evidence": [],
            "risk_flags": [],
            "recommended_next_evidence": ["痰分枝杆菌培养"],
            "alternative_candidates": [],
            "accept_reason": "key_support_sufficient",
        }


# 验证当启用 llm_verifier 模式时，agent evaluation 会消费 verifier 分数。
def test_trajectory_evaluator_supports_llm_verifier_mode() -> None:
    evaluator = TrajectoryEvaluator(
        TrajectoryEvaluatorConfig(agent_eval_mode="llm_verifier"),
        llm_client=FakeVerifierClient(),  # type: ignore[arg-type]
    )
    trajectories = [
        ReasoningTrajectory(
            trajectory_id="t1",
            final_answer_id="d1",
            final_answer_name="肺孢子菌肺炎",
            steps=[{"action_name": "发热"}, {"action_name": "低氧血症"}],
            score=0.8,
            metadata={"path_terminal": True},
        )
    ]

    grouped = evaluator.group_by_answer(trajectories)
    scores = evaluator.score_groups(grouped, patient_context=PatientContext(raw_text="发热伴呼吸困难"))

    assert len(scores) == 1
    assert scores[0].agent_evaluation == 0.88
    assert scores[0].metadata["verifier_reject_reason"] == "missing_key_support"
    assert scores[0].metadata["verifier_recommended_next_evidence"] == ["低氧血症"]
    assert scores[0].metadata["verifier_alternative_candidates"][0]["answer_name"] == "结核病"
    assert scores[0].metadata["verifier_reject_reason_source"] == "llm_schema"
    assert scores[0].metadata["verifier_schema_valid"] is True


# 验证 verifier schema 不合规时会记录 fallback 来源，并正确解析字符串布尔值。
def test_trajectory_evaluator_marks_invalid_verifier_reject_reason_schema() -> None:
    evaluator = TrajectoryEvaluator(
        TrajectoryEvaluatorConfig(agent_eval_mode="llm_verifier"),
        llm_client=InvalidReasonVerifierClient(),  # type: ignore[arg-type]
    )
    trajectories = [
        ReasoningTrajectory(
            trajectory_id="t1",
            final_answer_id="d1",
            final_answer_name="肺孢子菌肺炎",
            steps=[{"action_name": "发热"}],
            score=0.5,
        )
    ]

    grouped = evaluator.group_by_answer(trajectories)
    scores = evaluator.score_groups(grouped, patient_context=PatientContext(raw_text="发热"))

    assert scores[0].metadata["verifier_should_accept"] is False
    assert scores[0].metadata["verifier_reject_reason"] == "strong_alternative_not_ruled_out"
    assert scores[0].metadata["verifier_reject_reason_source"] == "fallback_inferred"
    assert scores[0].metadata["verifier_schema_valid"] is False


# 验证 rollout 模拟阳性不能替代真实会话已确认证据来触发 stop。
def test_trajectory_evaluator_blocks_acceptance_when_only_simulated_key_evidence_exists() -> None:
    evaluator = TrajectoryEvaluator(
        TrajectoryEvaluatorConfig(agent_eval_mode="llm_verifier"),
        llm_client=SimulatedEvidenceAcceptingVerifierClient(),  # type: ignore[arg-type]
    )
    trajectories = [
        ReasoningTrajectory(
            trajectory_id="t1",
            final_answer_id="tb",
            final_answer_name="活动性结核病",
            steps=[
                {"stage": "A3", "action_id": "a1", "action_name": "痰分枝杆菌培养", "question_type_hint": "lab"},
                {"stage": "PENDING_ACTION", "polarity": "present", "resolution": "clear", "answer_branch": "positive"},
            ],
            score=0.9,
        )
    ]
    patient_context = PatientContext(
        raw_text="发热伴咳嗽",
        metadata={"observed_session_evidence": []},
    )

    grouped = evaluator.group_by_answer(trajectories)
    scores = evaluator.score_groups(grouped, patient_context=patient_context)

    assert scores[0].metadata["verifier_should_accept"] is False
    assert scores[0].metadata["verifier_reject_reason"] == "missing_key_support"
    assert scores[0].metadata["verifier_reject_reason_source"] == "observed_evidence_guard"
    assert scores[0].metadata["verifier_acceptance_blocked_by_observed_evidence_guard"] is True


# 验证没有 rollout 轨迹时，候选态兜底也能生成保守的 final answer score。
def test_trajectory_evaluator_scores_candidate_hypotheses_without_trajectories() -> None:
    evaluator = TrajectoryEvaluator()
    hypotheses = [
        HypothesisScore(node_id="d1", label="Disease", name="水痘-带状疱疹病毒感染", score=0.92, metadata={}),
        HypothesisScore(node_id="d2", label="Disease", name="结核病", score=0.8, metadata={}),
    ]
    patient_context = PatientContext(
        raw_text="发热",
        metadata={
            "observed_session_evidence": [
                {
                    "source": "observed_evidence_state",
                    "node_id": "vzb",
                    "name": "水痘-带状疱疹病毒",
                    "polarity": "present",
                    "existence": "exist",
                    "resolution": "clear",
                    "relation_type": "HAS_PATHOGEN",
                }
            ]
        },
    )

    scores = evaluator.score_candidate_hypotheses_without_trajectories(hypotheses, patient_context=patient_context)

    assert len(scores) == 2
    assert scores[0].answer_id == "d1"
    assert scores[0].metadata["answer_score_source"] == "candidate_state_fallback"
    assert scores[0].metadata["trajectory_count"] == 0


# 验证在未达到可终止观察窗口前，llm verifier 会延后到后续轮次再调用。
def test_trajectory_evaluator_defers_llm_verifier_before_accept_window() -> None:
    class CountingVerifierClient(FakeVerifierClient):
        def __init__(self) -> None:
            self.called = False

        def run_structured_prompt(self, prompt_name: str, variables: dict, schema: type) -> dict:
            self.called = True
            return super().run_structured_prompt(prompt_name, variables, schema)

    client = CountingVerifierClient()
    evaluator = TrajectoryEvaluator(
        TrajectoryEvaluatorConfig(
            agent_eval_mode="llm_verifier",
            llm_verifier_min_turn_index=2,
            llm_verifier_min_trajectory_count=2,
        ),
        llm_client=client,  # type: ignore[arg-type]
    )
    trajectories = [
        ReasoningTrajectory(
            trajectory_id="t1",
            final_answer_id="d1",
            final_answer_name="肺孢子菌肺炎",
            steps=[{"action_name": "发热"}],
            score=0.7,
        )
    ]

    grouped = evaluator.group_by_answer(trajectories)
    scores = evaluator.score_groups(
        grouped,
        patient_context=PatientContext(raw_text="发热"),
        session_turn_index=1,
    )

    assert client.called is False
    assert scores[0].metadata["verifier_mode"] == "llm_verifier_deferred"
    assert scores[0].metadata["verifier_called"] is False
    assert scores[0].metadata["verifier_deferred_reason"] == "turn_index_too_low"
