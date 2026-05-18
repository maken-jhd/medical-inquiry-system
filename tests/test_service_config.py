"""测试默认配置文件会被读取并映射到运行参数。"""

import json
from pathlib import Path

from brain.search import StatisticalResponseTransitionModel
from brain.search import BeliefAwareRolloutRewardModel
from brain.search.tree import SearchTree
from brain.service import build_default_brain, load_brain_config
from brain.state import MctsAction


class FakeAvailableLlmClient:
    """提供最小可用 LLM client，避免默认构造测试依赖真实外部服务。"""

    def __init__(self) -> None:
        self.structured_retry_count = 0

    def is_available(self) -> bool:
        return True

    def close(self) -> None:
        return None


def _write_minimal_statistical_sources(tmp_path: Path) -> tuple[Path, Path, Path]:
    evidence_catalog_path = tmp_path / "disease_evidence_family_catalog.json"
    cases_path = tmp_path / "cases.jsonl"
    replay_path = tmp_path / "replay_results.jsonl"
    evidence_catalog_path.write_text(
        json.dumps(
            {
                "diseases": [
                    {
                        "disease_id": "d1",
                        "disease_name": "肺孢子菌肺炎",
                        "evidence": [
                            {
                                "evidence_id": "slot_cough",
                                "evidence_group": "symptom",
                                "families": ["respiratory_symptom"],
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cases_path.write_text(
        json.dumps(
            {
                "case_id": "case_pcp",
                "true_conditions": ["肺孢子菌肺炎"],
                "slot_truth_map": {
                    "slot_cough": {
                        "node_id": "slot_cough",
                        "value": True,
                        "group": "symptom",
                        "node_label": "ClinicalFinding",
                    }
                },
                "metadata": {"disease_id": "d1"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    replay_path.write_text("", encoding="utf-8")
    return evidence_catalog_path, cases_path, replay_path


# 验证 load_brain_config 能从 YAML 文件中读出结构化配置。
def test_load_brain_config_reads_yaml_file(tmp_path: Path) -> None:
    config_path = tmp_path / "brain.yaml"
    config_path.write_text(
        "\n".join(
            [
                "search:",
                "  num_rollouts: 5",
                "  discriminative_gain_weight: 0.18",
                "search_impl: modular_v2",
                "search_policy:",
                "  root_action_mode: no_tree_greedy",
                "  disable_verifier_repair_for_greedy: true",
                "  disable_early_exam_context_rescue_for_greedy: true",
                "  disable_low_cost_explorer_for_greedy: true",
                "rollout_control:",
                "  enable_multi_branch_rollout: true",
                "  branch_budget_per_action: 2",
                "  branch_selection_mode: expectation_ready",
                "transition_model:",
                "  type: heuristic",
                "  statistics_top_k_hypotheses: 5",
                "  statistics_min_total_count: 3",
                "  hybrid_enable_count_aware_mixing: true",
                "  hybrid_count_threshold_low: 4",
                "  hybrid_count_threshold_mid: 9",
                "  hybrid_count_threshold_high: 21",
                "  hybrid_lambda_low: 0.2",
                "  hybrid_lambda_mid: 0.45",
                "  hybrid_lambda_high: 0.75",
                "  backoff_discount_disease_family_question_type: 1.0",
                "  backoff_discount_disease_question_type: 0.82",
                "  backoff_discount_family_question_type: 0.61",
                "  backoff_discount_question_type: 0.35",
                "  backoff_discount_global: 0.12",
                "reward_model:",
                "  type: belief_aware_v1",
                "  belief_top_k_hypotheses: 5",
                "  enable_top3_separation_gain: true",
                "  enable_competitor_elimination_bonus: true",
                "  enable_discriminative_support_bonus: true",
                "  enable_alternative_preservation_bonus: true",
                "  top3_separation_weight: 0.29",
                "  competitor_elimination_weight: 0.31",
                "  discriminative_support_weight: 0.21",
                "  alternative_preservation_weight: 0.14",
                "  detail_non_discriminative_penalty: 0.07",
                "  posterior_update_alpha: 0.63",
                "state_signature:",
                "  include_exam_context: true",
                "  include_top_hypotheses: true",
                "path_evaluation:",
                "  agent_eval_mode: llm_verifier",
                "  llm_verifier_min_turn_index: 2",
                "  llm_verifier_min_trajectory_count: 2",
                "  enable_dynamic_group_weights: true",
                "  enable_discriminative_answer_bonus: true",
                "  discriminative_answer_bonus_weight: 0.11",
                "  competitor_suppression_bonus_weight: 0.09",
                "  rank_stability_bonus_weight: 0.07",
                "  discriminative_support_bonus_weight: 0.06",
                "  top3_coverage_stability_bonus_weight: 0.05",
                "acceptance_calibration:",
                "  margin_relaxation_buffer: 0.03",
                "  risk_relaxation_buffer: 0.05",
                "  high_support_quality_override: 0.61",
                "  high_support_quality_risk_discount: 0.04",
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
                "  enable_top3_candidate_rescue: true",
                "  top3_rescue_rank_window: 7",
                "  top3_rescue_bonus: 0.09",
                "  enable_candidate_rank_memory: true",
                "  candidate_rank_memory_bonus: 0.06",
                "  candidate_rank_memory_decay: 0.58",
                "  enable_evidence_supported_rerank: true",
                "  positive_evidence_support_weight: 0.05",
                "  evidence_family_diversity_weight: 0.035",
                "  contradiction_penalty_weight: 0.055",
                "  max_related_hypotheses_per_evidence: 4",
            ]
        ),
        encoding="utf-8",
    )

    config = load_brain_config(config_path)

    assert config["search"]["num_rollouts"] == 5
    assert config["search"]["discriminative_gain_weight"] == 0.18
    assert config["search_impl"] == "modular_v2"
    assert config["search_policy"]["root_action_mode"] == "no_tree_greedy"
    assert config["search_policy"]["disable_verifier_repair_for_greedy"] is True
    assert config["search_policy"]["disable_early_exam_context_rescue_for_greedy"] is True
    assert config["search_policy"]["disable_low_cost_explorer_for_greedy"] is True
    assert config["rollout_control"]["enable_multi_branch_rollout"] is True
    assert config["rollout_control"]["branch_budget_per_action"] == 2
    assert config["rollout_control"]["branch_selection_mode"] == "expectation_ready"
    assert config["transition_model"]["type"] == "heuristic"
    assert config["transition_model"]["statistics_top_k_hypotheses"] == 5
    assert config["transition_model"]["statistics_min_total_count"] == 3
    assert config["transition_model"]["hybrid_enable_count_aware_mixing"] is True
    assert config["transition_model"]["hybrid_count_threshold_low"] == 4
    assert config["transition_model"]["hybrid_count_threshold_mid"] == 9
    assert config["transition_model"]["hybrid_count_threshold_high"] == 21
    assert config["transition_model"]["hybrid_lambda_low"] == 0.2
    assert config["transition_model"]["hybrid_lambda_mid"] == 0.45
    assert config["transition_model"]["hybrid_lambda_high"] == 0.75
    assert config["transition_model"]["backoff_discount_global"] == 0.12
    assert config["reward_model"]["type"] == "belief_aware_v1"
    assert config["reward_model"]["belief_top_k_hypotheses"] == 5
    assert config["reward_model"]["top3_separation_weight"] == 0.29
    assert config["reward_model"]["competitor_elimination_weight"] == 0.31
    assert config["reward_model"]["discriminative_support_weight"] == 0.21
    assert config["reward_model"]["enable_alternative_preservation_bonus"] is True
    assert config["reward_model"]["alternative_preservation_weight"] == 0.14
    assert config["reward_model"]["detail_non_discriminative_penalty"] == 0.07
    assert config["reward_model"]["posterior_update_alpha"] == 0.63
    assert config["state_signature"]["include_exam_context"] is True
    assert config["state_signature"]["include_top_hypotheses"] is True
    assert config["path_evaluation"]["agent_eval_mode"] == "llm_verifier"
    assert config["path_evaluation"]["llm_verifier_min_turn_index"] == 2
    assert config["path_evaluation"]["llm_verifier_min_trajectory_count"] == 2
    assert config["path_evaluation"]["enable_dynamic_group_weights"] is True
    assert config["path_evaluation"]["enable_discriminative_answer_bonus"] is True
    assert config["path_evaluation"]["discriminative_answer_bonus_weight"] == 0.11
    assert config["path_evaluation"]["competitor_suppression_bonus_weight"] == 0.09
    assert config["path_evaluation"]["rank_stability_bonus_weight"] == 0.07
    assert config["path_evaluation"]["discriminative_support_bonus_weight"] == 0.06
    assert config["path_evaluation"]["top3_coverage_stability_bonus_weight"] == 0.05
    assert config["acceptance_calibration"]["margin_relaxation_buffer"] == 0.03
    assert config["acceptance_calibration"]["risk_relaxation_buffer"] == 0.05
    assert config["acceptance_calibration"]["high_support_quality_override"] == 0.61
    assert config["acceptance_calibration"]["high_support_quality_risk_discount"] == 0.04
    assert config["llm"]["structured_retry_count"] == 1
    assert config["a2"]["enable_scope_cluster_rerank"] is True
    assert config["a2"]["scope_cluster_exact_bonus"] == 0.4
    assert config["repair"]["enable_tree_reroot"] is False
    assert config["repair"]["protect_repair_action_from_low_cost_explorer"] is True
    assert config["repair"]["protect_search_root_action_from_low_cost_explorer"] is True
    assert config["a3"]["enable_early_exam_context_rescue"] is True
    assert config["a3"]["early_exam_context_turn_limit"] == 2
    assert config["candidate_feedback"]["enable_multi_hypothesis_feedback"] is True
    assert config["candidate_feedback"]["enable_top3_candidate_rescue"] is True
    assert config["candidate_feedback"]["top3_rescue_rank_window"] == 7
    assert config["candidate_feedback"]["top3_rescue_bonus"] == 0.09
    assert config["candidate_feedback"]["enable_candidate_rank_memory"] is True
    assert config["candidate_feedback"]["candidate_rank_memory_bonus"] == 0.06
    assert config["candidate_feedback"]["candidate_rank_memory_decay"] == 0.58
    assert config["candidate_feedback"]["enable_evidence_supported_rerank"] is True
    assert config["candidate_feedback"]["positive_evidence_support_weight"] == 0.05
    assert config["candidate_feedback"]["evidence_family_diversity_weight"] == 0.035
    assert config["candidate_feedback"]["contradiction_penalty_weight"] == 0.055
    assert config["candidate_feedback"]["max_related_hypotheses_per_evidence"] == 4
    assert "stop" not in config


# 验证 load_brain_config 也支持从环境变量切换整套 benchmark 配置文件。
def test_load_brain_config_supports_env_override(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "brain_env.yaml"
    config_path.write_text(
        "\n".join(
            [
                "search_impl: modular_v2",
                "search_policy:",
                "  root_action_mode: mcts",
                "repair:",
                "  enable_best_repair_action: false",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("BRAIN_CONFIG_PATH", str(config_path))
    default_config = load_brain_config(Path(__file__).resolve().parents[1] / "configs" / "brain.yaml")
    config = load_brain_config()

    assert config["search_impl"] == "modular_v2"
    assert config["search_policy"]["root_action_mode"] == "mcts"
    assert config["repair"]["enable_best_repair_action"] is False
    assert config["path_evaluation"]["agent_eval_mode"] == default_config["path_evaluation"]["agent_eval_mode"]
    assert (
        config["path_evaluation"]["llm_verifier_min_turn_index"]
        == default_config["path_evaluation"]["llm_verifier_min_turn_index"]
    )
    assert (
        config["path_evaluation"]["llm_verifier_min_trajectory_count"]
        == default_config["path_evaluation"]["llm_verifier_min_trajectory_count"]
    )


# 验证默认构造会真正读取 a3 / repair 配置，并且不会在启动阶段遗漏配置变量。
def test_build_default_brain_maps_a3_and_repair_config() -> None:
    brain = build_default_brain(
        client=object(),
        config_overrides={
            "search_impl": "modular_v2",
            "search_policy": {
                "root_action_mode": "no_tree_greedy",
                "disable_verifier_repair_for_greedy": True,
                "disable_early_exam_context_rescue_for_greedy": True,
                "disable_low_cost_explorer_for_greedy": True,
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
                "branch_selection_mode": "expectation_ready",
            },
            "transition_model": {
                "type": "heuristic",
            },
            "reward_model": {
                "type": "heuristic_v2",
                "turn_cost": 0.08,
            },
            "state_signature": {
                "include_exam_context": False,
                "include_top_hypotheses": True,
                "max_top_hypotheses": 2,
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
                "enable_top3_candidate_rescue": True,
                "top3_rescue_rank_window": 7,
                "top3_rescue_bonus": 0.09,
                "enable_candidate_rank_memory": True,
                "candidate_rank_memory_bonus": 0.06,
                "candidate_rank_memory_decay": 0.58,
                "enable_evidence_supported_rerank": True,
                "positive_evidence_support_weight": 0.05,
                "evidence_family_diversity_weight": 0.035,
                "contradiction_penalty_weight": 0.055,
            },
        },
        llm_client=FakeAvailableLlmClient(),
    )

    assert brain.deps.mcts_engine.config.search_impl == "modular_v2"
    assert brain.deps.search_policy.root_action_mode == "no_tree_greedy"
    assert brain.deps.search_policy.disable_verifier_repair_for_greedy is True
    assert brain.deps.search_policy.disable_early_exam_context_rescue_for_greedy is True
    assert brain.deps.search_policy.disable_low_cost_explorer_for_greedy is True
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
    assert brain.deps.simulation_engine.config.search_impl == "modular_v2"
    assert brain.deps.simulation_engine.config.enable_multi_branch_rollout is True
    assert brain.deps.simulation_engine.config.branch_budget_per_action == 2
    assert brain.deps.simulation_engine.config.enable_anti_collapse_penalty is True
    assert brain.deps.simulation_engine.config.branch_selection_mode == "expectation_ready"
    assert brain.deps.simulation_engine.config.transition_model_type == "heuristic"
    assert brain.deps.simulation_engine.config.reward_model_type == "heuristic_v2"
    assert brain.deps.mcts_engine.state_signature_builder.config.include_exam_context is False
    assert brain.deps.mcts_engine.state_signature_builder.config.max_top_hypotheses == 2
    assert brain.deps.simulation_engine.reward_model.config.turn_cost == 0.08
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
    assert brain.deps.hypothesis_manager.config.enable_top3_candidate_rescue is True
    assert brain.deps.hypothesis_manager.config.top3_rescue_rank_window == 7
    assert brain.deps.hypothesis_manager.config.top3_rescue_bonus == 0.09
    assert brain.deps.hypothesis_manager.config.enable_candidate_rank_memory is True
    assert brain.deps.hypothesis_manager.config.candidate_rank_memory_bonus == 0.06
    assert brain.deps.hypothesis_manager.config.candidate_rank_memory_decay == 0.58
    assert brain.deps.hypothesis_manager.config.enable_evidence_supported_rerank is True
    assert brain.deps.hypothesis_manager.config.positive_evidence_support_weight == 0.05
    assert brain.deps.hypothesis_manager.config.evidence_family_diversity_weight == 0.035
    assert brain.deps.hypothesis_manager.config.contradiction_penalty_weight == 0.055


# 验证 build_default_brain 也支持 statistical transition model 的配置装配。
def test_build_default_brain_supports_statistical_transition_model(tmp_path: Path) -> None:
    evidence_catalog_path, cases_path, replay_path = _write_minimal_statistical_sources(tmp_path)
    brain = build_default_brain(
        client=object(),
        config_overrides={
            "search_impl": "modular_v2",
            "transition_model": {
                "type": "statistical",
                "statistics_source_mode": "explicit",
                "statistics_top_k_hypotheses": 2,
                "statistics_min_total_count": 4,
                "hybrid_enable_count_aware_mixing": True,
                "hybrid_count_threshold_low": 4,
                "hybrid_count_threshold_mid": 9,
                "hybrid_count_threshold_high": 18,
                "hybrid_lambda_low": 0.22,
                "hybrid_lambda_mid": 0.48,
                "hybrid_lambda_high": 0.76,
                "backoff_discount_disease_family_question_type": 1.0,
                "backoff_discount_disease_question_type": 0.84,
                "backoff_discount_family_question_type": 0.63,
                "backoff_discount_question_type": 0.38,
                "backoff_discount_global": 0.11,
                "graph_case_paths": [str(cases_path)],
                "replay_result_paths": [str(replay_path)],
                "evidence_catalog_paths": [str(evidence_catalog_path)],
            },
        },
        llm_client=FakeAvailableLlmClient(),
    )

    assert brain.deps.simulation_engine.config.transition_model_type == "statistical"
    assert isinstance(brain.deps.simulation_engine.transition_model, StatisticalResponseTransitionModel)
    assert brain.deps.simulation_engine.transition_model.config.statistics_top_k_hypotheses == 2
    assert brain.deps.simulation_engine.transition_model.config.statistics_min_total_count == 4
    assert brain.deps.simulation_engine.transition_model.config.hybrid_enable_count_aware_mixing is True
    assert brain.deps.simulation_engine.transition_model.config.hybrid_count_threshold_mid == 9
    assert brain.deps.simulation_engine.transition_model.config.hybrid_lambda_high == 0.76
    assert brain.deps.simulation_engine.transition_model.config.backoff_discount_global == 0.11


# 验证 build_default_brain 也支持 belief-aware reward 与 acceptance calibration 的配置装配。
def test_build_default_brain_supports_belief_aware_reward_and_acceptance_calibration() -> None:
    brain = build_default_brain(
        client=object(),
        config_overrides={
            "search_impl": "modular_v2",
            "reward_model": {
                "type": "belief_aware_v1",
                "belief_top_k_hypotheses": 5,
                "enable_belief_margin_gain": True,
                "enable_top3_separation_gain": True,
                "enable_alternative_preservation_bonus": True,
                "enable_acceptance_risk_penalty": True,
                "margin_gain_weight": 0.41,
                "top3_separation_weight": 0.27,
                "uncertainty_reduction_weight": 0.27,
                "competitor_elimination_weight": 0.29,
                "discriminative_support_weight": 0.19,
                "alternative_preservation_weight": 0.13,
                "acceptance_risk_weight": 0.19,
                "detail_non_discriminative_penalty": 0.06,
                "posterior_update_alpha": 0.61,
                "enable_stage_aware_coverage_control": True,
                "early_stage_turn_cutoff": 2,
                "stage_aware_entropy_threshold": 0.57,
                "stage_aware_margin_threshold": 0.16,
                "early_narrow_evidence_penalty_weight": 0.09,
                "early_broad_coverage_bonus_weight": 0.07,
                "early_over_collapse_penalty_weight": 0.11,
                "stage_aware_competitor_elimination_scale": 0.49,
            },
            "search": {
                "discriminative_gain_weight": 0.17,
            },
            "acceptance_calibration": {
                "enable_reward_confidence_proxy": True,
                "min_belief_margin_proxy": 0.1,
                "max_acceptance_risk_proxy": 0.24,
                "min_branch_support_quality": 0.45,
                "margin_relaxation_buffer": 0.03,
                "risk_relaxation_buffer": 0.055,
                "high_support_quality_override": 0.64,
                "high_support_quality_risk_discount": 0.06,
            },
            "path_evaluation": {
                "enable_discriminative_answer_bonus": True,
                "discriminative_answer_bonus_weight": 0.1,
                "competitor_suppression_bonus_weight": 0.08,
                "rank_stability_bonus_weight": 0.06,
                "discriminative_support_bonus_weight": 0.05,
                "top3_coverage_stability_bonus_weight": 0.04,
                "answer_specific_support_bonus_weight": 0.08,
                "scope_consistency_bonus_weight": 0.07,
                "multi_path_consensus_bonus_weight": 0.06,
                "fragile_single_path_penalty_weight": 0.09,
            },
        },
        llm_client=FakeAvailableLlmClient(),
    )

    assert brain.deps.simulation_engine.config.reward_model_type == "belief_aware_v1"
    assert isinstance(brain.deps.simulation_engine.reward_model, BeliefAwareRolloutRewardModel)
    assert brain.deps.simulation_engine.reward_model.config.belief_top_k_hypotheses == 5
    assert brain.deps.simulation_engine.reward_model.config.margin_gain_weight == 0.41
    assert brain.deps.simulation_engine.reward_model.config.top3_separation_weight == 0.27
    assert brain.deps.simulation_engine.reward_model.config.competitor_elimination_weight == 0.29
    assert brain.deps.simulation_engine.reward_model.config.discriminative_support_weight == 0.19
    assert brain.deps.simulation_engine.reward_model.config.alternative_preservation_weight == 0.13
    assert brain.deps.simulation_engine.reward_model.config.detail_non_discriminative_penalty == 0.06
    assert brain.deps.simulation_engine.reward_model.config.posterior_update_alpha == 0.61
    assert brain.deps.simulation_engine.reward_model.config.enable_stage_aware_coverage_control is True
    assert brain.deps.simulation_engine.reward_model.config.stage_aware_entropy_threshold == 0.57
    assert brain.deps.simulation_engine.reward_model.config.stage_aware_margin_threshold == 0.16
    assert brain.deps.simulation_engine.reward_model.config.early_narrow_evidence_penalty_weight == 0.09
    assert brain.deps.simulation_engine.reward_model.config.early_broad_coverage_bonus_weight == 0.07
    assert brain.deps.simulation_engine.reward_model.config.early_over_collapse_penalty_weight == 0.11
    assert brain.deps.simulation_engine.reward_model.config.stage_aware_competitor_elimination_scale == 0.49
    assert brain.deps.mcts_engine.config.discriminative_gain_weight == 0.17
    assert brain.deps.acceptance_controller.config.max_acceptance_risk_proxy == 0.24
    assert brain.deps.acceptance_controller.config.margin_relaxation_buffer == 0.03
    assert brain.deps.acceptance_controller.config.risk_relaxation_buffer == 0.055
    assert brain.deps.acceptance_controller.config.high_support_quality_override == 0.64
    assert brain.deps.acceptance_controller.config.high_support_quality_risk_discount == 0.06
    assert brain.deps.trajectory_evaluator.config.enable_discriminative_answer_bonus is True
    assert brain.deps.trajectory_evaluator.config.discriminative_answer_bonus_weight == 0.1
    assert brain.deps.trajectory_evaluator.config.competitor_suppression_bonus_weight == 0.08
    assert brain.deps.trajectory_evaluator.config.top3_coverage_stability_bonus_weight == 0.04
    assert brain.deps.trajectory_evaluator.config.answer_specific_support_bonus_weight == 0.08
    assert brain.deps.trajectory_evaluator.config.scope_consistency_bonus_weight == 0.07
    assert brain.deps.trajectory_evaluator.config.multi_path_consensus_bonus_weight == 0.06
    assert brain.deps.trajectory_evaluator.config.fragile_single_path_penalty_weight == 0.09


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
