"""根据疾病级图谱审计输出生成图谱驱动的虚拟病人病例。"""

from __future__ import annotations

import json
import math
import random
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .case_schema import SlotTruth, VirtualPatientCase
from .evidence_family_catalog import classify_evidence_families
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
POSITIVE_SLOT_LIMIT = 8
COMPETITIVE_NEGATIVE_SLOT_LIMIT = 3
DEFAULT_BEHAVIOR_STYLE = "cooperative"
DEFAULT_MINIMUM_EVIDENCE_GROUPS_FILE = (
    "test_outputs/evidence_family/disease_evidence_catalog_20260502/disease_minimum_evidence_groups.json"
)
OPENING_RESULT_MARKERS = (
    "阳性",
    "阴性",
    "升高",
    "降低",
    "减少",
    "增多",
    "异常",
    "<",
    ">",
    "≤",
    "≥",
    "检出",
    "提示",
    "磨玻璃影",
    "空洞",
    "结节",
    "浸润",
    "溃疡",
)
BACKGROUND_OPENING_RISK_TERMS = (
    "hiv",
    "aids",
    "艾滋",
    "抗逆转录病毒",
    "art",
    "免疫功能低下",
)
BENCHMARK_QC_ELIGIBLE = "eligible"
BENCHMARK_QC_INELIGIBLE = "ineligible"


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
    minimum_evidence_groups_file: str = DEFAULT_MINIMUM_EVIDENCE_GROUPS_FILE
    minimum_evidence_group_match_by_name: bool = False


@dataclass(frozen=True)
class MinimumEvidenceRequirement:
    """单疾病 full-evidence 最低证据组约束。"""

    disease_id: str
    disease_name: str
    required_groups: list[set[str]]
    required_groups_by_evidence_group: dict[str, list[set[str]]]
    evidence_family_counts: dict[str, int]


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
    required_family_groups: list[set[str]]
    requirement_source: str
    catalog_required_family_groups: list[set[str]]
    catalog_required_family_groups_by_evidence_group: dict[str, list[set[str]]]
    catalog_unavailable_family_groups: list[set[str]]

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


def sample_cases_by_type(
    cases: Sequence[VirtualPatientCase],
    *,
    sample_size_per_type: int = 5,
    seed: int = 42,
    case_types: Sequence[str] | None = None,
) -> dict[str, list[VirtualPatientCase]]:
    """按病例类型分组抽样，便于人工检查。"""

    requested_case_types = tuple(case_types or CASE_TYPE_ORDER)
    rng = random.Random(seed)
    grouped_cases: dict[str, list[VirtualPatientCase]] = {case_type: [] for case_type in requested_case_types}

    for case in cases:
        case_type = str(case.metadata.get("case_type") or "")
        if case_type in grouped_cases:
            grouped_cases[case_type].append(case)

    sampled: dict[str, list[VirtualPatientCase]] = {}
    for case_type in requested_case_types:
        pool = sorted(grouped_cases.get(case_type) or [], key=lambda item: item.case_id)
        if len(pool) < sample_size_per_type:
            raise ValueError(
                f"病例类型 {case_type} 可用数量不足：需要 {sample_size_per_type}，实际只有 {len(pool)}"
            )
        sampled[case_type] = rng.sample(pool, sample_size_per_type)

    return sampled


def build_case_type_sample_payload(
    cases: Sequence[VirtualPatientCase],
    *,
    sample_size_per_type: int = 5,
    seed: int = 42,
    source_file: Path | None = None,
    case_types: Sequence[str] | None = None,
) -> dict[str, Any]:
    """构造按病例类型抽样后的可落盘 JSON 负载。"""

    sampled_cases = sample_cases_by_type(
        cases,
        sample_size_per_type=sample_size_per_type,
        seed=seed,
        case_types=case_types,
    )
    ordered_case_types = tuple(case_types or CASE_TYPE_ORDER)
    return {
        "source_file": str(source_file) if source_file is not None else None,
        "sample_size_per_type": sample_size_per_type,
        "seed": seed,
        "requested_case_types": list(ordered_case_types),
        "available_case_count_by_type": {
            case_type: sum(1 for case in cases if str(case.metadata.get("case_type") or "") == case_type)
            for case_type in ordered_case_types
        },
        "sampled_case_count": sum(len(items) for items in sampled_cases.values()),
        "sampled_cases_by_type": {
            case_type: [asdict(case) for case in sampled_cases.get(case_type) or []]
            for case_type in ordered_case_types
        },
    }


