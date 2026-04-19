from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from openai import AsyncOpenAI

KG_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = KG_ROOT.parent

ALLOWED_LABELS = [
    "Disease",
    "ClinicalFinding",
    "ClinicalAttribute",
    "LabTest",
    "LabFinding",
    "ImagingFinding",
    "Pathogen",
    "RiskFactor",
    "PopulationGroup",
]

ALLOWED_EDGE_TYPES = [
    "MANIFESTS_AS",
    "HAS_LAB_FINDING",
    "HAS_IMAGING_FINDING",
    "HAS_PATHOGEN",
    "DIAGNOSED_BY",
    "REQUIRES_DETAIL",
    "RISK_FACTOR_FOR",
    "COMPLICATED_BY",
    "APPLIES_TO",
]

DETAIL_LEVELS = {"minimal", "standard", "full"}
DISEASE_LIKE_LABELS = {
    "Disease",
}
EVIDENCE_LABELS = {
    "Pathogen",
    "ClinicalFinding",
    "ClinicalAttribute",
    "LabTest",
    "LabFinding",
    "ImagingFinding",
    "RiskFactor",
    "PopulationGroup",
}
REPAIR_FOCUS_LABELS = set(ALLOWED_LABELS)


def edge_label_rules() -> Dict[str, Tuple[set[str], set[str]]]:
    return {
        "MANIFESTS_AS": (DISEASE_LIKE_LABELS, {"ClinicalFinding"}),
        "HAS_LAB_FINDING": (DISEASE_LIKE_LABELS, {"LabFinding"}),
        "HAS_IMAGING_FINDING": (DISEASE_LIKE_LABELS, {"ImagingFinding"}),
        "HAS_PATHOGEN": (DISEASE_LIKE_LABELS, {"Pathogen"}),
        "DIAGNOSED_BY": (DISEASE_LIKE_LABELS, {"LabTest", "LabFinding", "ImagingFinding"}),
        "REQUIRES_DETAIL": (DISEASE_LIKE_LABELS | {"ClinicalFinding", "LabTest"}, {"ClinicalAttribute"}),
        "RISK_FACTOR_FOR": ({"RiskFactor", "PopulationGroup"}, DISEASE_LIKE_LABELS),
        "COMPLICATED_BY": (DISEASE_LIKE_LABELS, DISEASE_LIKE_LABELS),
        "APPLIES_TO": (DISEASE_LIKE_LABELS | EVIDENCE_LABELS, {"PopulationGroup"}),
    }


EDGE_LABEL_RULES = edge_label_rules()


def build_repair_output_schema(max_add_edges: int) -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["add_edges", "drop_node_ids", "notes"],
        "properties": {
            "add_edges": {
                "type": "array",
                "maxItems": max_add_edges,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "source_id",
                        "type",
                        "target_id",
                        "weight",
                        "detail_required",
                        "evidence_text",
                        "confidence",
                    ],
                    "properties": {
                        "source_id": {"type": "string"},
                        "type": {"type": "string", "enum": ALLOWED_EDGE_TYPES},
                        "target_id": {"type": "string"},
                        "weight": {"type": "number", "minimum": 0, "maximum": 1},
                        "detail_required": {"type": "string", "enum": sorted(DETAIL_LEVELS)},
                        "evidence_text": {"type": "string", "maxLength": 240},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "attributes": {
                            "type": "object",
                            "additionalProperties": True,
                        },
                    },
                },
            },
            "drop_node_ids": {
                "type": "array",
                "maxItems": max_add_edges,
                "items": {"type": "string"},
            },
            "notes": {
                "type": "array",
                "maxItems": 3,
                "items": {"type": "string", "maxLength": 160},
            },
        },
    }


REPAIR_SYSTEM_PROMPT = f"""
你正在为 HIV/AIDS 智能问诊系统修补“搜索专用知识图谱”的缺失关系。

这不是全量指南图谱，也不是治疗推荐图谱。你只能服务下面的问诊搜索链路：
- R1：临床表现 / 风险背景 / 检查 / 影像 / 病原线索 -> 候选诊断
- R2：候选诊断 -> 关键待验证证据
- A3：根据待验证证据构造下一问
- A4：根据患者回答更新证据和假设

任务目标：
1. 只针对 suspicious_nodes 中列出的可疑节点补充当前 chunk 内缺失的关系。
2. 只能在 existing_nodes 中已有节点之间补边，禁止新建节点，禁止引用当前 chunk 之外的节点 id。
3. 只在 chunk_text 中有明确文本依据时补边，evidence_text 必须来自当前文本或非常贴近原文。
4. 不要重复 existing_edges 中已有关系。
5. drop_node_ids 默认返回空数组；除非可疑节点明显是抽取误差且当前文本没有依据，否则不要建议删除。
6. 宁可少补，也不要为了降低孤立率而臆造关系。
7. 不要输出任何解释性文字，只返回严格 JSON。

只能使用这些节点标签：
{", ".join(ALLOWED_LABELS)}

只能使用这些关系类型：
{", ".join(ALLOWED_EDGE_TYPES)}

关系方向必须符合：
- `MANIFESTS_AS`：Disease -> ClinicalFinding
- `HAS_LAB_FINDING`：Disease -> LabFinding
- `HAS_IMAGING_FINDING`：Disease -> ImagingFinding
- `HAS_PATHOGEN`：Disease -> Pathogen
- `DIAGNOSED_BY`：Disease -> LabTest、LabFinding 或 ImagingFinding
- `REQUIRES_DETAIL`：Disease、ClinicalFinding 或 LabTest -> ClinicalAttribute
- `RISK_FACTOR_FOR`：RiskFactor 或 PopulationGroup -> Disease
- `COMPLICATED_BY`：Disease -> Disease
- `APPLIES_TO`：Disease 或证据节点 -> PopulationGroup

候选诊断只使用 Disease。临床表现只使用 ClinicalFinding。风险因素和风险行为都使用 RiskFactor。

不要使用旧版全量图谱关系，例如 RECOMMENDS、TREATED_WITH、SUPPORTED_BY、HAS_EVIDENCE、SUBJECT、OBJECT。
""".strip()


