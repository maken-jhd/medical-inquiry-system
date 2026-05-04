"""测试 verifier 拒停后的 repair action 分流。"""

from brain.action_builder import ActionBuilder
from brain.hypothesis_manager import HypothesisManager
from brain.mcts_engine import MctsEngine
from brain.service import BrainDependencies, ConsultationBrain, RepairPolicyConfig
from brain.state_tracker import StateTracker
from brain.types import HypothesisScore, MctsAction, PendingActionResult, SearchResult, SessionState


class RepairFakeRetriever:
    """提供固定 R2 候选，验证 repair action 的显式选择。"""

    client = object()

    def __init__(self, rows: list[dict] | dict[str, list[dict]]) -> None:
        self.rows = rows

    def retrieve_r2_expected_evidence(self, hypothesis: HypothesisScore, session_state: SessionState, top_k: int | None = None) -> list[dict]:
        _ = session_state, top_k

        if isinstance(self.rows, dict):
            return list(self.rows.get(hypothesis.node_id, []))

        return list(self.rows)


def _build_brain(
    rows: list[dict] | dict[str, list[dict]],
    state_tracker: StateTracker,
    repair_policy: RepairPolicyConfig | None = None,
    mcts_engine: MctsEngine | None = None,
) -> ConsultationBrain:
    return ConsultationBrain(
        BrainDependencies(
            state_tracker=state_tracker,
            retriever=RepairFakeRetriever(rows),
            med_extractor=object(),
            entity_linker=object(),
            question_selector=object(),
            acceptance_controller=object(),
            report_builder=object(),
            evidence_parser=object(),
            hypothesis_manager=HypothesisManager(),
            action_builder=ActionBuilder(),
            router=object(),
            mcts_engine=mcts_engine or object(),
            simulation_engine=object(),
            trajectory_evaluator=object(),
            llm_client=object(),
            repair_policy=repair_policy or RepairPolicyConfig(),
        )
    )


# 验证 missing_key_support 会优先选择 verifier 推荐的补强证据。
def test_service_prefers_recommended_repair_action_for_missing_support() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s1")
    state.candidate_hypotheses = [
        HypothesisScore(
            node_id="d1",
            label="Disease",
            name="肺孢子菌肺炎 (PCP)",
            score=1.0,
            metadata={"recommended_next_evidence": ["低氧血症"]},
        )
    ]
    rows = [
        {
            "node_id": "symptom_lymph",
            "label": "ClinicalFinding",
            "name": "淋巴结肿大",
            "relation_type": "MANIFESTS_AS",
            "relation_weight": 1.0,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
            "contradiction_priority": 0.85,
            "question_type_hint": "symptom",
            "priority": 3.2,
            "is_red_flag": False,
            "topic_id": "Disease",
        },
        {
            "node_id": "lab_oxy",
            "label": "LabFinding",
            "name": "低氧血症",
            "relation_type": "HAS_LAB_FINDING",
            "relation_weight": 0.8,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
            "contradiction_priority": 1.0,
            "question_type_hint": "lab",
            "priority": 2.7,
            "is_red_flag": True,
            "topic_id": "Disease",
        },
    ]
    brain = _build_brain(rows, tracker)

    action = brain._choose_repair_action(
        "s1",
        SearchResult(),
        {"reject_reason": "missing_key_support", "recommended_next_evidence": ["低氧血症"]},
    )

    assert action is not None
    assert action.action_type == "collect_general_exam_context"
    assert action.metadata["exam_kind"] == "general"
    assert "lab" in action.metadata["candidate_exam_kinds"]
    assert any(item["name"] == "低氧血症" for item in action.metadata["exam_candidate_evidence"])


