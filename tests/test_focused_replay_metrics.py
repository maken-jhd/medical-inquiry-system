"""测试 focused replay 的 acceptance 校准指标。"""

from scripts.run_focused_ablation import _augment_metrics
from scripts.run_focused_repair_replay import _is_correct_best_answer, _summarize_focused_rows


# 验证 focused replay 能把正确/错误与接受/拒停分成四类。
def test_focused_replay_summarizes_acceptance_categories() -> None:
    rows = [
        {
            "final_best_answer_name": "肺孢子菌肺炎 (PCP)",
            "true_conditions": ["肺孢子菌肺炎 (PCP)"],
            "true_disease_phase": "AIDS期",
            "final_stop_reason": "final_answer_accepted",
            "case_id": "accepted_case",
            "turn_summaries": [
                {
                    "turn_index": 1,
                    "best_answer_name": "肺孢子菌肺炎 (PCP)",
                    "best_answer_verifier_mode": "llm_verifier",
                    "best_answer_verifier_called": True,
                    "best_answer_verifier_should_accept": True,
                    "best_answer_verifier_accept_reason": "key_support_sufficient",
                    "best_answer_verifier_alternative_candidates": [],
                    "best_answer_verifier_schema_valid": True,
                    "best_answer_verifier_reject_reason_source": "llm_schema",
                    "best_answer_verifier_metadata_complete": True,
                    "best_answer_guarded_has_negative_or_doubtful_key_evidence": False,
                    "best_answer_guarded_recent_hypothesis_switch": False,
                    "best_answer_guarded_nonempty_alternative_candidates": False,
                    "best_answer_guarded_high_risk_respiratory_answer": True,
                    "best_answer_guarded_pcp_answer": True,
                    "best_answer_guarded_has_confirmed_key_evidence": True,
                    "best_answer_guarded_missing_evidence_families": ["immune_status"],
                    "best_answer_guarded_pcp_combo_satisfied": True,
                    "pending_action": {
                        "target_node_name": "CD4+ T淋巴细胞计数 < 200/μL",
                        "evidence_tags": ["immune_status", "type:lab"],
                    },
                    "a4_evidence_audit": {
                        "turn_index": 1,
                        "target_node_name": "CD4+ T淋巴细胞计数 < 200/μL",
                        "patient_answer": "有。",
                        "existence": "exist",
                        "certainty": "confident",
                        "evidence_tags": ["immune_status", "type:lab"],
                        "evidence_families": ["immune_status"],
                        "confirmed_family_candidate": True,
                    },
                    "stop_reason": "final_answer_accepted",
                }
            ],
        },
        {
            "final_best_answer_name": "急性期",
            "true_conditions": ["HIV感染"],
            "true_disease_phase": "急性期",
            "final_stop_reason": "verifier_rejected_stop",
            "case_id": "correct_rejected_case",
            "turn_summaries": [
                {
                    "turn_index": 2,
                    "best_answer_name": "急性期",
                    "best_answer_verifier_mode": "llm_verifier",
                    "best_answer_verifier_called": True,
                    "best_answer_verifier_should_accept": False,
                    "best_answer_verifier_schema_valid": True,
                    "best_answer_verifier_reject_reason_source": "llm_schema",
                    "best_answer_verifier_metadata_complete": True,
                    "stop_reason": None,
                },
                {
                    "turn_index": 3,
                    "best_answer_name": "急性期",
                    "best_answer_verifier_mode": "llm_verifier",
                    "best_answer_verifier_called": True,
                    "best_answer_verifier_should_accept": False,
                    "best_answer_verifier_schema_valid": True,
                    "best_answer_verifier_reject_reason_source": "llm_schema",
                    "best_answer_verifier_metadata_complete": True,
                    "stop_reason": None,
                },
            ],
        },
        {
            "final_best_answer_name": "肺结核",
            "true_conditions": ["肺孢子菌肺炎 (PCP)"],
            "true_disease_phase": "AIDS期",
            "final_stop_reason": "verifier_rejected_stop",
            "case_id": "wrong_rejected_case",
            "turn_summaries": [],
        },
        {
            "final_best_answer_name": "肺结核",
            "true_conditions": ["肺孢子菌肺炎 (PCP)"],
            "true_disease_phase": "AIDS期",
            "final_stop_reason": "final_answer_accepted",
            "case_id": "wrong_accepted_case",
            "turn_summaries": [],
        },
    ]

    metrics = _summarize_focused_rows(rows, {"variant": "baseline"})

    assert metrics["accepted_correct_count"] == 1
    assert metrics["correct_best_answer_but_rejected_count"] == 1
    assert metrics["wrong_best_answer_rejected_count"] == 1
    assert metrics["accepted_wrong_count"] == 1
    assert metrics["first_correct_best_answer_turns"]["accepted_case"] == 1
    assert metrics["first_verifier_accept_turns"]["accepted_case"] == 1
    assert metrics["correct_but_rejected_spans"]["accepted_case"] == 0
    assert metrics["first_correct_best_answer_turns"]["correct_rejected_case"] == 2
    assert metrics["first_verifier_accept_turns"]["correct_rejected_case"] is None
    assert metrics["correct_but_rejected_spans"]["correct_rejected_case"] == 2
    assert metrics["verifier_called_count"] == 3
    assert metrics["accepted_with_verifier_metadata_count"] == 1
    assert metrics["accepted_without_verifier_metadata_count"] == 1
    assert metrics["accepted_on_turn1_count"] == 1
    assert metrics["wrong_accept_on_turn1_count"] == 0
    assert metrics["accept_reason_counts"]["key_support_sufficient"] == 1
    assert metrics["wrong_accept_reason_counts"].get("key_support_sufficient", 0) == 0
    assert metrics["median_first_verifier_accept_turn"] == 1.0
    assert metrics["first_verifier_accept_turn_for_final_answer"]["accepted_case"] == 1
    assert metrics["final_answer_changed_after_first_accept_count"] == 0
    assert metrics["accepted_after_negative_key_evidence_count"] == 0
    assert metrics["accepted_after_recent_hypothesis_switch_count"] == 0
    assert metrics["accepted_with_nonempty_alternative_candidates_count"] == 0
    assert metrics["guarded_block_reason_counts"] == {}
    assert metrics["verifier_positive_but_gate_rejected_count"] == 0
    assert metrics["accept_candidate_without_confirmed_combo_count"] == 0
    assert metrics["guarded_gate_audit_records"] == []
    assert metrics["guarded_negative_evidence_node_counts"] == {}
    assert metrics["guarded_negative_evidence_family_counts"] == {}
    assert metrics["guarded_negative_evidence_tier_counts"] == {}
    assert metrics["guarded_negative_evidence_scope_counts"] == {}
    assert metrics["strong_alternative_block_count"] == 0
    assert metrics["weak_alternative_allowed_count"] == 0
    assert metrics["combo_satisfied_but_alternative_blocked_count"] == 0
    assert metrics["a4_evidence_audit_record_count"] == 1
    assert metrics["a4_confirmed_family_candidate_count"] == 1
    assert metrics["a4_provisional_family_candidate_count"] == 0
    assert metrics["provisional_family_used_count"] == 0
    assert metrics["provisional_combo_satisfied_count"] == 0
    assert metrics["accepted_with_provisional_combo_count"] == 0
    assert metrics["a4_evidence_audit_records"][0]["target_node_name"] == "CD4+ T淋巴细胞计数 < 200/μL"
    assert metrics["missing_family_first_selected_count"] == 0
    assert metrics["missing_family_repair_turn_count"] == 0
    assert metrics["combo_anchor_selected_before_turn3_count"] == 1
    assert metrics["family_recorded_after_question_count"] == 0
    assert metrics["family_recorded_after_question_attempt_count"] == 0


