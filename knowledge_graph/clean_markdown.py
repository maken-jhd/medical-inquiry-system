from __future__ import annotations

import concurrent.futures
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI

KG_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = KG_ROOT.parent

ZERO_WIDTH_PATTERN = re.compile(r"[\u200b\u200c\u200d\u2060]")
HEADING_PATTERN = re.compile(r"^(#{1,5})\s*(.*?)\s*$")
RANGE_PATTERN = re.compile(r"\s*[~～]\s*")
MULTISPACE_PATTERN = re.compile(r"[ \t]{2,}")
TABLE_LINE_PATTERN = re.compile(r"^\s*\|.*\|\s*$")
TABLE_SEPARATOR_PATTERN = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$")

TERM_NORMALIZATION_RULES: List[Tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"CD4\s*[\+＋⁺]?\s*T\s*(?:淋巴细胞|细胞)?", re.IGNORECASE),
        "CD4+ T 淋巴细胞",
        "normalized_cd4_term_count",
    ),
    (
        re.compile(r"CD8\s*[\+＋⁺]?\s*T\s*(?:淋巴细胞|细胞)?", re.IGNORECASE),
        "CD8+ T 淋巴细胞",
        "normalized_cd8_term_count",
    ),
    (
        re.compile(r"HIV\s*RNA", re.IGNORECASE),
        "HIV RNA",
        "normalized_hiv_rna_term_count",
    ),
    (
        re.compile(r"P24\s*抗原", re.IGNORECASE),
        "P24 抗原",
        "normalized_p24_term_count",
    ),
]
UNIT_NORMALIZATION_RULES: List[Tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"(?:个|cells)\s*/\s*(?:μL|uL|ul)", re.IGNORECASE),
        "/μL",
        "normalized_cells_per_microliter_count",
    ),
    (
        re.compile(r"(?:拷贝|copies)\s*/\s*mL", re.IGNORECASE),
        "copies/mL",
        "normalized_copies_per_ml_count",
    ),
    (
        re.compile(r"pg\s*/\s*mL", re.IGNORECASE),
        "pg/mL",
        "normalized_pg_per_ml_count",
    ),
    (
        re.compile(r"mg\s*/\s*L", re.IGNORECASE),
        "mg/L",
        "normalized_mg_per_l_count",
    ),
]
SYMBOL_NORMALIZATION_RULES: List[Tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"≥"), ">=", "normalized_greater_equal_count"),
    (re.compile(r"≤"), "<=", "normalized_less_equal_count"),
    (re.compile(r"＞"), ">", "normalized_greater_than_count"),
    (re.compile(r"＜"), "<", "normalized_less_than_count"),
    (re.compile(r"＝"), "=", "normalized_equal_count"),
]


@dataclass
class CleanConfig:
    input_dir: Path
    output_dir: Path
    report_file: Path
    table_draft_file: Path
    concurrency: int
    table_llm_enabled: bool
    table_validator_enabled: bool
    api_key: str
    base_url: str
    table_model: str
    table_validator_model: str
    request_timeout_seconds: float
    retry_count: int
    retry_delay_ms: int


@dataclass
class TableBlock:
    start_index: int
    end_index: int
    heading_path: List[str]
    table_text: str
    before_context: str
    after_context: str