# 验证 missing_key_support 下 verifier 推荐证据是强引导，能压过更高先验的泛化症状。
def test_service_missing_support_treats_recommended_evidence_as_hard_guide() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s1_hard_guide")
    state.candidate_hypotheses = [
        HypothesisScore(
            node_id="pcp",
            label="Disease",
            name="肺孢子菌肺炎 (PCP)",
            score=1.0,
            metadata={},
        )
    ]
    rows = [
        {
            "node_id": "symptom_fever",
            "label": "ClinicalFinding",
            "name": "发热",
            "relation_type": "MANIFESTS_AS",
            "relation_weight": 1.0,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
            "contradiction_priority": 0.9,
            "question_type_hint": "symptom",
            "priority": 6.2,
            "is_red_flag": False,
            "topic_id": "Disease",
        },
        {
            "node_id": "ct_ggo",
            "label": "ImagingFinding",
            "name": "胸部CT磨玻璃影",
            "relation_type": "HAS_IMAGING_FINDING",
            "relation_weight": 0.75,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
            "contradiction_priority": 0.92,
            "question_type_hint": "imaging",
            "priority": 1.2,
            "is_red_flag": False,
            "topic_id": "Disease",
        },
    ]
    brain = _build_brain(rows, tracker)

    action = brain._choose_repair_action(
        "s1_hard_guide",
        SearchResult(),
        {
            "reject_reason": "missing_key_support",
            "recommended_next_evidence": ["获取胸部CT结果以确认磨玻璃影"],
        },
    )

    assert action is not None
    assert action.action_type == "collect_general_exam_context"
    assert any(item["name"] == "胸部CT磨玻璃影" for item in action.metadata["exam_candidate_evidence"])


# 验证 trajectory_insufficient 会倾向切换到不同 question type，避免围绕同类问题打转。
def test_service_prefers_diversified_question_type_for_trajectory_insufficient() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s2")
    state.candidate_hypotheses = [
        HypothesisScore(
            node_id="phase_acute",
            label="Disease",
            name="急性期",
            score=1.0,
            metadata={},
        )
    ]
    state.metadata["last_answered_action"] = MctsAction(
        action_id="verify::phase_acute::lab_cd4",
        action_type="verify_evidence",
        target_node_id="lab_cd4",
        target_node_label="LabFinding",
        target_node_name="CD4+ T淋巴细胞计数 < 200/μL",
        metadata={"question_type_hint": "lab"},
    )
    rows = [
        {
            "node_id": "lab_viral_load",
            "label": "LabFinding",
            "name": "HIV RNA 病毒载量升高",
            "relation_type": "HAS_LAB_FINDING",
            "relation_weight": 0.7,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
            "contradiction_priority": 0.9,
            "question_type_hint": "lab",
            "priority": 2.2,
            "is_red_flag": False,
            "topic_id": "Disease",
        },
        {
            "node_id": "symptom_rash",
            "label": "ClinicalFinding",
            "name": "皮疹",
            "relation_type": "MANIFESTS_AS",
            "relation_weight": 0.9,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
            "contradiction_priority": 0.8,
            "question_type_hint": "symptom",
            "priority": 2.15,
            "is_red_flag": False,
            "topic_id": "Disease",
        },
    ]
    brain = _build_brain(rows, tracker)

    action = brain._choose_repair_action(
        "s2",
        SearchResult(),
        {"reject_reason": "trajectory_insufficient", "recommended_next_evidence": []},
    )

    assert action is not None
    assert action.target_node_name == "皮疹"


# 验证 missing_key_support 在连续实验室追问后会优先切向推荐的不同证据类型。
def test_service_missing_support_prefers_recommended_evidence_family() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s3")
    state.candidate_hypotheses = [
        HypothesisScore(
            node_id="pcp",
            label="Disease",
            name="肺孢子菌肺炎 (PCP)",
            score=1.0,
            metadata={},
        )
    ]
    state.metadata["last_answered_action"] = MctsAction(
        action_id="verify::pcp::lab_cd4",
        action_type="verify_evidence",
        target_node_id="lab_cd4",
        target_node_label="LabFinding",
        target_node_name="CD4+ T淋巴细胞计数 < 200/μL",
        metadata={
            "question_type_hint": "lab",
            "evidence_tags": ["immune_status", "type:lab"],
        },
    )
    rows = [
        {
            "node_id": "lab_pao2",
            "label": "LabFinding",
            "name": "动脉血氧分压 (PaO2) < 70 mmHg",
            "relation_type": "HAS_LAB_FINDING",
            "relation_weight": 0.95,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
            "contradiction_priority": 1.0,
            "question_type_hint": "lab",
            "priority": 3.25,
            "is_red_flag": True,
            "topic_id": "Disease",
        },
        {
            "node_id": "sign_ct",
            "label": "ClinicalFinding",
            "name": "胸部CT磨玻璃影",
            "relation_type": "DIAGNOSED_BY",
            "relation_weight": 0.9,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
            "contradiction_priority": 0.92,
            "question_type_hint": "symptom",
            "priority": 2.95,
            "is_red_flag": True,
            "topic_id": "Disease",
        },
    ]
    brain = _build_brain(rows, tracker)

    action = brain._choose_repair_action(
        "s3",
        SearchResult(),
        {
            "reject_reason": "missing_key_support",
            "recommended_next_evidence": ["获取胸部CT结果", "询问免疫状态及基础疾病"],
        },
    )

    assert action is not None
    assert action.target_node_name == "胸部CT磨玻璃影"


