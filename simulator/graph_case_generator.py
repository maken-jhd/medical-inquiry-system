"""根据疾病级图谱审计输出生成图谱驱动的虚拟病人病例。"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .case_schema import SlotTruth, VirtualPatientCase
from .generate_cases import write_cases_json, write_cases_jsonl


STANDARD_GROUPS = ("symptom", "risk", "lab", "imaging", "pathogen", "detail")
LOW_COST_GROUPS = {"symptom", "risk", "detail"}
CHIEF_COMPLAINT_NATURAL_GROUPS = {"symptom", "detail"}
EXAM_GROUPS = {"lab", "imaging", "pathogen"}
LOW_COST_ACQUISITION_MODES = {"direct_ask", "history_known"}
EXAM_ACQUISITION_MODES = {"needs_lab_test", "needs_imaging", "needs_pathogen_test"}
HIGH_VALUE_EXAM_RELATION_TYPES = {
    "DIAGNOSED_BY",
    "HAS_LAB_FINDING",
    "HAS_IMAGING_FINDING",
    "HAS_PATHOGEN",
}
REQUIRED_EVIDENCE_FIELDS = (
    "group",
    "relation_type",
    "priority",
    "relation_specificity",
    "acquisition_mode",
    "evidence_cost",
)
CASE_TYPE_LABELS = {
    "ordinary": "普通病例",
    "low_cost": "低成本病例",
    "exam_driven": "检查驱动病例",
    "competitive": "竞争病例",
}
CASE_TYPE_ORDER = ("ordinary", "low_cost", "exam_driven", "competitive")
POSITIVE_SLOT_LIMIT = 6
COMPETITIVE_NEGATIVE_SLOT_LIMIT = 3
DEFAULT_BEHAVIOR_STYLE = "cooperative"


@dataclass(frozen=True)
class GraphCaseGeneratorConfig:
    """图谱驱动病例生成器配置。"""

    ordinary_min_total_pool: int = 4
    ordinary_min_chief_pool: int = 2
    low_cost_min_pool: int = 4
    exam_driven_min_exam_pool: int = 3
    exam_driven_min_high_value_pool: int = 2
    competitive_min_shared_pool: int = 2
    competitive_min_target_only_pool: int = 2
    competitive_min_competitor_negative_pool: int = 1
    max_competitors_per_disease: int = 1


@dataclass
class DiseaseAuditRecord:
    """表示单个疾病审计 JSON 的核心内容。"""

    disease_id: str
    disease_name: str
    disease_label: str
    evidence: list[dict[str, Any]]
    group_summary: dict[str, dict[str, Any]]
    summary: dict[str, Any]
    source_file: Path


@dataclass
class DiseaseProfile:
    """缓存单个疾病可用于病例生成的证据池。"""

    record: DiseaseAuditRecord
    all_evidence: list[dict[str, Any]]
    chief_pool: list[dict[str, Any]]
    low_cost_pool: list[dict[str, Any]]
    exam_pool: list[dict[str, Any]]
    exam_high_value_pool: list[dict[str, Any]]
    symptom_pool: list[dict[str, Any]]
    risk_detail_pool: list[dict[str, Any]]
    all_keys: set[str]
    low_cost_keys: set[str]
    symptom_keys: set[str]
    risk_detail_keys: set[str]
    exam_name_keys: set[str]
    exam_group_keys: set[str]
    exam_relation_keys: set[str]
    discriminative_priority_cutoff: float

    @property
    def pool_counts(self) -> dict[str, int]:
        return {
            "total_pool": len(self.all_evidence),
            "chief_complaint_friendly_pool": len(self.chief_pool),
            "low_cost_pool": len(self.low_cost_pool),
            "exam_pool_total": len(self.exam_pool),
            "exam_pool_high_value": len(self.exam_high_value_pool),
            "symptom_pool": len(self.symptom_pool),
            "risk_detail_pool": len(self.risk_detail_pool),
        }

    @property
    def evidence_counts_by_group(self) -> dict[str, int]:
        counts = {group: 0 for group in STANDARD_GROUPS}
        for item in self.all_evidence:
            group = str(item.get("group") or "")
            if group in counts:
                counts[group] += 1
        return counts


@dataclass
class CompetitionCandidate:
    """表示 target disease 的一个竞争病候选。"""

    target: DiseaseProfile
    competitor: DiseaseProfile
    competition_score: float
    score_breakdown: dict[str, float]
    shared_low_cost: list[dict[str, Any]]
    target_only_discriminative: list[dict[str, Any]]
    competitor_only_negative: list[dict[str, Any]]


@dataclass
class GraphCaseGenerationResult:
    """封装病例生成结果及落盘内容。"""

    cases: list[VirtualPatientCase]
    manifest: dict[str, Any]
    summary_markdown: str


def load_disease_audit_reports(audit_root: Path) -> tuple[list[DiseaseAuditRecord], list[dict[str, Any]]]:
    """从审计目录读取所有疾病报告，并返回有效和无效报告。"""

    valid_records: list[DiseaseAuditRecord] = []
    invalid_records: list[dict[str, Any]] = []

    for path in sorted(audit_root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))

        if not _looks_like_disease_report(payload):
            continue

        disease_payload = payload.get("disease") or {}
        record = DiseaseAuditRecord(
            disease_id=str(disease_payload.get("disease_id") or ""),
            disease_name=str(disease_payload.get("disease_name") or ""),
            disease_label=str(disease_payload.get("disease_label") or ""),
            evidence=[item for item in payload.get("evidence") or [] if isinstance(item, dict)],
            group_summary={
                str(group): dict(summary)
                for group, summary in (payload.get("group_summary") or {}).items()
                if isinstance(summary, dict)
            },
            summary=dict(payload.get("summary") or {}),
            source_file=path,
        )

        missing_fields = _collect_missing_required_fields(record.evidence)
        if missing_fields:
            invalid_records.append(
                {
                    "disease_id": record.disease_id,
                    "disease_name": record.disease_name,
                    "source_file": str(path),
                    "missing_fields": missing_fields,
                }
            )
            continue

        valid_records.append(record)

    return valid_records, invalid_records


class GraphCaseGenerator:
    """按疾病审计输出批量生成图谱驱动病例。"""

    def __init__(self, config: GraphCaseGeneratorConfig | None = None) -> None:
        self.config = config or GraphCaseGeneratorConfig()

    def generate_from_audit_root(self, audit_root: Path) -> GraphCaseGenerationResult:
        """从审计目录读取报告并生成病例。"""

        valid_records, invalid_records = load_disease_audit_reports(audit_root)
        return self.generate_from_records(valid_records, invalid_records=invalid_records, audit_root=audit_root)

    def generate_from_records(
        self,
        records: Sequence[DiseaseAuditRecord],
        *,
        invalid_records: Sequence[dict[str, Any]] | None = None,
        audit_root: Path | None = None,
    ) -> GraphCaseGenerationResult:
        """直接从已解析的审计记录生成病例。"""

        profiles = [self._build_profile(record) for record in records]
        cases: list[VirtualPatientCase] = []
        disease_entries: list[dict[str, Any]] = []
        generated_case_count_by_type = {case_type: 0 for case_type in CASE_TYPE_ORDER}
        skipped_case_count_by_reason: dict[str, int] = {}

        for profile in sorted(profiles, key=lambda item: (item.record.disease_name, item.record.disease_id)):
            entry = {
                "disease_id": profile.record.disease_id,
                "disease_name": profile.record.disease_name,
                "source_file": str(profile.record.source_file),
                "generated_case_types": [],
                "generated_case_ids": [],
                "skipped": [],
                "pool_counts": profile.pool_counts,
            }

            ordinary_case, ordinary_skip = self._build_ordinary_case(profile)
            self._record_case_result(
                entry,
                ordinary_case,
                ordinary_skip,
                cases,
                generated_case_count_by_type,
                skipped_case_count_by_reason,
            )

            low_cost_case, low_cost_skip = self._build_low_cost_case(profile)
            self._record_case_result(
                entry,
                low_cost_case,
                low_cost_skip,
                cases,
                generated_case_count_by_type,
                skipped_case_count_by_reason,
            )

            exam_case, exam_skip = self._build_exam_driven_case(profile)
            self._record_case_result(
                entry,
                exam_case,
                exam_skip,
                cases,
                generated_case_count_by_type,
                skipped_case_count_by_reason,
            )

            disease_entries.append(entry)

        profile_by_id = {profile.record.disease_id: profile for profile in profiles}
        for entry in disease_entries:
            profile = profile_by_id.get(str(entry.get("disease_id") or ""))
            if profile is None:
                continue

            competition_cases, competition_skip = self._build_competitive_cases(profile, profiles)
            if competition_cases:
                for case in competition_cases:
                    if "competitive" not in entry["generated_case_types"]:
                        entry["generated_case_types"].append("competitive")
                    entry["generated_case_ids"].append(case.case_id)
                    cases.append(case)
                    generated_case_count_by_type["competitive"] += 1
            elif competition_skip:
                entry["skipped"].append(competition_skip)
                skipped_case_count_by_reason[competition_skip["reason"]] = (
                    skipped_case_count_by_reason.get(competition_skip["reason"], 0) + 1
                )

        for invalid_entry in sorted(
            invalid_records or [],
            key=lambda item: (str(item.get("disease_name") or ""), str(item.get("disease_id") or "")),
        ):
            skip_entry = {
                "case_type": "all",
                "reason": "audit_report_missing_required_fields",
                "available_pool_size": 0,
                "required_pool_size": 0,
                "missing_fields": list(invalid_entry.get("missing_fields") or []),
            }
            disease_entries.append(
                {
                    "disease_id": str(invalid_entry.get("disease_id") or ""),
                    "disease_name": str(invalid_entry.get("disease_name") or ""),
                    "source_file": str(invalid_entry.get("source_file") or ""),
                    "generated_case_types": [],
                    "generated_case_ids": [],
                    "skipped": [skip_entry],
                    "pool_counts": {},
                }
            )
            skipped_case_count_by_reason["audit_report_missing_required_fields"] = (
                skipped_case_count_by_reason.get("audit_report_missing_required_fields", 0) + 1
            )

        disease_entries.sort(key=lambda item: (str(item.get("disease_name") or ""), str(item.get("disease_id") or "")))
        cases.sort(key=lambda item: item.case_id)

        manifest = {
            "audit_root": str(audit_root) if audit_root else "",
            "config": asdict(self.config),
            "generated_case_count": len(cases),
            "generated_case_count_by_type": generated_case_count_by_type,
            "skipped_case_count_by_reason": dict(sorted(skipped_case_count_by_reason.items())),
            "disease_report_count": len(records) + len(invalid_records or []),
            "valid_disease_report_count": len(records),
            "invalid_disease_report_count": len(invalid_records or []),
            "diseases": disease_entries,
        }
        summary_markdown = render_generation_summary_markdown(manifest)
        return GraphCaseGenerationResult(cases=cases, manifest=manifest, summary_markdown=summary_markdown)

    def _build_profile(self, record: DiseaseAuditRecord) -> DiseaseProfile:
        """为单个疾病构建去重后的证据池。"""

        all_evidence = _sort_evidence_items(_unique_evidence_items(record.evidence))
        chief_pool = [item for item in all_evidence if _is_chief_complaint_friendly(item)]
        low_cost_pool = [item for item in all_evidence if _is_low_cost_evidence(item)]
        exam_pool = [item for item in all_evidence if _is_exam_evidence(item)]
        exam_high_value_pool = _select_high_value_exam_pool(exam_pool)
        symptom_pool = [item for item in all_evidence if str(item.get("group") or "") == "symptom"]
        risk_detail_pool = [
            item
            for item in all_evidence
            if str(item.get("group") or "") in {"risk", "detail"}
        ]

        return DiseaseProfile(
            record=record,
            all_evidence=all_evidence,
            chief_pool=chief_pool,
            low_cost_pool=low_cost_pool,
            exam_pool=exam_pool,
            exam_high_value_pool=exam_high_value_pool,
            symptom_pool=symptom_pool,
            risk_detail_pool=risk_detail_pool,
            all_keys={_evidence_key(item) for item in all_evidence},
            low_cost_keys={_evidence_key(item) for item in low_cost_pool},
            symptom_keys={_evidence_key(item) for item in symptom_pool},
            risk_detail_keys={_evidence_key(item) for item in risk_detail_pool},
            exam_name_keys={_normalize_text(str(item.get("target_name") or "")) for item in exam_high_value_pool},
            exam_group_keys={str(item.get("group") or "") for item in exam_high_value_pool},
            exam_relation_keys={str(item.get("relation_type") or "") for item in exam_high_value_pool},
            discriminative_priority_cutoff=_top_half_priority_cutoff(all_evidence),
        )

    def _record_case_result(
        self,
        manifest_entry: dict[str, Any],
        case: VirtualPatientCase | None,
        skip_entry: dict[str, Any] | None,
        cases: list[VirtualPatientCase],
        generated_case_count_by_type: dict[str, int],
        skipped_case_count_by_reason: dict[str, int],
    ) -> None:
        """将单个病例类型的生成结果写入 manifest。"""

        if case is not None:
            case_type = str(case.metadata.get("case_type") or "")
            if case_type not in manifest_entry["generated_case_types"]:
                manifest_entry["generated_case_types"].append(case_type)
            manifest_entry["generated_case_ids"].append(case.case_id)
            cases.append(case)
            if case_type in generated_case_count_by_type:
                generated_case_count_by_type[case_type] += 1
            return

        if skip_entry is not None:
            manifest_entry["skipped"].append(skip_entry)
            skipped_case_count_by_reason[skip_entry["reason"]] = (
                skipped_case_count_by_reason.get(skip_entry["reason"], 0) + 1
            )

    def _build_ordinary_case(
        self,
        profile: DiseaseProfile,
    ) -> tuple[VirtualPatientCase | None, dict[str, Any] | None]:
        """生成 ordinary 病例，或返回跳过原因。"""

        total_pool = len(profile.all_evidence)
        if total_pool < self.config.ordinary_min_total_pool:
            return None, _skip_entry(
                case_type="ordinary",
                reason="insufficient_total_pool",
                available=total_pool,
                required=self.config.ordinary_min_total_pool,
            )

        chief_pool = profile.chief_pool
        if len(chief_pool) < self.config.ordinary_min_chief_pool:
            return None, _skip_entry(
                case_type="ordinary",
                reason="insufficient_chief_complaint_friendly_pool",
                available=len(chief_pool),
                required=self.config.ordinary_min_chief_pool,
            )

        chief_items = _select_chief_complaint_items(chief_pool)
        if not any(str(item.get("group") or "") in CHIEF_COMPLAINT_NATURAL_GROUPS for item in chief_items):
            return None, _skip_entry(
                case_type="ordinary",
                reason="insufficient_chief_complaint_friendly_pool",
                available=len(chief_pool),
                required=self.config.ordinary_min_chief_pool,
            )

        chief_text = _render_low_cost_complaint(chief_items, fallback_name=profile.record.disease_name)
        positive_items = _limit_unique_items(
            [*chief_pool, *profile.exam_high_value_pool[:1]],
            limit=POSITIVE_SLOT_LIMIT,
        )
        return self._build_case(
            profile=profile,
            case_type="ordinary",
            chief_complaint=chief_text,
            positive_items=positive_items,
            negative_items=[],
            metadata_extra={},
            title_suffix=CASE_TYPE_LABELS["ordinary"],
            case_index=1,
        ), None

    def _build_low_cost_case(
        self,
        profile: DiseaseProfile,
    ) -> tuple[VirtualPatientCase | None, dict[str, Any] | None]:
        """生成 low_cost 病例，或返回跳过原因。"""

        pool = profile.low_cost_pool
        if len(pool) < self.config.low_cost_min_pool:
            return None, _skip_entry(
                case_type="low_cost",
                reason="insufficient_low_cost_pool",
                available=len(pool),
                required=self.config.low_cost_min_pool,
            )

        positive_items = _limit_unique_items(pool, limit=POSITIVE_SLOT_LIMIT)
        chief_text = _render_low_cost_complaint(positive_items[:3], fallback_name=profile.record.disease_name)
        return self._build_case(
            profile=profile,
            case_type="low_cost",
            chief_complaint=chief_text,
            positive_items=positive_items,
            negative_items=[],
            metadata_extra={},
            title_suffix=CASE_TYPE_LABELS["low_cost"],
            case_index=1,
        ), None

    def _build_exam_driven_case(
        self,
        profile: DiseaseProfile,
    ) -> tuple[VirtualPatientCase | None, dict[str, Any] | None]:
        """生成 exam_driven 病例，或返回跳过原因。"""

        exam_pool = profile.exam_pool
        if len(exam_pool) < self.config.exam_driven_min_exam_pool:
            return None, _skip_entry(
                case_type="exam_driven",
                reason="insufficient_exam_pool",
                available=len(exam_pool),
                required=self.config.exam_driven_min_exam_pool,
            )

        high_value_pool = profile.exam_high_value_pool
        if len(high_value_pool) < self.config.exam_driven_min_high_value_pool:
            return None, _skip_entry(
                case_type="exam_driven",
                reason="insufficient_high_value_exam_pool",
                available=len(high_value_pool),
                required=self.config.exam_driven_min_high_value_pool,
            )

        supplement = [
            item
            for item in profile.low_cost_pool
            if str(item.get("group") or "") in {"symptom", "risk"}
        ][:1]
        exam_selected = _limit_unique_items(high_value_pool + exam_pool, limit=POSITIVE_SLOT_LIMIT - len(supplement))
        positive_items = _limit_unique_items([*exam_selected, *supplement], limit=POSITIVE_SLOT_LIMIT)
        chief_text = _render_exam_driven_complaint(exam_selected[:3], supplement[:1], profile.record.disease_name)
        return self._build_case(
            profile=profile,
            case_type="exam_driven",
            chief_complaint=chief_text,
            positive_items=positive_items,
            negative_items=[],
            metadata_extra={},
            title_suffix=CASE_TYPE_LABELS["exam_driven"],
            case_index=1,
        ), None

    def _build_competitive_cases(
        self,
        profile: DiseaseProfile,
        profiles: Sequence[DiseaseProfile],
    ) -> tuple[list[VirtualPatientCase], dict[str, Any] | None]:
        """生成竞争病例，或返回跳过原因。"""

        candidates = self._rank_competitors(profile, profiles)
        if not candidates:
            return [], _skip_entry(
                case_type="competitive",
                reason="competitor_not_found",
                available=0,
                required=1,
            )

        eligible_candidates: list[CompetitionCandidate] = []
        first_failure: dict[str, Any] | None = None
        for candidate in candidates:
            skip_reason = self._competitive_skip_reason(candidate)
            if skip_reason is not None:
                if first_failure is None:
                    first_failure = skip_reason
                continue
            eligible_candidates.append(candidate)

        if not eligible_candidates:
            return [], first_failure

        cases: list[VirtualPatientCase] = []
        for index, candidate in enumerate(eligible_candidates[: self.config.max_competitors_per_disease], start=1):
            shared_items = _limit_unique_items(candidate.shared_low_cost, limit=3)
            target_only_items = _limit_unique_items(candidate.target_only_discriminative, limit=3)
            negative_items = _limit_unique_items(
                candidate.competitor_only_negative,
                limit=COMPETITIVE_NEGATIVE_SLOT_LIMIT,
            )
            chief_text = _render_low_cost_complaint(shared_items, fallback_name=profile.record.disease_name)
            metadata_extra = {
                "competitor_disease_id": candidate.competitor.record.disease_id,
                "competitor_disease_name": candidate.competitor.record.disease_name,
                "competition_score": round(candidate.competition_score, 4),
                "shared_low_cost": [str(item.get("target_name") or "") for item in shared_items],
                "target_only_discriminative": [
                    str(item.get("target_name") or "") for item in target_only_items
                ],
                "competitor_only_negative": [
                    str(item.get("target_name") or "") for item in negative_items
                ],
                "competition_score_breakdown": candidate.score_breakdown,
            }
            cases.append(
                self._build_case(
                    profile=profile,
                    case_type="competitive",
                    chief_complaint=chief_text,
                    positive_items=_limit_unique_items([*shared_items, *target_only_items], limit=POSITIVE_SLOT_LIMIT),
                    negative_items=negative_items,
                    metadata_extra=metadata_extra,
                    title_suffix=f"{CASE_TYPE_LABELS['competitive']}（vs {candidate.competitor.record.disease_name}）",
                    case_index=index,
                )
            )

        return cases, None

    def _rank_competitors(
        self,
        target: DiseaseProfile,
        profiles: Sequence[DiseaseProfile],
    ) -> list[CompetitionCandidate]:
        """按综合竞争分数为目标疾病排序竞争病。"""

        candidates: list[CompetitionCandidate] = []
        for competitor in profiles:
            if competitor.record.disease_id == target.record.disease_id:
                continue

            low_cost_overlap = _jaccard(target.low_cost_keys, competitor.low_cost_keys)
            symptom_jaccard = _jaccard(target.symptom_keys, competitor.symptom_keys)
            risk_detail_overlap = _jaccard(target.risk_detail_keys, competitor.risk_detail_keys)
            exam_path_similarity = _exam_path_similarity(target, competitor)
            competition_score = (
                0.40 * low_cost_overlap
                + 0.25 * symptom_jaccard
                + 0.20 * risk_detail_overlap
                + 0.15 * exam_path_similarity
            )

            if competition_score <= 0.0:
                continue

            shared_low_cost = [
                item
                for item in target.low_cost_pool
                if _evidence_key(item) in competitor.low_cost_keys
            ]
            target_only_discriminative = [
                item
                for item in target.all_evidence
                if _evidence_key(item) not in competitor.all_keys
                and (
                    float(item.get("relation_specificity") or 0.0) >= 0.85
                    or float(item.get("priority") or 0.0) >= target.discriminative_priority_cutoff
                )
            ]
            competitor_only_negative_pool = _limit_unique_items(
                [*competitor.low_cost_pool, *competitor.exam_high_value_pool],
                limit=POSITIVE_SLOT_LIMIT + COMPETITIVE_NEGATIVE_SLOT_LIMIT,
            )
            competitor_only_negative = [
                item
                for item in competitor_only_negative_pool
                if _evidence_key(item) not in target.all_keys
            ]

            candidates.append(
                CompetitionCandidate(
                    target=target,
                    competitor=competitor,
                    competition_score=competition_score,
                    score_breakdown={
                        "low_cost_overlap": round(low_cost_overlap, 4),
                        "symptom_jaccard": round(symptom_jaccard, 4),
                        "risk_detail_overlap": round(risk_detail_overlap, 4),
                        "exam_path_similarity": round(exam_path_similarity, 4),
                    },
                    shared_low_cost=_sort_evidence_items(shared_low_cost),
                    target_only_discriminative=_sort_evidence_items(target_only_discriminative),
                    competitor_only_negative=_sort_evidence_items(competitor_only_negative),
                )
            )

        return sorted(
            candidates,
            key=lambda item: (
                -item.competition_score,
                -len(item.shared_low_cost),
                -len(item.target_only_discriminative),
                item.competitor.record.disease_name,
            ),
        )

    def _competitive_skip_reason(self, candidate: CompetitionCandidate) -> dict[str, Any] | None:
        """判断竞争病候选是否满足生成门槛。"""

        if len(candidate.shared_low_cost) < self.config.competitive_min_shared_pool:
            return {
                **_skip_entry(
                    case_type="competitive",
                    reason="insufficient_shared_pool",
                    available=len(candidate.shared_low_cost),
                    required=self.config.competitive_min_shared_pool,
                ),
                "candidate_competitor_disease_id": candidate.competitor.record.disease_id,
                "candidate_competitor_disease_name": candidate.competitor.record.disease_name,
            }

        if len(candidate.target_only_discriminative) < self.config.competitive_min_target_only_pool:
            return {
                **_skip_entry(
                    case_type="competitive",
                    reason="insufficient_target_only_pool",
                    available=len(candidate.target_only_discriminative),
                    required=self.config.competitive_min_target_only_pool,
                ),
                "candidate_competitor_disease_id": candidate.competitor.record.disease_id,
                "candidate_competitor_disease_name": candidate.competitor.record.disease_name,
            }

        if len(candidate.competitor_only_negative) < self.config.competitive_min_competitor_negative_pool:
            return {
                **_skip_entry(
                    case_type="competitive",
                    reason="insufficient_competitor_negative_pool",
                    available=len(candidate.competitor_only_negative),
                    required=self.config.competitive_min_competitor_negative_pool,
                ),
                "candidate_competitor_disease_id": candidate.competitor.record.disease_id,
                "candidate_competitor_disease_name": candidate.competitor.record.disease_name,
            }

        return None

    def _build_case(
        self,
        *,
        profile: DiseaseProfile,
        case_type: str,
        chief_complaint: str,
        positive_items: Sequence[dict[str, Any]],
        negative_items: Sequence[dict[str, Any]],
        metadata_extra: dict[str, Any],
        title_suffix: str,
        case_index: int,
    ) -> VirtualPatientCase:
        """将选中的证据列表渲染成 VirtualPatientCase。"""

        slot_truth_map: dict[str, SlotTruth] = {}
        for item in positive_items:
            node_id = str(item.get("target_node_id") or "")
            if len(node_id) == 0:
                continue
            slot_truth_map[node_id] = _build_slot_truth(item, True)

        for item in negative_items:
            node_id = str(item.get("target_node_id") or "")
            if len(node_id) == 0 or node_id in slot_truth_map:
                continue
            slot_truth_map[node_id] = _build_slot_truth(item, False)

        case_id = _build_case_id(
            case_type=case_type,
            disease_id=profile.record.disease_id,
            competitor_id=str(metadata_extra.get("competitor_disease_id") or ""),
            case_index=case_index,
        )
        metadata = {
            "source": "graph_audit",
            "case_type": case_type,
            "disease_id": profile.record.disease_id,
            "disease_name": profile.record.disease_name,
            "selected_positive_slots": [str(item.get("target_name") or "") for item in positive_items],
            "selected_negative_slots": [str(item.get("target_name") or "") for item in negative_items],
            "evidence_counts_by_group": profile.evidence_counts_by_group,
            **metadata_extra,
        }
        return VirtualPatientCase(
            case_id=case_id,
            title=f"{profile.record.disease_name} - {title_suffix}",
            true_disease_phase=None,
            true_conditions=[profile.record.disease_name],
            chief_complaint=chief_complaint,
            behavior_style=DEFAULT_BEHAVIOR_STYLE,
            slot_truth_map=slot_truth_map,
            hidden_slots=[],
            red_flags=[],
            metadata=metadata,
        )


def write_graph_case_outputs(
    result: GraphCaseGenerationResult,
    *,
    output_file: Path,
    manifest_file: Path,
    output_json_file: Path | None = None,
    summary_file: Path | None = None,
) -> None:
    """将生成结果写入 JSONL、JSON、manifest 和可选 Markdown 摘要。"""

    write_cases_jsonl(result.cases, output_file)
    if output_json_file is not None:
        write_cases_json(result.cases, output_json_file)
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(json.dumps(result.manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if summary_file is not None:
        summary_file.parent.mkdir(parents=True, exist_ok=True)
        summary_file.write_text(result.summary_markdown, encoding="utf-8")


def render_generation_summary_markdown(manifest: dict[str, Any]) -> str:
    """将 manifest 渲染成人工可读的 Markdown 摘要。"""

    lines = [
        "# 图谱驱动虚拟病例生成摘要",
        "",
        f"- generated_case_count: {int(manifest.get('generated_case_count') or 0)}",
        f"- valid_disease_report_count: {int(manifest.get('valid_disease_report_count') or 0)}",
        f"- invalid_disease_report_count: {int(manifest.get('invalid_disease_report_count') or 0)}",
        "",
        "## 病例数量",
        "",
        "| case_type | count |",
        "| --- | ---: |",
    ]
    for case_type in CASE_TYPE_ORDER:
        count = int((manifest.get("generated_case_count_by_type") or {}).get(case_type) or 0)
        lines.append(f"| {case_type} | {count} |")

    lines.extend(
        [
            "",
            "## 疾病明细",
            "",
            "| disease_name | generated_case_types | skipped_reasons |",
            "| --- | --- | --- |",
        ]
    )
    for entry in manifest.get("diseases") or []:
        generated = "、".join(entry.get("generated_case_types") or []) or "-"
        skipped = "、".join(skip.get("reason") or "" for skip in entry.get("skipped") or []) or "-"
        lines.append(f"| {entry.get('disease_name') or ''} | {generated} | {skipped} |")

    lines.append("")
    return "\n".join(lines)


def _build_slot_truth(item: dict[str, Any], value: bool) -> SlotTruth:
    """从证据条目构造 SlotTruth。"""

    node_id = str(item.get("target_node_id") or "")
    target_name = str(item.get("target_name") or node_id)
    return SlotTruth(
        node_id=node_id,
        value=value,
        mention_style="direct",
        reveal_only_if_asked=True,
        aliases=[target_name],
    )


def _looks_like_disease_report(payload: dict[str, Any]) -> bool:
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("disease"), dict)
        and isinstance(payload.get("evidence"), list)
        and isinstance(payload.get("group_summary"), dict)
        and isinstance(payload.get("summary"), dict)
    )


def _collect_missing_required_fields(evidence: Sequence[dict[str, Any]]) -> list[str]:
    missing: set[str] = set()
    for item in evidence:
        for field in REQUIRED_EVIDENCE_FIELDS:
            if not _has_required_value(item.get(field)):
                missing.add(field)
    return sorted(missing)


def _has_required_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return len(value.strip()) > 0
    return True


def _normalize_text(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[\s\u3000]+", "", text)
    return text


def _evidence_key(item: dict[str, Any]) -> str:
    group = str(item.get("group") or "").strip()
    name = _normalize_text(str(item.get("target_name") or ""))
    if len(name) > 0:
        return f"{group}::{name}"
    node_id = str(item.get("target_node_id") or "").strip()
    if len(node_id) > 0:
        return node_id
    return group


def _sort_evidence_items(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [dict(item) for item in items],
        key=lambda item: (
            -float(item.get("priority") or 0.0),
            -float(item.get("relation_specificity") or 0.0),
            str(item.get("target_name") or ""),
        ),
    )


def _unique_evidence_items(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for item in _sort_evidence_items(items):
        deduped.setdefault(_evidence_key(item), dict(item))
    return list(deduped.values())


def _limit_unique_items(items: Iterable[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = _evidence_key(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dict(item))
        if len(deduped) >= limit:
            break
    return deduped


def _is_low_cost_evidence(item: dict[str, Any]) -> bool:
    return (
        str(item.get("group") or "") in LOW_COST_GROUPS
        and str(item.get("acquisition_mode") or "") in LOW_COST_ACQUISITION_MODES
        and str(item.get("evidence_cost") or "") != "high"
    )


def _is_chief_complaint_friendly(item: dict[str, Any]) -> bool:
    return _is_low_cost_evidence(item)


def _is_exam_evidence(item: dict[str, Any]) -> bool:
    return (
        str(item.get("group") or "") in EXAM_GROUPS
        or str(item.get("acquisition_mode") or "") in EXAM_ACQUISITION_MODES
    )


def _select_high_value_exam_pool(exam_pool: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    if not exam_pool:
        return []

    sorted_pool = _sort_evidence_items(exam_pool)
    priority_cutoff = _top_half_priority_cutoff(sorted_pool)
    selected = [
        item
        for item in sorted_pool
        if (
            float(item.get("priority") or 0.0) >= priority_cutoff
            or float(item.get("relation_specificity") or 0.0) >= 0.85
            or (
                str(item.get("group") or "") in EXAM_GROUPS
                and str(item.get("relation_type") or "") in HIGH_VALUE_EXAM_RELATION_TYPES
            )
        )
    ]
    return _sort_evidence_items(_unique_evidence_items(selected))


def _top_half_priority_cutoff(items: Sequence[dict[str, Any]]) -> float:
    if not items:
        return 0.0
    sorted_items = _sort_evidence_items(items)
    index = max(math.ceil(len(sorted_items) / 2) - 1, 0)
    return float(sorted_items[index].get("priority") or 0.0)


def _select_chief_complaint_items(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    preferred = sorted(
        items,
        key=lambda item: (
            0 if str(item.get("group") or "") == "symptom" else 1 if str(item.get("group") or "") == "detail" else 2,
            -float(item.get("priority") or 0.0),
            -float(item.get("relation_specificity") or 0.0),
            str(item.get("target_name") or ""),
        ),
    )
    return _limit_unique_items(preferred, limit=3)


def _render_low_cost_complaint(items: Sequence[dict[str, Any]], *, fallback_name: str) -> str:
    if not items:
        return f"最近想咨询一下{fallback_name}相关的情况。"

    natural_items = [item for item in items if str(item.get("group") or "") in CHIEF_COMPLAINT_NATURAL_GROUPS]
    render_items = natural_items or list(items)
    names = [str(item.get("target_name") or "") for item in render_items[:3] if str(item.get("target_name") or "")]
    if not names:
        return f"最近想咨询一下{fallback_name}相关的情况。"

    if all(str(item.get("group") or "") == "risk" for item in render_items[: len(names)]):
        if len(names) == 1:
            return f"最近主要想咨询一下{names[0]}相关的情况。"
        return f"最近主要想咨询一下{names[0]}、{names[1]}相关的情况。"

    if len(names) == 1:
        return f"最近主要是{names[0]}，想来看看是怎么回事。"

    if len(names) == 2:
        return f"最近主要是{names[0]}，还伴有{names[1]}，想来看看是怎么回事。"

    return f"最近主要是{names[0]}、{names[1]}，还有{names[2]}，想来看看是怎么回事。"


def _render_exam_driven_complaint(
    exam_items: Sequence[dict[str, Any]],
    supplement_items: Sequence[dict[str, Any]],
    fallback_name: str,
) -> str:
    exam_names = [str(item.get("target_name") or "") for item in exam_items[:3] if str(item.get("target_name") or "")]
    supplement_names = [
        str(item.get("target_name") or "")
        for item in supplement_items[:1]
        if str(item.get("target_name") or "")
    ]

    if not exam_names:
        return f"最近复查发现一些异常，想进一步确认是不是{fallback_name}。"

    prefix = ""
    if supplement_names:
        prefix = f"最近还有{supplement_names[0]}，"

    if len(exam_names) == 1:
        return f"{prefix}复查发现{exam_names[0]}，想进一步确认原因。"

    if len(exam_names) == 2:
        return f"{prefix}复查发现{exam_names[0]}，而且{exam_names[1]}也有异常，想进一步确认原因。"

    return (
        f"{prefix}复查发现{exam_names[0]}，{exam_names[1]}和{exam_names[2]}也提示异常，"
        "想进一步确认原因。"
    )


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _exam_path_similarity(target: DiseaseProfile, competitor: DiseaseProfile) -> float:
    if (
        not target.exam_name_keys
        and not competitor.exam_name_keys
        and not target.exam_group_keys
        and not competitor.exam_group_keys
    ):
        return 0.0

    name_score = _jaccard(target.exam_name_keys, competitor.exam_name_keys)
    group_score = _jaccard(target.exam_group_keys, competitor.exam_group_keys)
    relation_score = _jaccard(target.exam_relation_keys, competitor.exam_relation_keys)
    return 0.5 * name_score + 0.25 * group_score + 0.25 * relation_score


def _skip_entry(
    *,
    case_type: str,
    reason: str,
    available: int,
    required: int,
) -> dict[str, Any]:
    return {
        "case_type": case_type,
        "reason": reason,
        "available_pool_size": available,
        "required_pool_size": required,
    }


def _build_case_id(
    *,
    case_type: str,
    disease_id: str,
    competitor_id: str,
    case_index: int,
) -> str:
    disease_token = disease_id.replace("merged_node_", "")[:8] or "unknown"
    if case_type != "competitive":
        return f"kg_{case_type}_{disease_token}_{case_index:03d}"

    competitor_token = competitor_id.replace("merged_node_", "")[:8] or "unknown"
    return f"kg_competitive_{disease_token}_vs_{competitor_token}_{case_index:03d}"
