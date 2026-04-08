from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI

KG_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = KG_ROOT.parent

ALLOWED_LABELS = [
    "GuidelineDocument",
    "GuidelineSection",
    "EvidenceSpan",
    "Assertion",
    "Recommendation",
    "Disease",
    "DiseasePhase",
    "Pathogen",
    "Symptom",
    "Sign",
    "ClinicalAttribute",
    "LabTest",
    "LabFinding",
    "DiagnosticCriterion",
    "OpportunisticInfection",
    "Comorbidity",
    "Tumor",
    "SyndromeOrComplication",
    "ExposureScenario",
    "PopulationGroup",
    "RiskFactor",
    "TransmissionRoute",
    "ManagementAction",
    "Medication",
    "DrugClass",
    "TreatmentRegimen",
    "PreventionStrategy",
]

ALLOWED_EDGE_TYPES = [
    "HAS_SECTION",
    "HAS_EVIDENCE",
    "SUBJECT",
    "OBJECT",
    "SUPPORTED_BY",
    "RECOMMENDS",
    "CAUSED_BY",
    "HAS_PHASE",
    "MANIFESTS_AS",
    "HAS_LAB_FINDING",
    "DIAGNOSED_BY",
    "COMPLICATED_BY",
    "TREATED_WITH",
    "CONSISTS_OF",
    "BELONGS_TO_CLASS",
    "PREVENTED_BY",
    "MONITORED_BY",
    "SCREENED_BY",
    "APPLIES_TO",
    "RISK_FACTOR_FOR",
    "TRANSMITTED_VIA",
    "INTERACTS_WITH",
    "INITIATED_AFTER",
    "CONTRAINDICATED_IN",
    "NOT_RECOMMENDED_FOR",
    "REQUIRES_DETAIL",
]

DETAIL_LEVELS = {"minimal", "standard", "full"}

GRAPH_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["nodes", "edges"],
    "properties": {
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "label", "name", "weight", "detail_required"],
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string", "enum": ALLOWED_LABELS},
                    "name": {"type": "string"},
                    "canonical_name": {"type": "string"},
                    "aliases": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "definition": {"type": "string"},
                    "weight": {"type": "number", "minimum": 0, "maximum": 1},
                    "detail_required": {
                        "type": "string",
                        "enum": sorted(DETAIL_LEVELS),
                    },
                    "attributes": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                },
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "type", "source_id", "target_id", "weight", "detail_required"],
                "properties": {
                    "id": {"type": "string"},
                    "type": {"type": "string", "enum": ALLOWED_EDGE_TYPES},
                    "source_id": {"type": "string"},
                    "target_id": {"type": "string"},
                    "weight": {"type": "number", "minimum": 0, "maximum": 1},
                    "detail_required": {
                        "type": "string",
                        "enum": sorted(DETAIL_LEVELS),
                    },
                    "attributes": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                },
            },
        },
    },
}

SCHEMA_CONSTITUTION = f"""
你正在为一个 HIV/AIDS 问诊系统抽取可直接写入 Neo4j 的知识图谱。

你必须严格遵守以下本体与输出规则：
1. 只能返回严格 JSON，且顶层必须且只能包含两个键：`nodes` 和 `edges`。
2. 只能使用下面明确列出的节点类型和关系类型。
3. 每个节点必须包含以下字段：
   - id
   - label
   - name
   - weight
   - detail_required
4. 每条边必须包含以下字段：
   - id
   - type
   - source_id
   - target_id
   - weight
   - detail_required
5. `Recommendation` 必须作为独立节点存在。请将 `recommendation_no`、`evidence_grade`、`strength`、`recommendation_text` 放入 `Recommendation.attributes` 中。
   只有当文本中存在明确的推荐语句，且你能够可靠抽取推荐文本及其编号/等级/强度时，才输出为 `Recommendation`。
   如果只是一般性建议、流程描述或管理动作，而没有稳定的推荐元数据，请改为 `Assertion`、`ManagementAction`、`TreatmentRegimen` 或其他更合适的实体。
6. `LabFinding` 必须结构化表达。如果文本中出现阈值或比较条件，请放入 `LabFinding.attributes` 中，至少包含：
   - test_id
   - operator
   - value
   - unit
   如果原文是“高于检测下限”“低于检测下限”“阳性”“阴性”这类无法稳定转换为数值的检验结论，也可以使用：
   - value_text
   - reference_value_text
   如果你无法可靠判断某条信息对应的 `test_id` 或 `operator`，不要把它输出为 `LabFinding`，可改为 `DiagnosticCriterion`、`LabTest` 或其他更合适的实体。
7. 用于问诊追问的细节字段，必须建模为 `ClinicalAttribute` 节点，并通过 `REQUIRES_DETAIL` 关系连接。
8. 药物禁忌、条件限制或不推荐情形，必须使用 `CONTRAINDICATED_IN` 或 `NOT_RECOMMENDED_FOR` 关系表示。
9. 如果文本片段中存在明确证据，请尽量同时抽取 `GuidelineDocument`、`GuidelineSection`、`EvidenceSpan`、`Assertion` 以及 `SUPPORTED_BY`，以保留证据链和可解释性。
10. 不要臆造文本中不存在的事实。所有抽取结果都必须能够从当前文本块中直接或高度确定地归纳出来。
11. 每一条边的 `source_id` 和 `target_id` 都必须引用本次返回的 `nodes` 数组中真实存在的节点 id，禁止输出指向未定义节点的边。
12. 如果同一句中包含多个独立检验条件，例如“CD4 在某范围内且 HIV RNA 高于/低于检测下限”，请拆成多个 `LabFinding` 节点，不要把不同检验项目揉成一个复合 `LabFinding`。
13. 如果某种治疗或推荐只在满足特定阈值时成立，请在对应关系的 `attributes.condition_text` 中保留条件原文。

允许使用的节点标签：
{", ".join(ALLOWED_LABELS)}

允许使用的关系类型：
{", ".join(ALLOWED_EDGE_TYPES)}
""".strip()


@dataclass
class Config:
    root_dir: Path
    output_file: Path
    error_log_file: Path
    retry_error_log_file: Optional[Path]
    retry_error_types: List[str]
    concurrency: int
    retry_count: int
    retry_delay_ms: int
    target_chunk_chars: int
    max_chunk_chars: int
    min_chunk_chars: int
    api_key: str
    base_url: str
    model: str
    extra_body: Dict[str, Any]
    request_timeout_seconds: float
    sdk_max_retries: int


@dataclass
class Section:
    document_title: str
    heading_path: List[str]
    body_text: str
    heading_level: int
    line_start: int
    line_end: int
    relative_path: str


@dataclass
class Chunk:
    chunk_id: str
    relative_path: str
    document_title: str
    heading_path: List[str]
    line_start: int
    line_end: int
    text: str
    char_count: int


