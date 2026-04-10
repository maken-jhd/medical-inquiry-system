"""测试 verifier 拒停后的 repair action 分流。"""

from brain.action_builder import ActionBuilder
from brain.hypothesis_manager import HypothesisManager
from brain.mcts_engine import MctsEngine
from brain.service import BrainDependencies, ConsultationBrain, RepairPolicyConfig
from brain.state_tracker import StateTracker
from brain.types import HypothesisScore, MctsAction, SearchResult, SessionState


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
            stop_rule_engine=object(),
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
            "label": "Sign",
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
    assert action.target_node_name == "低氧血症"


# 验证 trajectory_insufficient 会倾向切换到不同 question type，避免围绕同类问题打转。
def test_service_prefers_diversified_question_type_for_trajectory_insufficient() -> None:
    tracker = StateTracker()
    state = tracker.create_session("s2")
    state.candidate_hypotheses = [
        HypothesisScore(
            node_id="phase_acute",
            label="DiseasePhase",
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
            "topic_id": "DiseasePhase",
        },
        {
            "node_id": "symptom_rash",
            "label": "Symptom",
            "name": "皮疹",
            "relation_type": "MANIFESTS_AS",
            "relation_weight": 0.9,
            "node_weight": 1.0,
            "similarity_confidence": 1.0,
            "contradiction_priority": 0.8,
            "question_type_hint": "symptom",
            "priority": 2.15,
            "is_red_flag": False,
            "topic_id": "DiseasePhase",
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
            "label": "Sign",
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
    assert action.target_node_name == "新冠核酸阳性"


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
                "label": "Symptom",
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
            "label": "Symptom",
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
            "label": "Sign",
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