def test_service_missing_anchor_repair_prefers_specific_role_over_background_path() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s_combo")
    state.candidate_hypotheses = [
        HypothesisScore(node_id="pneumonia", label="Disease", name="原发性肺部感染", score=1.1),
        HypothesisScore(node_id="pcp", label="Disease", name="肺孢子菌肺炎 (PCP)", score=0.95),
    ]
    rows = {
        "pneumonia": [
            {
                "node_id": "low_oxygen",
                "label": "ClinicalFinding",
                "name": "低氧血症",
                "relation_type": "MANIFESTS_AS",
                "relation_weight": 0.95,
                "node_weight": 1.0,
                "similarity_confidence": 1.0,
                "contradiction_priority": 1.0,
                "question_type_hint": "symptom",
                "priority": 4.4,
                "is_red_flag": True,
                "topic_id": "Disease",
            }
        ],
        "pcp": [
            {
                "node_id": "pcp_pcr",
                "label": "LabFinding",
                "name": "诱导痰肺孢子菌 PCR 阳性",
                "relation_type": "DIAGNOSED_BY",
                "relation_weight": 0.95,
                "node_weight": 1.0,
                "similarity_confidence": 1.0,
                "contradiction_priority": 0.98,
                "question_type_hint": "lab",
                "priority": 2.0,
                "is_red_flag": False,
                "topic_id": "Disease",
            },
            {
                "node_id": "dry_cough",
                "label": "ClinicalFinding",
                "name": "干咳",
                "relation_type": "MANIFESTS_AS",
                "relation_weight": 0.9,
                "node_weight": 1.0,
                "similarity_confidence": 1.0,
                "contradiction_priority": 0.75,
                "question_type_hint": "symptom",
                "priority": 3.5,
                "is_red_flag": False,
                "topic_id": "Disease",
            },
        ],
    }
    brain = _build_brain(rows, tracker)

    action = brain._choose_repair_action(
        "s_combo",
        SearchResult(best_answer_id="pcp"),
        {
            "reject_reason": "missing_required_anchor",
            "current_answer_id": "pcp",
            "current_answer_name": "肺孢子菌肺炎 (PCP)",
            "missing_evidence_roles": ["disease_specific_anchor"],
            "recommended_next_evidence": ["肺孢子菌 PCR", "病原学证据"],
        },
    )

    assert action is not None
    assert action.hypothesis_id == "pcp"
    assert action.action_type == "collect_general_exam_context"
    assert any(item["name"] == "诱导痰肺孢子菌 PCR 阳性" for item in action.metadata["exam_candidate_evidence"])


def test_service_missing_confirmed_evidence_prefers_missing_family_before_root_symptoms() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s_missing_family")
    state.candidate_hypotheses = [
        HypothesisScore(node_id="pcp", label="Disease", name="肺孢子菌肺炎 (PCP)", score=1.0),
    ]
    rows = [
        {
            "node_id": "fever",
            "label": "ClinicalFinding",
            "name": "发热",
            "relation_type": "MANIFESTS_AS",
            "relation_weight": 0.95,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
            "contradiction_priority": 0.85,
            "question_type_hint": "symptom",
            "priority": 4.2,
            "is_red_flag": False,
            "topic_id": "Disease",
        },
        {
            "node_id": "bdg",
            "label": "LabFinding",
            "name": "血清 β-D 葡聚糖升高",
            "relation_type": "HAS_LAB_FINDING",
            "relation_weight": 0.75,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
            "contradiction_priority": 0.92,
            "question_type_hint": "lab",
            "priority": 2.1,
            "is_red_flag": False,
            "topic_id": "Disease",
        },
    ]
    brain = _build_brain(rows, tracker)

    action = brain._choose_repair_action(
        "s_missing_family",
        SearchResult(best_answer_id="pcp"),
        {
            "reject_reason": "missing_key_support",
            "current_answer_id": "pcp",
            "missing_evidence_roles": ["disease_specific_anchor"],
            "recommended_next_evidence": ["β-D 葡聚糖", "PCP PCR"],
        },
    )

    assert action is not None
    assert action.action_type == "collect_general_exam_context"
    assert any(item["name"] == "血清 β-D 葡聚糖升高" for item in action.metadata["exam_candidate_evidence"])


