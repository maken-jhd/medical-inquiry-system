"""实现文本稀疏 RAG baseline 使用的轻量检索器。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
from math import log, sqrt
from pathlib import Path
import re


@dataclass
class TextRagDocument:
    """表示一条可被文本 RAG 检索到的语料文档。"""

    doc_id: str
    disease_name: str
    title: str
    content: str
    tags: list[str] = field(default_factory=list)
    source_path: str = ""
    chunk_id: str = ""


@dataclass
class TextRagHit:
    """表示一次文本检索返回的命中文档。"""

    doc_id: str
    disease_name: str
    title: str
    content: str
    score: float
    tags: list[str] = field(default_factory=list)
    matched_terms: list[str] = field(default_factory=list)
    source_path: str = ""
    chunk_id: str = ""


@dataclass
class _IndexedTextDocument:
    """保存稀疏检索运行时使用的文档索引信息。"""

    document: TextRagDocument
    token_counts: Counter[str]
    vector_norm: float


class SparseTextRagRetriever:
    """基于轻量 TF-IDF 风格打分的文本稀疏检索器。"""

    _TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+")

    def __init__(self, documents: list[TextRagDocument]) -> None:
        self.documents = list(documents)
        self._idf_by_token: dict[str, float] = {}
        self._indexed_documents = self._build_index(self.documents)

    @classmethod
    def from_jsonl(cls, corpus_file: str | Path) -> "SparseTextRagRetriever":
        """从 JSONL 语料文件加载文本 RAG 文档。"""

        path = Path(corpus_file)
        documents: list[TextRagDocument] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                raw_line = line.strip()
                if len(raw_line) == 0:
                    continue
                payload = json.loads(raw_line)
                tags = payload.get("tags", [])
                if not isinstance(tags, list):
                    tags = []
                documents.append(
                    TextRagDocument(
                        doc_id=str(payload.get("doc_id") or "").strip(),
                        disease_name=str(payload.get("disease_name") or "").strip(),
                        title=str(payload.get("title") or "").strip(),
                        content=str(payload.get("content") or "").strip(),
                        tags=[str(item).strip() for item in tags if len(str(item).strip()) > 0],
                        source_path=str(payload.get("source_path") or "").strip(),
                        chunk_id=str(payload.get("chunk_id") or "").strip(),
                    )
                )
        return cls(documents)

    # 基于 query 文本和可选候选病名，返回 top-k 稀疏检索命中结果。
    def query(
        self,
        query_text: str,
        *,
        top_k: int = 5,
        candidate_disease_names: list[str] | None = None,
    ) -> list[TextRagHit]:
        normalized_query = str(query_text or "").strip()
        if len(normalized_query) == 0 or not self._indexed_documents:
            return []

        query_token_counts = Counter(self._tokenize(normalized_query))
        if not query_token_counts:
            return []

        query_norm = self._compute_vector_norm(query_token_counts)
        candidate_name_set = {
            self._normalize_text(name)
            for name in (candidate_disease_names or [])
            if len(self._normalize_text(name)) > 0
        }

        hits: list[TextRagHit] = []
        for indexed in self._indexed_documents:
            score = self._score(query_token_counts, query_norm, indexed)
            if score <= 0.0:
                continue

            disease_name_norm = self._normalize_text(indexed.document.disease_name)
            if disease_name_norm and disease_name_norm in candidate_name_set:
                score += 0.12

            matched_terms = self._collect_matched_terms(query_token_counts, indexed.token_counts)
            hits.append(
                TextRagHit(
                    doc_id=indexed.document.doc_id,
                    disease_name=indexed.document.disease_name,
                    title=indexed.document.title,
                    content=indexed.document.content,
                    score=round(min(score, 1.0), 4),
                    tags=list(indexed.document.tags),
                    matched_terms=matched_terms,
                    source_path=indexed.document.source_path,
                    chunk_id=indexed.document.chunk_id,
                )
            )

        hits.sort(key=lambda item: (-item.score, item.disease_name, item.doc_id))
        return hits[: max(int(top_k), 1)]

    # 建立轻量倒排统计，避免为几十篇文档引入额外依赖。
    def _build_index(self, documents: list[TextRagDocument]) -> list[_IndexedTextDocument]:
        if not documents:
            self._idf_by_token = {}
            return []

        document_frequencies: Counter[str] = Counter()
        token_counters: list[Counter[str]] = []
        for document in documents:
            token_counts = Counter(self._tokenize(self._document_text(document)))
            token_counters.append(token_counts)
            for token in token_counts:
                document_frequencies[token] += 1

        total_documents = len(documents)
        self._idf_by_token = {
            token: log((1.0 + total_documents) / (1.0 + count)) + 1.0
            for token, count in document_frequencies.items()
        }
        return [
            _IndexedTextDocument(
                document=document,
                token_counts=token_counts,
                vector_norm=self._compute_vector_norm(token_counts),
            )
            for document, token_counts in zip(documents, token_counters)
        ]

    def _document_text(self, document: TextRagDocument) -> str:
        return " ".join(
            part
            for part in (
                document.disease_name,
                document.title,
                " ".join(document.tags),
                document.content,
            )
            if len(str(part).strip()) > 0
        )

    # 中文文本默认没有空格，这里同时保留连续词块和短窗口切片，兼容症状、检查名和病名检索。
    def _tokenize(self, text: str) -> list[str]:
        tokens: list[str] = []
        for raw_piece in self._TOKEN_PATTERN.findall(str(text or "")):
            piece = raw_piece.strip().lower()
            if len(piece) == 0:
                continue
            if piece.isascii():
                tokens.append(piece)
                continue

            if len(piece) <= 2:
                tokens.append(piece)
                continue

            tokens.append(piece)
            for window_size in (2, 3):
                if len(piece) < window_size:
                    continue
                tokens.extend(piece[index : index + window_size] for index in range(len(piece) - window_size + 1))
        return tokens

    def _compute_vector_norm(self, token_counts: Counter[str]) -> float:
        squared_sum = 0.0
        for token, count in token_counts.items():
            weight = float(count) * self._idf_by_token.get(token, 1.0)
            squared_sum += weight * weight
        return sqrt(squared_sum) if squared_sum > 0.0 else 1.0

    def _score(
        self,
        query_token_counts: Counter[str],
        query_norm: float,
        indexed_document: _IndexedTextDocument,
    ) -> float:
        numerator = 0.0
        for token, query_count in query_token_counts.items():
            document_count = indexed_document.token_counts.get(token, 0)
            if document_count <= 0:
                continue
            idf = self._idf_by_token.get(token, 1.0)
            numerator += float(query_count) * float(document_count) * idf * idf

        denominator = max(query_norm * indexed_document.vector_norm, 1e-8)
        return numerator / denominator

    def _collect_matched_terms(
        self,
        query_token_counts: Counter[str],
        document_token_counts: Counter[str],
    ) -> list[str]:
        weighted_matches = []
        for token in query_token_counts:
            if token not in document_token_counts:
                continue
            weighted_matches.append((self._idf_by_token.get(token, 1.0), token))
        weighted_matches.sort(key=lambda item: (-item[0], item[1]))
        return [token for _, token in weighted_matches[:5]]

    def _normalize_text(self, value: str) -> str:
        return str(value).strip().replace(" ", "").replace("-", "").replace("_", "").lower()