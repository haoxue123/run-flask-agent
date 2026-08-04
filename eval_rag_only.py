# -*- coding: utf-8 -*-
"""
RAG 检索准确率评测 —— 嵌入方案无关（embedding-agnostic）
============================================================
设计目标：同一套评测脚本，既能跑生产级语义检索，也能在本机无嵌入环境下跑代理测量。

嵌入来源（自动选择，优先级与生产 rag.py 一致）：
  1. rag._get_embedding_function() 返回的真实嵌入
     （SiliconFlow bge-m3 / sentence-transformers / ChromaDB 内置，取决于联网与依赖）
  2. 若上述全部不可用（本沙箱现状：无 chromadb、无 key、无本地模型），
     降级为「字符 n-gram TF-IDF」内存向量（零依赖、零网络）。
     ⚠️ 这是代理测量：仅验证分块逻辑正确性 + 提供失败诊断，
        不代表真实语义检索质量。真实指标需联网环境的语义嵌入。

分块逻辑：复用生产 rag._split_markdown，与生产完全一致。

----------------------------------------------------------------------
真实语义 Round 5 启动（需联网环境，一行不改即可跑真指标）：
    pip install chromadb sentence-transformers
    # 方式A（推荐中文）：SiliconFlow bge-m3
    export SILICONFLOW_API_KEY=sk-xxx
    # 方式B：本地 sentence-transformers（首次自动下载 ~400MB 多语种模型，无需 key）
    python eval_rag_only.py
脚本会自动复用 rag._get_embedding_function() 的真实嵌入；不可用则降级 TF-IDF 并提示。
----------------------------------------------------------------------

Critic 硬标准（真实语义轮）：
    - rag_top1_accuracy >= 0.95
    - rag_top3_accuracy == 1.00
    - 失败 case（如 R26 method_japan_china）必须进入 Top1
    - 回归门：分块逻辑不变（仍走 rag._split_markdown）
"""
import os
import sys
import json
import math
from collections import Counter

_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
import rag  # 复用真实分块逻辑 _split_markdown；并尝试复用生产嵌入入口

PROJECT = _PROJECT_DIR  # 脚本自身目录，随仓库走，不再硬编码旧路径
KNOWLEDGE = os.path.join(PROJECT, "data", "knowledge")
QUERIES = os.path.join(PROJECT, "data", "eval", "metric3_rag.json")


# ----------------------------------------------------------------------
# 降级嵌入：字符 n-gram TF-IDF（零依赖，仅本沙箱兜底用）
# ----------------------------------------------------------------------
class _TFIDFEmbedding:
    """实现 chromadb 嵌入函数接口：embed_documents / embed_query。"""

    def fit(self, corpus_texts):
        self._vocab = []
        self._df = Counter()
        for t in corpus_texts:
            for g in set(_ngrams(t)):
                self._df[g] += 1
                if g not in self._vocab_index:
                    self._vocab_index[g] = len(self._vocab)
                    self._vocab.append(g)
        n = len(corpus_texts)
        self._idf = {g: math.log((n + 1) / (self._df.get(g, 0) + 1)) + 1
                     for g in self._vocab}
        return self

    def __init__(self):
        self._vocab_index = {}
        self._vocab = []
        self._idf = {}
        self._fitted = False

    def _vector(self, text):
        tf = Counter(_ngrams(text))
        vec = [0.0] * len(self._vocab)
        for g, c in tf.items():
            i = self._vocab_index.get(g)
            if i is not None:
                vec[i] = (1 + math.log(c)) * self._idf[g]
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    def embed_documents(self, texts):
        return [self._vector(t) for t in texts]

    def embed_query(self, text):
        return self._vector(text)


def _ngrams(text, ns=(2, 3)):
    """字符 n-gram（对中英文混合文本友好，无需分词）。"""
    grams = set()
    for n in ns:
        for i in range(len(text) - n + 1):
            grams.add(text[i:i + n])
    return grams


