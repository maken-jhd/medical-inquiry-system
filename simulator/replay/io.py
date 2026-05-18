"""提供 replay 结果导出入口。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .types import ReplayResult


def write_replay_results_jsonl(results: Iterable[ReplayResult], output_file: Path) -> None:
    """将批量回放结果写入 JSONL，便于后续复盘分析。"""

    output_file.parent.mkdir(parents=True, exist_ok=True)

    with output_file.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result, ensure_ascii=False, default=lambda obj: obj.__dict__) + "\n")
