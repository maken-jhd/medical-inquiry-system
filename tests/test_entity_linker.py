"""测试实体链接器能否将特征名称对齐到图谱节点。"""

from brain.entity_linker import EntityLinker


class FakeNeo4jClient:
    """使用固定查询结果模拟图谱节点候选。"""

    # 根据提及词返回一组固定候选节点。
    def run_query(self, query: str, params: dict | None = None) -> list[dict]:
        _ = query
        mention = (params or {}).get("mention", "")

        if mention == "发热":
            return [
                {
                    "node_id": "symptom_fever",
                    "label": "ClinicalFinding",
                    "canonical_name": "发热",
                    "aliases": ["发烧"],
                }
            ]

        return []


# 验证实体链接器会输出可信的最佳匹配结果。
def test_entity_linker_returns_trusted_match() -> None:
    linker = EntityLinker(FakeNeo4jClient())

    results = linker.link_mentions(["发热"])

    assert len(results) == 1
    assert results[0].node_id == "symptom_fever"
    assert results[0].canonical_name == "发热"
    assert results[0].is_trusted is True
    assert results[0].metadata["top_matches"][0]["canonical_name"] == "发热"
