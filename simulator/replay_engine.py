"""驱动问诊大脑与虚拟病人自动对战并记录回放结果。"""

from __future__ import annotations

import json
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable, List, Optional

from brain.errors import BrainDomainError
from brain.service import ConsultationBrain

from .case_schema import VirtualPatientCase
from .evidence_family_catalog import classify_evidence_families
from .patient_agent import VirtualPatientAgent


# 这组标准分组用于把 replay 中“问了什么 / 问到了什么 / 哪类真值始终没问出”
# 统一归口，方便单病例分析和 batch 汇总共用同一套口径。
STANDARD_ANALYSIS_GROUPS = ("symptom", "risk", "detail", "lab", "imaging", "pathogen")
STANDARD_QUESTION_GROUPS = STANDARD_ANALYSIS_GROUPS + ("exam_context", "unknown")
STANDARD_COST_BUCKETS = ("low", "high", "unknown")


@dataclass
class ReplayTurn:
    """表示自动对战中的单轮问答记录。"""

    question_node_id: str
    question_text: str
    answer_text: str
    turn_index: int
    revealed_slot_id: Optional[str] = None
    stage: str = "A3"
    search_report: dict = field(default_factory=dict)
    search_metadata: dict = field(default_factory=dict)
    asked_action_id: str = ""
    asked_action_type: str = ""
    asked_target_node_label: str = ""
    asked_target_node_name: str = ""
    asked_action_hypothesis_id: str = ""
    asked_action_group: str = "unknown"
    asked_action_question_type_hint: str = ""
    asked_action_acquisition_mode: str = ""
    asked_action_evidence_cost: str = "unknown"
    asked_action_selected_source: str = ""
    asked_action_selected_source_priority_rank: int = 0
    truth_hit: bool = False
    revealed_slot_group: str = ""
    revealed_slot_label: str = ""
    revealed_slot_name: str = ""
    revealed_slot_value: Any = None
    revealed_slot_positive: Optional[bool] = None
    revealed_slot_families: list[str] = field(default_factory=list)
    patient_answer_seconds: float = 0.0
    brain_turn_seconds: float = 0.0
    total_seconds: float = 0.0


@dataclass
class ReplayResult:
    """表示单个病例自动对战完成后的回放结果。"""

    case_id: str
    case_title: str = ""
    case_type: str = ""
    case_qc_status: str = ""
    benchmark_qc_status: str = ""
    case_qc_reasons: List[str] = field(default_factory=list)
    opening_text: str = ""
    opening_revealed_slot_ids: List[str] = field(default_factory=list)
    true_conditions: List[str] = field(default_factory=list)
    true_disease_phase: Optional[str] = None
    red_flags: List[str] = field(default_factory=list)
    turns: List[ReplayTurn] = field(default_factory=list)
    final_report: dict = field(default_factory=dict)
    initial_output: dict = field(default_factory=dict)
    analysis: dict = field(default_factory=dict)
    status: str = "pending"
    timing: dict = field(default_factory=dict)
    error: dict = field(default_factory=dict)


@dataclass
class ReplayConfig:
    """保存自动回放的基础参数。"""

    max_turns: int = 8


