"""封装回放分析、命中统计与字段归一化逻辑。"""

from __future__ import annotations

from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

from ..catalog.evidence_family_catalog import classify_evidence_families
from .types import ReplayResult


# 统一回放分析里使用的标准证据分组，保证单病例分析与 benchmark 汇总口径一致。
STANDARD_ANALYSIS_GROUPS = ("symptom", "risk", "detail", "lab", "imaging", "pathogen")
STANDARD_QUESTION_GROUPS = STANDARD_ANALYSIS_GROUPS + ("exam_context", "unknown")
STANDARD_COST_BUCKETS = ("low", "high", "unknown")
FAMILY_MATCH_RATIO_THRESHOLD = 0.88


def now_iso() -> str:
    """返回秒级 ISO 时间戳，供回放 timing 记录使用。"""

    return datetime.now().isoformat(timespec="seconds")


def build_unexpected_error_payload(exc: Exception) -> dict[str, Any]:
    """把普通 Python 异常压成统一的 replay 错误结构。"""

    return {
        "code": "unexpected_runtime_error",
        "stage": "replay_engine",
        "prompt_name": "",
        "message": f"{type(exc).__name__}: {exc}",
        "attempts": 1,
        "error_type": type(exc).__name__,
    }


def extract_turn_search_report(output_payload: dict[str, Any]) -> dict[str, Any]:
    """从 brain 输出中提取 search_report。"""

    search_report = output_payload.get("search_report")
    return dict(search_report) if isinstance(search_report, dict) else {}


def extract_turn_search_metadata(output_payload: dict[str, Any]) -> dict[str, Any]:
    """从 brain 输出中提取 search_metadata。"""

    search_report = extract_turn_search_report(output_payload)
    search_metadata = search_report.get("search_metadata")
    return dict(search_metadata) if isinstance(search_metadata, dict) else {}


