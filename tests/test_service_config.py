"""测试默认配置文件会被读取并映射到运行参数。"""

from pathlib import Path

from brain.service import build_default_brain, load_brain_config


class FakeAvailableLlmClient:
    """提供最小可用 LLM client，避免默认构造测试依赖真实外部服务。"""

    def __init__(self) -> None:
        self.structured_retry_count = 0

    def is_available(self) -> bool:
        return True

    def close(self) -> None:
        return None


# 验证 load_brain_config 能从 YAML 文件中读出结构化配置。
def test_load_brain_config_reads_yaml_file(tmp_path: Path) -> None:
    config_path = tmp_path / "brain.yaml"
    config_path.write_text(
        "\n".join(
            [
                "search:",
                "  num_rollouts: 5",
                "rollout_control:",
                "  enable_multi_branch_rollout: true",
                "  branch_budget_per_action: 2",
                "path_evaluation:",
                "  agent_eval_mode: llm_verifier",
                "  llm_verifier_min_turn_index: 2",
                "  llm_verifier_min_trajectory_count: 2",
                "  enable_dynamic_group_weights: true",
                "llm:",
                "  structured_retry_count: 1",
                "a2:",
                "  enable_scope_cluster_rerank: true",
                "  scope_cluster_exact_bonus: 0.4",
                "repair:",
                "  enable_tree_reroot: false",
                "  protect_repair_action_from_low_cost_explorer: true",
                "a3:",
                "  enable_early_exam_context_rescue: true",
                "  early_exam_context_turn_limit: 2",
                "candidate_feedback:",
                "  enable_multi_hypothesis_feedback: true",
                "  max_related_hypotheses_per_evidence: 4",
            ]
        ),
        encoding="utf-8",
    )

    config = load_brain_config(config_path)

    assert config["search"]["num_rollouts"] == 5
    assert config["rollout_control"]["enable_multi_branch_rollout"] is True
    assert config["rollout_control"]["branch_budget_per_action"] == 2
    assert config["path_evaluation"]["agent_eval_mode"] == "llm_verifier"
    assert config["path_evaluation"]["llm_verifier_min_turn_index"] == 2
    assert config["path_evaluation"]["llm_verifier_min_trajectory_count"] == 2
    assert config["path_evaluation"]["enable_dynamic_group_weights"] is True
    assert config["llm"]["structured_retry_count"] == 1
    assert config["a2"]["enable_scope_cluster_rerank"] is True
    assert config["a2"]["scope_cluster_exact_bonus"] == 0.4
    assert config["repair"]["enable_tree_reroot"] is False
    assert config["repair"]["protect_repair_action_from_low_cost_explorer"] is True
    assert config["a3"]["enable_early_exam_context_rescue"] is True
    assert config["a3"]["early_exam_context_turn_limit"] == 2
    assert config["candidate_feedback"]["enable_multi_hypothesis_feedback"] is True
    assert config["candidate_feedback"]["max_related_hypotheses_per_evidence"] == 4
    assert "stop" not in config


# 验证默认构造会真正读取 a3 / repair 配置，并且不会在启动阶段遗漏配置变量。
def test_build_default_brain_maps_a3_and_repair_config() -> None:
    brain = build_default_brain(
        client=object(),
        config_overrides={
            "a3": {
                "enable_early_exam_context_rescue": True,
                "early_exam_context_turn_limit": 3,
                "early_exam_context_revealed_count_threshold": 1,
                "exam_context_rescue_high_cost_role_threshold": 0.55,
            },
            "repair": {
                "protect_repair_action_from_low_cost_explorer": True,
                "allow_low_cost_explorer_after_repair_if_unaskable_only": False,
                "enable_missing_key_support_competition_escalation": True,
                "missing_key_support_retry_threshold": 3,
            },
            "rollout_control": {
                "enable_multi_branch_rollout": True,
                "branch_budget_per_action": 2,
                "enable_anti_collapse_penalty": True,
            },
            "path_evaluation": {
                "enable_dynamic_group_weights": True,
                "enable_single_answer_group_cap": True,
                "low_anchor_single_group_score_cap": 0.58,
                "enable_scope_penalty_in_final_score": True,
            },
            "a2": {
                "enable_scope_cluster_rerank": True,
                "scope_cluster_exact_bonus": 0.4,
                "scope_cluster_generic_penalty": 0.3,
            },
            "candidate_feedback": {
                "enable_multi_hypothesis_feedback": True,
                "use_scope_weighted_feedback": True,
                "max_related_hypotheses_per_evidence": 4,
            },
        },
        llm_client=FakeAvailableLlmClient(),
    )

    assert brain.deps.a3_routing_policy.enable_early_exam_context_rescue is True
    assert brain.deps.a3_routing_policy.early_exam_context_turn_limit == 3
    assert brain.deps.a3_routing_policy.early_exam_context_revealed_count_threshold == 1
    assert brain.deps.a3_routing_policy.exam_context_rescue_high_cost_role_threshold == 0.55
    assert brain.deps.repair_policy.protect_repair_action_from_low_cost_explorer is True
    assert brain.deps.repair_policy.allow_low_cost_explorer_after_repair_if_unaskable_only is False
    assert brain.deps.repair_policy.enable_missing_key_support_competition_escalation is True
    assert brain.deps.repair_policy.missing_key_support_retry_threshold == 3
    assert brain.deps.simulation_engine.config.enable_multi_branch_rollout is True
    assert brain.deps.simulation_engine.config.branch_budget_per_action == 2
    assert brain.deps.simulation_engine.config.enable_anti_collapse_penalty is True
    assert brain.deps.trajectory_evaluator.config.enable_dynamic_group_weights is True
    assert brain.deps.trajectory_evaluator.config.enable_single_answer_group_cap is True
    assert brain.deps.trajectory_evaluator.config.low_anchor_single_group_score_cap == 0.58
    assert brain.deps.trajectory_evaluator.config.enable_scope_penalty_in_final_score is True
    assert brain.deps.evidence_anchor_analyzer.config.enable_scope_cluster_rerank is True
    assert brain.deps.evidence_anchor_analyzer.config.scope_cluster_exact_bonus == 0.4
    assert brain.deps.evidence_anchor_analyzer.config.scope_cluster_generic_penalty == 0.3
    assert brain.deps.hypothesis_manager.config.enable_multi_hypothesis_feedback is True
    assert brain.deps.hypothesis_manager.config.use_scope_weighted_feedback is True
    assert brain.deps.hypothesis_manager.config.max_related_hypotheses_per_evidence == 4