# 验证答案名既可匹配真实疾病，也可匹配真实疾病阶段。
def test_focused_replay_correctness_matches_condition_or_phase() -> None:
    assert _is_correct_best_answer(
        {
            "final_best_answer_name": "肺孢子菌肺炎",
            "true_conditions": ["肺孢子菌肺炎 (PCP)"],
            "true_disease_phase": "AIDS期",
        }
    )
    assert _is_correct_best_answer(
        {
            "final_best_answer_name": "急性期",
            "true_conditions": ["HIV感染"],
            "true_disease_phase": "急性期",
        }
    )


# 验证 accepted 路径也会进入 verifier schema/source 统计，而不是只统计 rejected repair turn。
def test_ablation_metrics_count_accepted_verifier_metadata() -> None:
    rows = [
        {
            "case_id": "accepted_case",
            "final_best_answer_name": "肺孢子菌肺炎 (PCP)",
            "true_conditions": ["肺孢子菌肺炎 (PCP)"],
            "true_disease_phase": "AIDS期",
            "final_stop_reason": "final_answer_accepted",
            "turn_summaries": [
                {
                    "turn_index": 1,
                    "best_answer_name": "肺孢子菌肺炎 (PCP)",
                    "best_answer_verifier_mode": "llm_verifier",
                    "best_answer_verifier_called": True,
                    "best_answer_verifier_should_accept": True,
                    "best_answer_verifier_schema_valid": True,
                    "best_answer_verifier_reject_reason_source": "llm_schema",
                    "best_answer_verifier_metadata_complete": True,
                    "stop_reason": "final_answer_accepted",
                    "semantic_repeat_as_previous": False,
                }
            ],
        }
    ]
    metrics = _augment_metrics(_summarize_focused_rows(rows, {"variant": "baseline"}), rows)

    assert metrics["verifier_schema_valid_counts"]["true"] == 1
    assert metrics["verifier_reject_reason_source_counts"]["llm_schema"] == 1