# 验证 strong_alternative_not_ruled_out 会把强备选 hypothesis 的动作纳入 repair 候选池。
def test_service_can_switch_to_alternative_hypothesis_action_when_verifier_requests_it() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s4")
    state.candidate_hypotheses = [
        HypothesisScore(
            node_id="pcp",
            label="Disease",
            name="肺孢子菌肺炎 (PCP)",
            score=1.0,
            metadata={"evidence_names": ["低氧血症"]},
        ),
        HypothesisScore(
            node_id="covid",
            label="Disease",
            name="新型冠状病毒感染",
            score=0.95,
            metadata={"evidence_names": ["核酸阳性"]},
        ),
    ]
    rows = {
        "pcp": [
            {
                "node_id": "lab_ldh",
                "label": "LabFinding",
                "name": "乳酸脱氢酶升高",
                "relation_type": "HAS_LAB_FINDING",
                "relation_weight": 0.8,
                "node_weight": 1.0,
                "similarity_confidence": 1.0,
                "contradiction_priority": 0.85,
                "question_type_hint": "lab",
                "priority": 2.8,
                "is_red_flag": False,
                "topic_id": "Disease",
            }
        ],
        "covid": [
            {
                "node_id": "lab_pcr",
                "label": "LabFinding",
                "name": "新冠核酸阳性",
                "relation_type": "DIAGNOSED_BY",
                "relation_weight": 0.95,
                "node_weight": 1.0,
                "similarity_confidence": 1.0,
                "contradiction_priority": 0.98,
                "question_type_hint": "lab",
                "priority": 2.75,
                "is_red_flag": False,
                "topic_id": "Disease",
            }
        ],
    }
    brain = _build_brain(rows, tracker)

    action = brain._choose_repair_action(
        "s4",
        SearchResult(),
        {
            "reject_reason": "strong_alternative_not_ruled_out",
            "current_answer_id": "pcp",
            "alternative_candidates": [{"answer_id": "covid", "answer_name": "新型冠状病毒感染"}],
            "recommended_next_evidence": ["新冠核酸检测"],
        },
    )

    assert action is not None
    assert action.hypothesis_id == "covid"
    assert action.action_type == "collect_general_exam_context"
    assert any(item["name"] == "新冠核酸阳性" for item in action.metadata["exam_candidate_evidence"])


# 验证 verifier 指出真实强锚点属于备选诊断时，repair 直接围绕 anchored alternative 取动作。
def test_service_verifier_repair_switches_to_anchored_alternative() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s_anchor_alt")
    state.candidate_hypotheses = [
        HypothesisScore(node_id="tb", label="Disease", name="活动性结核病", score=1.4),
        HypothesisScore(
            node_id="vzv",
            label="Disease",
            name="水痘-带状疱疹病毒感染",
            score=1.1,
            metadata={"anchor_tier": "strong_anchor", "observed_anchor_score": 0.7},
        ),
    ]
    rows = {
        "tb": [
            {
                "node_id": "lab_mtb",
                "label": "LabFinding",
                "name": "MTB培养阳性",
                "relation_type": "DIAGNOSED_BY",
                "relation_weight": 0.9,
                "node_weight": 1.0,
                "similarity_confidence": 1.0,
                "contradiction_priority": 0.98,
                "question_type_hint": "lab",
                "priority": 3.0,
                "is_red_flag": False,
                "topic_id": "Disease",
            }
        ],
        "vzv": [
            {
                "node_id": "path_vzv",
                "label": "Pathogen",
                "name": "水痘-带状疱疹病毒",
                "relation_type": "HAS_PATHOGEN",
                "relation_weight": 0.95,
                "node_weight": 1.0,
                "similarity_confidence": 1.0,
                "contradiction_priority": 1.0,
                "question_type_hint": "pathogen",
                "priority": 2.0,
                "is_red_flag": False,
                "topic_id": "Disease",
            }
        ],
    }
    brain = _build_brain(rows, tracker)

    action = brain._choose_repair_action(
        "s_anchor_alt",
        SearchResult(best_answer_id="tb"),
        {
            "reject_reason": "anchored_alternative_exists",
            "current_answer_id": "tb",
            "alternative_candidates": [
                {
                    "answer_id": "vzv",
                    "answer_name": "水痘-带状疱疹病毒感染",
                    "strength": "strong",
                    "reason": "真实会话中已有 VZV strong anchor。",
                }
            ],
        },
    )

    assert action is not None
    assert action.hypothesis_id == "vzv"
    assert any(item["name"] == "水痘-带状疱疹病毒" for item in action.metadata["exam_candidate_evidence"])


