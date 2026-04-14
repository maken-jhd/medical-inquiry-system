"""浏览本地 simulator replay 实验输出，并转换为前端可展示结构。"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from frontend.ui_adapter import (
    normalize_backend_turn,
    translate_certainty,
    translate_existence,
    translate_guarded_block,
    translate_question_type,
    translate_reject_reason,
    translate_repair_mode,
    translate_stage,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "test_outputs" / "simulator_replay"

RUN_FILE_KIND_MAP = {
    "focused_metrics.json": "focused metrics",
    "ablation_metrics.json": "ablation metrics",
    "benchmark_summary.json": "benchmark summary",
    "profile_summary.tsv": "profile summary",
    "status.json": "status",
    "run.log": "run log",
    "focused_repair_summary.jsonl": "focused repair cases",
    "ablation_summary.jsonl": "ablation cases",
    "replay_results.jsonl": "replay cases",
    "a4_evidence_audit.jsonl": "A4 evidence audit",
    "guarded_gate_audit.jsonl": "guarded gate audit",
}

CASE_FILE_PRIORITY = [
    ("focused_repair_summary.jsonl", "focused_repair_summary"),
    ("replay_results.jsonl", "replay_results"),
    ("ablation_summary.jsonl", "ablation_summary"),
]


@dataclass(frozen=True)
class OutputRun:
    """一个可被前端选择的实验输出目录。"""

    key: str
    path: Path
    relative_path: str
    modified_time: float
    file_kinds: tuple[str, ...]

    @property
    def label(self) -> str:
        kinds = "、".join(self.file_kinds[:3])
        if len(self.file_kinds) > 3:
            kinds += f" 等 {len(self.file_kinds)} 类"
        return f"{self.relative_path} ｜ {kinds}"


def list_output_runs(root: str | Path = DEFAULT_OUTPUT_ROOT) -> list[OutputRun]:
    """扫描 simulator_replay 目录，返回包含可识别输出文件的目录列表。"""

    output_root = Path(root)
    if not output_root.exists():
        return []

    runs: list[OutputRun] = []
    for dirpath, _, filenames in os.walk(output_root):
        file_kinds = sorted(
            {
                RUN_FILE_KIND_MAP[name]
                for name in filenames
                if name in RUN_FILE_KIND_MAP
            }
        )
        if not file_kinds:
            continue

        path = Path(dirpath)
        relative_path = path.relative_to(output_root).as_posix() if path != output_root else "."
        modified_time = max((path / name).stat().st_mtime for name in filenames if (path / name).is_file())
        runs.append(
            OutputRun(
                key=relative_path,
                path=path,
                relative_path=relative_path,
                modified_time=modified_time,
                file_kinds=tuple(file_kinds),
            )
        )

    return sorted(runs, key=lambda item: (_run_detail_rank(item.file_kinds), item.modified_time), reverse=True)


def load_run_overview(run_path: str | Path) -> dict[str, Any]:
    """读取一个实验目录下的汇总文件，供前端展示。"""

    path = Path(run_path)
    overview = {
        "path": str(path),
        "relative_path": _relative_output_path(path),
        "metrics": {},
        "ablation_metrics": {},
        "benchmark_summary": {},
        "status": {},
        "profile_summary": [],
        "available_files": [],
        "log_tail": "",
    }

    for filename in RUN_FILE_KIND_MAP:
        if (path / filename).exists():
            overview["available_files"].append(filename)

    overview["metrics"] = _read_json(path / "focused_metrics.json")
    overview["ablation_metrics"] = _read_json(path / "ablation_metrics.json")
    overview["benchmark_summary"] = _read_json(path / "benchmark_summary.json")
    overview["status"] = _read_json(path / "status.json")
    overview["profile_summary"] = _read_tsv(path / "profile_summary.tsv")
    overview["log_tail"] = _read_tail(path / "run.log", line_count=20)
    return overview


def list_case_records(run_path: str | Path) -> list[dict[str, Any]]:
    """读取目录中的逐病例记录，优先使用信息最完整的 JSONL 文件。"""

    path = Path(run_path)
    for filename, record_kind in CASE_FILE_PRIORITY:
        records = _read_jsonl(path / filename)
        if records:
            for index, record in enumerate(records):
                record["_source_file"] = filename
                record["_record_kind"] = record_kind
                record["_record_index"] = index
            return records
    return []


def case_record_label(record: dict[str, Any]) -> str:
    """生成人类可读的病例选择标签。"""

    case_id = record.get("case_id") or f"record_{record.get('_record_index', 0) + 1}"
    title = record.get("case_title") or record.get("title") or "未命名病例"
    answer = record.get("final_best_answer_name") or record.get("best_answer_name") or _final_report_answer(record)
    stop_reason = record.get("final_stop_reason") or record.get("stop_reason") or record.get("status") or "unknown"
    correctness = _correctness_label(record.get("is_best_answer_correct"))
    return f"{case_id} ｜ {title} ｜ {answer or '暂无答案'} ｜ {stop_reason} ｜ {correctness}"


def build_case_replay(record: dict[str, Any]) -> dict[str, Any]:
    """把实验逐病例记录转换为与 demo replay 类似的前端结构。"""

    kind = record.get("_record_kind")
    if kind == "focused_repair_summary" or record.get("turn_summaries"):
        return _build_from_focused_repair_summary(record)
    if kind == "replay_results" or record.get("initial_output") or record.get("turns"):
        return _build_from_replay_results(record)
    return _build_from_case_summary(record)


def summarize_case_record(record: dict[str, Any]) -> dict[str, Any]:
    """提取病例级指标，用于复盘面板。"""

    return {
        "case_id": record.get("case_id", ""),
        "case_title": record.get("case_title") or record.get("title", ""),
        "true_conditions": record.get("true_conditions", []),
        "true_disease_phase": record.get("true_disease_phase", ""),
        "best_answer": record.get("final_best_answer_name") or record.get("best_answer_name") or _final_report_answer(record),
        "stop_reason": record.get("final_stop_reason") or record.get("stop_reason") or record.get("status", ""),
        "acceptance_category": record.get("acceptance_category", ""),
        "is_best_answer_correct": record.get("is_best_answer_correct"),
        "first_correct_best_answer_turn": record.get("first_correct_best_answer_turn"),
        "first_verifier_accept_turn": record.get("first_verifier_accept_turn"),
        "correct_but_rejected_span": record.get("correct_but_rejected_span"),
        "repair_turns": record.get("repair_turns", _count_repair_turns(record.get("turn_summaries", []))),
        "semantic_repeat_turns": record.get("semantic_repeat_turns", _count_semantic_repeats(record.get("turn_summaries", []))),
        "source_file": record.get("_source_file", ""),
    }


def _build_from_focused_repair_summary(record: dict[str, Any]) -> dict[str, Any]:
    turn_summaries = record.get("turn_summaries") or []
    turns = [
        _focused_turn_to_ui(turn, record, is_last=index == len(turn_summaries) - 1)
        for index, turn in enumerate(turn_summaries)
    ]
    if not turns:
        turns = [_case_summary_to_ui_turn(record)]
    return {
        "id": record.get("case_id", ""),
        "title": record.get("case_title") or record.get("case_id", "实验病例"),
        "description": _case_description(record),
        "mode": "experiment",
        "summary": summarize_case_record(record),
        "turns": turns,
        "raw": record,
    }


def _build_from_replay_results(record: dict[str, Any]) -> dict[str, Any]:
    turns: list[dict[str, Any]] = []
    initial_output = record.get("initial_output")
    if isinstance(initial_output, dict):
        turns.append(normalize_backend_turn(initial_output))

    replay_turns = record.get("turns") or []
    for index, item in enumerate(replay_turns):
        item_dict = _as_dict(item)
        is_last = index == len(replay_turns) - 1
        turns.append(_replay_result_turn_to_ui(item_dict, record, is_last=is_last))

    if not turns:
        turns = [_case_summary_to_ui_turn(record)]

    return {
        "id": record.get("case_id", ""),
        "title": record.get("case_title") or record.get("case_id", "实验病例"),
        "description": _case_description(record),
        "mode": "experiment",
        "summary": summarize_case_record(record),
        "turns": turns,
        "raw": record,
    }


def _build_from_case_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("case_id", ""),
        "title": record.get("case_title") or record.get("case_id", "实验病例"),
        "description": _case_description(record),
        "mode": "experiment",
        "summary": summarize_case_record(record),
        "turns": [_case_summary_to_ui_turn(record)],
        "raw": record,
    }


def _focused_turn_to_ui(turn: dict[str, Any], record: dict[str, Any], is_last: bool) -> dict[str, Any]:
    selected_action = _as_dict(turn.get("selected_action"))
    root_action = _as_dict(turn.get("root_best_action"))
    repair_action = _as_dict(turn.get("repair_selected_action"))
    a4_audit = _as_dict(turn.get("a4_evidence_audit"))
    best_answer_name = turn.get("best_answer_name") or record.get("final_best_answer_name", "")
    stop_reason = turn.get("stop_reason") or (record.get("final_stop_reason") if is_last else "")
    is_final = bool(stop_reason)

    return {
        "turn_index": turn.get("turn_index", 0),
        "patient_text": turn.get("answer_text") or a4_audit.get("patient_answer", ""),
        "system_question": _question_from_action(selected_action),
        "is_final": is_final,
        "final_answer": {
            "answer_name": record.get("final_best_answer_name") or best_answer_name,
            "stop_reason": stop_reason,
            "confidence": turn.get("best_answer_verifier_score"),
            "why": _final_reason_text(record, turn),
        },
        "state": {
            "turn_index": turn.get("turn_index", 0),
            "is_running": not is_final,
            "has_final_report": is_final,
            "primary_hypothesis": best_answer_name,
            "has_pending_action": bool(turn.get("pending_action")),
            "pending_action_name": _action_name(_as_dict(turn.get("pending_action"))),
        },
        "a1": {
            "features": [],
            "reasoning": "实验摘要文件未保留完整 A1 结构；如需查看原始抽取，请选择包含 replay_results.jsonl 的目录。",
        },
        "a2": {
            "candidates": _candidates_from_focused_turn(turn, record),
            "reasoning": "来自实验复盘摘要中的 best answer 与 verifier alternative candidates。",
        },
        "a3": {
            "question_text": _question_from_action(selected_action),
            "selected_action_name": _action_name(selected_action),
            "question_type": _question_type(selected_action),
            "question_type_label": translate_question_type(_question_type(selected_action)),
            "reasoning": _repair_reason(turn),
            "root_best_action_name": _action_name(root_action),
            "repair_selected_action_name": _action_name(repair_action),
            "is_repair_override": bool(repair_action and _action_id(repair_action) != _action_id(root_action)),
            "evidence_tags": selected_action.get("evidence_tags", []),
            "recommended_match_score": None,
            "discriminative_gain": None,
        },
        "a4": _a4_from_audit(a4_audit, turn.get("route_after_a4_stage")),
        "search": {
            "rollouts": "实验摘要",
            "tree_node_count": "—",
            "trajectory_count": "—",
            "best_answer": best_answer_name,
            "consistency": None,
            "diversity": None,
            "agent_evaluation": turn.get("best_answer_verifier_score"),
            "verifier_result": _verifier_result_text(turn),
            "final_answer_scores": _final_scores_from_focused_turn(turn, record),
        },
        "safety": _safety_from_focused_turn(turn),
        "raw": turn,
    }


def _replay_result_turn_to_ui(turn: dict[str, Any], record: dict[str, Any], is_last: bool) -> dict[str, Any]:
    final_report = _as_dict(record.get("final_report"))
    is_final = is_last and bool(final_report)
    question_text = turn.get("question_text", "")
    return {
        "turn_index": turn.get("turn_index", 0),
        "patient_text": turn.get("answer_text", ""),
        "system_question": "",
        "is_final": is_final,
        "final_answer": {
            "answer_name": _final_report_answer(record),
            "stop_reason": final_report.get("stop_reason", record.get("status", "")),
            "confidence": final_report.get("stop_confidence"),
            "why": final_report.get("why_this_answer_wins", ""),
        },
        "state": {
            "turn_index": turn.get("turn_index", 0),
            "is_running": not is_final,
            "has_final_report": is_final,
            "primary_hypothesis": _final_report_answer(record),
            "has_pending_action": bool(question_text),
            "pending_action_name": question_text,
        },
        "a1": {"features": [], "reasoning": "该记录为自动 replay 摘要，本轮未保留完整 A1。"},
        "a2": {"candidates": _candidates_from_final_report(final_report), "reasoning": "来自 replay final_report。"},
        "a3": {
            "question_text": question_text,
            "selected_action_name": turn.get("question_node_id", ""),
            "question_type": turn.get("stage", "unknown"),
            "question_type_label": translate_question_type(turn.get("stage", "unknown")),
            "reasoning": f"自动 replay 中第 {turn.get('turn_index', 0)} 轮系统追问。",
            "root_best_action_name": "",
            "repair_selected_action_name": "",
            "is_repair_override": False,
            "evidence_tags": [],
        },
        "a4": {
            "has_result": bool(turn.get("answer_text")),
            "existence_label": "见患者回答",
            "certainty_label": "未结构化保存",
            "reasoning": turn.get("answer_text", ""),
            "route_label": translate_stage(turn.get("stage")),
        },
        "search": _search_from_final_report(final_report),
        "safety": {
            "verifier_should_accept": False,
            "guarded_acceptance_blocked": False,
            "tree_rerooted": False,
            "reject_reason_label": "该 replay 记录未保存逐轮 verifier 结构",
            "guarded_block_reason_label": "未保存",
            "repair_mode_label": "未保存",
        },
        "raw": turn,
    }


def _case_summary_to_ui_turn(record: dict[str, Any]) -> dict[str, Any]:
    best_answer = record.get("best_answer_name") or record.get("final_best_answer_name") or _final_report_answer(record)
    return {
        "turn_index": record.get("turn_index", 1),
        "patient_text": "该文件只包含病例级摘要，未保存完整逐轮对话。",
        "system_question": "",
        "is_final": True,
        "final_answer": {
            "answer_name": best_answer,
            "stop_reason": record.get("stop_reason") or record.get("final_stop_reason") or record.get("status", ""),
            "confidence": None,
            "why": _final_reason_text(record, {}),
        },
        "state": {
            "turn_index": record.get("turn_index", 1),
            "is_running": False,
            "has_final_report": True,
            "primary_hypothesis": best_answer,
            "has_pending_action": False,
            "pending_action_name": "",
        },
        "a1": {"features": [], "reasoning": "病例级摘要未保存 A1。"},
        "a2": {"candidates": _candidates_from_summary_record(record), "reasoning": "来自病例级 summary。"},
        "a3": {
            "question_text": "",
            "selected_action_name": "",
            "question_type": "unknown",
            "question_type_label": "未保存",
            "reasoning": "该记录未保存下一问细节。",
        },
        "a4": {
            "has_result": False,
            "existence_label": "未保存",
            "certainty_label": "未保存",
            "reasoning": "该记录未保存 A4 逐轮解释。",
            "route_label": "未保存",
        },
        "search": {
            "rollouts": "—",
            "tree_node_count": "—",
            "trajectory_count": "—",
            "best_answer": best_answer,
            "consistency": None,
            "diversity": None,
            "agent_evaluation": None,
            "verifier_result": "病例级摘要",
            "final_answer_scores": _candidates_from_summary_record(record),
        },
        "safety": {
            "verifier_should_accept": record.get("accepted_with_verifier_metadata", False),
            "guarded_acceptance_blocked": False,
            "tree_rerooted": False,
            "reject_reason_label": record.get("acceptance_category", "未保存"),
            "guarded_block_reason_label": "未保存",
            "repair_mode_label": "病例级摘要",
        },
        "raw": record,
    }


def _a4_from_audit(audit: dict[str, Any], route_stage: Any) -> dict[str, Any]:
    if not audit:
        return {
            "has_result": False,
            "existence_label": "未保存",
            "certainty_label": "未保存",
            "reasoning": "该轮摘要未记录 A4 evidence audit。",
            "route_label": translate_stage(route_stage),
        }
    return {
        "has_result": True,
        "existence": audit.get("existence"),
        "existence_label": translate_existence(audit.get("existence")),
        "certainty": audit.get("certainty"),
        "certainty_label": translate_certainty(audit.get("certainty")),
        "reasoning": audit.get("reasoning", ""),
        "supporting_span": audit.get("supporting_span", ""),
        "negation_span": audit.get("negation_span", ""),
        "uncertain_span": audit.get("uncertain_span", ""),
        "route_label": translate_stage(route_stage),
        "evidence_families": audit.get("evidence_families", []),
        "entered_confirmed_family": audit.get("entered_confirmed_family", False),
        "provisional_family_candidate": audit.get("provisional_family_candidate", False),
    }


def _safety_from_focused_turn(turn: dict[str, Any]) -> dict[str, Any]:
    guarded_block = turn.get("best_answer_guarded_acceptance_block_reason", "")
    reject_reason = turn.get("reject_reason") or turn.get("best_answer_verifier_reject_reason", "")
    return {
        "verifier_should_accept": turn.get("best_answer_verifier_should_accept", False),
        "verifier_score": turn.get("best_answer_verifier_score"),
        "verifier_reject_reason": reject_reason,
        "reject_reason_label": translate_reject_reason(reject_reason),
        "guarded_acceptance_blocked": bool(guarded_block),
        "guarded_block_reason": guarded_block,
        "guarded_block_reason_label": translate_guarded_block(guarded_block),
        "tree_rerooted": bool(turn.get("rerooted")),
        "reroot_reason": turn.get("reroot_reason", ""),
        "repair_mode": turn.get("repair_mode"),
        "repair_mode_label": translate_repair_mode(turn.get("repair_mode")),
        "missing_evidence_families": turn.get("best_answer_guarded_missing_evidence_families", []),
        "alternative_candidates": turn.get("alternative_candidates") or turn.get("best_answer_verifier_alternative_candidates") or [],
        "recommended_next_evidence": turn.get("recommended_next_evidence", []),
        "root_best_action": _action_name(_as_dict(turn.get("root_best_action"))),
        "repair_selected_action": _action_name(_as_dict(turn.get("repair_selected_action"))),
        "pcp_combo_uses_provisional": bool(turn.get("best_answer_guarded_pcp_combo_uses_provisional")),
        "confirmed_families": turn.get("best_answer_guarded_confirmed_key_evidence_families", []),
        "provisional_families": turn.get("best_answer_guarded_provisional_key_evidence_families", []),
    }


def _candidates_from_focused_turn(turn: dict[str, Any], record: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = []
    best_name = turn.get("best_answer_name") or record.get("final_best_answer_name", "")
    if best_name:
        candidates.append(
            {
                "name": best_name,
                "score": turn.get("best_answer_verifier_score", 0),
                "score_text": str(turn.get("best_answer_verifier_score", "")),
                "reasoning": "当前搜索聚合 best answer。",
                "is_primary": True,
            }
        )
    for item in turn.get("alternative_candidates") or turn.get("best_answer_verifier_alternative_candidates") or []:
        item_dict = _as_dict(item)
        candidates.append(
            {
                "name": item_dict.get("answer_name") or item_dict.get("name") or "备选诊断",
                "score": 0,
                "score_text": "",
                "reasoning": item_dict.get("reason", ""),
                "is_primary": False,
            }
        )
    return candidates[:5]


def _final_scores_from_focused_turn(turn: dict[str, Any], record: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "answer_name": turn.get("best_answer_name") or record.get("final_best_answer_name", ""),
            "final_score": turn.get("best_answer_verifier_score"),
            "consistency": None,
            "diversity": None,
            "agent_evaluation": turn.get("best_answer_verifier_score"),
        }
    ]


def _candidates_from_final_report(final_report: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = []
    for item in final_report.get("candidate_hypotheses", [])[:5]:
        item_dict = _as_dict(item)
        candidates.append(
            {
                "name": item_dict.get("name", "候选诊断"),
                "score": item_dict.get("score", 0),
                "score_text": str(item_dict.get("score", "")),
                "reasoning": "来自 final_report.candidate_hypotheses。",
                "is_primary": not candidates,
            }
        )
    return candidates


def _candidates_from_summary_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    best_answer = record.get("best_answer_name") or record.get("final_best_answer_name") or _final_report_answer(record)
    if not best_answer:
        return []
    return [
        {
            "name": best_answer,
            "score": 1.0 if record.get("is_best_answer_correct") else 0.0,
            "score_text": "",
            "reasoning": f"接受类别：{record.get('acceptance_category', '未保存')}",
            "is_primary": True,
        }
    ]


def _search_from_final_report(final_report: dict[str, Any]) -> dict[str, Any]:
    answer_scores = final_report.get("answer_group_scores") or []
    best_score = _as_dict(answer_scores[0]) if answer_scores else {}
    return {
        "rollouts": "—",
        "tree_node_count": "—",
        "trajectory_count": final_report.get("trajectory_count", "—"),
        "best_answer": _as_dict(final_report.get("best_final_answer")).get("answer_name", ""),
        "consistency": best_score.get("consistency"),
        "diversity": best_score.get("diversity"),
        "agent_evaluation": best_score.get("agent_evaluation"),
        "verifier_result": final_report.get("stop_reason", "replay final_report"),
        "final_answer_scores": answer_scores[:5],
    }


def _question_from_action(action: dict[str, Any]) -> str:
    name = _action_name(action)
    if not name:
        return ""
    question_type = _question_type(action)
    if question_type == "lab":
        return f"我想确认一下，之前有没有做过和“{name}”相关的检查，结果是否提示异常？"
    return f"我想进一步确认：是否存在“{name}”相关情况？"


def _repair_reason(turn: dict[str, Any]) -> str:
    if turn.get("repair_mode"):
        return (
            f"修复模式：{translate_repair_mode(turn.get('repair_mode'))}。"
            f"拒停原因：{translate_reject_reason(turn.get('reject_reason'))}。"
        )
    return "根据实验摘要，本轮使用 root best action 或常规搜索动作。"


def _verifier_result_text(turn: dict[str, Any]) -> str:
    if turn.get("best_answer_verifier_should_accept") is True:
        return f"复核器倾向允许停止，原因：{turn.get('best_answer_verifier_accept_reason') or '未记录'}"
    if turn.get("best_answer_verifier_should_accept") is False:
        return f"复核器建议继续，原因：{translate_reject_reason(turn.get('best_answer_verifier_reject_reason'))}"
    return "未记录 verifier 结果"


def _final_reason_text(record: dict[str, Any], turn: dict[str, Any]) -> str:
    pieces = []
    if record.get("acceptance_category"):
        pieces.append(f"接受分类：{record['acceptance_category']}")
    if record.get("is_best_answer_correct") is not None:
        pieces.append(f"答案是否正确：{_correctness_label(record.get('is_best_answer_correct'))}")
    if turn.get("best_answer_guarded_acceptance_block_reason"):
        pieces.append(f"安全闸门阻止：{translate_guarded_block(turn.get('best_answer_guarded_acceptance_block_reason'))}")
    return "；".join(pieces)


def _case_description(record: dict[str, Any]) -> str:
    true_conditions = "、".join(str(item) for item in record.get("true_conditions", [])) or "未标注"
    return (
        f"真实诊断：{true_conditions}；"
        f"阶段：{record.get('true_disease_phase') or '未标注'}；"
        f"来源：{record.get('_source_file', '未知文件')}"
    )


def _count_repair_turns(turns: list[dict[str, Any]]) -> int:
    return sum(1 for item in turns if _as_dict(item).get("repair_mode"))


def _count_semantic_repeats(turns: list[dict[str, Any]]) -> int:
    return sum(1 for item in turns if _as_dict(item).get("semantic_repeat_as_previous"))


def _final_report_answer(record: dict[str, Any]) -> str:
    final_report = _as_dict(record.get("final_report"))
    best = _as_dict(final_report.get("best_final_answer"))
    return best.get("answer_name", "")


def _correctness_label(value: Any) -> str:
    if value is True:
        return "正确"
    if value is False:
        return "错误"
    return "未标注"


def _action_name(action: dict[str, Any]) -> str:
    return action.get("target_node_name") or action.get("name") or ""


def _action_id(action: dict[str, Any]) -> str:
    return action.get("action_id") or action.get("target_node_id") or ""


def _question_type(action: dict[str, Any]) -> str:
    metadata = _as_dict(action.get("metadata"))
    return metadata.get("question_type_hint") or action.get("question_type_hint") or "unknown"


def _relative_output_path(path: Path) -> str:
    try:
        return path.relative_to(DEFAULT_OUTPUT_ROOT).as_posix()
    except ValueError:
        return str(path)


def _run_detail_rank(file_kinds: tuple[str, ...]) -> int:
    """让可逐轮复盘的详细目录排在只有汇总的目录前面。"""

    if "focused repair cases" in file_kinds:
        return 4
    if "replay cases" in file_kinds:
        return 3
    if "ablation cases" in file_kinds:
        return 2
    if "A4 evidence audit" in file_kinds or "guarded gate audit" in file_kinds:
        return 1
    return 0


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _read_jsonl(path: Path, max_records: int = 500) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if len(records) >= max_records:
                    break
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                if isinstance(item, dict):
                    records.append(item)
    except (OSError, json.JSONDecodeError):
        return records
    return records


def _read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            return list(csv.DictReader(handle, delimiter="\t"))
    except OSError:
        return []


def _read_tail(path: Path, line_count: int = 20) -> str:
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-line_count:])


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