def _build_embedding_fn():
    """返回 (emb_fn, tag, is_semantic)。优先生产真实嵌入，失败降级 TF-IDF。"""
    try:
        fn = rag._get_embedding_function()
        tag = getattr(rag, "_embedding_fn_tag", None) or "production_embedding"
        print(f"[RAG] 使用生产嵌入：{tag}（真实语义检索）")
        return fn, tag, True
    except Exception as e:
        print(f"[RAG] 生产嵌入不可用（{e}），降级为字符 n-gram TF-IDF 代理测量")
        return _TFIDFEmbedding(), "tfidf_ngram_proxy", False


def _cosine(a, b):
    """稠密向量余弦（真实嵌入与 TF-IDF 统一走此路径）。"""
    return sum(x * y for x, y in zip(a, b))


def main():
    md_files = sorted(
        os.path.join(KNOWLEDGE, f) for f in os.listdir(KNOWLEDGE) if f.endswith(".md")
    )
    chunks = []  # (text, source)
    for fp in md_files:
        fn = os.path.basename(fp)
        for c in rag._split_markdown(fp):
            chunks.append((c, fn))
    print(f"[RAG] 分块完成：{len(chunks)} 块 / {len(md_files)} 文档（分块逻辑同生产）")

    emb_fn, tag, is_semantic = _build_embedding_fn()

    # TF-IDF 降级需要先用语料拟合词表/IDF；真实嵌入直接嵌入即可。
    if not is_semantic:
        emb_fn.fit([c for c, _ in chunks])

    chunk_texts = [c for c, _ in chunks]
    chunk_vecs = emb_fn.embed_documents(chunk_texts)

    data = json.load(open(QUERIES, encoding="utf-8"))
    top1 = top3 = 0
    detail = []
    misses = []
    for q in data:
        qv = emb_fn.embed_query(q["query"])
        scored = sorted(
            ((_cosine(qv, cv), src) for cv, (_, src) in zip(chunk_vecs, chunks)),
            reverse=True,
        )
        top = scored[:3]
        top_srcs = [src for _, src in top]
        h1 = q["expected_source"] in top_srcs[:1]
        h3 = q["expected_source"] in top_srcs[:3]
        top1 += 1 if h1 else 0
        top3 += 1 if h3 else 0
        detail.append({
            "id": q["id"], "expected": q["expected_source"],
            "top1": top_srcs[0], "hit_top1": h1, "hit_top3": h3,
            "top3_sources": top_srcs,
        })
        if not h1:
            misses.append({
                "id": q["id"],
                "query": q["query"],
                "expected": q["expected_source"],
                "top3_actual": [
                    {"source": src, "score": round(score, 4)} for score, src in top
                ],
            })

    n = len(data)
    result = {
        "rag_top1_accuracy": round(top1 / n, 4),
        "rag_top3_accuracy": round(top3 / n, 4),
        "n": n,
        "embedding": tag,
        "is_semantic": is_semantic,
        "note": ("真实语义检索测量" if is_semantic
                 else "环境无法下载语义嵌入模型，用词粒度 TF-IDF 代理测量（仅验证分块逻辑+失败诊断，不代表真实质量）"),
        "detail": detail,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 失败诊断：让 Builder 一眼看到该修什么（真实语义轮可直接用）
    if misses:
        print("\n" + "=" * 60)
        print(f"[RAG] 失败诊断：{len(misses)} 题未命中 Top1")
        print("=" * 60)
        for m in misses:
            print(f"\n• {m['id']}  查询：{m['query']}")
            print(f"  期望来源：{m['expected']}")
            print(f"  实际 Top3：")
            for i, t in enumerate(m["top3_actual"], 1):
                print(f"    {i}. {t['source']}  (score={t['score']})")
        print("\n" + "=" * 60)
        print("Critic 判定：未命中题需在真实语义嵌入下复查（TF-IDF 代理下属已知局限）")
        print("=" * 60)


if __name__ == "__main__":
    main()