# 验证 strong_unresolved_alternative_candidates 保留细粒度原因后，仍走竞争诊断动作池。
def test_service_strong_unresolved_alternative_reason_uses_alternative_pool() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s4_alt_reason")
    state.candidate_hypotheses = [
        HypothesisScore(node_id="pcp", label="Disease", name="肺孢子菌肺炎 (PCP)", score=1.0),
        HypothesisScore(node_id="tb", label="Disease", name="肺结核", score=0.94),
    ]
    rows = {
        "pcp": [
            {
                "node_id": "lab_ldh",
                "label": "LabFinding",
                "name": "乳酸脱氢酶升高",
                "relation_type": "HAS_LAB_FINDING",
                "relation_weight": 0.8,
                "node_weight": 1.0,
                "similarity_confidence": 1.0,
                "contradiction_priority": 0.75,
                "question_type_hint": "lab",
                "priority": 2.8,
                "is_red_flag": False,
                "topic_id": "Disease",
            }
        ],
        "tb": [
            {
                "node_id": "symptom_night_sweat",
                "label": "ClinicalFinding",
                "name": "盗汗",
                "relation_type": "MANIFESTS_AS",
                "relation_weight": 0.8,
                "node_weight": 1.0,
                "similarity_confidence": 1.0,
                "contradiction_priority": 0.98,
                "question_type_hint": "symptom",
                "priority": 2.7,
                "is_red_flag": False,
                "topic_id": "Disease",
            }
        ],
    }
    brain = _build_brain(rows, tracker)

    action = brain._choose_repair_action(
        "s4_alt_reason",
        SearchResult(),
        {
            "reject_reason": "strong_unresolved_alternative_candidates",
            "current_answer_id": "pcp",
            "alternative_candidates": [{"answer_id": "tb", "answer_name": "肺结核"}],
            "recommended_next_evidence": ["盗汗"],
        },
    )

    assert action is not None
    assert action.hypothesis_id == "tb"
    assert action.target_node_name == "盗汗"


# 验证 hard_negative_key_evidence 不再被当成竞争诊断未排除，而是先围绕当前答案修复硬反证。
def test_service_hard_negative_repair_stays_on_current_answer_and_follows_recommendation() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s_hard_negative")
    state.candidate_hypotheses = [
        HypothesisScore(node_id="ks", label="Disease", name="卡波西肉瘤", score=1.2),
        HypothesisScore(node_id="toxo", label="Disease", name="弓形虫脑炎", score=0.9),
    ]
    rows = {
        "ks": [
            {
                "node_id": "symptom_diarrhea",
                "label": "ClinicalFinding",
                "name": "腹泻",
                "relation_type": "MANIFESTS_AS",
                "relation_weight": 0.9,
                "node_weight": 1.0,
                "similarity_confidence": 1.0,
                "contradiction_priority": 0.9,
                "question_type_hint": "symptom",
                "priority": 5.6,
                "is_red_flag": False,
                "topic_id": "Disease",
            }
        ],
        "toxo": [
            {
                "node_id": "mri_ring",
                "label": "ImagingFinding",
                "name": "头颅MRI环状增强病灶",
                "relation_type": "DIAGNOSED_BY",
                "relation_weight": 0.95,
                "node_weight": 1.0,
                "similarity_confidence": 1.0,
                "contradiction_priority": 0.95,
                "question_type_hint": "imaging",
                "priority": 1.5,
                "is_red_flag": False,
                "topic_id": "Disease",
            }
        ],
    }
    brain = _build_brain(rows, tracker)

    action = brain._choose_repair_action(
        "s_hard_negative",
        SearchResult(best_answer_id="toxo"),
        {
            "reject_reason": "hard_negative_key_evidence",
            "current_answer_id": "toxo",
            "current_answer_name": "弓形虫脑炎",
            "recommended_next_evidence": ["获取头颅MRI正式报告以确认环状增强病灶"],
        },
    )

    assert action is not None
    assert action.hypothesis_id == "toxo"
    assert action.action_type == "collect_general_exam_context"
    assert any(item["name"] == "头颅MRI环状增强病灶" for item in action.metadata["exam_candidate_evidence"])