def read_env_config() -> CleanConfig:
    input_dir = Path(os.getenv("CLEAN_INPUT_DIR", str(PROJECT_ROOT / "HIV"))).resolve()
    output_dir = Path(os.getenv("CLEAN_OUTPUT_DIR", str(PROJECT_ROOT / "HIV_cleaned"))).resolve()
    report_file = Path(
        os.getenv("CLEAN_REPORT_FILE", str(PROJECT_ROOT / "cleaning_report.jsonl"))
    ).resolve()
    table_draft_file = Path(
        os.getenv("CLEAN_TABLE_DRAFT_FILE", str(PROJECT_ROOT / "cleaning_table_drafts.jsonl"))
    ).resolve()
    concurrency = parse_positive_int(os.getenv("CLEAN_CONCURRENCY"), 10)
    table_llm_enabled = parse_bool_env(os.getenv("CLEAN_TABLE_LLM_ENABLED"), True)
    table_validator_enabled = parse_bool_env(os.getenv("CLEAN_TABLE_VALIDATOR_ENABLED"), True)
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
    table_model = os.getenv("CLEAN_TABLE_MODEL") or os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL") or "gpt-4.1"
    table_validator_model = (
        os.getenv("CLEAN_TABLE_VALIDATOR_MODEL")
        or table_model
    )
    request_timeout_seconds = parse_positive_float(os.getenv("CLEAN_REQUEST_TIMEOUT_SECONDS"), 180.0)
    retry_count = parse_positive_int(os.getenv("CLEAN_RETRY_COUNT"), 3)
    retry_delay_ms = parse_positive_int(os.getenv("CLEAN_RETRY_DELAY_MS"), 1500)

    return CleanConfig(
        input_dir=input_dir,
        output_dir=output_dir,
        report_file=report_file,
        table_draft_file=table_draft_file,
        concurrency=concurrency,
        table_llm_enabled=table_llm_enabled,
        table_validator_enabled=table_validator_enabled,
        api_key=api_key,
        base_url=base_url,
        table_model=table_model,
        table_validator_model=table_validator_model,
        request_timeout_seconds=request_timeout_seconds,
        retry_count=retry_count,
        retry_delay_ms=retry_delay_ms,
    )


def parse_bool_env(value: Optional[str], fallback: bool) -> bool:
    if value is None:
        return fallback

    normalized = value.strip().lower()

    if normalized in {"1", "true", "yes", "on"}:
        return True

    if normalized in {"0", "false", "no", "off"}:
        return False

    return fallback


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


def walk_markdown_files(root_dir: Path) -> List[Path]:
    results: List[Path] = []

    for path_item in sorted(root_dir.rglob("*.md"), key=lambda item: str(item)):
        if path_item.is_file():
            results.append(path_item)

    return results


def append_jsonl_record(output_file: Path, record: Dict[str, object]) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with output_file.open("a", encoding="utf-8") as handle:
        handle.write(f"{json.dumps(record, ensure_ascii=False)}\n")


def normalize_fullwidth_ascii(line: str) -> Tuple[str, int]:
    normalized_chars: List[str] = []
    replaced_count = 0

    for character in line:
        code_point = ord(character)

        if 0xFF01 <= code_point <= 0xFF5E:
            normalized_chars.append(chr(code_point - 0xFEE0))
            replaced_count += 1
        else:
            normalized_chars.append(character)

    return "".join(normalized_chars), replaced_count


def apply_regex_rules(
    line: str,
    rules: List[Tuple[re.Pattern[str], str, str]],
    stats: Dict[str, int],
) -> str:
    normalized = line

    for pattern, replacement, stat_key in rules:
        normalized, count = pattern.subn(replacement, normalized)

        if count > 0:
            stats[stat_key] += count

    return normalized


def normalize_non_heading_line(line: str) -> Tuple[str, Dict[str, int]]:
    stats = {
        "trimmed_trailing_whitespace": 0,
        "collapsed_internal_spaces": 0,
        "normalized_ranges": 0,
        "normalized_fullwidth_ascii_count": 0,
        "normalized_cd4_term_count": 0,
        "normalized_cd8_term_count": 0,
        "normalized_hiv_rna_term_count": 0,
        "normalized_p24_term_count": 0,
        "normalized_cells_per_microliter_count": 0,
        "normalized_copies_per_ml_count": 0,
        "normalized_pg_per_ml_count": 0,
        "normalized_mg_per_l_count": 0,
        "normalized_greater_equal_count": 0,
        "normalized_less_equal_count": 0,
        "normalized_greater_than_count": 0,
        "normalized_less_than_count": 0,
        "normalized_equal_count": 0,
    }
    normalized = re.sub(r"[ \t]+$", "", line)

    if normalized != line:
        stats["trimmed_trailing_whitespace"] += 1

    normalized, fullwidth_ascii_count = normalize_fullwidth_ascii(normalized)

    if fullwidth_ascii_count > 0:
        stats["normalized_fullwidth_ascii_count"] += fullwidth_ascii_count

    normalized = apply_regex_rules(normalized, SYMBOL_NORMALIZATION_RULES, stats)

    if not normalized.lstrip().startswith("|"):
        collapsed = MULTISPACE_PATTERN.sub(" ", normalized)

        if collapsed != normalized:
            stats["collapsed_internal_spaces"] += 1

        normalized = collapsed

    normalized = apply_regex_rules(normalized, TERM_NORMALIZATION_RULES, stats)
    normalized = apply_regex_rules(normalized, UNIT_NORMALIZATION_RULES, stats)

    ranged = RANGE_PATTERN.sub(" ~ ", normalized)

    if ranged != normalized:
        stats["normalized_ranges"] += 1

    normalized = ranged
    return normalized, stats


