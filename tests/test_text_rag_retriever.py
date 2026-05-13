"""测试文本稀疏 RAG 检索器的最小检索行为。"""

from __future__ import annotations

import json
from pathlib import Path

from baselines.text_rag_retriever import SparseTextRagRetriever, TextRagDocument


# 验证稀疏检索会优先返回与 query 和候选病名更一致的文档。
def test_sparse_text_rag_retriever_prefers_relevant_document() -> None:
    retriever = SparseTextRagRetriever(
        [
            TextRagDocument(
                doc_id="pcp_doc",
                disease_name="肺孢子菌肺炎",
                title="PCP 画像",
                content="常见表现包括干咳、呼吸困难、低氧血症和磨玻璃影。",
                tags=["呼吸系统", "机会性感染"],
            ),
            TextRagDocument(
                doc_id="tb_doc",
                disease_name="活动性结核病",
                title="结核画像",
                content="常见表现包括盗汗、消瘦、午后低热和空洞影。",
                tags=["呼吸系统", "结核"],
            ),
        ]
    )

    hits = retriever.query(
        "患者近期干咳、呼吸困难，考虑肺孢子菌肺炎。",
        top_k=2,
        candidate_disease_names=["肺孢子菌肺炎"],
    )

    assert hits[0].doc_id == "pcp_doc"
    assert hits[0].disease_name == "肺孢子菌肺炎"
    assert hits[0].score >= hits[1].score


# 验证检索器可以从 JSONL 语料文件加载，并返回对应文档。
def test_sparse_text_rag_retriever_loads_jsonl(tmp_path: Path) -> None:
    corpus_file = tmp_path / "text_rag_corpus.jsonl"
    corpus_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "doc_id": "cmv_doc",
                        "disease_name": "巨细胞病毒(CMV)肺炎",
                        "title": "CMV 肺炎画像",
                        "content": "CMV 肺炎常见于免疫抑制患者，可有发热、呼吸困难。",
                        "tags": ["机会性感染"],
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "doc_id": "lymphoma_doc",
                        "disease_name": "淋巴瘤",
                        "title": "淋巴瘤画像",
                        "content": "可有发热、盗汗、体重下降和淋巴结肿大。",
                        "tags": ["肿瘤"],
                    },
                    ensure_ascii=False,
                ),
            ]
        ),
        encoding="utf-8",
    )

    retriever = SparseTextRagRetriever.from_jsonl(corpus_file)
    hits = retriever.query("免疫抑制患者，发热、呼吸困难。", top_k=1)

    assert hits[0].doc_id == "cmv_doc"
    assert hits[0].disease_name == "巨细胞病毒(CMV)肺炎"