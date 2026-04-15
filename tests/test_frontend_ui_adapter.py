"""测试前端适配层对 A2 证据画像的中文展示字段整理。"""

from frontend.ui_adapter import normalize_backend_turn


def test_ui_adapter_preserves_a2_evidence_profile_counts_and_groups() -> None:
    turn = normalize_backend_turn(
        {
            "turn_index": 1,
            "a1": {},
            "a2": {
                "primary_hypothesis": {
                    "node_id": "pcp",
                    "name": "肺孢子菌肺炎 (PCP)",
                    "score": 0.82,
                },
                "alternatives": [],
            },
            "a2_evidence_profiles": [
                {
                    "candidate_id": "pcp",
                    "candidate_name": "肺孢子菌肺炎 (PCP)",
                    "is_primary": True,
                    "score": 0.82,
                    "matched_count": 1,
                    "negative_count": 1,
                    "unknown_count": 1,
                    "score_breakdown": "已有 1 条支持证据。",
                    "evidence_groups": {
                        "symptom": [
                            {
                                "name": "干咳",
                                "status": "matched",
                                "status_label": "已命中",
                                "question_type": "symptom",
                            }
                        ],
                        "lab": [
                            {
                                "name": "CD4+ T淋巴细胞计数 < 200/μL",
                                "status": "unknown",
                                "status_label": "待验证",
                                "question_type": "lab",
                            }
                        ],
                        "imaging": [
                            {
                                "name": "胸部CT磨玻璃影",
                                "status": "negative",
                                "status_label": "已否定",
                                "question_type": "imaging",
                            }
                        ],
                    },
                }
            ],
        }
    )

    candidate = turn["a2"]["candidates"][0]

    assert candidate["name"] == "肺孢子菌肺炎 (PCP)"
    assert candidate["matched_count"] == 1
    assert candidate["negative_count"] == 1
    assert candidate["unknown_count"] == 1
    assert candidate["evidence_groups"]["symptom"][0]["status_icon"] == "☑"
    assert candidate["evidence_groups"]["lab"][0]["status_icon"] == "☐"
    assert candidate["evidence_groups"]["imaging"][0]["status_icon"] == "✖"