# 验证 strong_alternative_not_ruled_out 会更敢于切到非当前 top1 的鉴别证据。
def test_service_strong_alternative_prioritizes_non_current_discriminative_action() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s7")
    state.candidate_hypotheses = [
        HypothesisScore(node_id="pcp", label="Disease", name="肺孢子菌肺炎 (PCP)", score=1.0),
        HypothesisScore(node_id="tb", label="Disease", name="肺结核", score=0.92),
    ]
    rows = {
        "pcp": [
            {
                "node_id": "lab_ldh",
                "label": "LabFinding",
                "name": "乳酸脱氢酶升高",
                "relation_type": "HAS_LAB_FINDING",
                "relation_weight": 0.9,
                "node_weight": 1.0,
                "similarity_confidence": 1.0,
                "contradiction_priority": 0.75,
                "question_type_hint": "lab",
                "priority": 3.4,
                "is_red_flag": False,
                "topic_id": "Disease",
            }
        ],
        "tb": [
            {
                "node_id": "symptom_night_sweat",
                "label": "ClinicalFinding",
                "name": "盗汗",
                "relation_type": "MANIFESTS_AS",
                "relation_weight": 0.75,
                "node_weight": 1.0,
                "similarity_confidence": 1.0,
                "contradiction_priority": 0.98,
                "question_type_hint": "symptom",
                "priority": 2.85,
                "is_red_flag": False,
                "topic_id": "Disease",
            }
        ],
    }
    brain = _build_brain(rows, tracker)

    action = brain._choose_repair_action(
        "s7",
        SearchResult(),
        {
            "reject_reason": "strong_alternative_not_ruled_out",
            "current_answer_id": "pcp",
            "alternative_candidates": [{"answer_id": "tb", "answer_name": "肺结核"}],
            "recommended_next_evidence": ["盗汗", "痰抗酸染色"],
        },
    )

    assert action is not None
    assert action.hypothesis_id == "tb"
    assert action.target_node_name == "盗汗"


# 验证 trajectory_insufficient 会惩罚同一 evidence family 的相似追问，而不是只看节点是否不同。
def test_service_trajectory_insufficient_penalizes_same_evidence_family() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s8")
    state.candidate_hypotheses = [
        HypothesisScore(node_id="pcp", label="Disease", name="肺孢子菌肺炎 (PCP)", score=1.0),
    ]
    state.metadata["last_answered_action"] = MctsAction(
        action_id="verify::pcp::lab_pao2",
        action_type="verify_evidence",
        target_node_id="lab_pao2",
        target_node_label="LabFinding",
        target_node_name="动脉血氧分压下降",
        metadata={"question_type_hint": "lab", "evidence_tags": ["oxygenation", "type:lab"]},
    )
    rows = [
        {
            "node_id": "symptom_low_oxygen",
            "label": "ClinicalFinding",
            "name": "血氧饱和度下降",
            "relation_type": "MANIFESTS_AS",
            "relation_weight": 0.75,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
            "contradiction_priority": 0.9,
            "question_type_hint": "symptom",
            "priority": 2.75,
            "is_red_flag": False,
            "topic_id": "Disease",
        },
        {
            "node_id": "sign_ct",
            "label": "ClinicalFinding",
            "name": "胸部CT磨玻璃影",
            "relation_type": "DIAGNOSED_BY",
            "relation_weight": 0.8,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
            "contradiction_priority": 0.86,
            "question_type_hint": "detail",
            "priority": 2.55,
            "is_red_flag": False,
            "topic_id": "Disease",
        },
    ]
    brain = _build_brain(rows, tracker)

    action = brain._choose_repair_action(
        "s8",
        SearchResult(),
        {"reject_reason": "trajectory_insufficient", "recommended_next_evidence": []},
    )

    assert action is not None
    assert action.target_node_name == "胸部CT磨玻璃影"


