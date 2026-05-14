"""测试 Top-3 candidate coverage 分析脚本能正确切片并兼容缺字段。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


def _load_analysis_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "analyze_top3_candidate_coverage.py"
    spec = importlib.util.spec_from_file_location("analyze_top3_candidate_coverage", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _build_case_row(
    *,
    case_id: str,
    gold: str,
    top1_answer: str,
    candidates: list[str],
    accepted: bool,
    status: str,
    include_answer_group_scores: bool = True,
) -> dict:
    answer_group_scores = []
    if include_answer_group_scores:
        answer_group_scores = [
            {
                "answer_id": f"id::{top1_answer}",
                "answer_name": top1_answer,
                "consistency": 0.4,
                "diversity": 0.3,
                "agent_evaluation": 0.6,
                "final_score": 0.55,
            }
        ]

    return {
        "case_id": case_id,
        "case_title": f"{gold} 病例",
        "true_conditions": [gold],
        "true_disease_phase": None,
        "status": status,
        "analysis": {"accepted_final_answer": accepted},
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
                    "score": 1.0 - index * 0.05,
                }
                for index, name in enumerate(candidates)
            ],
            "answer_group_scores": answer_group_scores,
            "stop_reason": "final_answer_accepted" if accepted else "verifier_not_ready",
        },
        "turns": [
            {
                "turn_index": 1,
                "search_report": {
                    "selected_action": {
                        "action_id": f"verify::{case_id}",
                        "action_type": "verify_evidence",
                        "target_node_id": f"node::{case_id}",
                        "target_node_name": "目标证据",
                        "metadata": {"question_type_hint": "symptom"},
                    }
                },
            }
        ],
    }


def test_analyze_top3_candidate_coverage_builds_summary_and_slices(tmp_path: Path) -> None:
    module = _load_analysis_module()
    run_dir = tmp_path / "run"
    output_dir = tmp_path / "analysis"
    run_dir.mkdir(parents=True, exist_ok=True)

    rows = [
        _build_case_row(
            case_id="case_a",
            gold="播散型隐球菌病",
            top1_answer="隐球菌性脑膜炎",
            candidates=["隐球菌性脑膜炎", "肺隐球菌感染", "马尔尼菲篮状菌病", "播散型隐球菌病"],
            accepted=False,
            status="max_turn_reached",
        ),
        _build_case_row(
            case_id="case_b",
            gold="肺孢子菌肺炎",
            top1_answer="结核病",
            candidates=["结核病", "巨细胞病毒肺炎", "细菌性肺炎"],
            accepted=False,
            status="max_turn_reached",
            include_answer_group_scores=False,
        ),
        _build_case_row(
            case_id="case_c",
            gold="隐球菌性脑膜炎",
            top1_answer="结核性脑膜炎",
            candidates=["结核性脑膜炎", "隐球菌性脑膜炎", "新型冠状病毒感染"],
            accepted=False,
            status="max_turn_reached",
        ),
        _build_case_row(
            case_id="case_d",
            gold="马尔尼菲篮状菌病",
            top1_answer="马尔尼菲篮状菌病",
            candidates=["结核病", "新型冠状病毒感染", "隐球菌病", "马尔尼菲篮状菌病"],
            accepted=True,
            status="completed",
        ),
    ]

    with (run_dir / "replay_results.jsonl").open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False))
            file.write("\n")

    (run_dir / "benchmark_summary.json").write_text(
        json.dumps({"case_count": 4, "hypothesis_hit_count": 3, "top3_hypothesis_hit_count": 1}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = module.analyze_run(run_dir=run_dir, output_dir=output_dir)

    assert result["summary"]["case_count"] == 4
    assert result["summary"]["hypothesis_hit_but_top3_miss_count"] == 2
    assert result["summary"]["candidate_miss_count"] == 1
    assert result["summary"]["top3_hit_but_top1_miss_count"] == 1
    assert result["summary"]["top1_hit_but_top3_miss_if_any_count"] == 1
    assert (output_dir / "coverage_summary.json").exists()
    assert (output_dir / "hypothesis_hit_but_top3_miss.jsonl").exists()
    assert (output_dir / "candidate_miss.jsonl").exists()
    assert (output_dir / "top3_hit_but_top1_miss.jsonl").exists()
