"""测试 repair-aware A3 动作构造 metadata。"""

from brain.action_builder import ActionBuilder
from brain.types import ExamContextState, HypothesisScore, MctsAction, SessionState


# 验证 A3 动作会显式区分 verifier 推荐证据、原 hypothesis 推荐证据和共同命中信号。
def test_action_builder_tracks_joint_recommended_evidence_match() -> None:
    builder = ActionBuilder()
    hypothesis = HypothesisScore(
        node_id="pcp",
        label="Disease",
        name="肺孢子菌肺炎 (PCP)",
        score=1.0,
        metadata={
            "recommended_next_evidence": ["获取胸部CT结果", "询问免疫状态"],
            "hypothesis_recommended_next_evidence": ["胸部CT结果"],
            "verifier_recommended_next_evidence": ["胸部CT或X线结果"],
        },
    )
    actions = builder.build_verification_actions(
        [
            {
                "node_id": "sign_ct",
                "label": "Sign",
                "name": "胸部CT磨玻璃影",
                "relation_type": "DIAGNOSED_BY",
                "relation_weight": 0.85,
                "node_weight": 1.0,
                "similarity_confidence": 1.0,
                "contradiction_priority": 0.9,
                "question_type_hint": "detail",
                "priority": 2.5,
                "is_red_flag": False,
                "topic_id": "Disease",
            }
        ],
        hypothesis_id="pcp",
        current_hypothesis=hypothesis,
    )

    assert len(actions) == 1
    metadata = actions[0].metadata
    assert metadata["verifier_recommended_match_score"] > 0.0
    assert metadata["hypothesis_recommended_match_score"] > 0.0
    assert metadata["joint_recommended_match_score"] > 0.0
    assert "imaging" in metadata["evidence_tags"]


# 验证 ImagingFinding 对应的 A3 问句使用影像学语境，而不是普通症状模板。
def test_action_builder_renders_imaging_question() -> None:
    builder = ActionBuilder()
    action = builder.build_verification_actions(
        [
            {
                "node_id": "imaging_ground_glass",
                "label": "ImagingFinding",
                "name": "双肺弥漫磨玻璃影",
                "relation_type": "HAS_IMAGING_FINDING",
                "relation_weight": 0.9,
                "node_weight": 1.0,
                "similarity_confidence": 1.0,
                "contradiction_priority": 1.0,
                "question_type_hint": "imaging",
                "priority": 2.0,
                "is_red_flag": True,
                "topic_id": "Disease",
            }
        ],
        hypothesis_id="pcp",
    )[0]

    question = builder.render_question_text(action)

    assert "胸片或胸部 CT" in question
    assert "磨玻璃影" in question
    assert "雾状" in question


# 验证 ART 这类专业缩写不会原样暴露给患者，而是改成普通人能懂的问法。
def test_action_builder_renders_art_question_patient_friendly() -> None:
    builder = ActionBuilder()
    action = MctsAction(
        action_id="verify::hiv::art",
        action_type="verify_evidence",
        target_node_id="art_use",
        target_node_label="ClinicalAttribute",
        target_node_name="ART药物使用",
        hypothesis_id="hiv",
        metadata={"question_type_hint": "detail"},
    )

    question = builder.render_question_text(action)

    assert "ART" not in question
    assert "抗病毒药" in question
    assert "治疗 HIV/艾滋病" in question
    assert "相关表现" not in question


# 验证检查类专业指标会带解释，例如 CD4 会说明它反映免疫力。
def test_action_builder_renders_cd4_question_patient_friendly() -> None:
    builder = ActionBuilder()
    action = MctsAction(
        action_id="verify::pcp::cd4",
        action_type="verify_evidence",
        target_node_id="cd4_low",
        target_node_label="LabFinding",
        target_node_name="CD4+ T淋巴细胞计数 < 200/μL",
        hypothesis_id="pcp",
        metadata={"question_type_hint": "lab"},
    )

    question = builder.render_question_text(action)

    assert "CD4" in question
    assert "免疫力" in question
    assert "低于 200" in question
    assert "T淋巴细胞计数" not in question