class ReplayEngine:
    """协调问诊大脑与虚拟病人完成自动回放。"""

    # 初始化回放引擎并注入问诊大脑与病人代理。
    def __init__(
        self,
        brain: ConsultationBrain,
        patient_agent: VirtualPatientAgent,
        config: ReplayConfig | None = None,
    ) -> None:
        self.brain = brain
        self.patient_agent = patient_agent
        self.config = config or ReplayConfig()

    # 运行单个病例的自动对战并返回回放结果。
    def run_case(self, case: VirtualPatientCase) -> ReplayResult:
        session_id = f"replay::{case.case_id}"
        started_at = self._now_iso()
        case_started = perf_counter()
        case_benchmark_fields = extract_case_benchmark_fields(case)
        result = ReplayResult(
            case_id=case.case_id,
            case_title=case.title,
            case_type=case_benchmark_fields["case_type"],
            case_qc_status=case_benchmark_fields["case_qc_status"],
            benchmark_qc_status=case_benchmark_fields["benchmark_qc_status"],
            case_qc_reasons=case_benchmark_fields["case_qc_reasons"],
            true_conditions=list(case.true_conditions),
            true_disease_phase=case.true_disease_phase,
            red_flags=list(case.red_flags),
            timing={
                "started_at": started_at,
                "finished_at": "",
                "opening_seconds": 0.0,
                "initial_brain_seconds": 0.0,
                "patient_answer_seconds_total": 0.0,
                "brain_turn_seconds_total": 0.0,
                "finalize_seconds": 0.0,
                "total_seconds": 0.0,
                "max_patient_answer_seconds": 0.0,
                "max_brain_turn_seconds": 0.0,
                "slowest_turn_index": 0,
                "slowest_turn_total_seconds": 0.0,
            },
        )
        try:
            self.brain.start_session(session_id)
            opening_started = perf_counter()
            opening = self.patient_agent.open_case(case)
            result.timing["opening_seconds"] = perf_counter() - opening_started
            result.opening_text = opening.opening_text
            result.opening_revealed_slot_ids = list(getattr(opening, "revealed_slot_ids", []) or [])
            initial_brain_started = perf_counter()
            current_output = self.brain.process_turn(session_id, opening.opening_text)
            result.timing["initial_brain_seconds"] = perf_counter() - initial_brain_started
            result.initial_output = current_output

            if current_output.get("final_report") is not None:
                result.final_report = current_output["final_report"]
                result.status = "completed"
                result.analysis = self._build_case_analysis(case, result)
                self._finalize_timing(result, case_started)
                return result

            for turn_index in range(1, self.config.max_turns + 1):
                question_text = str(current_output.get("next_question") or "")
                pending_action = current_output.get("pending_action") or {}
                question_node_id = str(pending_action.get("target_node_id") or "")

                if len(question_text) == 0 or len(question_node_id) == 0:
                    break

                answer_started = perf_counter()
                reply = self.patient_agent.answer_question(question_node_id, question_text, case)
                patient_answer_seconds = perf_counter() - answer_started
                brain_turn_started = perf_counter()
                current_output = self.brain.process_turn(session_id, reply.answer_text)
                brain_turn_seconds = perf_counter() - brain_turn_started
                turn_total_seconds = patient_answer_seconds + brain_turn_seconds
                turn_observation = self._build_turn_observation(
                    pending_action=pending_action,
                    reply=reply,
                    case=case,
                )
                result.turns.append(
                    ReplayTurn(
                        question_node_id=question_node_id,
                        question_text=question_text,
                        answer_text=reply.answer_text,
                        turn_index=turn_index,
                        revealed_slot_id=reply.revealed_slot_id,
                        search_report=self._extract_turn_search_report(current_output),
                        search_metadata=self._extract_turn_search_metadata(current_output),
                        asked_action_id=turn_observation["asked_action_id"],
                        asked_action_type=turn_observation["asked_action_type"],
                        asked_target_node_label=turn_observation["asked_target_node_label"],
                        asked_target_node_name=turn_observation["asked_target_node_name"],
                        asked_action_hypothesis_id=turn_observation["asked_action_hypothesis_id"],
                        asked_action_group=turn_observation["asked_action_group"],
                        asked_action_question_type_hint=turn_observation["asked_action_question_type_hint"],
                        asked_action_acquisition_mode=turn_observation["asked_action_acquisition_mode"],
                        asked_action_evidence_cost=turn_observation["asked_action_evidence_cost"],
                        asked_action_selected_source=turn_observation["asked_action_selected_source"],
                        asked_action_selected_source_priority_rank=turn_observation[
                            "asked_action_selected_source_priority_rank"
                        ],
                        truth_hit=turn_observation["truth_hit"],
                        revealed_slot_group=turn_observation["revealed_slot_group"],
                        revealed_slot_label=turn_observation["revealed_slot_label"],
                        revealed_slot_name=turn_observation["revealed_slot_name"],
                        revealed_slot_value=turn_observation["revealed_slot_value"],
                        revealed_slot_positive=turn_observation["revealed_slot_positive"],
                        revealed_slot_families=turn_observation["revealed_slot_families"],
                        patient_answer_seconds=round(patient_answer_seconds, 4),
                        brain_turn_seconds=round(brain_turn_seconds, 4),
                        total_seconds=round(turn_total_seconds, 4),
                    )
                )
                result.timing["patient_answer_seconds_total"] = (
                    float(result.timing["patient_answer_seconds_total"]) + patient_answer_seconds
                )
                result.timing["brain_turn_seconds_total"] = (
                    float(result.timing["brain_turn_seconds_total"]) + brain_turn_seconds
                )
                if patient_answer_seconds > float(result.timing["max_patient_answer_seconds"]):
                    result.timing["max_patient_answer_seconds"] = patient_answer_seconds
                if brain_turn_seconds > float(result.timing["max_brain_turn_seconds"]):
                    result.timing["max_brain_turn_seconds"] = brain_turn_seconds
                if turn_total_seconds > float(result.timing["slowest_turn_total_seconds"]):
                    result.timing["slowest_turn_total_seconds"] = turn_total_seconds
                    result.timing["slowest_turn_index"] = turn_index

                if current_output.get("final_report") is not None:
                    result.final_report = current_output["final_report"]
                    result.status = "completed"
                    result.analysis = self._build_case_analysis(case, result)
                    self._finalize_timing(result, case_started)
                    return result

            finalize_started = perf_counter()
            result.final_report = self.brain.finalize(session_id)
            result.timing["finalize_seconds"] = perf_counter() - finalize_started
            result.status = "max_turn_reached"
        except BrainDomainError as exc:
            result.status = "failed"
            result.error = exc.to_dict()
            result.final_report = {}
        except Exception as exc:
            # 普通 Python 异常也按单病例失败落盘，避免直接中断整批 replay。
            result.status = "failed"
            result.error = self._build_unexpected_error_payload(exc)
            result.final_report = {}
        result.analysis = self._build_case_analysis(case, result)
        self._finalize_timing(result, case_started)
        return result

    # 批量运行多个病例的自动对战。
    def run_cases(self, cases: Iterable[VirtualPatientCase]) -> List[ReplayResult]:
        return [self.run_case(case) for case in cases]

    def _finalize_timing(self, result: ReplayResult, case_started: float) -> None:
        result.timing["finished_at"] = self._now_iso()
        result.timing["total_seconds"] = perf_counter() - case_started
        result.timing["turn_count"] = len(result.turns)
        self._round_timing_fields(result)

    def _round_timing_fields(self, result: ReplayResult) -> None:
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
            result.timing[key] = round(float(result.timing.get(key, 0.0) or 0.0), 4)

    def _now_iso(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _build_unexpected_error_payload(self, exc: Exception) -> dict:
        return {
            "code": "unexpected_runtime_error",
            "stage": "replay_engine",
            "prompt_name": "",
            "message": f"{type(exc).__name__}: {exc}",
            "attempts": 1,
            "error_type": type(exc).__name__,
        }

    def _extract_turn_search_report(self, output_payload: dict) -> dict:
        search_report = output_payload.get("search_report")
        return dict(search_report) if isinstance(search_report, dict) else {}

    def _extract_turn_search_metadata(self, output_payload: dict) -> dict:
        search_report = self._extract_turn_search_report(output_payload)
        search_metadata = search_report.get("search_metadata")
        return dict(search_metadata) if isinstance(search_metadata, dict) else {}

    # 把本轮 pending action 与真实 truth 命中结果压成稳定字段，方便后续离线定位“问了什么、问中了什么”。
    def _build_turn_observation(
        self,
        *,
        pending_action: dict,
        reply: object,
        case: VirtualPatientCase,
    ) -> dict[str, Any]:
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
            "asked_action_group": self._normalize_question_group(pending_action, metadata),
            "asked_action_question_type_hint": str(metadata.get("question_type_hint") or "").strip(),
            "asked_action_acquisition_mode": str(metadata.get("acquisition_mode") or "").strip(),
            "asked_action_evidence_cost": self._normalize_evidence_cost(metadata.get("evidence_cost")),
            "asked_action_selected_source": str(metadata.get("selected_action_source") or "").strip(),
            "asked_action_selected_source_priority_rank": normalized_rank,
            "truth_hit": truth is not None,
            "revealed_slot_group": self._truth_group(truth) if truth is not None else "",
            "revealed_slot_label": str(getattr(truth, "node_label", "") or "").strip() if truth is not None else "",
            "revealed_slot_name": self._truth_display_name(truth) if truth is not None else "",
            "revealed_slot_value": getattr(truth, "value", None) if truth is not None else None,
            "revealed_slot_positive": self._truth_is_positive(truth) if truth is not None else None,
            "revealed_slot_families": self._truth_families(truth) if truth is not None else [],
        }

    # 汇总单病例的问法分布、truth 命中和 required family coverage 增量，方便后续按病例定位问题层次。
    def _build_case_analysis(self, case: VirtualPatientCase, result: ReplayResult) -> dict[str, Any]:
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

        question_count_by_group = self._empty_group_counter(STANDARD_QUESTION_GROUPS)
        question_truth_hit_count_by_group = self._empty_group_counter(STANDARD_QUESTION_GROUPS)
        question_count_by_cost = self._empty_group_counter(STANDARD_COST_BUCKETS)
        selected_action_source_count: dict[str, int] = {}

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

        truth_counts_by_group = self._empty_group_counter(STANDARD_ANALYSIS_GROUPS)
        positive_truth_counts_by_group = self._empty_group_counter(STANDARD_ANALYSIS_GROUPS)
        negative_truth_counts_by_group = self._empty_group_counter(STANDARD_ANALYSIS_GROUPS)
        opening_truth_counts_by_group = self._empty_group_counter(STANDARD_ANALYSIS_GROUPS)
        opening_positive_truth_counts_by_group = self._empty_group_counter(STANDARD_ANALYSIS_GROUPS)
        askable_truth_counts_by_group = self._empty_group_counter(STANDARD_ANALYSIS_GROUPS)
        askable_positive_truth_counts_by_group = self._empty_group_counter(STANDARD_ANALYSIS_GROUPS)
        covered_truth_counts_by_group = self._empty_group_counter(STANDARD_ANALYSIS_GROUPS)
        covered_positive_truth_counts_by_group = self._empty_group_counter(STANDARD_ANALYSIS_GROUPS)
        revealed_truth_counts_by_group = self._empty_group_counter(STANDARD_ANALYSIS_GROUPS)
        revealed_positive_truth_counts_by_group = self._empty_group_counter(STANDARD_ANALYSIS_GROUPS)

        opening_positive_families = self._collect_revealed_positive_families(case, opening_revealed_ids)
        final_positive_families = self._collect_revealed_positive_families(case, opening_revealed_ids | revealed_ids)
        required_groups = self._normalize_required_family_groups(metadata)
        required_covered_on_opening = self._count_required_groups_covered(required_groups, opening_positive_families)
        required_covered_after_replay = self._count_required_groups_covered(required_groups, final_positive_families)

        for truth in slot_truth_map.values():
            group = self._truth_group(truth)
            if group not in STANDARD_ANALYSIS_GROUPS:
                continue

            truth_counts_by_group[group] += 1
            if self._truth_is_positive(truth):
                positive_truth_counts_by_group[group] += 1
            else:
                negative_truth_counts_by_group[group] += 1

            if truth.node_id in opening_revealed_ids:
                opening_truth_counts_by_group[group] += 1
                if self._truth_is_positive(truth):
                    opening_positive_truth_counts_by_group[group] += 1

            if truth.node_id not in opening_revealed_ids:
                askable_truth_counts_by_group[group] += 1
                if self._truth_is_positive(truth):
                    askable_positive_truth_counts_by_group[group] += 1

            if truth.node_id in opening_revealed_ids or truth.node_id in revealed_ids:
                covered_truth_counts_by_group[group] += 1
                if self._truth_is_positive(truth):
                    covered_positive_truth_counts_by_group[group] += 1

            if truth.node_id in revealed_ids:
                revealed_truth_counts_by_group[group] += 1
                if self._truth_is_positive(truth):
                    revealed_positive_truth_counts_by_group[group] += 1

        return {
            "top1_hit": self._is_top1_hit(result),
            "accepted_final_answer": self._is_accepted_result(result),
            "opening_revealed_slot_count": len(opening_revealed_ids),
            "question_count_total": len(result.turns),
            "truth_hit_question_count_total": sum(question_truth_hit_count_by_group.values()),
            "question_count_by_group": dict(question_count_by_group),
            "question_truth_hit_count_by_group": dict(question_truth_hit_count_by_group),
            "question_truth_hit_rate_by_group": self._build_rate_mapping(
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
            "coverage_rate_by_group": self._build_rate_mapping(
                covered_truth_counts_by_group,
                truth_counts_by_group,
            ),
            "positive_coverage_rate_by_group": self._build_rate_mapping(
                covered_positive_truth_counts_by_group,
                positive_truth_counts_by_group,
            ),
            "askable_positive_coverage_rate_by_group": self._build_rate_mapping(
                revealed_positive_truth_counts_by_group,
                askable_positive_truth_counts_by_group,
            ),
            "required_family_group_count": len(required_groups),
            "required_family_groups_covered_on_opening": required_covered_on_opening,
            "required_family_groups_covered_after_replay": required_covered_after_replay,
            "required_family_coverage_gain": max(required_covered_after_replay - required_covered_on_opening, 0),
            "required_family_groups_missing_after_replay": self._missing_required_groups(
                required_groups,
                final_positive_families,
            ),
        }

    def _empty_group_counter(self, groups: tuple[str, ...]) -> dict[str, int]:
        return {group: 0 for group in groups}

    def _normalize_question_group(self, pending_action: dict, metadata: dict) -> str:
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

    def _normalize_evidence_cost(self, value: object) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"low", "high"}:
            return normalized
        return "unknown"

    def _truth_group(self, truth: object | None) -> str:
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

    def _truth_display_name(self, truth: object | None) -> str:
        if truth is None:
            return ""

        aliases = getattr(truth, "aliases", None) or []
        if isinstance(aliases, list):
            for alias in aliases:
                alias_text = str(alias).strip()
                if alias_text:
                    return alias_text

        return str(getattr(truth, "node_id", "") or "").strip()

    def _truth_is_positive(self, truth: object | None) -> bool:
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

    def _truth_families(self, truth: object | None) -> list[str]:
        if truth is None:
            return []

        payload = {
            "group": self._truth_group(truth),
            "label": str(getattr(truth, "node_label", "") or "").strip(),
            "name": self._truth_display_name(truth),
            "aliases": list(getattr(truth, "aliases", None) or []),
        }
        return classify_evidence_families(payload)

    def _collect_revealed_positive_families(
        self,
        case: object,
        revealed_slot_ids: set[str],
    ) -> set[str]:
        slot_truth_map = getattr(case, "slot_truth_map", {})
        if not isinstance(slot_truth_map, dict):
            slot_truth_map = {}

        families: set[str] = set()

        for slot_id in revealed_slot_ids:
            truth = slot_truth_map.get(slot_id)

            if truth is None or not self._truth_is_positive(truth):
                continue

            families.update(self._truth_families(truth))

        return families

    def _normalize_required_family_groups(self, metadata: dict[str, Any]) -> list[set[str]]:
        if not isinstance(metadata, dict):
            return []

        raw_groups = metadata.get("benchmark_required_family_groups") or []
        if not isinstance(raw_groups, list):
            return []

        normalized: list[set[str]] = []

        for item in raw_groups:
            if not isinstance(item, list):
                continue

            family_group = {
                str(family).strip()
                for family in item
                if len(str(family).strip()) > 0
            }
            if family_group:
                normalized.append(family_group)

        return normalized

    def _count_required_groups_covered(
        self,
        required_groups: list[set[str]],
        observed_families: set[str],
    ) -> int:
        return sum(1 for family_group in required_groups if family_group & observed_families)

    def _missing_required_groups(
        self,
        required_groups: list[set[str]],
        observed_families: set[str],
    ) -> list[list[str]]:
        missing: list[list[str]] = []

        for family_group in required_groups:
            if family_group & observed_families:
                continue
            missing.append(sorted(family_group))

        return missing

    def _build_rate_mapping(
        self,
        numerators: dict[str, int],
        denominators: dict[str, int],
    ) -> dict[str, float | None]:
        payload: dict[str, float | None] = {}

        for key, numerator in numerators.items():
            denominator = int(denominators.get(key, 0) or 0)
            payload[key] = round(numerator / float(denominator), 4) if denominator > 0 else None

        return payload

    def _is_top1_hit(self, result: ReplayResult) -> bool:
        answer_name = str((result.final_report or {}).get("best_final_answer", {}).get("answer_name") or "").strip()
        if len(answer_name) == 0:
            return False
        return answer_name in set(result.true_conditions or [])

    def _is_accepted_result(self, result: ReplayResult) -> bool:
        return str((result.final_report or {}).get("stop_reason") or "").strip() == "final_answer_accepted"


# 将批量回放结果写入 JSONL，便于后续复盘分析。
def write_replay_results_jsonl(results: Iterable[ReplayResult], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with output_file.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result, ensure_ascii=False, default=lambda obj: obj.__dict__) + "\n")


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
