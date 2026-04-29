"""测试最终推理报告会输出解释性字段。"""

from brain.report_builder import ReportBuilder
from brain.search_tree import SearchTree
from brain.types import FinalAnswerScore, MctsAction, ReasoningTrajectory, SearchResult, SessionState, StopDecision
from brain.types import TreeNode


# 验证 build_final_reasoning_report 会包含答案胜出原因与路径摘要。
def test_report_builder_includes_reasoning_summary_fields() -> None:
    builder = ReportBuilder()
    state = SessionState(session_id="s1")
    search_result = SearchResult(
        selected_action=MctsAction(
            action_id="verify::d1::n2",
            action_type="verify_evidence",
            target_node_id="n2",
            target_node_label="LabFinding",
            target_node_name="低氧血症",
        ),
        root_best_action=MctsAction(
            action_id="verify::d1::n3",
            action_type="verify_evidence",
            target_node_id="n3",
            target_node_label="LabFinding",
            target_node_name="CD4+ T淋巴细胞计数 < 200/μL",
        ),
        repair_selected_action=MctsAction(
            action_id="verify::d1::n2",
            action_type="verify_evidence",
            target_node_id="n2",
            target_node_label="LabFinding",
            target_node_name="低氧血症",
        ),
        best_answer_id="d1",
        best_answer_name="肺孢子菌肺炎",
        verifier_repair_context={
            "reject_reason": "missing_key_support",
            "recommended_next_evidence": ["低氧血症"],
        },
        trajectories=[
            ReasoningTrajectory(
                trajectory_id="t1",
                final_answer_id="d1",
                final_answer_name="肺孢子菌肺炎",
                steps=[
                    {"action_name": "发热"},
                    {"action_name": "低氧血症"},
                ],
                score=0.9,
            )
        ],
        final_answer_scores=[
            FinalAnswerScore(
                answer_id="d1",
                answer_name="肺孢子菌肺炎",
                consistency=0.7,
                diversity=0.5,
                agent_evaluation=0.8,
                final_score=0.66,
            ),
            FinalAnswerScore(
                answer_id="d2",
                answer_name="肺结核",
                consistency=0.3,
                diversity=0.4,
                agent_evaluation=0.5,
                final_score=0.4,
            ),
        ],
    )

    report = builder.build_final_reasoning_report(
        state,
        StopDecision(should_stop=True, reason="final_answer_accepted", confidence=0.66),
        search_result,
    )

    assert report["best_final_answer"]["answer_id"] == "d1"
    assert "肺孢子菌肺炎" in report["why_this_answer_wins"]
    assert "发热" in report["trajectory_summary"]
    assert report["evidence_for_best_answer"] == ["发热", "低氧血症"]
    assert report["root_best_action"]["target_node_name"] == "CD4+ T淋巴细胞计数 < 200/μL"
    assert report["repair_selected_action"]["target_node_name"] == "低氧血症"
    assert report["verifier_repair_context"]["reject_reason"] == "missing_key_support"
    assert report["repair_context"]["reject_reason"] == "missing_key_support"


# 验证 build_search_report 会显式区分 root best action 与 repair selected action。
def test_report_builder_exposes_action_selection_layers_in_search_report() -> None:
    builder = ReportBuilder()
    state = SessionState(session_id="s2", turn_index=2)
    search_result = SearchResult(
        selected_action=MctsAction(
            action_id="verify::phase::rash",
            action_type="verify_evidence",
            target_node_id="symptom_rash",
            target_node_label="ClinicalFinding",
            target_node_name="皮疹",
        ),
        root_best_action=MctsAction(
            action_id="verify::phase::cd4",
            action_type="verify_evidence",
            target_node_id="lab_cd4",
            target_node_label="LabFinding",
            target_node_name="CD4+ T淋巴细胞计数 < 200/μL",
        ),
        repair_selected_action=MctsAction(
            action_id="verify::phase::rash",
            action_type="verify_evidence",
            target_node_id="symptom_rash",
            target_node_label="ClinicalFinding",
            target_node_name="皮疹",
        ),
        verifier_repair_context={
            "reject_reason": "trajectory_insufficient",
            "recommended_next_evidence": ["皮疹"],
        },
    )

    report = builder.build_search_report(state, search_result)

    assert report["selected_action"]["target_node_name"] == "皮疹"
    assert report["root_best_action"]["target_node_name"] == "CD4+ T淋巴细胞计数 < 200/μL"
    assert report["repair_selected_action"]["target_node_name"] == "皮疹"
    assert report["verifier_repair_context"]["reject_reason"] == "trajectory_insufficient"
    assert report["repair_context"]["reject_reason"] == "trajectory_insufficient"


# 验证最终报告不会把整棵搜索树和完整搜索结果对象直接塞进 metadata。
def test_report_builder_strips_heavy_runtime_metadata_from_final_report() -> None:
    builder = ReportBuilder()
    state = SessionState(session_id="s3")
    tree = SearchTree(
        nodes={
            "root": TreeNode(
                node_id="root",
                state_signature="sig-root",
                parent_id=None,
                action_from_parent=None,
                stage="A3",
                depth=0,
                children_ids=["child"],
            ),
            "child": TreeNode(
                node_id="child",
                state_signature="sig-child",
                parent_id="root",
                action_from_parent="verify::n1",
                stage="A4",
                depth=1,
            ),
        },
        root_id="root",
    )
    last_search_result = SearchResult(
        best_answer_id="d1",
        best_answer_name="肺孢子菌肺炎",
        trajectories=[
            ReasoningTrajectory(
                trajectory_id="t1",
                final_answer_id="d1",
                final_answer_name="肺孢子菌肺炎",
                steps=[{"action_name": "低氧血症"}],
                score=0.88,
            )
        ],
        final_answer_scores=[
            FinalAnswerScore(
                answer_id="d1",
                answer_name="肺孢子菌肺炎",
                consistency=0.7,
                diversity=0.5,
                agent_evaluation=0.8,
                final_score=0.66,
            )
        ],
    )
    state.metadata["search_tree"] = tree
    state.metadata["last_search_result"] = last_search_result
    state.metadata["last_guarded_acceptance_decision"] = {
        "accepted": True,
        "reason": "confidence_ok",
    }

    report = builder.build_final_report(
        state,
        StopDecision(should_stop=True, reason="final_answer_accepted", confidence=0.7),
    )

    metadata = report["metadata"]

    assert "search_tree" not in metadata
    assert "last_search_result" not in metadata
    assert metadata["search_tree_summary"]["root_id"] == "root"
    assert metadata["search_tree_summary"]["node_count"] == 2
    assert metadata["last_search_result_summary"]["best_answer_id"] == "d1"
    assert metadata["last_search_result_summary"]["trajectory_count"] == 1
    assert metadata["last_search_result_summary"]["answer_group_score_count"] == 1
    assert metadata["last_guarded_acceptance_decision"]["accepted"] is True