# 验证高成本检查证据在检查上下文未知时，会先生成自然的检查上下文采集动作。
def test_action_builder_collects_exam_context_before_high_cost_evidence() -> None:
    builder = ActionBuilder()
    state = SessionState(session_id="s1")
    actions = builder.build_verification_actions(
        [
            {
                "node_id": "lab_cd4_low",
                "label": "LabFinding",
                "name": "CD4+ T淋巴细胞计数 < 200/μL",
                "relation_type": "HAS_LAB_FINDING",
                "relation_weight": 0.9,
                "node_weight": 1.0,
                "similarity_confidence": 1.0,
                "contradiction_priority": 1.0,
                "question_type_hint": "lab",
                "acquisition_mode": "needs_lab_test",
                "evidence_cost": "high",
                "priority": 2.0,
                "is_red_flag": True,
                "topic_id": "Disease",
            }
        ],
        hypothesis_id="pcp",
        session_state=state,
    )

    assert len(actions) == 1
    assert actions[0].action_type == "collect_general_exam_context"
    assert actions[0].metadata["exam_kind"] == "general"
    assert actions[0].metadata["candidate_exam_kinds"] == ["lab"]
    assert "CD4+ T淋巴细胞计数 < 200/μL" in actions[0].metadata["exam_examples"]
    question = builder.render_question_text(actions[0])
    assert "最近有没有去医院做过检查" in question
    assert "抽血化验" in question
    assert "CT" in question
    assert "PCR" in question


# 验证 lab / imaging / pathogen 高成本证据会合并成一个统一检查入口，而不是三轮分别追问。
def test_action_builder_merges_exam_context_kinds_into_general_entry() -> None:
    builder = ActionBuilder()
    state = SessionState(session_id="s_general")
    actions = builder.build_verification_actions(
        [
            {
                "node_id": "lab_cd4_low",
                "label": "LabFinding",
                "name": "CD4+ T淋巴细胞计数 < 200/μL",
                "question_type_hint": "lab",
                "acquisition_mode": "needs_lab_test",
                "evidence_cost": "high",
                "priority": 2.0,
            },
            {
                "node_id": "ct_ground_glass",
                "label": "ImagingFinding",
                "name": "胸部CT磨玻璃影",
                "question_type_hint": "imaging",
                "acquisition_mode": "needs_imaging",
                "evidence_cost": "high",
                "priority": 2.1,
            },
            {
                "node_id": "pcr_positive",
                "label": "LabFinding",
                "name": "PCP PCR 阳性",
                "question_type_hint": "pathogen",
                "acquisition_mode": "needs_pathogen_test",
                "evidence_cost": "high",
                "priority": 2.2,
            },
        ],
        hypothesis_id="pcp",
        session_state=state,
    )

    assert len(actions) == 1
    assert actions[0].action_type == "collect_general_exam_context"
    assert set(actions[0].metadata["candidate_exam_kinds"]) == {"lab", "imaging", "pathogen"}
    assert len(actions[0].metadata["exam_candidate_evidence"]) == 3


# 验证患者明确没做过某类检查后，具体高成本结果问题会被暂时屏蔽。
def test_action_builder_skips_high_cost_evidence_when_exam_not_done() -> None:
    builder = ActionBuilder()
    state = SessionState(
        session_id="s1",
        exam_context={
            "lab": ExamContextState(exam_kind="lab", availability="not_done"),
            "imaging": ExamContextState(exam_kind="imaging"),
            "pathogen": ExamContextState(exam_kind="pathogen"),
        },
    )
    actions = builder.build_verification_actions(
        [
            {
                "node_id": "lab_cd4_low",
                "label": "LabFinding",
                "name": "CD4+ T淋巴细胞计数 < 200/μL",
                "question_type_hint": "lab",
                "acquisition_mode": "needs_lab_test",
                "evidence_cost": "high",
                "priority": 2.0,
            }
        ],
        hypothesis_id="pcp",
        session_state=state,
    )

    assert actions == []
