"""分析 benchmark 中 candidate_hypotheses 的 Top-3 覆盖表现。"""

from __future__ import annotations

import argparse
import json
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


FAMILY_MATCH_RATIO_THRESHOLD = 0.88
SLICE_NAMES = (
    "hypothesis_hit_but_top3_miss",
    "candidate_miss",
    "top3_hit_but_top1_miss",
    "top1_hit_but_top3_miss_if_any",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="分析 candidate_hypotheses 的 Top-3 覆盖与 case slice。")
    parser.add_argument("--run-dir", required=True, help="benchmark 输出目录，需包含 replay_results.jsonl。")
    parser.add_argument(
        "--output-dir",
        default="",
        help="分析输出目录；默认写到 run-dir/top3_candidate_coverage_analysis。",
    )
    return parser.parse_args()


def _normalize_text(value: object) -> str:
    return "".join(str(value or "").strip().lower().split())


def _is_name_match(left: str, right: str, *, match_mode: str) -> bool:
    if len(left) == 0 or len(right) == 0:
        return False
    if left == right:
        return True
    if match_mode != "family":
        return False
    if left in right or right in left:
        return True
    return SequenceMatcher(None, left, right).ratio() >= FAMILY_MATCH_RATIO_THRESHOLD


def _expected_targets(row: dict[str, Any]) -> list[str]:
    targets = [str(item).strip() for item in (row.get("true_conditions") or []) if len(str(item).strip()) > 0]
    true_disease_phase = str(row.get("true_disease_phase") or "").strip()
    if len(true_disease_phase) > 0:
        targets.append(true_disease_phase)
    return targets


def _matches_expected_answer(answer_name: str, row: dict[str, Any], *, match_mode: str) -> bool:
    normalized_answer = _normalize_text(answer_name)
    if len(normalized_answer) == 0:
        return False

    for expected in _expected_targets(row):
        if _is_name_match(normalized_answer, _normalize_text(expected), match_mode=match_mode):
            return True
    return False


def _rank_of_gold(candidate_names: list[str], row: dict[str, Any], *, match_mode: str) -> tuple[int | None, str]:
    normalized_candidates = [_normalize_text(item) for item in candidate_names]

    for expected in _expected_targets(row):
        normalized_expected = _normalize_text(expected)
        for index, candidate_name in enumerate(normalized_candidates, start=1):
            if _is_name_match(candidate_name, normalized_expected, match_mode=match_mode):
                return index, expected
    return None, ""


def _extract_top3_answers(report: dict[str, Any]) -> list[str]:
    scores = report.get("answer_group_scores") or report.get("final_answer_scores") or []
    if not isinstance(scores, list):
        return []
    return [
        str(item.get("answer_name") or "").strip()
        for item in scores[:3]
        if isinstance(item, dict) and len(str(item.get("answer_name") or "").strip()) > 0
    ]


def _extract_final_answer_name(report: dict[str, Any]) -> str:
    best_final_answer = report.get("best_final_answer")
    if isinstance(best_final_answer, dict):
        answer_name = str(best_final_answer.get("answer_name") or "").strip()
        if len(answer_name) > 0:
            return answer_name

    top3_answers = _extract_top3_answers(report)
    if len(top3_answers) > 0:
        return top3_answers[0]

    return str(report.get("best_answer_name") or "").strip()


def _extract_first_action(row: dict[str, Any]) -> dict[str, Any]:
    turns = row.get("turns") or []
    if not isinstance(turns, list) or len(turns) == 0 or not isinstance(turns[0], dict):
        return {}

    search_report = turns[0].get("search_report") or {}
    if not isinstance(search_report, dict):
        return {}

    selected_action = search_report.get("selected_action") or search_report.get("root_best_action") or {}
    return dict(selected_action) if isinstance(selected_action, dict) else {}


def _is_accepted(row: dict[str, Any], report: dict[str, Any]) -> bool:
    analysis = row.get("analysis") or {}
    if isinstance(analysis, dict) and "accepted_final_answer" in analysis:
        return bool(analysis.get("accepted_final_answer"))
    stop_reason = str(report.get("stop_reason") or "")
    return row.get("status") == "completed" or stop_reason == "final_answer_accepted"


