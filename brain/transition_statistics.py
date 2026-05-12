"""负责构建统计版回答转移概率表，并提供 belief mixture 辅助工具。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from typing import Any, Iterable, Sequence

from .types import HypothesisCandidate, HypothesisScore, MctsAction


# rollout 当前仍主要消费三类普通回答分支。
VERIFY_OUTCOMES = ("present", "absent", "unclear")

# 检查上下文动作额外区分“做过没做过”。
EXAM_AVAILABILITY_OUTCOMES = ("done", "not_done", "unclear")
EXAM_RESULT_OUTCOMES = ("positive", "negative", "unclear")

QUESTION_TYPE_HINTS = ("symptom", "risk", "detail", "lab", "imaging", "pathogen", "exam_context")
EXAM_KINDS = ("general", "lab", "imaging", "pathogen")

# 当动作本身拿不到 family 时，先按最粗粒度 group 做保守回退。
DEFAULT_FAMILY_BY_QUESTION_TYPE = {
    "symptom": "general_symptom",
    "risk": "general_risk",
    "detail": "general_detail",
    "lab": "general_lab",
    "imaging": "imaging",
    "pathogen": "pathogen",
    "exam_context": "general_exam_context",
}

# evidence_tags 在 action builder / service 侧被大量使用，这里把它们压回统计可消费的 family。
EVIDENCE_TAG_TO_FAMILY = {
    "respiratory": "respiratory_symptom",
    "respiratory_symptom": "respiratory_symptom",
    "constitutional": "constitutional_symptom",
    "systemic": "constitutional_symptom",
    "constitutional_symptom": "constitutional_symptom",
    "phenotype": "general_symptom",
    "risk": "general_risk",
    "general_risk": "general_risk",
    "detail": "general_detail",
    "general_detail": "general_detail",
    "lab": "general_lab",
    "general_lab": "general_lab",
    "imaging": "imaging",
    "pathogen": "pathogen",
    "immune_status": "immune_status",
    "oxygenation": "oxygenation",
    "blood_count": "blood_count",
}

EXAM_NOT_DONE_HINTS = (
    "没做过",
    "没有做过",
    "还没做",
    "未做",
    "没查过",
    "没有查过",
    "没检查",
    "没有检查",
    "没化验",
    "没有化验",
    "没拍过",
    "没有拍过",
    "没做这项",
)


@dataclass
class HypothesisBeliefWeight:
    """表示某个候选疾病在当前 belief mixture 中的归一化权重。"""

    disease_id: str
    weight: float
    raw_score: float
    name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConditionalBranchDistribution:
    """承载某个条件键下的平滑后概率分布及其来源信息。"""

    probabilities: dict[str, float]
    total_count: float
    backoff_level: str
    source_key: tuple[str, ...]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TransitionStatisticsConfig:
    """保存统计版 transition model 的离线统计数据读取配置。"""

    source_mode: str = "auto"
    graph_case_paths: tuple[str, ...] = ()
    replay_result_paths: tuple[str, ...] = ()
    evidence_catalog_paths: tuple[str, ...] = ()
    graph_case_glob: str = "test_outputs/simulator_cases/**/cases.jsonl"
    replay_result_glob: str = "test_outputs/simulator_replay/**/replay_results.jsonl"
    evidence_catalog_glob: str = "test_outputs/evidence_family/**/disease_evidence_family_catalog.json"
    smoothing_alpha: float = 0.5
    min_total_count: int = 1


@dataclass
class TransitionActionContext:
    """集中描述一个动作在 transition model 中会用到的条件化上下文。"""

    question_type: str
    evidence_family: str
    evidence_families: tuple[str, ...]
    acquisition_mode: str
    evidence_cost: str
    exam_kind: str | None = None
    test_type: str | None = None
    branch_schema: str = "verify"


@dataclass
class TransitionStatistics:
    """保存统计版 transition model 需要查询的条件频数表。"""

    verify_by_disease_family_question: dict[tuple[str, str, str], Counter[str]] = field(default_factory=dict)
    verify_by_disease_question: dict[tuple[str, str], Counter[str]] = field(default_factory=dict)
    verify_by_family_question: dict[tuple[str, str], Counter[str]] = field(default_factory=dict)
    verify_by_question: dict[str, Counter[str]] = field(default_factory=dict)
    verify_global: Counter[str] = field(default_factory=Counter)
    exam_availability_by_disease_kind: dict[tuple[str, str], Counter[str]] = field(default_factory=dict)
    exam_availability_by_disease: dict[str, Counter[str]] = field(default_factory=dict)
    exam_availability_by_kind: dict[str, Counter[str]] = field(default_factory=dict)
    exam_availability_global: Counter[str] = field(default_factory=Counter)
    exam_result_by_disease_type: dict[tuple[str, str], Counter[str]] = field(default_factory=dict)
    exam_result_by_disease: dict[str, Counter[str]] = field(default_factory=dict)
    exam_result_by_type: dict[str, Counter[str]] = field(default_factory=dict)
    exam_result_global: Counter[str] = field(default_factory=Counter)
    evidence_families_by_disease_and_node: dict[tuple[str, str], tuple[str, ...]] = field(default_factory=dict)
    case_disease_map: dict[str, str] = field(default_factory=dict)
    source_metadata: dict[str, Any] = field(default_factory=dict)
    smoothing_alpha: float = 0.5
    min_total_count: int = 1

    def has_verify_statistics(self) -> bool:
        return sum(self.verify_global.values()) > 0

    def has_exam_statistics(self) -> bool:
        return sum(self.exam_availability_global.values()) > 0

    def lookup_evidence_families(self, disease_id: str | None, node_id: str | None) -> tuple[str, ...]:
        normalized_disease_id = str(disease_id or "").strip()
        normalized_node_id = str(node_id or "").strip()
        if len(normalized_disease_id) == 0 or len(normalized_node_id) == 0:
            return ()
        return self.evidence_families_by_disease_and_node.get((normalized_disease_id, normalized_node_id), ())

    # 普通问诊分支按 disease / family / question_type 查询，逐层 backoff。
    def query_verify_distribution(
        self,
        *,
        disease_id: str | None,
        evidence_family: str | None,
        question_type: str | None,
    ) -> ConditionalBranchDistribution:
        normalized_disease_id = str(disease_id or "").strip()
        normalized_family = str(evidence_family or "").strip()
        normalized_question_type = normalize_question_type_hint(
            str(question_type or "").strip(),
            "",
            "",
        )
        return self._query_distribution(
            outcomes=VERIFY_OUTCOMES,
            levels=[
                (
                    "disease_family_question_type",
                    self.verify_by_disease_family_question.get(
                        (normalized_disease_id, normalized_family, normalized_question_type),
                    ),
                    (normalized_disease_id, normalized_family, normalized_question_type),
                ),
                (
                    "disease_question_type",
                    self.verify_by_disease_question.get((normalized_disease_id, normalized_question_type)),
                    (normalized_disease_id, normalized_question_type),
                ),
                (
                    "family_question_type",
                    self.verify_by_family_question.get((normalized_family, normalized_question_type)),
                    (normalized_family, normalized_question_type),
                ),
                (
                    "question_type",
                    self.verify_by_question.get(normalized_question_type),
                    (normalized_question_type,),
                ),
                (
                    "global",
                    self.verify_global,
                    ("global",),
                ),
            ],
        )

    # 检查上下文先判断 done / not_done，再决定结果分布。
    def query_exam_availability_distribution(
        self,
        *,
        disease_id: str | None,
        exam_kind: str | None,
    ) -> ConditionalBranchDistribution:
        normalized_disease_id = str(disease_id or "").strip()
        normalized_exam_kind = normalize_exam_kind(str(exam_kind or "").strip())
        return self._query_distribution(
            outcomes=EXAM_AVAILABILITY_OUTCOMES,
            levels=[
                (
                    "disease_exam_kind",
                    self.exam_availability_by_disease_kind.get((normalized_disease_id, normalized_exam_kind)),
                    (normalized_disease_id, normalized_exam_kind),
                ),
                (
                    "disease",
                    self.exam_availability_by_disease.get(normalized_disease_id),
                    (normalized_disease_id,),
                ),
                (
                    "exam_kind",
                    self.exam_availability_by_kind.get(normalized_exam_kind),
                    (normalized_exam_kind,),
                ),
                (
                    "global",
                    self.exam_availability_global,
                    ("global",),
                ),
            ],
        )

    def query_exam_result_distribution(
        self,
        *,
        disease_id: str | None,
        test_type: str | None,
    ) -> ConditionalBranchDistribution:
        normalized_disease_id = str(disease_id or "").strip()
        normalized_test_type = normalize_exam_kind(str(test_type or "").strip())
        return self._query_distribution(
            outcomes=EXAM_RESULT_OUTCOMES,
            levels=[
                (
                    "disease_test_type",
                    self.exam_result_by_disease_type.get((normalized_disease_id, normalized_test_type)),
                    (normalized_disease_id, normalized_test_type),
                ),
                (
                    "disease",
                    self.exam_result_by_disease.get(normalized_disease_id),
                    (normalized_disease_id,),
                ),
                (
                    "test_type",
                    self.exam_result_by_type.get(normalized_test_type),
                    (normalized_test_type,),
                ),
                (
                    "global",
                    self.exam_result_global,
                    ("global",),
                ),
            ],
        )

    def record_verify_observation(
        self,
        *,
        disease_id: str | None,
        evidence_family: str | None,
        question_type: str | None,
        outcome: str,
    ) -> None:
        normalized_outcome = normalize_verify_outcome(outcome)
        normalized_disease_id = str(disease_id or "").strip()
        normalized_family = str(evidence_family or "").strip() or default_family_for_question_type(question_type)
        normalized_question_type = normalize_question_type_hint(str(question_type or "").strip(), "", "")
        self._bump_counter(self.verify_global, normalized_outcome)
        self._bump_counter(self.verify_by_question, normalized_question_type, normalized_outcome)
        self._bump_counter(
            self.verify_by_family_question,
            (normalized_family, normalized_question_type),
            normalized_outcome,
        )
        if len(normalized_disease_id) > 0:
            self._bump_counter(
                self.verify_by_disease_question,
                (normalized_disease_id, normalized_question_type),
                normalized_outcome,
            )
            self._bump_counter(
                self.verify_by_disease_family_question,
                (normalized_disease_id, normalized_family, normalized_question_type),
                normalized_outcome,
            )

    def record_exam_availability_observation(
        self,
        *,
        disease_id: str | None,
        exam_kind: str | None,
        outcome: str,
    ) -> None:
        normalized_outcome = normalize_exam_availability(outcome)
        normalized_disease_id = str(disease_id or "").strip()
        normalized_exam_kind = normalize_exam_kind(str(exam_kind or "").strip())
        self._bump_counter(self.exam_availability_global, normalized_outcome)
        self._bump_counter(self.exam_availability_by_kind, normalized_exam_kind, normalized_outcome)
        if len(normalized_disease_id) > 0:
            self._bump_counter(self.exam_availability_by_disease, normalized_disease_id, normalized_outcome)
            self._bump_counter(
                self.exam_availability_by_disease_kind,
                (normalized_disease_id, normalized_exam_kind),
                normalized_outcome,
            )

    def record_exam_result_observation(
        self,
        *,
        disease_id: str | None,
        test_type: str | None,
        outcome: str,
    ) -> None:
        normalized_outcome = normalize_exam_result(outcome)
        normalized_disease_id = str(disease_id or "").strip()
        normalized_test_type = normalize_exam_kind(str(test_type or "").strip())
        self._bump_counter(self.exam_result_global, normalized_outcome)
        self._bump_counter(self.exam_result_by_type, normalized_test_type, normalized_outcome)
        if len(normalized_disease_id) > 0:
            self._bump_counter(self.exam_result_by_disease, normalized_disease_id, normalized_outcome)
            self._bump_counter(
                self.exam_result_by_disease_type,
                (normalized_disease_id, normalized_test_type),
                normalized_outcome,
            )

    def _query_distribution(
        self,
        *,
        outcomes: Sequence[str],
        levels: Sequence[tuple[str, Counter[str] | None, tuple[str, ...]]],
    ) -> ConditionalBranchDistribution:
        fallback_level = "global"
        fallback_key = ("global",)
        fallback_counter = Counter()

        for level_name, counter, key in levels:
            if counter is None:
                continue
            total = sum(counter.values())
            if total >= max(int(self.min_total_count), 1):
                return ConditionalBranchDistribution(
                    probabilities=_smoothed_distribution(counter, outcomes, self.smoothing_alpha),
                    total_count=float(total),
                    backoff_level=level_name,
                    source_key=key,
                    metadata={"smoothing_alpha": self.smoothing_alpha},
                )
            fallback_level = level_name
            fallback_key = key
            fallback_counter = counter

        return ConditionalBranchDistribution(
            probabilities=_smoothed_distribution(fallback_counter, outcomes, self.smoothing_alpha),
            total_count=float(sum(fallback_counter.values())),
            backoff_level=fallback_level,
            source_key=fallback_key,
            metadata={"smoothing_alpha": self.smoothing_alpha},
        )

    def _bump_counter(
        self,
        store: dict[Any, Counter[str]] | Counter[str],
        key_or_outcome: Any,
        maybe_outcome: str | None = None,
    ) -> None:
        if isinstance(store, Counter):
            outcome = normalize_counter_outcome(key_or_outcome)
            store[outcome] += 1
            return

        counter = store.setdefault(key_or_outcome, Counter())
        counter[normalize_counter_outcome(maybe_outcome)] += 1


class TransitionStatisticsBuilder:
    """从图谱病例与 replay 输出构建统计版转移频数表。"""

    def __init__(self, config: TransitionStatisticsConfig | None = None) -> None:
        self.config = config or TransitionStatisticsConfig()

    def build(self) -> TransitionStatistics:
        stats = TransitionStatistics(
            smoothing_alpha=float(self.config.smoothing_alpha),
            min_total_count=max(int(self.config.min_total_count), 1),
            source_metadata={
                "source_mode": self.config.source_mode,
                "graph_case_paths": [],
                "replay_result_paths": [],
                "evidence_catalog_paths": [],
                "graph_case_record_count": 0,
                "replay_record_count": 0,
            },
        )
        graph_case_paths = self._resolve_paths(self.config.graph_case_paths, self.config.graph_case_glob)
        replay_result_paths = self._resolve_paths(self.config.replay_result_paths, self.config.replay_result_glob)
        evidence_catalog_paths = self._resolve_paths(
            self.config.evidence_catalog_paths,
            self.config.evidence_catalog_glob,
        )
        stats.source_metadata["graph_case_paths"] = [str(path) for path in graph_case_paths]
        stats.source_metadata["replay_result_paths"] = [str(path) for path in replay_result_paths]
        stats.source_metadata["evidence_catalog_paths"] = [str(path) for path in evidence_catalog_paths]

        for path in evidence_catalog_paths:
            self._load_evidence_catalog(path, stats)
        for path in graph_case_paths:
            self._load_graph_cases(path, stats)
        for path in replay_result_paths:
            self._load_replay_results(path, stats)

        return stats

    def _resolve_paths(self, explicit_paths: Sequence[str], auto_glob: str) -> tuple[Path, ...]:
        expanded_explicit_paths = self._expand_explicit_paths(explicit_paths)
        if len(expanded_explicit_paths) > 0:
            return expanded_explicit_paths

        if str(self.config.source_mode or "auto").strip().lower() != "auto":
            return ()

        matches = sorted(Path.cwd().glob(auto_glob))
        if len(matches) == 0:
            return ()

        latest = max(matches, key=lambda path: path.stat().st_mtime)
        return (latest,)

    def _expand_explicit_paths(self, explicit_paths: Sequence[str]) -> tuple[Path, ...]:
        resolved: list[Path] = []
        for raw_path in explicit_paths:
            path = Path(str(raw_path)).expanduser()
            if not path.is_absolute():
                path = Path.cwd() / path
            if path.is_dir():
                for filename in (
                    "cases.jsonl",
                    "cases.json",
                    "replay_results.jsonl",
                    "disease_evidence_family_catalog.json",
                ):
                    candidate = path / filename
                    if candidate.exists():
                        resolved.append(candidate)
            elif path.exists():
                resolved.append(path)
        deduped = sorted({item.resolve() for item in resolved})
        return tuple(deduped)

    def _load_evidence_catalog(self, path: Path, stats: TransitionStatistics) -> None:
        payload = self._load_json(path)
        if not isinstance(payload, dict):
            return
        diseases = payload.get("diseases")
        if not isinstance(diseases, list):
            return

        for disease in diseases:
            if not isinstance(disease, dict):
                continue
            disease_id = str(disease.get("disease_id") or "").strip()
            evidence_items = disease.get("evidence") or []
            if len(disease_id) == 0 or not isinstance(evidence_items, list):
                continue
            for evidence in evidence_items:
                if not isinstance(evidence, dict):
                    continue
                evidence_id = str(evidence.get("evidence_id") or "").strip()
                if len(evidence_id) == 0:
                    continue
                families = tuple(_normalize_family_list(evidence.get("families")))
                if len(families) == 0:
                    continue
                stats.evidence_families_by_disease_and_node[(disease_id, evidence_id)] = families

    def _load_graph_cases(self, path: Path, stats: TransitionStatistics) -> None:
        for record in self._iter_json_records(path):
            if not isinstance(record, dict):
                continue
            self._consume_graph_case(record, stats)
            stats.source_metadata["graph_case_record_count"] = (
                int(stats.source_metadata.get("graph_case_record_count", 0)) + 1
            )

    def _load_replay_results(self, path: Path, stats: TransitionStatistics) -> None:
        for record in self._iter_json_records(path):
            if not isinstance(record, dict):
                continue
            self._consume_replay_result(record, stats)
            stats.source_metadata["replay_record_count"] = (
                int(stats.source_metadata.get("replay_record_count", 0)) + 1
            )

    def _consume_graph_case(self, record: dict[str, Any], stats: TransitionStatistics) -> None:
        metadata = record.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        disease_id = str(metadata.get("disease_id") or "").strip()
        case_id = str(record.get("case_id") or "").strip()
        if len(case_id) > 0 and len(disease_id) > 0:
            stats.case_disease_map[case_id] = disease_id

        slot_truth_map = record.get("slot_truth_map")
        slot_truth_map = slot_truth_map if isinstance(slot_truth_map, dict) else {}
        exam_outcomes_by_kind: dict[str, list[str]] = {kind: [] for kind in EXAM_KINDS if kind != "general"}

        for slot_id, raw_slot in slot_truth_map.items():
            if not isinstance(raw_slot, dict):
                continue
            question_type = normalize_question_type_hint(
                str(raw_slot.get("group") or "").strip(),
                "",
                str(raw_slot.get("node_label") or "").strip(),
            )
            verify_outcome = infer_slot_truth_outcome(raw_slot.get("value"))
            families = stats.lookup_evidence_families(disease_id, str(slot_id))
            if len(families) == 0:
                families = tuple(_normalize_family_list(raw_slot.get("families")))
            if len(families) == 0:
                families = (default_family_for_question_type(question_type),)

            for family in families:
                stats.record_verify_observation(
                    disease_id=disease_id,
                    evidence_family=family,
                    question_type=question_type,
                    outcome=verify_outcome,
                )

            if question_type in exam_outcomes_by_kind:
                exam_outcomes_by_kind[question_type].append(verify_outcome)

        all_exam_outcomes: list[str] = []
        for exam_kind, outcomes in exam_outcomes_by_kind.items():
            availability = "done" if len(outcomes) > 0 else "not_done"
            stats.record_exam_availability_observation(
                disease_id=disease_id,
                exam_kind=exam_kind,
                outcome=availability,
            )
            if len(outcomes) > 0:
                collapsed = collapse_exam_result_outcomes(outcomes)
                stats.record_exam_result_observation(
                    disease_id=disease_id,
                    test_type=exam_kind,
                    outcome=collapsed,
                )
                all_exam_outcomes.extend(outcomes)

        general_availability = "done" if len(all_exam_outcomes) > 0 else "not_done"
        stats.record_exam_availability_observation(
            disease_id=disease_id,
            exam_kind="general",
            outcome=general_availability,
        )
        if len(all_exam_outcomes) > 0:
            stats.record_exam_result_observation(
                disease_id=disease_id,
                test_type="general",
                outcome=collapse_exam_result_outcomes(all_exam_outcomes),
            )

    def _consume_replay_result(self, record: dict[str, Any], stats: TransitionStatistics) -> None:
        case_id = str(record.get("case_id") or "").strip()
        disease_id = stats.case_disease_map.get(case_id, "")
        turns = record.get("turns")
        if not isinstance(turns, list):
            return

        for turn in turns:
            if not isinstance(turn, dict):
                continue
            self._consume_replay_turn(turn, disease_id, stats)

    def _consume_replay_turn(
        self,
        turn: dict[str, Any],
        disease_id: str,
        stats: TransitionStatistics,
    ) -> None:
        question_node_id = str(turn.get("question_node_id") or "").strip()
        action_group = str(turn.get("asked_action_group") or "").strip()
        question_type = normalize_question_type_hint(
            str(turn.get("asked_action_question_type_hint") or action_group).strip(),
            str(turn.get("asked_action_acquisition_mode") or "").strip(),
            str(turn.get("asked_target_node_label") or "").strip(),
        )
        if is_exam_context_action(question_node_id, action_group, question_type):
            exam_kind = normalize_exam_kind(question_node_id.split("::")[-1] if "::" in question_node_id else "general")
            availability = infer_replay_exam_availability(turn)
            stats.record_exam_availability_observation(
                disease_id=disease_id,
                exam_kind=exam_kind,
                outcome=availability,
            )
            if exam_kind != "general":
                stats.record_exam_availability_observation(
                    disease_id=disease_id,
                    exam_kind="general",
                    outcome=availability,
                )
            if availability == "done":
                result_outcome = infer_replay_exam_result(turn)
                test_type = normalize_question_type_hint(
                    str(turn.get("revealed_slot_group") or "").strip(),
                    "",
                    str(turn.get("revealed_slot_label") or "").strip(),
                )
                if test_type not in EXAM_KINDS:
                    test_type = exam_kind
                stats.record_exam_result_observation(
                    disease_id=disease_id,
                    test_type=test_type,
                    outcome=result_outcome,
                )
                if test_type != "general":
                    stats.record_exam_result_observation(
                        disease_id=disease_id,
                        test_type="general",
                        outcome=result_outcome,
                    )

            revealed_slot_id = str(turn.get("revealed_slot_id") or "").strip()
            if len(revealed_slot_id) > 0:
                families = tuple(_normalize_family_list(turn.get("revealed_slot_families")))
                if len(families) == 0:
                    families = stats.lookup_evidence_families(disease_id, revealed_slot_id)
                revealed_group = normalize_question_type_hint(
                    str(turn.get("revealed_slot_group") or "").strip(),
                    "",
                    str(turn.get("revealed_slot_label") or "").strip(),
                )
                verify_question_type = revealed_group if revealed_group != "exam_context" else exam_kind
                if len(families) == 0:
                    families = (default_family_for_question_type(verify_question_type),)
                verify_outcome = infer_replay_verify_outcome(turn)
                for family in families:
                    stats.record_verify_observation(
                        disease_id=disease_id,
                        evidence_family=family,
                        question_type=verify_question_type,
                        outcome=verify_outcome,
                    )
            return

        verify_outcome = infer_replay_verify_outcome(turn)
        revealed_slot_id = str(turn.get("revealed_slot_id") or "").strip()
        evidence_node_id = revealed_slot_id or question_node_id
        families = tuple(_normalize_family_list(turn.get("revealed_slot_families")))
        if len(families) == 0:
            families = stats.lookup_evidence_families(disease_id, evidence_node_id)
        if len(families) == 0:
            families = (default_family_for_question_type(question_type),)
        for family in families:
            stats.record_verify_observation(
                disease_id=disease_id,
                evidence_family=family,
                question_type=question_type,
                outcome=verify_outcome,
            )

    def _iter_json_records(self, path: Path) -> Iterable[dict[str, Any] | Any]:
        if path.suffix == ".jsonl":
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    payload = line.strip()
                    if len(payload) == 0:
                        continue
                    yield json.loads(payload)
            return

        payload = self._load_json(path)
        if isinstance(payload, list):
            for item in payload:
                yield item
            return

        if isinstance(payload, dict):
            if isinstance(payload.get("cases"), list):
                for item in payload["cases"]:
                    yield item
                return
            yield payload

    def _load_json(self, path: Path) -> Any:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)


def build_normalized_hypothesis_belief(
    candidates: Sequence[HypothesisScore | HypothesisCandidate] | None,
    *,
    top_k: int = 3,
) -> list[HypothesisBeliefWeight]:
    """把当前候选假设分数归一化成可直接用于 mixture 的 belief。"""

    if candidates is None or len(candidates) == 0:
        return []

    aggregated: dict[str, tuple[float, str, dict[str, Any]]] = {}
    for item in candidates:
        disease_id = str(getattr(item, "node_id", "") or "").strip()
        if len(disease_id) == 0:
            continue
        raw_score = float(getattr(item, "score", 0.0) or 0.0)
        if not math.isfinite(raw_score):
            continue
        current = aggregated.get(disease_id)
        metadata = dict(getattr(item, "metadata", {}) or {})
        if current is None or raw_score > current[0]:
            aggregated[disease_id] = (
                raw_score,
                str(getattr(item, "name", "") or "").strip(),
                metadata,
            )

    if len(aggregated) == 0:
        return []

    top_items = sorted(
        aggregated.items(),
        key=lambda pair: (-pair[1][0], pair[0]),
    )[: max(int(top_k), 1)]
    positive_total = sum(max(payload[0], 0.0) for _, payload in top_items)

    belief_weights: list[HypothesisBeliefWeight] = []
    for disease_id, payload in top_items:
        raw_score, name, metadata = payload
        if positive_total > 0.0:
            weight = max(raw_score, 0.0) / positive_total
        else:
            weight = 1.0 / len(top_items)
        belief_weights.append(
            HypothesisBeliefWeight(
                disease_id=disease_id,
                weight=weight,
                raw_score=raw_score,
                name=name,
                metadata=metadata,
            )
        )
    return belief_weights


def infer_action_context(
    action: MctsAction,
    *,
    statistics: TransitionStatistics | None = None,
    disease_id: str | None = None,
) -> TransitionActionContext:
    """集中抽取动作的 question_type / family / exam_kind / test_type 条件。"""

    metadata = dict(action.metadata or {})
    acquisition_mode = str(metadata.get("acquisition_mode") or "").strip()
    evidence_cost = str(metadata.get("evidence_cost") or "").strip()
    question_type = normalize_question_type_hint(
        str(metadata.get("question_type_hint") or "").strip(),
        acquisition_mode,
        str(action.target_node_label or "").strip(),
    )
    branch_schema = "verify"
    exam_kind = normalize_exam_kind(str(metadata.get("exam_kind") or "").strip())
    if exam_kind == "general" and not str(action.target_node_id or "").startswith("__exam_context__::general"):
        exam_kind = normalize_exam_kind(str(metadata.get("candidate_exam_kind") or "").strip())
    if exam_kind == "general" and question_type in {"lab", "imaging", "pathogen"}:
        exam_kind = normalize_exam_kind(question_type)
    if len(exam_kind) == 0:
        exam_kind = infer_exam_kind(acquisition_mode, question_type, str(action.target_node_label or ""))

    if is_exam_context_action(action.target_node_id, action.action_type, question_type):
        branch_schema = "exam_context"
        if str(action.target_node_id or "").startswith("__exam_context__::"):
            exam_kind = normalize_exam_kind(str(action.target_node_id).split("::")[-1])
        question_type = "exam_context"

    evidence_families = _extract_families_from_action_metadata(metadata)
    if len(evidence_families) == 0 and statistics is not None:
        evidence_families = statistics.lookup_evidence_families(
            str(disease_id or action.hypothesis_id or "").strip(),
            str(action.target_node_id or "").strip(),
        )
    if len(evidence_families) == 0:
        evidence_families = (default_family_for_question_type(question_type),)

    if branch_schema == "exam_context":
        test_type = normalize_exam_kind(str(metadata.get("test_type") or "").strip())
        if len(test_type) == 0:
            candidate_exam_kinds = [
                normalize_exam_kind(str(item))
                for item in metadata.get("candidate_exam_kinds", []) or []
                if len(normalize_exam_kind(str(item))) > 0
            ]
            test_type = candidate_exam_kinds[0] if len(candidate_exam_kinds) > 0 else normalize_exam_kind(exam_kind)
    else:
        test_type = normalize_exam_kind(question_type) if question_type in {"lab", "imaging", "pathogen"} else None

    return TransitionActionContext(
        question_type=question_type,
        evidence_family=evidence_families[0],
        evidence_families=evidence_families,
        acquisition_mode=acquisition_mode,
        evidence_cost=evidence_cost,
        exam_kind=exam_kind or None,
        test_type=test_type or None,
        branch_schema=branch_schema,
    )


def normalize_question_type_hint(question_type_hint: str, acquisition_mode: str, label: str) -> str:
    normalized_hint = str(question_type_hint or "").strip()
    normalized_mode = str(acquisition_mode or "").strip()
    normalized_label = str(label or "").strip()

    if normalized_hint == "exam_context":
        return "exam_context"
    if normalized_mode == "needs_pathogen_test" or normalized_label == "Pathogen":
        return "pathogen"
    if normalized_mode == "needs_imaging" or normalized_label == "ImagingFinding":
        return "imaging"
    if normalized_mode == "needs_lab_test" or normalized_label in {"LabFinding", "LabTest"}:
        return "lab"
    if normalized_label == "ClinicalAttribute":
        return "detail"
    if normalized_label in {"RiskFactor", "PopulationGroup"}:
        return "risk"
    if normalized_label == "ClinicalFinding":
        return "symptom"
    if normalized_hint in QUESTION_TYPE_HINTS:
        return normalized_hint
    return normalized_hint or "symptom"


def normalize_exam_kind(exam_kind: str) -> str:
    normalized = str(exam_kind or "").strip()
    if normalized in EXAM_KINDS:
        return normalized
    return ""


def infer_exam_kind(acquisition_mode: str, question_type: str, label: str) -> str:
    normalized_mode = str(acquisition_mode or "").strip()
    normalized_question_type = str(question_type or "").strip()
    normalized_label = str(label or "").strip()
    if normalized_mode == "needs_pathogen_test" or normalized_question_type == "pathogen" or normalized_label == "Pathogen":
        return "pathogen"
    if normalized_mode == "needs_imaging" or normalized_question_type == "imaging" or normalized_label == "ImagingFinding":
        return "imaging"
    if normalized_mode == "needs_lab_test" or normalized_question_type == "lab" or normalized_label in {"LabFinding", "LabTest"}:
        return "lab"
    return ""


def default_family_for_question_type(question_type: str | None) -> str:
    normalized_question_type = normalize_question_type_hint(str(question_type or ""), "", "")
    return DEFAULT_FAMILY_BY_QUESTION_TYPE.get(normalized_question_type, "general_symptom")


def normalize_verify_outcome(outcome: str) -> str:
    normalized = str(outcome or "").strip()
    if normalized in {"present", "positive", "done_positive"}:
        return "present"
    if normalized in {"absent", "negative", "done_negative"}:
        return "absent"
    return "unclear"


def normalize_exam_availability(outcome: str) -> str:
    normalized = str(outcome or "").strip()
    if normalized in {"done", "present"}:
        return "done"
    if normalized in {"not_done", "absent"}:
        return "not_done"
    return "unclear"


def normalize_exam_result(outcome: str) -> str:
    normalized = str(outcome or "").strip()
    if normalized in {"positive", "present", "done_positive"}:
        return "positive"
    if normalized in {"negative", "absent", "done_negative"}:
        return "negative"
    return "unclear"


def normalize_counter_outcome(outcome: str | None) -> str:
    normalized = str(outcome or "").strip()
    if normalized in VERIFY_OUTCOMES or normalized in EXAM_AVAILABILITY_OUTCOMES or normalized in EXAM_RESULT_OUTCOMES:
        return normalized
    if normalized in {"present", "absent", "unclear"}:
        return normalized
    return "unclear"


def infer_slot_truth_outcome(value: Any) -> str:
    if isinstance(value, bool):
        return "present" if value else "absent"
    if value is None:
        return "unclear"
    if isinstance(value, (int, float)):
        return "present"
    normalized = str(value).strip().lower()
    if normalized in {"true", "yes", "positive", "present"}:
        return "present"
    if normalized in {"false", "no", "negative", "absent"}:
        return "absent"
    return "unclear"


def collapse_exam_result_outcomes(outcomes: Sequence[str]) -> str:
    normalized_outcomes = [normalize_verify_outcome(item) for item in outcomes]
    if any(item == "present" for item in normalized_outcomes):
        return "positive"
    if any(item == "absent" for item in normalized_outcomes):
        return "negative"
    return "unclear"


def infer_replay_verify_outcome(turn: dict[str, Any]) -> str:
    revealed_positive = turn.get("revealed_slot_positive")
    if revealed_positive is True:
        return "present"
    if revealed_positive is False:
        return "absent"
    revealed_slot_id = str(turn.get("revealed_slot_id") or "").strip()
    if len(revealed_slot_id) > 0:
        return "present"
    return "unclear"


def infer_replay_exam_availability(turn: dict[str, Any]) -> str:
    revealed_slot_id = str(turn.get("revealed_slot_id") or "").strip()
    if len(revealed_slot_id) > 0:
        return "done"
    answer_text = str(turn.get("answer_text") or "").strip()
    if any(keyword in answer_text for keyword in EXAM_NOT_DONE_HINTS):
        return "not_done"
    if len(answer_text) == 0:
        return "unclear"
    return "done"


def infer_replay_exam_result(turn: dict[str, Any]) -> str:
    revealed_positive = turn.get("revealed_slot_positive")
    if revealed_positive is True:
        return "positive"
    if revealed_positive is False:
        return "negative"
    return "unclear"


def is_exam_context_action(target_node_id: str | None, action_type: str | None, question_type: str | None) -> bool:
    normalized_target = str(target_node_id or "").strip()
    normalized_action_type = str(action_type or "").strip()
    normalized_question_type = str(question_type or "").strip()
    return (
        normalized_target.startswith("__exam_context__::")
        or normalized_action_type in {"collect_exam_context", "collect_general_exam_context"}
        or normalized_question_type == "exam_context"
    )


def _extract_families_from_action_metadata(metadata: dict[str, Any]) -> tuple[str, ...]:
    for key in ("evidence_families", "families", "evidence_family", "family"):
        normalized = tuple(_normalize_family_list(metadata.get(key)))
        if len(normalized) > 0:
            return normalized
    normalized_tags = tuple(_normalize_evidence_tag_families(metadata.get("evidence_tags")))
    if len(normalized_tags) > 0:
        return normalized_tags
    return ()


def _normalize_family_list(value: Any) -> list[str]:
    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if len(normalized) > 0 else []
    if not isinstance(value, (list, tuple, set)):
        return []
    deduped: list[str] = []
    for item in value:
        normalized = str(item or "").strip()
        if len(normalized) == 0 or normalized in deduped:
            continue
        deduped.append(normalized)
    return deduped


def _normalize_evidence_tag_families(value: Any) -> list[str]:
    deduped: list[str] = []
    for tag in _normalize_family_list(value):
        normalized_tag = str(tag or "").strip()
        if len(normalized_tag) == 0 or normalized_tag.startswith("type:"):
            continue
        if normalized_tag in {"exam_context", "recommended"}:
            continue
        family = EVIDENCE_TAG_TO_FAMILY.get(normalized_tag, normalized_tag)
        if len(family) == 0 or family in deduped:
            continue
        deduped.append(family)
    return deduped


def _smoothed_distribution(counter: Counter[str], outcomes: Sequence[str], alpha: float) -> dict[str, float]:
    safe_alpha = max(float(alpha), 1e-6)
    total = float(sum(counter.values()))
    denominator = total + safe_alpha * len(outcomes)
    if denominator <= 0.0:
        uniform = 1.0 / max(len(outcomes), 1)
        return {str(outcome): uniform for outcome in outcomes}
    return {
        str(outcome): (float(counter.get(str(outcome), 0.0)) + safe_alpha) / denominator
        for outcome in outcomes
    }
