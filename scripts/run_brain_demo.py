"""提供第二阶段问诊大脑的最小命令行演示入口。"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path


# 将项目根目录加入导入路径，确保脚本可直接从命令行运行。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain.service import build_default_brain_from_env


# 解析命令行参数。
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行问诊大脑的最小 CLI 演示。")
    parser.add_argument(
        "--session-id",
        default=f"cli::{uuid.uuid4().hex[:8]}",
        help="当前演示会话的唯一标识。",
    )
    return parser.parse_args()


# 将单轮输出整理成更适合终端阅读的文本。
def render_turn_output(turn_output: dict) -> str:
    lines: list[str] = []

    a1 = turn_output.get("a1") or {}
    key_features = a1.get("key_features") or []

    if len(key_features) > 0:
        feature_names = [item.get("normalized_name") or item.get("name") for item in key_features]
        lines.append(f"A1 核心线索: {', '.join(str(item) for item in feature_names)}")
    else:
        lines.append("A1 核心线索: 未提取到明显线索")

    a2 = turn_output.get("a2") or {}
    primary = a2.get("primary_hypothesis")

    if primary is not None:
        lines.append(f"A2 当前主假设: {primary.get('name')}")
    else:
        lines.append("A2 当前主假设: 暂无")

    next_question = turn_output.get("next_question")
    if next_question is not None:
        lines.append(f"下一问: {next_question}")

    final_report = turn_output.get("final_report")
    if final_report is not None:
        lines.append("问诊已满足终止条件。")
        lines.append(json.dumps(final_report, ensure_ascii=False, indent=2))

    return "\n".join(lines)


# 运行一个最小的命令行问诊会话。
def main() -> int:
    args = parse_args()
    brain = build_default_brain_from_env()
    brain.start_session(args.session_id)

    print("问诊大脑 CLI 演示已启动。")
    print("输入患者主诉或回答；输入 exit / quit 结束。")

    while True:
        try:
            patient_text = input("患者: ").strip()
        except EOFError:
            print()
            break

        if patient_text.lower() in {"exit", "quit"}:
            break

        if len(patient_text) == 0:
            continue

        turn_output = brain.process_turn(args.session_id, patient_text)
        print(render_turn_output(turn_output))

        if turn_output.get("final_report") is not None:
            break

    return 0


# 程序主入口。
if __name__ == "__main__":
    raise SystemExit(main())
