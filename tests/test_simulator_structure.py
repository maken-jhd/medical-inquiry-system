"""测试 simulator 新分层结构与旧入口兼容壳。"""

from __future__ import annotations

from importlib import import_module


def test_simulator_top_level_compatibility_exports() -> None:
    from simulator.benchmark import (
        BenchmarkSummary,
        build_benchmark_cohort_summary,
        build_non_completed_case_report,
        build_replay_analysis_summary,
        summarize_benchmark,
    )
    from simulator.case_schema import BehaviorStyle, SlotTruth, VirtualPatientCase
    from simulator.generate_cases import build_seed_cases, load_cases_jsonl, write_cases_json, write_cases_jsonl
    from simulator.path_cache_builder import PathCacheEntry, build_path_cache
    from simulator.patient_agent import PatientOpening, PatientReply, VirtualPatientAgent
    from simulator.replay_engine import ReplayConfig, ReplayEngine, ReplayResult, ReplayTurn

    assert BehaviorStyle is not None
    assert SlotTruth is not None
    assert VirtualPatientCase is not None
    assert PatientOpening is not None
    assert PatientReply is not None
    assert VirtualPatientAgent is not None
    assert ReplayConfig is not None
    assert ReplayEngine is not None
    assert ReplayResult is not None
    assert ReplayTurn is not None
    assert BenchmarkSummary is not None
    assert callable(build_seed_cases)
    assert callable(load_cases_jsonl)
    assert callable(write_cases_json)
    assert callable(write_cases_jsonl)
    assert callable(summarize_benchmark)
    assert callable(build_non_completed_case_report)
    assert callable(build_benchmark_cohort_summary)
    assert callable(build_replay_analysis_summary)
    assert PathCacheEntry is not None
    assert callable(build_path_cache)


def test_simulator_new_subpackages_and_scripts_import() -> None:
    modules = [
        "simulator",
        "simulator.app",
        "simulator.cases",
        "simulator.patient",
        "simulator.replay",
        "simulator.benchmarking",
        "simulator.audit",
        "simulator.catalog",
        "simulator.cache",
        "scripts.run_batch_replay",
        "scripts.run_baseline_replay",
        "scripts.run_single_case_smoke",
    ]

    for module_name in modules:
        assert import_module(module_name) is not None


def test_replay_engine_and_patient_agent_are_runtime_facades() -> None:
    from simulator.patient_agent import VirtualPatientAgent
    from simulator.replay_engine import ReplayConfig, ReplayEngine

    class FakeBrain:
        def start_session(self, session_id: str) -> None:
            _ = session_id

        def process_turn(self, session_id: str, patient_text: str) -> dict:
            _ = session_id, patient_text
            return {"final_report": {"summary": "ok"}}

        def finalize(self, session_id: str) -> dict:
            _ = session_id
            return {"summary": "ok"}

    patient_agent = VirtualPatientAgent()
    replay_engine = ReplayEngine(FakeBrain(), patient_agent, ReplayConfig(max_turns=1))

    assert hasattr(patient_agent, "runtime")
    assert hasattr(replay_engine, "runtime")