def clean_markdown_content(markdown: str) -> Tuple[str, Dict[str, int]]:
    content = markdown.replace("\r\n", "\n").replace("\r", "\n")
    stats = {
        "removed_bom": 0,
        "removed_zero_width": 0,
        "replaced_full_width_spaces": 0,
        "normalized_headings": 0,
        "removed_duplicate_headings": 0,
        "collapsed_blank_lines": 0,
        "trimmed_trailing_whitespace": 0,
        "collapsed_internal_spaces": 0,
        "normalized_ranges": 0,
        "normalized_fullwidth_ascii_count": 0,
        "normalized_cd4_term_count": 0,
        "normalized_cd8_term_count": 0,
        "normalized_hiv_rna_term_count": 0,
        "normalized_p24_term_count": 0,
        "normalized_cells_per_microliter_count": 0,
        "normalized_copies_per_ml_count": 0,
        "normalized_pg_per_ml_count": 0,
        "normalized_mg_per_l_count": 0,
        "normalized_greater_equal_count": 0,
        "normalized_less_equal_count": 0,
        "normalized_greater_than_count": 0,
        "normalized_less_than_count": 0,
        "normalized_equal_count": 0,
        "detected_table_count": 0,
        "converted_table_count": 0,
        "validated_table_count": 0,
        "validator_corrected_table_count": 0,
        "skipped_table_count": 0,
    }

    if content.startswith("\ufeff"):
        content = content.lstrip("\ufeff")
        stats["removed_bom"] += 1

    cleaned_lines: List[str] = []
    previous_blank = False

    for raw_line in content.split("\n"):
        line = raw_line
        replaced_spaces = line.count("\u3000") + line.count("\u00a0")

        if replaced_spaces > 0:
            line = line.replace("\u3000", " ").replace("\u00a0", " ")
            stats["replaced_full_width_spaces"] += replaced_spaces

        zero_width_matches = ZERO_WIDTH_PATTERN.findall(line)

        if len(zero_width_matches) > 0:
            line = ZERO_WIDTH_PATTERN.sub("", line)
            stats["removed_zero_width"] += len(zero_width_matches)

        line, fullwidth_ascii_count = normalize_fullwidth_ascii(line)

        if fullwidth_ascii_count > 0:
            stats["normalized_fullwidth_ascii_count"] += fullwidth_ascii_count

        heading_match = HEADING_PATTERN.match(line)

        if heading_match is not None:
            heading_hashes = heading_match.group(1)
            heading_text = heading_match.group(2).strip()
            normalized_heading = f"{heading_hashes} {heading_text}" if len(heading_text) > 0 else heading_hashes

            if normalized_heading != line:
                stats["normalized_headings"] += 1

            if len(cleaned_lines) > 0 and cleaned_lines[-1] == normalized_heading:
                stats["removed_duplicate_headings"] += 1
                previous_blank = False
                continue

            cleaned_lines.append(normalized_heading)
            previous_blank = False
            continue

        normalized_line, line_stats = normalize_non_heading_line(line)

        for key, value in line_stats.items():
            stats[key] += value

        if len(normalized_line.strip()) == 0:
            if previous_blank:
                stats["collapsed_blank_lines"] += 1
                continue

            cleaned_lines.append("")
            previous_blank = True
            continue

        cleaned_lines.append(normalized_line)
        previous_blank = False

    cleaned_text = "\n".join(cleaned_lines).rstrip() + "\n"
    return cleaned_text, stats


