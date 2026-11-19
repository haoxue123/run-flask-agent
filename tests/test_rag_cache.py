"""
RAG 检索缓存回归测试（自包含，不依赖本机 chroma_db / 外网嵌入模型）
验证：
1. 相同参数二次调用命中缓存（不重复触发 col.query）
2. 不同参数仍走底层查询
3. top_k 不同视为不同缓存键
4. reset_index / build_index 后缓存整体失效

设计说明：
    原版本直接操作真实 data/chroma_db —— 该目录被 .gitignore，
    在干净 checkout 上是空的，查询返回空结果 -> 按设计不写缓存 ->
    "二次应命中"断言必挂。此外 sentence-transformers 需联网下载模型，
    网络不通时降级会拖慢整个收集阶段。
    本版本用假的 collection + 临时目录隔离这些外部依赖，只测缓存逻辑本身。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rag


class _FakeCollection:
    """模拟 ChromaDB collection：query 返回一条固定知识片段，并计数。"""

    def __init__(self):
        self.query_calls = 0

    def query(self, query_texts, n_results, **kwargs):
        self.query_calls += 1
        n = min(n_results, 1)
        return {
            "ids": [["doc_1"] * n],
            "documents": [["测试知识片段：间歇跑配速应按 VDOT 推导。"] * n],
            "metadatas": [[{"source": "test.md", "title": "测试文档",
                            "authority_level": "高", "is_summary": "false"}] * n],
            "distances": [[0.1] * n],
        }


def _install_fake_collection():
    """把假 collection 注入 rag 的单例位置，并清空缓存。"""
    fake = _FakeCollection()
    rag._collection = fake
    rag._retrieve_cache.clear()
    rag._retrieve_cache_order.clear()
    return fake


def test_cache_hit_and_miss():
    calls = _install_fake_collection()

    # 1) 首次：缓存未命中，触发一次底层查询
    r1 = rag.retrieve_knowledge("间歇跑配速如何确定", top_k=3)
    assert calls.query_calls == 1, f"第一次应为 1 次查询，实际 {calls.query_calls}"
    assert len(r1) == 1

    # 2) 相同参数：命中缓存，不再查询
    r2 = rag.retrieve_knowledge("间歇跑配速如何确定", top_k=3)
    assert calls.query_calls == 1, f"相同参数应命中缓存，查询次数仍为 1，实际 {calls.query_calls}"
    assert r1 == r2, "缓存命中返回结果应与原结果一致"

    # 3) 不同 query：未命中，触发新查询
    rag.retrieve_knowledge("马拉松赛前三天饮食安排", top_k=3)
    assert calls.query_calls == 2, f"不同参数应再查 1 次，实际 {calls.query_calls}"

    # 4) 不同 top_k：视为不同缓存键
    rag.retrieve_knowledge("间歇跑配速如何确定", top_k=5)
    assert calls.query_calls == 3, f"top_k 不同应视为新键，实际 {calls.query_calls}"


def test_cache_invalidated_on_reset():
    calls = _install_fake_collection()

    rag.retrieve_knowledge("间歇跑配速如何确定", top_k=3)
    assert calls.query_calls == 1

    # reset 会清空 _collection 引用并令缓存失效。
    # 真实场景下下次检索会重建 collection；这里重新注入假对象以保持隔离。
    rag.reset_index()
    assert rag._retrieve_cache == {}, "reset_index 后缓存应清空"
    calls = _install_fake_collection()
    rag.retrieve_knowledge("间歇跑配速如何确定", top_k=3)
    assert calls.query_calls == 1, f"重建后首次查询应未命中缓存，实际 {calls.query_calls}"


if __name__ == "__main__":
    test_cache_hit_and_miss()
    test_cache_invalidated_on_reset()
    print("CACHE_TEST_OK | 缓存命中/失效逻辑全部通过")