def write_case_type_sample_payload(payload: dict[str, Any], output_file: Path) -> None:
    """将病例类型抽样结果写入 JSON 文件。"""

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_case_type_sample_markdown(payload: dict[str, Any]) -> str:
    """将病例类型抽样结果渲染成人工检查用 Markdown。"""

    requested_case_types = [str(item) for item in payload.get("requested_case_types") or []]
    available_counts = payload.get("available_case_count_by_type") or {}
    sampled_cases_by_type = payload.get("sampled_cases_by_type") or {}

    lines = [
        "# 图谱病例抽样检查摘要",
        "",
        f"- source_file: {payload.get('source_file') or '-'}",
        f"- sample_size_per_type: {int(payload.get('sample_size_per_type') or 0)}",
        f"- seed: {int(payload.get('seed') or 0)}",
        f"- sampled_case_count: {int(payload.get('sampled_case_count') or 0)}",
        "",
        "## 抽样概览",
        "",
        "| case_type | available_count | sampled_count |",
        "| --- | ---: | ---: |",
    ]

    for case_type in requested_case_types:
        sampled_items = sampled_cases_by_type.get(case_type) or []
        lines.append(
            f"| {case_type} | {int(available_counts.get(case_type) or 0)} | {len(sampled_items)} |"
        )

    for case_type in requested_case_types:
        sampled_items = sampled_cases_by_type.get(case_type) or []
        lines.extend(
            [
                "",
                f"## {case_type}",
                "",
            ]
        )

        if not sampled_items:
            lines.extend(["- 无样本", ""])
            continue

        for index, case in enumerate(sampled_items, start=1):
            metadata = case.get("metadata") or {}
            lines.extend(
                [
                    f"### {index}. {case.get('title') or '-'}",
                    "",
                    f"- case_id: `{case.get('case_id') or '-'}`",
                    f"- disease_name: {metadata.get('disease_name') or '-'}",
                    f"- disease_id: `{metadata.get('disease_id') or '-'}`",
                    f"- behavior_style: `{case.get('behavior_style') or '-'}`",
                    f"- chief_complaint_cache: {case.get('chief_complaint') or '-'}",
                    f"- opening_slot_names: {_join_markdown_values(metadata.get('opening_slot_names') or [])}",
                    f"- selected_positive_slots: {_join_markdown_values(metadata.get('selected_positive_slots') or [])}",
                    f"- selected_negative_slots: {_join_markdown_values(metadata.get('selected_negative_slots') or [])}",
                    f"- red_flags: {_join_markdown_values(case.get('red_flags') or [])}",
                    f"- hidden_slots: {_join_markdown_values(case.get('hidden_slots') or [])}",
                    f"- slot_truth_positive: {_join_markdown_values(_collect_slot_truth_names(case, expected_value=True))}",
                    f"- slot_truth_negative: {_join_markdown_values(_collect_slot_truth_names(case, expected_value=False))}",
                    f"- evidence_counts_by_group: {_render_group_count_markdown(metadata.get('evidence_counts_by_group') or {})}",
                    "",
                ]
            )

    return "\n".join(lines)