@dataclass
class RepairConfig:
    input_file: Path
    output_file: Path
    report_file: Path
    retry_report_file: Optional[Path]
    baseline_output_file: Optional[Path]
    retry_chunk_ids: List[str]
    source_root_dir: Path
    api_key: str
    base_url: str
    model: str
    request_timeout_seconds: float
    sdk_max_retries: int
    concurrency: int
    retry_count: int
    retry_delay_ms: int
    min_confidence: float
    apply_drop_node_ids: bool
    max_context_nodes: int
    max_context_edges: int
    max_chunk_text_chars: int
    max_add_edges: int


@dataclass
class SuspiciousNode:
    node_id: str
    label: str
    name: str
    reasons: List[str]


@dataclass
class ChunkContext:
    chunk_id: str
    relative_path: str
    heading_path: List[str]
    line_start: int
    line_end: int
    chunk_text: str


class RepairValidationError(RuntimeError):
    pass


def parse_positive_int(value: Optional[str], fallback: int) -> int:
    if value is None:
        return fallback

    try:
        parsed = int(value)
    except ValueError:
        return fallback

    if parsed > 0:
        return parsed

    return fallback


def parse_positive_float(value: Optional[str], fallback: float) -> float:
    if value is None:
        return fallback

    try:
        parsed = float(value)
    except ValueError:
        return fallback

    if parsed > 0:
        return parsed

    return fallback


def parse_csv_strings(value: Optional[str]) -> List[str]:
    if value is None:
        return []

    parts = [item.strip() for item in value.split(",")]
    return [item for item in parts if len(item) > 0]


def clean_text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None

    cleaned = re.sub(r"\s+", " ", value).strip()

    if len(cleaned) == 0:
        return None

    return cleaned


def summarize_text(value: str, limit: int = 500) -> str:
    normalized = re.sub(r"\s+", " ", value).strip()

    if len(normalized) <= limit:
        return normalized

    return normalized[:limit].rstrip() + " ..."


def flatten_attributes(item: Dict[str, Any]) -> None:
    attributes = item.get("attributes")

    if not isinstance(attributes, dict):
        return

    for key, value in attributes.items():
        if key not in item:
            item[key] = value


