"""把后端推理结果转换为适合 Streamlit 展示的中文视图模型。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from brain.action_builder import ActionBuilder
from brain.types import MctsAction


_PATIENT_FRIENDLY_ACTION_BUILDER = ActionBuilder()


EXISTENCE_LABELS = {
    "exist": "存在",
    "non_exist": "不存在",
    "unknown": "不确定",
}

POLARITY_LABELS = {
    "present": "已提到 / 存在",
    "absent": "明确否定",
    "unclear": "不清楚",
}

RESOLUTION_LABELS = {
    "clear": "明确",
    "hedged": "保留",
    "unknown": "不确定",
    "confident": "明确",
    "doubt": "保留",
    "certain": "明确",
    "uncertain": "保留",
}

STAGE_LABELS = {
    "A1": "A1 重新抽取线索",
    "A2": "A2 重新排序候选诊断",
    "A3": "A3 继续验证证据",
    "PENDING_ACTION": "上一轮动作解释",
    "STOP": "建议停止并输出结论",
    "FALLBACK": "兜底追问",
}

REJECT_REASON_LABELS = {
    "missing_key_support": "缺少关键支持证据",
    "strong_alternative_not_ruled_out": "强竞争诊断尚未排除",
    "trajectory_insufficient": "搜索路径数量或稳定性不足",
    "": "无",
    None: "无",
}

GUARDED_BLOCK_LABELS = {
    "hard_negative_key_evidence": "存在关键反证，阻止过早停止",
    "missing_confirmed_key_evidence": "缺少已确认的关键证据",
    "pcp_combo_insufficient": "PCP 组合证据不足",
    "strong_alternative_not_ruled_out": "强竞争诊断未排除",
    "recent_hypothesis_switch": "最近发生候选诊断切换，需要再稳定一轮",
    "": "未阻止",
    None: "未阻止",
}

REPAIR_MODE_LABELS = {
    "repair_supporting_evidence": "补充关键支持证据",
    "repair_alternative_disambiguation": "区分强竞争诊断",
    "repair_trajectory_diversity": "增加路径多样性",
    "none": "未触发修复",
    "": "未触发修复",
    None: "未触发修复",
}

QUESTION_TYPE_LABELS = {
    "symptom": "症状 / 体征",
    "lab": "实验室 / 检查",
    "risk": "风险因素",
    "history": "病史",
    "imaging": "影像学",
    "detail": "补充细节 / 主诉澄清",
    "unknown": "未知类型",
    "": "未知类型",
    None: "未知类型",
}

EVIDENCE_GROUP_LABELS = {
    "symptom": "症状 / 体征",
    "risk": "风险背景 / 风险行为",
    "lab": "实验室 / 化验",
    "imaging": "影像",
    "pathogen": "病原学",
    "detail": "其他关键细节",
}

EVIDENCE_STATUS_LABELS = {
    "matched": "已命中",
    "negative": "已否定",
    "unknown": "待验证",
}

EVIDENCE_STATUS_ICONS = {
    "matched": "☑",
    "negative": "✖",
    "unknown": "☐",
}


# 从本地 JSON 文件读取演示回放，并补齐前端需要的默认字段。
def load_demo_replay(path: str | Path) -> dict[str, Any]:
    replay_path = Path(path)
    with replay_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    turns = [_normalize_demo_turn(item) for item in payload.get("turns", [])]
    return {
        "id": payload.get("id", replay_path.stem),
        "title": payload.get("title", replay_path.stem),
        "description": payload.get("description", ""),
        "mode": payload.get("mode", "replay"),
        "summary": payload.get("summary", {}),
        "turns": turns,
    }


# 将实时 process_turn 返回的大字典整理成前端卡片字段。
def normalize_backend_turn(result: dict[str, Any]) -> dict[str, Any]:
    search_report = _as_dict(result.get("search_report"))
    final_report = _as_dict(result.get("final_report"))
    pending_action = _as_dict(result.get("pending_action"))
    a3 = _as_dict(result.get("a3"))
    pending_action_result = _as_dict(result.get("pending_action_result"))
    route_after_pending_action = _as_dict(result.get("route_after_pending_action"))
    pending_action_decision = _as_dict(result.get("pending_action_decision"))
    selected_action = _as_dict(search_report.get("selected_action") or pending_action)
    root_best_action = _as_dict(search_report.get("root_best_action"))
    repair_action = _as_dict(search_report.get("repair_selected_action"))
    repair_context = _as_dict(search_report.get("repair_context") or search_report.get("verifier_repair_context"))
    best_score = _pick_best_answer_score(search_report, final_report)

    return {
        "turn_index": result.get("turn_index", 0),
        "patient_text": result.get("patient_text", ""),
        "system_question": result.get("next_question") or "",
        "chat_order": "patient_then_system",
        "is_final": bool(final_report),
        "final_answer": _extract_final_answer(final_report, search_report),
        "state": {
            "turn_index": result.get("turn_index", 0),
            "is_running": not bool(final_report),
            "has_final_report": bool(final_report),
            "primary_hypothesis": _extract_primary_hypothesis(result, search_report, final_report),
            "has_pending_action": bool(pending_action),
            "pending_action_name": pending_action.get("target_node_name", ""),
        },
        "a1": _adapt_a1(_as_dict(result.get("a1"))),
        "a2": _adapt_a2(
            _as_dict(result.get("a2")),
            final_report,
            search_report,
            _as_list(result.get("a2_evidence_profiles")),
        ),
        "a3": _adapt_a3(a3, selected_action, root_best_action, repair_action, repair_context),
        "pending_action_result": _adapt_pending_action(
            pending_action_result,
            route_after_pending_action,
            pending_action_decision,
            _as_dict(result.get("pending_action_audit")),
        ),
        "search": _adapt_search(search_report, best_score),
        "safety": _adapt_safety(search_report, best_score, repair_context),
        "raw": result,
    }


# 将离线 demo 的单轮结构标准化，允许示例 JSON 只写必要字段。
def _normalize_demo_turn(turn: dict[str, Any]) -> dict[str, Any]:
    search = _as_dict(turn.get("search"))
    safety = _as_dict(turn.get("safety"))
    a3 = _as_dict(turn.get("a3"))
    return {
        "turn_index": turn.get("turn_index", 0),
        "patient_text": turn.get("patient_text", ""),
        "system_question": turn.get("system_question", ""),
        "chat_order": turn.get("chat_order", "patient_then_system"),
        "is_final": bool(turn.get("is_final", False)),
        "final_answer": _as_dict(turn.get("final_answer")),
        "state": _as_dict(turn.get("state")),
        "a1": _as_dict(turn.get("a1")),
        "a2": _as_dict(turn.get("a2")),
        "a3": {
            **a3,
            "question_type_label": a3.get("question_type_label")
            or translate_question_type(a3.get("question_type")),
        },
        "pending_action_result": _as_dict(turn.get("pending_action_result") or turn.get("a4")),
        "search": search,
        "safety": {
            **safety,
            "reject_reason_label": safety.get("reject_reason_label")
            or translate_reject_reason(safety.get("verifier_reject_reason")),
            "guarded_block_reason_label": safety.get("guarded_block_reason_label")
            or translate_guarded_block(safety.get("guarded_block_reason")),
            "repair_mode_label": safety.get("repair_mode_label")
            or translate_repair_mode(safety.get("repair_mode")),
        },
        "raw": turn,
    }


def translate_existence(value: Any) -> str:
    return EXISTENCE_LABELS.get(value, str(value or "未知"))


def translate_polarity(value: Any) -> str:
    return POLARITY_LABELS.get(value, str(value or "未知"))


def translate_resolution(value: Any) -> str:
    return RESOLUTION_LABELS.get(value, str(value or "未知"))


def translate_certainty(value: Any) -> str:
    return translate_resolution(value)


def translate_stage(value: Any) -> str:
    return STAGE_LABELS.get(value, str(value or "未知"))


def translate_reject_reason(value: Any) -> str:
    return REJECT_REASON_LABELS.get(value, str(value or "无"))


def translate_guarded_block(value: Any) -> str:
    return GUARDED_BLOCK_LABELS.get(value, str(value or "未阻止"))


def translate_repair_mode(value: Any) -> str:
    return REPAIR_MODE_LABELS.get(value, str(value or "未触发修复"))


def translate_question_type(value: Any) -> str:
    return QUESTION_TYPE_LABELS.get(value, str(value or "未知类型"))


def format_score(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "暂无"


def score_to_progress(value: Any, fallback_max: float = 1.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0

    if numeric > 1.0 and fallback_max > 0:
        numeric = numeric / fallback_max

    return max(0.0, min(1.0, numeric))


def boolean_label(value: Any) -> str:
    return "是" if bool(value) else "否"


def _adapt_a1(a1: dict[str, Any]) -> dict[str, Any]:
    features = []
    for item in _as_list(a1.get("key_features")):
        item_dict = _as_dict(item)
        features.append(
            {
                "name": item_dict.get("name") or item_dict.get("normalized_name") or "未命名线索",
                "category": item_dict.get("category", ""),
                "reasoning": item_dict.get("reasoning", ""),
            }
        )

    return {
        "features": features,
        "selection_decision": a1.get("selection_decision", "selected"),
        "reasoning": a1.get("reasoning", ""),
    }


def _adapt_a2(
    a2: dict[str, Any],
    final_report: dict[str, Any],
    search_report: dict[str, Any],
    evidence_profiles: list[Any] | None = None,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    profiles_by_id: dict[str, dict[str, Any]] = {}
    profiles_by_name: dict[str, dict[str, Any]] = {}

    for profile in evidence_profiles or []:
        profile_dict = _adapt_evidence_profile(_as_dict(profile))
        candidate_id = str(profile_dict.get("candidate_id") or "")
        candidate_name = str(profile_dict.get("name") or "")

        if len(candidate_id) > 0:
            profiles_by_id[candidate_id] = profile_dict

        if len(candidate_name) > 0:
            profiles_by_name[candidate_name] = profile_dict

    primary = _as_dict(a2.get("primary_hypothesis"))

    if primary:
        candidates.append(
            _merge_candidate_profile(
                _normalize_candidate(primary, is_primary=True),
                profiles_by_id,
                profiles_by_name,
            )
        )

    for item in _as_list(a2.get("alternatives")):
        candidates.append(
            _merge_candidate_profile(
                _normalize_candidate(_as_dict(item), is_primary=False),
                profiles_by_id,
                profiles_by_name,
            )
        )

    if not candidates:
        if evidence_profiles:
            candidates = [_adapt_evidence_profile(_as_dict(item)) for item in evidence_profiles]
        else:
            for item in _as_list(final_report.get("candidate_hypotheses")):
                candidates.append(
                    _merge_candidate_profile(
                        _normalize_candidate(_as_dict(item), is_primary=False),
                        profiles_by_id,
                        profiles_by_name,
                    )
                )

    if not candidates:
        for item in _as_list(search_report.get("final_answer_scores")):
            candidate = _as_dict(item)
            candidates.append(
                {
                    "name": candidate.get("answer_name", "未知候选"),
                    "score": candidate.get("final_score", 0),
                    "score_text": format_score(candidate.get("final_score")),
                    "reasoning": "来自搜索路径聚合评分。",
                    "is_primary": False,
                    "evidence_groups": {},
                    "matched_count": 0,
                    "negative_count": 0,
                    "unknown_count": 0,
                    "score_breakdown": "当前仅有搜索路径聚合评分，暂未形成候选诊断证据画像。",
                }
            )

    return {
        "candidates": candidates[:5],
        "reasoning": a2.get("reasoning", ""),
    }


def _adapt_a3(
    a3: dict[str, Any],
    selected_action: dict[str, Any],
    root_best_action: dict[str, Any],
    repair_action: dict[str, Any],
    repair_context: dict[str, Any],
) -> dict[str, Any]:
    action = selected_action or _as_dict(a3.get("relevant_symptom"))
    metadata = _as_dict(action.get("metadata"))
    question_type = metadata.get("question_type_hint") or action.get("question_type_hint") or "unknown"
    selected_name = action.get("target_node_name", "")
    root_name = root_best_action.get("target_node_name", "")
    repair_name = repair_action.get("target_node_name", "")
    reason = a3.get("reasoning") or _build_action_reason(metadata, repair_context)

    return {
        "question_text": a3.get("question_text") or _question_from_action_name(selected_name),
        "selected_action_name": selected_name,
        "question_type": question_type,
        "question_type_label": translate_question_type(question_type),
        "reasoning": reason,
        "root_best_action_name": root_name,
        "repair_selected_action_name": repair_name,
        "is_repair_override": bool(repair_name and repair_name != root_name),
        "evidence_tags": metadata.get("evidence_tags", action.get("evidence_tags", [])),
        "recommended_match_score": metadata.get("recommended_match_score"),
        "discriminative_gain": metadata.get("discriminative_gain"),
    }


def _adapt_pending_action(
    pending_action_result: dict[str, Any],
    route_after_pending_action: dict[str, Any],
    pending_action_decision: dict[str, Any],
    pending_action_audit: dict[str, Any],
) -> dict[str, Any]:
    if not pending_action_result:
        return {
            "has_result": False,
            "polarity_label": "暂无上一轮回答",
            "resolution_label": "暂无",
            "reasoning": "第一轮尚未有待验证动作，因此没有上一轮动作解释结果。",
            "route_label": translate_stage(route_after_pending_action.get("stage")),
        }

    return {
        "has_result": True,
        "polarity": pending_action_result.get("polarity"),
        "polarity_label": translate_polarity(pending_action_result.get("polarity")),
        "resolution": pending_action_result.get("resolution"),
        "resolution_label": translate_resolution(pending_action_result.get("resolution")),
        "reasoning": pending_action_result.get("reasoning", ""),
        "supporting_span": pending_action_result.get("supporting_span", ""),
        "negation_span": pending_action_result.get("negation_span", ""),
        "uncertain_span": pending_action_result.get("uncertain_span", ""),
        "route_stage": route_after_pending_action.get("stage") or pending_action_decision.get("next_stage"),
        "route_label": translate_stage(
            route_after_pending_action.get("stage") or pending_action_decision.get("next_stage")
        ),
        "decision_type": pending_action_decision.get("decision_type", ""),
        "evidence_families": pending_action_audit.get("evidence_families", []),
        "entered_confirmed_family": pending_action_audit.get("entered_confirmed_family", False),
        "provisional_family_candidate": pending_action_audit.get("provisional_family_candidate", False),
    }


def _adapt_search(search_report: dict[str, Any], best_score: dict[str, Any]) -> dict[str, Any]:
    final_scores = [_as_dict(item) for item in _as_list(search_report.get("final_answer_scores"))]
    final_scores = sorted(final_scores, key=lambda item: float(item.get("final_score", 0) or 0), reverse=True)
    metadata = _as_dict(best_score.get("metadata"))
    return {
        "rollouts": _safe_first(
            metadata.get("rollouts_executed"),
            search_report.get("rollouts_executed"),
            search_report.get("trajectory_count"),
        ),
        "tree_node_count": _safe_first(metadata.get("tree_node_count"), search_report.get("tree_node_count")),
        "trajectory_count": search_report.get("trajectory_count", 0),
        "best_answer": search_report.get("best_answer_name") or best_score.get("answer_name", ""),
        "consistency": best_score.get("consistency"),
        "diversity": best_score.get("diversity"),
        "agent_evaluation": best_score.get("agent_evaluation"),
        "verifier_result": _verifier_result_from_metadata(metadata),
        "final_answer_scores": final_scores[:5],
    }


def _adapt_safety(
    search_report: dict[str, Any],
    best_score: dict[str, Any],
    repair_context: dict[str, Any],
) -> dict[str, Any]:
    metadata = _as_dict(best_score.get("metadata"))
    guarded_block = _safe_first(
        repair_context.get("guarded_acceptance_block_reason"),
        metadata.get("guarded_acceptance_block_reason"),
    )
    reject_reason = _safe_first(
        repair_context.get("reject_reason"),
        metadata.get("verifier_reject_reason"),
    )
    return {
        "verifier_should_accept": _safe_first(
            metadata.get("verifier_should_accept"),
            repair_context.get("verifier_should_accept"),
            False,
        ),
        "verifier_score": metadata.get("verifier_score"),
        "verifier_reject_reason": reject_reason,
        "reject_reason_label": translate_reject_reason(reject_reason),
        "guarded_acceptance_blocked": bool(guarded_block),
        "guarded_block_reason": guarded_block,
        "guarded_block_reason_label": translate_guarded_block(guarded_block),
        "tree_rerooted": bool(repair_context.get("rerooted")),
        "reroot_reason": repair_context.get("reroot_reason", ""),
        "repair_mode": repair_context.get("repair_mode"),
        "repair_mode_label": translate_repair_mode(repair_context.get("repair_mode")),
        "missing_evidence_families": repair_context.get("guarded_missing_evidence_families")
        or metadata.get("guarded_missing_evidence_families")
        or [],
        "alternative_candidates": repair_context.get("alternative_candidates")
        or metadata.get("verifier_alternative_candidates")
        or [],
        "recommended_next_evidence": repair_context.get("recommended_next_evidence") or [],
        "root_best_action": _as_dict(search_report.get("root_best_action")).get("target_node_name", ""),
        "repair_selected_action": _as_dict(search_report.get("repair_selected_action")).get("target_node_name", ""),
        "pcp_combo_uses_provisional": bool(metadata.get("guarded_pcp_combo_uses_provisional", False)),
        "confirmed_families": metadata.get("guarded_confirmed_key_evidence_families", []),
        "provisional_families": metadata.get("guarded_provisional_key_evidence_families", []),
    }


def _normalize_candidate(candidate: dict[str, Any], is_primary: bool) -> dict[str, Any]:
    return {
        "candidate_id": candidate.get("node_id") or candidate.get("answer_id") or "",
        "name": candidate.get("name") or candidate.get("answer_name") or "未知候选",
        "score": candidate.get("score", candidate.get("final_score", 0)),
        "score_text": format_score(candidate.get("score", candidate.get("final_score", 0))),
        "reasoning": candidate.get("reasoning") or candidate.get("metadata", {}).get("reasoning", ""),
        "is_primary": is_primary,
        "evidence_groups": {},
        "matched_count": 0,
        "negative_count": 0,
        "unknown_count": 0,
        "score_breakdown": "当前分数主要由 A2 候选排序和患者已知线索共同决定。",
    }


def _merge_candidate_profile(
    candidate: dict[str, Any],
    profiles_by_id: dict[str, dict[str, Any]],
    profiles_by_name: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    candidate_id = str(candidate.get("candidate_id") or "")
    candidate_name = str(candidate.get("name") or "")
    profile = profiles_by_id.get(candidate_id) or profiles_by_name.get(candidate_name)

    if profile is None:
        return candidate

    return {
        **candidate,
        "candidate_id": profile.get("candidate_id") or candidate.get("candidate_id", ""),
        "name": profile.get("name") or candidate.get("name", ""),
        "score": profile.get("score", candidate.get("score", 0)),
        "score_text": profile.get("score_text", candidate.get("score_text", "")),
        "is_primary": bool(profile.get("is_primary", candidate.get("is_primary", False))),
        "evidence_groups": profile.get("evidence_groups", {}),
        "matched_count": profile.get("matched_count", 0),
        "negative_count": profile.get("negative_count", 0),
        "unknown_count": profile.get("unknown_count", 0),
        "score_breakdown": profile.get("score_breakdown", candidate.get("score_breakdown", "")),
        "reasoning": profile.get("reasoning") or candidate.get("reasoning", ""),
    }


def _adapt_evidence_profile(profile: dict[str, Any]) -> dict[str, Any]:
    evidence_groups = {
        key: [_adapt_evidence_item(_as_dict(item)) for item in _as_list(items)]
        for key, items in _as_dict(profile.get("evidence_groups")).items()
        if len(_as_list(items)) > 0
    }
    matched_count = int(profile.get("matched_count", 0) or 0)
    negative_count = int(profile.get("negative_count", 0) or 0)
    unknown_count = int(profile.get("unknown_count", 0) or 0)

    if matched_count == 0 and negative_count == 0 and unknown_count == 0:
        for items in evidence_groups.values():
            for item in items:
                status = item.get("status")
                if status == "matched":
                    matched_count += 1
                elif status == "negative":
                    negative_count += 1
                else:
                    unknown_count += 1

    return {
        "candidate_id": profile.get("candidate_id", ""),
        "name": profile.get("candidate_name") or profile.get("name") or "未知候选",
        "score": profile.get("score", 0),
        "score_text": profile.get("score_text") or format_score(profile.get("score")),
        "reasoning": profile.get("reasoning", ""),
        "is_primary": bool(profile.get("is_primary", False)),
        "evidence_groups": evidence_groups,
        "matched_count": matched_count,
        "negative_count": negative_count,
        "unknown_count": unknown_count,
        "score_breakdown": profile.get("score_breakdown", ""),
    }


def _adapt_evidence_item(item: dict[str, Any]) -> dict[str, Any]:
    status = str(item.get("status") or "unknown")

    if status not in EVIDENCE_STATUS_LABELS:
        status = "unknown"

    group = str(item.get("question_type") or item.get("group") or "detail")
    return {
        "node_id": item.get("node_id", ""),
        "name": item.get("name", "未命名证据"),
        "label": item.get("label", ""),
        "relation_type": item.get("relation_type", ""),
        "question_type": group,
        "group_label": EVIDENCE_GROUP_LABELS.get(group, "其他关键细节"),
        "status": status,
        "status_label": item.get("status_label") or EVIDENCE_STATUS_LABELS[status],
        "status_icon": EVIDENCE_STATUS_ICONS[status],
        "resolution": item.get("resolution", item.get("certainty", "")),
        "evidence_text": item.get("evidence_text", ""),
    }


def _pick_best_answer_score(search_report: dict[str, Any], final_report: dict[str, Any]) -> dict[str, Any]:
    scores = [_as_dict(item) for item in _as_list(search_report.get("final_answer_scores"))]

    if not scores:
        scores = [_as_dict(item) for item in _as_list(final_report.get("answer_group_scores"))]

    if not scores:
        return {}

    return sorted(scores, key=lambda item: float(item.get("final_score", 0) or 0), reverse=True)[0]


def _extract_final_answer(final_report: dict[str, Any], search_report: dict[str, Any]) -> dict[str, Any]:
    best = _as_dict(final_report.get("best_final_answer"))
    if best:
        return {
            "answer_name": best.get("answer_name", ""),
            "stop_reason": final_report.get("stop_reason", ""),
            "confidence": final_report.get("stop_confidence"),
            "why": final_report.get("why_this_answer_wins", ""),
        }

    return {
        "answer_name": search_report.get("best_answer_name", ""),
        "stop_reason": final_report.get("stop_reason", ""),
        "confidence": final_report.get("stop_confidence"),
        "why": final_report.get("why_this_answer_wins", ""),
    }


def _extract_primary_hypothesis(
    result: dict[str, Any],
    search_report: dict[str, Any],
    final_report: dict[str, Any],
) -> str:
    a2 = _as_dict(result.get("a2"))
    primary = _as_dict(a2.get("primary_hypothesis"))
    if primary.get("name"):
        return primary["name"]

    if search_report.get("best_answer_name"):
        return str(search_report["best_answer_name"])

    best = _as_dict(final_report.get("best_final_answer"))
    if best.get("answer_name"):
        return best["answer_name"]

    candidates = _as_list(final_report.get("candidate_hypotheses"))
    if candidates:
        return str(_as_dict(candidates[0]).get("name", ""))

    return ""


def _build_action_reason(metadata: dict[str, Any], repair_context: dict[str, Any]) -> str:
    pieces = []
    if metadata.get("recommended_match_score"):
        pieces.append("该问题与复核器推荐补充的关键证据匹配。")
    if metadata.get("discriminative_gain"):
        pieces.append("该问题有助于区分当前主诊断与备选诊断。")
    if repair_context.get("reject_reason"):
        pieces.append(f"当前修复模式：{translate_repair_mode(repair_context.get('repair_mode'))}。")
    return " ".join(pieces) or "已结合图谱检索、搜索评分和当前证据缺口选择该问题。"


def _question_from_action_name(name: str) -> str:
    if not name:
        return ""
    action = MctsAction(
        action_id=f"frontend::fallback::{name}",
        action_type="verify_evidence",
        target_node_id=name,
        target_node_label="Unknown",
        target_node_name=name,
        metadata={"question_type_hint": "unknown"},
    )
    return _PATIENT_FRIENDLY_ACTION_BUILDER.render_question_text(action)


def _verifier_result_from_metadata(metadata: dict[str, Any]) -> str:
    if metadata.get("verifier_should_accept") is True:
        return "复核器认为可以停止"
    if metadata.get("verifier_should_accept") is False:
        return "复核器建议继续追问"
    return "暂无复核器结果"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_first(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None
