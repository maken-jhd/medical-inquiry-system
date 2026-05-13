"""从 HIV_cleaned 构建文本稀疏 RAG baseline 使用的 JSONL 语料。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建文本稀疏 RAG baseline 使用的 JSONL 语料。")
    parser.add_argument(
        "--source-dir",
        default=str(PROJECT_ROOT / "HIV_cleaned"),
        help="原始 Markdown 文档目录。",
    )
    parser.add_argument(
        "--output-file",
        default=str(PROJECT_ROOT / "test_outputs" / "rag_corpus" / "hiv_cleaned_text_rag_corpus.jsonl"),
        help="输出 JSONL 语料文件。",
    )
    parser.add_argument(
        "--max-chars-per-chunk",
        type=int,
        default=900,
        help="单个文本块的最大字符数。",
    )
    return parser.parse_args()


def iter_markdown_files(source_dir: Path) -> list[Path]:
    return sorted(path for path in source_dir.rglob("*.md") if path.is_file())


def infer_disease_name(source_root: Path, file_path: Path) -> str:
    stem = file_path.stem.strip()
    for separator in ("-", "—", "_"):
        if separator in stem:
            prefix = stem.split(separator, 1)[0].strip()
            if len(prefix) > 0:
                return prefix
    if file_path.parent != source_root:
        parent_name = file_path.parent.name.strip()
        if len(parent_name) > 0:
            return parent_name
    return stem


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def slugify(value: str) -> str:
    slug = value.strip().lower()
    slug = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "_", slug)
    slug = slug.strip("_")
    return slug or "document"


# 优先按 Markdown heading 分块，保证文本块仍然有较明确的主题语义。
def split_markdown_into_chunks(title: str, content: str, *, max_chars_per_chunk: int) -> list[tuple[str, str]]:
    lines = [line.rstrip() for line in str(content or "").splitlines()]
    sections: list[tuple[str, list[str]]] = []
    current_title = title
    current_lines: list[str] = []

    for line in lines:
        heading = line.lstrip("#").strip() if line.startswith("#") else ""
        if len(heading) > 0:
            if current_lines:
                sections.append((current_title, current_lines))
            current_title = heading
            current_lines = []
            continue
        current_lines.append(line)

    if current_lines:
        sections.append((current_title, current_lines))

    if not sections:
        sections = [(title, lines)]

    chunks: list[tuple[str, str]] = []
    for section_title, section_lines in sections:
        buffer: list[str] = []
        buffer_len = 0
        for line in section_lines:
            normalized_line = normalize_text(line)
            if len(normalized_line) == 0:
                continue
            projected_len = buffer_len + len(normalized_line) + (1 if buffer else 0)
            if buffer and projected_len > max_chars_per_chunk:
                chunks.append((section_title, "\n".join(buffer)))
                buffer = [normalized_line]
                buffer_len = len(normalized_line)
                continue
            buffer.append(normalized_line)
            buffer_len = projected_len
        if buffer:
            chunks.append((section_title, "\n".join(buffer)))

    return chunks


def build_corpus(source_dir: Path, *, max_chars_per_chunk: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for file_path in iter_markdown_files(source_dir):
        raw_text = file_path.read_text(encoding="utf-8")
        disease_name = infer_disease_name(source_dir, file_path)
        base_title = file_path.stem.strip()
        relative_path = file_path.relative_to(source_dir).as_posix()
        chunks = split_markdown_into_chunks(base_title, raw_text, max_chars_per_chunk=max_chars_per_chunk)
        for index, (section_title, section_content) in enumerate(chunks, start=1):
            chunk_id = f"{slugify(relative_path)}::{index}"
            rows.append(
                {
                    "doc_id": chunk_id,
                    "chunk_id": chunk_id,
                    "disease_name": disease_name,
                    "title": section_title or base_title,
                    "content": section_content,
                    "tags": [disease_name, file_path.parent.name.strip()],
                    "source_path": relative_path,
                }
            )
    return rows


def main() -> int:
    args = parse_args()
    source_dir = Path(args.source_dir).resolve()
    output_file = Path(args.output_file).resolve()
    output_file.parent.mkdir(parents=True, exist_ok=True)

    rows = build_corpus(source_dir, max_chars_per_chunk=max(int(args.max_chars_per_chunk), 200))
    with output_file.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(
        json.dumps(
            {
                "source_dir": str(source_dir),
                "output_file": str(output_file),
                "document_count": len(rows),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())