def read_env_config() -> RepairConfig:
    output_root = Path(
        os.getenv("REPAIR_OUTPUT_ROOT", str(PROJECT_ROOT / "test_outputs" / "relation_repair"))
    ).resolve()
    input_file = Path(
        os.getenv("REPAIR_INPUT_FILE", str(PROJECT_ROOT / "output_graph_test.jsonl"))
    ).resolve()
    output_file = Path(
        os.getenv("REPAIR_OUTPUT_FILE", str(output_root / "output_graph_repaired.jsonl"))
    ).resolve()
    report_file = Path(
        os.getenv("REPAIR_REPORT_FILE", str(output_root / "output_graph_repaired_report.json"))
    ).resolve()
    retry_report_env = os.getenv("REPAIR_RETRY_REPORT_FILE")
    baseline_output_env = os.getenv("REPAIR_BASELINE_OUTPUT_FILE")
    source_root_dir = Path(
        os.getenv(
            "REPAIR_SOURCE_ROOT_DIR",
            str(
                (PROJECT_ROOT / "HIV_cleaned")
                if (PROJECT_ROOT / "HIV_cleaned").exists()
                else (PROJECT_ROOT / "HIV")
            ),
        )
    ).resolve()
    api_key = (
        os.getenv("REPAIR_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("DASHSCOPE_API_KEY")
        or os.getenv("LLM_API_KEY")
        or ""
    )
    base_url = (
        os.getenv("REPAIR_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or os.getenv("DASHSCOPE_BASE_URL")
        or "https://api.openai.com/v1"
    )
    model = os.getenv("REPAIR_MODEL") or os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL") or "gpt-4.1"
    return RepairConfig(
        input_file=input_file,
        output_file=output_file,
        report_file=report_file,
        retry_report_file=Path(retry_report_env).resolve() if retry_report_env else None,
        baseline_output_file=Path(baseline_output_env).resolve() if baseline_output_env else None,
        retry_chunk_ids=parse_csv_strings(os.getenv("REPAIR_RETRY_CHUNK_IDS")),
        source_root_dir=source_root_dir,
        api_key=api_key,
        base_url=base_url,
        model=model,
        request_timeout_seconds=parse_positive_float(os.getenv("REPAIR_REQUEST_TIMEOUT_SECONDS"), 240.0),
        sdk_max_retries=parse_positive_int(os.getenv("REPAIR_SDK_MAX_RETRIES"), 0),
        concurrency=parse_positive_int(os.getenv("REPAIR_CONCURRENCY"), 5),
        retry_count=parse_positive_int(os.getenv("REPAIR_RETRY_COUNT"), 3),
        retry_delay_ms=parse_positive_int(os.getenv("REPAIR_RETRY_DELAY_MS"), 1500),
        min_confidence=parse_positive_float(os.getenv("REPAIR_MIN_CONFIDENCE"), 0.68),
        apply_drop_node_ids=(os.getenv("REPAIR_APPLY_DROP_NODE_IDS") or "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        },
        max_context_nodes=parse_positive_int(os.getenv("REPAIR_MAX_CONTEXT_NODES"), 50),
        max_context_edges=parse_positive_int(os.getenv("REPAIR_MAX_CONTEXT_EDGES"), 90),
        max_chunk_text_chars=parse_positive_int(os.getenv("REPAIR_MAX_CHUNK_TEXT_CHARS"), 6500),
        max_add_edges=parse_positive_int(os.getenv("REPAIR_MAX_ADD_EDGES"), 8),
    )


def load_jsonl_records(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []

    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()

            if len(line) == 0:
                continue

            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Invalid JSON at line {line_number} in {path}: {exc}") from exc

    return records


def load_retry_chunk_ids_from_report(path: Path) -> List[str]:
    report = json.loads(path.read_text(encoding="utf-8"))
    chunk_ids: List[str] = []

    for item in report.get("chunk_summaries", []):
        if not isinstance(item, dict) or item.get("status") != "repair_failed":
            continue

        chunk_id = clean_text(item.get("chunk_id"))

        if chunk_id is not None:
            chunk_ids.append(chunk_id)

    return chunk_ids


def determine_retry_chunk_ids(config: RepairConfig) -> List[str]:
    ordered_chunk_ids: List[str] = []
    seen: set[str] = set()

    for chunk_id in config.retry_chunk_ids:
        if chunk_id not in seen:
            ordered_chunk_ids.append(chunk_id)
            seen.add(chunk_id)

    if config.retry_report_file is not None and config.retry_report_file.exists():
        for chunk_id in load_retry_chunk_ids_from_report(config.retry_report_file):
            if chunk_id not in seen:
                ordered_chunk_ids.append(chunk_id)
                seen.add(chunk_id)

    return ordered_chunk_ids


def filter_records_for_retry(records: Sequence[Dict[str, Any]], retry_chunk_ids: set[str]) -> List[Dict[str, Any]]:
    return [
        record
        for record in records
        if record.get("record_type") == "chunk_result" and clean_text(record.get("chunk_id")) in retry_chunk_ids
    ]


def merge_retry_results_into_baseline(
    baseline_records: Sequence[Dict[str, Any]],
    retried_records: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    replacement_map: Dict[str, Dict[str, Any]] = {}

    for record in retried_records:
        chunk_id = clean_text(record.get("chunk_id"))

        if record.get("record_type") == "chunk_result" and chunk_id is not None:
            replacement_map[chunk_id] = record

    merged_records: List[Dict[str, Any]] = []

    for record in baseline_records:
        chunk_id = clean_text(record.get("chunk_id"))

        if record.get("record_type") == "chunk_result" and chunk_id in replacement_map:
            merged_records.append(replacement_map[chunk_id])
        else:
            merged_records.append(record)

    return merged_records


def resolve_source_file_path(relative_path: str, config: RepairConfig) -> Optional[Path]:
    candidates = [
        config.source_root_dir / relative_path,
        PROJECT_ROOT / relative_path,
        PROJECT_ROOT / "HIV_cleaned" / relative_path,
        PROJECT_ROOT / "HIV" / relative_path,
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return None


def load_chunk_text_from_source(record: Dict[str, Any], config: RepairConfig) -> str:
    relative_path = clean_text(record.get("relative_path"))
    line_start = int(record.get("line_start", 0) or 0)
    line_end = int(record.get("line_end", 0) or 0)

    if relative_path is None or line_start <= 0 or line_end <= 0 or line_end < line_start:
        return ""

    source_path = resolve_source_file_path(relative_path, config)

    if source_path is None:
        return ""

    lines = source_path.read_text(encoding="utf-8").splitlines()
    start_index = max(line_start - 1, 0)
    end_index = min(line_end, len(lines))

    if start_index >= end_index:
        return ""

    return "\n".join(lines[start_index:end_index])


def build_chunk_context(record: Dict[str, Any], config: RepairConfig) -> ChunkContext:
    chunk_text = str(record.get("chunk_text", ""))

    if len(chunk_text.strip()) == 0:
        chunk_text = load_chunk_text_from_source(record, config)

    if len(chunk_text) > config.max_chunk_text_chars:
        chunk_text = chunk_text[: config.max_chunk_text_chars].rstrip() + "\n...[TRUNCATED]"

    return ChunkContext(
        chunk_id=str(record.get("chunk_id", "")),
        relative_path=str(record.get("relative_path", "")),
        heading_path=list(record.get("heading_path", [])) if isinstance(record.get("heading_path"), list) else [],
        line_start=int(record.get("line_start", 0) or 0),
        line_end=int(record.get("line_end", 0) or 0),
        chunk_text=chunk_text,
    )


def compute_incident_counts(edges: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    incident_count: Dict[str, int] = {}

    for raw_edge in edges:
        if not isinstance(raw_edge, dict):
            continue

        edge = dict(raw_edge)
        flatten_attributes(edge)
        source_id = clean_text(edge.get("source_id"))
        target_id = clean_text(edge.get("target_id"))

        if source_id is None or target_id is None:
            continue

        incident_count[source_id] = incident_count.get(source_id, 0) + 1
        incident_count[target_id] = incident_count.get(target_id, 0) + 1

    return incident_count


def identify_suspicious_nodes(record: Dict[str, Any]) -> List[SuspiciousNode]:
    extraction = record.get("extraction")

    if not isinstance(extraction, dict):
        return []

    nodes = extraction.get("nodes")
    edges = extraction.get("edges")

    if not isinstance(nodes, list) or not isinstance(edges, list):
        return []

    incident_count = compute_incident_counts(edges)
    suspicious_nodes: List[SuspiciousNode] = []

    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            continue

        node = dict(raw_node)
        flatten_attributes(node)
        node_id = clean_text(node.get("id"))
        label = clean_text(node.get("label"))
        name = clean_text(node.get("name"))

        if node_id is None or label is None or name is None:
            continue

        if label not in REPAIR_FOCUS_LABELS:
            continue

        if incident_count.get(node_id, 0) == 0:
            suspicious_nodes.append(
                SuspiciousNode(
                    node_id=node_id,
                    label=label,
                    name=name,
                    reasons=["isolated_node"],
                )
            )

    return suspicious_nodes


def compact_node(node: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "id": node.get("id"),
        "label": node.get("label"),
        "name": node.get("name"),
    }

    for key in [
        "canonical_name",
        "definition",
        "test_id",
        "operator",
        "value",
        "value_text",
        "unit",
        "acquisition_mode",
        "evidence_cost",
    ]:
        if key in node and node.get(key) not in [None, "", [], {}]:
            result[key] = node.get(key)

    return result


def compact_edge(edge: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "id": edge.get("id"),
        "type": edge.get("type"),
        "source_id": edge.get("source_id"),
        "target_id": edge.get("target_id"),
    }
    condition_text = clean_text(edge.get("condition_text"))

    if condition_text is not None:
        result["condition_text"] = condition_text

    return result


def build_node_map(nodes: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    node_map: Dict[str, Dict[str, Any]] = {}

    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            continue

        node = dict(raw_node)
        flatten_attributes(node)
        node_id = clean_text(node.get("id"))

        if node_id is not None:
            node_map[node_id] = node

    return node_map


def score_candidate_node(node: Dict[str, Any], suspicious_node_ids: set[str]) -> int:
    node_id = clean_text(node.get("id")) or ""
    label = clean_text(node.get("label")) or ""
    score = 0

    if node_id in suspicious_node_ids:
        score += 1000

    if label in DISEASE_LIKE_LABELS:
        score += 180

    if label in EVIDENCE_LABELS:
        score += 90

    if label in {"ClinicalFinding", "LabFinding", "ImagingFinding", "RiskFactor"}:
        score += 40

    name = clean_text(node.get("name")) or ""
    score += min(len(name), 30)
    return score


def select_context_subgraph(
    record: Dict[str, Any],
    suspicious_nodes: Sequence[SuspiciousNode],
    config: RepairConfig,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    extraction = record["extraction"]
    raw_nodes = extraction["nodes"]
    raw_edges = extraction["edges"]
    node_map = build_node_map(raw_nodes)
    suspicious_node_ids = {node.node_id for node in suspicious_nodes}
    ranked_nodes = sorted(
        node_map.values(),
        key=lambda node: (
            -score_candidate_node(node, suspicious_node_ids),
            clean_text(node.get("label")) or "",
            clean_text(node.get("name")) or "",
        ),
    )
    selected_nodes = ranked_nodes[: config.max_context_nodes]
    selected_node_ids = {
        clean_text(node.get("id"))
        for node in selected_nodes
        if clean_text(node.get("id")) is not None
    }
    selected_node_ids = {node_id for node_id in selected_node_ids if node_id is not None}
    selected_edges: List[Dict[str, Any]] = []

    for raw_edge in raw_edges:
        if not isinstance(raw_edge, dict):
            continue

        edge = dict(raw_edge)
        flatten_attributes(edge)
        source_id = clean_text(edge.get("source_id"))
        target_id = clean_text(edge.get("target_id"))

        if source_id in selected_node_ids and target_id in selected_node_ids:
            selected_edges.append(edge)

    return selected_nodes, selected_edges[: config.max_context_edges]


def build_retry_feedback_block(retry_feedback: Optional[str]) -> List[str]:
    if retry_feedback is None:
        return []

    return [
        "",
        "[Previous Attempt Error]",
        retry_feedback,
        "请显式修正以上问题，不要重复同样的输出错误。",
    ]


def build_messages(
    record: Dict[str, Any],
    suspicious_nodes: Sequence[SuspiciousNode],
    config: RepairConfig,
    retry_feedback: Optional[str] = None,
) -> List[Dict[str, str]]:
    chunk = build_chunk_context(record, config)
    heading_text = " > ".join(chunk.heading_path) if len(chunk.heading_path) > 0 else chunk.relative_path
    suspicious_lines = [
        f"- {node.node_id} | {node.label} | {node.name} | reasons={','.join(node.reasons)}"
        for node in suspicious_nodes
    ]
    selected_nodes, selected_edges = select_context_subgraph(record, suspicious_nodes, config)
    existing_nodes = [compact_node(node) for node in selected_nodes]
    existing_edges = [compact_edge(edge) for edge in selected_edges]

    user_prompt = "\n".join(
        [
            "请只修补当前 chunk 内 suspicious_nodes 的缺失关系，不要重新抽取整张图。",
            "你只能使用 existing_nodes 中已经存在的节点 id。",
            f"最多补 {config.max_add_edges} 条最关键的新边；如果拿不准，宁可少补。",
            "优先把孤立的临床表现、风险背景、检查、影像、病原线索连接到合适的候选诊断，或把孤立诊断连接到关键证据。",
            "notes 最多写 3 条极短说明；如果没有补充说明，返回空数组。",
            "drop_node_ids 默认返回空数组。",
            *build_retry_feedback_block(retry_feedback),
            "",
            "[Chunk Metadata]",
            f"Chunk ID: {chunk.chunk_id}",
            f"Document: {chunk.relative_path}",
            f"Heading Path: {heading_text}",
            f"Line Range: {chunk.line_start}-{chunk.line_end}",
            "",
            "[Suspicious Nodes]",
            "\n".join(suspicious_lines),
            "",
            "[Existing Nodes JSON]",
            json.dumps(existing_nodes, ensure_ascii=False, indent=2),
            "",
            "[Existing Edges JSON]",
            json.dumps(existing_edges, ensure_ascii=False, indent=2),
            "",
            "[Chunk Text]",
            chunk.chunk_text,
        ]
    )
    return [
        {"role": "system", "content": REPAIR_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def extract_assistant_content(response: Any) -> str:
    choices = getattr(response, "choices", None)

    if not choices:
        raise RuntimeError("Model response does not contain choices.")

    message = choices[0].message
    content = getattr(message, "content", None)

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_parts = []

        for item in content:
            text_value = getattr(item, "text", None)

            if isinstance(text_value, str):
                text_parts.append(text_value)

        if len(text_parts) > 0:
            return "\n".join(text_parts)

    refusal = getattr(message, "refusal", None)

    if isinstance(refusal, str) and len(refusal) > 0:
        raise RuntimeError(f"Model refused the request: {refusal}")

    raise RuntimeError("Unable to extract text content from the model response.")


def strip_code_fences(text: str) -> str:
    stripped = text.strip()
    stripped = re.sub(r"^```json\s*", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"^```\s*", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def parse_json_content(content: str) -> Dict[str, Any]:
    trimmed = content.strip()

    try:
        return json.loads(trimmed)
    except json.JSONDecodeError:
        fenced = strip_code_fences(trimmed)

        try:
            return json.loads(fenced)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Failed to parse model JSON output. Excerpt: {summarize_text(trimmed, 700)}") from exc


def is_edge_direction_plausible(edge_type: str, source_label: Optional[str], target_label: Optional[str]) -> bool:
    if source_label is None or target_label is None:
        return False

    rules = EDGE_LABEL_RULES.get(edge_type)

    if rules is None:
        return False

    source_labels, target_labels = rules
    return source_label in source_labels and target_label in target_labels


def edge_signature(edge: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return (
        clean_text(edge.get("source_id")) or "",
        clean_text(edge.get("type")) or "",
        clean_text(edge.get("target_id")) or "",
        clean_text(edge.get("condition_text")) or "",
    )


def sanitize_repair_payload(
    payload: Dict[str, Any],
    node_map: Dict[str, Dict[str, Any]],
    suspicious_node_ids: set[str],
    existing_signatures: set[Tuple[str, str, str, str]],
    max_add_edges: int,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not isinstance(payload, dict):
        raise RepairValidationError("Repair payload must be an object.")

    raw_add_edges = payload.get("add_edges", [])
    raw_drop_node_ids = payload.get("drop_node_ids", [])
    raw_notes = payload.get("notes", [])
    sanitized_payload: Dict[str, Any] = {
        "add_edges": [],
        "drop_node_ids": [],
        "notes": [],
    }
    dropped_edge_reasons: Dict[str, int] = {}

    if not isinstance(raw_add_edges, list):
        raw_add_edges = []

    if not isinstance(raw_drop_node_ids, list):
        raw_drop_node_ids = []

    if not isinstance(raw_notes, list):
        raw_notes = []

    for note in raw_notes:
        cleaned_note = clean_text(note)

        if cleaned_note is not None:
            sanitized_payload["notes"].append(cleaned_note)

    for drop_node_id in raw_drop_node_ids:
        if isinstance(drop_node_id, str) and drop_node_id in suspicious_node_ids:
            sanitized_payload["drop_node_ids"].append(drop_node_id)

    for raw_edge in raw_add_edges:
        if not isinstance(raw_edge, dict):
            dropped_edge_reasons["invalid_edge_shape"] = dropped_edge_reasons.get("invalid_edge_shape", 0) + 1
            continue

        source_id = clean_text(raw_edge.get("source_id"))
        target_id = clean_text(raw_edge.get("target_id"))
        edge_type = clean_text(raw_edge.get("type"))
        evidence_text = clean_text(raw_edge.get("evidence_text"))

        if source_id is None or target_id is None:
            dropped_edge_reasons["missing_endpoint_id"] = dropped_edge_reasons.get("missing_endpoint_id", 0) + 1
            continue

        if source_id not in node_map or target_id not in node_map:
            dropped_edge_reasons["unknown_node_id"] = dropped_edge_reasons.get("unknown_node_id", 0) + 1
            continue

        if source_id not in suspicious_node_ids and target_id not in suspicious_node_ids:
            dropped_edge_reasons["no_suspicious_endpoint"] = dropped_edge_reasons.get("no_suspicious_endpoint", 0) + 1
            continue

        if source_id == target_id:
            dropped_edge_reasons["self_loop"] = dropped_edge_reasons.get("self_loop", 0) + 1
            continue

        if edge_type not in ALLOWED_EDGE_TYPES:
            dropped_edge_reasons["invalid_edge_type"] = dropped_edge_reasons.get("invalid_edge_type", 0) + 1
            continue

        source_label = clean_text(node_map[source_id].get("label"))
        target_label = clean_text(node_map[target_id].get("label"))

        if not is_edge_direction_plausible(edge_type, source_label, target_label):
            dropped_edge_reasons["implausible_direction"] = dropped_edge_reasons.get("implausible_direction", 0) + 1
            continue

        if evidence_text is None:
            dropped_edge_reasons["missing_evidence_text"] = dropped_edge_reasons.get("missing_evidence_text", 0) + 1
            continue

        detail_required = clean_text(raw_edge.get("detail_required")) or "standard"

        if detail_required not in DETAIL_LEVELS:
            detail_required = "standard"

        try:
            weight = float(raw_edge.get("weight", 0.7))
        except (TypeError, ValueError):
            weight = 0.7

        try:
            confidence = float(raw_edge.get("confidence", 0.7))
        except (TypeError, ValueError):
            confidence = 0.7

        weight = max(0.0, min(weight, 1.0))
        confidence = max(0.0, min(confidence, 1.0))
        attributes = raw_edge.get("attributes")

        if not isinstance(attributes, dict):
            attributes = {}

        condition_text = clean_text(attributes.get("condition_text")) or ""
        signature = (source_id, edge_type, target_id, condition_text)

        if signature in existing_signatures:
            dropped_edge_reasons["duplicate_edge"] = dropped_edge_reasons.get("duplicate_edge", 0) + 1
            continue

        sanitized_payload["add_edges"].append(
            {
                "source_id": source_id,
                "type": edge_type,
                "target_id": target_id,
                "weight": weight,
                "detail_required": detail_required,
                "evidence_text": evidence_text,
                "confidence": confidence,
                "attributes": attributes,
            }
        )
        existing_signatures.add(signature)

        if len(sanitized_payload["add_edges"]) >= max_add_edges:
            break

    sanitization_summary = {
        "raw_add_edge_count": len(raw_add_edges),
        "kept_add_edge_count": len(sanitized_payload["add_edges"]),
        "dropped_edge_reasons": dropped_edge_reasons,
    }
    return sanitized_payload, sanitization_summary


async def call_model_for_record(
    client: AsyncOpenAI,
    record: Dict[str, Any],
    suspicious_nodes: Sequence[SuspiciousNode],
    config: RepairConfig,
    retry_feedback: Optional[str] = None,
) -> Dict[str, Any]:
    request_kwargs: Dict[str, Any] = {
        "model": config.model,
        "messages": build_messages(record, suspicious_nodes, config, retry_feedback),
        "temperature": 0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "search_relation_repair",
                "strict": True,
                "schema": build_repair_output_schema(config.max_add_edges),
            },
        },
    }
    enable_thinking_env = os.getenv("REPAIR_ENABLE_THINKING")

    if enable_thinking_env is not None:
        # DashScope qwen3 系列结构化输出时关闭 thinking 更稳定；非 DashScope 服务可不设置该变量。
        request_kwargs["extra_body"] = {
            "enable_thinking": enable_thinking_env.strip().lower() in {"1", "true", "yes", "on"}
        }
    elif "dashscope" in config.base_url.lower():
        request_kwargs["extra_body"] = {"enable_thinking": False}

    response = await client.chat.completions.create(**request_kwargs)
    return parse_json_content(extract_assistant_content(response))


def build_added_edge_id(
    chunk_id: str,
    source_id: str,
    edge_type: str,
    target_id: str,
    condition_text: str,
    evidence_text: str,
) -> str:
    digest = hashlib.sha1(
        f"{chunk_id}|{source_id}|{edge_type}|{target_id}|{condition_text}|{evidence_text}".encode("utf-8")
    ).hexdigest()[:12]
    return f"repair_edge_{digest}"


def apply_repair_to_record(
    record: Dict[str, Any],
    payload: Dict[str, Any],
    suspicious_nodes: Sequence[SuspiciousNode],
    min_confidence: float,
    apply_drop_node_ids: bool,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    repaired_record = json.loads(json.dumps(record, ensure_ascii=False))
    extraction = repaired_record["extraction"]
    nodes = extraction["nodes"]
    edges = extraction["edges"]
    suspicious_node_ids = {node.node_id for node in suspicious_nodes}
    chunk_id = str(repaired_record.get("chunk_id", "unknown_chunk"))
    node_map = build_node_map(nodes)
    surviving_nodes: List[Dict[str, Any]] = []
    dropped_node_ids: set[str] = set()
    suggested_drop_node_ids: List[str] = []

    for drop_node_id in payload["drop_node_ids"]:
        if drop_node_id not in suspicious_node_ids:
            continue

        suggested_drop_node_ids.append(drop_node_id)

        if apply_drop_node_ids:
            dropped_node_ids.add(drop_node_id)

    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            continue

        node_id = clean_text(raw_node.get("id"))

        if node_id is None or node_id in dropped_node_ids:
            continue

        surviving_nodes.append(raw_node)

    surviving_node_ids = {
        clean_text(node.get("id"))
        for node in surviving_nodes
        if isinstance(node, dict) and clean_text(node.get("id")) is not None
    }
    surviving_node_ids = {node_id for node_id in surviving_node_ids if node_id is not None}
    surviving_edges: List[Dict[str, Any]] = []

    for raw_edge in edges:
        if not isinstance(raw_edge, dict):
            continue

        source_id = clean_text(raw_edge.get("source_id"))
        target_id = clean_text(raw_edge.get("target_id"))

        if source_id in surviving_node_ids and target_id in surviving_node_ids:
            surviving_edges.append(raw_edge)

    existing_signatures = {edge_signature(edge) for edge in surviving_edges if isinstance(edge, dict)}
    added_edges: List[Dict[str, Any]] = []

    for candidate_edge in payload["add_edges"]:
        confidence = float(candidate_edge.get("confidence", 0))

        if confidence < min_confidence:
            continue

        source_id = clean_text(candidate_edge.get("source_id"))
        target_id = clean_text(candidate_edge.get("target_id"))
        edge_type = clean_text(candidate_edge.get("type"))

        if source_id is None or target_id is None or edge_type is None:
            continue

        if source_id not in surviving_node_ids or target_id not in surviving_node_ids:
            continue

        attributes = candidate_edge.get("attributes")

        if not isinstance(attributes, dict):
            attributes = {}

        condition_text = clean_text(attributes.get("condition_text")) or ""
        signature = (source_id, edge_type, target_id, condition_text)

        if signature in existing_signatures:
            continue

        attributes["evidence_text"] = clean_text(candidate_edge.get("evidence_text")) or ""
        attributes["repair_confidence"] = confidence
        added_edge = {
            "id": build_added_edge_id(
                chunk_id=chunk_id,
                source_id=source_id,
                edge_type=edge_type,
                target_id=target_id,
                condition_text=condition_text,
                evidence_text=attributes["evidence_text"],
            ),
            "type": edge_type,
            "source_id": source_id,
            "target_id": target_id,
            "weight": float(candidate_edge.get("weight", 0.7)),
            "detail_required": clean_text(candidate_edge.get("detail_required")) or "standard",
            "attributes": attributes,
        }
        flatten_attributes(added_edge)
        existing_signatures.add(signature)
        added_edges.append(added_edge)

    extraction["nodes"] = surviving_nodes
    extraction["edges"] = surviving_edges + added_edges
    repaired_record["relation_repair"] = {
        "repair_schema": "search_only_v1",
        "candidate_node_ids": [node.node_id for node in suspicious_nodes],
        "candidate_reasons": {node.node_id: node.reasons for node in suspicious_nodes},
        "apply_drop_node_ids": apply_drop_node_ids,
        "suggested_drop_node_ids": suggested_drop_node_ids,
        "suggested_drop_node_count": len(suggested_drop_node_ids),
        "dropped_node_ids": sorted(dropped_node_ids),
        "added_edge_ids": [edge["id"] for edge in added_edges],
        "added_edge_count": len(added_edges),
        "dropped_node_count": len(dropped_node_ids),
        "notes": payload["notes"],
        "status": "repaired" if len(added_edges) > 0 or len(dropped_node_ids) > 0 else "reviewed_no_change",
    }
    summary = {
        "chunk_id": chunk_id,
        "relative_path": repaired_record.get("relative_path"),
        "candidate_node_count": len(suspicious_nodes),
        "added_edge_count": len(added_edges),
        "suggested_drop_node_count": len(suggested_drop_node_ids),
        "dropped_node_count": len(dropped_node_ids),
        "status": repaired_record["relation_repair"]["status"],
    }
    return repaired_record, summary


def summarize_exception_for_retry(exc: BaseException) -> str:
    message = clean_text(str(exc)) or exc.__class__.__name__

    return (
        "上一轮失败。请只输出严格 JSON，使用 existing_nodes 中已有 id，"
        "遵守搜索专用关系类型和方向，且 evidence_text 非空。"
        f" 错误摘要：{summarize_text(message, 420)}"
    )


async def with_retry(coro_factory, retry_count: int, retry_delay_ms: int, context_label: str) -> Any:
    attempt = 0
    last_error: Optional[BaseException] = None
    retry_feedback: Optional[str] = None

    while attempt < retry_count:
        attempt += 1

        try:
            return await coro_factory(attempt, retry_feedback)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            retry_feedback = summarize_exception_for_retry(exc)

            if attempt >= retry_count:
                break

            delay_seconds = (retry_delay_ms * attempt) / 1000
            print(
                f"[repair:retry] {context_label} failed on attempt {attempt}. "
                f"error={exc!r}. Retrying in {delay_seconds:.2f}s."
            )
            await asyncio.sleep(delay_seconds)

    raise last_error if last_error is not None else RuntimeError("Unknown relation repair failure.")


async def process_record(
    client: AsyncOpenAI,
    semaphore: asyncio.Semaphore,
    record: Dict[str, Any],
    config: RepairConfig,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if record.get("record_type") != "chunk_result":
        return record, {"status": "passthrough"}

    extraction = record.get("extraction")

    if not isinstance(extraction, dict):
        return record, {"status": "passthrough"}

    if not isinstance(extraction.get("nodes"), list) or not isinstance(extraction.get("edges"), list):
        return record, {"status": "passthrough"}

    suspicious_nodes = identify_suspicious_nodes(record)

    if len(suspicious_nodes) == 0:
        passthrough_record = json.loads(json.dumps(record, ensure_ascii=False))
        passthrough_record["relation_repair"] = {
            "repair_schema": "search_only_v1",
            "candidate_node_ids": [],
            "candidate_reasons": {},
            "dropped_node_ids": [],
            "added_edge_ids": [],
            "added_edge_count": 0,
            "dropped_node_count": 0,
            "notes": [],
            "status": "skipped_no_candidates",
        }
        return passthrough_record, {"status": "skipped_no_candidates"}

    chunk_id = str(record.get("chunk_id", "unknown_chunk"))
    node_map = build_node_map(extraction["nodes"])
    existing_signatures = {edge_signature(edge) for edge in extraction["edges"] if isinstance(edge, dict)}
    suspicious_node_ids = {node.node_id for node in suspicious_nodes}

    async with semaphore:
        print(
            f"[repair:start] {chunk_id} {record.get('relative_path')} "
            f"candidates={len(suspicious_nodes)}"
        )

        try:
            raw_payload = await with_retry(
                lambda _attempt, retry_feedback: call_model_for_record(
                    client,
                    record,
                    suspicious_nodes,
                    config,
                    retry_feedback=retry_feedback,
                ),
                config.retry_count,
                config.retry_delay_ms,
                chunk_id,
            )
            payload, sanitization_summary = sanitize_repair_payload(
                raw_payload,
                node_map,
                suspicious_node_ids,
                existing_signatures,
                config.max_add_edges,
            )
            repaired_record, summary = apply_repair_to_record(
                record,
                payload,
                suspicious_nodes,
                config.min_confidence,
                config.apply_drop_node_ids,
            )
            repaired_record["relation_repair"]["sanitization"] = sanitization_summary
            summary["sanitization"] = sanitization_summary
            print(
                f"[repair:done] {chunk_id} added_edges={summary['added_edge_count']} "
                f"dropped_nodes={summary['dropped_node_count']} status={summary['status']}"
            )
            return repaired_record, summary
        except Exception as exc:  # noqa: BLE001
            failure_record = json.loads(json.dumps(record, ensure_ascii=False))
            failure_record["relation_repair"] = {
                "repair_schema": "search_only_v1",
                "candidate_node_ids": [node.node_id for node in suspicious_nodes],
                "candidate_reasons": {node.node_id: node.reasons for node in suspicious_nodes},
                "dropped_node_ids": [],
                "added_edge_ids": [],
                "added_edge_count": 0,
                "dropped_node_count": 0,
                "notes": [],
                "status": "repair_failed",
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
            print(f"[repair:error] {chunk_id} {exc}", file=sys.stderr)
            return failure_record, {
                "status": "repair_failed",
                "chunk_id": chunk_id,
                "relative_path": record.get("relative_path"),
                "candidate_node_count": len(suspicious_nodes),
                "error": str(exc),
            }


async def run_async(config: RepairConfig) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    all_input_records = load_jsonl_records(config.input_file)
    retry_chunk_ids = determine_retry_chunk_ids(config)
    retry_chunk_id_set = set(retry_chunk_ids)
    processing_records = all_input_records

    if len(retry_chunk_id_set) > 0:
        processing_records = filter_records_for_retry(all_input_records, retry_chunk_id_set)

    semaphore = asyncio.Semaphore(config.concurrency)
    client = AsyncOpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=config.request_timeout_seconds,
        max_retries=config.sdk_max_retries,
    )

    try:
        tasks = [process_record(client, semaphore, record, config) for record in processing_records]
        results = await asyncio.gather(*tasks)
    finally:
        await client.close()

    processed_records = [result[0] for result in results]
    summaries = [result[1] for result in results]

    if len(retry_chunk_id_set) > 0 and config.baseline_output_file is not None and config.baseline_output_file.exists():
        baseline_records = load_jsonl_records(config.baseline_output_file)
        output_records = merge_retry_results_into_baseline(baseline_records, processed_records)
    else:
        output_records = processed_records

    chunk_summaries = [summary for summary in summaries if summary.get("status") != "passthrough"]
    repaired_count = sum(1 for summary in chunk_summaries if summary.get("status") == "repaired")
    failed_count = sum(1 for summary in chunk_summaries if summary.get("status") == "repair_failed")
    skipped_count = sum(1 for summary in chunk_summaries if summary.get("status") == "skipped_no_candidates")
    reviewed_no_change_count = sum(
        1 for summary in chunk_summaries if summary.get("status") == "reviewed_no_change"
    )
    total_added_edges = sum(int(summary.get("added_edge_count", 0) or 0) for summary in chunk_summaries)
    total_dropped_nodes = sum(int(summary.get("dropped_node_count", 0) or 0) for summary in chunk_summaries)
    total_filtered_invalid_edges = sum(
        sum(int(count or 0) for count in ((summary.get("sanitization") or {}).get("dropped_edge_reasons") or {}).values())
        for summary in chunk_summaries
    )
    report = {
        "repair_schema": "search_only_v1",
        "input_file": str(config.input_file),
        "output_file": str(config.output_file),
        "model": config.model,
        "base_url": config.base_url,
        "retry_mode": len(retry_chunk_id_set) > 0,
        "retry_chunk_ids": retry_chunk_ids,
        "retry_report_file": str(config.retry_report_file) if config.retry_report_file is not None else None,
        "baseline_output_file": str(config.baseline_output_file) if config.baseline_output_file is not None else None,
        "summary": {
            "record_count": len(all_input_records),
            "chunk_record_count": sum(1 for record in all_input_records if record.get("record_type") == "chunk_result"),
            "processed_record_count": len(processing_records),
            "processed_chunk_record_count": sum(
                1 for record in processing_records if record.get("record_type") == "chunk_result"
            ),
            "repaired_chunk_count": repaired_count,
            "repair_failed_chunk_count": failed_count,
            "skipped_no_candidate_chunk_count": skipped_count,
            "reviewed_no_change_chunk_count": reviewed_no_change_count,
            "total_added_edges": total_added_edges,
            "total_suggested_drop_nodes": sum(
                int(summary.get("suggested_drop_node_count", 0) or 0) for summary in chunk_summaries
            ),
            "total_dropped_nodes": total_dropped_nodes,
            "total_filtered_invalid_edges": total_filtered_invalid_edges,
        },
        "chunk_summaries": chunk_summaries,
    }
    return output_records, report


def write_jsonl(path: Path, records: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    config = read_env_config()

    if not config.input_file.exists():
        raise RuntimeError(f"Input file does not exist: {config.input_file}")

    if len(config.api_key) == 0:
        raise RuntimeError(
            "Missing API key. Set REPAIR_API_KEY, OPENAI_API_KEY, DASHSCOPE_API_KEY, or LLM_API_KEY."
        )

    output_records, report = asyncio.run(run_async(config))
    write_jsonl(config.output_file, output_records)
    config.report_file.parent.mkdir(parents=True, exist_ok=True)
    config.report_file.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = report["summary"]
    print(f"[repair] input={config.input_file}")
    print(f"[repair] output={config.output_file}")
    print(f"[repair] report={config.report_file}")
    print(
        "[repair] "
        f"chunks={summary['chunk_record_count']} "
        f"processed_chunks={summary['processed_chunk_record_count']} "
        f"repaired={summary['repaired_chunk_count']} "
        f"failed={summary['repair_failed_chunk_count']} "
        f"added_edges={summary['total_added_edges']} "
        f"dropped_nodes={summary['total_dropped_nodes']}"
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