def test_focused_replay_counts_guarded_acceptance_risks() -> None:
    rows = [
        {
            "case_id": "wrong_guarded_accept",
            "final_best_answer_name": "肺孢子菌肺炎 (PCP)",
            "true_conditions": ["活动性结核病"],
            "true_disease_phase": "",
            "final_stop_reason": "final_answer_accepted",
            "turn_summaries": [
                {
                    "turn_index": 1,
                    "best_answer_name": "结核病",
                    "best_answer_verifier_should_accept": True,
                    "best_answer_verifier_accept_reason": "trajectory_stable",
                    "stop_reason": None,
                },
                {
                    "turn_index": 2,
                    "best_answer_name": "肺孢子菌肺炎 (PCP)",
                    "best_answer_verifier_mode": "llm_verifier",
                    "best_answer_verifier_called": True,
                    "best_answer_verifier_should_accept": True,
                    "best_answer_verifier_accept_reason": "key_support_sufficient",
                    "best_answer_verifier_alternative_candidates": [
                        {"answer_name": "活动性结核病", "reason": "强混淆"}
                    ],
                    "best_answer_guarded_has_negative_or_doubtful_key_evidence": True,
                    "best_answer_guarded_recent_hypothesis_switch": True,
                    "best_answer_guarded_nonempty_alternative_candidates": True,
                    "best_answer_guarded_has_strong_unresolved_alternative": True,
                    "best_answer_guarded_strong_alternative_candidates": [
                        {"answer_name": "活动性结核病", "reason": "强混淆", "strength": "strong"}
                    ],
                    "best_answer_guarded_weak_or_ruled_down_alternative_candidates": [],
                    "best_answer_guarded_high_risk_respiratory_answer": True,
                    "best_answer_guarded_pcp_answer": True,
                    "best_answer_guarded_has_confirmed_key_evidence": False,
                    "best_answer_guarded_missing_evidence_families": ["immune_status", "pathogen"],
                    "best_answer_guarded_pcp_combo_satisfied": False,
                    "best_answer_guarded_acceptance_block_reason": "pcp_combo_insufficient",
                    "pending_action": {
                        "target_node_name": "CD4+ T淋巴细胞计数 < 200/μL",
                        "evidence_tags": ["immune_status", "type:lab"],
                    },
                    "best_answer_guarded_hard_negative_key_evidence": [
                        {
                            "name": "胸部CT磨玻璃影",
                            "evidence_families": ["imaging"],
                            "evidence_scope": "answer_scoped",
                            "negative_evidence_tier": "hard",
                        }
                    ],
                    "best_answer_guarded_soft_negative_or_doubtful_key_evidence": [
                        {
                            "name": "呼吸衰竭",
                            "evidence_families": ["oxygenation"],
                            "evidence_scope": "shared_clinical",
                            "negative_evidence_tier": "soft",
                        }
                    ],
                    "best_answer_guarded_recent_key_evidence_states": [
                        {"name": "胸部CT磨玻璃影", "evidence_tags": ["imaging"]}
                    ],
                    "best_answer_verifier_metadata_complete": True,
                    "stop_reason": "final_answer_accepted",
                },
                {
                    "turn_index": 3,
                    "best_answer_name": "肺孢子菌肺炎 (PCP)",
                    "best_answer_guarded_confirmed_key_evidence_families": ["immune_status"],
                    "stop_reason": None,
                },
            ],
        }
    ]

    metrics = _summarize_focused_rows(rows, {"variant": "baseline"})

    assert metrics["accepted_wrong_count"] == 1
    assert metrics["wrong_accept_reason_counts"]["key_support_sufficient"] == 1
    assert metrics["first_verifier_accept_turn_for_final_answer"]["wrong_guarded_accept"] == 2
    assert metrics["final_answer_changed_after_first_accept_count"] == 1
    assert metrics["accepted_after_negative_key_evidence_count"] == 1
    assert metrics["accepted_after_recent_hypothesis_switch_count"] == 1
    assert metrics["accepted_with_nonempty_alternative_candidates_count"] == 1
    assert metrics["guarded_block_reason_counts"]["pcp_combo_insufficient"] == 1
    assert metrics["verifier_positive_but_gate_rejected_count"] == 1
    assert metrics["accept_candidate_without_confirmed_combo_count"] == 1
    audit = metrics["guarded_gate_audit_records"][0]
    assert audit["case_id"] == "wrong_guarded_accept"
    assert audit["block_reason"] == "pcp_combo_insufficient"
    assert audit["missing_families"] == ["immune_status", "pathogen"]
    assert audit["recent_key_evidence_states"][0]["name"] == "胸部CT磨玻璃影"
    assert metrics["guarded_negative_evidence_node_counts"]["胸部CT磨玻璃影"] == 1
    assert metrics["guarded_negative_evidence_node_counts"]["呼吸衰竭"] == 1
    assert metrics["guarded_negative_evidence_family_counts"]["imaging"] == 1
    assert metrics["guarded_negative_evidence_family_counts"]["oxygenation"] == 1
    assert metrics["guarded_negative_evidence_tier_counts"]["hard"] == 1
    assert metrics["guarded_negative_evidence_tier_counts"]["soft"] == 1
    assert metrics["guarded_negative_evidence_scope_counts"]["answer_scoped"] == 1
    assert metrics["guarded_negative_evidence_scope_counts"]["shared_clinical"] == 1
    assert metrics["missing_family_first_selected_count"] == 1
    assert metrics["missing_family_repair_turn_count"] == 1
    assert metrics["combo_anchor_selected_before_turn3_count"] == 1
    assert metrics["family_recorded_after_question_count"] == 1
    assert metrics["family_recorded_after_question_attempt_count"] == 1
