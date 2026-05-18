"""提供虚拟病例 JSON/JSONL 的读写与反序列化入口。"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from .schema import SlotTruth, VirtualPatientCase


def write_cases_jsonl(cases: Iterable[VirtualPatientCase], output_file: Path) -> None:
    """将病例列表写成 JSONL，供批量回放和脚本消费。"""

    output_file.parent.mkdir(parents=True, exist_ok=True)

    with output_file.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(asdict(case), ensure_ascii=False) + "\n")


def write_cases_json(cases: Iterable[VirtualPatientCase], output_file: Path) -> None:
    """将病例列表写成 JSON 数组，便于人工查看与外部程序消费。"""

    output_file.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(case) for case in cases]
    output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_cases_jsonl(input_file: Path) -> list[VirtualPatientCase]:
    """从 JSONL 或 JSON 数组文件中读取病例列表。"""

    raw_text = input_file.read_text(encoding="utf-8")
    stripped = raw_text.lstrip()

    if stripped.startswith("["):
        payload = json.loads(raw_text)
        if not isinstance(payload, list):
            raise ValueError(f"病例 JSON 文件不是数组：{input_file}")
        return [_deserialize_case(data) for data in payload if isinstance(data, dict)]

    cases: list[VirtualPatientCase] = []

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if len(line) == 0:
            continue

        data = json.loads(line)
        if not isinstance(data, dict):
            continue
        cases.append(_deserialize_case(data))

    return cases


def _deserialize_case(data: dict) -> VirtualPatientCase:
    """将持久化字典还原成 `VirtualPatientCase`。"""

    slot_truth_map = {
        key: SlotTruth(**value)
        for key, value in data.get("slot_truth_map", {}).items()
    }
    payload = dict(data)
    payload["slot_truth_map"] = slot_truth_map
    return VirtualPatientCase(**payload)