def write_case_type_sample_markdown(payload: dict[str, Any], output_file: Path) -> None:
    """将病例类型抽样 Markdown 摘要写入文件。"""

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(render_case_type_sample_markdown(payload), encoding="utf-8")


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
        requirements_by_id, requirements_by_name, requirement_catalog_path = _load_minimum_evidence_requirements(
            self.config.minimum_evidence_groups_file
        )
        self.minimum_evidence_requirements_by_id = requirements_by_id
        self.minimum_evidence_requirements_by_name = requirements_by_name
        self.minimum_evidence_requirement_catalog_path = requirement_catalog_path

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
                "benchmark_requirement_source": profile.requirement_source,
                "benchmark_required_family_groups": _serialize_family_groups(profile.required_family_groups),
                "benchmark_catalog_required_family_groups": _serialize_family_groups(
                    profile.catalog_required_family_groups
                ),
                "benchmark_catalog_unavailable_family_groups": _serialize_family_groups(
                    profile.catalog_unavailable_family_groups
                ),
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
        benchmark_qc_count_by_status = _count_cases_by_metadata_value(cases, "benchmark_qc_status")
        benchmark_eligible_count_by_type = {
            case_type: sum(
                1
                for case in cases
                if str(case.metadata.get("case_type") or "") == case_type
                and str(case.metadata.get("benchmark_qc_status") or "") == BENCHMARK_QC_ELIGIBLE
            )
            for case_type in CASE_TYPE_ORDER
        }

        manifest = {
            "audit_root": str(audit_root) if audit_root else "",
            "config": asdict(self.config),
            "minimum_evidence_requirement_catalog": {
                "path": self.minimum_evidence_requirement_catalog_path,
                "requirement_count_by_id": len(self.minimum_evidence_requirements_by_id),
                "requirement_count_by_name": len(self.minimum_evidence_requirements_by_name),
            },
            "generated_case_count": len(cases),
            "generated_case_count_by_type": generated_case_count_by_type,
            "benchmark_qc_count_by_status": benchmark_qc_count_by_status,
            "benchmark_eligible_count_by_type": benchmark_eligible_count_by_type,
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
        available_family_coverage = _collect_evidence_family_coverage(all_evidence)
        (
            required_groups,
            requirement_source,
            catalog_requirement,
            catalog_unavailable_groups,
        ) = self._resolve_required_family_groups(record, available_family_coverage)

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
            required_family_groups=required_groups,
            requirement_source=requirement_source,
            catalog_required_family_groups=(
                list(catalog_requirement.required_groups)
                if catalog_requirement is not None
                else []
            ),
            catalog_required_family_groups_by_evidence_group=(
                {
                    evidence_group: list(groups)
                    for evidence_group, groups in catalog_requirement.required_groups_by_evidence_group.items()
                }
                if catalog_requirement is not None
                else {}
            ),
            catalog_unavailable_family_groups=catalog_unavailable_groups,
        )

    def _resolve_required_family_groups(
        self,
        record: DiseaseAuditRecord,
        available_family_coverage: set[str],
    ) -> tuple[list[set[str]], str, MinimumEvidenceRequirement | None, list[set[str]]]:
        """优先使用 full-evidence catalog，缺失时回退到内置疾病大类规则。"""

        catalog_requirement = self.minimum_evidence_requirements_by_id.get(record.disease_id)
        if catalog_requirement is None and self.config.minimum_evidence_group_match_by_name:
            catalog_requirement = self.minimum_evidence_requirements_by_name.get(_normalize_text(record.disease_name))

        if catalog_requirement is not None and catalog_requirement.required_groups:
            available_groups, unavailable_groups = _split_required_groups_by_available_families(
                catalog_requirement.required_groups,
                available_family_coverage,
            )
            if available_groups:
                return available_groups, "catalog", catalog_requirement, unavailable_groups

        builtin_required_groups = _benchmark_required_family_groups(record.disease_name)
        if builtin_required_groups:
            return builtin_required_groups, "builtin", catalog_requirement, []
        return [], "none", catalog_requirement, []

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
        positive_items = _select_requirement_covering_items(
            preferred_items=[*chief_pool, *profile.exam_high_value_pool[:1]],
            fallback_items=profile.all_evidence,
            required_groups=profile.required_family_groups,
            limit=POSITIVE_SLOT_LIMIT,
        )
        return self._build_case(
            profile=profile,
            case_type="ordinary",
            chief_complaint=chief_text,
            opening_items=chief_items,
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

        positive_items = _select_requirement_covering_items(
            preferred_items=pool,
            fallback_items=pool,
            required_groups=profile.required_family_groups,
            limit=POSITIVE_SLOT_LIMIT,
        )
        chief_text = _render_low_cost_complaint(positive_items[:3], fallback_name=profile.record.disease_name)
        return self._build_case(
            profile=profile,
            case_type="low_cost",
            chief_complaint=chief_text,
            opening_items=positive_items[:3],
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
        positive_items = _select_requirement_covering_items(
            preferred_items=[*exam_selected, *supplement],
            fallback_items=profile.all_evidence,
            required_groups=profile.required_family_groups,
            limit=POSITIVE_SLOT_LIMIT,
        )
        chief_text = _render_exam_driven_complaint(exam_selected[:3], supplement[:1], profile.record.disease_name)
        return self._build_case(
            profile=profile,
            case_type="exam_driven",
            chief_complaint=chief_text,
            opening_items=_limit_unique_items([*supplement, *exam_selected], limit=3),
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
            positive_items = _select_requirement_covering_items(
                preferred_items=[*shared_items, *target_only_items],
                fallback_items=candidate.target.all_evidence,
                required_groups=profile.required_family_groups,
                limit=POSITIVE_SLOT_LIMIT,
            )
            negative_items = _limit_unique_items(
                _filter_incompatible_negative_items(
                    profile=profile,
                    positive_items=positive_items,
                    negative_items=candidate.competitor_only_negative,
                ),
                limit=COMPETITIVE_NEGATIVE_SLOT_LIMIT,
            )
            opening_items = _select_competitive_opening_items(
                shared_items=shared_items,
                target_only_items=target_only_items,
                positive_items=positive_items,
            )
            chief_text = _render_low_cost_complaint(opening_items, fallback_name=profile.record.disease_name)
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
                    opening_items=opening_items,
                    positive_items=positive_items,
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
        opening_items: Sequence[dict[str, Any]],
        positive_items: Sequence[dict[str, Any]],
        negative_items: Sequence[dict[str, Any]],
        metadata_extra: dict[str, Any],
        title_suffix: str,
        case_index: int,
    ) -> VirtualPatientCase:
        """将选中的证据列表渲染成 VirtualPatientCase。"""

        positive_items = _filter_conflicting_positive_items(positive_items)
        negative_items = _filter_incompatible_negative_items(
            profile=profile,
            positive_items=positive_items,
            negative_items=negative_items,
        )
        opening_items = _filter_opening_items(opening_items, positive_items)
        qc_summary = _build_benchmark_qc_summary(
            disease_name=profile.record.disease_name,
            positive_items=positive_items,
            negative_items=negative_items,
            required_groups=profile.required_family_groups,
            requirement_source=profile.requirement_source,
            catalog_required_groups=profile.catalog_required_family_groups,
            catalog_required_groups_by_evidence_group=profile.catalog_required_family_groups_by_evidence_group,
            catalog_unavailable_groups=profile.catalog_unavailable_family_groups,
        )
        slot_truth_map: dict[str, SlotTruth] = {}
        opening_node_ids = [
            str(item.get("target_node_id") or "")
            for item in opening_items
            if str(item.get("target_node_id") or "")
        ]
        opening_node_id_set = set(opening_node_ids)
        for item in positive_items:
            node_id = str(item.get("target_node_id") or "")
            if len(node_id) == 0:
                continue
            truth = _build_slot_truth(item, True)
            if node_id in opening_node_id_set:
                truth.reveal_only_if_asked = False
            slot_truth_map[node_id] = truth

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
            "opening_slot_ids": opening_node_ids,
            "opening_slot_names": [str(item.get("target_name") or "") for item in opening_items],
            "selected_positive_slots": [str(item.get("target_name") or "") for item in positive_items],
            "selected_negative_slots": [str(item.get("target_name") or "") for item in negative_items],
            "evidence_counts_by_group": profile.evidence_counts_by_group,
            **qc_summary,
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
            "## Benchmark QC",
            "",
            f"- minimum_evidence_requirement_catalog: {(manifest.get('minimum_evidence_requirement_catalog') or {}).get('path') or '-'}",
            f"- benchmark_qc_count_by_status: {manifest.get('benchmark_qc_count_by_status') or {}}",
            "",
            "| case_type | eligible_count |",
            "| --- | ---: |",
        ]
    )
    for case_type in CASE_TYPE_ORDER:
        count = int((manifest.get("benchmark_eligible_count_by_type") or {}).get(case_type) or 0)
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


def _join_markdown_values(values: Sequence[Any]) -> str:
    items = [str(value).strip() for value in values if str(value).strip()]
    return "、".join(items) if items else "-"


def _collect_slot_truth_names(case: dict[str, Any], *, expected_value: bool) -> list[str]:
    names: list[str] = []
    slot_truth_map = case.get("slot_truth_map") or {}
    if not isinstance(slot_truth_map, dict):
        return names

    for truth in slot_truth_map.values():
        if not isinstance(truth, dict):
            continue
        if truth.get("value") is not expected_value:
            continue
        aliases = truth.get("aliases") or []
        alias_names = [str(alias).strip() for alias in aliases if str(alias).strip()]
        if alias_names:
            names.append(alias_names[0])
            continue
        node_id = str(truth.get("node_id") or "").strip()
        if node_id:
            names.append(node_id)

    return names


def _render_group_count_markdown(group_counts: dict[str, Any]) -> str:
    if not isinstance(group_counts, dict):
        return "-"
    parts = []
    for group in STANDARD_GROUPS:
        value = int(group_counts.get(group) or 0)
        if value <= 0:
            continue
        parts.append(f"{group}={value}")
    return "，".join(parts) if parts else "-"


