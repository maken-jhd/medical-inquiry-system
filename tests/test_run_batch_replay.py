"""测试 batch replay 脚本的并发调度逻辑。"""

from __future__ import annotations

import sys
import time
from types import SimpleNamespace
from pathlib import Path

from scripts import run_batch_replay
from simulator.replay_engine import ReplayResult


# 验证并发运行多个病例时，返回结果仍按输入顺序排列。
def test_run_cases_parallel_preserves_input_order(monkeypatch) -> None:
    cases = [
        SimpleNamespace(case_id="case1"),
        SimpleNamespace(case_id="case2"),
        SimpleNamespace(case_id="case3"),
    ]

    def fake_run_single_case(case, max_turns: int):
        _ = max_turns
        delays = {"case1": 0.03, "case2": 0.01, "case3": 0.02}
        time.sleep(delays[case.case_id])
        return case.case_id

    monkeypatch.setattr(run_batch_replay, "_run_single_case", fake_run_single_case)

    results = run_batch_replay._run_cases(cases, max_turns=8, case_concurrency=3)

    assert results == ["case1", "case2", "case3"]


# 验证命令行参数支持配置病例级并发。
def test_parse_args_supports_case_concurrency(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_batch_replay.py", "--cases-file", "cases.jsonl", "--case-concurrency", "4"],
    )

    args = run_batch_replay.parse_args()

    assert args.cases_file == "cases.jsonl"
    assert args.case_concurrency == 4


# 验证命令行参数支持限制只运行前 N 个病例。
def test_parse_args_supports_limit(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_batch_replay.py", "--cases-file", "cases.jsonl", "--limit", "10"],
    )

    args = run_batch_replay.parse_args()

    assert args.cases_file == "cases.jsonl"
    assert args.limit == 10


# 验证命令行参数支持关闭断点续跑。
def test_parse_args_supports_no_resume(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_batch_replay.py", "--cases-file", "cases.jsonl", "--no-resume"],
    )

    args = run_batch_replay.parse_args()

    assert args.cases_file == "cases.jsonl"
    assert args.no_resume is True


# 验证 batch replay 会持续报告病例级进度。
def test_run_cases_reports_progress(monkeypatch) -> None:
    cases = [
        SimpleNamespace(case_id="case1"),
        SimpleNamespace(case_id="case2"),
    ]
    reported: list[tuple[int, int, bool]] = []

    def fake_run_single_case(case, max_turns: int):
        _ = case, max_turns
        return "ok"

    def fake_progress(completed: int, total: int, *, finished: bool = False) -> None:
        reported.append((completed, total, finished))

    monkeypatch.setattr(run_batch_replay, "_run_single_case", fake_run_single_case)

    results = run_batch_replay._run_cases(
        cases,
        max_turns=8,
        case_concurrency=2,
        progress_callback=fake_progress,
    )

    assert results == ["ok", "ok"]
    assert reported[0] == (0, 2, False)
    assert reported[-1] == (2, 2, True)
    assert len(reported) == 3


def test_format_heartbeat_line_reports_oldest_active_case(monkeypatch) -> None:
    monkeypatch.setattr(run_batch_replay, "time", lambda: 130.0)

    heartbeat_line = run_batch_replay._format_heartbeat_line(
        completed=2,
        total=10,
        active_cases=[
            {"case_id": "case-old", "started_epoch": 100.0},
            {"case_id": "case-new", "started_epoch": 120.0},
        ],
    )

    assert "已完成病例：2 / 10" in heartbeat_line
    assert "活动病例：2" in heartbeat_line
    assert "case-old" in heartbeat_line
    assert "00:30" in heartbeat_line


def test_build_status_payload_hides_internal_active_case_fields() -> None:
    payload = run_batch_replay._build_status_payload(
        run_status="running",
        total_cases=10,
        completed_cases=2,
        skipped_completed_cases=1,
        case_concurrency=4,
        case_file="cases.jsonl",
        case_limit=10,
        output_root=Path("/tmp/replay"),
        start_time="2026-04-26T12:00:00",
        active_cases=[
            {
                "case_id": "case1",
                "case_title": "病例1",
                "started_at": "2026-04-26T12:01:00",
                "started_epoch": 123.0,
            }
        ],
        timing_summary={},
    )

    assert payload["active_cases"] == [
        {
            "case_id": "case1",
            "case_title": "病例1",
            "started_at": "2026-04-26T12:01:00",
        }
    ]


def test_payload_to_replay_result_preserves_turn_timing_fields() -> None:
    payload = {
        "case_id": "case1",
        "turns": [
            {
                "question_node_id": "slot1",
                "question_text": "有没有发热？",
                "answer_text": "有。",
                "turn_index": 1,
                "revealed_slot_id": "slot1",
                "stage": "A3",
                "patient_answer_seconds": 1.23,
                "brain_turn_seconds": 4.56,
                "total_seconds": 5.79,
            }
        ],
        "timing": {"total_seconds": 5.8},
    }

    result = run_batch_replay._payload_to_replay_result(payload)

    assert len(result.turns) == 1
    assert result.turns[0].patient_answer_seconds == 1.23
    assert result.turns[0].brain_turn_seconds == 4.56
    assert result.turns[0].total_seconds == 5.79


