"""测试 greedy vs mcts 决策对比脚本能生成总体统计与切片文件。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


def _load_compare_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "compare_greedy_vs_mcts_actions.py"
    spec = importlib.util.spec_from_file_location("compare_greedy_vs_mcts_actions", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _build_case_payload(
    *,
    case_id: str,
    gold: str,
    top1_answer: str,
    candidate_hypotheses: list[str],
    root_question_type: str,
    root_target_name: str,
    root_target_id: str,
    prior_score: float,
    accepted: bool,
    status: str,
    top_answer_metadata: dict | None = None,
) -> dict:
    return {
        "case_id": case_id,
        "case_title": f"{gold} - 普通病例",
        "case_type": "ordinary",
        "case_qc_status": "eligible",
        "benchmark_qc_status": "eligible",
        "case_qc_reasons": [],
        "true_conditions": [gold],
        "true_disease_phase": None,
        "status": status,
        "error": {},
        "red_flags": [],
        "opening_text": "",
        "opening_revealed_slot_ids": [],
        "initial_output": {},
        "analysis": {
            "top1_hit": top1_answer == gold,
            "accepted_final_answer": accepted,
        },
        "timing": {"turn_count": 1},
        "final_report": {
            "best_final_answer": {
                "answer_id": f"id::{top1_answer}",
                "answer_name": top1_answer,
            },
            "candidate_hypotheses": [
                {
                    "node_id": f"id::{name}",
                    "label": "Disease",
                    "name": name,
                    "score": 3.0 - index * 0.2,
                }
                for index, name in enumerate(candidate_hypotheses)
            ],
            "answer_group_scores": [
                {
                    "answer_id": f"id::{top1_answer}",
                    "answer_name": top1_answer,
                    "consistency": 0.5,
                    "diversity": 0.1,
                    "agent_evaluation": 0.7,
                    "final_score": 0.6,
                    "metadata": dict(top_answer_metadata or {}),
                }
            ],
            "search_metadata": {
                "root_action_mode": "mcts",
                "rollouts_executed": 4,
                "rollout_trajectory_count": 8,
                "answer_group_count": 2,
            },
            "stop_reason": "final_answer_accepted" if accepted else "verifier_not_ready",
        },
        "turns": [
            {
                "turn_index": 1,
                "search_metadata": {
                    "root_action_mode": "mcts",
                    "rollouts_executed": 4,
                    "rollout_trajectory_count": 8,
                    "answer_group_count": 2,
                },
                "search_report": {
                    "selected_action": {
                        "action_id": f"verify::{root_target_id}",
                        "action_type": "verify_evidence",
                        "target_node_id": root_target_id,
                        "target_node_name": root_target_name,
                        "target_node_label": "ClinicalFinding",
                        "prior_score": prior_score,
                        "hypothesis_id": "d1",
                        "topic_id": "Disease",
                        "metadata": {
                            "question_type_hint": root_question_type,
                            "evidence_cost": "high" if root_question_type == "lab" else "low",
                            "answerability_score": 0.2 if root_question_type == "lab" else 0.9,
                            "discriminative_gain": 0.9 if root_question_type == "lab" else 0.4,
                            "patient_burden": 0.75 if root_question_type == "lab" else 0.15,
                            "selected_action_source": "default_search_action",
                        },
                    },
                    "root_best_action": {
                        "action_id": f"verify::{root_target_id}",
                        "action_type": "verify_evidence",
                        "target_node_id": root_target_id,
                        "target_node_name": root_target_name,
                        "target_node_label": "ClinicalFinding",
                        "prior_score": prior_score,
                        "hypothesis_id": "d1",
                        "topic_id": "Disease",
                        "metadata": {
                            "question_type_hint": root_question_type,
                            "evidence_cost": "high" if root_question_type == "lab" else "low",
                            "answerability_score": 0.2 if root_question_type == "lab" else 0.9,
                            "discriminative_gain": 0.9 if root_question_type == "lab" else 0.4,
                            "patient_burden": 0.75 if root_question_type == "lab" else 0.15,
                            "selected_action_source": "default_search_action",
                        },
                    },
                    "search_metadata": {
                        "root_action_mode": "mcts",
                        "rollouts_executed": 4,
                        "rollout_trajectory_count": 8,
                        "answer_group_count": 2,
                    },
                    "final_answer_scores": [
                        {
                            "answer_id": f"id::{top1_answer}",
                            "answer_name": top1_answer,
                            "consistency": 0.5,
                            "diversity": 0.1,
                            "agent_evaluation": 0.7,
                            "final_score": 0.6,
                            "metadata": dict(top_answer_metadata or {}),
                        }
                    ],
                },
            }
        ],
    }


def _write_run_dir(path: Path, rows: list[dict], summary: dict) -> None:
    path.mkdir(parents=True, exist_ok=True)
    with (path / "replay_results.jsonl").open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False))
            file.write("\n")
    (path / "benchmark_summary.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")


def test_compare_greedy_vs_mcts_actions_builds_summary_and_slices(tmp_path: Path) -> None:
    module = _load_compare_module()
    mcts_dir = tmp_path / "mcts"
    greedy_dir = tmp_path / "greedy"
    output_dir = tmp_path / "compare"

    mcts_rows = [
        _build_case_payload(
            case_id="case_a",
            gold="肺孢子菌肺炎",
            top1_answer="结核病",
            candidate_hypotheses=["结核病", "急性呼吸衰竭", "真菌感染"],
            root_question_type="lab",
            root_target_name="痰抗酸染色",
            root_target_id="lab_a",
            prior_score=3.2,
            accepted=False,
            status="max_turn_reached",
            top_answer_metadata={
                "competitor_elimination_proxy": 0.24,
                "alternative_preservation_proxy": 0.12,
            },
        ),
        _build_case_payload(
            case_id="case_b",
            gold="隐球菌性脑膜炎",
            top1_answer="隐球菌性脑膜炎",
            candidate_hypotheses=["隐球菌性脑膜炎", "肺隐球菌感染", "新型冠状病毒感染"],
            root_question_type="symptom",
            root_target_name="头痛",
            root_target_id="symptom_b",
            prior_score=2.4,
            accepted=True,
            status="completed",
        ),
    ]
    greedy_rows = [
        _build_case_payload(
            case_id="case_a",
            gold="肺孢子菌肺炎",
            top1_answer="肺孢子菌肺炎",
            candidate_hypotheses=["肺孢子菌肺炎", "结核病", "真菌感染"],
            root_question_type="symptom",
            root_target_name="活动后气促",
            root_target_id="symptom_a",
            prior_score=2.8,
            accepted=True,
            status="completed",
        ),
        _build_case_payload(
            case_id="case_b",
            gold="隐球菌性脑膜炎",
            top1_answer="隐球菌性脑膜炎",
            candidate_hypotheses=["隐球菌性脑膜炎", "肺隐球菌感染", "新型冠状病毒感染"],
            root_question_type="symptom",
            root_target_name="头痛",
            root_target_id="symptom_b",
            prior_score=2.4,
            accepted=True,
            status="completed",
        ),
    ]
    summary = {
        "top1_final_answer_hit_rate": 0.5,
        "top3_hypothesis_hit_rate": 0.5,
        "completion_rate": 0.5,
        "accepted_exact_accuracy": 1.0,
        "wrong_accepted_count": 0,
    }
    _write_run_dir(mcts_dir, mcts_rows, summary)
    _write_run_dir(greedy_dir, greedy_rows, summary)

    result = module.compare_runs(
        mcts_dir=mcts_dir,
        greedy_dir=greedy_dir,
        output_dir=output_dir,
        mcts_label="mcts",
        greedy_label="no_tree_greedy",
    )

    assert result["summary"]["shared_case_count"] == 2
    assert result["summary"]["root_action_disagree_count"] == 1
    assert result["summary"]["greedy_correct_mcts_wrong_count"] == 1
    assert result["summary"]["reason_label_counts"]["top3_coverage_loss"] == 1
    assert (output_dir / "comparison_summary.json").exists()
    assert (output_dir / "pairwise_case_diff.jsonl").exists()
    assert (output_dir / "greedy_correct_mcts_wrong.jsonl").exists()