def _count_cases_by_metadata_value(cases: Sequence[VirtualPatientCase], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in cases:
        value = str(case.metadata.get(key) or "")
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _load_minimum_evidence_requirements(
    path_value: str,
) -> tuple[dict[str, MinimumEvidenceRequirement], dict[str, MinimumEvidenceRequirement], str]:
    """读取 full-evidence catalog 导出的疾病最低证据组。"""

    if not str(path_value or "").strip():
        return {}, {}, ""

    path = Path(str(path_value)).expanduser()
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[1] / path

    if not path.exists():
        return {}, {}, str(path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        raw_items = payload.get("items") or payload.get("diseases") or []
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []

    by_id: dict[str, MinimumEvidenceRequirement] = {}
    by_name: dict[str, MinimumEvidenceRequirement] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue

        requirement = MinimumEvidenceRequirement(
            disease_id=str(item.get("disease_id") or ""),
            disease_name=str(item.get("disease_name") or ""),
            required_groups=_parse_family_groups(item.get("minimum_evidence_groups") or []),
            required_groups_by_evidence_group={
                str(evidence_group): _parse_family_groups(groups)
                for evidence_group, groups in (item.get("minimum_evidence_groups_by_evidence_group") or {}).items()
            },
            evidence_family_counts={
                str(family): int(count or 0)
                for family, count in (item.get("evidence_family_counts") or {}).items()
            },
        )
        if requirement.disease_id and requirement.required_groups:
            by_id.setdefault(requirement.disease_id, requirement)
        normalized_name = _normalize_text(requirement.disease_name)
        if normalized_name and requirement.required_groups:
            by_name.setdefault(normalized_name, requirement)

    return by_id, by_name, str(path)


def _parse_family_groups(value: Any) -> list[set[str]]:
    """把 JSON 中的 family group 解析成 list[set[str]]。"""

    parsed: list[set[str]] = []
    if not isinstance(value, list):
        return parsed

    for group in value:
        if isinstance(group, str):
            family_group = {group.strip()} if group.strip() else set()
        elif isinstance(group, list):
            family_group = {str(item).strip() for item in group if str(item).strip()}
        else:
            family_group = set()
        if family_group:
            parsed.append(family_group)
    return parsed


def _serialize_family_groups(groups: Sequence[set[str]]) -> list[list[str]]:
    return [sorted(group) for group in groups if group]


def _serialize_family_groups_by_evidence_group(
    groups_by_evidence_group: dict[str, Sequence[set[str]]],
) -> dict[str, list[list[str]]]:
    return {
        str(evidence_group): _serialize_family_groups(groups)
        for evidence_group, groups in sorted(groups_by_evidence_group.items())
    }


def _build_slot_truth(item: dict[str, Any], value: bool) -> SlotTruth:
    """从证据条目构造 SlotTruth。"""

    node_id = str(item.get("target_node_id") or "")
    target_name = str(item.get("target_name") or node_id)
    return SlotTruth(
        node_id=node_id,
        value=value,
        group=str(item.get("group") or ""),
        node_label=str(item.get("target_label") or ""),
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


def _iter_string_values(value: Any) -> Iterable[str]:
    """递归展开 item 中可能参与规则判断的字符串值。"""

    if value is None:
        return
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            yield stripped
        return
    if isinstance(value, dict):
        for nested_value in value.values():
            yield from _iter_string_values(nested_value)
        return
    if isinstance(value, (list, tuple, set)):
        for nested_value in value:
            yield from _iter_string_values(nested_value)
        return
    if isinstance(value, (int, float)):
        yield str(value)


def _item_search_text(item: dict[str, Any]) -> str:
    """拼接证据名称、标签和附加属性，供 lightweight 规则匹配使用。"""

    parts: list[str] = []
    for key in (
        "target_name",
        "target_label",
        "test_id",
        "question_type_hint",
        "status_label",
        "attributes",
    ):
        parts.extend(_iter_string_values(item.get(key)))
    return _normalize_text(" ".join(parts))


def _parse_numeric_token(token: str) -> float | None:
    token = token.strip()
    sci_match = re.fullmatch(r"10\^(\d+)", token)
    if sci_match:
        return float(10 ** int(sci_match.group(1)))
    try:
        return float(token)
    except ValueError:
        return None


def _extract_measurement_comparators(text: str) -> list[tuple[str, float]]:
    matches: list[tuple[str, float]] = []
    for operator, token in re.findall(r"(<=|>=|<|>|≤|≥)\s*(10\^\d+|\d+(?:\.\d+)?)", text):
        value = _parse_numeric_token(token)
        if value is None:
            continue
        matches.append((operator, value))
    return matches


def _extract_first_range(text: str) -> tuple[float, float] | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|–|~|至)\s*(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    lower = _parse_numeric_token(match.group(1))
    upper = _parse_numeric_token(match.group(2))
    if lower is None or upper is None:
        return None
    return (lower, upper)


def _has_concrete_result_markers(name: str) -> bool:
    normalized_name = _normalize_text(name)
    if any(marker in name for marker in OPENING_RESULT_MARKERS):
        return True
    return any(_normalize_text(marker) in normalized_name for marker in OPENING_RESULT_MARKERS)


def _is_plain_cd4_test_name(name: str) -> bool:
    normalized_name = _normalize_text(name)
    plain_names = {
        "cd4",
        "cd4+",
        "cd4+t淋巴细胞计数",
        "cd4+t细胞计数",
        "cd4细胞计数",
        "cd4计数",
        "基线cd4+t淋巴细胞计数",
    }
    return normalized_name in plain_names


def _cd4_family_rank(item: dict[str, Any]) -> tuple[int, float, int]:
    """CD4 family 内优先保留更具体且更严重的阈值。"""

    name = str(item.get("target_name") or "")
    comparators = _extract_measurement_comparators(name)
    if comparators:
        operator, value = comparators[-1]
        if operator in {"<", "<=", "≤"}:
            return (0, value, 0)
        if operator in {">", ">=", "≥"}:
            return (1, -value, 0)

    range_values = _extract_first_range(name)
    if range_values is not None:
        lower, upper = range_values
        return (2, -lower, int(upper - lower))

    normalized_name = _normalize_text(name)
    if "持续低于" in normalized_name or "显著降低" in normalized_name or "降低" in normalized_name:
        return (3, 0.0, 0)
    if "迅速升高" in normalized_name or "升高" in normalized_name:
        return (4, 0.0, 0)
    if _is_plain_cd4_test_name(name):
        return (6, 0.0, 0)
    if _has_concrete_result_markers(name):
        return (5, 0.0, 0)
    return (7, 0.0, 0)


def _numeric_family_rank(
    item: dict[str, Any],
    *,
    prefer_higher_for_greater_than: bool = True,
    prefer_lower_for_less_than: bool = True,
    plain_test_names: set[str] | None = None,
    generic_state_terms: Sequence[str] | None = None,
) -> tuple[int, float, int]:
    """为一般 numeric family 生成排序键：阈值优先，随后是明确状态，再次是纯检查名。"""

    name = str(item.get("target_name") or "")
    normalized_name = _normalize_text(name)
    comparators = _extract_measurement_comparators(name)
    if comparators:
        operator, value = comparators[-1]
        if operator in {"<", "<=", "≤"}:
            return (0, value if prefer_lower_for_less_than else -value, 0)
        if operator in {">", ">=", "≥"}:
            return (0, -value if prefer_higher_for_greater_than else value, 0)

    range_values = _extract_first_range(name)
    if range_values is not None:
        lower, upper = range_values
        return (1, -lower, int(upper - lower))

    if generic_state_terms:
        for index, term in enumerate(generic_state_terms, start=1):
            if _normalize_text(term) in normalized_name:
                return (2, float(index), 0)

    if _has_concrete_result_markers(name):
        return (2, 99.0, 0)

    if plain_test_names and normalized_name in plain_test_names:
        return (4, 0.0, 0)

    return (5, 0.0, 0)


def _bmi_family_rank(item: dict[str, Any]) -> tuple[int, float, int]:
    """BMI 分层优先保留更高分层的区间或阈值。"""

    name = str(item.get("target_name") or "")
    normalized_name = _normalize_text(name)
    range_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:<=|<|≤)\s*(?:bmi|身体质量指数)\s*(?:<|<=|≤)\s*(\d+(?:\.\d+)?)", normalized_name)
    if range_match:
        lower = _parse_numeric_token(range_match.group(1))
        upper = _parse_numeric_token(range_match.group(2))
        if lower is not None and upper is not None:
            return (0, -lower, -int(upper * 100))

    comparators = _extract_measurement_comparators(name)
    if comparators:
        operator, value = comparators[-1]
        if operator in {">", ">=", "≥"}:
            return (0, -value, 0)
        if operator in {"<", "<=", "≤"}:
            return (1, value, 0)

    if _has_concrete_result_markers(name):
        return (2, 0.0, 0)

    return (3, 0.0, 0)


def _infer_conflict_family(item: dict[str, Any]) -> str:
    """推断阳性证据所属的互斥分层族；空字符串表示不参与互斥过滤。"""

    search_text = _item_search_text(item)
    if not search_text:
        return ""

    if "bmi" in search_text or "身体质量指数" in search_text or "kg/m²" in search_text or "kg/m2" in search_text:
        return "bmi"
    if "cd4" in search_text:
        return "cd4"
    if "hivrna" in search_text or "hiv-1rna" in search_text or "病毒载量" in search_text:
        return "hiv_rna"

    if "高密度脂蛋白" in search_text or "hdl" in search_text:
        return "hdl"
    if "低密度脂蛋白" in search_text or "ldl" in search_text:
        return "ldl"
    if "甘油三酯" in search_text or "tg" in search_text:
        return "triglyceride"
    if "总胆固醇" in search_text:
        return "total_cholesterol"
    if "egfr" in search_text or "肾小球滤过率" in search_text:
        return "egfr"
    if "年龄" in search_text:
        return "age"

    return ""


def _conflict_item_sort_key(item: dict[str, Any]) -> tuple[int, float, int, float, float, int, str]:
    """互斥证据保留顺序：priority 更高，其次 specificity 更高，再次名称更具体。"""

    name = str(item.get("target_name") or "")
    family = _infer_conflict_family(item)

    if family == "cd4":
        family_rank = _cd4_family_rank(item)
    elif family == "hiv_rna":
        family_rank = _numeric_family_rank(
            item,
            plain_test_names={"hivrna", "hiv-1rna", "hiv病毒载量", "病毒载量", "血浆hiv-1rna"},
            generic_state_terms=("低于检测下限", "完全抑制", "未受抑制", "阳性", "高", "低"),
        )
    elif family == "ldl":
        family_rank = _numeric_family_rank(
            item,
            plain_test_names={"ldl-c", "低密度脂蛋白胆固醇"},
            generic_state_terms=("升高", "降低"),
        )
    elif family == "hdl":
        family_rank = _numeric_family_rank(
            item,
            prefer_higher_for_greater_than=False,
            plain_test_names={"hdl", "高密度脂蛋白胆固醇"},
            generic_state_terms=("降低", "升高"),
        )
    elif family == "triglyceride":
        family_rank = _numeric_family_rank(
            item,
            plain_test_names={"甘油三酯", "tg"},
            generic_state_terms=("升高", "降低"),
        )
    elif family == "total_cholesterol":
        family_rank = _numeric_family_rank(
            item,
            plain_test_names={"总胆固醇"},
            generic_state_terms=("升高", "降低"),
        )
    elif family == "egfr":
        family_rank = _numeric_family_rank(
            item,
            prefer_lower_for_less_than=True,
            plain_test_names={"egfr", "肾小球滤过率"},
            generic_state_terms=("降低",),
        )
    elif family == "bmi":
        family_rank = _bmi_family_rank(item)
    elif family == "age":
        family_rank = _numeric_family_rank(item, plain_test_names={"年龄"})
    else:
        family_rank = (9, 0.0, 0)

    return (
        family_rank[0],
        family_rank[1],
        family_rank[2],
        -float(item.get("priority") or 0.0),
        -float(item.get("relation_specificity") or 0.0),
        -len(name),
        name,
    )


def _is_disallowed_opening_name(name: str, item: dict[str, Any]) -> bool:
    """判断证据名称是否属于不适合患者主动开场的槽位。"""

    normalized_name = _normalize_text(name)
    group = str(item.get("group") or "")
    target_label = str(item.get("target_label") or "")

    if target_label == "LabTest":
        return True
    if target_label == "Pathogen" or group == "pathogen":
        return True
    if target_label == "PopulationGroup":
        return True
    if target_label == "ClinicalAttribute" or group == "detail":
        return True

    if "年龄" in normalized_name:
        return True
    if "bmi" in normalized_name or "身体质量指数" in normalized_name or "kg/m²" in normalized_name or "kg/m2" in normalized_name:
        return True
    if "测量部位" in normalized_name:
        return True
    if "持续时间" in normalized_name:
        return True

    if normalized_name in {"异常", "感染", "检查", "筛查", "诊断", "临床症状无改善"}:
        return True
    if group == "risk" and any(term in normalized_name for term in BACKGROUND_OPENING_RISK_TERMS):
        return True

    plain_exam_names = {
        "cd4",
        "cd4+",
        "cd4+t淋巴细胞计数",
        "cd4+t细胞计数",
        "cd4细胞计数",
        "基线cd4+t淋巴细胞计数",
        "cd4计数",
        "hivrna",
        "hiv病毒载量检测",
        "cmvdna检测",
        "胸部ct",
        "血常规",
        "骨密度",
        "病毒载量",
        "脑脊液检查",
        "血脂检测",
        "骨髓培养",
        "隐球菌抗原检测",
    }
    if normalized_name in plain_exam_names:
        return True

    forbidden_finding_terms = (
        "病死率",
        "疗效",
        "耐受性",
        "无改善",
        "体征无改善",
        "无症状",
        "呼吸频率",
        "收缩压",
        "舒张压",
        "胃镜",
        "肠镜",
        "眼底",
        "筛查",
        "统计",
        "风险等级",
    )
    if any(term in name for term in forbidden_finding_terms):
        return True

    if group in {"lab", "imaging"} and not _looks_like_concrete_exam_result(name):
        return True

    return False


def _looks_like_concrete_exam_result(name: str) -> bool:
    """判断是否像“可以直接说给医生听”的具体检查结果，而不是纯检查项目名。"""

    raw_name = name.strip()
    normalized_name = _normalize_text(raw_name)
    if not raw_name:
        return False

    if _has_concrete_result_markers(raw_name):
        return True

    if re.search(r"(?:<=|>=|<|>|≤|≥)", raw_name):
        return True

    if normalized_name in {
        "cd4+t淋巴细胞计数",
        "cd4计数",
        "hivrna",
        "病毒载量",
        "胸部ct",
        "血常规",
        "骨密度",
    }:
        return False

    return False


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


def _select_requirement_covering_items(
    *,
    preferred_items: Sequence[dict[str, Any]],
    fallback_items: Sequence[dict[str, Any]],
    required_groups: Sequence[set[str]] | None = None,
    disease_name: str = "",
    limit: int,
) -> list[dict[str, Any]]:
    """优先覆盖疾病族最低证据要求，再按原排序补满阳性槽位。"""

    active_required_groups = list(required_groups or _benchmark_required_family_groups(disease_name))
    if not active_required_groups:
        return _limit_unique_items(preferred_items, limit=limit)

    candidate_pool = _sort_evidence_items(_unique_evidence_items([*preferred_items, *fallback_items]))
    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()

    # 先逐个覆盖要求族；每个 requirement group 内任一 family 命中即可。
    for required_group in active_required_groups:
        if _items_cover_family_group(selected, required_group):
            continue

        candidate = _best_item_for_required_group(candidate_pool, selected_keys, required_group)
        if candidate is None:
            continue

        selected.append(candidate)
        selected_keys.add(_evidence_key(candidate))
        if len(selected) >= limit:
            return _filter_conflicting_positive_items(selected)[:limit]

    # 再按原有偏好顺序补满，保留 shared / opening 友好证据的自然性。
    for item in [*preferred_items, *fallback_items]:
        key = _evidence_key(item)
        if key in selected_keys:
            continue
        selected.append(dict(item))
        selected_keys.add(key)
        if len(selected) >= limit:
            break

    filtered = _filter_conflicting_positive_items(selected)

    # 互斥 family 过滤后若打掉了某个 required family，再尝试用候选池补回。
    if len(filtered) < limit:
        filtered_keys = {_evidence_key(item) for item in filtered}
        missing_required_groups = _missing_required_family_groups(
            active_required_groups,
            _collect_evidence_family_coverage(filtered),
        )
        for required_group in missing_required_groups:
            candidate = _best_item_for_required_group(candidate_pool, filtered_keys, set(required_group))
            if candidate is None:
                continue
            filtered.append(candidate)
            filtered_keys.add(_evidence_key(candidate))
            filtered = _filter_conflicting_positive_items(filtered)
            if len(filtered) >= limit:
                break

    return _limit_unique_items(filtered, limit=limit)


def _best_item_for_required_group(
    candidate_pool: Sequence[dict[str, Any]],
    selected_keys: set[str],
    required_group: set[str],
) -> dict[str, Any] | None:
    matches = [
        dict(item)
        for item in candidate_pool
        if _evidence_key(item) not in selected_keys and _infer_evidence_families(item) & required_group
    ]
    if not matches:
        return None
    return min(matches, key=_required_family_candidate_sort_key)


def _required_family_candidate_sort_key(item: dict[str, Any]) -> tuple[int, int, int, int, float, float, str]:
    name = str(item.get("target_name") or "")
    normalized_name = _normalize_text(name)
    label = str(item.get("target_label") or "")
    relation_type = str(item.get("relation_type") or "")
    semantic_rank = 0
    if any(term in normalized_name for term in ("βd葡聚糖", "bdg", "葡聚糖", "g试验")):
        semantic_rank = -2
    elif any(term in normalized_name for term in ("肺孢子", "pcp", "病原", "抗原阳性", "培养阳性", "dna阳性", "rna阳性")):
        semantic_rank = -1
    elif any(term in normalized_name for term in ("乳酸脱氢酶", "ldh")):
        semantic_rank = 1
    concrete_rank = 0 if (_has_concrete_result_markers(name) or _extract_measurement_comparators(name)) else 1
    label_rank = 0 if label in {"LabFinding", "ImagingFinding", "Pathogen"} else 1
    relation_rank = 0 if relation_type in HIGH_VALUE_EXAM_RELATION_TYPES else 1
    return (
        semantic_rank,
        concrete_rank,
        label_rank,
        relation_rank,
        -float(item.get("priority") or 0.0),
        -float(item.get("relation_specificity") or 0.0),
        name,
    )


def _items_cover_family_group(items: Sequence[dict[str, Any]], required_group: set[str]) -> bool:
    return bool(_collect_evidence_family_coverage(items) & required_group)


def _build_benchmark_qc_summary(
    *,
    disease_name: str,
    positive_items: Sequence[dict[str, Any]],
    negative_items: Sequence[dict[str, Any]],
    required_groups: Sequence[set[str]] | None = None,
    requirement_source: str = "builtin",
    catalog_required_groups: Sequence[set[str]] | None = None,
    catalog_required_groups_by_evidence_group: dict[str, Sequence[set[str]]] | None = None,
    catalog_unavailable_groups: Sequence[set[str]] | None = None,
) -> dict[str, Any]:
    active_required_groups = list(required_groups or _benchmark_required_family_groups(disease_name))
    positive_coverage = _collect_evidence_family_coverage(positive_items)
    negative_coverage = _collect_evidence_family_coverage(negative_items)
    missing_groups = _missing_required_family_groups(active_required_groups, positive_coverage)

    return {
        "benchmark_qc_status": BENCHMARK_QC_ELIGIBLE if not missing_groups else BENCHMARK_QC_INELIGIBLE,
        "benchmark_requirement_source": requirement_source,
        "benchmark_required_family_groups": _serialize_family_groups(active_required_groups),
        "benchmark_required_family_group_count": len(active_required_groups),
        "benchmark_missing_family_groups": missing_groups,
        "evidence_family_coverage": sorted(positive_coverage),
        "negative_evidence_family_coverage": sorted(negative_coverage),
        "benchmark_catalog_required_family_groups": _serialize_family_groups(catalog_required_groups or []),
        "benchmark_catalog_required_family_groups_by_evidence_group": _serialize_family_groups_by_evidence_group(
            catalog_required_groups_by_evidence_group or {}
        ),
        "benchmark_catalog_unavailable_family_groups": _serialize_family_groups(catalog_unavailable_groups or []),
    }


def _benchmark_required_family_groups(disease_name: str) -> list[set[str]]:
    """按疾病大类定义 benchmark 可判定性所需的最低证据族。"""

    normalized_name = _normalize_text(disease_name)

    if "肺孢子" in normalized_name or "pcp" in normalized_name:
        return [
            {"respiratory_symptom"},
            {"immune_status"},
            {"imaging"},
            {"oxygenation"},
            {"fungal_marker", "pathogen"},
        ]

    if "免疫重建炎症综合征" in normalized_name or "iris" in normalized_name:
        return [
            {"underlying_infection", "immune_status"},
            {"art_or_reconstitution"},
            {"worsening"},
            {"imaging", "disease_specific_lab", "pathogen"},
        ]

    if any(term in normalized_name for term in ("脑炎", "脑膜炎", "脑膜脑炎", "cns", "中枢神经")):
        return [
            {"neurologic_symptom"},
            {"pathogen", "disease_specific_lab"},
            {"imaging", "cns_lab"},
        ]

    if any(term in normalized_name for term in ("肥胖", "血脂异常", "糖尿病", "代谢")):
        return [
            {"metabolic_definition"},
        ]

    return []


def _missing_required_family_groups(
    required_groups: Sequence[set[str]],
    coverage: set[str],
) -> list[list[str]]:
    return [sorted(group) for group in required_groups if not coverage.intersection(group)]


def _split_required_groups_by_available_families(
    required_groups: Sequence[set[str]],
    available_family_coverage: set[str],
) -> tuple[list[set[str]], list[set[str]]]:
    """将 catalog 要求分成当前审计证据池可覆盖与不可覆盖两类。"""

    available_groups: list[set[str]] = []
    unavailable_groups: list[set[str]] = []
    for required_group in required_groups:
        if required_group.intersection(available_family_coverage):
            available_groups.append(set(required_group))
        else:
            unavailable_groups.append(set(required_group))
    return available_groups, unavailable_groups


def _collect_evidence_family_coverage(items: Sequence[dict[str, Any]]) -> set[str]:
    coverage: set[str] = set()
    for item in items:
        coverage.update(_infer_evidence_families(item))
    return coverage


def _infer_evidence_families(item: dict[str, Any]) -> set[str]:
    """把图谱证据归入可审计的 benchmark evidence family。"""

    return set(classify_evidence_families(item))


def _filter_incompatible_negative_items(
    *,
    profile: DiseaseProfile,
    positive_items: Sequence[dict[str, Any]],
    negative_items: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """过滤会把目标病核心定义或已选阳性家族误写成阴性的竞争证据。"""

    target_name = profile.record.disease_name
    positive_keys = {_evidence_key(item) for item in positive_items}
    positive_conflict_families = {_infer_conflict_family(item) for item in positive_items}
    positive_conflict_families.discard("")
    filtered: list[dict[str, Any]] = []

    for item in negative_items:
        key = _evidence_key(item)
        if key in positive_keys:
            continue
        if _negative_name_conflicts_with_target(target_name, item):
            continue
        conflict_family = _infer_conflict_family(item)
        if conflict_family and conflict_family in positive_conflict_families:
            continue
        if _negative_family_conflicts_with_target_definition(target_name, item, profile.required_family_groups):
            continue
        filtered.append(dict(item))

    return filtered


def _negative_name_conflicts_with_target(disease_name: str, item: dict[str, Any]) -> bool:
    target = _normalize_text(disease_name)
    name = _normalize_text(str(item.get("target_name") or ""))
    if len(target) < 2 or len(name) < 2:
        return False
    return target in name or name in target


def _negative_family_conflicts_with_target_definition(
    disease_name: str,
    item: dict[str, Any],
    required_groups: Sequence[set[str]] | None = None,
) -> bool:
    disease_requirements = list(required_groups or _benchmark_required_family_groups(disease_name))
    if not disease_requirements:
        return False
    disease_requirement_families = set().union(*disease_requirements)
    item_families = _infer_evidence_families(item)

    if "metabolic_definition" in disease_requirement_families and "metabolic_definition" in item_families:
        return True

    return False


def _is_low_cost_evidence(item: dict[str, Any]) -> bool:
    return (
        str(item.get("group") or "") in LOW_COST_GROUPS
        and str(item.get("acquisition_mode") or "") in LOW_COST_ACQUISITION_MODES
        and str(item.get("evidence_cost") or "") != "high"
    )


def _is_chief_complaint_friendly(item: dict[str, Any]) -> bool:
    return _is_low_cost_evidence(item)


def _filter_conflicting_positive_items(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """过滤同一互斥分层族中的重复阳性证据，只保留一个。"""

    item_by_key = {_evidence_key(item): dict(item) for item in items}
    best_keys_by_family: dict[str, str] = {}
    for item in items:
        family = _infer_conflict_family(item)
        if not family:
            continue

        current_best_key = best_keys_by_family.get(family)
        candidate_key = _evidence_key(item)
        if current_best_key is None:
            best_keys_by_family[family] = candidate_key
            continue

        current_best_item = item_by_key[current_best_key]
        if _conflict_item_sort_key(item) < _conflict_item_sort_key(current_best_item):
            best_keys_by_family[family] = candidate_key

    filtered: list[dict[str, Any]] = []
    emitted_families: set[str] = set()
    for item in items:
        family = _infer_conflict_family(item)
        if not family:
            filtered.append(dict(item))
            continue

        if family in emitted_families:
            continue
        if _evidence_key(item) != best_keys_by_family.get(family):
            continue

        filtered.append(dict(item))
        emitted_families.add(family)

    return filtered


def _filter_opening_items(
    opening_items: Sequence[dict[str, Any]],
    positive_items: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """过滤不适合患者主动暴露的 opening 证据，并在必要时兜底。"""

    positive_item_by_key = {_evidence_key(item): dict(item) for item in positive_items}
    filtered_opening_items: list[dict[str, Any]] = []
    for item in opening_items:
        key = _evidence_key(item)
        positive_item = positive_item_by_key.get(key)
        if positive_item is None:
            continue
        if not _is_opening_eligible(positive_item):
            continue
        filtered_opening_items.append(positive_item)

    filtered_opening_items = _limit_unique_items(filtered_opening_items, limit=3)
    if filtered_opening_items:
        return filtered_opening_items

    fallback_groups = [
        lambda item: str(item.get("group") or "") == "symptom",
        lambda item: (
            str(item.get("group") or "") == "risk"
            and str(item.get("acquisition_mode") or "") in LOW_COST_ACQUISITION_MODES
        ),
        lambda item: (
            str(item.get("group") or "") in {"lab", "imaging"}
            and _looks_like_concrete_exam_result(str(item.get("target_name") or ""))
        ),
    ]
    fallback_items: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for matcher in fallback_groups:
        for item in positive_items:
            key = _evidence_key(item)
            if key in seen_keys:
                continue
            if not _is_opening_eligible(item):
                continue
            if not matcher(item):
                continue
            fallback_items.append(dict(item))
            seen_keys.add(key)
            if len(fallback_items) >= 3:
                return fallback_items

    return fallback_items


def _select_competitive_opening_items(
    *,
    shared_items: Sequence[dict[str, Any]],
    target_only_items: Sequence[dict[str, Any]],
    positive_items: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """为竞争病例优先挑选自然开场项，避免把 HIV/ART 背景信息当主诉。"""

    shared_opening_items = _filter_opening_items(shared_items, positive_items)
    if len(shared_opening_items) >= 2:
        return shared_opening_items

    supplement_items = _filter_opening_items(target_only_items, positive_items)
    return _limit_unique_items([*shared_opening_items, *supplement_items], limit=3)


def _is_opening_eligible(item: dict[str, Any]) -> bool:
    """判断某条证据是否适合在患者首轮开场中主动暴露。"""

    name = str(item.get("target_name") or "").strip()
    if not name:
        return False

    if _is_disallowed_opening_name(name, item):
        return False

    group = str(item.get("group") or "")
    acquisition_mode = str(item.get("acquisition_mode") or "")
    if group == "symptom":
        return True

    if group == "risk" and acquisition_mode in LOW_COST_ACQUISITION_MODES:
        return True

    if group in {"lab", "imaging"} and _looks_like_concrete_exam_result(name):
        return True

    return False


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
