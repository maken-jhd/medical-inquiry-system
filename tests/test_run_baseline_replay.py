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


# 验证 runner 支持 text_rag 模式相关参数。
def test_parse_args_supports_text_rag_options(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_baseline_replay.py",
            "--baseline-mode",
            "text_rag",
            "--rag-corpus-file",
            "rag_corpus.jsonl",
            "--retrieval-top-k",
            "3",
        ],
    )

    args = run_baseline_replay.parse_args()

    assert args.baseline_mode == "text_rag"
    assert args.rag_corpus_file == "rag_corpus.jsonl"
    assert args.retrieval_top_k == 3


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
        rag_corpus_file: str = "",
        retrieval_top_k: int = 4,
        api_error_retries: int = 1,
        on_case_start=None,
        on_result=None,
        progress_callback=None,
    ) -> None:
        _ = max_turns, case_concurrency, baseline_mode, rag_corpus_file, retrieval_top_k, api_error_retries, progress_callback
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


# 验证 text_rag 主流程会把语料路径和 top-k 传给 runner，并沿用同一 summary schema。
def test_main_runs_text_rag_with_corpus_args(monkeypatch, tmp_path: Path) -> None:
    output_root = tmp_path / "text_rag_completed"
    output_root.mkdir(parents=True, exist_ok=True)
    rag_corpus_file = tmp_path / "text_rag_corpus.jsonl"
    rag_corpus_file.write_text(
        json.dumps(
            {
                "doc_id": "pcp_doc",
                "disease_name": "肺孢子菌肺炎",
                "title": "PCP 画像",
                "content": "干咳、呼吸困难、低氧血症。",
                "tags": ["机会性感染"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    rag_corpus_file_path = rag_corpus_file
    cases = [
        SimpleNamespace(
            case_id="case_text_rag",
            title="文本 RAG 病例",
            true_conditions=["肺孢子菌肺炎"],
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
        rag_corpus_file: str = "",
        retrieval_top_k: int = 4,
        api_error_retries: int = 1,
        on_case_start=None,
        on_result=None,
        progress_callback=None,
    ) -> None:
        _ = max_turns, case_concurrency, api_error_retries, progress_callback
        assert baseline_mode == "text_rag"
        assert disease_scope == ["肺孢子菌肺炎", "巨细胞病毒(CMV)肺炎"]
        assert rag_corpus_file == str(rag_corpus_file_path.resolve())
        assert retrieval_top_k == 2
        case = pending_cases[0]
        if on_case_start is not None:
            on_case_start(case, 1, 1)
        result = ReplayResult(
            case_id="case_text_rag",
            case_title="文本 RAG 病例",
            true_conditions=["肺孢子菌肺炎"],
            final_report={
                "candidate_hypotheses": [{"name": "肺孢子菌肺炎", "score": 0.89}],
                "best_final_answer": {"answer_name": "肺孢子菌肺炎", "confidence": 0.89},
                "stop_reason": "final_answer_accepted",
                "metadata": {"backend": "llm_text_rag"},
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
        lambda cases_file, loaded_cases: (["肺孢子菌肺炎", "巨细胞病毒(CMV)肺炎"], "manifest:/tmp/manifest.json"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_baseline_replay.py",
            "--baseline-mode",
            "text_rag",
            "--rag-corpus-file",
            str(rag_corpus_file),
            "--retrieval-top-k",
            "2",
            "--cases-file",
            "cases.jsonl",
            "--output-root",
            str(output_root),
        ],
    )

    exit_code = run_baseline_replay.main()

    assert exit_code == 0
    summary_payload = json.loads((output_root / "benchmark_summary.json").read_text(encoding="utf-8"))
    assert summary_payload["baseline_mode"] == "text_rag"
    assert summary_payload["rag_corpus_file"] == str(rag_corpus_file.resolve())
    assert summary_payload["retrieval_top_k"] == 2
