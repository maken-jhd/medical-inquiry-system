"""测试纯 LLM baseline batch runner 的参数与结果写盘行为。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from scripts import run_baseline_replay
from simulator.replay_engine import ReplayResult


# 验证 baseline runner 支持 baseline_mode 与病例级并发参数。
def test_parse_args_supports_baseline_mode_and_case_concurrency(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_baseline_replay.py",
            "--baseline-mode",
            "pure_llm",
            "--cases-file",
            "cases.jsonl",
            "--case-concurrency",
            "4",
        ],
    )

    args = run_baseline_replay.parse_args()

    assert args.baseline_mode == "pure_llm"
    assert args.cases_file == "cases.jsonl"
    assert args.case_concurrency == 4


# 验证 runner 会优先从病例目录祖先 manifest 中提取全量 disease scope。
def test_resolve_disease_scope_prefers_parent_manifest(tmp_path: Path) -> None:
    case_root = tmp_path / "graph_cases"
    smoke_root = case_root / "smoke20"
    smoke_root.mkdir(parents=True, exist_ok=True)
    cases_file = smoke_root / "cases.jsonl"
    cases_file.write_text("", encoding="utf-8")
    (case_root / "manifest.json").write_text(
        json.dumps(
            {
                "diseases": [
                    {"disease_name": "活动性结核病"},
                    {"disease_name": "巨细胞病毒(CMV)肺炎"},
                    {"disease_name": "活动性结核病"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    scope, source = run_baseline_replay.resolve_disease_scope(str(cases_file), [])

    assert scope == ["活动性结核病", "巨细胞病毒(CMV)肺炎"]
    assert source.startswith("manifest:")


# 验证启动前若 llm_available=false，baseline runner 会尽早失败并写出 failed 状态。
def test_main_fails_fast_when_llm_unavailable(monkeypatch, tmp_path: Path) -> None:
    output_root = tmp_path / "baseline_llm_unavailable"
    output_root.mkdir(parents=True, exist_ok=True)
    cases = [SimpleNamespace(case_id="case1")]

    class FakeLlmClient:
        def is_available(self) -> bool:
            return False

    monkeypatch.setattr(run_baseline_replay, "load_frontend_config", lambda: {})
    monkeypatch.setattr(run_baseline_replay, "apply_config_to_environment", lambda config: None)
    monkeypatch.setattr(run_baseline_replay, "load_cases_jsonl", lambda path: cases)
    monkeypatch.setattr(run_baseline_replay, "LlmClient", FakeLlmClient)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_baseline_replay.py",
            "--cases-file",
            "cases.jsonl",
            "--output-root",
            str(output_root),
        ],
    )

    exit_code = run_baseline_replay.main()

    assert exit_code == 1
    status_payload = json.loads((output_root / "status.json").read_text(encoding="utf-8"))
    assert status_payload["status"] == "failed"
    assert status_payload["completed_cases"] == 0
    non_completed_payload = json.loads((output_root / "non_completed_cases.json").read_text(encoding="utf-8"))
    assert non_completed_payload["case_count"] == 0


# 验证主流程会沿用现有 summary schema，并额外记录 baseline_mode。
def test_main_writes_completed_summary_with_baseline_mode(monkeypatch, tmp_path: Path) -> None:
    output_root = tmp_path / "baseline_completed"
    output_root.mkdir(parents=True, exist_ok=True)
    cases = [
        SimpleNamespace(
            case_id="case1",
            title="病例1",
            true_conditions=["活动性结核病"],
            true_disease_phase=None,
            red_flags=[],
            metadata={
                "case_type": "ordinary",
                "case_qc_status": "eligible",
                "benchmark_qc_status": "eligible",
                "case_qc_reasons": [],
            },
        )
    ]

    class FakeLlmClient:
        def is_available(self) -> bool:
            return True

        def close(self) -> None:
            return None

    def fake_run_cases_streaming(
        pending_cases,
        *,
        max_turns: int,
        case_concurrency: int,
        baseline_mode: str,
        disease_scope: list[str],
        api_error_retries: int = 1,
        on_case_start=None,
        on_result=None,
        progress_callback=None,
    ) -> None:
        _ = max_turns, case_concurrency, baseline_mode, api_error_retries, progress_callback
        assert disease_scope == ["活动性结核病", "巨细胞病毒(CMV)肺炎"]
        case = pending_cases[0]
        if on_case_start is not None:
            on_case_start(case, 1, 1)
        result = ReplayResult(
            case_id="case1",
            case_title="病例1",
            true_conditions=["活动性结核病"],
            final_report={
                "candidate_hypotheses": [{"name": "活动性结核病", "score": 0.91}],
                "best_final_answer": {"answer_name": "活动性结核病", "confidence": 0.91},
                "stop_reason": "final_answer_accepted",
                "metadata": {"backend": "pure_llm"},
            },
            analysis={"question_count_total": 0},
            status="completed",
            timing={"total_seconds": 0.0},
        )
        if on_result is not None:
            on_result(result, case, 1, 1)

    monkeypatch.setattr(run_baseline_replay, "load_frontend_config", lambda: {})
    monkeypatch.setattr(run_baseline_replay, "apply_config_to_environment", lambda config: None)
    monkeypatch.setattr(run_baseline_replay, "load_cases_jsonl", lambda path: cases)
    monkeypatch.setattr(run_baseline_replay, "_run_cases_streaming", fake_run_cases_streaming)
    monkeypatch.setattr(run_baseline_replay, "LlmClient", FakeLlmClient)
    monkeypatch.setattr(
        run_baseline_replay,
        "resolve_disease_scope",
        lambda cases_file, loaded_cases: (["活动性结核病", "巨细胞病毒(CMV)肺炎"], "manifest:/tmp/manifest.json"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_baseline_replay.py",
            "--cases-file",
            "cases.jsonl",
            "--output-root",
            str(output_root),
        ],
    )

    exit_code = run_baseline_replay.main()

    assert exit_code == 0
    summary_payload = json.loads((output_root / "benchmark_summary.json").read_text(encoding="utf-8"))
    status_payload = json.loads((output_root / "status.json").read_text(encoding="utf-8"))
    replay_lines = (output_root / "replay_results.jsonl").read_text(encoding="utf-8").strip().splitlines()

    assert summary_payload["baseline_mode"] == "pure_llm"
    assert summary_payload["disease_scope_count"] == 2
    assert summary_payload["disease_scope_source"] == "manifest:/tmp/manifest.json"
    assert summary_payload["case_count"] == 1
    assert summary_payload["eligible_summary"]["case_count"] == 1
    assert status_payload["status"] == "completed"
    assert len(replay_lines) == 1
