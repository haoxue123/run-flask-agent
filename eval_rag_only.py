# -*- coding: utf-8 -*-
"""
RAG 检索准确率 —— 纯标准库实测（零外部依赖 / 零网络）
============================================================
环境限制：本机无法下载 chromadb 的 ONNX 嵌入模型，也未装 sentence-transformers，
故无法运行生产级语义检索。本脚本复用项目真实的知识库分块逻辑(rag._split_markdown)，
以字符 n-gram TF-IDF 在内存建向量索引，对 20 道评测题做 Top1/Top3 检索，
比对真实知识源(data/knowledge/*.md) 与预期源，得到可复现的检索准确率。
分块策略与生产一致；仅嵌入方式由语义改为词粒度（环境所限）。
"""
import os
import sys
import json
import math
from collections import Counter

sys.path.insert(0, r"E:\AI\trae\agent")
import rag  # 复用真实分块逻辑 _split_markdown

PROJECT = r"E:\AI\trae\agent"
KNOWLEDGE = os.path.join(PROJECT, "data", "knowledge")
QUERIES = os.path.join(PROJECT, "data", "eval", "metric3_rag.json")


def ngrams(text, ns=(2, 3)):
    """字符 n-gram（对中英文混合文本友好，无需分词）。"""
    grams = set()
    for n in ns:
        for i in range(len(text) - n + 1):
            grams.add(text[i:i + n])
    return grams


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

    N = len(chunks)
    df = {}
    tf_list = []
    for text, _ in chunks:
        tf = Counter(ngrams(text))
        tf_list.append(tf)
        for g in tf:
            df[g] = df.get(g, 0) + 1

    def idf(g):
        return math.log((N + 1) / (df.get(g, 0) + 1)) + 1

    chunk_vecs = []
    for tf in tf_list:
        vec = {g: (1 + math.log(tf[g])) * idf(g) for g in tf}
        norm = math.sqrt(sum(v * v for v in vec.values()))
        chunk_vecs.append({g: v / norm for g, v in vec.items() if norm > 0})

    def query_vec(text):
        tf = Counter(ngrams(text))
        vec = {g: (1 + math.log(tf[g])) * idf(g) for g in tf}
        norm = math.sqrt(sum(v * v for v in vec.values()))
        return {g: v / norm for g, v in vec.items() if norm > 0}

    def cosine(a, b):
        if len(a) > len(b):
            a, b = b, a
        return sum(v * a.get(g, 0.0) for g, v in b.items())

    data = json.load(open(QUERIES, encoding="utf-8"))
    top1 = top3 = 0
    detail = []
    for q in data:
        qv = query_vec(q["query"])
        scored = sorted(
            ((cosine(qv, cv), src) for cv, (_, src) in zip(chunk_vecs, chunks)),
            reverse=True,
        )
        top_srcs = [src for _, src in scored[:3]]
        h1 = q["expected_source"] in top_srcs[:1]
        h3 = q["expected_source"] in top_srcs[:3]
        top1 += 1 if h1 else 0
        top3 += 1 if h3 else 0
        detail.append({"id": q["id"], "expected": q["expected_source"],
                       "top1": top_srcs[0], "hit_top1": h1, "hit_top3": h3})

    n = len(data)
    result = {
        "rag_top1_accuracy": round(top1 / n, 4),
        "rag_top3_accuracy": round(top3 / n, 4),
        "n": n,
        "embedding": "字符 n-gram TF-IDF（纯标准库，分块逻辑同生产）",
        "note": "环境无法下载语义嵌入模型，故用词粒度向量做真实可复现检索测量",
        "detail": detail,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