def extract_case_benchmark_fields(case: object) -> dict[str, Any]:
    """从病例对象中提炼用于 replay 分层统计的轻量字段。"""

    metadata = getattr(case, "metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    case_type = str(metadata.get("case_type") or "").strip()
    case_qc_status = str(metadata.get("case_qc_status") or "").strip()
    benchmark_qc_status = str(metadata.get("benchmark_qc_status") or "").strip()
    case_qc_reasons = metadata.get("case_qc_reasons") or []

    if not isinstance(case_qc_reasons, list):
        case_qc_reasons = []

    normalized_reasons = [str(item).strip() for item in case_qc_reasons if str(item).strip()]

    if not case_qc_status and benchmark_qc_status == "eligible":
        case_qc_status = "eligible"
    if not benchmark_qc_status and case_qc_status:
        benchmark_qc_status = "eligible" if case_qc_status == "eligible" else "ineligible"

    return {
        "case_type": case_type,
        "case_qc_status": case_qc_status,
        "benchmark_qc_status": benchmark_qc_status,
        "case_qc_reasons": normalized_reasons,
    }


def build_turn_observation(
    *,
    pending_action: dict[str, Any],
    reply: object,
    case: object,
) -> dict[str, Any]:
    """把 pending action 与病例真值命中结果压成稳定字段。"""

    pending_action = dict(pending_action or {})
    metadata = pending_action.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    slot_truth_map = getattr(case, "slot_truth_map", {})
    if not isinstance(slot_truth_map, dict):
        slot_truth_map = {}

    revealed_slot_id = getattr(reply, "revealed_slot_id", None)
    truth = slot_truth_map.get(str(revealed_slot_id)) if revealed_slot_id is not None else None
    selected_source_rank = metadata.get("selected_action_source_priority_rank", 0)

    try:
        normalized_rank = int(selected_source_rank or 0)
    except (TypeError, ValueError):
        normalized_rank = 0

    return {
        "asked_action_id": str(pending_action.get("action_id") or "").strip(),
        "asked_action_type": str(pending_action.get("action_type") or "").strip(),
        "asked_target_node_label": str(pending_action.get("target_node_label") or "").strip(),
        "asked_target_node_name": str(pending_action.get("target_node_name") or "").strip(),
        "asked_action_hypothesis_id": str(pending_action.get("hypothesis_id") or "").strip(),
        "asked_action_group": _normalize_question_group(pending_action, metadata),
        "asked_action_question_type_hint": str(metadata.get("question_type_hint") or "").strip(),
        "asked_action_acquisition_mode": str(metadata.get("acquisition_mode") or "").strip(),
        "asked_action_evidence_cost": _normalize_evidence_cost(metadata.get("evidence_cost")),
        "asked_action_selected_source": str(metadata.get("selected_action_source") or "").strip(),
        "asked_action_selected_source_priority_rank": normalized_rank,
        "truth_hit": truth is not None,
        "revealed_slot_group": _truth_group(truth) if truth is not None else "",
        "revealed_slot_label": str(getattr(truth, "node_label", "") or "").strip() if truth is not None else "",
        "revealed_slot_name": _truth_display_name(truth) if truth is not None else "",
        "revealed_slot_value": getattr(truth, "value", None) if truth is not None else None,
        "revealed_slot_positive": _truth_is_positive(truth) if truth is not None else None,
        "revealed_slot_families": _truth_families(truth) if truth is not None else [],
    }


def build_case_analysis(case: object, result: ReplayResult) -> dict[str, Any]:
    """汇总单病例的问法分布、truth 命中与 required family coverage。"""

    slot_truth_map = getattr(case, "slot_truth_map", {})
    if not isinstance(slot_truth_map, dict):
        slot_truth_map = {}

    metadata = getattr(case, "metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    opening_revealed_ids = {
        str(slot_id).strip()
        for slot_id in result.opening_revealed_slot_ids
        if len(str(slot_id).strip()) > 0
    }
    revealed_ids = {
        str(turn.revealed_slot_id).strip()
        for turn in result.turns
        if turn.revealed_slot_id is not None and len(str(turn.revealed_slot_id).strip()) > 0
    }

    question_count_by_group = _empty_group_counter(STANDARD_QUESTION_GROUPS)
    question_truth_hit_count_by_group = _empty_group_counter(STANDARD_QUESTION_GROUPS)
    question_count_by_cost = _empty_group_counter(STANDARD_COST_BUCKETS)
    selected_action_source_count: dict[str, int] = {}

    # 先按回放里真正问出去的问题统计分布、来源和 truth hit。
    for turn in result.turns:
        question_group = turn.asked_action_group if turn.asked_action_group in STANDARD_QUESTION_GROUPS else "unknown"
        question_cost = (
            turn.asked_action_evidence_cost
            if turn.asked_action_evidence_cost in STANDARD_COST_BUCKETS
            else "unknown"
        )
        question_count_by_group[question_group] += 1
        question_count_by_cost[question_cost] += 1
        if turn.truth_hit:
            question_truth_hit_count_by_group[question_group] += 1

        source = str(turn.asked_action_selected_source or "").strip() or "unknown"
        selected_action_source_count[source] = selected_action_source_count.get(source, 0) + 1

    truth_counts_by_group = _empty_group_counter(STANDARD_ANALYSIS_GROUPS)
    positive_truth_counts_by_group = _empty_group_counter(STANDARD_ANALYSIS_GROUPS)
    negative_truth_counts_by_group = _empty_group_counter(STANDARD_ANALYSIS_GROUPS)
    opening_truth_counts_by_group = _empty_group_counter(STANDARD_ANALYSIS_GROUPS)
    opening_positive_truth_counts_by_group = _empty_group_counter(STANDARD_ANALYSIS_GROUPS)
    askable_truth_counts_by_group = _empty_group_counter(STANDARD_ANALYSIS_GROUPS)
    askable_positive_truth_counts_by_group = _empty_group_counter(STANDARD_ANALYSIS_GROUPS)
    covered_truth_counts_by_group = _empty_group_counter(STANDARD_ANALYSIS_GROUPS)
    covered_positive_truth_counts_by_group = _empty_group_counter(STANDARD_ANALYSIS_GROUPS)
    revealed_truth_counts_by_group = _empty_group_counter(STANDARD_ANALYSIS_GROUPS)
    revealed_positive_truth_counts_by_group = _empty_group_counter(STANDARD_ANALYSIS_GROUPS)

    opening_positive_families = _collect_revealed_positive_families(case, opening_revealed_ids)
    final_positive_families = _collect_revealed_positive_families(case, opening_revealed_ids | revealed_ids)
    required_groups = _normalize_required_family_groups(metadata)
    required_covered_on_opening = _count_required_groups_covered(required_groups, opening_positive_families)
    required_covered_after_replay = _count_required_groups_covered(required_groups, final_positive_families)

    # 再从病例真值表视角统计“总共有多少可问真值、已覆盖多少、opening 先给了多少”。
    for truth in slot_truth_map.values():
        group = _truth_group(truth)
        if group not in STANDARD_ANALYSIS_GROUPS:
            continue

        truth_counts_by_group[group] += 1
        if _truth_is_positive(truth):
            positive_truth_counts_by_group[group] += 1
        else:
            negative_truth_counts_by_group[group] += 1

        if truth.node_id in opening_revealed_ids:
            opening_truth_counts_by_group[group] += 1
            if _truth_is_positive(truth):
                opening_positive_truth_counts_by_group[group] += 1

        if truth.node_id not in opening_revealed_ids:
            askable_truth_counts_by_group[group] += 1
            if _truth_is_positive(truth):
                askable_positive_truth_counts_by_group[group] += 1

        if truth.node_id in opening_revealed_ids or truth.node_id in revealed_ids:
            covered_truth_counts_by_group[group] += 1
            if _truth_is_positive(truth):
                covered_positive_truth_counts_by_group[group] += 1

        if truth.node_id in revealed_ids:
            revealed_truth_counts_by_group[group] += 1
            if _truth_is_positive(truth):
                revealed_positive_truth_counts_by_group[group] += 1

    return {
        "top1_hit": _is_top1_hit(result),
        "accepted_final_answer": _is_accepted_result(result),
        "opening_revealed_slot_count": len(opening_revealed_ids),
        "question_count_total": len(result.turns),
        "truth_hit_question_count_total": sum(question_truth_hit_count_by_group.values()),
        "question_count_by_group": dict(question_count_by_group),
        "question_truth_hit_count_by_group": dict(question_truth_hit_count_by_group),
        "question_truth_hit_rate_by_group": _build_rate_mapping(
            question_truth_hit_count_by_group,
            question_count_by_group,
        ),
        "question_count_by_cost": dict(question_count_by_cost),
        "selected_action_source_count": dict(sorted(selected_action_source_count.items(), key=lambda item: item[0])),
        "truth_count_by_group": dict(truth_counts_by_group),
        "positive_truth_count_by_group": dict(positive_truth_counts_by_group),
        "negative_truth_count_by_group": dict(negative_truth_counts_by_group),
        "opening_truth_count_by_group": dict(opening_truth_counts_by_group),
        "opening_positive_truth_count_by_group": dict(opening_positive_truth_counts_by_group),
        "askable_truth_count_by_group": dict(askable_truth_counts_by_group),
        "askable_positive_truth_count_by_group": dict(askable_positive_truth_counts_by_group),
        "revealed_truth_count_by_group": dict(revealed_truth_counts_by_group),
        "revealed_positive_truth_count_by_group": dict(revealed_positive_truth_counts_by_group),
        "covered_truth_count_by_group": dict(covered_truth_counts_by_group),
        "covered_positive_truth_count_by_group": dict(covered_positive_truth_counts_by_group),
        "coverage_rate_by_group": _build_rate_mapping(covered_truth_counts_by_group, truth_counts_by_group),
        "positive_coverage_rate_by_group": _build_rate_mapping(
            covered_positive_truth_counts_by_group,
            positive_truth_counts_by_group,
        ),
        "askable_positive_coverage_rate_by_group": _build_rate_mapping(
            revealed_positive_truth_counts_by_group,
            askable_positive_truth_counts_by_group,
        ),
        "required_family_group_count": len(required_groups),
        "required_family_groups_covered_on_opening": required_covered_on_opening,
        "required_family_groups_covered_after_replay": required_covered_after_replay,
        "required_family_coverage_gain": max(required_covered_after_replay - required_covered_on_opening, 0),
        "required_family_groups_missing_after_replay": _missing_required_groups(required_groups, final_positive_families),
    }


def round_timing_fields(timing: dict[str, Any]) -> None:
    """把 timing 里的关键浮点字段统一 round 到 4 位。"""

    for key in (
        "opening_seconds",
        "initial_brain_seconds",
        "patient_answer_seconds_total",
        "brain_turn_seconds_total",
        "finalize_seconds",
        "total_seconds",
        "max_patient_answer_seconds",
        "max_brain_turn_seconds",
        "slowest_turn_total_seconds",
    ):
        timing[key] = round(float(timing.get(key, 0.0) or 0.0), 4)


def _empty_group_counter(groups: tuple[str, ...]) -> dict[str, int]:
    return {group: 0 for group in groups}


def _normalize_question_group(pending_action: dict[str, Any], metadata: dict[str, Any]) -> str:
    action_type = str(pending_action.get("action_type") or "").strip()
    question_type_hint = str(metadata.get("question_type_hint") or "").strip()
    target_label = str(pending_action.get("target_node_label") or "").strip()

    if action_type.startswith("collect_") or target_label == "ExamContext" or question_type_hint == "exam_context":
        return "exam_context"
    if question_type_hint in STANDARD_ANALYSIS_GROUPS:
        return question_type_hint

    label_to_group = {
        "ClinicalFinding": "symptom",
        "ClinicalAttribute": "detail",
        "RiskFactor": "risk",
        "PopulationGroup": "risk",
        "LabFinding": "lab",
        "LabTest": "lab",
        "ImagingFinding": "imaging",
        "Pathogen": "pathogen",
    }
    return label_to_group.get(target_label, "unknown")


def _normalize_evidence_cost(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"low", "high"}:
        return normalized
    return "unknown"


def _truth_group(truth: object | None) -> str:
    if truth is None:
        return ""

    group = str(getattr(truth, "group", "") or "").strip()
    if group in STANDARD_ANALYSIS_GROUPS:
        return group

    label = str(getattr(truth, "node_label", "") or "").strip()
    label_to_group = {
        "ClinicalFinding": "symptom",
        "ClinicalAttribute": "detail",
        "RiskFactor": "risk",
        "PopulationGroup": "risk",
        "LabFinding": "lab",
        "LabTest": "lab",
        "ImagingFinding": "imaging",
        "Pathogen": "pathogen",
    }
    return label_to_group.get(label, "")


def _truth_display_name(truth: object | None) -> str:
    if truth is None:
        return ""

    aliases = getattr(truth, "aliases", None) or []
    if isinstance(aliases, list):
        for alias in aliases:
            alias_text = str(alias).strip()
            if alias_text:
                return alias_text

    return str(getattr(truth, "node_id", "") or "").strip()


def _truth_is_positive(truth: object | None) -> bool:
    if truth is None:
        return False

    value = getattr(truth, "value", None)
    if isinstance(value, bool):
        return value

    value_text = str(value).strip().lower()
    return value_text not in {
        "",
        "false",
        "0",
        "none",
        "null",
        "negative",
        "absent",
        "阴性",
        "未见",
        "未检出",
        "无",
        "否",
        "正常",
    }


def _truth_families(truth: object | None) -> list[str]:
    if truth is None:
        return []

    payload = {
        "group": _truth_group(truth),
        "label": str(getattr(truth, "node_label", "") or "").strip(),
        "name": _truth_display_name(truth),
        "aliases": list(getattr(truth, "aliases", None) or []),
    }
    return classify_evidence_families(payload)


def _collect_revealed_positive_families(case: object, revealed_slot_ids: set[str]) -> set[str]:
    slot_truth_map = getattr(case, "slot_truth_map", {})
    if not isinstance(slot_truth_map, dict):
        slot_truth_map = {}

    families: set[str] = set()
    for slot_id in revealed_slot_ids:
        truth = slot_truth_map.get(slot_id)
        if truth is None or not _truth_is_positive(truth):
            continue
        families.update(_truth_families(truth))
    return families


def _normalize_required_family_groups(metadata: dict[str, Any]) -> list[set[str]]:
    raw_groups = metadata.get("benchmark_required_family_groups") or []
    if not isinstance(raw_groups, list):
        return []

    normalized: list[set[str]] = []
    for item in raw_groups:
        if not isinstance(item, list):
            continue
        family_group = {str(family).strip() for family in item if len(str(family).strip()) > 0}
        if family_group:
            normalized.append(family_group)
    return normalized


def _count_required_groups_covered(required_groups: list[set[str]], observed_families: set[str]) -> int:
    return sum(1 for family_group in required_groups if family_group & observed_families)


def _missing_required_groups(required_groups: list[set[str]], observed_families: set[str]) -> list[list[str]]:
    missing: list[list[str]] = []
    for family_group in required_groups:
        if family_group & observed_families:
            continue
        missing.append(sorted(family_group))
    return missing


def _build_rate_mapping(numerators: dict[str, int], denominators: dict[str, int]) -> dict[str, float | None]:
    payload: dict[str, float | None] = {}
    for key, numerator in numerators.items():
        denominator = int(denominators.get(key, 0) or 0)
        payload[key] = round(numerator / float(denominator), 4) if denominator > 0 else None
    return payload


def _is_top1_hit(result: ReplayResult) -> bool:
    answer_name = str((result.final_report or {}).get("best_final_answer", {}).get("answer_name") or "").strip()
    if len(answer_name) == 0:
        return False
    return answer_name in set(result.true_conditions or [])


def _is_accepted_result(result: ReplayResult) -> bool:
    return str((result.final_report or {}).get("stop_reason") or "").strip() == "final_answer_accepted"