# 验证检查/测量类 no-result 回答会被后处理为 unclear，而不是 hard negative。
def test_service_normalizes_no_result_for_measurement_to_unclear() -> None:
    tracker = StateTracker()
    tracker.create_session("s_no_result")
    brain = _build_brain([], tracker)
    action = MctsAction(
        action_id="verify::obesity::bmi",
        action_type="verify_evidence",
        target_node_id="bmi_high",
        target_node_label="ClinicalAttribute",
        target_node_name="BMI >= 30",
        metadata={"question_type_hint": "detail", "evidence_cost": "low"},
    )
    result = PendingActionResult(
        action_type="verify_evidence",
        target_node_id="bmi_high",
        target_node_name="BMI >= 30",
        polarity="absent",
        resolution="clear",
        negation_span="没做过这个测量",
    )

    normalized = brain._normalize_no_result_pending_action_result(action, "没做过这个测量。", result)

    assert normalized.polarity == "unclear"
    assert normalized.resolution == "hedged"
    assert normalized.metadata["no_result_normalized_to_unclear"] is True


# 验证真正的阴性/未检出结果仍然保留为 absent。
def test_service_preserves_explicit_negative_result_for_exam() -> None:
    tracker = StateTracker()
    tracker.create_session("s_negative_result")
    brain = _build_brain([], tracker)
    action = MctsAction(
        action_id="verify::hiv::rna",
        action_type="verify_evidence",
        target_node_id="hiv_rna",
        target_node_label="LabFinding",
        target_node_name="HIV RNA阳性",
        metadata={"question_type_hint": "lab", "acquisition_mode": "needs_lab_test", "evidence_cost": "high"},
    )
    result = PendingActionResult(
        action_type="verify_evidence",
        target_node_id="hiv_rna",
        target_node_name="HIV RNA阳性",
        polarity="absent",
        resolution="clear",
        negation_span="HIV RNA 未检出",
    )

    normalized = brain._normalize_no_result_pending_action_result(action, "HIV RNA 未检出。", result)

    assert normalized.polarity == "absent"
    assert "no_result_normalized_to_unclear" not in normalized.metadata


# 验证关闭 verifier-driven reshuffle 后，repair 只记录上下文，不应改写 hypothesis 分数。
def test_service_can_disable_verifier_hypothesis_reshuffle() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s5")
    state.candidate_hypotheses = [
        HypothesisScore(node_id="top1", label="Disease", name="主假设", score=1.0),
        HypothesisScore(node_id="alt1", label="Disease", name="强备选", score=0.95),
    ]
    brain = _build_brain(
        [],
        tracker,
        repair_policy=RepairPolicyConfig(enable_verifier_hypothesis_reshuffle=False),
    )

    brain._apply_verifier_repair_strategy(
        "s5",
        {
            "reject_reason": "strong_alternative_not_ruled_out",
            "current_answer_id": "top1",
            "alternative_candidates": [{"answer_id": "alt1", "answer_name": "强备选"}],
            "recommended_next_evidence": ["核酸阳性"],
            "force_tree_refresh": True,
        },
    )

    updated = tracker.get_session("s5").candidate_hypotheses
    assert [round(item.score, 4) for item in updated] == [1.0, 0.95]
    assert tracker.get_session("s5").metadata["verifier_repair_context"]["reject_reason"] == "strong_alternative_not_ruled_out"


# 验证关闭 reroot 后，即使状态签名变化也继续复用旧 root。
def test_service_can_disable_tree_reroot() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s6")
    state.candidate_hypotheses = [
        HypothesisScore(node_id="pcp", label="Disease", name="肺孢子菌肺炎 (PCP)", score=1.0),
    ]
    brain = _build_brain(
        [],
        tracker,
        repair_policy=RepairPolicyConfig(enable_tree_reroot=False),
        mcts_engine=MctsEngine(),
    )

    initial_tree = brain._ensure_search_tree("s6", state)
    initial_root_id = initial_tree.root_id
    state.metadata["pending_action_id"] = "verify::pcp::cd4"

    reused_tree = brain._ensure_search_tree("s6", state)

    assert reused_tree.root_id == initial_root_id
    assert tracker.get_session("s6").metadata["last_tree_refresh"]["reason"] == "reroot_disabled"