# 验证主流程会跳过已完成病例，并把新结果直接追加写入输出目录。
def test_main_resume_skips_completed_cases_and_appends_outputs(monkeypatch, tmp_path: Path) -> None:
    output_root = tmp_path / "replay_run"
    output_root.mkdir(parents=True, exist_ok=True)
    replay_file = output_root / "replay_results.jsonl"

    existing = ReplayResult(case_id="case1", case_title="已完成病例", status="completed")
    replay_file.write_text(
        "\n".join(
            [
                __import__("json").dumps(
                    run_batch_replay._replay_result_to_payload(existing),
                    ensure_ascii=False,
                )
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    cases = [
        SimpleNamespace(case_id="case1"),
        SimpleNamespace(case_id="case2"),
    ]
    called_case_ids: list[str] = []

    def fake_load_cases_jsonl(path):
        _ = path
        return cases

    def fake_run_single_case(case, max_turns: int):
        _ = max_turns
        called_case_ids.append(case.case_id)
        return ReplayResult(case_id=case.case_id, case_title=f"title-{case.case_id}", status="completed")

    monkeypatch.setattr(run_batch_replay, "load_cases_jsonl", fake_load_cases_jsonl)
    monkeypatch.setattr(run_batch_replay, "_run_single_case", fake_run_single_case)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_batch_replay.py",
            "--cases-file",
            "cases.jsonl",
            "--output-root",
            str(output_root),
            "--case-concurrency",
            "1",
        ],
    )

    exit_code = run_batch_replay.main()

    assert exit_code == 0
    assert called_case_ids == ["case2"]

    lines = replay_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    status_payload = __import__("json").loads((output_root / "status.json").read_text(encoding="utf-8"))
    assert status_payload["status"] == "completed"
    assert status_payload["completed_cases"] == 2
    assert status_payload["skipped_completed_cases"] == 1
    assert "timing_summary" in status_payload

    benchmark_payload = __import__("json").loads((output_root / "benchmark_summary.json").read_text(encoding="utf-8"))
    assert "timing_summary" in benchmark_payload
    run_log = (output_root / "run.log").read_text(encoding="utf-8")
    assert "已完成 1" in run_log
    assert "病例完成：case_id=case2" in run_log


# 验证用户中断时，batch replay 会写状态后强制退出，避免线程池拖住进程。
def test_main_forces_exit_on_keyboard_interrupt(monkeypatch, tmp_path: Path) -> None:
    output_root = tmp_path / "interrupt_run"
    output_root.mkdir(parents=True, exist_ok=True)
    forced_exit_codes: list[int] = []

    cases = [SimpleNamespace(case_id="case1")]

    def fake_load_cases_jsonl(path):
        _ = path
        return cases

    def fake_run_cases_streaming(*args, **kwargs):
        _ = args, kwargs
        raise KeyboardInterrupt

    def fake_force_exit(code: int = 130):
        forced_exit_codes.append(code)
        raise SystemExit(code)

    monkeypatch.setattr(run_batch_replay, "load_cases_jsonl", fake_load_cases_jsonl)
    monkeypatch.setattr(run_batch_replay, "_run_cases_streaming", fake_run_cases_streaming)
    monkeypatch.setattr(run_batch_replay, "_force_exit_after_interrupt", fake_force_exit)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_batch_replay.py",
            "--cases-file",
            "cases.jsonl",
            "--output-root",
            str(output_root),
        ],
    )

    try:
        run_batch_replay.main()
    except SystemExit as exc:
        assert exc.code == 130

    assert forced_exit_codes == [130]
    status_payload = __import__("json").loads((output_root / "status.json").read_text(encoding="utf-8"))
    assert status_payload["status"] == "interrupted"


# 验证启动前若 llm_available=false，batch replay 会尽早失败并写出 failed 状态。
def test_main_fails_fast_when_llm_unavailable(monkeypatch, tmp_path: Path) -> None:
    output_root = tmp_path / "llm_unavailable_run"
    output_root.mkdir(parents=True, exist_ok=True)
    cases = [SimpleNamespace(case_id="case1")]

    class FakeLlmClient:
        def is_available(self) -> bool:
            return False

    monkeypatch.setattr(run_batch_replay, "load_frontend_config", lambda: {})
    monkeypatch.setattr(run_batch_replay, "apply_config_to_environment", lambda config: None)
    monkeypatch.setattr(run_batch_replay, "load_cases_jsonl", lambda path: cases)
    monkeypatch.setattr(run_batch_replay, "LlmClient", FakeLlmClient)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_batch_replay.py",
            "--cases-file",
            "cases.jsonl",
            "--output-root",
            str(output_root),
        ],
    )

    exit_code = run_batch_replay.main()

    assert exit_code == 1
    status_payload = __import__("json").loads((output_root / "status.json").read_text(encoding="utf-8"))
    assert status_payload["status"] == "failed"
    assert status_payload["completed_cases"] == 0
    run_log = (output_root / "run.log").read_text(encoding="utf-8")
    assert "llm_available=false" in run_log
