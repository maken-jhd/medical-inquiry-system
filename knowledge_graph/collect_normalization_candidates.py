from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

KG_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = KG_ROOT.parent

@dataclass
class CandidateConfig:
    input_file: Path
    output_file: Path


def read_env_config() -> CandidateConfig:
    output_root = Path(
        os.getenv(
            "CANDIDATE_OUTPUT_ROOT",
            str(PROJECT_ROOT / "test_outputs" / "normalization_candidates"),
        )
    ).resolve()
    input_file = Path(
        os.getenv("CANDIDATE_INPUT_FILE", str(PROJECT_ROOT / "output_graph_test.jsonl"))
    ).resolve()
    output_file = Path(
        os.getenv(
            "CANDIDATE_OUTPUT_FILE",
            str(output_root / "node_names_by_label.json"),
        )
    ).resolve()
    return CandidateConfig(
        input_file=input_file,
        output_file=output_file,
    )


def clean_text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None

    cleaned = re.sub(r"[ \t]{2,}", " ", value.strip())

    if len(cleaned) == 0:
        return None

    return cleaned


def main() -> None:
    config = read_env_config()

    if not config.input_file.exists():
        raise RuntimeError(f"Input file does not exist: {config.input_file}")

    names_by_label: Dict[str, Counter[str]] = defaultdict(Counter)
    record_count = 0
    chunk_count = 0
    node_count = 0

    with config.input_file.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()

            if len(line) == 0:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Invalid JSON at line {line_number}: {exc}") from exc

            record_count += 1

            if record.get("record_type") != "chunk_result":
                continue

            extraction = record.get("extraction")

            if not isinstance(extraction, dict):
                continue

            nodes = extraction.get("nodes")

            if not isinstance(nodes, list):
                continue

            chunk_count += 1

            for node in nodes:
                if not isinstance(node, dict):
                    continue

                label = clean_text(node.get("label"))
                name = clean_text(node.get("name"))

                if label is None or name is None:
                    continue

                names_by_label[label][name] += 1
                node_count += 1

    output_payload = {
        label: [
            name
            for name, _count in sorted(
                counter.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]
        for label, counter in sorted(names_by_label.items())
    }

    config.output_file.parent.mkdir(parents=True, exist_ok=True)
    config.output_file.write_text(
        json.dumps(output_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    unique_name_count = sum(len(counter) for counter in names_by_label.values())
    print(f"[candidates] input={config.input_file}")
    print(f"[candidates] output={config.output_file}")
    print(
        "[candidates] "
        f"records={record_count} "
        f"chunks={chunk_count} "
        f"labels={len(names_by_label)} "
        f"nodes={node_count} "
        f"unique_names={unique_name_count}"
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[fatal] interrupted by user", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:  # noqa: BLE001
        print(f"[fatal] {exc}", file=sys.stderr)
        raise SystemExit(1)
