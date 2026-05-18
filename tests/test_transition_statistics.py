"""测试统计版 transition builder 能从最小 mock 数据构造条件频数表。"""

from __future__ import annotations

import json
from pathlib import Path

from brain.search import TransitionStatisticsBuilder, TransitionStatisticsConfig
from brain.search import TransitionStatistics


def _write_mock_statistics_sources(tmp_path: Path) -> tuple[Path, Path, Path]:
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
                            },
                            {
                                "evidence_id": "slot_bdg",
                                "evidence_group": "lab",
                                "families": ["fungal_marker", "general_lab"],
                            },
                        ],
                    },
                    {
                        "disease_id": "d2",
                        "disease_name": "结核病",
                        "evidence": [
                            {
                                "evidence_id": "slot_cough",
                                "evidence_group": "symptom",
                                "families": ["respiratory_symptom"],
                            },
                            {
                                "evidence_id": "slot_ct",
                                "evidence_group": "imaging",
                                "families": ["pulmonary_imaging", "imaging"],
                            },
                        ],
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    cases = [
        {
            "case_id": "case_pcp",
            "true_conditions": ["肺孢子菌肺炎"],
            "slot_truth_map": {
                "slot_cough": {
                    "node_id": "slot_cough",
                    "value": True,
                    "group": "symptom",
                    "node_label": "ClinicalFinding",
                },
                "slot_bdg": {
                    "node_id": "slot_bdg",
                    "value": True,
                    "group": "lab",
                    "node_label": "LabFinding",
                },
            },
            "metadata": {"disease_id": "d1"},
        },
        {
            "case_id": "case_tb",
            "true_conditions": ["结核病"],
            "slot_truth_map": {
                "slot_cough": {
                    "node_id": "slot_cough",
                    "value": False,
                    "group": "symptom",
                    "node_label": "ClinicalFinding",
                },
                "slot_ct": {
                    "node_id": "slot_ct",
                    "value": True,
                    "group": "imaging",
                    "node_label": "ImagingFinding",
                },
            },
            "metadata": {"disease_id": "d2"},
        },
    ]
    cases_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in cases),
        encoding="utf-8",
    )

    replay_results = [
        {
            "case_id": "case_pcp",
            "turns": [
                {
                    "question_node_id": "__exam_context__::lab",
                    "asked_action_group": "exam_context",
                    "asked_action_question_type_hint": "exam_context",
                    "asked_action_acquisition_mode": "needs_lab_test",
                    "asked_target_node_label": "ExamContext",
                    "answer_text": "做过化验，β-D 葡聚糖升高。",
                    "revealed_slot_id": "slot_bdg",
                    "revealed_slot_group": "lab",
                    "revealed_slot_label": "LabFinding",
                    "revealed_slot_positive": True,
                    "revealed_slot_families": ["fungal_marker"],
                }
            ],
        },
        {
            "case_id": "case_tb",
            "turns": [
                {
                    "question_node_id": "__exam_context__::lab",
                    "asked_action_group": "exam_context",
                    "asked_action_question_type_hint": "exam_context",
                    "asked_action_acquisition_mode": "needs_lab_test",
                    "asked_target_node_label": "ExamContext",
                    "answer_text": "这项化验还没做过。",
                    "revealed_slot_id": None,
                    "revealed_slot_group": "",
                    "revealed_slot_label": "",
                    "revealed_slot_positive": None,
                    "revealed_slot_families": [],
                }
            ],
        },
    ]
    replay_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in replay_results),
        encoding="utf-8",
    )
    return evidence_catalog_path, cases_path, replay_path


# 验证 builder 能从最小 mock graph cases / replay / evidence catalog 构造统计表。
def test_transition_statistics_builder_builds_counts_from_mock_sources(tmp_path: Path) -> None:
    evidence_catalog_path, cases_path, replay_path = _write_mock_statistics_sources(tmp_path)
    builder = TransitionStatisticsBuilder(
        TransitionStatisticsConfig(
            source_mode="explicit",
            graph_case_paths=(str(cases_path),),
            replay_result_paths=(str(replay_path),),
            evidence_catalog_paths=(str(evidence_catalog_path),),
            smoothing_alpha=0.2,
        )
    )

    statistics = builder.build()
    verify_distribution = statistics.query_verify_distribution(
        disease_id="d1",
        evidence_family="respiratory_symptom",
        question_type="symptom",
    )
    exam_distribution = statistics.query_exam_availability_distribution(
        disease_id="d2",
        exam_kind="lab",
    )

    assert statistics.case_disease_map["case_pcp"] == "d1"
    assert verify_distribution.total_count > 0.0
    assert round(sum(verify_distribution.probabilities.values()), 6) == 1.0
    assert verify_distribution.probabilities["present"] > verify_distribution.probabilities["absent"]
    assert exam_distribution.probabilities["not_done"] > exam_distribution.probabilities["done"]


# 验证缺少 disease 命中时仍会安全回退到 family / question / global，而不是报错或全零。
def test_transition_statistics_builder_supports_backoff_when_condition_is_missing(tmp_path: Path) -> None:
    evidence_catalog_path, cases_path, replay_path = _write_mock_statistics_sources(tmp_path)
    statistics = TransitionStatisticsBuilder(
        TransitionStatisticsConfig(
            source_mode="explicit",
            graph_case_paths=(str(cases_path),),
            replay_result_paths=(str(replay_path),),
            evidence_catalog_paths=(str(evidence_catalog_path),),
        )
    ).build()

    distribution = statistics.query_verify_distribution(
        disease_id="missing_disease",
        evidence_family="respiratory_symptom",
        question_type="symptom",
    )

    assert round(sum(distribution.probabilities.values()), 6) == 1.0
    assert distribution.backoff_level in {"family_question_type", "question_type", "global"}


# 验证 min_total_count 提高后，会从低样本 disease-level 继续 backoff，而不是直接吃掉 1~2 条样本。
def test_transition_statistics_respects_min_total_count_before_using_specific_bucket() -> None:
    statistics = TransitionStatistics(smoothing_alpha=0.05, min_total_count=3)
    for _ in range(2):
        statistics.record_verify_observation(
            disease_id="d1",
            evidence_family="respiratory_symptom",
            question_type="symptom",
            outcome="present",
        )
    for _ in range(2):
        statistics.record_verify_observation(
            disease_id="d2",
            evidence_family="respiratory_symptom",
            question_type="symptom",
            outcome="absent",
        )

    distribution = statistics.query_verify_distribution(
        disease_id="d1",
        evidence_family="respiratory_symptom",
        question_type="symptom",
    )

    assert distribution.backoff_level == "family_question_type"
    assert distribution.total_count == 4.0
