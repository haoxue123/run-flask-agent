"""
RAG 检索缓存回归测试
验证：
1. 相同参数二次调用命中缓存（不重复触发 col.query）
2. 不同参数仍走底层查询
3. reset_index / build_index 后缓存整体失效
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rag

calls = {"n": 0}


def wrap(col):
    orig = col.query

    def spy(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    col.query = spy
    return col


# 确保索引可用
rag.reset_index()
col = wrap(rag._get_collection())

# 1) 首次：缓存未命中
r1 = rag.retrieve_knowledge("间歇跑配速如何确定", top_k=3)
assert calls["n"] == 1, f"第一次应为 1 次查询，实际 {calls['n']}"

# 2) 相同参数：应命中缓存，不再查询
r2 = rag.retrieve_knowledge("间歇跑配速如何确定", top_k=3)
assert calls["n"] == 1, f"相同参数应命中缓存，查询次数仍为 1，实际 {calls['n']}"
assert r1 == r2, "缓存命中返回结果应与原结果一致"

# 3) 不同 query：未命中，触发新查询
r3 = rag.retrieve_knowledge("马拉松赛前三天饮食安排", top_k=3)
assert calls["n"] == 2, f"不同参数应再查 1 次，实际 {calls['n']}"

# 4) 不同 top_k：视为不同缓存键
r4 = rag.retrieve_knowledge("间歇跑配速如何确定", top_k=5)
assert calls["n"] == 3, f"top_k 不同应视为新键，实际 {calls['n']}"

# 5) 重建索引后缓存应整体失效
rag.reset_index()
col = wrap(rag._get_collection())
calls["n"] = 0
rag.retrieve_knowledge("间歇跑配速如何确定", top_k=3)
assert calls["n"] == 1, f"重建后首次查询应未命中，实际 {calls['n']}"

print("CACHE_TEST_OK | 缓存命中/失效逻辑全部通过")
