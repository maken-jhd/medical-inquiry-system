"""测试默认配置文件会被读取并映射到运行参数。"""

from pathlib import Path

from brain.search_tree import SearchTree
from brain.service import build_default_brain, load_brain_config
from brain.types import MctsAction


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
                "search_policy:",
                "  root_action_mode: no_tree_greedy",
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
                "  protect_search_root_action_from_low_cost_explorer: true",
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
    assert config["search_policy"]["root_action_mode"] == "no_tree_greedy"
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
    assert config["repair"]["protect_search_root_action_from_low_cost_explorer"] is True
    assert config["a3"]["enable_early_exam_context_rescue"] is True
    assert config["a3"]["early_exam_context_turn_limit"] == 2
    assert config["candidate_feedback"]["enable_multi_hypothesis_feedback"] is True
    assert config["candidate_feedback"]["max_related_hypotheses_per_evidence"] == 4
    assert "stop" not in config


# 验证 load_brain_config 也支持从环境变量切换整套 benchmark 配置文件。
def test_load_brain_config_supports_env_override(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "brain_env.yaml"
    config_path.write_text(
        "\n".join(
            [
                "search_policy:",
                "  root_action_mode: mcts",
                "repair:",
                "  enable_best_repair_action: false",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("BRAIN_CONFIG_PATH", str(config_path))
    config = load_brain_config()

    assert config["search_policy"]["root_action_mode"] == "mcts"
    assert config["repair"]["enable_best_repair_action"] is False


# 验证默认构造会真正读取 a3 / repair 配置，并且不会在启动阶段遗漏配置变量。
def test_build_default_brain_maps_a3_and_repair_config() -> None:
    brain = build_default_brain(
        client=object(),
        config_overrides={
            "search_policy": {
                "root_action_mode": "no_tree_greedy",
            },
            "a3": {
                "enable_early_exam_context_rescue": True,
                "early_exam_context_turn_limit": 3,
                "early_exam_context_revealed_count_threshold": 1,
                "exam_context_rescue_high_cost_role_threshold": 0.55,
            },
            "repair": {
                "enable_best_repair_action": False,
                "protect_repair_action_from_low_cost_explorer": True,
                "protect_search_root_action_from_low_cost_explorer": True,
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

    assert brain.deps.search_policy.root_action_mode == "no_tree_greedy"
    assert brain.deps.a3_routing_policy.enable_early_exam_context_rescue is True
    assert brain.deps.a3_routing_policy.early_exam_context_turn_limit == 3
    assert brain.deps.a3_routing_policy.early_exam_context_revealed_count_threshold == 1
    assert brain.deps.a3_routing_policy.exam_context_rescue_high_cost_role_threshold == 0.55
    assert brain.deps.repair_policy.enable_best_repair_action is False
    assert brain.deps.repair_policy.protect_repair_action_from_low_cost_explorer is True
    assert brain.deps.repair_policy.protect_search_root_action_from_low_cost_explorer is True
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


# 验证 service 层会按 search_policy 把根动作选择分发给 mcts 或 greedy selector。
def test_search_policy_dispatches_root_action_selector() -> None:
    brain = build_default_brain(
        client=object(),
        config_overrides={
            "search_policy": {
                "root_action_mode": "greedy",
            },
        },
        llm_client=FakeAvailableLlmClient(),
    )
    greedy_action = MctsAction(
        action_id="greedy_action",
        action_type="verify_evidence",
        target_node_id="node_greedy",
        target_node_label="ClinicalFinding",
        target_node_name="咳嗽",
        prior_score=0.8,
    )
    mcts_action = MctsAction(
        action_id="mcts_action",
        action_type="verify_evidence",
        target_node_id="node_mcts",
        target_node_label="ClinicalFinding",
        target_node_name="发热",
        prior_score=0.6,
    )

    brain.deps.mcts_engine.select_root_action = lambda tree, excluded_target_node_ids=None: mcts_action
    brain.deps.mcts_engine.select_root_action_greedy = lambda tree, excluded_target_node_ids=None: greedy_action

    selected_greedy = brain._select_root_action_with_policy(SearchTree())
    brain.deps.search_policy.root_action_mode = "mcts"
    selected_mcts = brain._select_root_action_with_policy(SearchTree())

    assert selected_greedy is greedy_action
    assert selected_mcts is mcts_action
