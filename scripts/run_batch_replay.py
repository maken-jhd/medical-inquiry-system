"""批量运行虚拟病人回放并输出评测摘要。"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import sleep, time
from typing import Any


# 将项目根目录加入导入路径，确保脚本可直接运行。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain.llm_client import LlmClient
from brain.service import build_default_brain_from_env
from frontend.config_loader import apply_config_to_environment, load_frontend_config
from simulator.benchmark import build_non_completed_case_report, summarize_benchmark
from simulator.generate_cases import build_seed_cases, load_cases_jsonl, write_cases_jsonl
from simulator.patient_agent import VirtualPatientAgent
from simulator.replay_engine import ReplayConfig, ReplayEngine, ReplayResult, ReplayTurn


_TERMINAL_HANDLE = None
_TERMINAL_LOCK = threading.Lock()
_HEARTBEAT_INTERVAL_SECONDS = 15.0
_WORKER_RUNTIME_LOCAL = threading.local()
_WORKER_RUNTIME_LOCK = threading.Lock()
_WORKER_RUNTIMES: list["_ReplayWorkerRuntime"] = []


@dataclass
class _ReplayWorkerRuntime:
    """保存单个 batch worker 会跨病例复用的轻量运行资源。"""

    llm_client: LlmClient

    def close(self) -> None:
        self.llm_client.close()


# 解析命令行参数。
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量运行虚拟病人回放并输出指标。")
    parser.add_argument(
        "--cases-file",
        default="",
        help="可选的病例 JSON/JSONL 文件；不提供时将使用内置 seed cases。",
    )
    parser.add_argument(
        "--output-root",
        default=str(PROJECT_ROOT / "test_outputs" / "simulator_replay"),
        help="批量回放输出目录。",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=8,
        help="单个病例最大追问轮次。",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="只运行前 N 个病例；0 表示不限制。",
    )
    parser.add_argument(
        "--case-concurrency",
        type=int,
        default=4,
        help="病例级并发数；每个并发任务使用独立 brain 实例，避免共享会话状态。",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="禁用断点续跑；即使输出目录中已有 replay_results.jsonl，也会重新运行全部病例。",
    )
    parser.add_argument(
        "--api-error-retries",
        type=int,
        default=1,
        help="遇到 APIConnectionError / Connection error 时，单病例额外自动重试次数。",
    )
    return parser.parse_args()


def _read_api_error_cooldown_seconds() -> float:
    raw_value = os.getenv("BATCH_API_ERROR_COOLDOWN_SECONDS") or "2.0"

    try:
        cooldown_seconds = float(raw_value)
    except ValueError:
        cooldown_seconds = 2.0

    return max(cooldown_seconds, 0.0)


def _sleep_before_api_retry(retry_index: int) -> float:
    # batch 外层只在连接错误时做冷却，尽量错开下一次整例重跑的建连脉冲。
    base_seconds = _read_api_error_cooldown_seconds()
    cooldown_seconds = min(base_seconds * (2 ** max(int(retry_index) - 1, 0)), 12.0)

    if cooldown_seconds > 0.0:
        sleep(cooldown_seconds)

    return cooldown_seconds


def _get_worker_runtime() -> _ReplayWorkerRuntime:
    runtime = getattr(_WORKER_RUNTIME_LOCAL, "runtime", None)
    if runtime is not None:
        return runtime

    runtime = _ReplayWorkerRuntime(llm_client=LlmClient())
    _WORKER_RUNTIME_LOCAL.runtime = runtime
    with _WORKER_RUNTIME_LOCK:
        _WORKER_RUNTIMES.append(runtime)
    return runtime


def _cleanup_worker_runtimes(*, reset_current_thread: bool = False) -> None:
    with _WORKER_RUNTIME_LOCK:
        runtimes = list(_WORKER_RUNTIMES)
        _WORKER_RUNTIMES.clear()

    for runtime in runtimes:
        try:
            runtime.close()
        except Exception:
            pass

    if reset_current_thread and hasattr(_WORKER_RUNTIME_LOCAL, "runtime"):
        delattr(_WORKER_RUNTIME_LOCAL, "runtime")


def _run_single_case(case, max_turns: int):
    worker_runtime = _get_worker_runtime()
    brain = build_default_brain_from_env(llm_client=worker_runtime.llm_client)
    patient_agent = VirtualPatientAgent(use_llm=True, llm_client=worker_runtime.llm_client)
    engine = ReplayEngine(
        brain=brain,
        patient_agent=patient_agent,
        config=ReplayConfig(max_turns=max_turns),
    )

    try:
        return engine.run_case(case)
    finally:
        _close_brain(brain)


def _build_unexpected_case_failure_result(case, exc: Exception, *, stage: str) -> ReplayResult:
    return ReplayResult(
        case_id=str(getattr(case, "case_id", "")),
        case_title=str(getattr(case, "title", "")),
        status="failed",
        final_report={},
        timing={
            "started_at": _timestamp(),
            "finished_at": _timestamp(),
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
            "turn_count": 0,
        },
        error={
            "code": "unexpected_runtime_error",
            "stage": stage,
            "prompt_name": "",
            "message": f"{type(exc).__name__}: {exc}",
            "attempts": 1,
            "error_type": type(exc).__name__,
        },
    )


def _run_single_case_guarded(case, max_turns: int, api_error_retries: int = 1):
    retry_count = max(int(api_error_retries), 0)
    retries_used = 0
    total_cooldown_seconds = 0.0

    while True:
        try:
            result = _run_single_case(case, max_turns)
        except Exception as exc:
            if retries_used < retry_count and _is_retryable_api_exception(exc):
                retries_used += 1
                total_cooldown_seconds += _sleep_before_api_retry(retries_used)
                continue

            # batch runner 再兜一层：即使单病例出现普通运行时异常，也只把该病例记为 failed。
            result = _build_unexpected_case_failure_result(case, exc, stage="batch_runner")
            _annotate_batch_retry(result, retries_used, total_cooldown_seconds=total_cooldown_seconds)
            return result

        if retries_used < retry_count and _is_retryable_api_error_result(result):
            retries_used += 1
            total_cooldown_seconds += _sleep_before_api_retry(retries_used)
            continue

        _annotate_batch_retry(result, retries_used, total_cooldown_seconds=total_cooldown_seconds)
        return result


def _is_retryable_api_exception(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}"
    return _contains_retryable_api_error(text)


def _is_retryable_api_error_result(result: object) -> bool:
    if not isinstance(result, ReplayResult) or result.status != "failed":
        return False

    error_payload = dict(result.error or {})
    text = " ".join(
        str(error_payload.get(key) or "")
        for key in ("code", "stage", "prompt_name", "message", "error_type")
    )
    return _contains_retryable_api_error(text)


def _contains_retryable_api_error(text: str) -> bool:
    normalized = text.lower()
    return any(
        marker in normalized
        for marker in (
            "apiconnectionerror",
            "connection error",
            "api connection error",
        )
    )


def _annotate_batch_retry(result: object, retries_used: int, *, total_cooldown_seconds: float = 0.0) -> None:
    if not isinstance(result, ReplayResult):
        return

    result.timing["batch_retry_attempts"] = retries_used
    result.timing["batch_retry_cooldown_seconds_total"] = round(float(total_cooldown_seconds), 4)
    if retries_used <= 0:
        return

    result.timing["retried_after_api_connection_error"] = True
    if result.status == "failed":
        result.error = {
            **dict(result.error or {}),
            "batch_retry_attempts": retries_used,
            "batch_retry_cooldown_seconds_total": round(float(total_cooldown_seconds), 4),
            "retried_after_api_connection_error": True,
        }


def _format_progress_line(completed: int, total: int) -> str:
    bar_width = 24
    if total <= 0:
        filled = bar_width
    else:
        ratio = min(max(completed / total, 0.0), 1.0)
        filled = int(ratio * bar_width)
    bar = "#" * filled + "-" * (bar_width - filled)
    return f"[batch_replay] 进度 [{bar}] 已完成病例：{completed} / {total}"


def _get_terminal_handle():
    global _TERMINAL_HANDLE

    if _TERMINAL_HANDLE is not None:
        return _TERMINAL_HANDLE

    try:
        _TERMINAL_HANDLE = open("/dev/tty", "w", encoding="utf-8", buffering=1)
    except OSError:
        _TERMINAL_HANDLE = sys.stderr

    return _TERMINAL_HANDLE


def _emit_terminal_line(message: str) -> None:
    handle = _get_terminal_handle()
    with _TERMINAL_LOCK:
        handle.write(message + "\n")
        handle.flush()


def _emit_progress(completed: int, total: int, *, finished: bool = False) -> None:
    line = _format_progress_line(completed, total)
    _emit_terminal_line(line)


def _format_elapsed_seconds(seconds: float) -> str:
    normalized = max(int(round(seconds)), 0)
    minutes, remain_seconds = divmod(normalized, 60)
    hours, remain_minutes = divmod(minutes, 60)

    if hours > 0:
        return f"{hours:02d}:{remain_minutes:02d}:{remain_seconds:02d}"
    return f"{remain_minutes:02d}:{remain_seconds:02d}"


def _format_duration_value(seconds: float) -> str:
    normalized = max(float(seconds), 0.0)
    if normalized < 1.0:
        return f"{normalized:.4f}"
    return f"{normalized:.2f}"


def _format_active_case_line(case_id: str, started_epoch: float) -> str:
    elapsed_seconds = max(time() - started_epoch, 0.0)
    return f"{case_id}（已运行 {_format_elapsed_seconds(elapsed_seconds)}）"


def _format_heartbeat_line(
    *,
    completed: int,
    total: int,
    active_cases: list[dict[str, Any]],
) -> str:
    message = f"[batch_replay] 心跳 已完成病例：{completed} / {total}，活动病例：{len(active_cases)}"

    if not active_cases:
        return message

    oldest_case = min(active_cases, key=lambda item: float(item.get("started_epoch", 0.0) or 0.0))
    oldest_case_id = str(oldest_case.get("case_id", "")).strip() or "unknown"
    oldest_started_epoch = float(oldest_case.get("started_epoch", 0.0) or 0.0)
    return f"{message}，当前最久：{_format_active_case_line(oldest_case_id, oldest_started_epoch)}"


def _public_active_cases(active_cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "case_id": str(item.get("case_id", "")),
            "case_title": str(item.get("case_title", "")),
            "started_at": str(item.get("started_at", "")),
        }
        for item in active_cases
    ]


def _start_heartbeat_loop(
    *,
    total_cases: int,
    snapshot_state,
    stop_event: threading.Event,
) -> threading.Thread:
    def _worker() -> None:
        while not stop_event.wait(_HEARTBEAT_INTERVAL_SECONDS):
            completed_count, active_cases = snapshot_state()
            _emit_terminal_line(
                _format_heartbeat_line(
                    completed=completed_count,
                    total=total_cases,
                    active_cases=active_cases,
                )
            )

    thread = threading.Thread(target=_worker, name="batch-replay-heartbeat", daemon=True)
    thread.start()
    return thread


def _run_cases_streaming(
    cases,
    *,
    max_turns: int,
    case_concurrency: int,
    api_error_retries: int = 1,
    on_case_start=None,
    on_result=None,
    progress_callback=None,
):
    normalized_concurrency = max(int(case_concurrency), 1)
    total = len(cases)
    callback = progress_callback or _emit_progress

    if total == 0:
        callback(0, 0, finished=True)
        return

    callback(0, total, finished=False)

    if normalized_concurrency == 1 or len(cases) <= 1:
        try:
            for index, case in enumerate(cases, start=1):
                if on_case_start is not None:
                    on_case_start(case, index, total)
                result = _run_single_case_guarded(case, max_turns, api_error_retries)
                if on_result is not None:
                    on_result(result, case, index, total)
                callback(index, total, finished=index == total)
        finally:
            _cleanup_worker_runtimes(reset_current_thread=True)
        return

    completed = 0
    max_workers = min(normalized_concurrency, len(cases))
    executor = ThreadPoolExecutor(max_workers=max_workers)
    interrupted = False

    try:
        case_iter = iter(cases)
        futures = {}
        started = 0

        while len(futures) < max_workers:
            try:
                case = next(case_iter)
            except StopIteration:
                break
            started += 1
            if on_case_start is not None:
                on_case_start(case, started, total)
            futures[executor.submit(_run_single_case_guarded, case, max_turns, api_error_retries)] = case

        while futures:
            for future in as_completed(list(futures.keys())):
                case = futures.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    result = _build_unexpected_case_failure_result(case, exc, stage="batch_runner_future")
                completed += 1
                if on_result is not None:
                    on_result(result, case, completed, total)
                callback(completed, total, finished=completed == total)

                try:
                    next_case = next(case_iter)
                except StopIteration:
                    pass
                else:
                    started += 1
                    if on_case_start is not None:
                        on_case_start(next_case, started, total)
                    futures[
                        executor.submit(_run_single_case_guarded, next_case, max_turns, api_error_retries)
                    ] = next_case
                break
    except KeyboardInterrupt:
        interrupted = True
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    finally:
        if not interrupted:
            executor.shutdown(wait=True, cancel_futures=False)
        _cleanup_worker_runtimes(reset_current_thread=True)


def _run_cases(
    cases,
    *,
    max_turns: int,
    case_concurrency: int,
    api_error_retries: int = 1,
    progress_callback=None,
):
    indexed_results: dict[int, Any] = {}
    case_index_map = {case.case_id: index for index, case in enumerate(cases)}

    def collect_result(result, case, completed: int, total: int) -> None:
        _ = completed, total
        indexed_results[case_index_map[case.case_id]] = result

    _run_cases_streaming(
        cases,
        max_turns=max_turns,
        case_concurrency=case_concurrency,
        api_error_retries=api_error_retries,
        on_result=collect_result,
        progress_callback=progress_callback,
    )

    return [indexed_results[index] for index in sorted(indexed_results)]


def _close_brain(brain) -> None:
    retriever = getattr(getattr(brain, "deps", None), "retriever", None)
    neo4j_client = getattr(retriever, "client", None)

    if neo4j_client is None or not hasattr(neo4j_client, "close"):
        return

    try:
        neo4j_client.close()
    except Exception:
        pass


def _timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _replay_result_to_payload(result: ReplayResult) -> dict[str, Any]:
    return json.loads(json.dumps(result, ensure_ascii=False, default=lambda obj: obj.__dict__))


def _payload_to_replay_result(payload: dict[str, Any]) -> ReplayResult:
    turns = [
        ReplayTurn(
            question_node_id=str(item.get("question_node_id", "")),
            question_text=str(item.get("question_text", "")),
            answer_text=str(item.get("answer_text", "")),
            turn_index=int(item.get("turn_index", 0)),
            revealed_slot_id=item.get("revealed_slot_id"),
            stage=str(item.get("stage", "A3")),
            patient_answer_seconds=float(item.get("patient_answer_seconds", 0.0) or 0.0),
            brain_turn_seconds=float(item.get("brain_turn_seconds", 0.0) or 0.0),
            total_seconds=float(item.get("total_seconds", 0.0) or 0.0),
        )
        for item in payload.get("turns", [])
        if isinstance(item, dict)
    ]

    return ReplayResult(
        case_id=str(payload.get("case_id", "")),
        case_title=str(payload.get("case_title", "")),
        opening_text=str(payload.get("opening_text", "")),
        true_conditions=list(payload.get("true_conditions", [])),
        true_disease_phase=payload.get("true_disease_phase"),
        red_flags=list(payload.get("red_flags", [])),
        turns=turns,
        final_report=dict(payload.get("final_report", {})),
        initial_output=dict(payload.get("initial_output", {})),
        status=str(payload.get("status", "pending")),
        timing=dict(payload.get("timing", {})),
        error=dict(payload.get("error", {})),
    )


def _load_existing_replay_results(results_file: Path) -> list[ReplayResult]:
    if not results_file.exists():
        return []

    results: list[ReplayResult] = []
    with results_file.open("r", encoding="utf-8") as handle:
        for line_index, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if len(line) == 0:
                continue

            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                sys.stderr.write(
                    f"[batch_replay] 警告：忽略 {results_file} 第 {line_index} 行的损坏 JSON 记录。\n"
                )
                continue

            if not isinstance(payload, dict):
                continue

            case_id = str(payload.get("case_id", "")).strip()
            if len(case_id) == 0:
                continue

            results.append(_payload_to_replay_result(payload))

    return results


def _append_replay_result(result: ReplayResult, output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_replay_result_to_payload(result), ensure_ascii=False) + "\n")


def _append_run_log(log_file: Path, message: str) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(f"[{_timestamp()}] {message}\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2))


def _build_summary_payload(
    results: list[ReplayResult],
    *,
    case_concurrency: int,
    case_file: str,
    case_limit: int,
) -> dict[str, Any]:
    summary = summarize_benchmark(results)
    payload = json.loads(json.dumps(summary, ensure_ascii=False, default=lambda obj: obj.__dict__))
    payload["case_concurrency"] = max(int(case_concurrency), 1)
    payload["case_file"] = case_file
    payload["case_limit"] = int(case_limit)
    payload["timing_summary"] = _build_timing_summary(results)
    return payload


def _build_timing_summary(results: list[ReplayResult]) -> dict[str, Any]:
    timed_results = [result for result in results if isinstance(result.timing, dict) and result.timing]

    if not timed_results:
        return {
            "timed_case_count": 0,
            "average_case_seconds": 0.0,
            "average_opening_seconds": 0.0,
            "average_initial_brain_seconds": 0.0,
            "average_patient_answer_seconds_total": 0.0,
            "average_brain_turn_seconds_total": 0.0,
            "average_finalize_seconds": 0.0,
            "slowest_cases": [],
        }

    def timing_value(result: ReplayResult, key: str) -> float:
        value = result.timing.get(key, 0.0)
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    case_count = len(timed_results)
    slowest_cases = sorted(
        [
            {
                "case_id": result.case_id,
                "case_title": result.case_title,
                "status": result.status,
                "total_seconds": round(timing_value(result, "total_seconds"), 4),
                "turn_count": int(result.timing.get("turn_count", len(result.turns)) or 0),
                "slowest_turn_index": int(result.timing.get("slowest_turn_index", 0) or 0),
                "slowest_turn_total_seconds": round(timing_value(result, "slowest_turn_total_seconds"), 4),
                "max_patient_answer_seconds": round(timing_value(result, "max_patient_answer_seconds"), 4),
                "max_brain_turn_seconds": round(timing_value(result, "max_brain_turn_seconds"), 4),
            }
            for result in timed_results
        ],
        key=lambda item: (-item["total_seconds"], item["case_id"]),
    )[:5]

    return {
        "timed_case_count": case_count,
        "average_case_seconds": round(sum(timing_value(item, "total_seconds") for item in timed_results) / case_count, 4),
        "average_opening_seconds": round(sum(timing_value(item, "opening_seconds") for item in timed_results) / case_count, 4),
        "average_initial_brain_seconds": round(
            sum(timing_value(item, "initial_brain_seconds") for item in timed_results) / case_count,
            4,
        ),
        "average_patient_answer_seconds_total": round(
            sum(timing_value(item, "patient_answer_seconds_total") for item in timed_results) / case_count,
            4,
        ),
        "average_brain_turn_seconds_total": round(
            sum(timing_value(item, "brain_turn_seconds_total") for item in timed_results) / case_count,
            4,
        ),
        "average_finalize_seconds": round(sum(timing_value(item, "finalize_seconds") for item in timed_results) / case_count, 4),
        "slowest_cases": slowest_cases,
    }


def _build_status_payload(
    *,
    run_status: str,
    total_cases: int,
    completed_cases: int,
    skipped_completed_cases: int,
    case_concurrency: int,
    case_file: str,
    case_limit: int,
    output_root: Path,
    start_time: str,
    last_completed_case_id: str = "",
    active_cases: list[dict[str, Any]] | None = None,
    timing_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": run_status,
        "updated_at": _timestamp(),
        "started_at": start_time,
        "output_root": str(output_root),
        "case_file": case_file,
        "case_limit": int(case_limit),
        "case_concurrency": max(int(case_concurrency), 1),
        "total_cases": int(total_cases),
        "completed_cases": int(completed_cases),
        "skipped_completed_cases": int(skipped_completed_cases),
        "pending_cases": max(int(total_cases) - int(completed_cases), 0),
        "last_completed_case_id": last_completed_case_id,
        "active_cases": _public_active_cases(list(active_cases or [])),
        "timing_summary": dict(timing_summary or {}),
    }


def _install_interrupt_signal_handlers():
    previous_handlers: dict[int, Any] = {}

    def _signal_handler(signum, frame) -> None:
        _ = frame
        raise KeyboardInterrupt(f"收到信号 {signum}")

    for current_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            previous_handlers[current_signal] = signal.getsignal(current_signal)
            signal.signal(current_signal, _signal_handler)
        except (ValueError, OSError):
            continue

    return previous_handlers


def _restore_interrupt_signal_handlers(previous_handlers: dict[int, Any]) -> None:
    for current_signal, previous_handler in previous_handlers.items():
        try:
            signal.signal(current_signal, previous_handler)
        except (ValueError, OSError):
            continue


def _force_exit_after_interrupt(exit_code: int = 130) -> None:
    sys.stderr.flush()
    sys.stdout.flush()
    os._exit(exit_code)


# 运行批量回放主流程。
def main() -> int:
    args = parse_args()
    previous_signal_handlers = _install_interrupt_signal_handlers()
    frontend_config = load_frontend_config()
    apply_config_to_environment(frontend_config)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    results_file = output_root / "replay_results.jsonl"
    summary_file = output_root / "benchmark_summary.json"
    non_completed_cases_file = output_root / "non_completed_cases.json"
    status_file = output_root / "status.json"
    run_log_file = output_root / "run.log"
    start_time = _timestamp()

    if len(args.cases_file.strip()) > 0:
        cases = load_cases_jsonl(Path(args.cases_file))
    else:
        cases = build_seed_cases()
        write_cases_jsonl(cases, output_root / "seed_cases.jsonl")

    if int(args.limit) > 0:
        cases = cases[: int(args.limit)]

    existing_results: list[ReplayResult] = []
    if not args.no_resume:
        existing_results = _load_existing_replay_results(results_file)

    completed_case_ids = {result.case_id for result in existing_results}
    pending_cases = [case for case in cases if case.case_id not in completed_case_ids]
    skipped_completed_cases = len(cases) - len(pending_cases)
    results = list(existing_results)
    active_cases: dict[str, dict[str, Any]] = {}
    runtime_state_lock = threading.Lock()
    heartbeat_stop_event = threading.Event()
    current_timing_summary = _build_timing_summary(results)
    llm_available = LlmClient().is_available()
    api_error_cooldown_seconds = _read_api_error_cooldown_seconds()

    if not llm_available:
        _write_json(
            status_file,
            _build_status_payload(
                run_status="failed",
                total_cases=len(cases),
                completed_cases=len(results),
                skipped_completed_cases=skipped_completed_cases,
                case_concurrency=args.case_concurrency,
                case_file=args.cases_file.strip(),
                case_limit=args.limit,
                output_root=output_root,
                start_time=start_time,
                active_cases=[],
                timing_summary=current_timing_summary,
            ),
        )
        _write_json(non_completed_cases_file, build_non_completed_case_report(results))
        _append_run_log(run_log_file, "启动失败：当前配置要求走 LLM-first 主链路，但 llm_available=false。")
        _emit_terminal_line("[batch_replay] 启动失败：llm_available=false，当前批量回放不会退回规则链路。")
        return 1

    initial_summary = _build_summary_payload(
        results,
        case_concurrency=args.case_concurrency,
        case_file=args.cases_file.strip(),
        case_limit=args.limit,
    )
    _write_json(summary_file, initial_summary)
    _write_json(non_completed_cases_file, build_non_completed_case_report(results))
    _write_json(
        status_file,
        _build_status_payload(
            run_status="running",
            total_cases=len(cases),
            completed_cases=len(results),
            skipped_completed_cases=skipped_completed_cases,
            case_concurrency=args.case_concurrency,
            case_file=args.cases_file.strip(),
            case_limit=args.limit,
            output_root=output_root,
            start_time=start_time,
            active_cases=list(active_cases.values()),
            timing_summary=current_timing_summary,
        ),
    )
    _append_run_log(
        run_log_file,
        (
            f"启动 batch replay：总病例 {len(cases)}，已完成 {len(existing_results)}，"
            f"待运行 {len(pending_cases)}，并发 {max(int(args.case_concurrency), 1)}，"
            f"api_error_retries={max(int(args.api_error_retries), 0)}，"
            f"api_error_cooldown_seconds={api_error_cooldown_seconds:.2f}，"
            f"resume={'off' if args.no_resume else 'on'}，llm_available={str(llm_available).lower()}"
        ),
    )
    _emit_terminal_line(
        (
            f"[batch_replay] 启动：总病例 {len(cases)}，已完成 {len(existing_results)}，"
            f"待运行 {len(pending_cases)}，并发 {max(int(args.case_concurrency), 1)}，"
            f"API 连接错误重试 {max(int(args.api_error_retries), 0)} 次，"
            f"冷却基线 {api_error_cooldown_seconds:.2f} 秒，"
            f"resume={'off' if args.no_resume else 'on'}，llm_available={str(llm_available).lower()}"
        )
    )

    def snapshot_runtime_state() -> tuple[int, list[dict[str, Any]]]:
        with runtime_state_lock:
            return len(results), list(active_cases.values())

    heartbeat_thread = _start_heartbeat_loop(
        total_cases=len(cases),
        snapshot_state=snapshot_runtime_state,
        stop_event=heartbeat_stop_event,
    )

    def persist_result(result: ReplayResult, case, completed: int, total: int) -> None:
        _ = case
        with runtime_state_lock:
            active_cases.pop(result.case_id, None)
            _append_replay_result(result, results_file)
            results.append(result)

        summary_payload = _build_summary_payload(
            results,
            case_concurrency=args.case_concurrency,
            case_file=args.cases_file.strip(),
            case_limit=args.limit,
        )
        current_timing_summary = summary_payload["timing_summary"]
        _write_json(summary_file, summary_payload)
        _write_json(non_completed_cases_file, build_non_completed_case_report(results))
        _write_json(
            status_file,
            _build_status_payload(
                run_status="running",
                total_cases=len(cases),
                completed_cases=len(results),
                skipped_completed_cases=skipped_completed_cases,
                case_concurrency=args.case_concurrency,
                case_file=args.cases_file.strip(),
                case_limit=args.limit,
                output_root=output_root,
                start_time=start_time,
                last_completed_case_id=result.case_id,
                active_cases=snapshot_runtime_state()[1],
                timing_summary=current_timing_summary,
            ),
        )
        total_seconds = float(result.timing.get("total_seconds", 0.0) or 0.0)
        opening_seconds = float(result.timing.get("opening_seconds", 0.0) or 0.0)
        initial_brain_seconds = float(result.timing.get("initial_brain_seconds", 0.0) or 0.0)
        patient_total_seconds = float(result.timing.get("patient_answer_seconds_total", 0.0) or 0.0)
        brain_total_seconds = float(result.timing.get("brain_turn_seconds_total", 0.0) or 0.0)
        finalize_seconds = float(result.timing.get("finalize_seconds", 0.0) or 0.0)
        slowest_turn_index = int(result.timing.get("slowest_turn_index", 0) or 0)
        slowest_turn_total_seconds = float(result.timing.get("slowest_turn_total_seconds", 0.0) or 0.0)
        error_payload = dict(result.error or {})
        error_suffix = ""
        retry_attempts = int(result.timing.get("batch_retry_attempts", 0) or 0)
        retry_suffix = f"，batch_retry_attempts={retry_attempts}" if retry_attempts > 0 else ""
        if result.status == "failed":
            error_suffix = (
                f"，error_code={str(error_payload.get('code', ''))}"
                f"，error_stage={str(error_payload.get('stage', ''))}"
            )
        _append_run_log(
            run_log_file,
            (
                f"病例完成：case_id={result.case_id}，status={result.status}，"
                f"turns={len(result.turns)}，total_seconds={_format_duration_value(total_seconds)}，"
                f"opening_seconds={_format_duration_value(opening_seconds)}，initial_brain_seconds={_format_duration_value(initial_brain_seconds)}，"
                f"patient_answer_seconds_total={_format_duration_value(patient_total_seconds)}，brain_turn_seconds_total={_format_duration_value(brain_total_seconds)}，"
                f"finalize_seconds={_format_duration_value(finalize_seconds)}，slowest_turn={slowest_turn_index}:{_format_duration_value(slowest_turn_total_seconds)}s，"
                f"本轮完成 {completed}/{total}，累计完成 {len(results)}/{len(cases)}"
                f"{retry_suffix}{error_suffix}"
            ),
        )
        _emit_terminal_line(
            (
                f"[batch_replay] 病例完成 {len(results)}/{len(cases)}："
                f"case_id={result.case_id}，status={result.status}，"
                f"total_seconds={_format_duration_value(total_seconds)}，turns={len(result.turns)}"
                f"{retry_suffix}{error_suffix}"
            )
        )

    def handle_case_start(case, started: int, total: int) -> None:
        with runtime_state_lock:
            active_cases[case.case_id] = {
                "case_id": case.case_id,
                "case_title": getattr(case, "title", ""),
                "started_at": _timestamp(),
                "started_epoch": time(),
            }
        _write_json(
            status_file,
            _build_status_payload(
                run_status="running",
                total_cases=len(cases),
                completed_cases=len(results),
                skipped_completed_cases=skipped_completed_cases,
                case_concurrency=args.case_concurrency,
                case_file=args.cases_file.strip(),
                case_limit=args.limit,
                output_root=output_root,
                start_time=start_time,
                active_cases=snapshot_runtime_state()[1],
                timing_summary=_build_timing_summary(results),
            ),
        )
        _append_run_log(
            run_log_file,
            f"病例启动：case_id={case.case_id}，title={getattr(case, 'title', '')}，已启动 {started}/{total}",
        )
        _emit_terminal_line(
            f"[batch_replay] 病例启动 {started}/{total}：case_id={case.case_id}，title={getattr(case, 'title', '')}"
        )

    try:
        if len(pending_cases) == 0:
            _emit_progress(len(results), len(cases), finished=True)
            _append_run_log(run_log_file, "无需续跑，当前输出目录中的病例均已完成。")
        else:
            _run_cases_streaming(
                pending_cases,
                max_turns=args.max_turns,
                case_concurrency=args.case_concurrency,
                api_error_retries=args.api_error_retries,
                on_case_start=handle_case_start,
                on_result=persist_result,
            )
    except KeyboardInterrupt:
        _write_json(
            status_file,
            _build_status_payload(
                run_status="interrupted",
                total_cases=len(cases),
                completed_cases=len(results),
                skipped_completed_cases=skipped_completed_cases,
                case_concurrency=args.case_concurrency,
                case_file=args.cases_file.strip(),
                case_limit=args.limit,
                output_root=output_root,
                start_time=start_time,
                active_cases=snapshot_runtime_state()[1],
                timing_summary=_build_timing_summary(results),
            ),
        )
        _append_run_log(run_log_file, "运行被用户中断，可下次直接续跑未完成病例。")
        heartbeat_stop_event.set()
        heartbeat_thread.join(timeout=1.0)
        _force_exit_after_interrupt(130)
    except Exception as exc:
        _write_json(
            status_file,
            _build_status_payload(
                run_status="failed",
                total_cases=len(cases),
                completed_cases=len(results),
                skipped_completed_cases=skipped_completed_cases,
                case_concurrency=args.case_concurrency,
                case_file=args.cases_file.strip(),
                case_limit=args.limit,
                output_root=output_root,
                start_time=start_time,
                active_cases=snapshot_runtime_state()[1],
                timing_summary=_build_timing_summary(results),
            ),
        )
        _append_run_log(run_log_file, f"运行异常终止：{type(exc).__name__}: {exc}")
        heartbeat_stop_event.set()
        heartbeat_thread.join(timeout=1.0)
        raise
    finally:
        heartbeat_stop_event.set()
        heartbeat_thread.join(timeout=1.0)
        _restore_interrupt_signal_handlers(previous_signal_handlers)

    final_summary = _build_summary_payload(
        results,
        case_concurrency=args.case_concurrency,
        case_file=args.cases_file.strip(),
        case_limit=args.limit,
    )
    _write_json(summary_file, final_summary)
    _write_json(non_completed_cases_file, build_non_completed_case_report(results))
    _write_json(
        status_file,
        _build_status_payload(
            run_status="completed",
            total_cases=len(cases),
            completed_cases=len(results),
            skipped_completed_cases=skipped_completed_cases,
            case_concurrency=args.case_concurrency,
            case_file=args.cases_file.strip(),
            case_limit=args.limit,
            output_root=output_root,
            start_time=start_time,
            active_cases=[],
            timing_summary=final_summary["timing_summary"],
        ),
    )
    _append_run_log(
        run_log_file,
        f"batch replay 完成：累计完成 {len(results)}/{len(cases)}，completed={final_summary['completed_count']}",
    )

    print(json.dumps(final_summary, ensure_ascii=False, indent=2))
    return 0


# 程序主入口。
if __name__ == "__main__":
    raise SystemExit(main())
