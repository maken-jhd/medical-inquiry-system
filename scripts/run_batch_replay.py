"""批量运行虚拟病人回放并输出评测摘要。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# 将项目根目录加入导入路径，确保脚本可直接运行。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain.service import build_default_brain_from_env
from simulator.benchmark import summarize_benchmark
from simulator.generate_cases import build_seed_cases, load_cases_jsonl, write_cases_jsonl
from simulator.patient_agent import VirtualPatientAgent
from simulator.replay_engine import ReplayConfig, ReplayEngine, write_replay_results_jsonl


# 解析命令行参数。
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量运行虚拟病人回放并输出指标。")
    parser.add_argument(
        "--cases-file",
        default="",
        help="可选的病例 JSONL 文件；不提供时将使用内置 seed cases。",
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
    return parser.parse_args()


# 运行批量回放主流程。
def main() -> int:
    args = parse_args()
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    if len(args.cases_file.strip()) > 0:
        cases = load_cases_jsonl(Path(args.cases_file))
    else:
        cases = build_seed_cases()
        write_cases_jsonl(cases, output_root / "seed_cases.jsonl")

    brain = build_default_brain_from_env()
    engine = ReplayEngine(
        brain=brain,
        patient_agent=VirtualPatientAgent(),
        config=ReplayConfig(max_turns=args.max_turns),
    )
    results = engine.run_cases(cases)
    summary = summarize_benchmark(results)

    write_replay_results_jsonl(results, output_root / "replay_results.jsonl")

    with (output_root / "benchmark_summary.json").open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(summary, ensure_ascii=False, indent=2, default=lambda obj: obj.__dict__))

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=lambda obj: obj.__dict__))
    return 0


# 程序主入口。
if __name__ == "__main__":
    raise SystemExit(main())