def is_markdown_table_start(lines: List[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False

    current_line = lines[index]
    next_line = lines[index + 1]

    if not TABLE_LINE_PATTERN.match(current_line):
        return False

    if not TABLE_SEPARATOR_PATTERN.match(next_line):
        return False

    return True


def gather_context_lines(lines: List[str], start_index: int, step: int, limit: int) -> str:
    collected: List[str] = []
    index = start_index

    while 0 <= index < len(lines) and len(collected) < limit:
        candidate = lines[index].strip()

        if len(candidate) > 0 and not TABLE_LINE_PATTERN.match(candidate):
            collected.append(candidate)

        index += step

    if step < 0:
        collected.reverse()

    return "\n".join(collected)


def detect_table_blocks(markdown: str) -> List[TableBlock]:
    lines = markdown.splitlines()
    table_blocks: List[TableBlock] = []
    current_heading_path: List[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        heading_match = HEADING_PATTERN.match(line)

        if heading_match is not None:
            heading_level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()
            current_heading_path = current_heading_path[: max(0, heading_level - 1)]
            current_heading_path.append(heading_text)

        if not is_markdown_table_start(lines, index):
            index += 1
            continue

        start_index = index
        end_index = index + 1

        while end_index + 1 < len(lines) and TABLE_LINE_PATTERN.match(lines[end_index + 1]):
            end_index += 1

        table_text = "\n".join(lines[start_index : end_index + 1])
        before_context = gather_context_lines(lines, start_index - 1, -1, 4)
        after_context = gather_context_lines(lines, end_index + 1, 1, 4)
        table_blocks.append(
            TableBlock(
                start_index=start_index,
                end_index=end_index,
                heading_path=list(current_heading_path),
                table_text=table_text,
                before_context=before_context,
                after_context=after_context,
            )
        )
        index = end_index + 1

    return table_blocks


def parse_json_content(content: str) -> Dict[str, Any]:
    trimmed = content.strip()

    try:
        return json.loads(trimmed)
    except json.JSONDecodeError:
        fenced = re.sub(r"^```json\s*", "", trimmed, flags=re.IGNORECASE)
        fenced = re.sub(r"^```\s*", "", fenced, flags=re.IGNORECASE)
        fenced = re.sub(r"\s*```$", "", fenced)
        return json.loads(fenced)


def call_with_retry(callable_factory, retry_count: int, retry_delay_ms: int, context_label: str) -> Any:
    attempt = 0
    last_error: Optional[BaseException] = None

    while attempt < retry_count:
        try:
            return callable_factory()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            attempt += 1

            if attempt >= retry_count:
                break

            delay_seconds = (retry_delay_ms * attempt) / 1000
            print(
                f"[clean:retry] {context_label} failed on attempt {attempt}. "
                f"type={type(exc).__name__} error={exc!r}. Retrying in {delay_seconds:.2f}s.",
                file=sys.stderr,
            )
            time.sleep(delay_seconds)

    raise last_error if last_error is not None else RuntimeError("Unknown clean retry failure.")


def build_table_conversion_messages(
    relative_path: str,
    heading_path: List[str],
    table_block: TableBlock,
) -> List[Dict[str, str]]:
    heading_text = " > ".join(heading_path) if len(heading_path) > 0 else relative_path
    user_prompt = "\n".join(
        [
            "请把下面 Markdown 医学表格改写成忠实的中文正文，供后续知识图谱抽取使用。",
            "要求：",
            "1. 只能依据表格和给定上下文，不得新增原文没有的事实。",
            "2. 必须保留阶段、首选/次选、剂量、频次、疗程、条件限制等关键信息。",
            "3. 如果表格表达的是治疗方案，请用短段落或分行句子表述，不要输出 Markdown 表格。",
            "4. 不要解释你的思考过程。",
            "5. 只返回严格 JSON，格式为 {\"converted_markdown\": \"...\"}。",
            "",
            "[文件路径]",
            relative_path,
            "",
            "[标题路径]",
            heading_text,
            "",
            "[表格前文]",
            table_block.before_context or "无",
            "",
            "[表格原文]",
            table_block.table_text,
            "",
            "[表格后文]",
            table_block.after_context or "无",
        ]
    )

    return [
        {
            "role": "system",
            "content": "你是医学文档预处理助手，负责把表格转写成忠实、紧凑、可抽取的中文正文。",
        },
        {
            "role": "user",
            "content": user_prompt,
        },
    ]


def build_table_validation_messages(
    relative_path: str,
    heading_path: List[str],
    table_block: TableBlock,
    converted_markdown: str,
) -> List[Dict[str, str]]:
    heading_text = " > ".join(heading_path) if len(heading_path) > 0 else relative_path
    user_prompt = "\n".join(
        [
            "请校验下面的表格转写正文是否忠实于原始医学表格。",
            "要求：",
            "1. 不能放过新增事实、遗漏关键条件、剂量/疗程错误。",
            "2. 如果转写忠实，请返回 is_faithful=true，并原样返回 converted_markdown。",
            "3. 如果转写不忠实，请返回 is_faithful=false，并给出 corrected_markdown。",
            "4. 只返回严格 JSON，格式为 {\"is_faithful\": true|false, \"issues\": [\"...\"], \"corrected_markdown\": \"...\"}。",
            "",
            "[文件路径]",
            relative_path,
            "",
            "[标题路径]",
            heading_text,
            "",
            "[表格原文]",
            table_block.table_text,
            "",
            "[候选正文]",
            converted_markdown,
        ]
    )

    return [
        {
            "role": "system",
            "content": "你是医学表格转写校验助手，只负责判断转写是否忠实，不做自由发挥。",
        },
        {
            "role": "user",
            "content": user_prompt,
        },
    ]


def call_chat_json(client: OpenAI, model: str, messages: List[Dict[str, str]]) -> Dict[str, Any]:
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0,
    )
    choices = getattr(response, "choices", None)

    if not choices:
        raise RuntimeError("Model response does not contain choices.")

    message = choices[0].message
    content = getattr(message, "content", None)

    if not isinstance(content, str) or len(content.strip()) == 0:
        raise RuntimeError("Model response does not contain text content.")

    return parse_json_content(content)


def convert_table_block(
    client: OpenAI,
    config: CleanConfig,
    relative_path: str,
    table_block: TableBlock,
    draft_output_file: Path,
) -> Dict[str, Any]:
    conversion_payload = call_with_retry(
        lambda: call_chat_json(
            client,
            config.table_model,
            build_table_conversion_messages(relative_path, table_block.heading_path, table_block),
        ),
        config.retry_count,
        config.retry_delay_ms,
        f"table-convert:{relative_path}:{table_block.start_index + 1}",
    )

    converted_markdown = conversion_payload.get("converted_markdown")

    if not isinstance(converted_markdown, str) or len(converted_markdown.strip()) == 0:
        raise RuntimeError("Table conversion model did not return converted_markdown.")

    converted_markdown = converted_markdown.strip()
    draft_record = {
        "record_type": "table_conversion_draft",
        "relative_path": relative_path,
        "line_start": table_block.start_index + 1,
        "line_end": table_block.end_index + 1,
        "heading_path": table_block.heading_path,
        "before_context": table_block.before_context,
        "table_text": table_block.table_text,
        "after_context": table_block.after_context,
        "draft_model": config.table_model,
        "draft_converted_markdown": converted_markdown,
    }
    append_jsonl_record(draft_output_file, draft_record)
    result = {
        "draft_converted_markdown": converted_markdown,
        "converted_markdown": converted_markdown,
        "validator_applied": False,
        "validator_corrected": False,
        "validator_issues": [],
    }

    if not config.table_validator_enabled:
        return result

    validation_payload = call_with_retry(
        lambda: call_chat_json(
            client,
            config.table_validator_model,
            build_table_validation_messages(
                relative_path,
                table_block.heading_path,
                table_block,
                converted_markdown,
            ),
        ),
        config.retry_count,
        config.retry_delay_ms,
        f"table-validate:{relative_path}:{table_block.start_index + 1}",
    )

    is_faithful = validation_payload.get("is_faithful")
    corrected_markdown = validation_payload.get("corrected_markdown")
    issues = validation_payload.get("issues", [])

    if not isinstance(issues, list):
        issues = []

    result["validator_applied"] = True
    result["validator_issues"] = issues

    if is_faithful is False:
        if isinstance(corrected_markdown, str) and len(corrected_markdown.strip()) > 0:
            result["converted_markdown"] = corrected_markdown.strip()
            result["validator_corrected"] = True
        else:
            raise RuntimeError("Table validator marked conversion unfaithful but did not return corrected_markdown.")

    return result


def rewrite_tables_with_llm(
    markdown: str,
    relative_path: str,
    client: Optional[OpenAI],
    config: CleanConfig,
    stats: Dict[str, int],
    draft_output_file: Path,
) -> Tuple[str, List[Dict[str, Any]]]:
    lines = markdown.splitlines()
    table_blocks = detect_table_blocks(markdown)
    table_records: List[Dict[str, Any]] = []
    stats["detected_table_count"] += len(table_blocks)

    if len(table_blocks) == 0:
        return markdown, table_records

    if client is None:
        stats["skipped_table_count"] += len(table_blocks)
        return markdown, table_records

    rewritten_lines = list(lines)

    for table_block in reversed(table_blocks):
        conversion_result = convert_table_block(client, config, relative_path, table_block, draft_output_file)
        replacement_lines = [
            "",
            "> [表格转写]",
            conversion_result["converted_markdown"],
            "",
        ]
        rewritten_lines[table_block.start_index : table_block.end_index + 1] = replacement_lines
        stats["converted_table_count"] += 1

        if conversion_result["validator_applied"]:
            stats["validated_table_count"] += 1

        if conversion_result["validator_corrected"]:
            stats["validator_corrected_table_count"] += 1

        table_records.append(
            {
                "line_start": table_block.start_index + 1,
                "line_end": table_block.end_index + 1,
                "heading_path": table_block.heading_path,
                "draft_converted_markdown": conversion_result["draft_converted_markdown"],
                "converted_markdown": conversion_result["converted_markdown"],
                "validator_applied": conversion_result["validator_applied"],
                "validator_corrected": conversion_result["validator_corrected"],
                "validator_issues": conversion_result["validator_issues"],
            }
        )

    rewritten_text = "\n".join(rewritten_lines).rstrip() + "\n"
    return rewritten_text, list(reversed(table_records))


def clean_file(
    absolute_path: Path,
    config: CleanConfig,
) -> Dict[str, object]:
    client = build_client(config)
    original_text = absolute_path.read_text(encoding="utf-8")
    cleaned_text, stats = clean_markdown_content(original_text)
    relative_path = str(absolute_path.relative_to(config.input_dir))
    cleaned_text, table_records = rewrite_tables_with_llm(
        cleaned_text,
        relative_path,
        client,
        config,
        stats,
        config.table_draft_file,
    )
    output_path = config.output_dir / Path(relative_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(cleaned_text, encoding="utf-8")

    return {
        "record_type": "clean_file",
        "relative_path": relative_path,
        "source_file": str(absolute_path),
        "output_file": str(output_path),
        "source_char_count": len(original_text),
        "output_char_count": len(cleaned_text),
        "changed": original_text != cleaned_text,
        "table_records": table_records,
        "stats": stats,
    }


def summarize(records: List[Dict[str, object]]) -> Dict[str, int]:
    summary = {
        "file_count": len(records),
        "changed_file_count": 0,
        "removed_bom": 0,
        "removed_zero_width": 0,
        "replaced_full_width_spaces": 0,
        "normalized_headings": 0,
        "removed_duplicate_headings": 0,
        "collapsed_blank_lines": 0,
        "trimmed_trailing_whitespace": 0,
        "collapsed_internal_spaces": 0,
        "normalized_ranges": 0,
        "normalized_fullwidth_ascii_count": 0,
        "normalized_cd4_term_count": 0,
        "normalized_cd8_term_count": 0,
        "normalized_hiv_rna_term_count": 0,
        "normalized_p24_term_count": 0,
        "normalized_cells_per_microliter_count": 0,
        "normalized_copies_per_ml_count": 0,
        "normalized_pg_per_ml_count": 0,
        "normalized_mg_per_l_count": 0,
        "normalized_greater_equal_count": 0,
        "normalized_less_equal_count": 0,
        "normalized_greater_than_count": 0,
        "normalized_less_than_count": 0,
        "normalized_equal_count": 0,
        "detected_table_count": 0,
        "converted_table_count": 0,
        "validated_table_count": 0,
        "validator_corrected_table_count": 0,
        "skipped_table_count": 0,
    }

    for record in records:
        if bool(record.get("changed")):
            summary["changed_file_count"] += 1

        stats = record.get("stats", {})

        if not isinstance(stats, dict):
            continue

        for key in list(summary.keys()):
            if key.endswith("_count") and key not in stats:
                continue

            if key == "file_count" or key == "changed_file_count":
                continue

            value = stats.get(key)

            if isinstance(value, int):
                summary[key] += value

    return summary


def build_client(config: CleanConfig) -> Optional[OpenAI]:
    if not config.table_llm_enabled:
        return None

    if len(config.api_key) == 0:
        raise RuntimeError(
            "Missing API key. Set OPENAI_API_KEY, DASHSCOPE_API_KEY, or LLM_API_KEY before table LLM cleaning."
        )

    return OpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=config.request_timeout_seconds,
        max_retries=0,
    )


def main() -> None:
    config = read_env_config()

    if not config.input_dir.exists():
        raise RuntimeError(f"Input directory does not exist: {config.input_dir}")

    markdown_files = walk_markdown_files(config.input_dir)
    run_records: List[Dict[str, object]] = []

    print(f"[clean] input={config.input_dir}")
    print(f"[clean] output={config.output_dir}")
    print(f"[clean] report={config.report_file}")
    print(f"[clean] table_draft_file={config.table_draft_file}")
    print(f"[clean] found {len(markdown_files)} markdown files")
    print(f"[clean] concurrency={config.concurrency}")
    print(f"[clean] table_llm_enabled={config.table_llm_enabled}")

    if config.table_llm_enabled:
        print(f"[clean] table_model={config.table_model}")
        print(f"[clean] table_validator_enabled={config.table_validator_enabled}")
        print(f"[clean] table_validator_model={config.table_validator_model}")
        print(f"[clean] request_timeout={config.request_timeout_seconds}s")

    with concurrent.futures.ThreadPoolExecutor(max_workers=config.concurrency) as executor:
        future_to_path = {
            executor.submit(clean_file, absolute_path, config): absolute_path
            for absolute_path in markdown_files
        }

        for future in concurrent.futures.as_completed(future_to_path):
            record = future.result()
            run_records.append(record)
            append_jsonl_record(config.report_file, record)
            print(
                f"[clean:file] {record['relative_path']} changed={record['changed']} "
                f"tables={record['stats']['detected_table_count']}"
            )

    summary_record = {
        "record_type": "clean_summary",
        "input_dir": str(config.input_dir),
        "output_dir": str(config.output_dir),
        "summary": summarize(run_records),
    }
    append_jsonl_record(config.report_file, summary_record)
    print(f"[clean] completed. cleaned_files={len(run_records)} output={config.output_dir}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[fatal] interrupted by user", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:  # noqa: BLE001
        print(f"[fatal] {exc}", file=sys.stderr)
        raise SystemExit(1)