def _build_case_payload(row: dict[str, Any]) -> dict[str, Any]:
    report = row.get("final_report") or {}
    candidates = report.get("candidate_hypotheses") or []
    candidate_items = [dict(item) for item in candidates if isinstance(item, dict)]
    candidate_names = [str(item.get("name") or "").strip() for item in candidate_items if len(str(item.get("name") or "").strip()) > 0]
    gold_rank, matched_gold_name = _rank_of_gold(candidate_names, row, match_mode="family")
    top3_hypotheses = candidate_names[:3]
    final_top1_answer = _extract_final_answer_name(report)
    final_top3_answers = _extract_top3_answers(report)
    top1_final_answer_hit = _matches_expected_answer(final_top1_answer, row, match_mode="exact")
    top3_hypothesis_hit = gold_rank is not None and gold_rank <= 3

    return {
        "case_id": str(row.get("case_id") or ""),
        "case_title": str(row.get("case_title") or ""),
        "gold_diagnosis": matched_gold_name or (_expected_targets(row)[0] if len(_expected_targets(row)) > 0 else ""),
        "gold_diagnoses": _expected_targets(row),
        "candidate_hypotheses": candidate_items,
        "gold_rank": gold_rank,
        "top3_hypotheses": top3_hypotheses,
        "final_top1_answer": final_top1_answer,
        "final_top3_answers": final_top3_answers,
        "top1_final_answer_hit": top1_final_answer_hit,
        "hypothesis_hit": gold_rank is not None,
        "top3_hypothesis_hit": top3_hypothesis_hit,
        "accepted": _is_accepted(row, report),
        "status": str(row.get("status") or ""),
        "turns": len(row.get("turns") or []),
        "selected_first_action": _extract_first_action(row),
        "answer_group_scores": [
            dict(item)
            for item in (report.get("answer_group_scores") or [])
            if isinstance(item, dict)
        ][:5],
    }


def analyze_run(run_dir: Path, output_dir: Path | None = None) -> dict[str, Any]:
    replay_results_path = run_dir / "replay_results.jsonl"
    summary_path = run_dir / "benchmark_summary.json"
    if not replay_results_path.exists():
        raise FileNotFoundError(f"未找到 replay_results.jsonl: {replay_results_path}")

    rows = [
        json.loads(line)
        for line in replay_results_path.read_text(encoding="utf-8").splitlines()
        if len(line.strip()) > 0
    ]
    benchmark_summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    destination = output_dir or (run_dir / "top3_candidate_coverage_analysis")
    destination.mkdir(parents=True, exist_ok=True)

    slices: dict[str, list[dict[str, Any]]] = {name: [] for name in SLICE_NAMES}
    case_payloads = [_build_case_payload(row) for row in rows]

    for payload in case_payloads:
        hypothesis_hit = bool(payload.get("hypothesis_hit"))
        top3_hit = bool(payload.get("top3_hypothesis_hit"))
        top1_hit = bool(payload.get("top1_final_answer_hit"))

        if hypothesis_hit and not top3_hit:
            slices["hypothesis_hit_but_top3_miss"].append(payload)
        if not hypothesis_hit:
            slices["candidate_miss"].append(payload)
        if top3_hit and not top1_hit:
            slices["top3_hit_but_top1_miss"].append(payload)
        if top1_hit and not top3_hit:
            slices["top1_hit_but_top3_miss_if_any"].append(payload)

    summary = {
        "run_dir": str(run_dir),
        "case_count": int(benchmark_summary.get("case_count", len(case_payloads)) or len(case_payloads)),
        "hypothesis_hit_count": int(
            benchmark_summary.get(
                "hypothesis_hit_count",
                sum(1 for item in case_payloads if bool(item.get("hypothesis_hit"))),
            )
            or 0
        ),
        "top3_hypothesis_hit_count": int(
            benchmark_summary.get(
                "top3_hypothesis_hit_count",
                sum(1 for item in case_payloads if bool(item.get("top3_hypothesis_hit"))),
            )
            or 0
        ),
        "hypothesis_hit_but_top3_miss_count": len(slices["hypothesis_hit_but_top3_miss"]),
        "candidate_miss_count": len(slices["candidate_miss"]),
        "top3_hit_but_top1_miss_count": len(slices["top3_hit_but_top1_miss"]),
        "top1_hit_but_top3_miss_if_any_count": len(slices["top1_hit_but_top3_miss_if_any"]),
        "top1_final_answer_hit_count": int(
            benchmark_summary.get(
                "top1_final_answer_hit_count",
                sum(1 for item in case_payloads if bool(item.get("top1_final_answer_hit"))),
            )
            or 0
        ),
    }

    (destination / "coverage_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    for slice_name, records in slices.items():
        with (destination / f"{slice_name}.jsonl").open("w", encoding="utf-8") as file:
            for record in records:
                file.write(json.dumps(record, ensure_ascii=False))
                file.write("\n")

    return {
        "summary": summary,
        "slices": slices,
        "output_dir": str(destination),
    }


def main() -> None:
    args = parse_args()
    result = analyze_run(
        run_dir=Path(args.run_dir).resolve(),
        output_dir=Path(args.output_dir).resolve() if len(args.output_dir.strip()) > 0 else None,
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
