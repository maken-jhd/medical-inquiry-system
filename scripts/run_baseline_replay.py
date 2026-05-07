"""批量运行纯 LLM baseline 回放，并尽量复用现有 benchmark 输出 schema。"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from dataclasses import dataclass
from pathlib import Path


# 将项目根目录加入导入路径，确保脚本可直接运行。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from baselines.llm_consultation_brain import PureLlmConsultationBrain
from brain.llm_client import LlmClient
from frontend.config_loader import apply_config_to_environment, load_frontend_config
from scripts import run_batch_replay
from simulator.generate_cases import build_seed_cases, load_cases_jsonl, write_cases_jsonl
from simulator.patient_agent import VirtualPatientAgent
from simulator.replay_engine import ReplayConfig, ReplayEngine, ReplayResult


_WORKER_RUNTIME_LOCAL = threading.local()
_WORKER_RUNTIME_LOCK = threading.Lock()
_WORKER_RUNTIMES: list["_BaselineWorkerRuntime"] = []
DEFAULT_DISEASE_SCOPE_CATALOG = (
    PROJECT_ROOT / "test_outputs" / "evidence_family" / "disease_evidence_catalog_20260502" / "disease_evidence_family_catalog.json"
)


@dataclass
class _BaselineWorkerRuntime:
    """保存单个 baseline worker 会跨病例复用的 LLM client。"""

    llm_client: LlmClient

    def close(self) -> None:
        self.llm_client.close()


# 解析 baseline batch runner 的命令行参数。
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量运行纯 LLM baseline 并输出 benchmark 指标。")
    parser.add_argument(
        "--baseline-mode",
        default="pure_llm",
        choices=["pure_llm"],
        help="当前只支持 pure_llm；文本 RAG 基线留待下一步实现。",
    )
    parser.add_argument(
        "--cases-file",
        default="",
        help="可选的病例 JSON/JSONL 文件；不提供时将使用内置 seed cases。",
    )
    parser.add_argument(
        "--output-root",
        default=str(PROJECT_ROOT / "test_outputs" / "simulator_replay" / "benchmark_external_baselines" / "pure_llm"),
        help="baseline 输出目录。",
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
        help="病例级并发数；每个并发任务使用独立 baseline doctor 实例。",
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


def _get_worker_runtime() -> _BaselineWorkerRuntime:
    runtime = getattr(_WORKER_RUNTIME_LOCAL, "runtime", None)
    if runtime is not None:
        return runtime

    runtime = _BaselineWorkerRuntime(llm_client=LlmClient())
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


def _normalize_text(value: str) -> str:
    return str(value).strip().replace(" ", "").replace("-", "").replace("_", "").lower()


def _run_single_case(case, max_turns: int, baseline_mode: str, disease_scope: list[str]):
    worker_runtime = _get_worker_runtime()
    if baseline_mode != "pure_llm":
        raise ValueError(f"暂不支持的 baseline_mode: {baseline_mode}")

    brain = PureLlmConsultationBrain(
        llm_client=worker_runtime.llm_client,
        max_turns=max_turns,
        backend_name=baseline_mode,
        disease_scope=disease_scope,
    )
    patient_agent = VirtualPatientAgent(use_llm=True, llm_client=worker_runtime.llm_client)
    engine = ReplayEngine(
        brain=brain,
        patient_agent=patient_agent,
        config=ReplayConfig(max_turns=max_turns),
    )
    return engine.run_case(case)


def _run_single_case_guarded(
    case,
    max_turns: int,
    *,
    baseline_mode: str,
    disease_scope: list[str],
    api_error_retries: int = 1,
):
    retry_count = max(int(api_error_retries), 0)
    retries_used = 0
    total_cooldown_seconds = 0.0

    while True:
        try:
            result = _run_single_case(case, max_turns, baseline_mode, disease_scope)
        except Exception as exc:
            if retries_used < retry_count and run_batch_replay._is_retryable_api_exception(exc):
                retries_used += 1
                total_cooldown_seconds += run_batch_replay._sleep_before_api_retry(retries_used)
                continue

            result = run_batch_replay._build_unexpected_case_failure_result(case, exc, stage="baseline_batch_runner")
            run_batch_replay._annotate_batch_retry(
                result,
                retries_used,
                total_cooldown_seconds=total_cooldown_seconds,
            )
            return result

        if retries_used < retry_count and run_batch_replay._is_retryable_api_error_result(result):
            retries_used += 1
            total_cooldown_seconds += run_batch_replay._sleep_before_api_retry(retries_used)
            continue

        run_batch_replay._annotate_batch_retry(
            result,
            retries_used,
            total_cooldown_seconds=total_cooldown_seconds,
        )
        return result


def _run_cases_streaming(
    cases,
    *,
    max_turns: int,
    case_concurrency: int,
    baseline_mode: str,
    disease_scope: list[str],
    api_error_retries: int = 1,
    on_case_start=None,
    on_result=None,
    progress_callback=None,
):
    normalized_concurrency = max(int(case_concurrency), 1)
    total = len(cases)
    callback = progress_callback or run_batch_replay._emit_progress

    if total == 0:
        callback(0, 0, finished=True)
        return

    callback(0, total, finished=False)

    if normalized_concurrency == 1 or len(cases) <= 1:
        try:
            for index, case in enumerate(cases, start=1):
                if on_case_start is not None:
                    on_case_start(case, index, total)
                result = _run_single_case_guarded(
                    case,
                    max_turns,
                    baseline_mode=baseline_mode,
                    disease_scope=disease_scope,
                    api_error_retries=api_error_retries,
                )
                if on_result is not None:
                    on_result(result, case, index, total)
                callback(index, total, finished=index == total)
        finally:
            _cleanup_worker_runtimes(reset_current_thread=True)
        return

    completed = 0
    max_workers = min(normalized_concurrency, len(cases))
    executor = run_batch_replay.ThreadPoolExecutor(max_workers=max_workers)
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
            futures[
                executor.submit(
                    _run_single_case_guarded,
                    case,
                    max_turns,
                    baseline_mode=baseline_mode,
                    disease_scope=disease_scope,
                    api_error_retries=api_error_retries,
                )
            ] = case

        while futures:
            for future in run_batch_replay.as_completed(list(futures.keys())):
                case = futures.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    result = run_batch_replay._build_unexpected_case_failure_result(
                        case,
                        exc,
                        stage="baseline_batch_runner_future",
                    )
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
                        executor.submit(
                            _run_single_case_guarded,
                            next_case,
                            max_turns,
                            baseline_mode=baseline_mode,
                            disease_scope=disease_scope,
                            api_error_retries=api_error_retries,
                        )
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


def _extract_disease_scope_from_manifest(manifest_path: Path) -> list[str]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    diseases = payload.get("diseases")
    if not isinstance(diseases, list):
        return []

    names: list[str] = []
    seen: set[str] = set()
    for item in diseases:
        if isinstance(item, dict):
            name = str(item.get("disease_name") or "").strip()
        else:
            name = str(item or "").strip()
        normalized_name = _normalize_text(name)
        if len(normalized_name) == 0 or normalized_name in seen:
            continue
        seen.add(normalized_name)
        names.append(name)
    return names


def _extract_disease_scope_from_catalog(catalog_path: Path) -> list[str]:
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    diseases = payload.get("diseases")
    if not isinstance(diseases, list):
        return []

    names: list[str] = []
    seen: set[str] = set()
    for item in diseases:
        if not isinstance(item, dict):
            continue
        name = str(item.get("disease_name") or "").strip()
        normalized_name = _normalize_text(name)
        if len(normalized_name) == 0 or normalized_name in seen:
            continue
        seen.add(normalized_name)
        names.append(name)
    return names


def _extract_disease_scope_from_cases(cases) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for case in cases:
        for item in list(getattr(case, "true_conditions", []) or []):
            name = str(item or "").strip()
            normalized_name = _normalize_text(name)
            if len(normalized_name) == 0 or normalized_name in seen:
                continue
            seen.add(normalized_name)
            names.append(name)
    return names


def resolve_disease_scope(cases_file: str, cases) -> tuple[list[str], str]:
    if len(cases_file.strip()) > 0:
        cases_path = Path(cases_file).resolve()
        for directory in [cases_path.parent, *cases_path.parents]:
            manifest_path = directory / "manifest.json"
            if not manifest_path.exists():
                continue
            disease_scope = _extract_disease_scope_from_manifest(manifest_path)
            if disease_scope:
                return disease_scope, f"manifest:{manifest_path}"

    if DEFAULT_DISEASE_SCOPE_CATALOG.exists():
        disease_scope = _extract_disease_scope_from_catalog(DEFAULT_DISEASE_SCOPE_CATALOG)
        if disease_scope:
            return disease_scope, f"catalog:{DEFAULT_DISEASE_SCOPE_CATALOG}"

    disease_scope = _extract_disease_scope_from_cases(cases)
    if disease_scope:
        return disease_scope, "cases:true_conditions"

    return [], "unavailable"


# 运行 baseline batch replay 主流程，并复用现有主 benchmark 的 summary/status 写盘逻辑。
def main() -> int:
    args = parse_args()
    previous_signal_handlers = run_batch_replay._install_interrupt_signal_handlers()
    frontend_config = load_frontend_config()
    apply_config_to_environment(frontend_config)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    results_file = output_root / "replay_results.jsonl"
    summary_file = output_root / "benchmark_summary.json"
    non_completed_cases_file = output_root / "non_completed_cases.json"
    status_file = output_root / "status.json"
    run_log_file = output_root / "run.log"
    start_time = run_batch_replay._timestamp()

    if len(args.cases_file.strip()) > 0:
        cases = load_cases_jsonl(Path(args.cases_file))
    else:
        cases = build_seed_cases()
        write_cases_jsonl(cases, output_root / "seed_cases.jsonl")

    if int(args.limit) > 0:
        cases = cases[: int(args.limit)]

    disease_scope, disease_scope_source = resolve_disease_scope(args.cases_file.strip(), cases)

    existing_results: list[ReplayResult] = []
    if not args.no_resume:
        existing_results = run_batch_replay._load_existing_replay_results(results_file)
        existing_results = run_batch_replay._enrich_replay_results_from_cases(existing_results, cases)

    completed_case_ids = {result.case_id for result in existing_results}
    pending_cases = [case for case in cases if case.case_id not in completed_case_ids]
    skipped_completed_cases = len(cases) - len(pending_cases)
    results = list(existing_results)
    active_cases: dict[str, dict[str, Any]] = {}
    runtime_state_lock = threading.Lock()
    heartbeat_stop_event = threading.Event()
    current_timing_summary = run_batch_replay._build_timing_summary(results)
    llm_available = LlmClient().is_available()
    api_error_cooldown_seconds = run_batch_replay._read_api_error_cooldown_seconds()

    if not llm_available:
        run_batch_replay._write_json(
            status_file,
            run_batch_replay._build_status_payload(
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
        run_batch_replay._write_json(
            non_completed_cases_file,
            run_batch_replay.build_non_completed_case_report(results),
        )
        run_batch_replay._append_run_log(
            run_log_file,
            f"启动失败：baseline_mode={args.baseline_mode}，llm_available=false。",
        )
        run_batch_replay._emit_terminal_line(
            "[baseline_replay] 启动失败：llm_available=false，当前纯 LLM baseline 不会退回规则链路。"
        )
        return 1

    initial_summary = run_batch_replay._build_summary_payload(
        results,
        case_concurrency=args.case_concurrency,
        case_file=args.cases_file.strip(),
        case_limit=args.limit,
    )
    initial_summary["baseline_mode"] = args.baseline_mode
    initial_summary["disease_scope_count"] = len(disease_scope)
    initial_summary["disease_scope_source"] = disease_scope_source
    run_batch_replay._write_json(summary_file, initial_summary)
    run_batch_replay._write_json(
        non_completed_cases_file,
        run_batch_replay.build_non_completed_case_report(results),
    )
    run_batch_replay._write_json(
        status_file,
        run_batch_replay._build_status_payload(
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
    run_batch_replay._append_run_log(
        run_log_file,
        (
            f"启动 baseline replay：mode={args.baseline_mode}，总病例 {len(cases)}，已完成 {len(existing_results)}，"
            f"待运行 {len(pending_cases)}，并发 {max(int(args.case_concurrency), 1)}，"
            f"api_error_retries={max(int(args.api_error_retries), 0)}，"
            f"disease_scope_count={len(disease_scope)}，"
            f"disease_scope_source={disease_scope_source}，"
            f"api_error_cooldown_seconds={api_error_cooldown_seconds:.2f}，"
            f"resume={'off' if args.no_resume else 'on'}，llm_available={str(llm_available).lower()}"
        ),
    )
    run_batch_replay._emit_terminal_line(
        (
            f"[baseline_replay] 启动：mode={args.baseline_mode}，总病例 {len(cases)}，已完成 {len(existing_results)}，"
            f"待运行 {len(pending_cases)}，并发 {max(int(args.case_concurrency), 1)}，"
            f"disease scope {len(disease_scope)}，"
            f"API 连接错误重试 {max(int(args.api_error_retries), 0)} 次，"
            f"冷却基线 {api_error_cooldown_seconds:.2f} 秒，"
            f"resume={'off' if args.no_resume else 'on'}，llm_available={str(llm_available).lower()}"
        )
    )

    def snapshot_runtime_state() -> tuple[int, list[dict[str, Any]]]:
        with runtime_state_lock:
            return len(results), list(active_cases.values())

    heartbeat_thread = run_batch_replay._start_heartbeat_loop(
        total_cases=len(cases),
        snapshot_state=snapshot_runtime_state,
        stop_event=heartbeat_stop_event,
    )

    def persist_result(result: ReplayResult, case, completed: int, total: int) -> None:
        _ = completed, total
        with runtime_state_lock:
            active_cases.pop(result.case_id, None)
            run_batch_replay._enrich_replay_result_from_case(result, case)
            run_batch_replay._append_replay_result(result, results_file)
            results.append(result)

        summary_payload = run_batch_replay._build_summary_payload(
            results,
            case_concurrency=args.case_concurrency,
            case_file=args.cases_file.strip(),
            case_limit=args.limit,
        )
        summary_payload["baseline_mode"] = args.baseline_mode
        summary_payload["disease_scope_count"] = len(disease_scope)
        summary_payload["disease_scope_source"] = disease_scope_source
        current_timing_summary = summary_payload["timing_summary"]
        run_batch_replay._write_json(summary_file, summary_payload)
        run_batch_replay._write_json(
            non_completed_cases_file,
            run_batch_replay.build_non_completed_case_report(results),
        )
        run_batch_replay._write_json(
            status_file,
            run_batch_replay._build_status_payload(
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
        run_batch_replay._append_run_log(
            run_log_file,
            (
                f"病例完成 [{len(results)}/{len(cases)}] {result.case_id} status={result.status} "
                f"turns={len(result.turns)} total_seconds={run_batch_replay._format_duration_value(total_seconds)}"
            ),
        )

    def mark_case_started(case, started: int, total: int) -> None:
        _ = started, total
        with runtime_state_lock:
            active_cases[str(case.case_id)] = {
                "case_id": str(case.case_id),
                "case_title": str(getattr(case, "title", "") or ""),
                "started_at": run_batch_replay._timestamp(),
                "started_epoch": run_batch_replay.time(),
            }

    try:
        _run_cases_streaming(
            pending_cases,
            max_turns=args.max_turns,
            case_concurrency=args.case_concurrency,
            baseline_mode=args.baseline_mode,
            disease_scope=disease_scope,
            api_error_retries=args.api_error_retries,
            on_case_start=mark_case_started,
            on_result=persist_result,
            progress_callback=run_batch_replay._emit_progress,
        )
    except KeyboardInterrupt:
        heartbeat_stop_event.set()
        heartbeat_thread.join(timeout=1.0)
        interrupted_status = run_batch_replay._build_status_payload(
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
            timing_summary=run_batch_replay._build_timing_summary(results),
        )
        run_batch_replay._write_json(status_file, interrupted_status)
        run_batch_replay._append_run_log(run_log_file, "运行被中断。")
        run_batch_replay._restore_interrupt_signal_handlers(previous_signal_handlers)
        run_batch_replay._force_exit_after_interrupt()
        return 130
    except Exception as exc:
        heartbeat_stop_event.set()
        heartbeat_thread.join(timeout=1.0)
        failed_status = run_batch_replay._build_status_payload(
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
            timing_summary=run_batch_replay._build_timing_summary(results),
        )
        run_batch_replay._write_json(status_file, failed_status)
        run_batch_replay._append_run_log(run_log_file, f"运行失败：{type(exc).__name__}: {exc}")
        run_batch_replay._restore_interrupt_signal_handlers(previous_signal_handlers)
        raise

    heartbeat_stop_event.set()
    heartbeat_thread.join(timeout=1.0)
    final_summary = run_batch_replay._build_summary_payload(
        results,
        case_concurrency=args.case_concurrency,
        case_file=args.cases_file.strip(),
        case_limit=args.limit,
    )
    final_summary["baseline_mode"] = args.baseline_mode
    final_summary["disease_scope_count"] = len(disease_scope)
    final_summary["disease_scope_source"] = disease_scope_source
    run_batch_replay._write_json(summary_file, final_summary)
    run_batch_replay._write_json(
        non_completed_cases_file,
        run_batch_replay.build_non_completed_case_report(results),
    )
    run_batch_replay._write_json(
        status_file,
        run_batch_replay._build_status_payload(
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
    run_batch_replay._append_run_log(
        run_log_file,
        (
            f"baseline replay 完成：mode={args.baseline_mode}，总病例 {len(cases)}，"
            f"完成 {len(results)}，未完成 {len([item for item in results if item.status != 'completed'])}。"
        ),
    )
    run_batch_replay._emit_terminal_line(
        (
            f"[baseline_replay] 完成：mode={args.baseline_mode}，总病例 {len(cases)}，"
            f"完成 {len(results)}，未完成 {len([item for item in results if item.status != 'completed'])}。"
        )
    )
    run_batch_replay._restore_interrupt_signal_handlers(previous_signal_handlers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