class ExtractionValidationError(RuntimeError):
    def __init__(self, message: str, error_code: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.details = details or {}


def compact_debug_value(value: Any, max_length: int = 240) -> Any:
    if isinstance(value, str):
        compacted = re.sub(r"\s+", " ", value).strip()

        if len(compacted) > max_length:
            return compacted[: max_length - 3] + "..."

        return compacted

    if isinstance(value, list):
        return [compact_debug_value(item, max_length=max_length) for item in value[:8]]

    if isinstance(value, dict):
        compacted: Dict[str, Any] = {}

        for index, key in enumerate(sorted(value.keys())):
            if index >= 12:
                compacted["..."] = f"{len(value) - 12} more keys"
                break

            compacted[str(key)] = compact_debug_value(value[key], max_length=max_length)

        return compacted

    return value


def summarize_node_for_error(node: Any) -> Any:
    if not isinstance(node, dict):
        return compact_debug_value(node)

    summary: Dict[str, Any] = {}

    for key in ["id", "label", "name", "canonical_name", "weight", "detail_required"]:
        if key in node:
            summary[key] = compact_debug_value(node.get(key))

    if "attributes" in node:
        summary["attributes"] = compact_debug_value(node.get("attributes"))

    return summary


def summarize_edge_for_error(edge: Any) -> Any:
    if not isinstance(edge, dict):
        return compact_debug_value(edge)

    summary: Dict[str, Any] = {}

    for key in ["id", "type", "source_id", "target_id", "weight", "detail_required"]:
        if key in edge:
            summary[key] = compact_debug_value(edge.get(key))

    if "attributes" in edge:
        summary["attributes"] = compact_debug_value(edge.get("attributes"))

    return summary


def raise_validation_error(
    message: str,
    error_code: str,
    *,
    chunk: Chunk,
    node: Optional[Dict[str, Any]] = None,
    edge: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    details: Dict[str, Any] = {
        "chunk_id": chunk.chunk_id,
        "relative_path": chunk.relative_path,
        "heading_path": chunk.heading_path,
    }

    if node is not None:
        details["node"] = summarize_node_for_error(node)

    if edge is not None:
        details["edge"] = summarize_edge_for_error(edge)

    if extra is not None:
        for key, value in extra.items():
            details[key] = compact_debug_value(value)

    raise ExtractionValidationError(message, error_code=error_code, details=details)


def read_env_config() -> Config:
    default_root_dir = PROJECT_ROOT / "HIV_cleaned"

    if not default_root_dir.exists():
        default_root_dir = PROJECT_ROOT / "HIV"

    root_dir = Path(os.getenv("PIPELINE_ROOT_DIR", str(default_root_dir))).resolve()
    output_file = Path(
        os.getenv("PIPELINE_OUTPUT_FILE", str(PROJECT_ROOT / "output_graph_test.jsonl"))
    ).resolve()
    error_log_file = Path(
        os.getenv(
            "PIPELINE_ERROR_LOG_FILE",
            str(output_file.with_name(f"{output_file.stem}_errors.jsonl")),
        )
    ).resolve()
    retry_error_log_raw = os.getenv("PIPELINE_RETRY_ERROR_LOG_FILE")
    retry_error_log_file = Path(retry_error_log_raw).resolve() if retry_error_log_raw else None
    retry_error_types = parse_csv_env(os.getenv("PIPELINE_RETRY_ERROR_TYPES"))
    concurrency = parse_positive_int(os.getenv("PIPELINE_CONCURRENCY"), 3)
    retry_count = parse_positive_int(os.getenv("PIPELINE_RETRY_COUNT"), 3)
    retry_delay_ms = parse_positive_int(os.getenv("PIPELINE_RETRY_DELAY_MS"), 1500)
    target_chunk_chars = parse_positive_int(os.getenv("PIPELINE_TARGET_CHARS"), 6000)
    max_chunk_chars = parse_positive_int(os.getenv("PIPELINE_MAX_CHARS"), 8000)
    min_chunk_chars = parse_positive_int(os.getenv("PIPELINE_MIN_CHARS"), 1500)
    api_key = (
        os.getenv("OPENAI_API_KEY")
        or os.getenv("DASHSCOPE_API_KEY")
        or os.getenv("LLM_API_KEY")
        or ""
    )
    base_url = (
        os.getenv("OPENAI_BASE_URL")
        or os.getenv("DASHSCOPE_BASE_URL")
        or os.getenv("LLM_BASE_URL")
        or "https://api.openai.com/v1"
    )
    model = os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL") or "gpt-4.1"
    extra_body = read_extra_body_config()
    request_timeout_seconds = parse_positive_float(os.getenv("PIPELINE_REQUEST_TIMEOUT_SECONDS"), 180.0)
    sdk_max_retries = parse_non_negative_int(os.getenv("PIPELINE_SDK_MAX_RETRIES"), 0)

    return Config(
        root_dir=root_dir,
        output_file=output_file,
        error_log_file=error_log_file,
        retry_error_log_file=retry_error_log_file,
        retry_error_types=retry_error_types,
        concurrency=concurrency,
        retry_count=retry_count,
        retry_delay_ms=retry_delay_ms,
        target_chunk_chars=target_chunk_chars,
        max_chunk_chars=max_chunk_chars,
        min_chunk_chars=min_chunk_chars,
        api_key=api_key,
        base_url=base_url,
        model=model,
        extra_body=extra_body,
        request_timeout_seconds=request_timeout_seconds,
        sdk_max_retries=sdk_max_retries,
    )


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


def parse_non_negative_int(value: Optional[str], fallback: int) -> int:
    if value is None:
        return fallback

    try:
        parsed = int(value)
    except ValueError:
        return fallback

    if parsed >= 0:
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


def parse_csv_env(value: Optional[str]) -> List[str]:
    if value is None or len(value.strip()) == 0:
        return []

    return [item.strip() for item in value.split(",") if len(item.strip()) > 0]


def parse_bool_env(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None

    normalized = value.strip().lower()

    if normalized in {"1", "true", "yes", "on"}:
        return True

    if normalized in {"0", "false", "no", "off"}:
        return False

    return None


def read_extra_body_config() -> Dict[str, Any]:
    extra_body: Dict[str, Any] = {}
    raw_extra_body = os.getenv("PIPELINE_EXTRA_BODY_JSON")
    enable_thinking = parse_bool_env(os.getenv("PIPELINE_ENABLE_THINKING"))

    if raw_extra_body is not None and len(raw_extra_body.strip()) > 0:
        try:
            parsed = json.loads(raw_extra_body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("PIPELINE_EXTRA_BODY_JSON is not valid JSON.") from exc

        if not isinstance(parsed, dict):
            raise RuntimeError("PIPELINE_EXTRA_BODY_JSON must decode to a JSON object.")

        extra_body.update(parsed)

    if enable_thinking is not None:
        extra_body["enable_thinking"] = enable_thinking

    return extra_body


def walk_markdown_files(root_dir: Path) -> List[Path]:
    results: List[Path] = []

    for path_item in sorted(root_dir.rglob("*.md"), key=lambda item: str(item)):
        if path_item.is_file():
            results.append(path_item)

    return results


def parse_markdown_sections(markdown: str, absolute_path: Path, root_dir: Path) -> List[Section]:
    relative_path = str(absolute_path.relative_to(root_dir))
    lines = markdown.splitlines()
    heading_pattern = re.compile(r"^(#{1,5})\s+(.*)$")
    sections: List[Section] = []
    document_title = absolute_path.stem
    current_heading_path: List[str] = []
    current_section: Optional[Dict[str, Any]] = None

    def push_current_section() -> None:
        nonlocal current_section

        if current_section is None:
            return

        body_text = "\n".join(current_section["body_lines"]).strip()
        normalized_section = Section(
            document_title=document_title,
            heading_path=list(current_section["heading_path"]),
            body_text=body_text,
            heading_level=int(current_section["heading_level"]),
            line_start=int(current_section["line_start"]),
            line_end=int(current_section["line_end"] or current_section["line_start"]),
            relative_path=relative_path,
        )

        if len(normalized_section.body_text) == 0 and len(normalized_section.heading_path) == 0:
            current_section = None
            return

        sections.append(normalized_section)
        current_section = None

    for index, line in enumerate(lines, start=1):
        heading_match = heading_pattern.match(line)

        if heading_match is not None:
            heading_level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()

            push_current_section()

            if heading_level == 1:
                document_title = heading_text

            parent_depth = heading_level - 1
            current_heading_path = current_heading_path[: min(len(current_heading_path), parent_depth)]
            current_heading_path.append(heading_text)

            current_section = {
                "heading_path": list(current_heading_path),
                "body_lines": [],
                "heading_level": heading_level,
                "line_start": index,
                "line_end": index,
            }
            continue

        if current_section is None:
            current_section = {
                "heading_path": list(current_heading_path),
                "body_lines": [],
                "heading_level": len(current_heading_path) if len(current_heading_path) > 0 else 0,
                "line_start": index,
                "line_end": index,
            }

        current_section["body_lines"].append(line)
        current_section["line_end"] = index

    push_current_section()

    return sections


def render_section(section: Section) -> str:
    heading_lines: List[str] = []

    for index, heading in enumerate(section.heading_path, start=1):
        heading_lines.append(f'{"#" * index} {heading}')

    heading_block = "\n".join(heading_lines).strip()
    body_block = section.body_text.strip()

    if len(heading_block) > 0 and len(body_block) > 0:
        return f"{heading_block}\n\n{body_block}"

    if len(heading_block) > 0:
        return heading_block

    return body_block


def split_oversized_section(section: Section, max_chunk_chars: int) -> List[Chunk]:
    heading_lines: List[str] = []

    for index, heading in enumerate(section.heading_path, start=1):
        heading_lines.append(f'{"#" * index} {heading}')

    heading_block = "\n".join(heading_lines).strip()
    paragraph_blocks = [item.strip() for item in re.split(r"\n\s*\n", section.body_text) if len(item.strip()) > 0]
    chunks: List[Chunk] = []
    current_paragraphs: List[str] = []
    current_char_count = len(heading_block)
    sub_index = 0

    def flush_current_paragraphs() -> None:
        nonlocal current_paragraphs
        nonlocal current_char_count
        nonlocal sub_index

        if len(current_paragraphs) == 0:
            return

        content_parts = [heading_block, "\n\n".join(current_paragraphs)]
        content = "\n\n".join([part for part in content_parts if len(part) > 0]).strip()
        text = content if len(content) > 0 else heading_block
        chunk_hash = hashlib.sha1(
            f"{section.relative_path}:{section.line_start}:{sub_index}:{text}".encode("utf-8")
        ).hexdigest()[:12]
        chunks.append(
            Chunk(
                chunk_id=f"chunk_{chunk_hash}",
                relative_path=section.relative_path,
                document_title=section.document_title,
                heading_path=list(section.heading_path),
                line_start=section.line_start,
                line_end=section.line_end,
                text=text,
                char_count=len(text),
            )
        )
        current_paragraphs = []
        current_char_count = len(heading_block)
        sub_index += 1

    for paragraph in paragraph_blocks:
        next_size = current_char_count + len(paragraph) + 2

        if next_size > max_chunk_chars and len(current_paragraphs) > 0:
            flush_current_paragraphs()

        if len(paragraph) > max_chunk_chars:
            sentence_parts = [item for item in re.split(r"(?<=[。！？.!?])\s*", paragraph) if len(item.strip()) > 0]
            current_sentence_buffer = ""

            for sentence_part in sentence_parts:
                if len(current_sentence_buffer) > 0:
                    candidate = f"{current_sentence_buffer}{sentence_part}"
                else:
                    candidate = sentence_part

                if len(candidate) > max_chunk_chars and len(current_sentence_buffer) > 0:
                    current_paragraphs.append(current_sentence_buffer.strip())
                    current_char_count += len(current_sentence_buffer) + 2
                    flush_current_paragraphs()
                    current_sentence_buffer = sentence_part
                else:
                    current_sentence_buffer = candidate

            if len(current_sentence_buffer.strip()) > 0:
                current_paragraphs.append(current_sentence_buffer.strip())
                current_char_count += len(current_sentence_buffer) + 2

            continue

        current_paragraphs.append(paragraph)
        current_char_count += len(paragraph) + 2

    flush_current_paragraphs()

    if len(chunks) == 0:
        rendered_section = render_section(section)
        chunk_hash = hashlib.sha1(
            f"{section.relative_path}:{section.line_start}:0:{rendered_section}".encode("utf-8")
        ).hexdigest()[:12]
        chunks.append(
            Chunk(
                chunk_id=f"chunk_{chunk_hash}",
                relative_path=section.relative_path,
                document_title=section.document_title,
                heading_path=list(section.heading_path),
                line_start=section.line_start,
                line_end=section.line_end,
                text=rendered_section,
                char_count=len(rendered_section),
            )
        )

    return chunks


def longest_common_heading_path(left: List[str], right: List[str]) -> List[str]:
    shared: List[str] = []
    shortest_length = min(len(left), len(right))

    for index in range(shortest_length):
        if left[index] == right[index]:
            shared.append(left[index])
        else:
            break

    return shared


def build_chunks_from_sections(sections: List[Section], config: Config) -> List[Chunk]:
    chunks: List[Chunk] = []
    current_chunk: Optional[Chunk] = None

    def flush_chunk() -> None:
        nonlocal current_chunk

        if current_chunk is None:
            return

        current_chunk.text = current_chunk.text.strip()
        current_chunk.char_count = len(current_chunk.text)
        chunks.append(current_chunk)
        current_chunk = None

    for section in sections:
        rendered_section = render_section(section)

        if len(rendered_section) == 0:
            continue

        if len(rendered_section) > config.max_chunk_chars:
            flush_chunk()
            chunks.extend(split_oversized_section(section, config.max_chunk_chars))
            continue

        if current_chunk is None:
            chunk_hash = hashlib.sha1(
                f"{section.relative_path}:{section.line_start}:{section.line_end}".encode("utf-8")
            ).hexdigest()[:12]
            current_chunk = Chunk(
                chunk_id=f"chunk_{chunk_hash}",
                relative_path=section.relative_path,
                document_title=section.document_title,
                heading_path=list(section.heading_path),
                line_start=section.line_start,
                line_end=section.line_end,
                text=rendered_section,
                char_count=len(rendered_section),
            )
            continue

        candidate_text = f"{current_chunk.text}\n\n{rendered_section}"

        if len(candidate_text) <= config.target_chunk_chars:
            current_chunk.text = candidate_text
            current_chunk.line_end = section.line_end
            current_chunk.heading_path = longest_common_heading_path(current_chunk.heading_path, section.heading_path)
            current_chunk.char_count = len(current_chunk.text)
            continue

        if current_chunk.char_count < config.min_chunk_chars and len(candidate_text) <= config.max_chunk_chars:
            current_chunk.text = candidate_text
            current_chunk.line_end = section.line_end
            current_chunk.heading_path = longest_common_heading_path(current_chunk.heading_path, section.heading_path)
            current_chunk.char_count = len(current_chunk.text)
            continue

        flush_chunk()
        chunk_hash = hashlib.sha1(
            f"{section.relative_path}:{section.line_start}:{section.line_end}:{rendered_section}".encode("utf-8")
        ).hexdigest()[:12]
        current_chunk = Chunk(
            chunk_id=f"chunk_{chunk_hash}",
            relative_path=section.relative_path,
            document_title=section.document_title,
            heading_path=list(section.heading_path),
            line_start=section.line_start,
            line_end=section.line_end,
            text=rendered_section,
            char_count=len(rendered_section),
        )

    flush_chunk()
    return chunks


def load_documents(config: Config) -> List[Dict[str, Any]]:
    documents: List[Dict[str, Any]] = []

    for absolute_path in walk_markdown_files(config.root_dir):
        markdown = absolute_path.read_text(encoding="utf-8")
        sections = parse_markdown_sections(markdown, absolute_path, config.root_dir)
        chunks = build_chunks_from_sections(sections, config)
        documents.append(
            {
                "absolute_path": str(absolute_path),
                "relative_path": str(absolute_path.relative_to(config.root_dir)),
                "sections": sections,
                "chunks": chunks,
            }
        )

    return documents


def build_extraction_messages(chunk: Chunk) -> List[Dict[str, str]]:
    heading_text = " > ".join(chunk.heading_path) if len(chunk.heading_path) > 0 else chunk.document_title
    metadata_text = "\n".join(
        [
            f"Document: {chunk.relative_path}",
            f"Chunk ID: {chunk.chunk_id}",
            f"Heading Path: {heading_text}",
            f"Line Range: {chunk.line_start}-{chunk.line_end}",
        ]
    )

    user_prompt = "\n".join(
        [
            "请从下面的 Markdown 文本块中抽取可直接写入 Neo4j 的知识图谱。",
            "请重点关注：临床实体、诊疗决策逻辑、禁忌或限制条件、监测要求、结构化阈值检验结果，以及证据支持关系。",
            "只允许返回严格 JSON，不要输出任何解释性文字。",
            "",
            "[元数据]",
            metadata_text,
            "",
            "[Markdown 文本块]",
            chunk.text,
        ]
    )

    return [
        {
            "role": "system",
            "content": f"{SCHEMA_CONSTITUTION}\n\n你必须严格遵守 JSON Schema，不要输出任何额外说明。",
        },
        {
            "role": "user",
            "content": user_prompt,
        },
    ]


async def call_model_for_chunk(client: AsyncOpenAI, chunk: Chunk, config: Config) -> Dict[str, Any]:
    request_kwargs: Dict[str, Any] = {
        "model": config.model,
        "messages": build_extraction_messages(chunk),
        "temperature": 0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "hiv_graph_extraction",
                "strict": True,
                "schema": GRAPH_OUTPUT_SCHEMA,
            },
        },
    }

    if len(config.extra_body) > 0:
        if config.extra_body.get("enable_thinking") is True:
            raise RuntimeError(
                "Structured output with response_format=json_schema is not compatible with enable_thinking=true "
                "on DashScope-compatible chat completions. Set PIPELINE_ENABLE_THINKING=false or unset it."
            )

        request_kwargs["extra_body"] = config.extra_body

    response = await client.chat.completions.create(
        **request_kwargs,
    )

    content = extract_assistant_content(response)
    parsed = parse_json_content(content)
    parsed = repair_lab_finding_nodes(parsed, chunk)
    parsed = repair_recommendation_nodes(parsed, chunk)

    try:
        validate_extraction_result(parsed, chunk)
    except RuntimeError as exc:
        if "unknown source_id" in str(exc) or "unknown target_id" in str(exc):
            parsed = repair_dangling_edges(parsed, chunk)
            validate_extraction_result(parsed, chunk)
        elif "LabFinding" in str(exc):
            parsed = repair_lab_finding_nodes(parsed, chunk)
            parsed = repair_recommendation_nodes(parsed, chunk)
            validate_extraction_result(parsed, chunk)
        elif "Recommendation" in str(exc):
            parsed = repair_recommendation_nodes(parsed, chunk)
            parsed = repair_lab_finding_nodes(parsed, chunk)
            validate_extraction_result(parsed, chunk)
        else:
            raise

    for node in parsed["nodes"]:
        flatten_attributes(node)

    for edge in parsed["edges"]:
        flatten_attributes(edge)

    return parsed


def extract_assistant_content(response: Any) -> str:
    choices = getattr(response, "choices", None)

    if not choices:
        raise RuntimeError("Model response does not contain choices.")

    message = choices[0].message
    content = getattr(message, "content", None)

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_parts: List[str] = []

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


def parse_json_content(content: str) -> Dict[str, Any]:
    trimmed = content.strip()

    try:
        return json.loads(trimmed)
    except json.JSONDecodeError:
        fenced = re.sub(r"^```json\s*", "", trimmed, flags=re.IGNORECASE)
        fenced = re.sub(r"^```\s*", "", fenced, flags=re.IGNORECASE)
        fenced = re.sub(r"\s*```$", "", fenced)

        try:
            return json.loads(fenced)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Failed to parse model JSON output. Raw content: {trimmed}") from exc


def validate_extraction_result(payload: Dict[str, Any], chunk: Chunk) -> None:
    if not isinstance(payload, dict):
        raise_validation_error(
            f"Chunk {chunk.chunk_id} returned a non-object payload.",
            "PAYLOAD_NOT_OBJECT",
            chunk=chunk,
            extra={"payload_type": type(payload).__name__},
        )

    nodes = payload.get("nodes")
    edges = payload.get("edges")

    if not isinstance(nodes, list):
        raise_validation_error(
            f"Chunk {chunk.chunk_id} is missing a valid nodes array.",
            "PAYLOAD_MISSING_NODES_ARRAY",
            chunk=chunk,
            extra={"nodes_type": type(nodes).__name__},
        )

    if not isinstance(edges, list):
        raise_validation_error(
            f"Chunk {chunk.chunk_id} is missing a valid edges array.",
            "PAYLOAD_MISSING_EDGES_ARRAY",
            chunk=chunk,
            extra={"edges_type": type(edges).__name__},
        )

    node_ids = set()

    for node in nodes:
        validate_node(node, chunk)
        node_ids.add(node["id"])

    for edge in edges:
        validate_edge(edge, node_ids, chunk)


def collect_dangling_edges(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    nodes = payload.get("nodes", [])
    edges = payload.get("edges", [])
    node_ids = {
        node["id"]
        for node in nodes
        if isinstance(node, dict) and isinstance(node.get("id"), str)
    }
    dangling_edges: List[Dict[str, Any]] = []

    for edge in edges:
        if not isinstance(edge, dict):
            continue

        source_id = edge.get("source_id")
        target_id = edge.get("target_id")

        if source_id not in node_ids or target_id not in node_ids:
            dangling_edges.append(edge)

    return dangling_edges


def normalize_lookup_text(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"[\s\-_()/（）,，.;；:+\[\]]+", "", normalized)
    return normalized


def build_lab_test_lookup(payload: Dict[str, Any]) -> Dict[str, str]:
    lookup: Dict[str, str] = {}

    for node in payload.get("nodes", []):
        if not isinstance(node, dict):
            continue

        if node.get("label") != "LabTest":
            continue

        node_id = node.get("id")

        if not isinstance(node_id, str):
            continue

        candidate_values: List[str] = []

        for key in ["name", "canonical_name"]:
            raw_value = node.get(key)

            if isinstance(raw_value, str) and len(raw_value.strip()) > 0:
                candidate_values.append(raw_value)

        aliases = node.get("aliases")

        if isinstance(aliases, list):
            for alias in aliases:
                if isinstance(alias, str) and len(alias.strip()) > 0:
                    candidate_values.append(alias)

        for raw_value in candidate_values:
            normalized = normalize_lookup_text(raw_value)

            if len(normalized) > 0:
                lookup[normalized] = node_id

            for token in re.findall(r"[A-Za-z][A-Za-z0-9+\-_/]*", raw_value):
                normalized_token = normalize_lookup_text(token)

                if len(normalized_token) > 1:
                    lookup[normalized_token] = node_id

    return lookup


def infer_lab_finding_operator(attributes: Dict[str, Any], text: str) -> Optional[str]:
    if "value_low" in attributes and "value_high" in attributes:
        return "between"

    if re.search(r">=", text):
        return ">="

    if re.search(r"<=", text):
        return "<="

    if re.search(r">", text):
        return ">"

    if re.search(r"<", text):
        return "<"

    if re.search(r"=", text):
        return "="

    if "高于检测下限" in text or "高于检测值下限" in text:
        return "above_detection_limit"

    if "低于检测下限" in text or "低于检测值下限" in text:
        return "below_detection_limit"

    if "阳性" in text:
        return "positive"

    if "阴性" in text:
        return "negative"

    if "转阴" in text:
        return "becomes_negative"

    if "转阳" in text:
        return "becomes_positive"

    if "升高" in text or "增高" in text or "高于正常值" in text:
        return "above_normal_range"

    if "降低" in text or "低于正常值" in text:
        return "below_normal_range"

    if re.search(r"\d+\s*[~～-]\s*\d+", text):
        return "between"

    return None


def infer_lab_finding_test_id(text: str, lab_test_lookup: Dict[str, str]) -> Optional[str]:
    normalized_text = normalize_lookup_text(text)

    for key in sorted(lab_test_lookup.keys(), key=len, reverse=True):
        if len(key) == 0:
            continue

        if key in normalized_text:
            return lab_test_lookup[key]

    heuristic_patterns = [
        (r"cd4", "LabTest_CD4_count"),
        (r"hivrna|病毒载量|hiv核酸", "LabTest_HIV_RNA"),
        (r"ldh|乳酸脱氢酶", "LabTest_LDH"),
        (r"bdg|βd葡聚糖|g试验|葡聚糖", "LabTest_beta_D_glucan"),
        (r"pao2|po2|动脉血氧分压|血氧分压", "LabTest_Arterial_PO2"),
        (r"aado2|肺泡动脉氧分压差|aado₂", "LabTest_AaDO2"),
        (r"cmvdna", "LabTest_CMV_DNA"),
        (r"弓形虫igg", "LabTest_Toxoplasma_IgG"),
    ]

    for pattern, test_id in heuristic_patterns:
        if re.search(pattern, normalized_text):
            return test_id

    return None


def parse_recommendation_number(*text_values: Optional[str]) -> Optional[int]:
    patterns = [
        r"推荐意见\s*([0-9]{1,3})",
        r"recommendation[_\s-]*([0-9]{1,3})",
        r"rec[_-]?([0-9]{1,3})",
    ]

    for text in text_values:
        if not isinstance(text, str) or len(text.strip()) == 0:
            continue

        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)

            if match is not None:
                try:
                    return int(match.group(1))
                except ValueError:
                    continue

    return None


def parse_recommendation_grade_strength(*text_values: Optional[str]) -> tuple[Optional[str], Optional[int]]:
    evidence_grade: Optional[str] = None
    strength: Optional[int] = None

    compact_text = " ".join(
        text.strip()
        for text in text_values
        if isinstance(text, str) and len(text.strip()) > 0
    )

    if len(compact_text) == 0:
        return evidence_grade, strength

    direct_match = re.search(r"[\(（]\s*([ABCabc])\s*([12])\s*[\)）]", compact_text)

    if direct_match is not None:
        evidence_grade = direct_match.group(1).upper()
        strength = int(direct_match.group(2))
        return evidence_grade, strength

    evidence_match = re.search(r"(?:证据(?:等级|级别|水平)|推荐级别)\s*[:：]?\s*([ABCabc])", compact_text)

    if evidence_match is not None:
        evidence_grade = evidence_match.group(1).upper()

    strength_match = re.search(r"(?:推荐强度|推荐等级|强度)\s*[:：]?\s*([12])", compact_text)

    if strength_match is not None:
        strength = int(strength_match.group(1))

    roman_strength_match = re.search(r"([ⅠIⅡ]{1,2}|IV|Ⅳ)\s*类推荐", compact_text)

    if strength is None and roman_strength_match is not None:
        roman_value = roman_strength_match.group(1)
        roman_map = {
            "Ⅰ": 1,
            "I": 1,
            "Ⅱ": 2,
            "II": 2,
            "Ⅳ": 2,
            "IV": 2,
        }
        strength = roman_map.get(roman_value)

    evidence_level_digit_match = re.search(r"证据(?:等级|级别|水平)\s*[:：]?\s*([12])", compact_text)

    if strength is None and evidence_level_digit_match is not None:
        strength = int(evidence_level_digit_match.group(1))

    return evidence_grade, strength


def is_generic_recommendation_name(value: Optional[str]) -> bool:
    if not isinstance(value, str):
        return False

    return re.fullmatch(r"\s*推荐意见\s*\d{1,3}\s*", value) is not None


def strip_recommendation_prefix(text: str) -> str:
    stripped = re.sub(r"^\s*推荐意见\s*\d{1,3}\s*[:：]?\s*", "", text).strip()
    return stripped


def strip_recommendation_rating_suffix(text: str) -> str:
    stripped = re.sub(r"\s*[\(（]\s*[ABCabc]\s*[12]\s*[\)）]\s*[。.]?\s*$", "", text).strip()
    return stripped


def summarize_recommendation_text(text: str, max_length: int = 36) -> str:
    normalized = re.sub(r"[ \t]{2,}", " ", text.strip())

    normalized = strip_recommendation_prefix(normalized)
    normalized = strip_recommendation_rating_suffix(normalized)
    normalized = normalized.strip("；;，,。. ")

    clause_candidates = [part.strip() for part in re.split(r"[、；;。]", normalized) if len(part.strip()) > 0]

    for clause in clause_candidates:
        if len(clause) <= max_length:
            return clause

    if len(normalized) <= max_length:
        return normalized

    sentence_match = re.match(r"^(.{1," + str(max_length) + r"}?[；;。.!！？])", normalized)

    if sentence_match is not None:
        return sentence_match.group(1).rstrip("；;。.!！？")

    return normalized[:max_length].rstrip()


def build_recommendation_display_name(
    recommendation_no: Optional[int],
    recommendation_text: Optional[str],
) -> Optional[str]:
    if not isinstance(recommendation_text, str) or len(recommendation_text.strip()) == 0:
        return None

    summary = summarize_recommendation_text(recommendation_text)

    if len(summary) == 0:
        return None

    if summary.startswith("推荐"):
        summary = summary[2:].strip()

    if isinstance(recommendation_no, int):
        return f"推荐意见{recommendation_no}：{summary}"

    return summary


def repair_dangling_edges(payload: Dict[str, Any], chunk: Chunk) -> Dict[str, Any]:
    repaired_payload = {
        "nodes": list(payload.get("nodes", [])),
        "edges": list(payload.get("edges", [])),
    }
    dangling_edges = collect_dangling_edges(repaired_payload)

    if len(dangling_edges) == 0:
        return repaired_payload

    dangling_edge_ids = [edge.get("id", "<unknown-edge-id>") for edge in dangling_edges]
    dangling_source_ids = sorted(
        {
            edge.get("source_id")
            for edge in dangling_edges
            if isinstance(edge.get("source_id"), str)
        }
    )
    dangling_target_ids = sorted(
        {
            edge.get("target_id")
            for edge in dangling_edges
            if isinstance(edge.get("target_id"), str)
        }
    )

    print(
        "[repair] "
        f"{chunk.chunk_id} dropped {len(dangling_edges)} dangling edges. "
        f"edge_ids={dangling_edge_ids} "
        f"source_ids={dangling_source_ids} "
        f"target_ids={dangling_target_ids}",
        file=sys.stderr,
    )

    repaired_payload["edges"] = [
        edge
        for edge in repaired_payload["edges"]
        if edge not in dangling_edges
    ]
    repaired_payload["repair_notes"] = [
        {
            "type": "dropped_dangling_edges",
            "count": len(dangling_edges),
            "edge_ids": dangling_edge_ids,
            "source_ids": dangling_source_ids,
            "target_ids": dangling_target_ids,
        }
    ]
    return repaired_payload


def repair_lab_finding_nodes(payload: Dict[str, Any], chunk: Chunk) -> Dict[str, Any]:
    repaired_payload = {
        "nodes": list(payload.get("nodes", [])),
        "edges": list(payload.get("edges", [])),
    }
    repair_notes = list(payload.get("repair_notes", []))
    initialized_attributes_count = 0
    repaired_value_count = 0
    repaired_operator_count = 0
    repaired_test_id_count = 0
    dropped_node_ids: List[str] = []
    lab_test_lookup = build_lab_test_lookup(repaired_payload)

    for node in repaired_payload["nodes"]:
        if not isinstance(node, dict):
            continue

        if node.get("label") != "LabFinding":
            continue

        attributes = node.get("attributes")

        if not isinstance(attributes, dict):
            attributes = {}
            node["attributes"] = attributes
            initialized_attributes_count += 1

        definition = node.get("definition")
        name = node.get("name")
        text_candidates: List[str] = []

        if isinstance(name, str) and len(name.strip()) > 0:
            text_candidates.append(name.strip())

        if isinstance(definition, str) and len(definition.strip()) > 0:
            text_candidates.append(definition.strip())

        if isinstance(attributes.get("value_text"), str) and len(str(attributes["value_text"]).strip()) > 0:
            text_candidates.append(str(attributes["value_text"]).strip())

        if isinstance(attributes.get("reference_value_text"), str) and len(str(attributes["reference_value_text"]).strip()) > 0:
            text_candidates.append(str(attributes["reference_value_text"]).strip())

        text_source = "；".join(text_candidates)

        has_numeric_value = "value" in attributes
        has_text_value = "value_text" in attributes
        has_reference_text = "reference_value_text" in attributes

        if "operator" not in attributes and len(text_source) > 0:
            inferred_operator = infer_lab_finding_operator(attributes, text_source)

            if inferred_operator is not None:
                attributes["operator"] = inferred_operator
                repaired_operator_count += 1

                if inferred_operator == "above_detection_limit" and "reference_value_text" not in attributes:
                    attributes["reference_value_text"] = "检测下限"

                if inferred_operator == "below_detection_limit" and "reference_value_text" not in attributes:
                    attributes["reference_value_text"] = "检测下限"

        if "test_id" not in attributes and len(text_source) > 0:
            inferred_test_id = infer_lab_finding_test_id(text_source, lab_test_lookup)

            if inferred_test_id is not None:
                attributes["test_id"] = inferred_test_id
                repaired_test_id_count += 1

        if not has_numeric_value and not has_text_value and not has_reference_text:
            fallback_value = None

            if isinstance(definition, str) and len(definition.strip()) > 0:
                fallback_value = definition.strip()
            elif isinstance(name, str) and len(name.strip()) > 0:
                fallback_value = name.strip()

            if fallback_value is not None:
                attributes["value_text"] = fallback_value
                repaired_value_count += 1

    repaired_payload["nodes"] = [
        node
        for node in repaired_payload["nodes"]
        if not (
            isinstance(node, dict)
            and node.get("label") == "LabFinding"
            and isinstance(node.get("attributes"), dict)
            and (
                "test_id" not in node["attributes"]
                or "operator" not in node["attributes"]
                or (
                    "value" not in node["attributes"]
                    and "value_text" not in node["attributes"]
                    and "reference_value_text" not in node["attributes"]
                )
            )
            and dropped_node_ids.append(str(node.get("id", "<unknown-node-id>"))) is None
        )
    ]

    if len(dropped_node_ids) > 0:
        repaired_payload["edges"] = [
            edge
            for edge in repaired_payload["edges"]
            if not (
                isinstance(edge, dict)
                and (
                    edge.get("source_id") in dropped_node_ids
                    or edge.get("target_id") in dropped_node_ids
                )
            )
        ]

    if repaired_value_count > 0:
        print(
            f"[repair] {chunk.chunk_id} backfilled value_text for {repaired_value_count} LabFinding nodes.",
            file=sys.stderr,
        )
        repair_notes.append(
            {
                "type": "backfilled_lab_finding_value_text",
                "count": repaired_value_count,
            }
        )

    if initialized_attributes_count > 0:
        print(
            f"[repair] {chunk.chunk_id} initialized empty attributes for {initialized_attributes_count} LabFinding nodes.",
            file=sys.stderr,
        )
        repair_notes.append(
            {
                "type": "initialized_lab_finding_attributes",
                "count": initialized_attributes_count,
            }
        )

    if repaired_operator_count > 0:
        print(
            f"[repair] {chunk.chunk_id} inferred operator for {repaired_operator_count} LabFinding nodes.",
            file=sys.stderr,
        )
        repair_notes.append(
            {
                "type": "inferred_lab_finding_operator",
                "count": repaired_operator_count,
            }
        )

    if repaired_test_id_count > 0:
        print(
            f"[repair] {chunk.chunk_id} inferred test_id for {repaired_test_id_count} LabFinding nodes.",
            file=sys.stderr,
        )
        repair_notes.append(
            {
                "type": "inferred_lab_finding_test_id",
                "count": repaired_test_id_count,
            }
        )

    if len(dropped_node_ids) > 0:
        print(
            f"[repair] {chunk.chunk_id} dropped {len(dropped_node_ids)} unrepaired LabFinding nodes.",
            file=sys.stderr,
        )
        repair_notes.append(
            {
                "type": "dropped_unrepaired_lab_finding_nodes",
                "count": len(dropped_node_ids),
                "node_ids": dropped_node_ids,
            }
        )

    if len(repair_notes) > 0:
        repaired_payload["repair_notes"] = repair_notes

    return repaired_payload


def repair_recommendation_nodes(payload: Dict[str, Any], chunk: Chunk) -> Dict[str, Any]:
    repaired_payload = {
        "nodes": list(payload.get("nodes", [])),
        "edges": list(payload.get("edges", [])),
    }
    repair_notes = list(payload.get("repair_notes", []))
    initialized_attributes_count = 0
    repaired_recommendation_no_count = 0
    repaired_recommendation_text_count = 0
    repaired_evidence_grade_count = 0
    repaired_strength_count = 0
    repaired_display_name_count = 0
    downgraded_node_ids: List[str] = []

    for node in repaired_payload["nodes"]:
        if not isinstance(node, dict):
            continue

        if node.get("label") != "Recommendation":
            continue

        attributes = node.get("attributes")

        if not isinstance(attributes, dict):
            attributes = {}
            node["attributes"] = attributes
            initialized_attributes_count += 1

        name = node.get("name")
        definition = node.get("definition")
        node_id = node.get("id")
        aliases = node.get("aliases")
        text_candidates: List[str] = []

        for candidate in [name, definition, node_id]:
            if isinstance(candidate, str) and len(candidate.strip()) > 0:
                text_candidates.append(candidate.strip())

        if isinstance(aliases, list):
            for alias in aliases:
                if isinstance(alias, str) and len(alias.strip()) > 0:
                    text_candidates.append(alias.strip())

        if "recommendation_text" not in attributes:
            fallback_text = None

            if isinstance(definition, str) and len(definition.strip()) > 0:
                fallback_text = definition.strip()
            elif isinstance(name, str) and len(name.strip()) > 0:
                fallback_text = name.strip()

            if fallback_text is not None:
                attributes["recommendation_text"] = fallback_text
                repaired_recommendation_text_count += 1

        if "recommendation_no" not in attributes:
            parsed_number = parse_recommendation_number(name, definition, node_id)

            if parsed_number is not None:
                attributes["recommendation_no"] = parsed_number
                repaired_recommendation_no_count += 1

        parsed_grade, parsed_strength = parse_recommendation_grade_strength(
            attributes.get("recommendation_text") if isinstance(attributes, dict) else None,
            definition,
            name,
        )

        if "evidence_grade" not in attributes and parsed_grade is not None:
            attributes["evidence_grade"] = parsed_grade
            repaired_evidence_grade_count += 1

        if "strength" not in attributes and parsed_strength is not None:
            attributes["strength"] = parsed_strength
            repaired_strength_count += 1

        display_name = build_recommendation_display_name(
            attributes.get("recommendation_no"),
            attributes.get("recommendation_text"),
        )

        if isinstance(display_name, str) and len(display_name) > 0:
            aliases = node.get("aliases")

            if not isinstance(aliases, list):
                aliases = []

            current_name = name.strip() if isinstance(name, str) else None

            if isinstance(current_name, str) and len(current_name) > 0 and current_name != display_name:
                if current_name not in aliases:
                    aliases.append(current_name)

            if current_name != display_name and (
                current_name is None or is_generic_recommendation_name(current_name)
            ):
                node["name"] = display_name
                repaired_display_name_count += 1

            canonical_name = node.get("canonical_name")

            if not isinstance(canonical_name, str) or len(canonical_name.strip()) == 0:
                node["canonical_name"] = display_name
            elif is_generic_recommendation_name(canonical_name):
                node["canonical_name"] = display_name

            node["aliases"] = aliases

        required_keys = ["recommendation_no", "evidence_grade", "strength", "recommendation_text"]

        if any(key not in attributes for key in required_keys):
            downgraded_node_ids.append(str(node.get("id", "<unknown-node-id>")))
            node["label"] = "Assertion"
            node["attributes"] = {
                "original_label": "Recommendation",
                "downgrade_reason": "missing_recommendation_attributes",
            }

            if isinstance(definition, str) and len(definition.strip()) > 0:
                node["definition"] = definition.strip()
            elif isinstance(name, str) and len(name.strip()) > 0:
                node["definition"] = name.strip()

    if initialized_attributes_count > 0:
        print(
            f"[repair] {chunk.chunk_id} initialized empty attributes for {initialized_attributes_count} Recommendation nodes.",
            file=sys.stderr,
        )
        repair_notes.append(
            {
                "type": "initialized_recommendation_attributes",
                "count": initialized_attributes_count,
            }
        )

    if repaired_recommendation_text_count > 0:
        print(
            f"[repair] {chunk.chunk_id} backfilled recommendation_text for {repaired_recommendation_text_count} Recommendation nodes.",
            file=sys.stderr,
        )
        repair_notes.append(
            {
                "type": "backfilled_recommendation_text",
                "count": repaired_recommendation_text_count,
            }
        )

    if repaired_recommendation_no_count > 0:
        print(
            f"[repair] {chunk.chunk_id} inferred recommendation_no for {repaired_recommendation_no_count} Recommendation nodes.",
            file=sys.stderr,
        )
        repair_notes.append(
            {
                "type": "inferred_recommendation_no",
                "count": repaired_recommendation_no_count,
            }
        )

    if repaired_evidence_grade_count > 0:
        print(
            f"[repair] {chunk.chunk_id} inferred evidence_grade for {repaired_evidence_grade_count} Recommendation nodes.",
            file=sys.stderr,
        )
        repair_notes.append(
            {
                "type": "inferred_recommendation_evidence_grade",
                "count": repaired_evidence_grade_count,
            }
        )

    if repaired_strength_count > 0:
        print(
            f"[repair] {chunk.chunk_id} inferred strength for {repaired_strength_count} Recommendation nodes.",
            file=sys.stderr,
        )
        repair_notes.append(
            {
                "type": "inferred_recommendation_strength",
                "count": repaired_strength_count,
            }
        )

    if repaired_display_name_count > 0:
        print(
            f"[repair] {chunk.chunk_id} upgraded display names for {repaired_display_name_count} Recommendation nodes.",
            file=sys.stderr,
        )
        repair_notes.append(
            {
                "type": "upgraded_recommendation_display_name",
                "count": repaired_display_name_count,
            }
        )

    if len(downgraded_node_ids) > 0:
        print(
            f"[repair] {chunk.chunk_id} downgraded {len(downgraded_node_ids)} Recommendation nodes to Assertion.",
            file=sys.stderr,
        )
        repair_notes.append(
            {
                "type": "downgraded_recommendation_to_assertion",
                "count": len(downgraded_node_ids),
                "node_ids": downgraded_node_ids,
            }
        )

    if len(repair_notes) > 0:
        repaired_payload["repair_notes"] = repair_notes

    return repaired_payload


def validate_node(node: Dict[str, Any], chunk: Chunk) -> None:
    if not isinstance(node, dict):
        raise_validation_error(
            f"Chunk {chunk.chunk_id} contains an invalid node item.",
            "NODE_NOT_OBJECT",
            chunk=chunk,
            extra={"node_item": node},
        )

    if not isinstance(node.get("id"), str) or len(node["id"].strip()) == 0:
        raise_validation_error(
            f"Chunk {chunk.chunk_id} contains a node with an invalid id.",
            "NODE_INVALID_ID",
            chunk=chunk,
            node=node,
        )

    if not isinstance(node.get("label"), str) or node["label"] not in ALLOWED_LABELS:
        raise_validation_error(
            f"Chunk {chunk.chunk_id} contains an unsupported node label: {node.get('label')}",
            "NODE_UNSUPPORTED_LABEL",
            chunk=chunk,
            node=node,
        )

    if not isinstance(node.get("name"), str) or len(node["name"].strip()) == 0:
        raise_validation_error(
            f"Chunk {chunk.chunk_id} contains a node with an invalid name.",
            "NODE_INVALID_NAME",
            chunk=chunk,
            node=node,
        )

    if not isinstance(node.get("weight"), (int, float)) or node["weight"] < 0 or node["weight"] > 1:
        raise_validation_error(
            f"Chunk {chunk.chunk_id} contains a node with an invalid weight.",
            "NODE_INVALID_WEIGHT",
            chunk=chunk,
            node=node,
        )

    if node.get("detail_required") not in DETAIL_LEVELS:
        raise_validation_error(
            f"Chunk {chunk.chunk_id} contains a node with an invalid detail_required value.",
            "NODE_INVALID_DETAIL_REQUIRED",
            chunk=chunk,
            node=node,
        )

    if node["label"] == "LabFinding":
        validate_lab_finding_node(node, chunk)

    if node["label"] == "Recommendation":
        validate_recommendation_node(node, chunk)


def validate_lab_finding_node(node: Dict[str, Any], chunk: Chunk) -> None:
    attributes = node.get("attributes")

    if not isinstance(attributes, dict):
        raise_validation_error(
            f"Chunk {chunk.chunk_id} returned LabFinding without attributes.",
            "LAB_FINDING_MISSING_ATTRIBUTES",
            chunk=chunk,
            node=node,
        )

    for key in ["test_id", "operator"]:
        if key not in attributes:
            raise_validation_error(
                f"Chunk {chunk.chunk_id} returned LabFinding missing attributes.{key} "
                f"for node {node.get('id')} ({node.get('name')}).",
                f"LAB_FINDING_MISSING_{key.upper()}",
                chunk=chunk,
                node=node,
                extra={
                    "missing_field": key,
                    "present_attribute_keys": sorted(attributes.keys()),
                },
            )

    has_numeric_value = "value" in attributes
    has_text_value = "value_text" in attributes
    has_reference_text = "reference_value_text" in attributes

    if not has_numeric_value and not has_text_value and not has_reference_text:
        raise_validation_error(
            f"Chunk {chunk.chunk_id} returned LabFinding missing attributes.value/value_text/reference_value_text "
            f"for node {node.get('id')} ({node.get('name')}).",
            "LAB_FINDING_MISSING_VALUE_FIELDS",
            chunk=chunk,
            node=node,
            extra={"present_attribute_keys": sorted(attributes.keys())},
        )

    if has_numeric_value and "unit" not in attributes:
        raise_validation_error(
            f"Chunk {chunk.chunk_id} returned numeric LabFinding missing attributes.unit "
            f"for node {node.get('id')} ({node.get('name')}).",
            "LAB_FINDING_MISSING_UNIT",
            chunk=chunk,
            node=node,
            extra={"present_attribute_keys": sorted(attributes.keys())},
        )


def validate_recommendation_node(node: Dict[str, Any], chunk: Chunk) -> None:
    attributes = node.get("attributes")

    if not isinstance(attributes, dict):
        raise_validation_error(
            f"Chunk {chunk.chunk_id} returned Recommendation without attributes.",
            "RECOMMENDATION_MISSING_ATTRIBUTES",
            chunk=chunk,
            node=node,
        )

    for key in ["recommendation_no", "evidence_grade", "strength", "recommendation_text"]:
        if key not in attributes:
            raise_validation_error(
                f"Chunk {chunk.chunk_id} returned Recommendation missing attributes.{key} "
                f"for node {node.get('id')} ({node.get('name')}).",
                f"RECOMMENDATION_MISSING_{key.upper()}",
                chunk=chunk,
                node=node,
                extra={
                    "missing_field": key,
                    "present_attribute_keys": sorted(attributes.keys()),
                },
            )


def validate_edge(edge: Dict[str, Any], node_ids: set[str], chunk: Chunk) -> None:
    if not isinstance(edge, dict):
        raise_validation_error(
            f"Chunk {chunk.chunk_id} contains an invalid edge item.",
            "EDGE_NOT_OBJECT",
            chunk=chunk,
            extra={"edge_item": edge},
        )

    if not isinstance(edge.get("id"), str) or len(edge["id"].strip()) == 0:
        raise_validation_error(
            f"Chunk {chunk.chunk_id} contains an edge with an invalid id.",
            "EDGE_INVALID_ID",
            chunk=chunk,
            edge=edge,
        )

    if not isinstance(edge.get("type"), str) or edge["type"] not in ALLOWED_EDGE_TYPES:
        raise_validation_error(
            f"Chunk {chunk.chunk_id} contains an unsupported edge type: {edge.get('type')}",
            "EDGE_UNSUPPORTED_TYPE",
            chunk=chunk,
            edge=edge,
        )

    if not isinstance(edge.get("source_id"), str) or edge["source_id"] not in node_ids:
        raise_validation_error(
            f"Chunk {chunk.chunk_id} contains an edge with an unknown source_id: {edge.get('source_id')}",
            "EDGE_UNKNOWN_SOURCE_ID",
            chunk=chunk,
            edge=edge,
            extra={"known_node_ids_count": len(node_ids)},
        )

    if not isinstance(edge.get("target_id"), str) or edge["target_id"] not in node_ids:
        raise_validation_error(
            f"Chunk {chunk.chunk_id} contains an edge with an unknown target_id: {edge.get('target_id')}",
            "EDGE_UNKNOWN_TARGET_ID",
            chunk=chunk,
            edge=edge,
            extra={"known_node_ids_count": len(node_ids)},
        )

    if not isinstance(edge.get("weight"), (int, float)) or edge["weight"] < 0 or edge["weight"] > 1:
        raise_validation_error(
            f"Chunk {chunk.chunk_id} contains an edge with an invalid weight.",
            "EDGE_INVALID_WEIGHT",
            chunk=chunk,
            edge=edge,
        )

    if edge.get("detail_required") not in DETAIL_LEVELS:
        raise_validation_error(
            f"Chunk {chunk.chunk_id} contains an edge with an invalid detail_required value.",
            "EDGE_INVALID_DETAIL_REQUIRED",
            chunk=chunk,
            edge=edge,
        )


def flatten_attributes(item: Dict[str, Any]) -> Dict[str, Any]:
    attributes = item.get("attributes")

    if isinstance(attributes, dict):
        for key, value in attributes.items():
            if key not in item:
                item[key] = value

        del item["attributes"]

    return item


async def with_retry(coro_factory, retry_count: int, retry_delay_ms: int, context_label: str) -> Any:
    attempt = 0
    last_error: Optional[BaseException] = None

    while attempt < retry_count:
        try:
            return await coro_factory()
        except Exception as exc:  # noqa: BLE001
            if not is_retryable_exception(exc):
                raise

            last_error = exc
            attempt += 1

            if attempt >= retry_count:
                break

            delay_seconds = (retry_delay_ms * attempt) / 1000
            print(
                f"[retry] {context_label} failed on attempt {attempt}. "
                f"type={type(exc).__name__} error={exc!r}. Retrying in {delay_seconds:.2f}s.",
                file=sys.stderr,
            )
            await asyncio.sleep(delay_seconds)

    raise last_error if last_error is not None else RuntimeError("Unknown retry failure.")


def is_retryable_exception(exc: BaseException) -> bool:
    error_type = type(exc).__name__
    error_text = str(exc)

    if error_type in {"APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError"}:
        return True

    if isinstance(exc, RuntimeError):
        retryable_runtime_markers = [
            "Failed to parse model JSON output.",
            "Unable to extract text content from the model response.",
            "Model response does not contain choices.",
        ]

        for marker in retryable_runtime_markers:
            if marker in error_text:
                return True

        return False

    return True


def summarize_run(run_record: Dict[str, Any]) -> Dict[str, int]:
    node_count = 0
    edge_count = 0

    for chunk in run_record["chunks"]:
        node_count += len(chunk["extraction"]["nodes"])
        edge_count += len(chunk["extraction"]["edges"])

    return {
        "chunk_count": len(run_record["chunks"]),
        "node_count": node_count,
        "edge_count": edge_count,
        "success_count": len([item for item in run_record["chunks"] if item["status"] == "success"]),
        "failure_count": len([item for item in run_record["chunks"] if item["status"] == "failed"]),
    }


def append_jsonl_record(output_file: Path, record: Dict[str, Any]) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with output_file.open("a", encoding="utf-8") as handle:
        handle.write(f"{json.dumps(record, ensure_ascii=False)}\n")


def load_retry_chunks(error_log_file: Path, retry_error_types: List[str]) -> List[Chunk]:
    if not error_log_file.exists():
        raise RuntimeError(f"Retry error log file does not exist: {error_log_file}")

    retry_chunks_by_id: Dict[str, Chunk] = {}

    with error_log_file.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()

            if len(line) == 0:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                print(
                    f"[retry:skip] invalid JSON line in {error_log_file}",
                    file=sys.stderr,
                )
                continue

            if record.get("record_type") != "chunk_error":
                continue

            error_type = record.get("error_type", "")

            if len(retry_error_types) > 0 and error_type not in retry_error_types:
                continue

            chunk_id = record.get("chunk_id")
            chunk_text = record.get("chunk_text")

            if not isinstance(chunk_id, str) or len(chunk_id) == 0:
                continue

            if not isinstance(chunk_text, str) or len(chunk_text) == 0:
                continue

            retry_chunks_by_id[chunk_id] = Chunk(
                chunk_id=chunk_id,
                relative_path=str(record.get("relative_path", "")),
                document_title=str(record.get("document_title", "")),
                heading_path=list(record.get("heading_path", [])),
                line_start=int(record.get("line_start", 0)),
                line_end=int(record.get("line_end", 0)),
                text=chunk_text,
                char_count=int(record.get("char_count", len(chunk_text))),
            )

    return list(retry_chunks_by_id.values())


async def process_chunk(
    client: AsyncOpenAI,
    chunk: Chunk,
    config: Config,
    semaphore: asyncio.Semaphore,
    output_lock: asyncio.Lock,
    error_lock: asyncio.Lock,
    run_id: str,
) -> Dict[str, Any]:
    async with semaphore:
        print(f"[chunk:start] {chunk.chunk_id} {chunk.relative_path} {chunk.line_start}-{chunk.line_end}")

        try:
            extraction = await with_retry(
                lambda: call_model_for_chunk(client, chunk, config),
                config.retry_count,
                config.retry_delay_ms,
                chunk.chunk_id,
            )
            print(
                f"[chunk:done] {chunk.chunk_id} nodes={len(extraction['nodes'])} edges={len(extraction['edges'])}"
            )

            result = {
                "chunk_id": chunk.chunk_id,
                "relative_path": chunk.relative_path,
                "document_title": chunk.document_title,
                "heading_path": chunk.heading_path,
                "line_start": chunk.line_start,
                "line_end": chunk.line_end,
                "char_count": chunk.char_count,
                "status": "success",
                "extraction": extraction,
            }

            output_record = {
                "record_type": "chunk_result",
                "run_id": run_id,
                "chunk_id": chunk.chunk_id,
                "relative_path": chunk.relative_path,
                "document_title": chunk.document_title,
                "heading_path": chunk.heading_path,
                "line_start": chunk.line_start,
                "line_end": chunk.line_end,
                "char_count": chunk.char_count,
                "status": "success",
                "extraction": extraction,
            }

            async with output_lock:
                await asyncio.to_thread(append_jsonl_record, config.output_file, output_record)

            return result
        except Exception as exc:  # noqa: BLE001
            retryable = is_retryable_exception(exc)
            error_code = getattr(exc, "error_code", type(exc).__name__)
            error_details = getattr(exc, "details", None)
            print(
                f"[chunk:error] {chunk.chunk_id} code={error_code} retryable={retryable} {exc}",
                file=sys.stderr,
            )
            error_traceback = traceback.format_exc()

            error_record = {
                "record_type": "chunk_error",
                "run_id": run_id,
                "chunk_id": chunk.chunk_id,
                "relative_path": chunk.relative_path,
                "document_title": chunk.document_title,
                "heading_path": chunk.heading_path,
                "line_start": chunk.line_start,
                "line_end": chunk.line_end,
                "char_count": chunk.char_count,
                "error_type": type(exc).__name__,
                "error_code": error_code,
                "retryable": retryable,
                "error": str(exc),
                "error_repr": repr(exc),
                "error_details": error_details,
                "traceback": error_traceback,
                "chunk_text": chunk.text,
            }

            async with error_lock:
                await asyncio.to_thread(append_jsonl_record, config.error_log_file, error_record)

            return {
                "chunk_id": chunk.chunk_id,
                "relative_path": chunk.relative_path,
                "document_title": chunk.document_title,
                "heading_path": chunk.heading_path,
                "line_start": chunk.line_start,
                "line_end": chunk.line_end,
                "char_count": chunk.char_count,
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "extraction": {
                    "nodes": [],
                    "edges": [],
                },
            }


async def async_main() -> None:
    config = read_env_config()

    if len(config.api_key) == 0:
        raise RuntimeError(
            "Missing API key. Set OPENAI_API_KEY, DASHSCOPE_API_KEY, or LLM_API_KEY before running pipeline.py."
        )

    if config.retry_error_log_file is not None:
        documents: List[Dict[str, Any]] = []
        all_chunks = load_retry_chunks(config.retry_error_log_file, config.retry_error_types)
    else:
        documents = load_documents(config)
        all_chunks = [chunk for document in documents for chunk in document["chunks"]]

    semaphore = asyncio.Semaphore(config.concurrency)
    output_lock = asyncio.Lock()
    error_lock = asyncio.Lock()
    started_at = asyncio.get_running_loop().time()
    run_id = f"run_{uuid.uuid4()}"

    if config.retry_error_log_file is not None:
        print(
            f"[pipeline] loaded {len(all_chunks)} retry chunks from {config.retry_error_log_file}"
        )
    else:
        print(f"[pipeline] loaded {len(documents)} markdown files and {len(all_chunks)} chunks.")

    print(f"[pipeline] chunk results will be appended to {config.output_file}")
    print(f"[pipeline] chunk errors will be appended to {config.error_log_file}")
    print(
        "[pipeline] client config: "
        f"concurrency={config.concurrency}, "
        f"timeout={config.request_timeout_seconds}s, "
        f"sdk_max_retries={config.sdk_max_retries}"
    )

    client = AsyncOpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=config.request_timeout_seconds,
        max_retries=config.sdk_max_retries,
    )
    chunk_results = await asyncio.gather(
        *[
            process_chunk(client, chunk, config, semaphore, output_lock, error_lock, run_id)
            for chunk in all_chunks
        ]
    )

    run_record: Dict[str, Any] = {
        "run_id": run_id,
        "started_at_epoch": started_at,
        "finished_at_epoch": asyncio.get_running_loop().time(),
        "root_dir": str(config.root_dir),
        "model": config.model,
        "base_url": config.base_url,
        "run_mode": "retry" if config.retry_error_log_file is not None else "full_scan",
        "config": {
            "concurrency": config.concurrency,
            "retry_count": config.retry_count,
            "retry_delay_ms": config.retry_delay_ms,
            "target_chunk_chars": config.target_chunk_chars,
            "max_chunk_chars": config.max_chunk_chars,
            "min_chunk_chars": config.min_chunk_chars,
            "retry_error_log_file": str(config.retry_error_log_file) if config.retry_error_log_file else None,
            "retry_error_types": config.retry_error_types,
        },
        "files": (
            [document["relative_path"] for document in documents]
            if len(documents) > 0
            else sorted({chunk.relative_path for chunk in all_chunks})
        ),
        "chunks": chunk_results,
    }
    run_record["summary"] = summarize_run(run_record)

    summary_record = {
        "record_type": "run_summary",
        "run_id": run_id,
        "started_at_epoch": run_record["started_at_epoch"],
        "finished_at_epoch": run_record["finished_at_epoch"],
        "root_dir": run_record["root_dir"],
        "model": run_record["model"],
        "base_url": run_record["base_url"],
        "config": run_record["config"],
        "files": run_record["files"],
        "summary": run_record["summary"],
    }

    await asyncio.to_thread(append_jsonl_record, config.output_file, summary_record)

    print(f"[pipeline] completed. output={config.output_file}")
    print(
        "[pipeline] summary: "
        f"chunks={run_record['summary']['chunk_count']}, "
        f"successes={run_record['summary']['success_count']}, "
        f"failures={run_record['summary']['failure_count']}, "
        f"nodes={run_record['summary']['node_count']}, "
        f"edges={run_record['summary']['edge_count']}"
    )


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("[fatal] interrupted by user", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:  # noqa: BLE001
        print(f"[fatal] {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
